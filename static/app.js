const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const usd=v=>{if(v==null||v===''||!isFinite(+v))return'—';v=+v;const a=Math.abs(v);
  for(const[d,u]of[[1e12,'T'],[1e9,'B'],[1e6,'M'],[1e3,'K']])if(a>=d){let s=(v/d).toFixed(a/d>=100?0:a/d>=10?1:2);if(s.includes('.'))s=s.replace(/0+$/,'').replace(/\.$/,'');return'$'+s+u}
  return a>=1||a===0?'$'+v.toFixed(0):(typeof fmtPx==='function'?fmtPx(v):'$'+v.toPrecision(2))};
const pct=v=>v==null||v===''||!isFinite(+v)?'—':`<span class="${v>=0?'up':'dn'}">${v>=0?'+':''}${(+v).toFixed(1)}%</span>`;
const rm=v=>v==null?'—':`<span class="${v>=0?'up':'dn'}">RM${(+v).toFixed(2)}</span>`;
const tierCls=s=>s>=65?'t-look':s>=50?'t-watch':'t-skip';
const AXES={paid_shill:'Paid shill',copycat:'Copycat',bundle:'Bundle',dump:'Dump/fade',rug:'Rug risk',thin_liq:'Thin liq'};
const imgUrl=(m,sym)=>'/api/img/'+encodeURIComponent(m)+(sym?'?s='+encodeURIComponent(sym):'');
const tokImg=(m,sym,cls='timg')=>m?`<img class="${cls}" src="${imgUrl(m,sym)}" alt="" loading="lazy" decoding="async" onerror="this.style.visibility='hidden'">`:'';
const shortCa=m=>m?m.slice(0,4)+'…'+m.slice(-4):'';
const ageTxt=h=>{if(h==null||!isFinite(+h))return'—';h=+h;if(h<1)return Math.max(1,Math.round(h*60))+'m';if(h<48)return h.toFixed(h<10?1:0)+'h';
  const d=h/24;if(d<60)return Math.round(d)+'d';if(d<730)return Math.round(d/30.4)+'mo';return (d/365).toFixed(1)+'y'};
