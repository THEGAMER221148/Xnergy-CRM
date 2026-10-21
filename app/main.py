import json, uuid, os, asyncio
from contextlib import suppress
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from .config import get_settings
from .db import init_db,close_db,pool,tenant_tx
from . import security
from .security import (init_security,close_security,oidc_login,oidc_callback,create_session,
                       destroy_session,require_user,enforce_csrf,rate_limit,safe_next_path)
from .storage import init_storage,close_storage,upload_quarantine,clean_blob_stream,object_key
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
s=get_settings()

@asynccontextmanager
async def lifespan(app):
    await init_db(); await init_security(); await init_storage()
    from .mailbox_import import worker
    mailbox_worker=asyncio.create_task(worker())
    try:
        yield
    finally:
        mailbox_worker.cancel()
        with suppress(asyncio.CancelledError): await mailbox_worker
        await close_storage(); await close_security(); await close_db()

app=FastAPI(title="Xnergy DealOS",version="1.0.0",lifespan=lifespan,docs_url=None if s.app_env=="production" else "/docs")
from .crm import router as crm_router
app.include_router(crm_router)
from .mailbox_import import router as mailbox_router
app.include_router(mailbox_router)
from .followup_workspace import router as followup_router
app.include_router(followup_router)
from .pipeline import router as pipeline_router
app.include_router(pipeline_router)
from .intake import router as intake_router
app.include_router(intake_router)
from .workspace import router as workspace_router
app.include_router(workspace_router)
from .library import router as library_router
app.include_router(library_router)

app.mount("/static",StaticFiles(directory=ROOT/"static"),name="static")

@app.middleware("http")
async def security_headers(request,call_next):
    # Coarse IP protection; route-level limits are tighter where needed.
    ip=request.client.host if request.client else "unknown"
    if security.redis_client is not None:
        try: await rate_limit("ip:"+ip,300,60)
        except HTTPException as e: return JSONResponse({"detail":e.detail},status_code=e.status_code,headers=e.headers)
    response=await call_next(request)
    response.headers["Strict-Transport-Security"]="max-age=31536000; includeSubDomains"
    response.headers["X-Content-Type-Options"]="nosniff"
    intake_page = request.url.path == "/static/workspace/talk.html"
    workspace_page = request.url.path.startswith("/static/workspace/") or request.url.path == "/"
    response.headers["X-Frame-Options"]="SAMEORIGIN" if intake_page else "DENY"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["Permissions-Policy"]="camera=(), microphone=" + ("(self)" if workspace_page else "()") + ", geolocation=()"
    response.headers["Content-Security-Policy"]="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors " + ("'self'" if intake_page else "'none'")
    response.headers["Cache-Control"]="no-store"
    return response

@app.get("/")
async def home():
    return HTMLResponse((ROOT/"static"/"workspace"/"overview.html").read_text())

@app.get("/healthz")
async def healthz(): return {"ok":True}

@app.get("/readyz")
async def readyz():
    try:
        async with pool().acquire() as c: await c.fetchval("select 1")
        await security._redis().ping()
        return {"ok":True}
    except Exception as e: raise HTTPException(503,"Dependency unavailable")

@app.get("/auth/login")
async def login(next:str="/"):
    if s.dev_auth_bypass and s.app_env!="production":
        return RedirectResponse("/auth/dev")
    return await oidc_login(next)

@app.get("/auth/callback")
async def callback(code:str='',state:str='',request:Request=None,error:str=''):
    if error:
        raw=await security._redis().getdel(f'oidc:{state}') if state else None
        if not raw:raise HTTPException(400,'The connection request expired. Return to DealOS and connect again.')
        data=json.loads(raw)
        if data.get('calendar_user',{}).get('purpose') in ('ai_inbox','ai_resources'):return RedirectResponse('/static/workspace/ask.html?connection=declined#work',303)
        destination='/static/workspace/library.html' if data.get('calendar_user',{}).get('purpose')=='library' else '/static/workspace/calendar.html'
        if data.get('calendar_user',{}).get('purpose')=='mail':return RedirectResponse('/static/workspace/deals.html?view=morning&connection=declined',303)
        return RedirectResponse(destination+'?connection=declined',303)
    claims,next_path=await oidc_callback(code,state)
    async with pool().acquire() as c:
        row=await security.resolve_identity(c,claims)
    connection=claims.pop('_calendar_connection',None)
    if connection:
        from .workspace import save_calendar_connection
        current=await require_user(request)
        if connection['user'].get('purpose') in ('ai_inbox','ai_resources'):
            from .ai_hub import save_connection
            await save_connection(current,dict(row),connection)
        elif connection['user'].get('purpose')=='library':
            from .library import save_library_connection
            await save_library_connection(current,dict(row),connection)
        elif connection['user'].get('purpose')=='mail':
            from .morning import save_mail_connection
            await save_mail_connection(current,dict(row),connection)
        else:
            await save_calendar_connection(current,dict(row),connection)
    sid,csrf=await create_session(dict(row))
    res=RedirectResponse(safe_next_path(next_path),303)
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

