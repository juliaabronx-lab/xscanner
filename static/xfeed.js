// xScanner · X Live Feed (read-only). Polls /api/xfeed/heads for the "new posts" badge and /api/xfeed for the cards.
(function(){
const TOPICS=['memecoins','politics','tech'];
const LS='xs_xfeed_v1';
const st=JSON.parse(localStorage.getItem(LS)||'{}');
let topic=TOPICS.includes(st.topic)?st.topic:'memecoins';
const seen={}; TOPICS.forEach(t=>seen[t]=new Set((st.seen&&st.seen[t])||[]));
const firstRun=!st.seen;
let lastHeads={}, renderedTopic=null, every=+(st.every??15), countdown=1, lastCheck=null, updatedAt=null, rendered=new Set(), pending=[], loading=false, failed=false;
function save(){const o={topic,every,seen:{}};TOPICS.forEach(t=>o.seen[t]=[...seen[t]].slice(-500));localStorage.setItem(LS,JSON.stringify(o))}
const k=v=>v==null?'—':v>=1e6?(v/1e6).toFixed(1).replace(/\.0$/,'')+'M':v>=1e3?(v/1e3).toFixed(1).replace(/\.0$/,'')+'K':String(v);
function ago(iso){if(!iso)return '—';const s=Math.max(0,(Date.now()-Date.parse(iso))/1000);
  return s<45?Math.round(s)+'s':s<3600?Math.round(s/60)+'m':s<86400?Math.round(s/3600)+'h':Math.round(s/86400)+'d'}
const myt=iso=>iso?new Date(iso).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' MYT':'';
function hue(s){let h=0;for(const c of String(s))h=(h*31+c.charCodeAt(0))%360;return h}
function terms(){return $('#xQ').value.toLowerCase().split(/\s+/).filter(Boolean).slice(0,8)}
function rich(text,cas){
  const tq=terms().filter(w=>w.length>1).map(w=>w.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'));
  const parts=['https?:\\/\\/[^\\s]+','\\$[A-Za-z][A-Za-z0-9_]{1,14}'];
  if(cas&&cas.length)parts.unshift(cas.map(c=>c.replace(/[^1-9A-HJ-NP-Za-km-z]/g,'')).join('|'));
  if(tq.length)parts.push(tq.join('|'));
  const re=new RegExp('('+parts.join('|')+')','gi');
  return String(text||'').split(re).map((p,i)=>{
    if(i%2===0||!p)return esc(p);
    if(/^https?:/i.test(p))return `<a href="${esc(p)}" target="_blank" rel="noopener nofollow">${esc(p.replace(/^https?:\/\//,'').slice(0,40))}</a>`;
    if(cas&&cas.includes(p))return `<span class="ca">${esc(p)}</span>`;
    if(p[0]==='$'&&/^\$[A-Za-z]/.test(p))return `<span class="tk">${esc(p)}</span>`;
    return `<mark>${esc(p)}</mark>`;}).join('');
}
function card(p,fresh){
  const m=p.metrics||{}, h=hue(p.handle);
  const cas=(p.cas||[]);
  const caHtml=(topic==='memecoins'&&cas.length)?`<div class="xcas">${cas.slice(0,3).map(c=>`<div class="xca"><img class="xava" src="/api/img/${encodeURIComponent(c)}" alt="" loading="lazy" decoding="async" onerror="this.remove()"><code title="${esc(c)}">CA ${esc(c)}</code>
     <button class="cp" data-cp="${esc(c)}" title="Copy CA">Copy</button><button class="scan" data-ca="${esc(c)}">Scan CA</button></div>`).join('')}</div>`:'';
  const long=(p.text||'').length>420||(p.text||'').split('\n').length>9;
  return `<article class="xcard${fresh?' fresh':''}" data-id="${esc(p.id)}">
    <div class="xhead"><div class="xav" style="background:hsl(${h} 55% 38%)">${esc((p.author||p.handle||'?').trim().charAt(0).toUpperCase())}</div>
      <div class="xwho"><b>${esc(p.author||p.handle)}${p.verified?'<span class="xver" title="Verified">✔</span>':''}</b>
      <small class="muted">@${esc(p.handle)} · ${k(p.followers)} followers</small></div>
      <span class="xtime" data-t="${esc(p.created_at)}" title="${esc(myt(p.created_at))}">${ago(p.created_at)} ago</span></div>
    <div class="xtext${long?' long':''}">${rich(p.text,cas)}</div>${caHtml}
    <div class="xfoot"><span title="Likes">♥ ${k(m.likes||0)}</span><span title="Reposts">⟲ ${k(m.reposts||0)}</span><span title="Replies">💬 ${k(m.replies||0)}</span>${m.views!=null?`<span title="Views">👁 ${k(m.views)}</span>`:''}
      <a href="${esc(p.url)}" target="_blank" rel="noopener">Open on X ↗</a></div></article>`;
}
function params(){return new URLSearchParams({topic,q:$('#xQ').value.trim(),sort:$('#xSort').value,min_followers:$('#xMinF').value,ca_only:$('#xCaOnly').checked,limit:300})}
async function fetchList(){const r=await fetch('/api/xfeed?'+params());if(!r.ok)throw new Error('HTTP '+r.status);return r.json()}
function setCounts(counts){$$('#xTopics button').forEach(b=>{const c=counts&&counts[b.dataset.t];b.querySelector('.cnt').textContent=c!=null?c:''})}
function setBadges(unseen){
  let total=0;
  $$('#xTopics button').forEach(b=>{const n=unseen[b.dataset.t]||0;total+=n;const e=b.querySelector('.nbadge');e.textContent=n>99?'99+':n;e.classList.toggle('hidden',!n)});
  const nb=$('#xNavBadge');nb.textContent=total>99?'99+':total;nb.classList.toggle('hidden',!total);
}
function renderAll(d,freshIds){
  const posts=d.posts||[]; renderedTopic=topic; rendered=new Set(posts.map(p=>p.id)); pending=[];
  $('#xNew').classList.add('hidden');
  $('#xCards').innerHTML=posts.length?posts.map(p=>card(p,freshIds&&freshIds.has(p.id))).join('')
    :`<div class="xempty">${d.error==='missing'?'No X feed yet: data/x_feed.json has not been created.':'No posts match these filters.'}</div>`;
  posts.forEach(p=>seen[topic].add(p.id)); (lastHeads[topic]||[]).forEach(id=>seen[topic].add(id)); save();
  if(window.XS)XS.applyEnter($('#xCards'),'.xcard');
}
function skelX(n=6){return Array.from({length:n},()=>`<article class="xcard skel-card" aria-hidden="true"><div class="xhead"><div class="skel" style="width:36px;height:36px;border-radius:50%;flex:0 0 36px"></div><div style="flex:1"><div class="skel skel-line mid"></div><div class="skel skel-line short"></div></div></div><div class="skel skel-line"></div><div class="skel skel-line mid"></div></article>`).join('')}
async function loadX(fresh){
  if(loading)return; loading=true;
  if(!$('#xCards').querySelector('.xcard:not(.skel-card)')){$('#xCards').innerHTML=skelX();$('#xCards').setAttribute('aria-busy','true')}
  try{const d=await fetchList(); updatedAt=d.updated_at; setCounts(d.counts); renderAll(d,fresh); failed=false; lastCheck=Date.now(); await pollHeads(true)}
  catch(e){failed=true;$('#xCards').innerHTML=`<div class="xempty">Couldn't load the X feed (${esc(e.message)}). Retrying…</div>`;if(window.XS)XS.err()}
  finally{loading=false;$('#xCards').removeAttribute('aria-busy');status()}
}
async function pollHeads(skipList){
  const r=await fetch('/api/xfeed/heads?n=300'); if(!r.ok)throw new Error('HTTP '+r.status); const h=await r.json();
  updatedAt=h.updated_at; lastHeads=h.heads||{};
  if(firstRun&&!st.seen){TOPICS.forEach(t=>(h.heads[t]||[]).forEach(id=>seen[t].add(id)));st.seen=true;save()}
  const unseen={}; TOPICS.forEach(t=>unseen[t]=(h.heads[t]||[]).filter(id=>!seen[t].has(id)).length);
  const counts={};TOPICS.forEach(t=>counts[t]=(h.topics[t]||{}).count);setCounts(counts);
  if(view==='xfeed'&&!skipList&&unseen[topic]){
    const d=await fetchList(); pending=d.posts||[];
    const nNew=pending.filter(p=>!rendered.has(p.id)).length;
    const b=$('#xNew'); b.textContent=`↑ ${nNew||unseen[topic]} new post${(nNew||unseen[topic])>1?'s':''} · show`; b.classList.toggle('hidden',!(nNew||unseen[topic]));
    b._data=d;
  }
  setBadges(unseen); lastCheck=Date.now(); failed=false;
}
async function tick(){
  if(document.hidden)return;
  status();
  if(!every)return;
  if(--countdown>0)return;
  countdown=every;
  try{await pollHeads(false)}catch(e){failed=true}
  status();
}
function status(){
  const el=$('#xLive'); el.classList.toggle('off',!every); el.classList.toggle('err',failed);
  $('#xStatus').textContent=failed?'connection problem, retrying':
    `feed updated ${updatedAt?ago(updatedAt)+' ago':'—'}`+(every?` · next check ${Math.max(0,countdown)}s`:' · auto-refresh off');
  $('#xStatus').title=updatedAt?'Feed file updated '+myt(updatedAt):'';
}
function setTopic(t){topic=t;save();$$('#xTopics button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
  $('#xCaOnly').closest('label').classList.toggle('hidden',t!=='memecoins');loadX()}
// wire up
$$('#xTopics button').forEach(b=>b.onclick=()=>setTopic(b.dataset.t));
let qT; $('#xQ').oninput=()=>{clearTimeout(qT);qT=setTimeout(()=>loadX(),250)};
['#xSort','#xMinF','#xCaOnly'].forEach(s=>$(s).onchange=()=>loadX());
$('#xEvery').value=String(every); $('#xEvery').onchange=()=>{every=+$('#xEvery').value;countdown=every||1;save();status()};
$('#xNew').onclick=()=>{const d=$('#xNew')._data; if(!d)return loadX(); const fresh=new Set((d.posts||[]).filter(p=>!rendered.has(p.id)).map(p=>p.id));
  renderAll(d,fresh); window.scrollTo({top:0,behavior:'smooth'}); pollHeads(true).then(status)};
$('#xCards').addEventListener('click',async e=>{
  const s=e.target.closest('button.scan'); if(s){e.preventDefault();runLookup(s.dataset.ca);return}
  const c=e.target.closest('button.cp'); if(c){try{await navigator.clipboard.writeText(c.dataset.cp);c.textContent='Copied'}catch(_){c.textContent='Copy failed'}setTimeout(()=>c.textContent='Copy',1500)}
});
document.addEventListener('visibilitychange',()=>{if(!document.hidden){countdown=1}});
// integrate with app.js tab system
loaders.xfeed=()=>(renderedTopic===topic?pollHeads(false).then(status):loadX());
$$('#tabs button').forEach(b=>b.onclick=()=>show(b.dataset.v));
$$('#xTopics button').forEach(b=>b.classList.toggle('on',b.dataset.t===topic));
$('#xCaOnly').closest('label').classList.toggle('hidden',topic!=='memecoins');
if((window.__h0||location.hash||'').slice(1)==='xfeed')show('xfeed'); else pollHeads(true).catch(()=>{});
setInterval(tick,1000);
setInterval(()=>$$('#xCards .xtime').forEach(e=>e.textContent=ago(e.dataset.t)+' ago'),20000);

})();
