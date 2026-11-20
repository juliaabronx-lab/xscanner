// xScanner trader layout: Pulse board, token page, watchlist, wallet tracker. Read-only: no keys, no swaps, no orders.
// Data: /api/pulse (incremental), /api/token, /api/trades, /api/dev, /api/wallet, /api/quotes. Ideas (three columns, filters, presets,
// token page layout) follow Axiom's public docs; no code or assets are copied and no Axiom API is called.
(()=>{
'use strict';
const LS=(k,d)=>{try{const v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}};
const LSs=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const now=()=>Date.now()/1000;
const ageS=s=>{if(s==null||!isFinite(s))return'—';s=Math.max(0,Math.floor(s));if(s<60)return s+'s';if(s<3600)return Math.floor(s/60)+'m';if(s<86400)return Math.floor(s/3600)+'h '+(Math.floor(s/60)%60)+'m';return Math.floor(s/86400)+'d'};
const num=v=>v==null||v===''||!isFinite(+v)?null:+v;
const pc1=v=>v==null?'—':`<span class="${v>=0?'up':'dn'}">${v>=0?'+':''}${Math.abs(v)>=1000?Math.round(v).toLocaleString():(+v).toFixed(1)}%</span>`;
const pTxt=(v,d=1)=>v==null?'—':(+v).toFixed(d)+'%';
const hostOk=u=>{try{const x=new URL(u);return x.protocol==='https:'||x.protocol==='http:'}catch(e){return false}};
const ext=(u,t,cls='')=>hostOk(u)?`<a class="${cls}" href="${esc(u)}" target="_blank" rel="noopener noreferrer nofollow">${t}</a>`:'';
const WSOL='So11111111111111111111111111111111111111112';
const B58=/^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
const toast=(msg,cls='')=>{let b=document.getElementById('toasts');if(!b){b=document.createElement('div');b.id='toasts';document.body.appendChild(b)}
  const t=document.createElement('div');t.className='toast '+cls;t.innerHTML=msg;b.appendChild(t);setTimeout(()=>t.classList.add('out'),6500);setTimeout(()=>t.remove(),7200)};
window.toast=toast;
const symOf=r=>r.s||r.sym||'?';
const SC=['feed','narr','risk','paper','xfeed'];
const isP=v=>v==='pulse'||v==='trend';
function navSync(){const sc=SC.includes(view);const h=$('#scHead');if(h)h.classList.toggle('hidden',!sc);const n=$('#scNav');if(n)n.classList.toggle('on',sc);
  $$('#subtabs button').forEach(b=>b.classList.toggle('on',b.dataset.v===view))}
const kvRow=(k,v,extra='')=>`<div class="kvr"><span class="kk">${k}</span><span class="kvv">${v}</span>${extra?`<span class="kx">${extra}</span>`:''}</div>`;
const addrCell=(a,kind='account')=>a?`<code class="addr" title="${esc(a)}">${esc(a)}</code>`:'—';
const addrBtns=(a,kind='account')=>a?`<button class="cpy mini" data-cp="${esc(a)}" title="Copy">⧉</button><a class="xl" href="https://solscan.io/${kind}/${esc(a)}" target="_blank" rel="noopener noreferrer" title="Open in Solscan">Solscan ↗</a>`:'';
const dateS=t=>t?new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' MYT':'—';
const tokImgDirect=(r,cls='pimgi')=>{const u=r.img&&hostOk(r.img)?r.img:imgUrl(r.m,symOf(r));
  return `<img class="${cls}" src="${esc(u)}" alt="" loading="lazy" decoding="async" referrerpolicy="no-referrer" onerror="if(!this.dataset.f){this.dataset.f=1;this.src='${imgUrl(r.m,symOf(r))}'}else{this.style.visibility='hidden'}">`};
const jupLink=m=>'https://jup.ag/swap/SOL-'+m;
const axLink=(r)=>'https://axiom.trade/meme/'+(r.p||r.m)+'?chain=sol';
const phLink=(r)=>'https://photon-sol.tinyastro.io/en/lp/'+(r.p||r.m);

// ===================================================================== watchlist store (localStorage)
const W={list:LS('xs_watch_v1',[]),alerts:LS('xs_watch_alerts_v1',[]),cfg:LS('xs_watch_cfg_v1',{pct:20,notify:false}),q:{},unseen:0,timer:null};
const saveW=()=>{LSs('xs_watch_v1',W.list);LSs('xs_watch_alerts_v1',W.alerts.slice(0,60));LSs('xs_watch_cfg_v1',W.cfg)};
const isWatched=m=>W.list.some(x=>x.m===m);
function toggleWatch(m,info){
  const i=W.list.findIndex(x=>x.m===m);
  if(i>=0){W.list.splice(i,1);toast('Removed $'+esc(info?.s||'')+' from your watchlist')}
  else{W.list.unshift({m,s:info?.s||'?',n:info?.n||'',ref:info?.px||null,added:Math.floor(now()),px0:info?.px||null});toast('★ Watching $'+esc(info?.s||'')+' — alerts at ±'+W.cfg.pct+'%')}
  saveW();paintStars();if(view==='watch')renderWatch();watchPoll(true)}
function paintStars(){$$('[data-star]').forEach(b=>{const on=isWatched(b.dataset.star);b.classList.toggle('on',on);b.textContent=on?'★':'☆';b.title=on?'Remove from watchlist':'Add to watchlist'});
  const n=W.unseen;const bd=$('#wNavBadge');if(bd){bd.textContent=n;bd.classList.toggle('hidden',!n)}}
document.addEventListener('click',e=>{const b=e.target.closest('[data-star]');if(!b)return;e.preventDefault();e.stopPropagation();
  const m=b.dataset.star;const r=P.rows.get(m)||(TK.cur&&TK.cur.mint===m?{s:TK.cur.symbol,n:TK.cur.name,px:TK.cur.market?.px}:null)||{};toggleWatch(m,{s:symOf(r),n:r.n||r.name,px:r.px});});
async function watchPoll(force){
  if(!W.list.length||document.hidden&&!force)return;
  try{const j=await api('/api/quotes?mints='+W.list.slice(0,30).map(x=>x.m).join(','));W.q=j.quotes||{};W.t=j.t}catch(e){return}
  for(const w of W.list){const q=W.q[w.m];if(!q||!q.px)continue;if(!w.ref){w.ref=q.px;w.px0=w.px0||q.px;continue}
    if(!w.px0)w.px0=q.px;if(q.sym&&w.s==='?')w.s=q.sym;
    const mv=(q.px/w.ref-1)*100;
    if(Math.abs(mv)>=W.cfg.pct){const a={t:Math.floor(now()),m:w.m,s:w.s,from:w.ref,to:q.px,pct:mv};W.alerts.unshift(a);w.ref=q.px;W.unseen++;
      const msg=`$${esc(w.s)} ${mv>=0?'▲':'▼'} ${Math.abs(mv).toFixed(1)}% (${fmtPx(a.from)} → ${fmtPx(a.to)})`;toast(`<b>Watchlist alert</b> ${msg}`,mv>=0?'up':'dn');
      if(W.cfg.notify&&window.Notification&&Notification.permission==='granted'){try{new Notification('xScanner: $'+w.s+' '+(mv>=0?'up ':'down ')+Math.abs(mv).toFixed(1)+'%',{body:fmtPx(a.from)+' → '+fmtPx(a.to)+' · read-only alert, not advice',tag:'xs-'+w.m})}catch(e){}}}}
  saveW();paintStars();if(view==='watch')renderWatch()}
function renderWatch(){
  $('#wlCount').textContent=W.list.length?`(${W.list.length})`:'';$('#wlPct').value=String(W.cfg.pct);$('#wlNotif').checked=!!W.cfg.notify;
  $('#wlList').innerHTML=W.list.length?`<div class="tablewrap"><table class="wltab"><thead><tr><th>Token</th><th>Price</th><th>5m</th><th>1h</th><th>24h</th><th>MCap</th><th>Since added</th><th></th></tr></thead><tbody>`+W.list.map(w=>{const q=W.q[w.m]||{};
    const since=q.px&&w.px0?(q.px/w.px0-1)*100:null;
    return `<tr><td><a href="/token/${esc(w.m)}" class="tl" data-tok="${esc(w.m)}">${tokImg(w.m,w.s,'timg')}<b>$${esc(w.s)}</b></a> <small class="muted">${esc(shortCa(w.m))}</small></td><td>${q.px?fmtPx(q.px):'—'}</td><td>${pc1(q.pc5)}</td><td>${pc1(q.pc1)}</td><td>${pc1(q.pc24)}</td><td>${usd(q.mc)}</td><td>${pc1(since)}</td><td><button class="linkbtn" data-star="${esc(w.m)}">★</button></td></tr>`}).join('')+`</tbody></table></div>`
    :`<p class="muted">Nothing yet. Tap ☆ on any Pulse row or token page, or paste a CA above.</p>`;
  $('#wlAlerts').innerHTML=W.alerts.length?W.alerts.slice(0,30).map(a=>`<div class="alrt ${a.pct>=0?'up':'dn'}"><b>$${esc(a.s)}</b> ${a.pct>=0?'▲':'▼'} ${Math.abs(a.pct).toFixed(1)}% <small class="muted">${fmtPx(a.from)} → ${fmtPx(a.to)} · ${ageS(now()-a.t)} ago</small></div>`).join(''):`<p class="muted">No alerts yet. You get one when a watched coin moves ±${W.cfg.pct}% from the last alert price, while this page is open.</p>`;
  paintStars()}
$('#wlPct').onchange=e=>{W.cfg.pct=+e.target.value;saveW()};
$('#wlNotif').onchange=async e=>{if(e.target.checked){if(!window.Notification){e.target.checked=false;toast('This browser has no notification support.');return}
  const p=await Notification.requestPermission();W.cfg.notify=p==='granted';e.target.checked=W.cfg.notify;if(!W.cfg.notify)toast('Notifications are blocked in the browser settings.')}else W.cfg.notify=false;saveW()};
$('#wlClear').onclick=()=>{W.alerts=[];W.unseen=0;saveW();renderWatch()};
$('#wlAdd').onsubmit=async e=>{e.preventDefault();const v=$('#wlIn').value.trim(),m=(typeof extractMint==='function'?extractMint(v):v);const box=$('#wlMsg');
  if(!B58.test(m||'')){box.className='searchmsg warn';box.textContent='That is not a Solana token address (32-44 letters/numbers).';return}
  box.className='searchmsg hidden';try{const j=await api('/api/quotes?mints='+m);const q=(j.quotes||{})[m];if(!q){box.className='searchmsg warn';box.textContent='No market found for that token yet.';return}
    if(!isWatched(m))toggleWatch(m,{s:q.sym,n:q.name,px:q.px});$('#wlIn').value=''}catch(err){box.className='searchmsg err';box.textContent='Could not look that token up: '+err.message}};

// ===================================================================== PULSE
const P={rows:new Map(),cols:{new:[],final:[],migrated:[]},ver:0,h:'',busy:false,els:new Map(),seg:'new',warming:true,okAt:0,first:true,src:null,filt:null,stale:0};
const DEF_F={minLiq:0,minMc:0,maxMc:0,maxTop10:0,maxDev:0,maxAge:0,minHolders:0,minTx:0,hideBundled:false,hasSocials:false};
const BUILTIN={'All':{},'Hide dust':{minLiq:1000},'Clean':{minLiq:3000,maxTop10:30,maxDev:5,hideBundled:true},'Fresh + socials':{maxAge:30,hasSocials:true,minLiq:2000},'Active':{minLiq:5000,minTx:40,minHolders:60},'Low cap':{maxMc:30000,minLiq:2500,hideBundled:true}};
P.filt={...DEF_F,...LS('xs_pulse_filters_v1',{minLiq:1000})};
let presets=LS('xs_pulse_presets_v1',{});
let activePreset=LS('xs_pulse_active_v1','Hide dust');
const fNum=v=>{v=parseFloat(v);return isFinite(v)&&v>0?v:0};
function passes(r){const F=P.filt,age=r.cr?now()-r.cr:null;
  if(F.minLiq&&(r.lq??0)<F.minLiq)return false;
  if(F.minMc&&(r.mc??0)<F.minMc)return false;
  if(F.maxMc&&r.mc!=null&&r.mc>F.maxMc)return false;
  if(F.maxTop10&&(r.t10??r.t10x)!=null&&(r.t10??r.t10x)>F.maxTop10)return false;
  if(F.maxDev&&r.dv!=null&&r.dv>F.maxDev)return false;
  if(F.maxAge&&age!=null&&age>F.maxAge*60)return false;
  if(F.minHolders&&(r.h??0)<F.minHolders)return false;
  if(F.minTx&&((r.b1||0)+(r.s1||0))<F.minTx)return false;
  if(F.hideBundled&&(r.bd==='flagged'||r.ebl))return false;
  if(F.hasSocials&&!(r.tw||r.tg||r.web))return false;
  return true}
function filtCount(){return Object.entries(P.filt).filter(([k,v])=>v&&v!==DEF_F[k]).length}
function paintFilters(){const f=$('#pFilters');for(const [k,v] of Object.entries(P.filt)){const el=f.elements[k];if(!el)continue;if(el.type==='checkbox')el.checked=!!v;else el.value=v||''}
  const n=filtCount(),b=$('#pFiltN');b.textContent=n;b.classList.toggle('hidden',!n)}
function paintPresets(){const names=[...Object.keys(BUILTIN),...Object.keys(presets)];
  $('#pPresets').innerHTML=names.map(n=>`<button type="button" data-p="${esc(n)}" class="${n===activePreset?'on':''}">${esc(n)}${presets[n]?` <i data-del="${esc(n)}" title="Delete preset">×</i>`:''}</button>`).join('')}
function setFilters(o,name){P.filt={...DEF_F,...o};activePreset=name||'';LSs('xs_pulse_filters_v1',P.filt);LSs('xs_pulse_active_v1',activePreset);paintFilters();paintPresets();renderAll()}
$('#pPresets').onclick=e=>{const d=e.target.closest('[data-del]');if(d){e.stopPropagation();delete presets[d.dataset.del];LSs('xs_pulse_presets_v1',presets);if(activePreset===d.dataset.del)activePreset='';paintPresets();return}
  const b=e.target.closest('[data-p]');if(!b)return;const n=b.dataset.p;setFilters(BUILTIN[n]||presets[n]||{},n)};
$('#pFiltBtn').onclick=()=>{const f=$('#pFilters'),open=f.classList.toggle('hidden')===false;$('#pFiltBtn').setAttribute('aria-expanded',open)};
$('#pFilters').addEventListener('input',e=>{const el=e.target;if(!el.name)return;P.filt[el.name]=el.type==='checkbox'?el.checked:fNum(el.value);activePreset='';LSs('xs_pulse_filters_v1',P.filt);LSs('xs_pulse_active_v1','');paintPresets();
  const n=filtCount(),b=$('#pFiltN');b.textContent=n;b.classList.toggle('hidden',!n);clearTimeout(P.ft);P.ft=setTimeout(renderAll,120)});
$('#pFReset').onclick=()=>setFilters({}, 'All');
$('#pFSave').onclick=()=>{const n=(prompt('Name this preset (saved in this browser only):')||'').trim().slice(0,20);if(!n)return;if(BUILTIN[n]){toast('That name is built in.');return}
  presets[n]={...P.filt};LSs('xs_pulse_presets_v1',presets);activePreset=n;LSs('xs_pulse_active_v1',n);paintPresets();toast('Saved preset “'+esc(n)+'”')};
$('#pSeg').onclick=e=>{const b=e.target.closest('button');if(!b)return;P.seg=b.dataset.c;paintSeg()};
function paintSeg(){$$('#pSeg button').forEach(b=>b.classList.toggle('on',b.dataset.c===P.seg));$$('.pcol').forEach(c=>c.classList.toggle('on',c.dataset.c===P.seg))}
$('#pSrcBtn').onclick=()=>{const b=$('#pSrcBox');b.classList.toggle('hidden');paintSrc()};
function paintSrc(){const s=P.src;if(!s)return;const nm={gecko:'GeckoTerminal (new pools, pump.fun / pumpswap lists)',dexscreener:'DexScreener (price, liquidity, volume, buys/sells, socials)',jupiter:'Jupiter (holders, top-10 %, dev %)',rpc:'Solana RPC (bonding curve %, snipers)',rugcheck:'RugCheck (bundle check, via the scanner)'};
  $('#pSrcBox').innerHTML=Object.entries(nm).map(([k,t])=>{const x=s[k]||{};const st=x.cooling_s>0?`<b class="dn">rate-limited, retry in ${x.cooling_s}s</b>`:x.last_ok_s!=null?`<b class="up">ok</b> · last ${x.last_ok_s}s ago`:x.errors?`<b class="dn">${esc(x.last_error||'error')}</b>`:'<span class="muted">not used yet</span>';
    return `<div><span>${t}</span><span>${st}</span></div>`}).join('')+`<p class="muted">Free public APIs rate-limit shared servers, so a source can pause for a while; its fields then show — until it is back.</p>`}

const chipCls=(v,a,b)=>v==null?'na':v>=b?'red':v>=a?'amb':'grn';
function rowHTML(r,col){
  const cv=r.cv,mcc=r.d==='pumpswap'||col==='migrated',t10=r.t10!=null?r.t10:r.t10x;
  const b1=r.b1,s1=r.s1,tot=(b1||0)+(s1||0),bp=tot?Math.round((b1||0)/tot*100):null;
  const bund=r.bd==='flagged'?['red','Bundle']:r.ebl?['red',(r.ebm||4)+'+ in block']:r.bd==='elevated'?['amb','Bundle risk']:r.bd==='ok'?['grn','Bundle ok']:r.bd==='unavailable'?['na','Bundle ?']:['na','Bundle —'];
  const sn=r.sn!=null?[chipCls(r.sn,5,15),'Snipers '+pTxt(r.sn,r.sn<10?1:0)]:r.es==='too_old'?['na','Snipers n/a']:['na','Snipers —'];
  const soc=[r.tw?ext(r.tw,'𝕏','soc'):'',r.tg?ext(r.tg,'TG','soc'):'',r.web?ext(r.web,'🌐','soc'):''].join('');
  return `<div class="pimg">${tokImgDirect(r)}<button class="star" data-star="${esc(r.m)}" aria-label="Watch"></button></div>
  <div class="pmain">
   <div class="ptop"><b class="psym">${esc(symOf(r))}</b><span class="pname">${esc(r.n||'')}</span><button class="cpy mini" data-cp="${esc(r.m)}" title="Copy CA">⧉</button><span class="page" data-cr="${r.cr||''}" data-mg="${r.mg||''}" data-col="${col}">${ageS(r.cr?now()-r.cr:null)}</span>${soc?`<span class="psoc">${soc}</span>`:''}</div>
   <div class="pmets"><span title="Market cap">MC <b class="mcv">${usd(r.mc)}</b></span><span title="Liquidity">Liq <b>${usd(r.lq)}</b></span><span title="Volume (1h)">Vol <b>${usd(r.v1)}</b></span><span title="Buys / sells in the last hour">TX <b class="up">${b1??'—'}</b>/<b class="dn">${s1??'—'}</b></span><span title="Holders">Hold <b>${r.h!=null?r.h.toLocaleString():'—'}</b></span>${r.pc5!=null?`<span title="5 minute change">${pc1(r.pc5)}</span>`:''}</div>
   <div class="pchips"><span class="chip ${chipCls(t10,20,35)}" title="Top 10 holders share (excluding the pool)">Top10 ${pTxt(t10,0)}</span><span class="chip ${chipCls(r.dv,3,10)}" title="Dev wallet holding">Dev ${pTxt(r.dv,1)}</span><span class="chip ${sn[0]}" title="Supply bought in the first blocks after launch (RPC check)">${sn[1]}</span><span class="chip ${bund[0]}" title="Bundle: RugCheck insider clusters or 4+ txs in one block">${bund[1]}</span></div>
   ${tot?`<div class="bsbar" title="${bp}% of 1h transactions are buys"><i style="width:${bp}%"></i></div>`:''}
   ${!mcc?`<div class="cvbar" title="${cv!=null?(r.cvs==='chain'?'Bonding curve progress, read from the chain':'Estimated from market cap'):'Curve progress unavailable'}"><i style="width:${cv!=null?Math.min(100,cv):0}%" class="${cv!=null&&cv>=70?'hot':''}"></i><span>${cv!=null?cv.toFixed(cv<10?1:0)+'%'+(r.cvs==='est'?' ~':''):'curve —'}${r.cv_done?' · migrating':''}</span></div>`:`<div class="cvbar done"><span>migrated${r.mg?' '+ageS(now()-r.mg)+' ago':''}${r.lpl!=null?' · LP '+Math.round(r.lpl)+'% locked/burned':''}</span></div>`}
  </div>
  <div class="pact"><a class="open" href="${esc(jupLink(r.m))}" target="_blank" rel="noopener noreferrer" title="Opens Jupiter in a new tab. xScanner never trades for you." data-noopen>Jupiter ↗</a><a class="open alt" href="${esc(axLink(r))}" target="_blank" rel="noopener noreferrer" data-noopen>Axiom ↗</a><a class="open alt" href="${esc(phLink(r))}" target="_blank" rel="noopener noreferrer" data-noopen>Photon ↗</a></div>`}
function rowEl(r,col){let el=P.els.get(r.m);
  if(!el){el=document.createElement('article');el.className='prow';el.dataset.m=r.m;el.tabIndex=0;el.setAttribute('role','link');el.setAttribute('aria-label','Open '+symOf(r));P.els.set(r.m,el);if(!P.first)el.classList.add('pin')}
  if(el._v!==r.v||el._col!==col){const prevMc=el._mc;el.innerHTML=rowHTML(r,col);el._v=r.v;el._col=col;
    if(prevMc!=null&&r.mc!=null&&prevMc!==r.mc){el.classList.remove('fl-up','fl-dn');void el.offsetWidth;el.classList.add(r.mc>prevMc?'fl-up':'fl-dn')}
    el._mc=r.mc}
  return el}
function renderCol(c){
  const list=$(`.pcol[data-c="${c}"] .plist`);const ids=P.cols[c]||[];const vis=[];
  for(const m of ids){const r=P.rows.get(m);if(r&&passes(r))vis.push(r)}
  const cnt=$(`.pcol[data-c="${c}"] .pcnt`);cnt.textContent=vis.length===ids.length?ids.length:`${vis.length}/${ids.length}`;
  $(`#pSeg [data-c="${c}"] b`).textContent=vis.length;
  if(P.warming&&!ids.length){list.innerHTML=skelRows(7);return}
  if(list.querySelector('.skel-row'))list.innerHTML='';
  const want=vis.map(r=>rowEl(r,c));
  // reconcile order without rebuilding every row
  let cur=list.firstElementChild;
  for(const el of want){if(cur===el){cur=cur.nextElementSibling;continue}list.insertBefore(el,cur)}
  while(cur){const nx=cur.nextElementSibling;cur.remove();cur=nx}
  if(!vis.length){let e=list.querySelector('.pempty');if(!e){e=document.createElement('div');e.className='pempty muted';list.appendChild(e)}
    e.textContent=ids.length?'No rows match your filters. Loosen them or press Reset.':(c==='final'?'No tokens in the final stretch right now.':c==='migrated'?'No fresh migrations seen yet. They appear here as they happen.':'Waiting for new tokens…')}
  else list.querySelector('.pempty')?.remove()}
function skelRows(n){return Array.from({length:n},()=>`<div class="prow skel-row" aria-hidden="true"><div class="pimg"><div class="skel pimgi"></div></div><div class="pmain"><div class="skel skel-line short"></div><div class="skel skel-line"></div><div class="skel skel-line mid"></div></div></div>`).join('')}
function renderAll(){if(view==='trend'){trendRender();P.first=false;return}for(const c of ['new','final','migrated'])renderCol(c);paintStars();P.first=false}
document.addEventListener('click',e=>{const row=e.target.closest('.prow[data-m]');if(!row)return;
  const cp=e.target.closest('[data-cp]');if(cp){e.stopPropagation();copyCa(cp.dataset.cp,cp);return}
  if(e.target.closest('[data-star],[data-noopen],a,.psoc'))return;
  openToken(row.dataset.m)});
document.addEventListener('keydown',e=>{if(e.key==='Enter'&&e.target.classList&&e.target.classList.contains('prow'))openToken(e.target.dataset.m)});
setInterval(()=>{if(view==='trend'&&!document.hidden&&P.okAt){const s=Math.round(Date.now()/1000-P.okAt);$('#tLiveTxt').textContent=P.warming?'warming up…':`live · updated ${s<=1?'just now':s+'s ago'}`;$('#tLiveDot').className=s>20?'stale':'on'}if(view!=='pulse'||document.hidden)return;const t=now();$$('.page').forEach(el=>{const cr=+el.dataset.cr;if(cr)el.textContent=ageS(t-cr)});
  if(P.okAt){const s=Math.round(Date.now()/1000-P.okAt);$('#pLiveTxt').textContent=P.warming?'warming up…':`live · updated ${s<=1?'just now':s+'s ago'}`;$('#pLiveDot').className=s>20?'stale':'on'}},1000);
async function pulsePoll(){
  if(P.busy||document.hidden||!isP(view))return;P.busy=true;
  try{const j=await api(`/api/pulse?since=${P.ver}&h=${encodeURIComponent(P.h)}`);
    P.okAt=now();P.src=j.sources;P.warming=!!j.warming;P.sol=j.sol_usd;
    for(const r of j.rows||[])P.rows.set(r.m,r);
    if(j.cols){P.cols=j.cols;P.h=j.h}
    P.ver=j.ver;
    const missing=Object.values(P.cols).flat().some(m=>!P.rows.has(m));
    if(missing&&P.ver){P.ver=0}
    if((j.rows&&j.rows.length)||j.cols||P.first)renderAll();
    if(!$('#pSrcBox').classList.contains('hidden'))paintSrc();
  }catch(e){$('#pLiveTxt').textContent='offline · retrying';$('#pLiveDot').className='stale'}
  finally{P.busy=false}}
let pulseTimer=null;
function pulseStart(){pulseStop();if(view==='pulse'){paintFilters();paintPresets();paintSeg();if(P.first)for(const c of ['new','final','migrated'])renderCol(c)}else trendRender();pulsePoll();pulseTimer=setInterval(pulsePoll,P.warming?1500:3000)}
function pulseStop(){clearInterval(pulseTimer);pulseTimer=null}
document.addEventListener('visibilitychange',()=>{if(!document.hidden){if(isP(view))pulsePoll();watchPoll(true);if(view==='wallets')walletsRefresh()}});

// ===================================================================== TRENDING (ranked view of the Pulse universe)
const TR={k:LS('xs_trend_k_v1','v1'),dust:LS('xs_trend_dust_v1',true)};
const trVal={v1:r=>r.v1,pc5:r=>r.pc5,pc1:r=>r.pc1,tx:r=>(r.b1==null&&r.s1==null)?null:(r.b1||0)+(r.s1||0),v24:r=>r.v24,mc:r=>r.mc};
function trendRender(){
  const body=$('#trBody');if(!body)return;
  $$('#trSort button').forEach(b=>b.classList.toggle('on',b.dataset.k===TR.k));$('#trDust').checked=!!TR.dust;
  if(P.warming&&!P.rows.size){body.innerHTML=Array.from({length:8},()=>'<tr class="skel-tr"><td colspan="13"><div class="skel" style="height:22px"></div></td></tr>').join('');return}
  const f=trVal[TR.k]||trVal.v1;
  const rows=[...P.rows.values()].filter(r=>r.st&&(!TR.dust||(r.lq||0)>=1000)&&f(r)!=null).sort((a,b)=>f(b)-f(a)).slice(0,50);
  body.innerHTML=rows.length?rows.map((r,i)=>`<tr class="trr" data-m="${esc(r.m)}"><td class="mono mut">${i+1}</td>
   <td><span class="tcell">${tokImgDirect(r,'timg')}<span class="tnm"><b>${esc(symOf(r))}</b><small>${esc(r.n||'')}</small></span><button class="cpy mini" data-cp="${esc(r.m)}" title="Copy CA">⧉</button></span></td>
   <td class="mono">${r.cr?ageS(now()-r.cr):'—'}</td><td class="mono">${r.px?fmtPx(r.px):'—'}</td><td class="mono">${pc1(r.pc5)}</td><td class="mono">${pc1(r.pc1)}</td><td class="mono">${pc1(r.pc24)}</td>
   <td class="mono">${usd(r.v1)}</td><td class="mono">${usd(r.lq)}</td><td class="mono">${usd(r.mc)}</td>
   <td class="mono"><span class="up">${r.b1??'—'}</span>/<span class="dn">${r.s1??'—'}</span></td><td class="mono">${r.h!=null?r.h.toLocaleString():'—'}</td>
   <td class="tact"><button class="star" data-star="${esc(r.m)}" aria-label="Watch"></button><a class="open alt" href="${esc(jupLink(r.m))}" target="_blank" rel="noopener noreferrer" data-noopen>Jupiter ↗</a></td></tr>`).join(''):'<tr><td colspan="13" class="muted" style="padding:18px">Nothing matches yet. The board fills in about a minute after the server wakes up.</td></tr>';
  paintStars()}
$('#trSort').onclick=e=>{const b=e.target.closest('button[data-k]');if(!b)return;TR.k=b.dataset.k;LSs('xs_trend_k_v1',TR.k);trendRender()};
$('#trDust').onchange=e=>{TR.dust=e.target.checked;LSs('xs_trend_dust_v1',TR.dust);trendRender()};
document.addEventListener('click',e=>{const row=e.target.closest('tr.trr[data-m]');if(!row)return;
  const cp=e.target.closest('[data-cp]');if(cp){e.stopPropagation();copyCa(cp.dataset.cp,cp);return}
  if(e.target.closest('[data-star],[data-noopen],a'))return;openToken(row.dataset.m)});
$$('#subtabs button').forEach(b=>b.onclick=()=>window.show(b.dataset.v));

// ===================================================================== TOKEN PAGE
const TK={cur:null,mint:null,seq:0,tape:null,tapeTimer:null,detailTimer:null,tab:'trades',lookup:null,seen:new Set()};
const secRow=(ok,label,val,note)=>`<div class="secrow"><span class="si ${ok===true?'ok':ok===false?'bad':'na'}">${ok===true?'✓':ok===false?'✗':'?'}</span><span class="sl">${label}</span><span class="sv">${val??'—'}</span>${note?`<small class="muted">${note}</small>`:''}</div>`;
function tokSkeleton(m,r){
  return `<div class="tk-head card"><div class="tk-id">${r?tokImgDirect({...r,m},'tkimg'):'<div class="skel tkimg"></div>'}<div><h2>${r?'$'+esc(symOf(r)):'<span class="skel" style="display:inline-block;width:120px;height:22px"></span>'} <small class="muted">${esc(r?.n||'')}</small></h2><div class="tca"><code>${esc(shortCa(m))}</code><button class="cpy" data-cp="${esc(m)}">⧉ Copy CA</button></div></div></div><div id="tkLinks" class="tk-links"></div></div>
  <div id="tkStats" class="tk-stats">${Array.from({length:8},()=>'<div class="skel" style="height:46px"></div>').join('')}</div>
  <div class="tk-grid"><div class="tk-main">
    <div class="card tk-chart">${chartBlockHTML()}</div>
    <div class="card tk-tabs"><div class="tfs" id="tkTabs"><button data-t="trades" class="on">Live trades</button><button data-t="holders">Holders</button><button data-t="traders">Top traders</button><button data-t="dev">Dev wallet</button></div><div id="tkPane" class="tk-pane"><div class="skel" style="height:160px"></div></div></div>
    <div class="card" id="tkStory"><h3>Our read</h3><div class="skel" style="height:120px"></div></div>
   </div><aside class="tk-side"><div class="card" id="tkInfo"><h3>Token info</h3><div class="skel" style="height:200px"></div></div><div class="card" id="tkSec"><h3>Security</h3><div class="skel" style="height:220px"></div></div></aside></div>
  <div class="disc" role="note"><b>Not financial advice.</b> Data is public and can be wrong or late. xScanner is read-only: the buttons above open other sites in a new tab; we never hold keys or place orders.</div>`}
const ma0=a=>!a||!a.known?'—':a.disabled?'<span class="up">Disabled</span>':'<b class="dn">ENABLED</b>'+(a.who?' <code class="addr">'+esc(shortCa(a.who))+'</code>':'');
function stat(l,v,sub){return `<div class="tks"><span>${l}</span><b>${v}</b>${sub?`<small>${sub}</small>`:''}</div>`}
function tokPaint(d){
  TK.cur=d;const mk=d.market||{},sc=d.security||{},cv=d.curve;
  $('#tokBody .tk-head').innerHTML=`<div class="tk-id">${tokImgDirect({m:d.mint,img:d.img,s:d.symbol},'tkimg')}<div><h2>$${esc(d.symbol||'?')} <small class="muted">${esc(d.name||'')}</small> <button class="star big" data-star="${esc(d.mint)}"></button></h2>
    <div class="tca"><code title="${esc(d.mint)}">${esc(shortCa(d.mint))}</code><button class="cpy" data-cp="${esc(d.mint)}">⧉ Copy CA</button>${d.socials?.twitter?ext(d.socials.twitter,'𝕏','soc'):''}${d.socials?.telegram?ext(d.socials.telegram,'Telegram','soc'):''}${d.socials?.website?ext(d.socials.website,'Website','soc'):''}</div></div></div>
    <div id="tkLinks" class="tk-links"><span class="muted">Open in</span>${(d.links||[]).slice(0,3).map(l=>`<a class="open" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)} ↗</a>`).join('')}<span class="muted">·</span>${(d.links||[]).slice(3).map(l=>`<a class="open alt" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${esc(l.label)}</a>`).join('')}</div>`;
  const age=mk.created?ageS(now()-mk.created):'—';
  const sec=sc.top10_pct;
  $('#tkStats').innerHTML=stat('Price',fmtPx(mk.px),pc1(mk.pc5)+' 5m')+stat('Market cap',usd(mk.mc),pc1(mk.pc1)+' 1h')+stat('Liquidity',usd(mk.lq))+stat('Volume 24h',usd(mk.v24),'1h '+usd(mk.v1))+
    stat('Buys / sells 1h',`<span class="up">${mk.b1??'—'}</span> / <span class="dn">${mk.s1??'—'}</span>`,'5m '+(mk.b5??'—')+' / '+(mk.s5??'—'))+stat('Holders',sc.holders!=null?(+sc.holders).toLocaleString():'—','top-10 '+pTxt(sec,0))+stat('Dev holds',pTxt(sc.dev?.balance_pct,1),sc.dev?.launched!=null?sc.dev.launched+' tokens launched':'')+stat('Age',age,esc(mk.dex||''));
  const cvEl=cv&&cv.progress!=null?`<div class="tkcurve"><div class="cvbar big" title="${esc(cv.note||'')}"><i style="width:${Math.min(100,cv.progress)}%" class="${cv.progress>=70?'hot':''}"></i><span>Bonding curve ${cv.progress.toFixed(1)}%${cv.source==='est'?' (estimated)':cv.complete?' · complete':''}</span></div></div>`:'';
  let ce=$('#tkCurve');if(!ce){ce=document.createElement('div');ce.id='tkCurve';$('#tkStats').after(ce)}ce.innerHTML=cvEl;
  // Solscan-style key/value card: copyable addresses + explorer links
  const dv=sc.dev||{};
  $('#tkInfo').innerHTML=`<h3>Token info</h3><div class="kvl">
   ${kvRow('Mint address',addrCell(d.mint),addrBtns(d.mint,'token'))}
   ${d.pool&&d.pool!==d.mint?kvRow(mk.dex==='pumpfun'?'Bonding curve':'Pool',addrCell(d.pool),addrBtns(d.pool,'account')):''}
   ${dv.wallet?kvRow('Dev / creator',addrCell(dv.wallet),addrBtns(dv.wallet,'account')):''}
   ${kvRow('Market',esc((mk.dex||'—')+(mk.quote?' · '+mk.quote:'')))}
   ${kvRow('Created',esc(dateS(mk.created)),mk.created?'<small class="muted">'+ageS(now()-mk.created)+' ago</small>':'')}
   ${mk.migrated_at?kvRow('Migrated',esc(dateS(mk.migrated_at))):''}
   ${kvRow('Token program',sc.token2022==null?'—':sc.token2022?'Token-2022':'SPL Token')}
   ${kvRow('Mint authority',ma0(sc.mint_authority))}${kvRow('Freeze authority',ma0(sc.freeze_authority))}
   ${kvRow('Holders',sc.holders!=null?(+sc.holders).toLocaleString():'—')}
   ${kvRow('Explorers',`<a class="xl" href="https://solscan.io/token/${esc(d.mint)}" target="_blank" rel="noopener noreferrer">Solscan ↗</a> <a class="xl" href="https://explorer.solana.com/address/${esc(d.mint)}" target="_blank" rel="noopener noreferrer">Solana Explorer ↗</a> <a class="xl" href="https://rugcheck.xyz/tokens/${esc(d.mint)}" target="_blank" rel="noopener noreferrer">RugCheck ↗</a>`)}
  </div>`;
  // security
  const ea=d.early,ma=sc.mint_authority||{},fa=sc.freeze_authority||{};
  const bnd=TK.lookup?.bundle;
  const earlyRows=!ea?'<div class="muted" style="padding:6px 0">Checking the first blocks after launch…</div>':ea.status==='ok'?
     secRow(!ea.bundle_flag,'Same-block bundle',ea.bundle_flag?`<b class="dn">${ea.bundle_max_txs_in_block} txs in one block</b>`:ea.bundle_max_txs_in_block+' tx/block max','4+ txs in one block near launch = bundled (Axiom’s public rule)')+
     secRow(ea.sniper_pct==null?null:ea.sniper_pct<8,'Snipers',ea.snipers!=null?`${ea.snipers} wallets · ${pTxt(ea.sniper_pct,1)} of supply`:'—','bought within ~1 s of launch (first '+ea.txs_seen+' txs seen'+(ea.partial?', partial':'')+')')+
     secRow(ea.same_block_pct==null?null:ea.same_block_pct<5,'Bought in launch block',`${ea.same_block_buyers} wallets · ${pTxt(ea.same_block_pct,1)}`,'')
     :secRow(null,'Early buyers',ea.status==='too_old'?'not assessed':'unavailable',esc(ea.reason||''));
  $('#tkSec').innerHTML=`<h3>Security</h3>
   ${secRow(ma.known?ma.disabled:null,'Mint authority',ma.known?(ma.disabled?'disabled':'<b class="dn">ENABLED</b>'):'—',ma.source?'via '+ma.source:'')}
   ${secRow(fa.known?fa.disabled:null,'Freeze authority',fa.known?(fa.disabled?'disabled':'<b class="dn">ENABLED</b>'):'—',fa.source?'via '+fa.source:'')}
   ${secRow(sc.lp?.locked_pct!=null?sc.lp.locked_pct>=90:(d.market?.dex==='pumpswap'?true:null),'LP burned / locked',sc.lp?.locked_pct!=null?Math.round(sc.lp.locked_pct)+'%':(d.market?.dex==='pumpfun'?'on curve':'—'),esc(sc.lp?.note||''))}
   ${secRow(sec==null?null:sec<25,'Top-10 holders',pTxt(sec,1),'excluding the pool')}
   ${secRow(sc.dev?.balance_pct==null?null:sc.dev.balance_pct<5,'Dev holding',pTxt(sc.dev?.balance_pct,1),sc.dev?.creator_rugged?'<b class="dn">creator flagged as rugged</b>':'')}
   ${secRow(sc.insiders?(sc.insiders.networks===0&&!sc.insiders.top_holders_flagged):null,'Insiders',sc.insiders?`${sc.insiders.networks} networks · ${sc.insiders.top_holders_flagged} top holders flagged`:'—','RugCheck insider graph')}
   ${bnd?secRow(bnd.status==='ok'?true:bnd.status==='unavailable'?null:false,'Our bundle check',esc(bnd.status==='ok'?'passed':bnd.status),esc((bnd.reason||'').slice(0,90))):''}
   ${secRow(sc.token2022?null:true,'Token program',sc.token2022?'Token-2022':'Token','Token-2022 can carry extensions; check fees on RugCheck')}
   <h4>Early buyers</h4>${earlyRows}
   ${(sc.risks||[]).length?`<h4>RugCheck notes</h4>${sc.risks.map(r=>`<span class="pill ${/danger|high/i.test(r.level||'')?'red':'amb'}" title="${esc(r.description||'')}">${esc(r.name)}</span>`).join('')}`:''}
   ${(d.pending||[]).length?`<p class="muted" style="font-size:11px">Still loading: ${esc(d.pending.join(', '))}…</p>`:''}${(d.notes||[]).map(n=>`<p class="muted" style="font-size:11px">${esc(n)}</p>`).join('')}`;
  paintStars();
  if(TK.tab==='holders'||TK.tab==='dev')tkPane()}
