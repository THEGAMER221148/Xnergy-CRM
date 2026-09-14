import json, uuid, os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from .config import get_settings
from .db import init_db,close_db,pool,tenant_tx
from .security import (init_security,close_security,oidc_login,oidc_callback,create_session,
                       destroy_session,require_user,enforce_csrf,rate_limit,redis_client)
from .storage import init_storage,close_storage,upload_quarantine,clean_blob_stream,object_key
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
s=get_settings()

@asynccontextmanager
async def lifespan(app):
    await init_db(); await init_security(); await init_storage()
    yield
    await close_storage(); await close_security(); await close_db()

app=FastAPI(title="Xnergy DealOS",version="1.0.0",lifespan=lifespan,docs_url=None if s.app_env=="production" else "/docs")
app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")

@app.middleware("http")
async def security_headers(request,call_next):
    # Coarse IP protection; route-level limits are tighter where needed.
    ip=request.client.host if request.client else "unknown"
    if redis_client:
        try: await rate_limit("ip:"+ip,300,60)
        except HTTPException as e: return JSONResponse({"detail":e.detail},status_code=e.status_code,headers=e.headers)
    response=await call_next(request)
    response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Permissions-Policy"]="camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
    response.headers["Cache-Control"]="no-store"
    return response

@app.get("/")
async def home():
    return HTMLResponse((ROOT/"static"/"index.html").read_text())

@app.get("/healthz")
async def healthz(): return {"ok":True}

@app.get("/readyz")
async def readyz():
    try:
        async with pool().acquire() as c: await c.fetchval("select 1")
        await redis_client.ping()
        return {"ok":True}
    except Exception as e: raise HTTPException(503,"Dependency unavailable")

@app.get("/auth/login")
async def login(next:str="/"):
    if s.dev_auth_bypass and s.app_env!="production":
        return RedirectResponse("/auth/dev")
    return await oidc_login(next)

@app.get("/auth/callback")
async def callback(code:str,state:str):
    claims,next_path=await oidc_callback(code,state)
    issuer=claims.get("iss"); subject=claims.get("sub")
    email=(claims.get("email") or claims.get("preferred_username") or "").lower()
    if not email: raise HTTPException(403,"Identity provider did not provide email")
    async with pool().acquire() as c:
        row=await c.fetchrow("""select u.* from auth_identities a join users u on u.id=a.user_id
          where a.issuer=$1 and a.subject=$2 and u.active=true""",issuer,subject)
        if not row:
            # Pre-provisioning only: identity may attach to an already-active CRM/user record by exact email.
            row=await c.fetchrow("select * from users where lower(email)=$1 and active=true",email)
            if not row: raise HTTPException(403,"Account is not provisioned in DealOS")
            await c.execute("""insert into auth_identities(id,user_id,issuer,subject,email,last_login_at)
              values(gen_random_uuid(),$1,$2,$3,$4,now())
              on conflict(issuer,subject) do update set last_login_at=now(),email=excluded.email""",
              row["id"],issuer,subject,email)
        else:
            await c.execute("update auth_identities set last_login_at=now(),email=$1 where issuer=$2 and subject=$3",email,issuer,subject)
    sid,csrf=await create_session(dict(row))
    res=RedirectResponse(next_path if next_path.startswith("/") else "/",303)
    res.set_cookie("dealos_session",sid,secure=s.cookie_secure,httponly=True,samesite="lax",
                   max_age=s.session_ttl_seconds,domain=s.cookie_domain,path="/")
    res.set_cookie("dealos_csrf",csrf,secure=s.cookie_secure,httponly=False,samesite="strict",
                   max_age=s.session_ttl_seconds,domain=s.cookie_domain,path="/")
    return res

@app.get("/auth/dev")
async def dev_login():
    if not (s.dev_auth_bypass and s.app_env!="production"): raise HTTPException(404)
    async with pool().acquire() as c:
        row=await c.fetchrow("select * from users where lower(email)=$1 and active=true",s.dev_auth_email.lower())
    if not row: raise HTTPException(404,"DEV_AUTH_EMAIL is not provisioned")
    sid,csrf=await create_session(dict(row))
    res=RedirectResponse("/",303)
    res.set_cookie("dealos_session",sid,secure=False,httponly=True,samesite="lax",max_age=s.session_ttl_seconds)
    res.set_cookie("dealos_csrf",csrf,secure=False,httponly=False,samesite="strict",max_age=s.session_ttl_seconds)
    return res

@app.post("/auth/logout")
async def logout(request:Request):
    u=await require_user(request); await enforce_csrf(request,u)
    await destroy_session(u["session_id"])
    res=JSONResponse({"ok":True}); res.delete_cookie("dealos_session"); res.delete_cookie("dealos_csrf"); return res

@app.get("/api/me")
async def me(request:Request):
    u=await require_user(request)
    return {k:u.get(k) for k in ("user_id","tenant_id","email","display_name","role","investor_contact_id")}

@app.get("/api/investors")
async def investors(request:Request,q:str="",limit:int=100):
    u=await require_user(request,["admin","banker","deal_manager","compliance"])
    await rate_limit("user:"+u["user_id"]+":investors",120,60)
    async with tenant_tx(u) as c:
        rows=await c.fetch("""select o.id,o.name,o.domain,o.sector_focus,o.geography,o.relationship_owner,
          count(distinct ct.id) contact_count,max(ct.last_contact_at) last_contact
          from investor_organizations o left join investor_contacts ct on ct.organization_id=o.id
          where ($1='' or o.name ilike '%'||$1||'%' or coalesce(o.domain,'') ilike '%'||$1||'%')
          group by o.id order by o.name limit $2""",q,min(limit,500))
    return [dict(x) for x in rows]

