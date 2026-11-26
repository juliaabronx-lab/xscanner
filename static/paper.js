// Paper book: play-money buys and sells. No wallet, no keys, no transactions.
// Prices come from the token page and /api/quotes (both already cached server-side).
// The fill math lives in papertrade.py; this file only previews, waits, and draws.
(function(){
'use strict';
const PB={acct:null,user:null,signed:false,csrf:'',sol:null,summary:null,token:null,quotes:{},tab:'buy',limSide:'buy',
  busy:false,marking:false,seq:0,entryLine:null,candles:null,chartMint:null,shareUrl:null,panelMint:null};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const read=k=>{try{return JSON.parse(localStorage.getItem(k)||'null')}catch(e){return null}};
const write=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
const key=()=>'xs_paper_book_v1:'+(PB.signed&&PB.user?('u:'+PB.user):'anon');
const used=a=>!!(a&&((a.cash_sol||0)>0||(a.deposited_sol||0)>0||(a.trades||[]).length||Object.keys(a.positions||{}).length||(a.orders||[]).length));
function toast(msg,cls){let b=document.getElementById('toasts');if(!b){b=document.createElement('div');b.id='toasts';document.body.appendChild(b)}
  const t=document.createElement('div');t.className='toast '+(cls||'');t.textContent=msg;b.appendChild(t);setTimeout(()=>t.classList.add('out'),4200);setTimeout(()=>t.remove(),4800)}
function solTxt(v,signed){if(v==null||!isFinite(+v))return'—';let n=+v;if(Math.abs(n)<5e-7)n=0;const a=Math.abs(n);const d=a===0||a>=100?2:a>=1?3:a>=0.01?4:6;const body=a.toFixed(d);
  if(signed)return(n>0?'+':n<0?'-':'')+body+' SOL';return(n<0?'-':'')+body+' SOL'}
function clsPn(v){return v==null?'':(+v>=0?'up':'dn')}
function fmtQty(v){if(v==null||!isFinite(+v))return'—';const n=+v,a=Math.abs(n);if(a>=1e9)return(n/1e9).toFixed(2)+'B';if(a>=1e6)return(n/1e6).toFixed(2)+'M';if(a>=1e3)return(n/1e3).toFixed(2)+'K';if(a>=1)return n.toFixed(2);return n.toPrecision(3)}
function when(ts){if(!ts)return'—';try{return new Date(ts*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}catch(e){return'—'}}
function settings(){return(PB.acct&&PB.acct.settings)||{slippage_bps:150,priority_fee_sol:0.001,platform_fee_bps:100,exec_delay_ms:700}}
function pxTxt(v){try{return fmtPx(v)}catch(e){return v==null||!isFinite(+v)?'—':String(v)}}
function posOf(mint){return PB.acct&&PB.acct.positions&&PB.acct.positions[mint]}
function rowOf(mint){return((PB.summary&&PB.summary.positions)||[]).find(r=>r.mint===mint)}
function livePx(mint){const q=PB.quotes[mint];if(q&&q.px&&Date.now()/1000-q.t<45)return q.px;
  if(PB.token&&PB.token.mint===mint&&PB.token.market&&PB.token.market.px)return+PB.token.market.px;
  if(PB.chartMint===mint&&typeof CH!=='undefined'&&CH.lastPx)return CH.lastPx;return q&&q.px||null}
function bookVisible(){const s=document.getElementById('v-book');return !!(s&&!s.classList.contains('hidden'))}
function saveLocal(){if(PB.acct)write(key(),PB.acct)}
function fillMsg(f){if(!f)return'Paper book updated.';
  if(f.side==='deposit')return'Deposited '+solTxt(f.sol)+' (play money).';
  if(f.side==='reset')return'Paper book reset.';
  if(f.side==='settings')return'Paper settings saved.';
  if(f.side==='cancel')return'Limit cancelled. Reserved paper funds are back in cash.';
  if(f.side==='limit')return'Limit placed. It fills only if the live price crosses it.';
  if(f.side==='buy')return'Paper buy $'+(f.symbol||'')+' · spent '+solTxt(f.sol)+' · fee '+solTxt(f.fee_sol);
  if(f.side==='sell'){const r=f.realized_sol||0;return'Paper sell $'+(f.symbol||'')+' · '+(r>=0?'+':'')+solTxt(r)+' realised';}
  return'Paper book updated.'}

async function refreshCsrf(){
  try{const d=await (await fetch('/api/auth/status',{credentials:'same-origin'})).json();if(d.csrf)PB.csrf=d.csrf;return d}catch(e){return null}}
async function act(payload,opts,retried){
  opts=opts||{};
  const body=Object.assign({},payload);
  if(!body.rid)body.rid='r'+Math.random().toString(36).slice(2)+Date.now().toString(36);
  if(body.action==='adopt')body.account=payload.account;
  else if(!PB.signed)body.account=PB.acct;
  if(body.sol_usd==null&&PB.sol)body.sol_usd=PB.sol;
  if(!PB.csrf)await refreshCsrf();
  const r=await fetch('/api/paper/book/act',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':PB.csrf||''},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(r.status===403&&j.csrf_failed&&!retried){await refreshCsrf();return act(payload,opts,true)}
  if(!r.ok||j.ok===false){const msg=typeof j.detail==='string'?j.detail:(j.error||'Paper trade failed');
    if(!opts.quiet)toast(msg,'dn');throw new Error(msg)}
  if(j.sol_usd)PB.sol=j.sol_usd;
  if(!j.dry){PB.acct=j.account;PB.summary=j.summary;saveLocal();
    paintBar();paintChart();updatePanelFigures();if(bookVisible())fillBook();
    if(!opts.silent){const rows=j.fills&&j.fills.length?j.fills:(j.fill?[j.fill]:[]);
      rows.forEach(f=>{if(!f||!f.side||f.side==='mark')return;const up=f.side==='sell'?(f.realized_sol||0)>=0:f.side!=='cancel';toast(fillMsg(f),f.side==='sell'?(up?'up':'dn'):'')})}}
  return j}

function barFields(){return read('xs_bar_fields')||{cash:1,invested:1,u:1,r:1}}
function paintBar(){
  const bar=document.getElementById('paperBar');if(!bar)return;bar.hidden=false;
  const a=PB.acct,s=PB.summary,f=barFields();
  const cash=a?a.cash_sol:0;
  const invested=a?Object.values(a.positions||{}).reduce((n,p)=>n+(p.cost_sol||0),0):0;
  const u=s&&s.unrealized_known_sol, r=a?a.realized_sol:0;
  const show=(id,on,text,cls)=>{const el=document.getElementById(id);if(!el)return;el.hidden=!on;el.className=cls||'';el.textContent=text};
  show('pbBarCash',f.cash!==0,a?solTxt(cash):'Paper');
  show('pbBarInv',!!f.invested,'invested '+solTxt(invested));
  show('pbBarU',f.u!==0,'uPnL '+(u==null?'—':solTxt(u,true)),clsPn(u));
  show('pbBarR',f.r!==0,'rPnL '+solTxt(r||0,true),clsPn(r));
  const hint=document.getElementById('pbBarHint');
  if(hint)hint.textContent=((a&&a.mode)==='hard'?'hard mode · ':'')+(PB.signed?'on your account · ':'in this browser · ')+'paper · not advice'}

function buyAmounts(){const a=settings().buy_amounts;return (a&&a.length?a:[0.1,0.25,0.5,1]).slice(0,4)}
function sellPcts(){const a=settings().sell_pcts;return (a&&a.length?a:[10,25,50,100]).slice(0,4)}
function feeBundle(){const s=settings();return {slippage_bps:s.slippage_bps,sell_slippage_bps:s.sell_slippage_bps||s.slippage_bps,priority_fee_sol:s.priority_fee_sol,bribe_sol:s.bribe_sol||0,platform_fee_bps:s.platform_fee_bps}}
function ensurePanel(){
  const host=document.querySelector('#tokBody .tk-chart')||document.querySelector('#tokBody .tk-side');if(!host)return null;
  let el=document.getElementById('tkPaper');
  if(!el){el=document.createElement('div');el.id='tkPaper';el.className='it-panel';host.appendChild(el)}
  else if(!el.classList.contains('floating')&&el.parentElement!==host)host.appendChild(el);
  const pos=read('xs_it_pos');
  if(pos&&pos.float){el.classList.add('floating');el.style.left=pos.x+'px';el.style.top=pos.y+'px'}
  return el}

function panelShell(){
  return `<div class="pb it"><div class="it-drag" id="itDrag"><span class="pb-tag">PAPER</span><b>Instant</b><button type="button" class="it-pen" id="itDock" title="Dock on the chart">Dock</button></div>
    <div class="it-ps" id="itPs"></div>
    <div class="pb-tabs" role="tablist"><button type="button" data-pbt="buy">Buy</button><button type="button" data-pbt="sell">Sell</button><button type="button" data-pbt="limit">Limit</button></div>
    <div id="pbForm"></div>
    <p id="itLine" class="it-line"></p>
    <div id="itEditBox" hidden></div>
    <div class="it-pos"><div><b id="pbPos">No paper position</b><div id="pbUpnl"></div></div><div class="it-cash"><span id="pbCash">—</span><span id="pbPx">—</span></div></div>
    <div class="it-tpsl"><input id="pbTp" type="number" step="any" placeholder="Take profit"><input id="pbSl" type="number" step="any" placeholder="Stop"><button type="button" id="pbTpGo">Set</button></div>
    <p id="pbEst" class="pb-est"></p><p id="pbErr" class="pb-err"></p>
    <div id="pbMine" class="pb-orders"></div>
    <p class="pb-foot"><button type="button" class="linkbtn" id="itMode">Practice</button><button type="button" class="linkbtn" id="pbFaucet" hidden>Claim 1 SOL</button>
      <button type="button" class="linkbtn" id="pbOpenBook">Portfolio</button><button type="button" class="linkbtn" id="pbCard">Card</button></p>
    <div id="pbDepBox"><input id="pbDep" data-depinput type="number" min="0" step="any" placeholder="Deposit play SOL"><button type="button" id="pbDepGo">Add</button><button type="button" class="linkbtn" id="pbReset">Reset</button></div>
    <p class="pb-note">Paper trading. Not financial advice. One click fills after a short delay at the live price. No wallet, no real order.</p></div>`}

function formHTML(){
  const t=PB.tab;
  if(t==='sell'){const pcts=sellPcts();
    const grid=PB.edit?pcts.map((n,i)=>`<input data-spct="${i}" type="number" min="1" max="100" value="${n}">`).join('')
      :pcts.map(n=>`<button type="button" class="it-sell" data-pct="${n}" data-go="1">${n}%</button>`).join('');
    return `<div class="it-grid">${grid}</div><div class="it-custom"><input id="pbPct" type="number" min="1" max="100" placeholder="Custom %"><button type="button" class="pb-sell" id="pbSell">Sell</button></div>`;}
  if(t==='limit')return `<div class="pb-tabs"><button type="button" data-limside="buy">Buy limit</button><button type="button" data-limside="sell">Sell limit</button></div>
    <label>Trigger USD<input id="pbLim" type="number" min="0" step="any"></label><div id="pbLimAmt"></div>
    <button type="button" class="pb-limit" id="pbLimGo">Place limit</button>`;
  const amts=buyAmounts();
  const grid=PB.edit?amts.map((n,i)=>`<input data-amt="${i}" type="number" min="0" step="any" value="${n}">`).join('')
    :amts.map(n=>`<button type="button" class="it-buy" data-sol="${n}">${n}</button>`).join('');
  return `<div class="it-grid">${grid}</div><div class="it-custom"><input id="pbSol" type="number" min="0" step="any" placeholder="Custom SOL"><button type="button" class="pb-buy" id="pbBuy">Buy</button></div>`}

function paintForm(){
  const box=document.getElementById('pbForm');if(!box)return;
  const host=document.getElementById('tkPaper');
  if(host&&host.dataset.tab===PB.tab&&box.childElementCount)return;
  if(host)host.dataset.tab=PB.tab;
  box.innerHTML=formHTML();
  document.querySelectorAll('#tkPaper [data-pbt]').forEach(b=>b.classList.toggle('on',b.dataset.pbt===PB.tab));
  if(PB.tab==='limit'){document.querySelectorAll('#tkPaper [data-limside]').forEach(b=>b.classList.toggle('on',b.dataset.limside===PB.limSide));
    const amt=document.getElementById('pbLimAmt');
    if(amt)amt.innerHTML=PB.limSide==='sell'?`<label>% of free tokens<input id="pbLimPct" type="number" min="1" max="100" step="1" value="100"></label>`
      :`<label>Paper SOL to reserve<input id="pbLimSol" type="number" min="0" step="any" placeholder="0.5"></label>`;
    const lim=document.getElementById('pbLim');
    const px=PB.token&&livePx(PB.token.mint);
    if(lim&&px&&!lim.value)lim.value=String(px)}
  paintPresetRow();wireDrag()}
function paintPresetRow(){
  const row=document.getElementById('itPs');if(!row)return;
  const s=settings();const names=['P1','P2','P3'];
  const cur=s.active_p||'P1';
  row.innerHTML=names.map(n=>`<button type="button" data-pset="${n}" class="${n===cur?'on':''}">${n}</button>`).join('')+'<button type="button" class="it-pen" id="itEdit">✎</button>';
  const box=document.getElementById('itEditBox');
  const pack=(s.bundles&&s.bundles[cur])||{slippage_bps:s.slippage_bps,sell_slippage_bps:s.slippage_bps,priority_fee_sol:s.priority_fee_sol,bribe_sol:s.bribe_sol||0};
  if(box){box.hidden=!PB.edit;box.innerHTML=PB.edit?`<label>Buy slip %<input id="itBuySlip" type="number" step="0.1" value="${(pack.slippage_bps||0)/100}"></label><label>Sell slip %<input id="itSellSlip" type="number" step="0.1" value="${((pack.sell_slippage_bps??pack.slippage_bps)||0)/100}"></label><label>Priority<input id="itPrio" type="number" step="0.0001" value="${pack.priority_fee_sol||0}"></label><label>Tip<input id="itTip" type="number" step="0.0001" value="${pack.bribe_sol||0}"></label><label>Delay ms<input id="itDelay" type="number" step="50" value="${s.exec_delay_ms||0}"></label><button type="button" id="itSave">Save ${cur}</button>`:''}
  const line=document.getElementById('itLine');
  if(line){const slip=PB.tab==='sell'?(pack.sell_slippage_bps??pack.slippage_bps):pack.slippage_bps;
    line.textContent=`${((slip||0)/100).toFixed(1)}% · prio ${pack.priority_fee_sol||0} · tip ${pack.bribe_sol||0} · ${s.exec_delay_ms||0}ms`}
  const modeBtn=document.getElementById('itMode');
  if(modeBtn)modeBtn.textContent=(PB.acct&&PB.acct.mode)==='hard'?'Hard mode':'Practice';
  const dep=document.getElementById('pbDepBox');if(dep)dep.hidden=(PB.acct&&PB.acct.mode)==='hard';
  const faucet=document.getElementById('pbFaucet');if(faucet)faucet.hidden=(PB.acct&&PB.acct.mode)!=='hard';
}
function wireDrag(){
  const el=document.getElementById('tkPaper'),h=document.getElementById('itDrag');if(!el||!h||h.dataset.wired)return;h.dataset.wired='1';
  h.addEventListener('pointerdown',ev=>{if(ev.target.closest('button'))return;const r=el.getBoundingClientRect();const dx=ev.clientX-r.left,dy=ev.clientY-r.top;
    el.classList.add('floating');
    const move=e=>{el.style.left=Math.max(4,e.clientX-dx)+'px';el.style.top=Math.max(4,e.clientY-dy)+'px'};
    const up=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',up);write('xs_it_pos',{float:true,x:parseInt(el.style.left,10)||8,y:parseInt(el.style.top,10)||80})};
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',up)});
}

function updatePanelFigures(){
  const cash=document.getElementById('pbCash');if(!cash)return;
  const a=PB.acct,mint=PB.token&&PB.token.mint,px=mint?livePx(mint):null;
  cash.textContent=a?solTxt(a.cash_sol):'—';
  const solEl=document.getElementById('pbSolUsd');if(solEl)solEl.textContent=PB.sol?'$'+PB.sol.toFixed(2):'waiting';
  const pxEl=document.getElementById('pbPx');if(pxEl)pxEl.textContent=px?pxTxt(px):'—';
  const pos=mint&&posOf(mint),row=mint&&rowOf(mint);
  const posEl=document.getElementById('pbPos'),up=document.getElementById('pbUpnl');
  if(posEl)posEl.textContent=pos?fmtQty(pos.qty)+' · avg '+pxTxt(pos.avg_px_usd):'no position';
  if(up){if(row&&row.upnl_sol!=null){up.className=clsPn(row.upnl_sol);up.textContent=solTxt(row.upnl_sol,true)+(row.upnl_pct!=null?' ('+(row.upnl_pct>=0?'+':'')+row.upnl_pct.toFixed(1)+'%)':'')}
    else if(pos&&pos.reserved_qty>0){up.className='';up.textContent=fmtQty(pos.reserved_qty)+' reserved by limits'}else up.textContent=''}
  const buy=document.getElementById('pbBuy'),sell=document.getElementById('pbSell'),lim=document.getElementById('pbLimGo');
  const wait=!px||!PB.sol;
  if(buy){buy.disabled=PB.busy||wait;buy.textContent=PB.busy?'Filling…':'Buy'}
  if(sell){sell.disabled=PB.busy||wait||!(pos&&pos.qty>pos.reserved_qty);sell.textContent=PB.busy?'Filling…':'Sell'}
  if(lim)lim.disabled=PB.busy||!PB.sol||(PB.limSide==='sell'&&!(pos&&pos.qty>pos.reserved_qty));
  const mine=document.getElementById('pbMine');
  if(mine&&PB.acct){const orders=(PB.acct.orders||[]).filter(o=>!mint||o.mint===mint);
    mine.innerHTML=orders.length?orders.map(o=>`<div class="pb-ord"><span>${o.side==='buy'?'Buy':'Sell'} ${esc(o.symbol||'')} @ ${pxTxt(o.limit_px)}<br><small class="muted">${o.side==='buy'?solTxt(o.sol)+' reserved':fmtQty(o.qty)+' reserved'}</small></span><button type="button" class="linkbtn" data-cancel="${esc(o.id)}">Cancel</button></div>`).join(''):''}
  document.querySelectorAll('#tkPaper [data-pbt]').forEach(b=>b.classList.toggle('on',b.dataset.pbt===PB.tab))}

function meta(){const d=PB.token||{};return{symbol:d.symbol||'',name:d.name||'',pool:d.pool||''}}

async function runEst(){
  const est=document.getElementById('pbEst');if(!est||!PB.token||PB.busy)return;
  const px=livePx(PB.token.mint),sol=PB.sol;
  if(!px||!sol){est.textContent=!sol?'Waiting for SOL/USD from the Pulse cache (no extra price call from the paper book).':'Waiting for a token price on this page.';return}
  const base=Object.assign({mint:PB.token.mint,px_usd:px,sol_usd:sol,dry:true},meta());
  try{
    if(PB.tab==='buy'){const spend=+(document.getElementById('pbSol')||{}).value;if(!spend){est.textContent='Enter paper SOL to preview the fill.';return}
      const j=await act(Object.assign({action:'buy',sol:spend},base),{silent:true,quiet:true});const f=j.fill;
      est.textContent=`About ${fmtQty(f.qty)} tokens. Fill ${fmtPx(f.fill_px_usd)} after slippage (spot ${fmtPx(f.px_usd)}). Fee ${solTxt(f.fee_sol)}.`}
    else if(PB.tab==='sell'){const pct=+(document.getElementById('pbPct')||{}).value||100;
      const j=await act(Object.assign({action:'sell',pct},base),{silent:true,quiet:true});const f=j.fill;
      const r=f.realized_sol||0;est.textContent=`About ${solTxt(f.sol)} back. Fee ${solTxt(f.fee_sol)}. Realised ${solTxt(r,true)} (${f.pnl_pct==null?'—':(f.pnl_pct>=0?'+':'')+f.pnl_pct.toFixed(1)+'%'}).`}
    else est.textContent=PB.limSide==='buy'?'Reserves the SOL until the live price is at or under your trigger, then fills with slippage and fees.':'Reserves the tokens until the live price is at or above your trigger.';
  }catch(e){est.textContent=e.message}}

async function market(payload){
  if(PB.busy)return;const gen=++PB.seq;PB.busy=true;updatePanelFigures();
  const delay=+settings().exec_delay_ms||0;
  const est=document.getElementById('pbEst');if(est)est.textContent=delay?`Simulating a ${delay} ms fill…`:'Filling…';
  await sleep(delay);
  if(gen!==PB.seq)return;
  const px=livePx(payload.mint);if(px)payload.px_usd=px;payload.waited_ms=delay;payload.sol_usd=PB.sol;payload.fees=feeBundle();
  try{await act(payload)}finally{if(gen===PB.seq){PB.busy=false;updatePanelFigures();scheduleEst()}}}

async function doDeposit(input){
  const sol=input&&+input.value;if(!sol||sol<=0){toast('Enter an amount of paper SOL.','dn');return}
  await act({action:'deposit',sol});if(input)input.value=''}

async function doReset(){
  if(!confirm('Reset this paper book? Play SOL, positions, limits and history will be cleared. Nothing real is affected.'))return;
  PB.seq++;PB.busy=false;await act({action:'reset'})}

async function saveBundle(){
  const s=settings();const id=s.active_p||'P1';
  const bundles=Object.assign({},s.bundles||{});
  const cur=Object.assign({},bundles[id]||{});
  const num=el=>el&&el.value!==''?+el.value:null;
  const b=num(document.getElementById('itBuySlip')),se=num(document.getElementById('itSellSlip'));
  const pri=num(document.getElementById('itPrio')),tip=num(document.getElementById('itTip')),delay=num(document.getElementById('itDelay'));
  if(b!=null)cur.slippage_bps=Math.round(b*100);
  if(se!=null)cur.sell_slippage_bps=Math.round(se*100);
  if(pri!=null)cur.priority_fee_sol=pri;if(tip!=null)cur.bribe_sol=tip;
  bundles[id]=cur;
  const amounts=[...document.querySelectorAll('#tkPaper [data-amt]')].map(i=>+i.value).filter(n=>n>0);
  const pcts=[...document.querySelectorAll('#tkPaper [data-spct]')].map(i=>+i.value).filter(n=>n>0&&n<=100);
  const patch={bundles,active_p:id,slippage_bps:cur.slippage_bps,priority_fee_sol:cur.priority_fee_sol,bribe_sol:cur.bribe_sol};
  if(cur.sell_slippage_bps!=null)patch.sell_slippage_bps=cur.sell_slippage_bps;
  if(delay!=null)patch.exec_delay_ms=delay;
  if(amounts.length)patch.buy_amounts=amounts;if(pcts.length)patch.sell_pcts=pcts;
  await act({action:'settings',settings:patch});
  PB.edit=false;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm()}

function ensureBook(){
  const root=document.getElementById('bookApp');if(!root||root.dataset.ready)return;
  root.dataset.ready='1';
  root.innerHTML=`<div id="bkOffer"></div><div class="pb-kpis" id="bkKpis"></div>
    <div class="grid2"><div class="card"><h3>Paper SOL</h3>
      <p class="muted">A deposit is play money. It does not come from a wallet.</p>
      <div class="pb-row"><input id="bkDep" data-depinput type="number" min="0" step="any" placeholder="Amount" aria-label="Paper SOL to deposit"><button type="button" class="primary" id="bkDepGo">Deposit</button></div>
      <div class="pb-presets"><button type="button" data-dep="5">5</button><button type="button" data-dep="10">10</button><button type="button" data-dep="25">25</button><button type="button" data-dep="100">100</button></div>
      <p id="bkStore" class="muted"></p><button type="button" class="linkbtn" id="bkReset">Reset book</button>
    </div><div class="card"><h3>Record</h3><div id="bkStats"></div></div></div>
    <div class="card"><h3>Open positions</h3><div id="bkPos"></div></div>
    <div class="card"><h3>Limit orders</h3><div id="bkOrd"></div></div>
    <div class="card"><h3>Daily realised PnL</h3><div id="bkCal"></div><p class="muted" id="bkCalNote"></p></div>
    <div class="card"><h3>Trades</h3><div id="bkHist"></div></div>`}

function kpi(l,v,c){return `<div class="pb-kpi"><span>${l}</span><b class="${c||''}">${v}</b></div>`}
function mytDate(off){const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit'});
  const today=fmt.format(new Date());const [y,m,d]=today.split('-').map(Number);const dt=new Date(Date.UTC(y,m-1,d)+off*86400000);
  return dt.getUTCFullYear()+'-'+String(dt.getUTCMonth()+1).padStart(2,'0')+'-'+String(dt.getUTCDate()).padStart(2,'0')}
function weekday(ds){const [y,m,d]=ds.split('-').map(Number);return new Date(Date.UTC(y,m-1,d)).getUTCDay()}

function fillBook(){
  ensureBook();if(!document.getElementById('bkKpis'))return;
  const a=PB.acct||{},s=PB.summary||{},u=s.unrealized_known_sol;
  document.getElementById('bkKpis').innerHTML=kpi('Cash',solTxt(a.cash_sol||0))+kpi('Reserved',solTxt(a.reserved_sol||0))+
    kpi('Equity',s.equity_sol==null?(s.equity_known_sol!=null?solTxt(s.equity_known_sol):'—'):solTxt(s.equity_sol))+
    kpi('Unrealised',u==null?'—':solTxt(u,true),clsPn(u))+kpi('Realised',solTxt(a.realized_sol||0,true),clsPn(a.realized_sol))+
    kpi('Win rate',s.win_rate==null?'—':Math.round(s.win_rate*100)+'%');
  const offer=document.getElementById('bkOffer');
  if(offer)offer.innerHTML=PB.offerAnon?`<div class="disc pb-offer"><span>This browser has a paper book from before you signed in.</span><button type="button" class="primary" id="bkImport">Keep it on this account</button></div>`:'';
  const store=document.getElementById('bkStore');
  if(store)store.textContent=PB.signed?'Signed in: this book is stored in your account (the same database as your watchlist). A copy also stays in this browser. Sign in on another device to get it back.'
    :'Signed out: this book stays in this browser only (localStorage). Sign in to keep it with your account.';
  const wr=s.wins||0,ls=s.losses||0;
  const best=s.best,worst=s.worst;
  document.getElementById('bkStats').innerHTML=`<p>${wr} wins · ${ls} losses${s.scratches?' · '+s.scratches+' flat':''}. ${s.n_trades||(a.trades||[]).length} fills kept in the history.</p>
    <p class="muted">Best ${best?esc(best.symbol)+' '+solTxt(best.realized_sol,true):'—'} · Worst ${worst?esc(worst.symbol)+' '+solTxt(worst.realized_sol,true):'—'}</p>
    <p class="muted">Deposited ${solTxt(a.deposited_sol||0)} in total. Resets: ${a.resets||0}. Mark-to-market uses the live price before exit fees, so a brand-new buy is down by the fee and the slippage.</p>`;
  const rows=s.positions||[];
  const pos=Object.values(a.positions||{});
  document.getElementById('bkPos').innerHTML=pos.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>Token</th><th>Qty</th><th>Avg entry</th><th>Mark</th><th>uPnL</th><th></th></tr></thead><tbody>`+
    pos.map(p=>{const r=rows.find(x=>x.mint===p.mint)||{};const upnl=r.upnl_sol;
      return `<tr><td><a href="/token/${esc(p.mint)}" data-tok="${esc(p.mint)}"><b>$${esc(p.symbol||'?')}</b></a></td><td>${fmtQty(p.qty)}${p.reserved_qty?` <small class="muted">${fmtQty(p.reserved_qty)} lim</small>`:''}</td><td>${pxTxt(p.avg_px_usd)}</td><td>${r.mark_px?pxTxt(r.mark_px):'—'}</td><td class="${clsPn(upnl)}">${upnl==null?'—':solTxt(upnl,true)+(r.upnl_pct!=null?' '+((r.upnl_pct>=0?'+':'')+r.upnl_pct.toFixed(1)+'%'):'')}</td>
      <td class="pb-mini"><button type="button" data-sellnow="${esc(p.mint)}" data-pct="25">25%</button><button type="button" data-sellnow="${esc(p.mint)}" data-pct="50">50%</button><button type="button" data-sellnow="${esc(p.mint)}" data-pct="100">100%</button><button type="button" class="linkbtn" data-sharepos="${esc(p.mint)}">Card</button></td></tr>`}).join('')+`</tbody></table></div>`
    :'<p class="muted">No open paper positions. Open a token and use the Paper trade panel.</p>';
  const orders=a.orders||[];
  document.getElementById('bkOrd').innerHTML=orders.length?orders.map(o=>`<div class="pb-ord"><span><a href="/token/${esc(o.mint)}" data-tok="${esc(o.mint)}">$${esc(o.symbol||'?')}</a> ${o.side} @ ${pxTxt(o.limit_px)} · ${o.side==='buy'?solTxt(o.sol):fmtQty(o.qty)} · ${when(o.created_at)}${o.last_error?`<br><small class="dn">${esc(o.last_error)}</small>`:''}</span><button type="button" class="linkbtn" data-cancel="${esc(o.id)}">Cancel</button></div>`).join('')
    :'<p class="muted">No resting limits.</p>';
  const cal=calendar(a.daily||{});
  document.getElementById('bkCal').innerHTML=cal.html;
  document.getElementById('bkCalNote').textContent=`Realised paper SOL by day (Asia/Kuala_Lumpur). This window: ${solTxt(cal.sum,true)} across ${cal.n} days with a close. Unrealised PnL is not on the calendar.`;
  const trades=(a.trades||[]).slice().reverse().slice(0,80);
  document.getElementById('bkHist').innerHTML=trades.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>When</th><th>Side</th><th>Token</th><th>SOL</th><th>Fill</th><th>Fee</th><th>Realised</th><th></th></tr></thead><tbody>`+
    trades.map(t=>`<tr><td>${when(t.ts)}</td><td class="${t.side==='buy'?'up':'dn'}">${esc(t.side)}${t.kind==='limit'?' lim':''}</td><td><a href="/token/${esc(t.mint)}" data-tok="${esc(t.mint)}">$${esc(t.symbol||'?')}</a></td><td>${solTxt(t.sol)}</td><td>${pxTxt(t.fill_px_usd)}</td><td>${solTxt(t.fee_sol)}</td><td class="${clsPn(t.realized_sol)}">${t.realized_sol==null?'—':solTxt(t.realized_sol,true)}</td><td>${t.side==='sell'?`<button type="button" class="linkbtn" data-share="${esc(t.id)}">Card</button>`:''}</td></tr>`).join('')+'</tbody></table></div>'
    :'<p class="muted">No paper trades yet.</p>'}

function calendar(daily){
  const today=mytDate(0);const sinceMon=(weekday(today)+6)%7;const start=-(sinceMon+15*7);
  const cells=16*7;const vals=[];let sum=0,n=0;
  for(let i=0;i<cells;i++){const v=daily[mytDate(start+i)];if(v){sum+=+v;n++;vals.push(Math.abs(+v))}}
  const max=Math.max(0.01,...vals,0.01);
  let html='<div class="pb-calhead">'+['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d=>`<span>${d}</span>`).join('')+'</div><div class="pb-cal">';
  for(let i=0;i<cells;i++){const off=start+i,key=mytDate(off),v=Object.prototype.hasOwnProperty.call(daily,key)?+daily[key]:null;
    let cls='empty',style='';
    if(off>0)cls='future';
    else if(v!=null&&v!==0){cls=v>0?'up':'dn';style=` style="opacity:${(0.28+0.72*Math.min(1,Math.abs(v)/max)).toFixed(2)}"`}
    else if(v===0)cls='flat';
    html+=`<i class="${cls}"${style} title="${key}${v!=null?' · '+solTxt(v,true):' · no close'}"></i>`}
  return {html:html+'</div>',sum,n}}

function drawCard(opts){
  const c=document.createElement('canvas');c.width=1200;c.height=675;const g=c.getContext('2d');if(!g)return c;
  const bg=opts.bg||'night';
  if(bg==='up'){g.fillStyle='#07140c';g.fillRect(0,0,1200,675);g.fillStyle='rgba(0,199,3,.18)';g.fillRect(0,0,1200,675)}
  else if(bg==='down'){g.fillStyle='#14080a';g.fillRect(0,0,1200,675);g.fillStyle='rgba(230,101,101,.16)';g.fillRect(0,0,1200,675)}
  else if(bg==='image'&&opts.img){g.drawImage(opts.img,0,0,1200,675);g.fillStyle='rgba(0,0,0,.45)';g.fillRect(0,0,1200,675)}
  else {g.fillStyle='#0b0c0e';g.fillRect(0,0,1200,675)}
  for(let x=0;x<1200;x+=28)for(let y=0;y<675;y+=28){g.fillStyle='rgba(255,255,255,.025)';g.fillRect(x,y,2,2)}
  const up=opts.pnlPct==null?true:opts.pnlPct>=0;
  g.fillStyle=up?'#00c703':'#e66565';g.fillRect(0,0,14,675);
  g.fillStyle='#9aa0a6';g.font='500 28px Geist, system-ui, sans-serif';g.fillText('xScanner   ·   paper trade',64,78);
  g.strokeStyle=up?'#00c703':'#e66565';g.lineWidth=2;g.strokeRect(900,48,240,78);
  g.fillStyle=up?'#00c703':'#e66565';g.font='700 30px Geist, system-ui, sans-serif';g.fillText('PAPER',962,98);
  g.fillStyle='#f2f3f5';g.font='600 54px Geist, system-ui, sans-serif';g.fillText('$'+(opts.symbol||'?'),64,170);
  g.font='600 132px Geist, system-ui, sans-serif';g.fillStyle=up?'#00c703':'#e66565';
  const pct=opts.pnlPct==null?'—':(opts.pnlPct>=0?'+':'')+Number(opts.pnlPct).toFixed(1)+'%';
  g.fillText(pct,64,340);
  g.font='500 36px Geist, system-ui, sans-serif';g.fillStyle='#e6e8eb';
  const line=opts.pnlSol==null?(opts.spent!=null?solTxt(opts.spent)+' spent':''):(opts.label||'PnL')+'  '+solTxt(opts.pnlSol,true);
  g.fillText(line,64,410);
  g.font='400 28px Geist, system-ui, sans-serif';g.fillStyle='#9aa0a6';
  const entry=pxTxt(opts.entry),mark=pxTxt(opts.mark);
  g.fillText('Entry  '+entry+'      Mark  '+mark,64,490);
  g.font='400 24px Geist, system-ui, sans-serif';
  const who=opts.who||PB.name||PB.user||'xScanner';
  g.beginPath();g.arc(80,548,22,0,Math.PI*2);g.fillStyle='#e6c35c';g.fill();g.fillStyle='#111';g.font='700 22px Geist, system-ui, sans-serif';g.fillText(String(who).slice(0,1).toUpperCase(),72,556);
  g.fillStyle='#d5d8de';g.font='500 22px Geist, system-ui, sans-serif';g.fillText(who,116,556);
  g.fillStyle='#9aa0a6';g.font='400 20px Geist, system-ui, sans-serif';
  g.fillText('Paper trading. Not financial advice. No wallet and no real order.',64,610);
  return c}

function openShare(opts){
  opts=Object.assign({bg:PB.cardBg||'night',img:PB.cardImg||null,who:(document.getElementById('pbWho')||{}).value||PB.name||'xScanner'},opts);
  PB.cardOpts=opts;PB.cardBg=opts.bg;
  const c=drawCard(opts);
  let ov=document.getElementById('pbShare');
  if(!ov){ov=document.createElement('div');ov.id='pbShare';ov.innerHTML='<div class="pb-sharebox" role="dialog" aria-label="Paper PnL card"><img alt="Paper trading PnL card"><div class="pb-sharerow"><button type="button" data-bg="night">Night</button><button type="button" data-bg="up">Green</button><button type="button" data-bg="down">Red</button><button type="button" data-bg="image">Image</button><input id="pbCardFile" type="file" accept="image/*" hidden></div><label class="pb-who">Name <input id="pbWho" maxlength="24"></label><div class="pb-sharerow"><button type="button" class="primary" id="pbShareDl">Download PNG</button><button type="button" class="ghost" id="pbShareCopy">Copy</button><button type="button" class="ghost" id="pbShareX">Close</button></div><p class="muted">Paper trading. Not financial advice.</p></div>';
    document.body.appendChild(ov);
    ov.addEventListener('click',e=>{if(e.target===ov||e.target.id==='pbShareX')ov.classList.remove('on')});
    ov.querySelector('#pbWho').addEventListener('change',()=>openShare(PB.cardOpts||{}));
    ov.querySelector('#pbCardFile').addEventListener('change',ev=>{const f=ev.target.files&&ev.target.files[0];if(!f)return;const img=new Image();img.onload=()=>{PB.cardImg=img;openShare(Object.assign({},PB.cardOpts,{bg:'image',img}))};img.src=URL.createObjectURL(f)})}
  const who=ov.querySelector('#pbWho');if(who&&document.activeElement!==who)who.value=opts.who||'';
  c.toBlob(async blob=>{if(!blob){toast('Could not draw the card.','dn');return}
    if(PB.shareUrl)URL.revokeObjectURL(PB.shareUrl);PB.shareUrl=URL.createObjectURL(blob);PB.shareBlob=blob;
    ov.querySelector('img').src=PB.shareUrl;ov.classList.add('on');
    document.getElementById('pbShareDl').onclick=()=>{const a=document.createElement('a');a.href=PB.shareUrl;a.download='xscanner-paper-'+(opts.symbol||'pnl').replace(/[^A-Za-z0-9]+/g,'')+'.png';a.click()};
    document.getElementById('pbShareCopy').onclick=async()=>{try{await navigator.clipboard.write([new ClipboardItem({'image/png':PB.shareBlob})]);toast('Card copied.')}catch(e){toast('Copy is blocked. Download the PNG instead.','dn')}}})}

function sharePosition(mint){const p=posOf(mint),r=rowOf(mint);if(!p)return;
  openShare({symbol:p.symbol,pnlPct:r&&r.upnl_pct,pnlSol:r&&r.upnl_sol,entry:p.avg_px_usd,mark:r&&r.mark_px,label:'Unrealised'})}
function shareTrade(id){const t=((PB.acct&&PB.acct.trades)||[]).find(x=>x.id===id);if(!t)return;
  openShare({symbol:t.symbol,pnlPct:t.pnl_pct,pnlSol:t.realized_sol,entry:t.avg_px_usd,mark:t.fill_px_usd,label:t.side==='sell'?'Realised':'Fill',spent:t.side==='buy'?t.sol:null})}

let estT=0;
function scheduleEst(){clearTimeout(estT);estT=setTimeout(()=>{runEst().catch(()=>{})},280)}

async function onClick(e){
  if(e.target.id==='pbBarGear'){e.preventDefault();e.stopPropagation();const m=document.getElementById('pbBarMenu');if(!m)return;
    const f=barFields();m.hidden=!m.hidden;m.innerHTML=['cash','invested','u','r'].map(k=>`<label><input type="checkbox" data-bar="${k}" ${f[k]!==0?'checked':''}> ${{cash:'Balance',invested:'Invested',u:'Unrealised',r:'Realised'}[k]}</label>`).join('');
    return}
  const t=e.target.closest('button');if(!t)return;
  if(t.id==='paperBar'||t.id==='toMyPaper'||t.id==='pbOpenBook'){e.preventDefault();if(window.show)window.show('book');return}
  if(t.dataset.dep){const scope=t.closest('.pb,.card')||document;const input=scope.querySelector('[data-depinput]');if(input){input.value=t.dataset.dep;input.focus()}return}
  if(t.dataset.bg){if(t.dataset.bg==='image'){const f=document.getElementById('pbCardFile');if(f)f.click();return}openShare(Object.assign({},PB.cardOpts||{},{bg:t.dataset.bg,img:PB.cardImg}));return}
  if(t.dataset.qbuy){e.preventDefault();e.stopPropagation();const px=+t.dataset.qpx;if(!px){toast('No cached price on this row yet.','dn');return}
    await guard(()=>market({action:'buy',mint:t.dataset.qbuy,sol:buyAmounts()[0],px_usd:px,symbol:t.dataset.qsym||'',name:t.dataset.qname||''}));return}
  if(t.dataset.pset){const s=settings();const pack=(s.bundles||{})[t.dataset.pset];if(pack&&PB.acct){PB.acct.settings.active_p=t.dataset.pset;PB.acct.settings.slippage_bps=pack.slippage_bps;PB.acct.settings.sell_slippage_bps=pack.sell_slippage_bps;PB.acct.settings.priority_fee_sol=pack.priority_fee_sol;PB.acct.settings.bribe_sol=pack.bribe_sol;saveLocal();paintPresetRow();act({action:'settings',settings:{active_p:t.dataset.pset,slippage_bps:pack.slippage_bps,priority_fee_sol:pack.priority_fee_sol,bribe_sol:pack.bribe_sol}},{silent:true,quiet:true}).catch(()=>{})}return}
  if(t.id==='itEdit'){PB.edit=!PB.edit;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();return}
  if(t.id==='itSave'){await guard(saveBundle);return}
  if(t.id==='itDock'){write('xs_it_pos',{float:false});const el=document.getElementById('tkPaper');if(el){el.classList.remove('floating');el.style.left='';el.style.top=''}return}
  if(t.id==='itMode'){const next=(PB.acct&&PB.acct.mode)==='hard'?'practice':'hard';await guard(()=>act({action:'mode',mode:next}));return}
  if(t.id==='pbFaucet'){await guard(()=>act({action:'faucet'}));return}
  if(t.id==='pbTpGo'&&PB.token){await guard(()=>act(Object.assign({action:'tpsl',mint:PB.token.mint,tp_px:+document.getElementById('pbTp').value||null,sl_px:+document.getElementById('pbSl').value||null,pct:100},meta())));return}
  if(t.dataset.sol&&PB.token&&!PB.edit){await guard(()=>market(Object.assign({action:'buy',mint:PB.token.mint,sol:+t.dataset.sol,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.dataset.pbt){PB.tab=t.dataset.pbt;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.limside){PB.limSide=t.dataset.limside;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();return}
  if(t.dataset.pct&&t.dataset.go&&PB.token){const pct=+t.dataset.pct;await guard(()=>market(Object.assign({action:'sell',mint:PB.token.mint,pct,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbBuy'&&PB.token){const sol=+(document.getElementById('pbSol')||{}).value;await guard(()=>market(Object.assign({action:'buy',mint:PB.token.mint,sol,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbSell'&&PB.token){const pct=+(document.getElementById('pbPct')||{}).value;if(!pct){toast('Enter a percent, or use 25 / 50 / 100.','dn');return}await guard(()=>market(Object.assign({action:'sell',mint:PB.token.mint,pct,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbLimGo'&&PB.token){const limit=+(document.getElementById('pbLim')||{}).value;
    const payload=Object.assign({action:'limit',side:PB.limSide,mint:PB.token.mint,limit_px:limit},meta());
    if(PB.limSide==='buy')payload.sol=+(document.getElementById('pbLimSol')||{}).value;else payload.pct=+(document.getElementById('pbLimPct')||{}).value||100;
    await guard(()=>act(payload));return}
  if(t.id==='pbDepGo'){await guard(()=>doDeposit(document.getElementById('pbDep')));return}
  if(t.id==='bkDepGo'){await guard(()=>doDeposit(document.getElementById('bkDep')));return}
  if(t.id==='pbReset'||t.id==='bkReset'){await guard(doReset);return}
  if(t.id==='pbSetSave'){await guard(saveSettings);return}
  if(t.id==='pbCard'&&PB.token){sharePosition(PB.token.mint);return}
  if(t.dataset.cancel){await guard(()=>act({action:'cancel',id:t.dataset.cancel}));return}
  if(t.dataset.sellnow){const mint=t.dataset.sellnow,p=posOf(mint);await guard(()=>market({action:'sell',mint,pct:+t.dataset.pct,px_usd:livePx(mint),symbol:p&&p.symbol,name:p&&p.name,pool:p&&p.pool}));return}
  if(t.dataset.sharepos){sharePosition(t.dataset.sharepos);return}
  if(t.dataset.share){shareTrade(t.dataset.share);return}
  if(t.id==='bkImport'){await guard(importAnon)}}
async function guard(fn){try{await fn();const el=document.getElementById('pbErr');if(el)el.textContent=''}catch(e){const el=document.getElementById('pbErr');if(el)el.textContent=e.message}}

function onInput(e){if(e.target&&e.target.dataset&&e.target.dataset.bar){const f=barFields();f[e.target.dataset.bar]=e.target.checked?1:0;write('xs_bar_fields',f);paintBar();return}
  if(e.target&&(e.target.id==='pbSol'||e.target.id==='pbPct'||e.target.id==='pbLim'))scheduleEst()}

function snapTime(ts,candles){if(!candles||!candles.length)return null;let best=candles[0].t;for(const k of candles){if(k.t<=ts)best=k.t;else break}return best}
function paintChart(){
  if(typeof CH==='undefined'||!CH.cs||!CH.coin||!PB.acct)return;
  const mint=CH.coin.mint;if(!PB.candles||PB.chartMint!==mint)return;
  const grouped=new Map();
  for(const t of PB.acct.trades||[]){if(t.mint!==mint||(t.side!=='buy'&&t.side!=='sell'))continue;const tm=snapTime(t.ts,PB.candles);if(tm==null)continue;
    const g=grouped.get(tm)||{time:tm,buy:0,sell:0};g[t.side]++;grouped.set(tm,g)}
  const markers=[...grouped.values()].sort((a,b)=>a.time-b.time).map(g=>{
    const both=g.buy&&g.sell;
    return {time:g.time,position:both?'inBar':g.buy?'belowBar':'aboveBar',color:both?'#e6c35c':g.buy?'#00c703':'#e66565',size:2,
      shape:both?'circle':g.buy?'arrowUp':'arrowDown',
      text:(g.buy?('Buy'+(g.buy>1?' ×'+g.buy:'')):'')+(both?' · ':'')+(g.sell?('Sell'+(g.sell>1?' ×'+g.sell:'')):'')}})
  try{CH.cs.setMarkers(markers)}catch(e){}
  PB.byTime={};
  for(const t of PB.acct.trades||[]){if(t.mint!==mint)continue;const tm=snapTime(t.ts,PB.candles);if(tm==null)continue;
    const b=PB.byTime[tm]||(PB.byTime[tm]={n:0,buys:0,sells:0,realized:0,last:t});
    b.n++;if(t.side==='buy')b.buys++;else b.sells++;if(t.realized_sol)b.realized+=t.realized_sol;b.last=t}
  (PB.lines||[]).forEach(l=>{try{CH.cs.removePriceLine(l)}catch(e){}});PB.lines=[];
  const add=(price,color,title)=>{if(!(price>0))return;try{PB.lines.push(CH.cs.createPriceLine({price,color,lineWidth:1,lineStyle:2,axisLabelVisible:true,title}))}catch(e){}};
  const pos=posOf(mint);
  if(pos)add(pos.avg_px_usd,'#e6c35c','avg entry');
  const lastSell=[...(PB.acct.trades||[])].reverse().find(t=>t.mint===mint&&t.side==='sell');
  if(lastSell)add(lastSell.fill_px_usd,'#e66565','last exit');
  const oco=(PB.acct.orders||[]).find(o=>o.side==='oco'&&o.mint===mint);
  if(oco){add(oco.tp_px,'#00c703','take profit');add(oco.sl_px,'#ffb020','stop')}
  const cardEl=()=>document.getElementById('pbHover')||document.body.appendChild(Object.assign(document.createElement('div'),{id:'pbHover',className:'pb-hover'}));
  const showCard=(info,x,y)=>{const card=cardEl();if(!info){card.hidden=true;return}const t=info.last;card.hidden=false;
    card.innerHTML=`<b>${t.side==='buy'?'Buy':'Sell'} ${esc(t.symbol||'')}</b><div>${solTxt(t.sol)} · ${pxTxt(t.fill_px_usd)}</div><div class="${clsPn(info.realized)}">realised ${solTxt(info.realized,true)}</div><div class="muted">${info.n} on this candle · ${(PB.acct.trades||[]).filter(x=>x.mint===mint).length} on this token</div>`;
    card.style.left=Math.max(8,x+14)+'px';card.style.top=Math.max(8,y+14)+'px'};
  const near=(time)=>{if(time==null||!PB.byTime)return null;if(PB.byTime[time])return PB.byTime[time];let best=null,bd=1e15;for(const k of Object.keys(PB.byTime)){const d=Math.abs(+k-time);if(d<bd){bd=d;best=+k}}return bd<=1800?PB.byTime[best]:null};
  const host=document.getElementById('chNative');
  if(host&&host._pbChart!==CH.chart){host._pbChart=CH.chart;host.addEventListener('mousemove',ev=>{
    let time=null;try{const r=host.getBoundingClientRect();time=CH.chart.timeScale().coordinateToTime(ev.clientX-r.left)}catch(e){}
    showCard(near(time),ev.clientX,ev.clientY)});host.addEventListener('mouseleave',()=>{const c=document.getElementById('pbHover');if(c)c.hidden=true})}}

let lastMark=0;
async function maybeMark(force){
  if(PB.busy||PB.marking||!PB.acct)return;
  if(!force&&Date.now()-lastMark<8000)return;
  const mints=new Set([...Object.keys(PB.acct.positions||{}),...(PB.acct.orders||[]).map(o=>o.mint)]);
  if(!mints.size||document.hidden)return;
  const quotes={};const now=Date.now()/1000;const need=[];
  mints.forEach(m=>{const px=livePx(m);const q=PB.quotes[m];if(px&&q&&now-q.t<20)quotes[m]=px;else need.push(m)});
  if(need.length){try{const j=await (await fetch('/api/quotes?mints='+need.slice(0,30).map(encodeURIComponent).join(','))).json();
    for(const [m,q] of Object.entries(j.quotes||{}))if(q&&q.px){quotes[m]=q.px;PB.quotes[m]={px:q.px,t:now}}}catch(e){}}
  // A mint the token page is already showing does not need another upstream lookup.
  if(PB.token&&mints.has(PB.token.mint)&&PB.token.market&&PB.token.market.px&&quotes[PB.token.mint]==null)quotes[PB.token.mint]=+PB.token.market.px;
  if(!Object.keys(quotes).length||!PB.sol)return;
  PB.marking=true;lastMark=Date.now();
  try{await act({action:'mark',quotes,sol_usd:PB.sol},{quiet:true})}catch(e){}finally{PB.marking=false}}

async function pull(){
  try{const j=await (await fetch('/api/paper/book')).json();
    if(j.sol_usd)PB.sol=j.sol_usd;
    if(j.signed_in&&j.account&&!PB.busy&&(j.account.updated_at||0)>=((PB.acct&&PB.acct.updated_at)||0)){PB.acct=j.account;PB.summary=j.summary||PB.summary;saveLocal()}
    paintBar();updatePanelFigures();if(bookVisible())fillBook()}catch(e){}}

async function importAnon(){
  const anon=read('xs_paper_book_v1:anon');if(!anon){PB.offerAnon=false;fillBook();return}
  await act({action:'adopt',account:anon});
  try{localStorage.removeItem('xs_paper_book_v1:anon')}catch(e){}
  PB.offerAnon=false;toast('Moved this browser’s paper book onto your account.')}

window.paperShowBook=function(){document.documentElement.dataset.view='book';ensureBook();fillBook()};
window.paperOnToken=function(d){if(!d||!d.mint)return;PB.token=d;
  if(d.market&&d.market.px)PB.quotes[d.mint]={px:+d.market.px,t:Date.now()/1000};
  const el=ensurePanel();if(!el)return;
  if(PB.panelMint!==d.mint||!el.querySelector('.pb')){PB.panelMint=d.mint;PB.tab='buy';el.dataset.tab='';el.innerHTML=panelShell();paintForm()}
  updatePanelFigures();paintChart();
  if((PB.acct&&PB.acct.orders||[]).some(o=>o.mint===d.mint)){clearTimeout(PB.markSoon);PB.markSoon=setTimeout(()=>maybeMark(true),600)}};
window.paperLeaveToken=function(){PB.token=null;PB.entryLine=null};

document.addEventListener('click',e=>{onClick(e).catch(()=>{})});
document.addEventListener('input',onInput);
document.addEventListener('keydown',e=>{if(e.key!=='Enter')return;
  if(e.target.id==='pbSol'){e.preventDefault();const b=document.getElementById('pbBuy');if(b)b.click()}
  if(e.target.id==='pbPct'){e.preventDefault();const b=document.getElementById('pbSell');if(b)b.click()}
  if(e.target.id==='pbDep'){e.preventDefault();const b=document.getElementById('pbDepGo');if(b)b.click()}
  if(e.target.id==='bkDep'){e.preventDefault();const b=document.getElementById('bkDepGo');if(b)b.click()}});
document.addEventListener('xschart',e=>{const d=e.detail||{};PB.chartMint=d.mint;PB.candles=d.candles||[];if(d.last&&d.mint)PB.quotes[d.mint]={px:d.last,t:Date.now()/1000,src:'chart'};paintChart()});

async function boot(){
  await refreshCsrf();
  let state={};try{state=await (await fetch('/api/paper/book',{credentials:'same-origin'})).json()}catch(e){}
  PB.signed=!!state.signed_in;
  PB.user=state.user||null;
  PB.sol=state.sol_usd||null;
  const local=read(key());
  if(PB.signed&&state.account)PB.acct=state.account;
  else if(PB.signed&&!state.account&&used(local)){try{const j=await act({action:'adopt',account:local},{silent:true});PB.acct=j.account}catch(e){PB.acct=local}}
  else PB.acct=local||state.blank||null;
  const anon=PB.signed?read('xs_paper_book_v1:anon'):null;
  PB.offerAnon=!!(PB.signed&&used(anon)&&!state.account&&!used(local));
  if(state.summary)PB.summary=state.summary;
  saveLocal();paintBar();updatePanelFigures();paintChart();
  const bar=document.getElementById('paperBar');if(bar)bar.hidden=false;
  const jump=document.getElementById('toMyPaper');if(jump&&!jump.dataset.bound){jump.dataset.bound='1';jump.addEventListener('click',ev=>{ev.preventDefault();if(window.show)window.show('book')})}
  if(!PB._tick){PB._tick=setInterval(()=>{pull();maybeMark(false)},20000)}
  maybeMark(true)}

window.paperOnAuth=function(user){
  const name=user&&(user.name||user.email)||null;
  const on=!!name;
  if(on===PB.signed&&name===PB.user)return;
  if(!on){
    try{if(PB.user)localStorage.removeItem('xs_paper_book_v1:u:'+PB.user)}catch(e){}
    PB.signed=false;PB.user=null;PB.acct=read('xs_paper_book_v1:anon')||null;PB.summary=null;PB.offerAnon=false;
    paintBar();updatePanelFigures();if(bookVisible())fillBook();paintChart();
    boot();
    return}
  boot()};
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