function tkStoryPaint(){
  const c=TK.lookup;const el=$('#tkStory');if(!el)return;
  if(c==null){el.innerHTML='<h3>Our read</h3><div class="skel" style="height:120px"></div><p class="muted" style="font-size:11px">Scoring this token (a live scan can take up to ~20 s)…</p>';return}
  if(c==='err'){el.innerHTML='<h3>Our read</h3><p class="muted">Our score and story are unavailable right now (the scanner is busy). Market data and security above are unaffected.</p>';return}
  const tier=c.score>=65?'Worth a look':c.score>=50?'Watch':'Skip';
  el.innerHTML=`<h3>Our read <span class="score ${tierCls(c.score)}">${c.score}</span> <small class="muted" style="font-size:12px">${esc(c.verdict||tier)}</small></h3><p class="story">${esc(c.story||'No story available yet for this coin.')}</p>${c.reason?`<p class="muted" style="font-size:12px">Scanner notes: ${esc(c.reason)}</p>`:''}${bundleHTML(c).replace(/<h3/g,'<h4').replace(/<\/h3>/g,'</h4>')}${confHTML(c).replace(/<h3/g,'<h4').replace(/<\/h3>/g,'</h4>')}<p style="margin:8px 0 0"><a href="#" id="tkDrawer" class="linkbtn">Full scanner detail (risk radar, X data) →</a></p>`;
  const a=$('#tkDrawer');if(a)a.onclick=e=>{e.preventDefault();openCoin(c.mint,c)}}