from decimal import Decimal
from .crm_search import investor_query

@app.get("/api/investors")
async def investors(request:Request,q:str=Query("",max_length=300),limit:int=Query(50,ge=1,le=500),offset:int=Query(0,ge=0),sector:str=Query("",max_length=200),geography:str=Query("",max_length=200),category:str=Query("",max_length=100),financing:str=Query("",max_length=100),stage:str=Query("",max_length=100),minimum:Decimal|None=Query(None,ge=0),maximum:Decimal|None=Query(None,ge=0),currency:str=Query("USD",pattern=r'^[A-Z]{3}$')):
    u=await require_user(request,["admin","banker","deal_manager","compliance"])
    await rate_limit("user:"+u["user_id"]+":investors",120,60)
    try: query,args=investor_query(q,sector,geography,category,financing,stage,minimum,maximum,currency,limit,offset)
    except ValueError as e: raise HTTPException(422,str(e))
    async with tenant_tx(u) as c: rows=await c.fetch(query,*args)
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
        else: rows=await c.fetch("select d.*,p.sector company_sector,p.company_profile,p.service_need company_service,rt.banker_ids relationship_banker_ids,rt.manager_user_id from deals d left join company_prospects p on p.id=d.prospect_id left join crm_relationship_teams rt on rt.tenant_id=d.tenant_id and rt.entity_kind=case when d.prospect_id is null then 'deal' else 'prospect' end and rt.entity_id=coalesce(d.prospect_id,d.id) order by d.created_at desc")
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
async def upload_document(deal_id:str,request:Request,file:UploadFile=File(...),level:int=Form(1),watermark:bool=Form(False),submitted_by_contact_id:str|None=Form(None)):
    u=await require_user(request,["admin","banker","deal_manager"]); await enforce_csrf(request,u)
    await rate_limit("user:"+u["user_id"]+":upload",30,60)
    if not 1<=level<=5: raise HTTPException(400,"level must be 1-5")
    async with tenant_tx(u) as c:
        if not await c.fetchval('select id from deals where id=$1::uuid',deal_id):raise HTTPException(404,'Deal not found')
        if submitted_by_contact_id:
            try:uuid.UUID(submitted_by_contact_id)
            except ValueError:raise HTTPException(422,'Choose a company contact')
            if not await c.fetchval('select pc.id from prospect_contacts pc join deals d on d.prospect_id=pc.prospect_id where d.id=$1::uuid and pc.id=$2::uuid',deal_id,submitted_by_contact_id):raise HTTPException(422,'Contact does not belong to this company')
    data=await file.read(s.max_upload_bytes+1)
    if len(data)>s.max_upload_bytes: raise HTTPException(413,"File exceeds configured upload limit")
    if not data: raise HTTPException(400,"Empty file")
    doc_id=str(uuid.uuid4()); key=object_key(u["tenant_id"],deal_id,doc_id)
    sha=await upload_quarantine(key,data,file.content_type,{"document_id":doc_id,"tenant_id":u["tenant_id"],"deal_id":deal_id})
    async with tenant_tx(u) as c:
        await c.execute("""insert into documents(id,tenant_id,deal_id,filename,storage_key,mime_type,size_bytes,sha256,
          confidentiality_level,watermark_required,scan_status,uploaded_by,submitted_by_contact_id)
          values($1::uuid,$2::uuid,$3::uuid,$4,$5,$6,$7,$8,$9,$10,'pending',$11::uuid,$12::uuid)""",
          doc_id,u["tenant_id"],deal_id,file.filename,key,file.content_type,len(data),sha,level,watermark,u["user_id"],submitted_by_contact_id)
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
            rows=await c.fetch("""select id,filename,mime_type,size_bytes,confidentiality_level,watermark_required,scan_status,created_at,submitted_by_contact_id
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

from .ask import router as ask_router
app.include_router(ask_router)

from .research_center import router as research_center_router
app.include_router(research_center_router)

from .relationship_team import router as relationship_team_router
app.include_router(relationship_team_router)

from .morning import router as morning_router
app.include_router(morning_router)

from .meeting_followups import router as meeting_followups_router
app.include_router(meeting_followups_router)

from .ai_hub import router as ai_hub_router
app.include_router(ai_hub_router)

from .bulk_import import router as bulk_import_router
app.include_router(bulk_import_router)

from .prospect_shortlist import router as prospect_shortlist_router
app.include_router(prospect_shortlist_router)
