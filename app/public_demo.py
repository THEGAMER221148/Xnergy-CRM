"""Unauthenticated, read-only demo. No production write endpoints or raw exports."""
import asyncio,time,logging,re
from datetime import datetime,timezone
from pathlib import Path
from fastapi import APIRouter,HTTPException,Query
from fastapi.responses import HTMLResponse
router=APIRouter()
SNAPSHOT_CONTAINER='dealos-public-demo'
SNAPSHOT_BLOB='snapshot-20261005-v1.json.gz'
def public_text(value):
    if isinstance(value,dict):return {k:public_text(v) for k,v in value.items()}
    if isinstance(value,list):return [public_text(v) for v in value]
    if isinstance(value,str):
        return re.sub(r'https?://[^\s<>]*(?:outlook\.(?:office365|office|live)\.com|sharepoint\.com|onedrive\.com|1drv\.ms)[^\s<>]*','[Private source link omitted]',value,flags=re.I)
    return value
_cache=None
_lock=asyncio.Lock()
async def snapshot():
    global _cache
    if _cache is not None:return _cache
    async with _lock:
        if _cache is not None:return _cache
        try:
            import gzip,json
            from . import storage
            blob=storage._service.get_blob_client(SNAPSHOT_CONTAINER,SNAPSHOT_BLOB)
            raw=await (await blob.download_blob()).readall()
            data=await asyncio.to_thread(lambda:json.loads(gzip.decompress(raw)))
            for kind in ('prospects','deals'):
                for r in data[kind]:
                    if r.get('private') and set(r)!={'id','name','private','stage'}:
                        raise ValueError('Invalid public client projection')
            # Demonstration stages only; never update production or the source snapshot.
            for row in [r for r in data['deals'] if not r.get('private') and r.get('stage')=='contact_made'][:3]:
                row['stage']='vetting';row['demo_stage']=True
            _cache=public_text(data)
            return _cache
        except Exception:
            logging.exception('Public demo snapshot unavailable')
            raise HTTPException(503,'Demo data is temporarily unavailable. Please try again shortly.')

@router.get('/demo',response_class=HTMLResponse)
@router.get('/demo/',response_class=HTMLResponse,include_in_schema=False)
async def page():return (Path(__file__).resolve().parents[1]/'static/demo/index.html').read_text()

@router.get('/api/demo/summary')
async def summary():
    d=await snapshot()
    return {'as_of':d['as_of'],'investors':len(d['investors']),'prospects':len(d['prospects']),'deals':len(d['deals']),'private_clients':d['private_clients'],'bankers':d['bankers']}

@router.get('/api/demo/records/{kind}')
async def records(kind:str,q:str=Query('',max_length=150),sector:str=Query('',max_length=150),stage:str=Query('',max_length=80),category:str=Query('',max_length=100),banker:str=Query('',max_length=80),minimum:float|None=Query(None,ge=0),maximum:float|None=Query(None,ge=0),offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    if kind not in ('investors','prospects','deals'):raise HTTPException(404)
    d=await snapshot();rows=d[kind]
    if q:
        needle=q.casefold();rows=[r for r in rows if needle in str(r.get('name','')).casefold() or any(needle in str(c).casefold() for c in r.get('contacts',[]))]
    if sector:rows=[r for r in rows if sector.casefold() in (str(r.get('sector') or '')+' '+str(r.get('holding_sectors') or '')).casefold()]
    if stage:rows=[r for r in rows if r.get('stage')==stage or any(stage.casefold() in str(m.get('criteria',{}).get('stages',[])).casefold() for m in r.get('mandates',[]))]
    if banker:rows=[r for r in rows if any(b['id']==banker for b in r.get('bankers',[]))]
    if category:rows=[r for r in rows if category.casefold() in str(r.get('category') or '').casefold()]
    if minimum is not None or maximum is not None:
        def fits(r):
            for m in r.get('mandates',[]):
                c=m.get('criteria',{})
                try:
                    lo=float(c['check_min']) if c.get('check_min') is not None else None
                    hi=float(c['check_max']) if c.get('check_max') is not None else None
                    if lo is None and hi is None:continue
                    if (minimum is None or hi is None or hi>=minimum) and (maximum is None or lo is None or lo<=maximum):return True
                except (ValueError,TypeError):continue
            return False
        rows=[r for r in rows if fits(r)]
    return {'total':len(rows),'items':[{k:v for k,v in r.items() if k not in ('notes','description','contacts','mandates')} for r in rows[offset:offset+limit]],'offset':offset,'has_more':offset+limit<len(rows),'as_of':d['as_of']}

@router.get('/api/demo/record/{kind}/{identity}')
async def detail(kind:str,identity:str):
    if kind not in ('investors','prospects','deals'):raise HTTPException(404)
    d=await snapshot()
    for r in d[kind]:
        if r['id']==identity:return r
    raise HTTPException(404)
