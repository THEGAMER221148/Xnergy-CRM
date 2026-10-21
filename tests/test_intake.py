import base64,io,zipfile
from unittest.mock import AsyncMock
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from app import intake,main
from app.intake_sources import file_content,IntakeSourceError,office_text,public_addresses,website_text

def encoded(data):return base64.b64encode(data).decode()

def test_text_file_is_evidence_not_executed():
    r=file_content('contacts.csv',encoded(b'name,email\nTest,test@example.org'))
    assert r['type']=='input_text'
    assert 'Test,test@example.org' in r['text']

@pytest.mark.parametrize('filename,data',[('x.png',b'<script>'),('x.pdf',b'not a pdf'),('x.exe',b'test')])
def test_disguised_or_unsupported_files_rejected(filename,data):
    with pytest.raises(IntakeSourceError):file_content(filename,encoded(data))

def test_oversized_file_rejected():
    with pytest.raises(IntakeSourceError):file_content('x.txt',encoded(b'a'*(5*1024*1024+1)))

def test_docx_extracts_text_without_macros():
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w') as z:z.writestr('word/document.xml','<w:document xmlns:w="urn:test"><w:t>Jane</w:t><w:t>Investor</w:t></w:document>')
    assert office_text(b.getvalue(),'docx')=='Jane Investor'

@pytest.mark.asyncio
@pytest.mark.parametrize('url',['file:///etc/passwd','http://user:password@example.org','http://127.0.0.1','http://169.254.169.254/latest/meta-data','http://[::1]','http://example.org:8080'])
async def test_local_and_credential_urls_rejected(url):
    with pytest.raises(IntakeSourceError):await website_text(url)

@pytest.mark.asyncio
async def test_mixed_public_private_dns_rejected(monkeypatch):
    import asyncio,socket
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',AsyncMock(return_value=[(socket.AF_INET,0,0,'',('1.1.1.1',443)),(socket.AF_INET,0,0,'',('10.0.0.1',443))]))
    with pytest.raises(IntakeSourceError):await public_addresses('example.org',443)


def test_disabled_ai_never_reads_or_sends_source(monkeypatch):
    monkeypatch.setattr(intake,'require_user',AsyncMock(return_value={'user_id':'test','tenant_id':'test'}))
    monkeypatch.setattr(intake,'enforce_csrf',AsyncMock())
    monkeypatch.setattr(intake,'configured',lambda:False)
    provider=AsyncMock();monkeypatch.setattr(intake,'extract',provider)
    response=TestClient(main.app).post('/api/intake/preview',json={'text':'Private source'})
    assert response.status_code==503
    provider.assert_not_awaited()


def test_investor_role_cannot_use_intake(monkeypatch):
    async def reject(request,roles):
        assert 'investor' not in roles
        raise HTTPException(403)
    monkeypatch.setattr(intake,'require_user',reject)
    assert TestClient(main.app).post('/api/intake/preview',json={'text':'Source'}).status_code==403


def test_provider_cannot_add_tenant_or_permissions():
    with pytest.raises(ValueError):intake.validated_records({'records':[],'more_records_in_source':False,'explanation':'Done','tenant_id':'other'})

def test_contact_links_retained_but_scripts_hidden():
    from app.intake_sources import TextOnly
    parser=TextOnly();parser.feed('<a href="mailto:info@example.com">Contact</a><a href="tel:+12025550100">Call</a><script>secret</script>')
    assert any('info@example.com' in x for x in parser.parts)
    assert 'secret' not in parser.parts

def test_related_pages_stay_on_firm_site_and_skip_login():
    from app.intake_sources import related_pages
    result=related_pages('https://example.com/team/person/',['/contact','/investment-criteria','/login','https://other.example/team','javascript:alert(1)','/team/another'])
    assert result==['https://example.com/investment-criteria','https://example.com/contact','https://example.com/team/another']

@pytest.mark.asyncio
async def test_research_reads_related_pages_and_reports_limit(monkeypatch):
    from app import intake_sources as source
    async def page(url,allowed_host=None):
        if url=='https://example.com/':return 'Firm',url,['/contact','/team/person']
        assert allowed_host=='example.com'
        return 'Published data',url,[]
    monkeypatch.setattr(source,'website_page',page)
    pages,warnings=await source.website_research('https://example.com/',max_pages=2)
    assert len(pages)==2 and pages[1]['url'].endswith('/contact')
    assert warnings