function tkPane(){
  const pane=$('#tkPane');if(!pane)return;const d=TK.cur;const t=TK.tab;
  $$('#tkTabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===t));
  if(t==='trades')return tapePaint();
  if(t==='holders'){const h=d?.holders;
    pane.innerHTML=h?`<div class="tablewrap"><table class="tkt"><thead><tr><th>#</th><th>Wallet</th><th>% supply</th><th>Tags</th></tr></thead><tbody>${h.rows.map(x=>`<tr><td>${x.rank}</td><td><a href="https://solscan.io/account/${esc(x.owner)}" target="_blank" rel="noopener noreferrer"><code>${esc(shortCa(x.owner))}</code></a></td><td><div class="hbar"><i style="width:${Math.min(100,x.pct*2)}%"></i><b>${x.pct.toFixed(2)}%</b></div></td><td>${x.pool?'<span class="pill">pool / curve</span>':''}${x.dev?'<span class="pill amb">dev</span>':''}${x.insider?'<span class="pill red">insider</span>':''}</td></tr>`).join('')}</tbody></table></div><p class="muted" style="font-size:11px">Top ${h.rows.length} of ${h.total!=null?(+h.total).toLocaleString():'?'} holders · source: RugCheck (${ageS(h.age_s)} old)</p>`:'<p class="muted">Holders table is loading (RugCheck is rate limited, so it can take a moment)…</p>';return}
  if(t==='traders'){const tr=TK.tape;
    pane.innerHTML=!tr?'<div class="skel" style="height:140px"></div>':!tr.available?`<p class="muted">Top traders unavailable: ${esc(tr.reason||'trades feed busy')}.</p>`:tr.traders.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>Wallet</th><th>Buys</th><th>Sells</th><th>Bought $</th><th>Sold $</th><th>Net</th></tr></thead><tbody>${tr.traders.map(x=>`<tr><td><a href="https://solscan.io/account/${esc(x.w)}" target="_blank" rel="noopener noreferrer"><code>${esc(shortCa(x.w))}</code></a></td><td class="up">${x.buys}</td><td class="dn">${x.sells}</td><td>${usd(x.buy_usd)}</td><td>${usd(x.sell_usd)}</td><td>${pc1x(x.net_usd)}</td></tr>`).join('')}</tbody></table></div><p class="muted" style="font-size:11px">${esc(tr.note)}</p>`:'<p class="muted">No trades yet.</p>';return}
  if(t==='dev')devPane()}
const pc1x=v=>v==null?'—':`<span class="${v>=0?'up':'dn'}">${v>=0?'+':'-'}${usd(Math.abs(v))}</span>`;
async function devPane(){
  const pane=$('#tkPane'),d=TK.cur;const w=d?.security?.dev?.wallet;
  if(!w){pane.innerHTML='<p class="muted">Dev wallet not known yet for this token.</p>';return}
  const seq=TK.seq;
  pane.innerHTML=`<p class="muted">Dev wallet <a href="https://solscan.io/account/${esc(w)}" target="_blank" rel="noopener noreferrer"><code>${esc(shortCa(w))}</code></a> · checking its recent launches… <span class="spin"></span></p>`;
  for(let i=0;i<14;i++){
    let j;try{j=await api(`/api/dev/${w}?mint=${d.mint}`)}catch(e){j={status:'error',reason:e.message}}
    if(seq!==TK.seq||TK.tab!=='dev')return;
    if(j.status==='pending'){await sleep(3000);continue}
    if(j.status==='error'){pane.innerHTML=`<p class="muted">Dev history unavailable (${esc(j.reason||'error')}).</p>`;return}
    const ts=j.tokens||[];
    pane.innerHTML=`<div class="kv" style="grid-template-columns:repeat(4,1fr)"><div><span>Other launches seen</span>${j.launched_seen}</div><div><span>Migrated</span><b class="up">${j.migrated}</b></div><div><span>Live</span>${j.live}</div><div><span>Dead</span><b class="dn">${j.dead}</b></div></div>
     ${j.launched_seen===0&&d.security.dev.launched?`<p class="muted">Jupiter counts ${d.security.dev.launched} tokens minted by this wallet, but none show up in its latest ${j.txs_checked} transactions (it is busy trading), so we cannot list them.</p>`:''}${ts.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>Token</th><th>Launched</th><th>MCap</th><th>How it ended</th></tr></thead><tbody>${ts.map(x=>`<tr><td><a href="/token/${esc(x.mint)}" data-tok="${esc(x.mint)}">$${esc(x.sym||shortCa(x.mint))}</a> <small class="muted">${esc(x.name||'')}</small></td><td>${x.created?ageS(now()-x.created)+' ago':'—'}</td><td>${usd(x.mcap)}</td><td><span class="pill ${x.end==='migrated'?'grn':x.end==='live'?'':'red'}">${esc(x.state)}</span></td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No other pump.fun launches found in this wallet’s recent transactions.</p>'}
     <p class="muted" style="font-size:11px">${esc(j.note||'')} (${j.txs_checked} txs checked${j.error?'; RPC error: '+esc(j.error):''})</p>`;return}
  pane.innerHTML='<p class="muted">The dev history check is taking long (free RPC is busy). Try again in a minute.</p>'}
function tapePaint(){
  const pane=$('#tkPane');if(!pane||TK.tab!=='trades')return;const tr=TK.tape;
  if(!tr){pane.innerHTML='<div class="skel" style="height:160px"></div>';return}
  if(!tr.available){pane.innerHTML=`<p class="muted"><b>Trades tape unavailable</b> — ${esc(tr.reason||'the free trades feed is busy')}. Retrying every 10 s.</p>`;return}
  pane.innerHTML=`<div class="tablewrap tape"><table class="tkt"><thead><tr><th>Age</th><th>Side</th><th>USD</th><th>Price</th><th>Wallet</th><th></th></tr></thead><tbody>${tr.trades.slice(0,50).map(x=>`<tr class="${TK.seen.has(x.sig)?'':'fresh'} ${x.k==='buy'?'b':'s'}"><td>${ageS(now()-x.t)}</td><td class="${x.k==='buy'?'up':'dn'}">${x.k}</td><td>${usd(x.usd)}</td><td>${x.px?fmtPx(x.px):'—'}</td><td><code>${esc(shortCa(x.w||''))}</code></td><td><a href="https://solscan.io/tx/${esc(x.sig)}" target="_blank" rel="noopener noreferrer" title="Open the transaction">↗</a></td></tr>`).join('')}</tbody></table></div><p class="muted" style="font-size:11px">Last ${tr.n_seen} trades GeckoTerminal reports for this pool (refreshes every 10 s)${tr.stale?' · <b>delayed: '+esc(tr.stale_reason||'')+'</b>':''}. Not every on-chain trade is included.</p>`;
  tr.trades.forEach(x=>TK.seen.add(x.sig))}
async function tapePoll(){
  const d=TK.cur;if(!d||!d.pool||document.hidden||view!=='token')return;const seq=TK.seq;
  try{const j=await api(`/api/trades/${d.pool}?token=${d.mint}`);if(seq!==TK.seq)return;TK.tape=j;if(TK.tab==='trades'||TK.tab==='traders')tkPane()}catch(e){if(seq===TK.seq&&!TK.tape){TK.tape={available:false,reason:'network error'};tkPane()}}}
document.addEventListener('click',e=>{const b=e.target.closest('#tkTabs button');if(b){TK.tab=b.dataset.t;tkPane();return}
  const a=e.target.closest('a[data-tok]');if(a&&!e.metaKey&&!e.ctrlKey){e.preventDefault();openToken(a.dataset.tok)}});
async function tokLoad(m){
  const seq=TK.seq;
  for(let i=0;i<3&&seq===TK.seq;i++){
    try{const d=await api('/api/token/'+m);if(seq!==TK.seq)return;
      if(d.found===false){$('#tokBody').innerHTML=`<div class="card"><h3>No market found</h3><p class="muted">${esc((d.notes||[])[0]||'This token has no trading pair yet.')}</p><p><a href="#pulse" onclick="location.hash='pulse';history.pushState({},'','/#pulse');show('pulse');return false">← Back to Pulse</a></p></div>`;return}
      const first=!TK.cur||TK.cur.mint!==m;TK.cur=d;tokPaint(d);
      if(first||!CH.coin){CH.h=window.innerWidth<700?320:470;mountChart({pool:d.pool,mint:d.mint,symbol:d.symbol})}
      if(first){tapePoll();clearInterval(TK.tapeTimer);TK.tapeTimer=setInterval(tapePoll,10000)}
      clearTimeout(TK.detailTimer);TK.detailTimer=setTimeout(()=>{if(view==='token'&&seq===TK.seq)tokLoad(m)},(d.pending||[]).length?4000:12000);return}
    catch(e){if(i===2){$('#tokBody').innerHTML=`<div class="card"><h3>Could not load this token</h3><p class="muted">${esc(e.message)}</p><p><button class="ghost" onclick="openToken('${esc(m)}')">Retry</button></p></div>`;return}await sleep(1500)}}}
async function lookupLoad(m){
  const seq=TK.seq;
  for(let i=0;i<4&&seq===TK.seq;i++){
    try{const r=await fetch('/api/lookup/'+m);const j=await r.json().catch(()=>({}));if(seq!==TK.seq)return;
      if(r.ok){TK.lookup=j;tkStoryPaint();if(TK.cur)tokPaint(TK.cur);return}
      if(r.status===429){await sleep(6000);continue}
      TK.lookup='err';tkStoryPaint();return}
    catch(e){await sleep(2000)}}
  if(seq===TK.seq){TK.lookup='err';tkStoryPaint()}}
function leaveToken(){TK.seq++;clearInterval(TK.tapeTimer);clearTimeout(TK.detailTimer);TK.tapeTimer=null;TK.cur=null;TK.tape=null;TK.lookup=null;TK.seen.clear();try{unmountChart()}catch(e){}CH.h=0}
window.openToken=function(m,opts){
  opts=opts||{};if(!B58.test(m||''))return;
  if(view==='token')leaveToken();
  const r=P.rows.get(m);
  if(opts.push!==false)history.pushState({tok:m},'','/token/'+m);else history.replaceState({tok:m},'','/token/'+m);
  document.title=(r?'$'+symOf(r)+' · ':'')+'xScanner token page';
  setView('token');TK.mint=m;TK.seq++;TK.cur=null;TK.tape=null;TK.lookup=null;TK.tab='trades';
  $('#tokBody').innerHTML=tokSkeleton(m,r);paintStars();
  window.scrollTo({top:0});
  tkPane();tokLoad(m);lookupLoad(m)};
// ===================================================================== views / routing
function setView(v){
  const prev=view;if(isP(prev)&&!isP(v))pulseStop();
  if(prev==='token'&&v!=='token')leaveToken();
  view=v;$$('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  $$('.view').forEach(s=>{s.classList.toggle('hidden',s.id!=='v-'+v);s.classList.remove('fx-leave','fx-enter-view')});
  document.documentElement.dataset.view=v;show.ready=true;navSync()}
const _show=window.show;
window.show=function(v){
  if(v==='token'&&TK.mint){openToken(TK.mint);return}
  const wasTok=view==='token'||/^\/token\//.test(location.pathname);
  if(wasTok||location.pathname==='/pulse'){if(view==='token')leaveToken();history.replaceState({},'','/#'+v);document.title='xScanner · Solana memecoin scanner'}
  if(v==='pulse'||v==='trend'||v==='watch'||v==='wallets'){const prev=view;if(isP(prev)&&!isP(v))pulseStop();view=v;$$('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));$$('.view').forEach(s=>s.classList.toggle('hidden',s.id!=='v-'+v));document.documentElement.dataset.view=v;location.hash=v;show.ready=true;navSync();enter(v);return}
  if(isP(view))pulseStop();
  document.documentElement.dataset.view=v;
  _show(v);navSync()};
function enter(v){
  if(v==='pulse'||v==='trend')pulseStart();
  if(v==='watch'){W.unseen=0;saveW();renderWatch();watchPoll(true)}
  if(v==='wallets'){wRender();walletsRefresh()}}
loaders.pulse=async()=>{};loaders.trend=async()=>{};loaders.watch=async()=>{};loaders.wallets=async()=>{};
addEventListener('popstate',()=>route());
function route(){
  const m=location.pathname.match(/^\/token\/([1-9A-HJ-NP-Za-km-z]{32,44})\/?$/);
  if(m){openToken(m[1],{push:false});return true}
  const h=(location.hash||'').slice(1);
  const v=(location.pathname==='/pulse'&&!h)?'pulse':h;
  if(view==='token'){leaveToken();document.title='xScanner · Solana memecoin scanner'}
  if(v&&v in loaders){window.show(v);return false}
  window.show('pulse');return false}
addEventListener('hashchange',()=>{const h=(location.hash||'').slice(1);if(h&&h!==view&&h in loaders&&!/^\/token\//.test(location.pathname))window.show(h)});

// ===================================================================== WALLET TRACKER (watch-only)
const WL={list:LS('xs_wallets_v1',[]),data:{},timer:null};
const looksSecret=v=>/\s/.test(v.trim())&&v.trim().split(/\s+/).length>=11||/^[1-9A-HJ-NP-Za-km-z]{64,100}$/.test(v.trim())||/^\[?\s*\d+(\s*,\s*\d+){30,}/.test(v.trim());
$('#wAdd').onsubmit=e=>{e.preventDefault();const v=$('#wIn').value.trim(),box=$('#wMsg');
  if(looksSecret(v)){$('#wIn').value='';box.className='searchmsg err';box.innerHTML='<b>That looks like a private key or seed phrase.</b> It was not saved or sent anywhere. Never paste those into any website. Public addresses only.';return}
  if(!B58.test(v)){box.className='searchmsg warn';box.textContent='That is not a valid Solana address (32-44 letters/numbers).';return}
  if(WL.list.some(w=>w.a===v)){box.className='searchmsg warn';box.textContent='Already tracking that wallet.';return}
  if(WL.list.length>=8){box.className='searchmsg warn';box.textContent='Up to 8 wallets (free RPC limits).';return}
  WL.list.push({a:v,l:($('#wLabel').value.trim()||'').slice(0,24)});LSs('xs_wallets_v1',WL.list);$('#wIn').value='';$('#wLabel').value='';box.className='searchmsg hidden';wRender();walletsRefresh()};
function wRender(){
  $('#wList').innerHTML=WL.list.length?WL.list.map(w=>{const d=WL.data[w.a];const ev=d?.events||[];
    const buys=ev.filter(x=>x.side==='buy'),sells=ev.filter(x=>x.side==='sell'),toks=new Set(ev.map(x=>x.mint)).size;
    const solIn=sells.reduce((a,x)=>a+(+x.sol||0),0),solOut=buys.reduce((a,x)=>a+(+x.sol||0),0);
    const st=!d?'checking…':d.status==='pending'&&!d.events?'loading recent swaps…':`last ${d.txs_checked||0} txs`+(d.error?' · RPC partial':'');
    return `<div class="wcard"><div class="wch"><b>${esc(w.l||'Wallet')}</b><button class="linkbtn" data-wdel="${esc(w.a)}">remove</button></div><div class="kvl">
     ${kvRow('Address',addrCell(w.a),addrBtns(w.a,'account'))}
     ${kvRow('Moves',`<span class="up">${buys.length} buys</span> · <span class="dn">${sells.length} sells</span> · ${toks} tokens`,'<small class="muted">'+st+'</small>')}
     ${kvRow('SOL flow',`<span class="dn">−${solOut.toFixed(2)}</span> / <span class="up">+${solIn.toFixed(2)}</span>`,'<small class="muted">in the moves shown</small>')}
     ${kvRow('Explorers',`<a class="xl" href="https://solscan.io/account/${esc(w.a)}" target="_blank" rel="noopener noreferrer">Solscan ↗</a> <a class="xl" href="https://explorer.solana.com/address/${esc(w.a)}" target="_blank" rel="noopener noreferrer">Solana Explorer ↗</a>`)}</div></div>`}).join(''):'<p class="muted">Add a public wallet address above to follow what it buys and sells.</p>';
  const ev=[];for(const w of WL.list){const d=WL.data[w.a];for(const x of (d?.events||[]))ev.push({...x,wl:w.l||shortCa(w.a)})}
  ev.sort((a,b)=>(b.t||0)-(a.t||0));
  $('#wFeed').innerHTML=ev.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>When</th><th>Wallet</th><th>Move</th><th>Token</th><th>Amount</th><th>SOL</th></tr></thead><tbody>${ev.slice(0,60).map(x=>`<tr><td>${x.t?ageS(now()-x.t)+' ago':'—'}</td><td>${esc(x.wl)}</td><td class="${x.side==='buy'||x.side==='in'?'up':'dn'}">${x.side==='in'?'received':x.side==='out'?'sent':x.side}</td><td><a href="/token/${esc(x.mint)}" data-tok="${esc(x.mint)}"><b>$${esc(x.sym||shortCa(x.mint))}</b></a></td><td>${x.tokens>=1000?Math.round(x.tokens).toLocaleString():(+x.tokens).toPrecision(3)}${x.now_usd?` <small class="muted">(${usd(x.now_usd)} now)</small>`:''}</td><td>${x.sol!=null?x.sol:'—'}</td></tr>`).join('')}</tbody></table></div><p class="muted" style="font-size:11px">Swaps are inferred from token-balance changes in each wallet’s latest transactions. Transfers with no SOL leg show as received / sent. Read-only.</p>`:
    `<p class="muted">${WL.list.length?'No token swaps found in the latest transactions yet…':'Activity shows here.'}</p>`}
document.addEventListener('click',e=>{const b=e.target.closest('[data-wdel]');if(!b)return;WL.list=WL.list.filter(w=>w.a!==b.dataset.wdel);LSs('xs_wallets_v1',WL.list);delete WL.data[b.dataset.wdel];wRender()});
async function walletsRefresh(){
  clearTimeout(WL.timer);if(view!=='wallets'||!WL.list.length){$('#wStat').textContent='';return}
  let pend=false;
  for(const w of WL.list){try{const j=await api('/api/wallet/'+w.a);if(j.status==='pending'){pend=true;WL.data[w.a]={...(WL.data[w.a]||{}),...j}}else WL.data[w.a]=j}catch(e){WL.data[w.a]={status:'error',error:e.message,events:WL.data[w.a]?.events}}
    wRender();if(view!=='wallets')return}
  $('#wStat').textContent=pend?'loading…':'updated '+new Date().toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT · refreshes every 45 s';
  WL.timer=setTimeout(walletsRefresh,pend?3500:45000)}

// sync.js writes server data into localStorage then calls this so the in-memory state follows
window.XS_reload=function(){
  W.list=LS('xs_watch_v1',[]);W.cfg=LS('xs_watch_cfg_v1',{pct:20,notify:false});WL.list=LS('xs_wallets_v1',[]);presets=LS('xs_pulse_presets_v1',{});
  paintStars();if(view==='watch'){renderWatch();watchPoll(true)}if(view==='wallets'){wRender();walletsRefresh()}if(view==='pulse')paintPresets()};
window.XS_changed=function(kind){/* local edits already went through LSs(); sync.js hooks localStorage */};
// ===================================================================== boot
paintStars();watchPoll(true);setInterval(()=>watchPoll(false),15000);
(()=>{const isTok=/^\/token\//.test(location.pathname);
  if(isTok){route()}
  else{const h=(window.__h0||location.hash||'#pulse').slice(1);const v=(h in loaders)?h:'pulse';window.show(v);if(view==='pulse'&&v==='pulse'){/* started by show */}}})();
})();
