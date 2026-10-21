"""Bounded local-file extraction and public website reading for CRM intake."""
import asyncio,base64,binascii,io,ipaddress,json,socket,zipfile
from html.parser import HTMLParser
from urllib.parse import urlsplit,urljoin
from xml.etree import ElementTree as ET
import aiohttp

MAX_BYTES=5*1024*1024
class IntakeSourceError(ValueError):pass

class TextOnly(HTMLParser):
    def __init__(self):super().__init__();self.hidden=0;self.parts=[];self.links=[]
    def handle_starttag(self,tag,attrs):
        if tag in ('script','style','noscript'):self.hidden+=1
        if tag=='a' and not self.hidden:
            href=dict(attrs).get('href','')
            if href.startswith(('mailto:','tel:')):self.parts.append('Published contact link: '+href.split('?')[0])
            elif href:self.links.append(href)
    def handle_endtag(self,tag):
        if tag in ('script','style','noscript'):self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden and data.strip():self.parts.append(data.strip())

def file_content(filename,encoded):
    try:data=base64.b64decode(encoded,validate=True)
    except (ValueError,binascii.Error):raise IntakeSourceError('The attachment is not a valid file.')
    if not data or len(data)>MAX_BYTES:raise IntakeSourceError('Choose a non-empty file smaller than 5 MB.')
    ext=filename.lower().rsplit('.',1)[-1]
    if ext in ('png','jpg','jpeg','webp'):
        mime=None
        if data.startswith(b'\x89PNG\r\n\x1a\n'):mime='image/png'
        elif data.startswith(b'\xff\xd8\xff'):mime='image/jpeg'
        elif data[:4]==b'RIFF' and data[8:12]==b'WEBP':mime='image/webp'
        if not mime:raise IntakeSourceError('The image file is not a supported PNG, JPEG or WebP image.')
        return {'type':'input_image','image_url':'data:'+mime+';base64,'+encoded}
    if ext=='pdf':
        if not data.startswith(b'%PDF-'):raise IntakeSourceError('The PDF file is not valid.')
        return {'type':'input_file','filename':filename,'file_data':'data:application/pdf;base64,'+encoded}
    if ext in ('txt','csv','tsv'):
        try:text=data.decode('utf-8-sig')
        except UnicodeDecodeError:raise IntakeSourceError('Save this text file using UTF-8 encoding.')
    elif ext in ('xlsx','docx'):
        text=office_text(data,ext)
    else:raise IntakeSourceError('Use PNG, JPEG, WebP, PDF, DOCX, XLSX, CSV, TSV or TXT.')
    if len(text)>45000:raise IntakeSourceError('This file is too large for one intake. Split it into smaller batches; no rows were imported.')
    return {'type':'input_text','text':'Attachment: '+filename+'\n'+text}

def office_text(data,ext):
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            infos=z.infolist()
            if len(infos)>2000 or sum(x.file_size for x in infos)>20*1024*1024:raise IntakeSourceError('The document expands beyond the intake limit.')
            def xml(name):
                body=z.read(name)
                if b'<!DOCTYPE' in body or b'<!ENTITY' in body:raise IntakeSourceError('Unsupported document XML.')
                return ET.fromstring(body)
            if ext=='docx':return ' '.join(x.text or '' for x in xml('word/document.xml').iter() if x.tag.endswith('}t'))
            strings=[]
            if 'xl/sharedStrings.xml' in z.namelist():
                strings=[''.join(t.text or '' for t in x.iter() if t.tag.endswith('}t')) for x in xml('xl/sharedStrings.xml')]
            output=[]
            for name in sorted(z.namelist()):
                if not (name.startswith('xl/worksheets/sheet') and name.endswith('.xml')):continue
                output.append(name)
                for row in xml(name).iter():
                    if not row.tag.endswith('}row'):continue
                    values=[]
                    for cell in row:
                        if not cell.tag.endswith('}c'):continue
                        texts=[t.text or '' for t in cell.iter() if t.tag.endswith('}v') or t.tag.endswith('}t')]
                        value=' '.join(texts)
                        if cell.get('t')=='s' and value.isdigit():value=strings[int(value)]
                        values.append(cell.get('r','')+': '+value)
                    output.append(' | '.join(values))
            return '\n'.join(output)
    except IntakeSourceError:raise
    except (zipfile.BadZipFile,KeyError,ET.ParseError,IndexError,ValueError):raise IntakeSourceError('The Office file could not be read. Try exporting it as CSV or PDF.')