let layout=localStorage.getItem('xs_layout')||'grid';
let sort={k:'score',desc:true}, view='feed', riskData=null, publicReadonly=false;
const feedPrev={};
let drawerGen=0;
function skelCards(n=12){
  return Array.from({length:n},()=>`<article class="tcard skel-card" aria-hidden="true"><div class="tthumb skel"></div><div class="tbody"><div class="skel skel-line"></div><div class="skel skel-line short"></div><div class="skel skel-line mid"></div></div></article>`).join('');
}
async function api(p,o){const r=await fetch(p,o);const j=await r.json().catch(()=>({}));if(r.status===401&&(j.detail==='sign in required'||j.required)){location.replace('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));throw new Error('sign in required')}if(!r.ok&&r.status!==409)throw new Error(j.detail||r.status);return j}
function banner(msg,cls=''){const b=$('#banner');if(!msg){b.className='banner hidden';return}b.className='banner '+cls;b.innerHTML=msg;if(window.XS&&cls==='err')XS.err()}
function flagCls(f){f=f.toLowerCase();if(/^(data uncertain|data confidence|bundle risk|bundle check)/.test(f))return 'amb';return /^data:|^bundle:|^concentrated:|dump|rug|blacklist|copycat|bundle:|fading|mint|freeze|paid shill|phish/.test(f)?'red':/thin|weak|no momentum|cooling|boost|capped|copycats/.test(f)?'amb':''}

async function loadSummary(){
  const s=await api('/api/summary');
  const src=s.sources||{}; const bad=Object.entries(src).filter(([k,m])=>m.error).map(([k,m])=>`${k}: ${m.error}`);
  banner(bad.length?`Data issue — ${esc(bad.join(' · '))}. Showing whatever is available.`:'', bad.length?'':'');
  const ver=s.verdict||'—';
  const vcls=/buy-worthy/i.test(ver)?'grn':/bad data|error|caution/i.test(ver)?'red':'amb';
  const chips=[['Coins in report',s.coin_count],['Worth a look',s.tiers?.look],['Watch',s.tiers?.watch],
    ['X posts / paid',`${s.x_posts??'—'} / ${s.x_shill_posts??'—'}`],['Checked / deep',`${s.coins_checked??'—'} / ${s.deep_checked??'—'}`]];
  $('#stats').innerHTML=`<div class="statstrip-track"><span class="verdict-pill ${vcls}" title="${esc(ver)}">${esc(ver)}</span>${chips.map(([a,b])=>`<div class="schip"><span>${a}</span><b>${esc(b??'—')}</b></div>`).join('')}</div>`;
}

async function loadFeed(){
  const grid=$('#cardGrid');
  const hadCards=!!grid.querySelector('.tcard:not(.skel-card)');
  let sk;
  if(!hadCards){grid.innerHTML=skelCards(12);grid.setAttribute('aria-busy','true')}
  else{loadFeed._pending=true;sk=setTimeout(()=>{if(loadFeed._pending){grid.innerHTML=skelCards(12);grid.setAttribute('aria-busy','true')}},400)}
  const q=new URLSearchParams({min_score:$('#fScore').value,min_liq:$('#fLiq').value||0,pump_only:$('#fPump').checked,hide_paid:$('#fPaid').checked,sort:sort.k,desc:sort.desc});
  let d;
  try{d=await api('/api/coins?'+q)}finally{loadFeed._pending=false;clearTimeout(sk)}
  $('#feedCount').textContent=`${d.count} of ${d.total} coins`;
  $$('#feed th').forEach(th=>{th.classList.toggle('sorted',th.dataset.s===sort.k);th.textContent=th.textContent.replace(/ [▲▼]$/,'')+(th.dataset.s===sort.k?(sort.desc?' ▼':' ▲'):'')});
  $('#feed tbody').innerHTML=d.coins.length?d.coins.map(c=>`<tr data-m="${esc(c.mint)}">
    <td><span class="score ${tierCls(c.score)}">${c.score??'—'}</span></td>
    <td class="sym">${tokImg(c.mint,c.symbol,'tava')}<b>$${esc(c.symbol)}</b> ${c.pump?'<span class="pill pur">pump</span>':''}<small>${esc(c.name)}</small></td>
    <td><span class="pill ${c.tier==='look'?'grn':c.tier==='watch'?'amb':'red'}">${c.status||c.tier}</span></td>
    <td>${usd(c.mcap)}</td><td>${usd(c.liq)}</td><td>${usd(c.vol24)}</td><td>${pct(c.pc1)}</td><td>${pct(c.pc24)}</td>
    <td>${ageTxt(c.age_h)}</td>
    <td>${c.paid_share!=null?`<span class="${c.paid_shilled?'dn':''}">${Math.round(c.paid_share*100)}%</span>`:'—'}</td>
    <td>${(c.flags||[]).slice(0,3).map(f=>`<span class="pill ${flagCls(f)}" title="${esc(f)}">${esc(f.length>28?f.slice(0,28)+'…':f)}</span>`).join('')}${c.flags.length>3?`<span class="pill">+${c.flags.length-3}</span>`:''}</td></tr>`).join('')
    :`<tr><td colspan="11" class="muted" style="padding:30px;text-align:center">No coins match (or report.json is missing/empty).</td></tr>`;
  grid.innerHTML=d.coins.length?d.coins.map(cardHTML).join(''):`<div class="xempty">No coins match (or report.json is missing/empty).</div>`;
  grid.removeAttribute('aria-busy');
  $$('#cardGrid .tcard').forEach(el=>el.onclick=e=>{if(e.target.closest('.cpy'))return;openCoin(el.dataset.m)});
  $$('#cardGrid .cpy').forEach(b=>b.onclick=e=>{e.stopPropagation();copyCa(b.dataset.cp,b)});
  applyLayout();
  $$('#feed tbody tr[data-m]').forEach(tr=>tr.onclick=()=>openCoin(tr.dataset.m));
  const first=!hadCards;
  d.coins.forEach(c=>{
    const el=grid.querySelector(`.tcard[data-m="${CSS.escape(c.mint)}"]`);
    const prev=feedPrev[c.mint];
    const mc=el&&el.querySelector('[data-n=mcap]');
    const p1=el&&el.querySelector('[data-n=pc1]');
    const p24=el&&el.querySelector('[data-n=pc24]');
    if(prev&&window.XS&&XS.motionOn()&&el){
      let changed=false;
      if(mc&&prev.mcap!=null&&c.mcap!=null&&prev.mcap!==c.mcap){mc.dataset.v=prev.mcap;XS.tick(mc,c.mcap,usd);changed=true}
      if(p1&&prev.pc1!=null&&c.pc1!=null&&prev.pc1!==c.pc1){p1.dataset.v=prev.pc1;XS.tick(p1,c.pc1,pct);changed=true}
      if(p24&&prev.pc24!=null&&c.pc24!=null&&prev.pc24!==c.pc24){p24.dataset.v=prev.pc24;XS.tick(p24,c.pc24,pct);changed=true}
      if(changed)XS.flash(el);
    }else{
      if(mc&&c.mcap!=null)mc.dataset.v=c.mcap;
      if(p1&&c.pc1!=null)p1.dataset.v=c.pc1;
      if(p24&&c.pc24!=null)p24.dataset.v=c.pc24;
    }
    feedPrev[c.mint]={mcap:c.mcap,pc1:c.pc1,pc24:c.pc24};
  });
  if(first&&window.XS){
    if(layout==='grid')XS.applyEnter(grid,'.tcard');
    else XS.applyEnter($('#feed tbody'),'tr[data-m]',true);
  }
}

function cardHTML(c){
  const fl=c.flags||[];
  const chips=fl.slice(0,3).map(f=>`<span class="pill ${flagCls(f)}" title="${esc(f)}">${esc(f.length>22?f.slice(0,22)+'…':f)}</span>`).join('')+(fl.length>3?`<span class="pill">+${fl.length-3}</span>`:'');
  return `<article class="tcard ${c.status==='blocked'?'blocked':''}" data-m="${esc(c.mint)}" tabindex="0">
    <div class="tthumb">${tokImg(c.mint,c.symbol)}<span class="score ${tierCls(c.score)} tbadge" title="Score ${c.score}/100">${c.score??'—'}</span>${c.pump?'<span class="pill pur tpump">pump</span>':''}</div>
    <div class="tbody"><div class="tname" title="${esc(c.name)}">${esc(c.name||c.symbol)}</div>
    <div class="trow"><b class="ttick">$${esc(c.symbol)}</b><span class="tca"><code title="${esc(c.mint)}">${esc(shortCa(c.mint))}</code><button class="cpy" data-cp="${esc(c.mint)}" title="Copy CA" aria-label="Copy contract address">⧉</button></span></div>
    <div class="tmc"><span class="muted">MC</span> <b data-n="mcap">${usd(c.mcap)}</b> <span class="muted">· Liq</span> ${usd(c.liq)}</div>
    <div class="tstats"><span>⏱ ${ageTxt(c.age_h)}</span><span>1h <span data-n="pc1">${pct(c.pc1)}</span></span><span>24h <span data-n="pc24">${pct(c.pc24)}</span></span></div>
    ${c.story_short?`<div class="twhy" title="${esc(c.story_short)}"><b>Why it's moving</b> ${esc(c.story_short)}</div>`:''}
    <div class="tflags">${chips||'<span class="muted" style="font-size:11px">no flags</span>'}</div></div></article>`;
}
async function copyCa(ca,btn){try{await navigator.clipboard.writeText(ca)}catch(e){const t=document.createElement('textarea');t.value=ca;document.body.appendChild(t);t.select();try{document.execCommand('copy')}catch(_){}t.remove()}
  if(btn){const o=btn.textContent;btn.textContent='✓';btn.classList.add('ok');setTimeout(()=>{btn.textContent=o;btn.classList.remove('ok')},1200)}}
function applyLayout(){$('#cardGrid').classList.toggle('hidden',layout!=='grid');$('#tableWrap').classList.toggle('hidden',layout!=='table');
  $$('.vtoggle button').forEach(b=>b.classList.toggle('on',b.dataset.l===layout));$('#fSort').value=sort.k;}
function radarSVG(risk,size=260){
  const keys=Object.keys(AXES),n=keys.length,cx=size/2,cy=size/2,R=size/2-42;
  const pt=(i,v)=>{const a=-Math.PI/2+i*2*Math.PI/n;return[cx+Math.cos(a)*R*v,cy+Math.sin(a)*R*v]};
  let g='';[.25,.5,.75,1].forEach(l=>g+=`<polygon points="${keys.map((_,i)=>pt(i,l).join(',')).join(' ')}" fill="none" stroke="#232838"/>`);
  keys.forEach((k,i)=>{const[x,y]=pt(i,1),[lx,ly]=pt(i,1.22);g+=`<line x1="${cx}" y1="${cy}" x2="${x}" y2="${y}" stroke="#232838"/><text x="${lx}" y="${ly}" fill="#8a91a6" font-size="11" text-anchor="middle" dominant-baseline="middle">${AXES[k]}</text>`});
  const poly=keys.map((k,i)=>pt(i,Math.max(.03,risk[k]||0)).join(',')).join(' ');
  g+=`<polygon points="${poly}" fill="#ff5c7a33" stroke="#ff5c7a" stroke-width="2"/>`;
  keys.forEach((k,i)=>{const[x,y]=pt(i,Math.max(.03,risk[k]||0));g+=`<circle cx="${x}" cy="${y}" r="3" fill="#ff5c7a"/>`});
  return `<svg viewBox="0 0 ${size} ${size}" width="100%" style="max-width:${size+60}px;display:block;margin:auto">${g}</svg>`;
}
function spark(series,w=260,h=60){
  if(!series||!series.length)return '<div class="muted">No hourly series.</div>';
  const mx=Math.max(1,...series.map(s=>s.value||0)),st=w/Math.max(1,series.length-1);
  const p=series.map((s,i)=>`${i*st},${h-4-(s.value||0)/mx*(h-14)}`).join(' ');
  return `<svg viewBox="0 0 ${w} ${h+14}" width="100%"><polyline points="0,${h} ${p} ${w},${h}" fill="#9b6bff22" stroke="none"/><polyline points="${p}" fill="none" stroke="#9b6bff" stroke-width="2"/>${series.map((s,i)=>`<text x="${i*st}" y="${h+12}" font-size="9" fill="#8a91a6" text-anchor="${i===0?'start':i===series.length-1?'end':'middle'}">${esc(s.label)}</text>`).join('')}</svg>`;
}

// 'Linked wallets' is only RugCheck's insider-network count; other detectors are named for what they measure
const clusterLabel=b=>({'near-identical holders':'Near-identical holders','same funding wallet':'Holders from one funder','top-10 holders':'Top holders'}[b.basis]||'Linked wallets');
function bundleHTML(c){
  const b=c.bundle; if(!b)return '';
  const lvl=b.status==='flagged'?'red':b.status==='elevated'||b.status==='unavailable'?'amb':'grn';
  const word={flagged:b.reason&&b.reason.startsWith('Concentrated')?'CONCENTRATED':'BUNDLE',elevated:'Bundle risk',unavailable:'Check unavailable',ok:'Passed'}[b.status]||b.status;
  const f=b.fund&&b.fund.resolved?`${b.fund.largest} of ${b.fund.resolved} checked top holders share one creator wallet`:'RPC funding check not available';
  return `<h3>Bundle / insider check <span class="pill ${lvl}">${esc(word)}</span></h3><p class="muted" style="margin:4px 0">${esc(b.reason||'')}${b.status==='unavailable'?' — missing data is never counted as safe.':''}</p>
   <div class="kv"><div><span>${esc(clusterLabel(b))}</span>${b.linked_wallets??'—'}${b.basis&&b.linked_wallets?` <small class="muted">(${esc(b.basis)})</small>`:''}</div><div><span>Share held by cluster</span>${b.share_pct!=null?b.share_pct+'%':(b.linked_wallets?'<span title="RugCheck reports more than 100% of supply for these wallets, so the share is not credible">unknown</span>':'—')}</div><div><span>Insider networks</span>${b.networks??'—'}</div>
   <div><span>Top 10 (ex-pool)</span>${b.top10_ex_lp!=null&&b.status!=='unavailable'?b.top10_ex_lp+'%':'—'}</div><div><span>Insider top holders</span>${b.insider_top_holders??'—'}</div><div><span>On-chain funding</span><small>${esc(f)}</small></div></div>`;
}
function confHTML(c){
  const d=c.data_confidence; if(!d)return '';
  const lvl=d.level==='high'?'grn':d.level==='medium'?'amb':'red';
  const fmt=v=>typeof v==='boolean'?(v?'set':'none'):typeof v==='number'?(Math.abs(v)>=1000?Math.round(v).toLocaleString():+v.toPrecision(4)):esc(v);
  const rows=Object.values(d.fields||{}).map(f=>`<tr><td>${esc(f.label)}</td><td><span class="pill ${f.status==='agree'?'grn':f.status==='single'?'':'amb'}">${f.status==='agree'?'sources agree':f.status}</span></td><td class="muted" style="font-size:11px">${Object.entries(f.values||{}).map(([k,v])=>esc(k)+' '+fmt(v)).join(' · ')||'no source'}</td></tr>`).join('');
  return `<h3>Data confidence <span class="pill ${lvl}">${esc(d.label)} · ${d.score}/100</span></h3>
   ${(()=>{const pw={...(d.warnings||{}),...(d.problems||{})};return Object.keys(pw).length?`<p class="amb-note" style="margin:4px 0;color:#f5a524">⚠ ${Object.entries(pw).map(([k,v])=>'<b>'+esc(k)+'</b>: '+esc(v)).join(' · ')}</p>`:''})()}
   <p class="muted" style="margin:4px 0">${d.uncertain&&d.uncertain.length?'Shaky: '+esc(d.uncertain.join(', '))+'.':'Key fields agree across sources.'} Sources: ${esc((d.sources||[]).join(', ')||'none yet')}.</p>
   <table class="conf"><tbody>${rows}</tbody></table>`;
}

async function openCoin(mint,pre){
  const c=pre||await api('/api/coins/'+encodeURIComponent(mint)); const lk=c.lookup||null; const rc=c.rugcheck||{},x=c.x||{},p=c.parts||{};
  const tierWord=c.score>=65?'Worth a look':c.score>=50?'Watch':'Skip';
  const part=(l,v,m)=>`<div style="margin:6px 0"><div class="hrow"><span class="muted">${l}</span><span>${v??0} / ${m}</span></div><div class="bar"><i style="width:${(v||0)/m*100}%"></i></div></div>`;
  $('#dBody').innerHTML=`<div class="dhead">${tokImg(c.mint,c.symbol,'dimg')}<div style="min-width:0"><div class="hrow"><h2 style="margin:0">$${esc(c.symbol)} <span class="score ${tierCls(c.score)}">${c.score}</span> <small class="muted" style="font-size:13px" id="dVerdict">${esc(c.verdict||(c.status==='blocked'?'Blocked (hard flag)':tierWord))}</small></h2></div>
   <div class="muted">${esc(c.name)} · ${esc(c.dex)} ${c.pump?'· pump.fun':''}</div>
   <div class="tca dca"><code title="${esc(c.mint)}">${esc(shortCa(c.mint))}</code><button class="cpy" data-cp="${esc(c.mint)}" onclick="copyCa(this.dataset.cp,this)" title="Copy CA">⧉ Copy CA</button></div></div></div>
   ${lk?`<div class="lkbadge ${lk.source==='live'?'live':'rep'}">${lk.source==='live'?'● Live scan':'● From latest report'}${lk.cached?` · cached ${lk.cache_age_s}s ago`:''}${lk.partial?' · partial (RugCheck was slow; re-scored on the next view)':''}${lk.scored_at?' · scored '+new Date(lk.scored_at*1000).toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT':''}${lk.source==='live'&&!lk.x_available?' · X / paid-shill data unavailable':''}</div>`:''}
   ${lk?.copy_of?`<div class="lkbadge warn">Looks like a copycat of $${esc(lk.copy_of.symbol)} (${usd(lk.copy_of.mcap)} mcap)</div>`:''}
   ${chartBlockHTML()}
   <h3 style="margin-top:12px">The story</h3><p class="story">${esc(c.story||'No story available yet for this coin.')}</p>
   <h3 style="margin-top:14px">Scores</h3>${c.reason?`<p class="muted" style="font-size:12px;margin:2px 0 6px">Scanner notes: ${esc(c.reason)}</p>`:''}
   ${part('X narrative',p.x,40)}${part('Market',p.market,35)}${part('Safety',p.safety,25)}
   ${bundleHTML(c)}${confHTML(c)}
   <div class="kv"><div><span>MCap</span>${usd(c.mcap)}</div><div><span>Liquidity</span>${usd(c.liq)}</div><div><span>Vol 24h</span>${usd(c.vol24)}</div>
   <div><span>5m / 1h / 24h</span>${pct(c.pc5)} ${pct(c.pc1)} ${pct(c.pc24)}</div><div><span>Buys/sells 5m</span>${c.buys5??'—'} / ${c.sells5??'—'}</div><div><span>Buys/sells 1h</span>${c.buys1h??'—'} / ${c.sells1h??'—'}</div>
   <div><span>Age</span>${ageTxt(c.age_h)}</div><div><span>Fees est (SOL)</span>${c.fees_est??'—'}</div><div><span>Boosts · copycats</span>${c.boosts??0} · ${c.copycats??0}</div></div>
   <h3>Risk radar</h3>${radarSVG(c.risk,240)}
   <h3>Flags</h3><div class="flaglist">${(c.flags||[]).map(f=>`<div><span class="pill ${flagCls(f)}">●</span>${esc(f)}</div>`).join('')||'<div class="muted">none</div>'}</div>
   <h3 style="margin-top:14px">X</h3><div class="kv"><div><span>Mentions</span>${x.mentions??'—'}</div><div><span>Organic / paid accts</span>${x.organic_authors??'—'} / ${x.shill_authors??'—'}</div><div><span>Paid share</span>${x.shill_share!=null?Math.round(x.shill_share*100)+'%':'—'}</div>
   <div><span>Velocity now / avg</span>${c.vel?.last??'—'} / ${c.vel?.prev_avg??'—'}</div><div><span>Organic followers</span>${x.organic_followers??'—'}</div><div><span>Phishing posts</span>${x.phishing??'—'}</div></div>
   ${x.shill_reasons&&Object.keys(x.shill_reasons).length?`<div>${Object.entries(x.shill_reasons).map(([k,v])=>`<span class="pill red">${esc(k)}: ${v}</span>`).join('')}</div>`:''}
   ${(x.shill_accts||c.paid_accounts||[]).length?`<p class="muted">Paid accounts: ${(x.shill_accts||c.paid_accounts).slice(0,12).map(a=>'@'+esc(typeof a==='string'?a:a.username||JSON.stringify(a))).join(', ')}</p>`:''}
   ${c.narrative?`<h3>Hourly X mentions</h3>${spark(c.narrative.series)}`:''}
   <p style="margin:10px 0"><a class="primary" style="display:inline-block;text-decoration:none" href="/token/${esc(c.mint)}" onclick="if(window.openToken){closeDrawer();openToken('${esc(c.mint)}');return false}">Open trader page →</a></p>
   <h3 style="margin-top:14px">RugCheck</h3><div class="kv"><div><span>Score</span>${rc.score??'—'}</div><div><span>Top10 / top1</span>${rc.top10??'—'}% / ${rc.top1??'—'}%</div><div><span>Holders</span>${rc.holders??'—'}</div>
   <div><span>RugCheck bundle</span>${rc.bundle_pct??'—'}% (${rc.bundle_wallets??'—'} wallets)</div><div><span>Insider networks</span>${rc.insider_networks??'—'}</div><div><span>Creator rugged</span>${rc.creator_rugged??'—'}</div>
   <div><span>Mint auth</span>${rc.mint_auth?'ON':'off'}</div><div><span>Freeze auth</span>${rc.freeze_auth?'ON':'off'}</div><div><span>Launchpad</span>${esc(rc.launchpad??'—')}</div></div>
   ${(rc.risks||[]).length?`<div>${rc.risks.map(r=>`<span class="pill amb">${esc(typeof r==='string'?r:r.name||JSON.stringify(r))}</span>`).join('')}</div>`:''}
   <h3 style="margin-top:14px">Links</h3><p>${c.url?`<a href="${esc(c.url)}" target="_blank" rel="noopener">DexScreener</a> · `:''}<a href="https://rugcheck.xyz/tokens/${esc(c.mint)}" target="_blank" rel="noopener">RugCheck</a>${(c.organic_examples||[]).map((u,i)=>` · <a href="${esc(u)}" target="_blank" rel="noopener">organic post ${i+1}</a>`).join('')}</p>
   <p class="muted" style="font-size:11px;word-break:break-all">Mint ${esc(c.mint)}<br>Pool ${esc(c.pool)}</p>`;
  drawerGen++;if(typeof CH!=='undefined')CH.h=0;
  $('#drawer').classList.add('open');$('#scrim').classList.add('on');$('#scrim').classList.remove('hidden');
  mountChart(c);
}
function closeDrawer(){
  const g=++drawerGen;
  $('#drawer').classList.remove('open');$('#scrim').classList.remove('on');
  const done=()=>{if(g!==drawerGen)return;unmountChart()};
  if(!window.XS||!XS.motionOn()){done();return}
  const d=$('#drawer');
  const te=(e)=>{if(e.propertyName==='transform'){d.removeEventListener('transitionend',te);done()}};
  d.addEventListener('transitionend',te);
  setTimeout(done,500);
}

async function loadNarr(){
  const d=await api('/api/narratives');
  $('#themes').innerHTML=(d.themes||[]).map(t=>`<div style="margin:6px 0"><span class="pill pur">${esc(t.word)}</span> ${(t.tickers||[]).map(x=>`<span class="pill">$${esc(x)}</span>`).join('')}</div>`).join('')||'<div class="muted">No shared themes this scan.</div>';
  $('#blocked').innerHTML=((d.blocked||[]).map(b=>`<div style="margin:4px 0"><span class="pill red">${esc(b.kind)}</span> <b>$${esc(b.ticker)}</b> <span class="muted">${esc(b.why)}</span></div>`).join('')||'')+
    ((d.blacklisted_seen||[]).length?`<div style="margin-top:8px" class="muted">Blacklisted names seen: ${d.blacklisted_seen.map(esc).join(', ')}</div>`:'')||'<div class="muted">Nothing blocked.</div>';
  $('#narrCards').innerHTML=(d.narratives||[]).map(n=>{const tot=(n.real||0)+(n.paid||0),real=tot?Math.round((n.real||0)/tot*100):0;
    return `<div class="ncard" data-m="${esc(n.mint)}"><div class="hrow"><b>$${esc(n.ticker)}</b><span class="${(n.velRatio||0)>=1?'up':'dn'}">${n.velRatio!=null?(+n.velRatio).toFixed(2)+'× vel':''}</span></div>
     <div class="muted">${esc(n.theme)}${n.blacklisted?' · <span class="dn">blacklisted</span>':''}</div>${spark(n.series)}
     <div class="hrow" style="font-size:12px"><span>${n.velNow??'—'}/h now · ${n.velAvg??'—'}/h avg</span><span>${n.mentions??0} mentions</span></div>
     <div style="margin-top:6px;font-size:12px" class="muted">Organic ${n.real??0} vs paid ${n.paid??0} accounts</div><div class="bar"><i style="width:${real}%;background:var(--grn)"></i></div></div>`}).join('')||'<div class="card muted">No narrative data (dashboard_data.json missing).</div>';
  $$('.ncard').forEach(e=>e.onclick=()=>openCoin(e.dataset.m).catch(()=>banner('That coin is not in the current report.','')));
  if(window.XS)XS.applyEnter($('#narrCards'),'.ncard');
}

async function loadRisk(){
  riskData=await api('/api/risk'); const d=riskData,keys=d.axes;
  $('#radarSel').innerHTML=d.coins.map(c=>`<option value="${esc(c.mint)}">$${esc(c.symbol)} (${c.score})</option>`).join('');
  drawRadar();
  const mx=Math.max(1,d.coins.length);
  $('#flagBars').innerHTML=keys.map(k=>`<div style="margin:9px 0"><div class="hrow"><span>${AXES[k]}</span><span class="muted">${d.flag_counts[k]} / ${d.coins.length}</span></div><div class="bar"><i style="width:${d.flag_counts[k]/mx*100}%;background:linear-gradient(90deg,#ffb547,#ff5c7a)"></i></div></div>`).join('');
  const col=v=>`background:rgba(255,92,122,${.08+v*.75});color:${v>.5?'#fff':'#e6e9f2'}`;
  $('#heat').className='heat';
  $('#heat').innerHTML=`<thead><tr><th>Coin</th><th>Score</th>${keys.map(k=>`<th>${AXES[k]}</th>`).join('')}</tr></thead><tbody>`+
    d.coins.map(c=>`<tr data-m="${esc(c.mint)}"><td><b>$${esc(c.symbol)}</b></td><td><span class="score ${tierCls(c.score)}">${c.score}</span></td>${keys.map(k=>`<td class="c" style="${col(c.risk[k])}">${Math.round(c.risk[k]*100)}</td>`).join('')}</tr>`).join('')+'</tbody>';
  $$('#heat tbody tr').forEach(tr=>tr.onclick=()=>openCoin(tr.dataset.m));
}
function drawRadar(){const c=riskData?.coins.find(c=>c.mint===$('#radarSel').value);if(!c){$('#radar').innerHTML='<div class="muted">No coins.</div>';return}
  $('#radar').innerHTML=radarSVG(c.risk);$('#radarFlags').innerHTML=c.flags.map(f=>`<div><span class="pill ${flagCls(f)}">●</span>${esc(f)}</div>`).join('')}

async function loadPaper(){
  const d=await api('/api/paper'); const err=d.sources?.ledger?.error;
  $('#paperKpis').innerHTML=[['Start',d.start_rm!=null?'RM'+d.start_rm:'—'],['Cash',d.cash_rm!=null?'RM'+d.cash_rm:'—'],['Realised P&L',rm(d.realised_pnl_rm)],['USDC profit (ledger)',d.usdc_profit_rm!=null?'RM'+d.usdc_profit_rm:'—'],
    ['Trades',d.n_trades],['Win rate',d.win_rate!=null?d.win_rate+'%':'—'],['Open position',d.open_position?esc(d.open_position.symbol||'yes'):'none']]
    .map(([a,b])=>`<div class="stat"><span>${a}</span><b>${b}</b></div>`).join('')+(err?`<div class="stat"><span>Ledger</span><b class="dn" style="font-size:13px">${esc(err)}</b></div>`:'');
  const cv=d.curve||[];
  if(cv.length){const w=520,h=170,vals=[0,...cv.map(c=>c.cum)],mn=Math.min(...vals),mx=Math.max(...vals),rg=(mx-mn)||1,st=w/cv.length;
    const y=v=>12+(mx-v)/rg*(h-34);const pts=vals.map((v,i)=>`${i*st},${y(v)}`).join(' ');
    $('#curve').innerHTML=`<svg viewBox="-6 0 ${w+12} ${h}" width="100%"><line x1="0" x2="${w}" y1="${y(0)}" y2="${y(0)}" stroke="#2c3245" stroke-dasharray="4"/>
     <polyline points="${pts}" fill="none" stroke="${cv.at(-1).cum>=0?'#2ee59d':'#ff5c7a'}" stroke-width="2.5"/>
     ${cv.map((c,i)=>`<circle cx="${(i+1)*st}" cy="${y(c.cum)}" r="4" fill="${c.pnl>=0?'#2ee59d':'#ff5c7a'}"/><text x="${(i+1)*st}" y="${h-4}" font-size="10" fill="#8a91a6" text-anchor="end">${esc(c.symbol)}</text>`).join('')}</svg>`;
  } else $('#curve').innerHTML='<div class="muted">No closed trades.</div>';
  $('#rules').textContent=d.rules||'—'; $('#lastRun').textContent=`${d.last_run||'—'} — ${d.last_run_note||''}`;
  const dt=s=>s?new Date(s).toLocaleString('en-MY',{timeZone:'Asia/Kuala_Lumpur',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'}):'—';
  $('#trades').innerHTML=`<thead><tr><th>Coin</th><th>Entry (MYT)</th><th>Exit (MYT)</th><th>Size</th><th>Peak</th><th>Gain</th><th>P&L</th><th>Exit reason</th></tr></thead><tbody>`+
    (d.trades||[]).map(t=>`<tr><td><b>$${esc(t.symbol)}</b></td><td>${dt(t.entry_time)}</td><td>${dt(t.exit_time)}</td><td>RM${t.size_rm??'—'}</td><td>${pct(t.peak_gain_pct)}</td><td>${pct(t.gain_pct)}</td><td>${rm(t.pnl_rm)}</td><td class="muted" style="white-space:normal;min-width:240px">${esc(t.exit_reason)}</td></tr>`).join('')+'</tbody>';
  $('#cands').innerHTML=`<thead><tr><th>Time (MYT)</th><th>Ticker</th><th>Decision</th><th>Reason</th></tr></thead><tbody>`+
    (d.candidates_seen||[]).map(c=>`<tr><td>${dt(c.time)}</td><td><b>${esc(c.ticker)}</b></td><td><span class="pill ${/buy/.test(c.decision)?'grn':/sell/.test(c.decision)?'pur':''}">${esc(c.decision)}</span></td><td class="muted" style="white-space:normal;min-width:320px">${esc((c.reason||'').slice(0,220))}</td></tr>`).join('')+'</tbody>';
}

const loaders={feed:loadFeed,narr:loadNarr,risk:loadRisk,paper:loadPaper,pulse:async()=>{},watch:async()=>{},wallets:async()=>{},token:async()=>{}};
async function refresh(){try{await loadSummary();await loaders[view]()}catch(e){banner('Failed to load: '+esc(e.message),'err')}}
function show(v){
  const prev=view, first=!show.ready;
  view=v;
  $$('#tabs button').forEach(b=>b.classList.toggle('on',b.dataset.v===v));
  location.hash=v;
  const cur=$('#v-'+prev), next=$('#v-'+v);
  const reveal=()=>{$$('.view').forEach(s=>{s.classList.toggle('hidden',s.id!=='v-'+v);s.classList.remove('fx-leave','fx-enter-view')});show.ready=true;refresh()};
  if(first||prev===v||!window.XS||!XS.motionOn()||!cur||cur===next||cur.classList.contains('hidden')){reveal();return}
  let done=false;
  const finish=()=>{if(done)return;done=true;cur.classList.add('hidden');cur.classList.remove('fx-leave');next.classList.remove('hidden');next.classList.add('fx-enter-view');next.addEventListener('animationend',()=>next.classList.remove('fx-enter-view'),{once:true});show.ready=true;refresh()};
  cur.classList.add('fx-leave');
  cur.addEventListener('animationend',finish,{once:true});
  setTimeout(finish,280);
}


async function loadAuth(){
  // The header Sign in / account chip and the watchlist sync live in sync.js. Here we only honour AUTH_REQUIRED.
  try{
    const r=await fetch('/api/me'); const d=await r.json().catch(()=>({}));
    if(r.status===401 && d.required){
      location.replace('/login?next='+encodeURIComponent(location.pathname+location.search+location.hash));
    }
  }catch(e){/* offline: nothing to do */}
}

async function pollScan(){
  const s=await api('/api/scan');
  publicReadonly=!!s.public_readonly;
  const btn=$('#scanBtn');
  liveInfo=s.live||null; liveInfo&&(liveInfo._got=Date.now());
  if(publicReadonly){
    btn.hidden=true; btn.disabled=true;
    paintLive();
    return s;
  }
  btn.hidden=false;
  btn.disabled=s.running; btn.textContent=s.running?'⏳ Scanning…':'▶ Run scan';
  $('#scanInfo').textContent=s.running?`started ${s.started?.slice(11,19)} MYT`:s.finished?`last run ${s.finished.slice(11,16)} MYT · ${s.error?'failed':'ok'} (${s.duration_s}s)`:'';
  return s;
}
$('#scanBtn').onclick=async()=>{
  if(publicReadonly) return;
  const r=await api('/api/scan',{method:'POST'});
  if(!r.started){banner('A scan is already running.','');}
  else banner('Scan started — DexScreener + RugCheck refresh, reusing the last X data…','');
  let s; do{await new Promise(r=>setTimeout(r,1500)); s=await pollScan()}while(s.running);
  banner(s.error?`Scan failed: ${esc(s.error)}<pre>${esc(s.output)}</pre>`:`Scan finished in ${s.duration_s}s. Data reloaded.`, s.error?'err':'ok');
  refresh();
};

$$('#tabs button').forEach(b=>b.onclick=()=>show(b.dataset.v));
$$('.vtoggle button').forEach(b=>b.onclick=()=>{layout=b.dataset.l;localStorage.setItem('xs_layout',layout);applyLayout();if(window.XS){if(layout==='grid')XS.applyEnter($('#cardGrid'),'.tcard');else XS.applyEnter($('#feed tbody'),'tr[data-m]',true)}});
$('#fSort').onchange=()=>{sort={k:$('#fSort').value,desc:$('#fSort').value!=='age_h'};loadFeed()};
$$('#feed th[data-s]').forEach(th=>th.onclick=()=>{sort={k:th.dataset.s,desc:sort.k===th.dataset.s?!sort.desc:true};loadFeed()});
['#fLiq','#fPump','#fPaid'].forEach(s=>$(s).onchange=loadFeed);
$('#fScore').oninput=()=>{$('#fScoreV').textContent=$('#fScore').value;loadFeed()};
$('#radarSel').onchange=drawRadar; $('#dClose').onclick=closeDrawer; $('#scrim').onclick=closeDrawer;
document.addEventListener('keydown',e=>e.key==='Escape'&&closeDrawer());
loadAuth(); pollScan(); show((location.hash||'#pulse').slice(1) in loaders?(location.hash||'#pulse').slice(1):'pulse');
setInterval(()=>{if(publicReadonly||!$('#scanBtn').disabled){refresh();pollScan().catch(()=>{})}},60000);
// ---- Live-scan indicator ("Last scan: N min ago"), ticks every 15 s between data refreshes
var liveInfo=null;
function agoTxt(s){if(s==null)return'—';if(s<60)return'just now';const m=Math.round(s/60);return m<60?m+' min ago':Math.round(m/60)+' h ago'}
function paintLive(){
  const el=$('#scanInfo'); if(!el||!publicReadonly)return;
  const L=liveInfo;
  if(!L||!L.enabled){el.innerHTML='<span class="livedot off"></span>Snapshot view';el.title='Public read-only view';return}
  const drift=L._got?Math.round((Date.now()-L._got)/1000):0;
  if(!L.last_scan_epoch){el.innerHTML=`<span class="livedot pulse"></span>${L.running?'Scanning now…':'Starting first scan…'}`;el.title='Showing the saved snapshot until the first live scan finishes';return}
  const age=(L.now_epoch-L.last_scan_epoch)+drift;
  el.innerHTML=`<span class="livedot ${L.running?'pulse':''} ${age>600?'stale':''}"></span>Last scan: ${agoTxt(age)}`;
  el.title=`Live server scan every ${Math.round((L.interval_s||120)/60)} min · ${L.scans_ok} scans since wake · page refreshes every 60 s`+(L.last_error?` · last error: ${L.last_error}`:'');
}
setInterval(paintLive,15000);

// ---- Paste CA to scan
const B58=/^[1-9A-HJ-NP-Za-km-z]{32,44}$/;
function sMsg(msg,cls=''){const m=$('#searchMsg');if(!msg){m.className='searchmsg hidden';return}m.className='searchmsg '+cls;m.innerHTML=msg;if(window.XS){if(cls==='err')XS.err();else if(cls==='ok')XS.ok()}}
function extractMint(v){v=(v||'').trim();const m=v.match(/[1-9A-HJ-NP-Za-km-z]{32,44}/g);if(B58.test(v))return v;
  // allow pasted dexscreener/pump.fun/solscan links: take the last base58 run of 32-44 chars
  return m&&m.length?m[m.length-1]:v}
async function runLookup(raw){
  const mint=extractMint(raw); $('#searchIn').value=mint;
  if(!mint){sMsg('Paste a token contract address first.','err');return}
  if(!B58.test(mint)){sMsg('That doesn\'t look like a Solana token address. It should be 32–44 letters/numbers (no 0, O, I or l).','err');return}
  const btn=$('#searchBtn');btn.disabled=true;btn.textContent='⏳ Scanning…';
  sMsg(`<span class="spin"></span> Looking up <code>${esc(mint.slice(0,6))}…${esc(mint.slice(-6))}</code> — checking the report, then DexScreener + RugCheck live…`);
  const sleep=ms=>new Promise(res=>setTimeout(res,ms));
  try{
    let r=null,j={},ok=false;
    for(let i=1;i<=3;i++){
      try{
        r=await fetch('/api/lookup/'+encodeURIComponent(mint));
        j=await r.json().catch(()=>({}));
      }catch(e){r=null;j={}}
      if(r&&r.ok){ok=true;break}
      const busy= !r || [429,502,503].includes(r.status);
      if(busy && i<3){
        sMsg(`<span class="spin"></span> DexScreener is busy — retrying (${i}/2)…`,'warn');
        btn.textContent='⏳ Retrying…';
        await sleep(900*i);
        continue;
      }
      const detail=(j&&j.detail)||(r?('Lookup failed ('+r.status+')'):'Network error — try again.');
      sMsg(esc(typeof detail==='string'?detail:JSON.stringify(detail)), busy?'warn':'err');
      return;
    }
    if(!ok){sMsg('DexScreener is busy right now. Please try the scan again in a moment.','warn');return}
    const lk=j.lookup||{};
    sMsg(tokImg(mint,j.symbol,'savatar')+(lk.source==='live'?`<b>$${esc(j.symbol)}</b> scored <b>${j.score}</b>/100 in a live scan${lk.cached?' (cached)':''}${lk.stale?' (a moment ago)':''}. ${lk.x_available?'':'X / paid-shill data isn\'t available for live lookups.'}`
      :`<b>$${esc(j.symbol)}</b> is already in the latest scan report (score ${j.score}).`),'ok');
    openCoin(mint,j);
  }catch(e){sMsg('Network error — try again.','err')}
  finally{btn.disabled=false;btn.textContent='Scan CA'}
}
$('#searchForm').onsubmit=e=>{e.preventDefault();runLookup($('#searchIn').value)};
$('#searchIn').addEventListener('paste',e=>{const t=(e.clipboardData||window.clipboardData)?.getData('text');if(t&&B58.test(extractMint(t))){e.preventDefault();runLookup(t)}});
$('#pasteBtn').onclick=async()=>{try{const t=await navigator.clipboard.readText();$('#searchIn').value=t;runLookup(t)}catch(e){$('#searchIn').focus();sMsg('Clipboard blocked by the browser — long-press / Ctrl+V into the box instead.','warn')}};
// deep link: /?ca=<mint> runs the same lookup as pasting the address and opens its detail drawer
(()=>{try{const ca=new URLSearchParams(location.search).get('ca');if(ca)setTimeout(()=>runLookup(ca),400)}catch(e){}})();
