/* Persistent investor research tasks, separate from personal assignments. */
(()=>{
 const hiddenStyle=document.createElement('style');hiddenStyle.textContent='main [hidden]{display:none!important}';document.head.append(hiddenStyle);
 const taskBox=document.getElementById('tasks'), section=taskBox.closest('section');
 const manual=[...section.children].filter(x=>x.tagName!=='H2');
 const tabs=document.createElement('div');tabs.className='tools';tabs.setAttribute('aria-label','Work queues');
 const names={manual:'My work',working:'AI working',input:'Needs my input',finished:'Completed research',paused:'Paused'};
 tabs.innerHTML=Object.entries(names).map(([k,v])=>`<button class="btn" type="button" data-view="${k}" aria-pressed="${k==='manual'}">${v}</button>`).join('');
 section.querySelector('h2').after(tabs);
 const add=document.createElement('div');add.className='tools';add.innerHTML='<strong>Add with AI</strong><a class="btn primary" href="/static/workspace/talk.html?category=prospect">Company prospect</a><a class="btn primary" href="/static/workspace/talk.html?category=investor">Investor / buyer</a>';tabs.before(add);
 const area=document.createElement('div');area.hidden=true;
 area.innerHTML='<p>AI fills supported missing details and saves them to the CRM. Incomplete investors remain searchable. Research currently checks identified websites and SEC adviser records.</p><form id="aiSearch" class="tools"><label class="field">Find investor research<input id="aiQuery" maxlength="200" placeholder="Investor or firm name"></label><button class="btn">Search</button><button class="btn" type="button" id="aiRefresh">Refresh</button></form><p role="status" id="aiNotice"></p><div id="aiRows"></div><div class="tools"><button class="btn" id="aiPrev">Previous</button><button class="btn" id="aiNext">Next</button></div>';
 section.append(area);
 let view='manual',offset=0,version=0;
 const labels={pending:'Queued',running:'Working',completed:'Profile complete',needs_follow_up:'Partially updated / needs input',failed:'Could not complete'};
 const e=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 async function call(path,body){const headers={};if(body){headers['Content-Type']='application/json';const cookie=document.cookie.split('; ').find(x=>x.startsWith('dealos_csrf='));if(cookie)headers['X-CSRF-Token']=decodeURIComponent(cookie.slice(12));}const r=await fetch(path,{method:body?'POST':'GET',headers,credentials:'same-origin',body:body?JSON.stringify(body):undefined});const d=await r.json();if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Unable to load research. Please sign in or try again.');return d;}
 const notice=s=>document.getElementById('aiNotice').textContent=s;
 const parsed=x=>typeof x==='string'?JSON.parse(x):x;
 async function results(id){const d=await call('/api/ai-work/'+id),p=parsed(d.proposal)||{},changes=parsed(d.saved_changes);
  const dialog=document.createElement('dialog');dialog.style.cssText='max-width:800px;width:90vw;max-height:85vh;overflow:auto;border:1px solid #92B2CD;border-radius:14px;padding:24px';
  dialog.innerHTML=`<button class="btn" autofocus type="button">× Close</button><h2>${e(d.name)}</h2><p>${e(d.result_summary||'No research result yet.')}</p><p>Attempts: ${d.attempts} · ${e(d.finished_at?new Date(d.finished_at).toLocaleString():'Awaiting results')}</p><h3>Saved changes</h3>${changes?`<dl>${Object.entries(changes.organization||{}).map(([k,v])=>`<dt><strong>${e(k.replaceAll('_',' '))}</strong></dt><dd>${e(v.before||'Not recorded')} → ${e(v.after)}</dd>`).join('')}</dl><p>Contacts: ${changes.contacts_before} → ${changes.contacts_after} · Criteria groups: ${changes.criteria_groups_before} → ${changes.criteria_groups_after}</p><p>Open the CRM record for the saved contact and criteria details.</p>`:'<p>No saved change report for this attempt. Earlier research may predate this report.</p>'}<h3>Sources checked</h3>${(p.sources||[]).filter(x=>/^https?:\/\//i.test(x)).map(x=>`<p><a href="${e(x)}" target="_blank" rel="noopener noreferrer">${e(x)}</a></p>`).join('')||'<p>No source links recorded.</p>'}<p>${e((p.source_warnings||[]).join(' '))}</p><a class="btn" href="/static/workspace/index.html#investor=${encodeURIComponent(d.organization_id)}">Open investor record</a>`;
  document.body.append(dialog);dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove());dialog.showModal();
 }
 async function load(){if(view==='manual')return;const n=++version;notice('Loading research…');try{const data=await call('/api/ai-work?'+new URLSearchParams({view,q:document.getElementById('aiQuery').value,offset}));if(n!==version)return;
 document.getElementById('aiRows').innerHTML=data.items.map(j=>`<article class="item"><h3>${e(j.name)}</h3><p>${e(j.paused?'Paused':labels[j.status])} · ${j.attempts} attempts</p><p>${e(j.result_summary||'Waiting for a research worker. Prioritized requests run first, subject to source and daily limits.')}</p><div class="tools"><a class="btn" href="/static/workspace/index.html#investor=${encodeURIComponent(j.organization_id)}">Edit contact / profile</a><button class="btn" data-result="${j.id}">View findings</button>${data.can_edit&&j.status!=='running'?`<button class="btn primary" data-id="${j.id}" data-action="run">${j.paused?'Resume':j.attempts?'Retry with AI':'Assign to AI'}</button>${j.status==='pending'&&!j.paused?`<button class="btn" data-id="${j.id}" data-action="pause">Pause</button>`:''}`:''}</div></article>`).join('')||'<p>No research tasks in this view.</p>';
 document.getElementById('aiPrev').disabled=offset===0;document.getElementById('aiNext').disabled=!data.has_more;notice(`Showing ${data.items.length} research tasks${offset?' · starting at '+(offset+1):''}.`);
 area.querySelectorAll('[data-result]').forEach(b=>b.onclick=()=>results(b.dataset.result).catch(x=>notice(x.message)));
 area.querySelectorAll('[data-action]').forEach(b=>b.onclick=async()=>{b.disabled=true;try{const r=await call('/api/ai-work/'+b.dataset.id,{action:b.dataset.action});await load();notice(r.message);}catch(x){notice(x.message);b.disabled=false;}});
 }catch(x){if(n===version)notice(x.message);}}
 function select(v){view=v;offset=0;version++;manual.forEach(x=>x.hidden=v!=='manual');area.hidden=v==='manual';tabs.querySelectorAll('button').forEach(b=>{b.setAttribute('aria-pressed',String(b.dataset.view===v));b.classList.toggle('primary',b.dataset.view===v);});load();}
 tabs.querySelectorAll('button').forEach(b=>b.onclick=()=>select(b.dataset.view));
 document.getElementById('aiSearch').onsubmit=x=>{x.preventDefault();offset=0;load();};document.getElementById('aiRefresh').onclick=load;
 document.getElementById('aiPrev').onclick=()=>{offset=Math.max(0,offset-25);load();};document.getElementById('aiNext').onclick=()=>{offset+=25;load();};
 setInterval(()=>{if(!document.hidden&&view==='working')load();},30000);
 window.addEventListener('hashchange',()=>{if(location.hash==='#ai-work')select('working');});
 if(location.hash==='#ai-work')select('working');
 const coverage=document.createElement('details');coverage.innerHTML='<summary>Research sources and connections</summary>';area.prepend(coverage);call('/api/investor-research-status').then(d=>(d.sources||[]).forEach(x=>{const p=document.createElement('p');p.textContent=x.name+' — '+x.status+'. '+x.detail;coverage.append(p);})).catch(()=>{});
})();