async def public_addresses(host,port):
    try:entries=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM)
    except socket.gaierror:raise IntakeSourceError('The website address could not be resolved.')
    result=[]
    for family,_,_,_,address in entries:
        ip=ipaddress.ip_address(address[0])
        if not ip.is_global or ip.is_multicast or ip.is_reserved:raise IntakeSourceError('Only public websites can be imported.')
        result.append({'hostname':host,'host':str(ip),'port':port,'family':family,'proto':0,'flags':0})
    if not result:raise IntakeSourceError('The website has no public address.')
    return result

class PinnedResolver(aiohttp.abc.AbstractResolver):
    def __init__(self,host,addresses):self.host=host;self.addresses=addresses
    async def resolve(self,host,port=0,family=socket.AF_INET):
        if host!=self.host:raise IntakeSourceError('Unexpected website redirect.')
        return self.addresses
    async def close(self):pass

async def website_page(url,allowed_host=None):
    for _ in range(4):
        u=urlsplit(url)
        if allowed_host and (u.hostname or '').removeprefix('www.')!=allowed_host:raise IntakeSourceError('Related pages must stay on the same firm website.')
        try:port=u.port or (443 if u.scheme=='https' else 80)
        except ValueError:raise IntakeSourceError('Invalid website port.')
        if u.scheme not in ('https','http') or not u.hostname or u.username or u.password or port not in (80,443):raise IntakeSourceError('Use a public HTTP or HTTPS website address.')
        host=u.hostname.encode('idna').decode('ascii')
        addresses=await public_addresses(host,port)
        connector=aiohttp.TCPConnector(resolver=PinnedResolver(host,addresses),use_dns_cache=False)
        async with aiohttp.ClientSession(connector=connector,trust_env=False,timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.get(url,allow_redirects=False,headers={'User-Agent':'Xnergy-DealOS-Intake/1.0'}) as response:
                if response.status in (301,302,303,307,308):
                    if not response.headers.get('Location'):raise IntakeSourceError('The website returned an invalid redirect.')
                    url=urljoin(url,response.headers['Location']);continue
                if response.status!=200:raise IntakeSourceError('The website could not be read. Upload a screenshot or paste its text instead.')
                if response.content_type not in ('text/html','text/plain','application/xhtml+xml'):raise IntakeSourceError('Use an HTML website page, or upload the file directly.')
                data=bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    data.extend(chunk)
                    if len(data)>2*1024*1024:raise IntakeSourceError('This page is too large. Use a specific team or investment-criteria page.')
                parser=TextOnly();parser.feed(data.decode(response.charset or 'utf-8',errors='replace'))
                text='\n'.join(parser.parts)
                if len(text)>45000:raise IntakeSourceError('This page contains too much text. Use a more specific page.')
                return text,url,parser.links
    raise IntakeSourceError('The website redirected too many times.')

async def website_text(url):
    text,final,_=await website_page(url)
    return text,final

def related_pages(base,links):
    host=(urlsplit(base).hostname or '').removeprefix('www.')
    ranked={}
    for link in links:
        url=urljoin(base,link).split('#')[0]
        u=urlsplit(url)
        if u.scheme not in ('http','https') or u.username or u.password or (u.hostname or '').removeprefix('www.')!=host or u.query:continue
        path=u.path.lower()
        if any(x in path for x in ['login','privacy','legal','press','news','portfolio','disclosure','logout']):continue
        score=next((score for terms,score in [(('criteria','strategy','strategies','approach','investment-focus'),0),(('contact','about'),1),(('team','people','professionals','leadership'),2)] if any(t in path for t in terms)),None)
        if score is not None:ranked[url]=score
    return sorted(ranked,key=lambda u:(ranked[u],len(urlsplit(u).path)))

async def website_research(url,max_pages=8):
    first,final,links=await website_page(url)
    host=(urlsplit(final).hostname or '').removeprefix('www.')
    pages=[{'url':final,'text':first}];seen={final};warnings=[]
    candidates=related_pages(final,links)
    # Read only public links discovered on the supplied firm site; never search unrelated sites.
    while candidates and len(pages)<max_pages:
        target=candidates.pop(0)
        if target in seen:continue
        seen.add(target)
        if len(seen)>16:break
        try:
            text,resolved,more=await website_page(target,allowed_host=host)
            if resolved in {p['url'] for p in pages}:continue
            if sum(len(p['text']) for p in pages)+len(text)>90000:
                warnings.append('The website text limit was reached. Import a specific profile or criteria page for more detail.');break
            pages.append({'url':resolved,'text':text})
            candidates=related_pages(final,candidates+related_pages(resolved,more))
        except (IntakeSourceError,asyncio.TimeoutError,aiohttp.ClientError):
            warnings.append('Could not read related page: '+target)
    if candidates:warnings.append('Read up to '+str(max_pages)+' relevant pages. This is not a complete website import; use individual profile URLs for additional contacts.')
    return pages,warnings