@app.get("/api/deals")
async def deals(request:Request):
    u=await require_user(request)
    async with tenant_tx(u) as c:
        if u["role"]=="investor":
            rows=await c.fetch("""select distinct d.* from deals d
              join deal_investors di on di.deal_id=d.id
              join investor_contacts ct on ct.organization_id=di.organization_id
              where ct.id=nullif(current_setting('app.investor_contact_id',true),'')::uuid
                and di.access_level>0 and di.revoked_at is null""")
        else: rows=await c.fetch("select * from deals order by created_at desc")
    return [dict(x) for x in rows]

@app.post("/api/deals/{deal_id}/investors/{org_id}/access")
async def grant_access(deal_id:str,org_id:str,request:Request):
    u=await require_user(request,["admin","deal_manager"]); await enforce_csrf(request,u)
    body=await request.json(); level=int(body.get("access_level",0))
    if not 0<=level<=5: raise HTTPException(400,"access_level must be 0-5")
    async with tenant_tx(u) as c:
        row=await c.fetchrow("""update deal_investors set access_level=$1,revoked_at=case when $1=0 then now() else null end,
          updated_at=now() where deal_id=$2::uuid and organization_id=$3::uuid returning id""",level,deal_id,org_id)
        if not row: raise HTTPException(404,"Deal/investor relationship not found")
        await c.execute("select audit_event($1,$2,$3,$4,$5::jsonb)",u["user_id"],"DEAL_ACCESS_CHANGED","deal_investor",str(row["id"]),json.dumps({"level":level}))
    return {"ok":True,"access_level":level}

@app.post("/api/deals/{deal_id}/documents")
async def upload_document(deal_id:str,request:Request,file:UploadFile=File(...),level:int=Form(1),watermark:bool=Form(False)):
    u=await require_user(request,["admin","banker","deal_manager"]); await enforce_csrf(request,u)
    await rate_limit("user:"+u["user_id"]+":upload",30,60)
    if not 1<=level<=5: raise HTTPException(400,"level must be 1-5")
    data=await file.read(s.max_upload_bytes+1)
    if len(data)>s.max_upload_bytes: raise HTTPException(413,"File exceeds configured upload limit")
    if not data: raise HTTPException(400,"Empty file")
    doc_id=str(uuid.uuid4()); key=object_key(u["tenant_id"],deal_id,doc_id)
    sha=await upload_quarantine(key,data,file.content_type,{"document_id":doc_id,"tenant_id":u["tenant_id"],"deal_id":deal_id})
    async with tenant_tx(u) as c:
        await c.execute("""insert into documents(id,tenant_id,deal_id,filename,storage_key,mime_type,size_bytes,sha256,
          confidentiality_level,watermark_required,scan_status,uploaded_by)
          values($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10,'pending',$11::uuid)""",
          doc_id,u["tenant_id"],deal_id,file.filename,key,file.content_type,len(data),sha,level,watermark,u["user_id"])
        await c.execute("select audit_event($1,$2,$3,$4,$5::jsonb)",u["user_id"],"DOCUMENT_QUARANTINED","document",doc_id,json.dumps({"filename":file.filename,"sha256":sha}))
    return {"id":doc_id,"filename":file.filename,"scan_status":"pending"}

@app.get("/api/deals/{deal_id}/documents")
async def list_documents(deal_id:str,request:Request):
    u=await require_user(request)
    async with tenant_tx(u) as c:
        if u["role"]=="investor":
            rows=await c.fetch("""select id,filename,mime_type,size_bytes,confidentiality_level,watermark_required,scan_status,created_at
              from documents where deal_id=$1::uuid and scan_status='clean'
              and confidentiality_level <= (
                select di.access_level from deal_investors di join investor_contacts ct on ct.organization_id=di.organization_id
                where di.deal_id=$1::uuid and ct.id=nullif(current_setting('app.investor_contact_id',true),'')::uuid
                  and di.revoked_at is null limit 1)
              order by created_at desc""",deal_id)
        else:
            rows=await c.fetch("""select id,filename,mime_type,size_bytes,confidentiality_level,watermark_required,scan_status,created_at
              from documents where deal_id=$1::uuid order by created_at desc""",deal_id)
    return [dict(r) for r in rows]

@app.get("/api/documents/{doc_id}/download")
async def download_document(doc_id:str,request:Request):
    u=await require_user(request)
    await rate_limit("user:"+u["user_id"]+":download",120,60)
    async with tenant_tx(u) as c:
        d=await c.fetchrow("select * from documents where id=$1::uuid",doc_id)
        if not d: raise HTTPException(404)
        if d["scan_status"]!="clean": raise HTTPException(423,"Document is not cleared for access")
        if u["role"]=="investor":
            level=await c.fetchval("""select di.access_level from deal_investors di join investor_contacts ct on ct.organization_id=di.organization_id
               where di.deal_id=$1 and ct.id=nullif(current_setting('app.investor_contact_id',true),'')::uuid and di.revoked_at is null""",d["deal_id"])
            if level is None or level<d["confidentiality_level"]: raise HTTPException(403)
        await c.execute("select audit_event($1,$2,$3,$4,$5::jsonb)",u["user_id"],"DOCUMENT_DOWNLOADED","document",doc_id,json.dumps({"filename":d["filename"]}))
    downloader=await clean_blob_stream(d["storage_key"])
    headers={"Content-Disposition":f'attachment; filename="{d["filename"].replace(chr(34),"")}"'}
    return StreamingResponse(downloader.chunks(),media_type=d["mime_type"] or "application/octet-stream",headers=headers)
