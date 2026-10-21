const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '../static/index.html'), 'utf8');
assert.match(html, /href="\/auth\/login"/);
assert.doesNotMatch(html, /type="password"|admin@xnergy\.local|\/api\/login/);
const elements = new Map();
function element(id) {
  if (!elements.has(id)) {
    const classes = new Set(['hidden']);
    elements.set(id, {textContent:'',innerHTML:'',value:'',style:{},classList:{add:x=>classes.add(x),remove:x=>classes.delete(x),contains:x=>classes.has(x)}});
  }
  return elements.get(id);
}
let signedIn = false;
const requests = [];
const context = vm.createContext({
  console, Headers, URL, URLSearchParams, setTimeout:()=>{}, location:{hash:"",href:""},
  document:{cookie:'dealos_csrf=test-csrf',getElementById:element,querySelectorAll:()=>[]},
  fetch:async(url,options={})=>{
    requests.push({url,options});
    let status=200, data={};
    if(url==='/api/me'){status=signedIn?200:401;data={display_name:'Nia',role:'admin'};}
    else if(url.startsWith('/api/investors?')||url==='/api/deals')data=[];
    else if(url==='/api/stats'||url==='/api/audit')status=404;
    return {status,ok:status<400,headers:{get:()=> 'application/json'},json:async()=>data,text:async()=> 'Unavailable'};
  },
  ...Object.fromEntries(['me','q','sorg','scontacts','sverified','sinter','quality','invrows','dealrows','auditrows'].map(id=>[id,element(id)])),
});
(async()=>{
  vm.runInContext(html.match(/<script>([\s\S]*?)<\/script>/)[1],context);
  await new Promise(setImmediate);
  assert.equal(element('login').classList.contains('hidden'),false);
  assert.equal(element('app').classList.contains('hidden'),true);
  signedIn=true;
  await vm.runInContext('boot()',context);
  assert.equal(element('login').classList.contains('hidden'),true);
  assert.equal(element('app').classList.contains('hidden'),false);
  assert.equal(element('me').textContent,'Nia · admin');
  assert.equal(element('workspaceNotice').classList.contains('hidden'),false);
  await vm.runInContext('api("/test",{method:"POST",headers:{"Content-Type":"application/json"}})',context);
  const request=requests.at(-1);
  assert.equal(request.options.headers.get('X-CSRF-Token'),'test-csrf');
  assert.equal(request.options.headers.get('Content-Type'),'application/json');
  assert.equal(request.options.credentials,'same-origin');
  await vm.runInContext('logout()',context);
  assert.equal(requests.at(-1).url,'/auth/logout');
  assert.equal(element('app').classList.contains('hidden'),true);
  console.log('PASS: Microsoft login link, no demo password form, session display, partial-section error notice, CSRF-protected mutations, and logout.');
})().catch(e=>{console.error(e);process.exitCode=1;});
