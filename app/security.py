import base64, hashlib, json, secrets, time
from urllib.parse import urlencode
import httpx, jwt
from jwt.algorithms import RSAAlgorithm
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from redis.asyncio import Redis
from .config import get_settings

redis_client: Redis | None = None
_discovery_cache=None
_jwks_cache=None

async def init_security():
    global redis_client
    redis_client=Redis.from_url(get_settings().redis_url, decode_responses=True)
    await redis_client.ping()

async def close_security():
    if redis_client:
        await redis_client.aclose()

async def discovery():
    global _discovery_cache
    if _discovery_cache:
        return _discovery_cache
    s=get_settings()
    url=s.oidc_issuer.rstrip("/")+"/.well-known/openid-configuration"
    async with httpx.AsyncClient(timeout=10) as c:
        r=await c.get(url); r.raise_for_status()
        _discovery_cache=r.json()
    return _discovery_cache

async def jwks():
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    d=await discovery()
    async with httpx.AsyncClient(timeout=10) as c:
        r=await c.get(d["jwks_uri"]); r.raise_for_status()
        _jwks_cache=r.json()
    return _jwks_cache

def _challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()

async def oidc_login(next_path="/"):
    s=get_settings(); d=await discovery()
    state=secrets.token_urlsafe(32); nonce=secrets.token_urlsafe(24); verifier=secrets.token_urlsafe(64)
    payload={"nonce":nonce,"verifier":verifier,"next":next_path,"created":int(time.time())}
    await redis_client.setex(f"oidc:{state}",600,json.dumps(payload))
    params={
      "client_id":s.oidc_client_id,"response_type":"code",
      "redirect_uri":s.app_base_url.rstrip("/")+"/auth/callback",
      "scope":s.oidc_scopes,"state":state,"nonce":nonce,
      "code_challenge":_challenge(verifier),"code_challenge_method":"S256"
    }
    return RedirectResponse(d["authorization_endpoint"]+"?"+urlencode(params),status_code=302)

async def oidc_callback(code:str,state:str):
    s=get_settings()
    raw=await redis_client.getdel(f"oidc:{state}")
    if not raw: raise HTTPException(400,"Invalid or expired OIDC state")
    st=json.loads(raw); d=await discovery()
    async with httpx.AsyncClient(timeout=15) as c:
        r=await c.post(d["token_endpoint"],data={
          "grant_type":"authorization_code","code":code,
          "redirect_uri":s.app_base_url.rstrip("/")+"/auth/callback",
          "client_id":s.oidc_client_id,"client_secret":s.oidc_client_secret,
          "code_verifier":st["verifier"]
        })
        if r.status_code>=400: raise HTTPException(401,"OIDC token exchange failed")
        tok=r.json()
    idt=tok.get("id_token")
    if not idt: raise HTTPException(401,"Identity provider did not return id_token")
    hdr=jwt.get_unverified_header(idt)
    keys=(await jwks())["keys"]
    jwk=next((k for k in keys if k.get("kid")==hdr.get("kid")),None)
    if not jwk: raise HTTPException(401,"Unknown signing key")
    key=RSAAlgorithm.from_jwk(json.dumps(jwk))
    claims=jwt.decode(idt,key,algorithms=[hdr.get("alg","RS256")],audience=s.oidc_client_id,
                      issuer=d.get("issuer",s.oidc_issuer),options={"require":["exp","iat","sub","iss","aud"]})
    if claims.get("nonce")!=st["nonce"]: raise HTTPException(401,"OIDC nonce mismatch")
    return claims, st["next"]

async def create_session(user:dict):
    sid=secrets.token_urlsafe(40); csrf=secrets.token_urlsafe(32); s=get_settings()
    data={"user_id":str(user["id"]),"tenant_id":str(user["tenant_id"]),"role":user["role"],
          "email":user["email"],"display_name":user["display_name"],
          "investor_contact_id":str(user.get("investor_contact_id") or ""),"csrf":csrf}
    await redis_client.setex("sess:"+sid,s.session_ttl_seconds,json.dumps(data))
    return sid, csrf

async def destroy_session(sid:str|None):
    if sid: await redis_client.delete("sess:"+sid)

async def session_from_request(request:Request):
    sid=request.cookies.get("dealos_session")
    if not sid: return None
    raw=await redis_client.get("sess:"+sid)
    if not raw: return None
    # Sliding session refresh.
    await redis_client.expire("sess:"+sid,get_settings().session_ttl_seconds)
    d=json.loads(raw)
    return {"session_id":sid,**d}

async def require_user(request:Request,roles=None):
    u=await session_from_request(request)
    if not u: raise HTTPException(401,"Authentication required")
    if roles and u["role"] not in roles: raise HTTPException(403,"Forbidden")
    return u

async def enforce_csrf(request:Request,u:dict):
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        token=request.headers.get(get_settings().csrf_header_name)
        if not token or not secrets.compare_digest(token,u["csrf"]):
            raise HTTPException(403,"CSRF validation failed")

async def rate_limit(key:str,limit:int,window:int):
    """Atomic fixed-window limiter. Returns remaining."""
    now=int(time.time()); bucket=now//window; k=f"rl:{key}:{bucket}"
    pipe=redis_client.pipeline()
    pipe.incr(k); pipe.expire(k,window+2)
    count,_=await pipe.execute()
    if int(count)>limit:
        raise HTTPException(429,"Rate limit exceeded",headers={"Retry-After":str(window)})
    return max(0,limit-int(count))
