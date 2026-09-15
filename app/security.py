import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from jwt.algorithms import RSAAlgorithm
from redis.asyncio import Redis
from redis_entraid.cred_provider import create_from_default_azure_credential

from .config import get_settings


redis_client: Redis | None = None
_discovery_cache = None
_jwks_cache = None


async def init_security():
    global redis_client

    s = get_settings()

    if s.app_env.lower() != "production" and s.redis_url:
        redis_client = Redis.from_url(
            s.redis_url,
            decode_responses=True,
            socket_connect_timeout=10,
            socket_timeout=10,
        )
    else:
        credential_provider = create_from_default_azure_credential(
            ("https://redis.azure.com/.default",)
        )

        redis_client = Redis(
            host=s.azure_redis_host,
            port=10000,
            ssl=True,
            decode_responses=True,
            credential_provider=credential_provider,
            socket_connect_timeout=10,
            socket_timeout=10,
        )

    await redis_client.ping()


async def close_security():
    global redis_client

    if redis_client:
        await redis_client.aclose()
        redis_client = None


def _redis() -> Redis:
    if redis_client is None:
        raise RuntimeError("Redis client is not initialized")
    return redis_client


async def discovery():
    global _discovery_cache

    if _discovery_cache:
        return _discovery_cache

    s = get_settings()
    url = s.oidc_issuer.rstrip("/") + "/.well-known/openid-configuration"

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(url)
        response.raise_for_status()
        _discovery_cache = response.json()

    return _discovery_cache


async def jwks():
    global _jwks_cache

    if _jwks_cache:
        return _jwks_cache

    oidc_discovery = await discovery()

    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.get(oidc_discovery["jwks_uri"])
        response.raise_for_status()
        _jwks_cache = response.json()

    return _jwks_cache


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


async def oidc_login(next_path: str = "/"):
    s = get_settings()
    oidc_discovery = await discovery()

    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)

    payload = {
        "nonce": nonce,
        "verifier": verifier,
        "next": next_path,
        "created": int(time.time()),
    }

    await _redis().setex(
        f"oidc:{state}",
        600,
        json.dumps(payload),
    )

    params = {
        "client_id": s.oidc_client_id,
        "response_type": "code",
        "redirect_uri": s.app_base_url.rstrip("/") + "/auth/callback",
        "scope": s.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": _challenge(verifier),
        "code_challenge_method": "S256",
    }

    return RedirectResponse(
        oidc_discovery["authorization_endpoint"] + "?" + urlencode(params),
        status_code=302,
    )


async def oidc_callback(code: str, state: str):
    s = get_settings()

    raw = await _redis().getdel(f"oidc:{state}")

    if not raw:
        raise HTTPException(400, "Invalid or expired OIDC state")

    state_data = json.loads(raw)
    oidc_discovery = await discovery()

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            oidc_discovery["token_endpoint"],
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": s.app_base_url.rstrip("/") + "/auth/callback",
                "client_id": s.oidc_client_id,
                "client_secret": s.oidc_client_secret,
                "code_verifier": state_data["verifier"],
            },
        )

        if response.status_code >= 400:
            raise HTTPException(401, "OIDC token exchange failed")

        token_response = response.json()

    id_token = token_response.get("id_token")

    if not id_token:
        raise HTTPException(
            401,
            "Identity provider did not return id_token",
        )

    header = jwt.get_unverified_header(id_token)
    keys = (await jwks())["keys"]

    jwk = next(
        (
            key
            for key in keys
            if key.get("kid") == header.get("kid")
        ),
        None,
    )

    if not jwk:
        raise HTTPException(401, "Unknown signing key")

    signing_key = RSAAlgorithm.from_jwk(json.dumps(jwk))

    claims = jwt.decode(
        id_token,
        signing_key,
        algorithms=[header.get("alg", "RS256")],
        audience=s.oidc_client_id,
        issuer=oidc_discovery.get("issuer", s.oidc_issuer),
        options={
            "require": [
                "exp",
                "iat",
                "sub",
                "iss",
                "aud",
            ]
        },
    )

    if claims.get("nonce") != state_data["nonce"]:
        raise HTTPException(401, "OIDC nonce mismatch")

    return claims, state_data["next"]


async def create_session(user: dict):
    s = get_settings()

    session_id = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)

    data = {
        "user_id": str(user["id"]),
        "tenant_id": str(user["tenant_id"]),
        "role": user["role"],
        "email": user["email"],
        "display_name": user["display_name"],
        "investor_contact_id": str(
            user.get("investor_contact_id") or ""
        ),
        "csrf": csrf,
    }

    await _redis().setex(
        "sess:" + session_id,
        s.session_ttl_seconds,
        json.dumps(data),
    )

    return session_id, csrf


async def destroy_session(session_id: str | None):
    if session_id:
        await _redis().delete("sess:" + session_id)


async def session_from_request(request: Request):
    session_id = request.cookies.get("dealos_session")

    if not session_id:
        return None

    raw = await _redis().get("sess:" + session_id)

    if not raw:
        return None

    await _redis().expire(
        "sess:" + session_id,
        get_settings().session_ttl_seconds,
    )

    data = json.loads(raw)

    return {
        "session_id": session_id,
        **data,
    }


async def require_user(request: Request, roles=None):
    user = await session_from_request(request)

    if not user:
        raise HTTPException(401, "Authentication required")

    if roles and user["role"] not in roles:
        raise HTTPException(403, "Forbidden")

    return user


async def enforce_csrf(request: Request, user: dict):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        token = request.headers.get(
            get_settings().csrf_header_name
        )

        if not token or not secrets.compare_digest(
            token,
            user["csrf"],
        ):
            raise HTTPException(
                403,
                "CSRF validation failed",
            )


async def rate_limit(
    key: str,
    limit: int,
    window: int,
):
    now = int(time.time())
    bucket = now // window
    redis_key = f"rl:{key}:{bucket}"

    pipe = _redis().pipeline()
    pipe.incr(redis_key)
    pipe.expire(redis_key, window + 2)

    count, _ = await pipe.execute()

    if int(count) > limit:
        raise HTTPException(
            429,
            "Rate limit exceeded",
            headers={"Retry-After": str(window)},
        )

    return max(0, limit - int(count))
