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
function solCompact(v,signed){if(v==null||!isFinite(+v))return'—';let n=+v;if(Math.abs(n)<5e-7)n=0;const body=Math.abs(n).toFixed(2);
  if(signed)return(n>0?'+':n<0?'-':'')+body+' SOL';return(n<0?'-':'')+body+' SOL'}
function usdCompact(v){if(v==null||!isFinite(+v))return'—';const n=+v;return(n<0?'-':'')+'$'+Math.abs(n).toFixed(2)}
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
    paintBar();paintChart();updatePanelFigures();if(bookVisible())fillBook();paintInstant();paintCal();
    if(!opts.silent){const rows=j.fills&&j.fills.length?j.fills:(j.fill?[j.fill]:[]);
      rows.forEach(f=>{if(!f||!f.side||f.side==='mark')return;const up=f.side==='sell'?(f.realized_sol||0)>=0:f.side!=='cancel';toast(fillMsg(f),f.side==='sell'?(up?'up':'dn'):'')})}}
  return j}


function liveUnrealized(){
  const a=PB.acct,sol=PB.sol,s=PB.summary;
  if(!a||!a.positions||!Object.keys(a.positions).length)return s&&s.unrealized_known_sol;
  let u=0,known=true,any=false;
  for(const [m,p] of Object.entries(a.positions)){
    if(!p||!(+p.qty>0))continue;
    any=true;
    const px=livePx(m);
    if(!(px>0)||!(sol>0)){known=false;continue}
    u += (p.qty*px/sol) - (p.cost_sol||0);
  }
  if(!any)return 0;
  return known?u:(s&&s.unrealized_known_sol);
}
function paintBar(){
  const bar=document.getElementById('paperBar');if(!bar)return;bar.hidden=false;
  const a=PB.acct,s=PB.summary;
  const cash=a?a.cash_sol:0, res=a?a.reserved_sol:0;
  document.getElementById('pbBarCash').textContent=a?solTxt(cash)+(res>0?' · '+solTxt(res)+' reserved':''):'Paper';
  const u=liveUnrealized();
  const uEl=document.getElementById('pbBarU');
  if(a&&Object.keys(a.positions||{}).length){uEl.className=clsPn(u);uEl.textContent='uPnL '+(u==null?'—':solTxt(u,true))+(s&&s.unpriced&&s.unpriced.length?' ·':'')}
  else{uEl.className='';uEl.textContent='no open position'}
  const r=a?a.realized_sol:0;
  const rEl=document.getElementById('pbBarR');rEl.className=clsPn(r);rEl.textContent='rPnL '+solTxt(r||0,true);
  const hint=document.getElementById('pbBarHint');
  if(hint)hint.textContent=PB.signed?'saved to your account · simulation · not advice':'saved in this browser · simulation · not advice'}

function ensurePanel(){
  const side=document.querySelector('#tokBody .tk-side');if(!side)return null;
  let el=document.getElementById('tkPaper');
  if(!el){el=document.createElement('div');el.id='tkPaper';el.className='card pb-card';
    const mini=document.getElementById('tkMini');
    if(mini&&mini.parentNode===side)side.insertBefore(el,mini.nextSibling);
    else side.insertBefore(el,side.firstChild)}
  return el}

function panelShell(){
  return `<div class="pb"><div class="pb-h"><span class="pb-tag">PAPER</span><small class="pb-note-inline">Not financial advice · Simulation. Play money only — no wallet, no real SOL.</small></div>
    <div class="pb-bal-line"><span>Cash <span class="sol-inline">${typeof solMark==='function'?solMark():''}<b id="pbCash">—</b></span></span><button type="button" class="linkbtn" id="pbDepToggle">+ Deposit</button><span>This token <b id="pbPos">—</b> <small id="pbUpnl"></small></span></div>
    <div class="pb-dep hidden" id="pbDepBox"><label>Deposit paper SOL<input id="pbDep" data-depinput type="number" min="0" step="any" placeholder="10" inputmode="decimal"></label>
      <div class="pb-presets"><button type="button" data-dep="1">1</button><button type="button" data-dep="5">5</button><button type="button" data-dep="10">10</button><button type="button" data-dep="25">25</button></div>
      <button type="button" class="primary" id="pbDepGo">Deposit</button></div>
    <div class="pb-px"><span>Price <b id="pbPx">—</b></span><span>SOL <span class="sol-inline">${typeof solMark==='function'?solMark():''}<b id="pbSolUsd">—</b></span></span></div>
    <div class="pb-tabs pb-sides" role="tablist"><button type="button" data-pbt="buy">Buy</button><button type="button" data-pbt="sell">Sell</button></div>
    <div class="pb-subtabs" role="tablist"><button type="button" data-pbmode="market" class="on">Market</button><button type="button" data-pbmode="limit">Limit</button></div>
    <div id="pbForm"></div>
    <p id="pbEst" class="pb-est"></p><p id="pbErr" class="pb-err"></p>
    <details class="pb-set"><summary>Slippage, fees, delay</summary>
      <label>Slippage %<input id="pbSlip" type="number" min="0" max="30" step="0.1"></label>
      <label>Priority fee (SOL)<input id="pbPri" type="number" min="0" max="0.05" step="0.0001"></label>
      <label>Platform fee %<input id="pbPlat" type="number" min="0" max="10" step="0.1"></label>
      <label>Simulated fill delay (ms)<input id="pbDelay" type="number" min="0" max="5000" step="50"></label>
      <button type="button" class="ghost" id="pbSetSave">Save settings</button>
      <p class="muted" style="margin:6px 0 0">A buy spends the SOL you type. The priority fee, then the platform fee, come out of it, and slippage worsens the fill. Sells take the same fees from the proceeds.</p>
    </details>
    <div id="pbMine" class="pb-orders"></div>
    <p class="pb-foot"><button type="button" class="linkbtn" id="pbOpenBook">Portfolio, history and calendar</button>
      <button type="button" class="linkbtn" id="pbReset">Reset</button>
      <button type="button" class="linkbtn" id="pbCard">PnL card</button></p></div>`}

function formHTML(){
  const t=PB.tab;
  if(t==='sell')return `<p class="muted" style="margin:0 0 8px;font-size:12px">Sells a share of the tokens that are not reserved by a sell limit.</p>
    <div class="pb-presets"><button type="button" data-pct="25" data-go="1">25%</button><button type="button" data-pct="50" data-go="1">50%</button><button type="button" data-pct="100" data-go="1">100%</button></div>
    <label>Custom % of free tokens<input id="pbPct" type="number" min="1" max="100" step="1" placeholder="25"></label>
    <p class="pb-fee" id="pbFee"></p><button type="button" class="pb-sell" id="pbSell">Paper Sell</button>`;
  if(t==='limit')return `<div class="pb-tabs" role="tablist"><button type="button" data-limside="buy">Buy limit</button><button type="button" data-limside="sell">Sell limit</button></div>
    <label>Trigger price, USD per token<input id="pbLim" type="number" min="0" step="any" inputmode="decimal"></label>
    <div id="pbLimAmt"></div>
    <p class="pb-fee" id="pbFee"></p><button type="button" class="pb-limit" id="pbLimGo">Place paper limit</button>
    <p class="muted" style="font-size:11px;margin:8px 0 0">A buy limit reserves the SOL now and fills when the live price is at or under the trigger. A sell limit reserves tokens and fills at or above it. The fill then uses that live price, plus slippage and fees.</p>`;
  const sm=typeof solMark==='function'?solMark():'';
  return `<label>AMOUNT<span class="sol-amt">${sm}<input id="pbSol" type="number" min="0" step="any" inputmode="decimal" placeholder="0.5"></span></label>
    <div class="pb-presets"><button type="button" data-sol="0.1">${sm}0.1</button><button type="button" data-sol="0.5">${sm}0.5</button><button type="button" data-sol="1">${sm}1</button><button type="button" data-sol="2">${sm}2</button></div>
    <p class="pb-fee" id="pbFee"></p><button type="button" class="pb-buy" id="pbBuy">Paper Buy</button>`}

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
  const s=settings();
  const set=(id,val)=>{const el=document.getElementById(id);if(el&&document.activeElement!==el&&el.value==='')el.value=val};
  set('pbSlip',String(s.slippage_bps/100));set('pbPri',String(s.priority_fee_sol));set('pbPlat',String(s.platform_fee_bps/100));set('pbDelay',String(s.exec_delay_ms))}

function updatePanelFigures(){
  paintInstant();
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
  const sym=(PB.token&&PB.token.symbol)||'TOKEN';
  if(buy){buy.disabled=PB.busy||wait;buy.textContent=PB.busy?'Filling…':'Paper Buy $'+sym}
  if(sell){sell.disabled=PB.busy||wait||!(pos&&pos.qty>pos.reserved_qty);sell.textContent=PB.busy?'Filling…':'Paper Sell $'+sym}
  const fee=document.getElementById('pbFee');
  if(fee){const st=settings();fee.textContent=(st.slippage_bps/100)+'% slip · '+st.priority_fee_sol+' SOL · '+(st.platform_fee_bps/100)+'% fee'}
  document.querySelectorAll('#tkPaper [data-pbmode]').forEach(b=>b.classList.toggle('on',b.dataset.pbmode===(PB.tab==='limit'?'limit':'market')));
  document.querySelectorAll('#tkPaper .pb-sides [data-pbt]').forEach(b=>{
    const side=PB.tab==='limit'?(PB.limSide||'buy'):PB.tab;
    b.classList.toggle('on',b.dataset.pbt===side);
  })
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

async function market(payload,opts){
  opts=opts||{};
  if(PB.busy)return;const gen=++PB.seq;PB.busy=true;updatePanelFigures();paintInstant();
  const delay=opts.delay!=null?Math.max(0,+opts.delay):(+settings().exec_delay_ms||0);
  const est=document.getElementById('pbEst');if(est)est.textContent=delay?`Simulating a ${delay} ms fill…`:'Filling…';
  await sleep(delay);
  if(gen!==PB.seq)return;
  const px=livePx(payload.mint);if(px)payload.px_usd=px;payload.waited_ms=delay;payload.sol_usd=PB.sol;
  try{await act(payload)}finally{if(gen===PB.seq){PB.busy=false;updatePanelFigures();scheduleEst()}}}

async function doDeposit(input){
  const sol=input&&+input.value;if(!sol||sol<=0){toast('Enter an amount of paper SOL.','dn');return}
  await act({action:'deposit',sol});if(input)input.value=''}

async function doReset(){
  if(!confirm('Reset this paper book? Play SOL, positions, limits and history will be cleared. Nothing real is affected.'))return;
  PB.seq++;PB.busy=false;await act({action:'reset'})}

async function saveSettings(){
  const num=id=>{const el=document.getElementById(id);return el&&el.value!==''?+el.value:null};
  await act({action:'settings',settings:{
    slippage_bps:num('pbSlip')!=null?Math.round(num('pbSlip')*100):null,
    priority_fee_sol:num('pbPri'),
    platform_fee_bps:num('pbPlat')!=null?Math.round(num('pbPlat')*100):null,
    exec_delay_ms:num('pbDelay')}})}

function ensureBook(){
  const root=document.getElementById('bookApp');if(!root||root.dataset.ready)return;
  root.dataset.ready='1';
  root.innerHTML=`<div id="bkOffer"></div><div class="pb-kpis" id="bkKpis"></div>
    <div class="grid2"><div class="card"><h3>Paper ${typeof solMark==='function'?solMark():''} SOL</h3>
      <p class="muted">A deposit is play money. It does not come from a wallet.</p>
      <div class="pb-row"><span class="sol-amt">${typeof solMark==='function'?solMark():''}<input id="bkDep" data-depinput type="number" min="0" step="any" placeholder="Amount" aria-label="Paper SOL to deposit"></span><button type="button" class="primary" id="bkDepGo">Deposit</button></div>
      <div class="pb-presets"><button type="button" data-dep="5">${typeof solMark==='function'?solMark():''}5</button><button type="button" data-dep="10">${typeof solMark==='function'?solMark():''}10</button><button type="button" data-dep="25">${typeof solMark==='function'?solMark():''}25</button><button type="button" data-dep="100">${typeof solMark==='function'?solMark():''}100</button></div>
      <p id="bkStore" class="muted"></p><button type="button" class="linkbtn" id="bkReset">Reset book</button>
    </div><div class="card"><h3>Record</h3><div id="bkStats"></div></div></div>
    <div class="card"><h3>Open positions</h3><div id="bkPos"></div></div>
    <div class="card"><h3>Limit orders</h3><div id="bkOrd"></div></div>
    <div class="card"><h3>Trades <button type="button" class="ghost" id="bkCalBtn" title="Open the paper PnL calendar">PnL calendar</button></h3><div id="bkHist"></div></div>`}

function kpi(l,v,c){return `<div class="pb-kpi"><span>${l}</span><b class="${c||''}">${v}</b></div>`}
function mytDate(off){const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit'});
  const today=fmt.format(new Date());const [y,m,d]=today.split('-').map(Number);const dt=new Date(Date.UTC(y,m-1,d)+off*86400000);
  return dt.getUTCFullYear()+'-'+String(dt.getUTCMonth()+1).padStart(2,'0')+'-'+String(dt.getUTCDate()).padStart(2,'0')}
function weekday(ds){const [y,m,d]=ds.split('-').map(Number);return new Date(Date.UTC(y,m-1,d)).getUTCDay()}

function fillBook(){
  ensureBook();if(!document.getElementById('bkKpis'))return;
  const a=PB.acct||{},s=PB.summary||{},u=s.unrealized_known_sol;
  document.getElementById('bkKpis').innerHTML=kpi('Cash',(typeof solMark==='function'?solMark():'')+' '+solTxt(a.cash_sol||0))+kpi('Reserved',solTxt(a.reserved_sol||0))+
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
  paintCal();
  const trades=(a.trades||[]).slice().reverse().slice(0,80);
  document.getElementById('bkHist').innerHTML=trades.length?`<div class="tablewrap"><table class="tkt"><thead><tr><th>When</th><th>Side</th><th>Token</th><th>SOL</th><th>Fill</th><th>Fee</th><th>Realised</th><th></th></tr></thead><tbody>`+
    trades.map(t=>`<tr><td>${when(t.ts)}</td><td class="${t.side==='buy'?'up':'dn'}">${esc(t.side)}${t.kind==='limit'?' lim':''}</td><td><a href="/token/${esc(t.mint)}" data-tok="${esc(t.mint)}">$${esc(t.symbol||'?')}</a></td><td>${solTxt(t.sol)}</td><td>${pxTxt(t.fill_px_usd)}</td><td>${solTxt(t.fee_sol)}</td><td class="${clsPn(t.realized_sol)}">${t.realized_sol==null?'—':solTxt(t.realized_sol,true)}</td><td>${t.side==='sell'?`<button type="button" class="linkbtn" data-share="${esc(t.id)}">Card</button>`:''}</td></tr>`).join('')+'</tbody></table></div>'
    :'<p class="muted">No paper trades yet.</p>'}

function calendar(daily){return monthGrid(daily, CAL.y, CAL.m)}

const MONTHS=['January','February','March','April','May','June','July','August','September','October','November','December'];
const CAL={open:false,y:null,m:null,bound:false,drag:null};
function mytParts(){const fmt=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Kuala_Lumpur',year:'numeric',month:'2-digit',day:'2-digit'});
  const [y,m,d]=fmt.format(new Date()).split('-').map(Number);return {y,m,d}}
function calLoad(){
  const now=mytParts();
  try{const s=JSON.parse(localStorage.getItem('xs_cal_ym_v1')||'null');if(s&&s.y&&s.m){CAL.y=s.y;CAL.m=s.m}else{CAL.y=now.y;CAL.m=now.m}}catch(e){CAL.y=now.y;CAL.m=now.m}
  if(!CAL.y){CAL.y=now.y;CAL.m=now.m}
  try{CAL.open=localStorage.getItem('xs_cal_open_v1')==='1'}catch(e){CAL.open=false}
}
function calSave(){try{localStorage.setItem('xs_cal_open_v1',CAL.open?'1':'0');localStorage.setItem('xs_cal_ym_v1',JSON.stringify({y:CAL.y,m:CAL.m}))}catch(e){}}
function monthGrid(daily,y,m){
  daily=daily||{};
  const first=y+'-'+String(m).padStart(2,'0')+'-01';
  const lead=(weekday(first)+6)%7;
  const dim=new Date(Date.UTC(y,m,0)).getUTCDate();
  const now=mytParts();
  const cells=[];let sum=0,n=0;
  for(let i=0;i<lead;i++)cells.push({empty:true});
  for(let d=1;d<=dim;d++){
    const key=y+'-'+String(m).padStart(2,'0')+'-'+String(d).padStart(2,'0');
    const has=Object.prototype.hasOwnProperty.call(daily,key);
    const v=has?+daily[key]:null;
    if(v){sum+=v;n++}
    const future=y>now.y||(y===now.y&&(m>now.m||(m===now.m&&d>now.d)));
    const today=y===now.y&&m===now.m&&d===now.d;
    cells.push({d,key,v,has,future,today});
  }
  while(cells.length%7)cells.push({empty:true});
  return {cells,sum,n,y,m};
}
function calCardHTML(){
  return `<div class="cal-head" data-caldrag="1">
      <button type="button" class="cal-nav" id="calPrev" aria-label="Previous month">‹</button>
      <b id="calTitle">—</b>
      <button type="button" class="cal-nav" id="calNext" aria-label="Next month">›</button>
      <span class="cal-total" id="calTotal">—</span>
      <button type="button" class="it-ico" id="calClose" title="Close PnL calendar" aria-label="Close">×</button>
    </div>
    <div class="cal-dow">${['Mon','Tue','Wed','Thu','Fri','Sat','Sun'].map(d=>`<span>${d}</span>`).join('')}</div>
    <div class="cal-grid" id="calGrid"></div>
    <p class="cal-note">PAPER · Realised PnL · Asia/Kuala_Lumpur</p>`;
}
function ensureCal(){
  let el=document.getElementById('calCard');
  if(!el){
    el=document.createElement('aside');
    el.id='calCard';el.className='cal-card hidden';
    el.setAttribute('role','dialog');el.setAttribute('aria-label','Paper PnL calendar');
    document.body.appendChild(el);
    el.innerHTML=calCardHTML();
    bindCal(el);
  }
  return el;
}
function bindCal(el){
  if(CAL.bound)return;CAL.bound=true;
  el.addEventListener('click',e=>{
    const t=e.target.closest('button');if(!t)return;
    if(t.id==='calClose'){e.preventDefault();setCalOpen(false);return}
    if(t.id==='calPrev'){e.preventDefault();CAL.m--;if(CAL.m<1){CAL.m=12;CAL.y--}calSave();paintCal();return}
    if(t.id==='calNext'){e.preventDefault();CAL.m++;if(CAL.m>12){CAL.m=1;CAL.y++}calSave();paintCal();return}
  });
  el.addEventListener('pointerdown',e=>{
    if(!e.target.closest('[data-caldrag]'))return;
    if(e.target.closest('button'))return;
    const r=el.getBoundingClientRect();
    CAL.drag={dx:e.clientX-r.left,dy:e.clientY-r.top,pid:e.pointerId};
    try{el.setPointerCapture(e.pointerId)}catch(err){}
    e.preventDefault();
  });
  el.addEventListener('pointermove',e=>{
    if(!CAL.drag||(CAL.drag.pid!=null&&e.pointerId!==CAL.drag.pid))return;
    const x=Math.max(8,Math.min(window.innerWidth-el.offsetWidth-8,e.clientX-CAL.drag.dx));
    const y=Math.max(8,Math.min(window.innerHeight-el.offsetHeight-8,e.clientY-CAL.drag.dy));
    el.style.left=x+'px';el.style.top=y+'px';el.style.right='auto';el.style.bottom='auto';
  });
  const end=e=>{
    if(!CAL.drag)return;
    if(CAL.drag.pid!=null&&e.pointerId!==CAL.drag.pid&&e.type==='pointerup')return;
    try{localStorage.setItem('xs_cal_pos_v1',JSON.stringify({left:el.style.left,top:el.style.top}))}catch(err){}
    CAL.drag=null;
  };
  el.addEventListener('pointerup',end);el.addEventListener('pointercancel',end);
}
function defaultCalPos(el){
  try{const pos=JSON.parse(localStorage.getItem('xs_cal_pos_v1')||'null');
    if(pos&&pos.left&&pos.top){el.style.left=pos.left;el.style.top=pos.top;el.style.right='auto';el.style.bottom='auto';return}}catch(e){}
  el.style.left=Math.max(16,window.innerWidth-392)+'px';
  el.style.top='96px';el.style.right='auto';el.style.bottom='auto';
}
function setCalOpen(on){
  CAL.open=!!on;calSave();
  const el=ensureCal();
  el.classList.toggle('hidden',!CAL.open);
  el.setAttribute('aria-hidden',CAL.open?'false':'true');
  if(CAL.open){if(!el.style.left)defaultCalPos(el);paintCal()}
  document.querySelectorAll('#dockCal,#bkCalBtn').forEach(b=>{if(b){b.classList.toggle('on',CAL.open);b.setAttribute('aria-pressed',CAL.open?'true':'false')}});
}
function paintCal(){
  document.querySelectorAll('#dockCal,#bkCalBtn').forEach(b=>{if(b){b.classList.toggle('on',!!CAL.open);b.setAttribute('aria-pressed',CAL.open?'true':'false')}});
  const el=document.getElementById('calCard');if(!el)return;
  el.classList.toggle('hidden',!CAL.open);
  if(!CAL.open)return;
  if(!CAL.y)calLoad();
  const g=monthGrid((PB.acct&&PB.acct.daily)||{},CAL.y,CAL.m);
  const title=document.getElementById('calTitle');if(title)title.textContent=MONTHS[CAL.m-1]+' '+CAL.y;
  const tot=document.getElementById('calTotal');
  if(tot){tot.textContent=solCompact(g.sum,true);tot.className='cal-total '+(g.sum>0?'up':g.sum<0?'dn':'')}
  const grid=document.getElementById('calGrid');if(!grid)return;
  grid.innerHTML=g.cells.map(c=>{
    if(c.empty)return'<div class="cal-cell empty"></div>';
    const cls=['cal-cell',c.today?'today':'',c.future?'future':'',c.v>0?'up':c.v<0?'dn':c.has?'flat':''].filter(Boolean).join(' ');
    const pnl=c.has&&c.v!=null?`<small>${c.v===0?'0.00':solCompact(c.v,true).replace(' SOL','')}</small>`:'';
    return `<div class="${cls}" title="${c.key}${c.has?' · '+solTxt(c.v,true):' · no close'}"><span>${c.d}</span>${pnl}</div>`;
  }).join('');
}
window.paperToggleCal=function(force){calLoad();setCalOpen(force==null?!CAL.open:!!force)};
function bindCalChrome(){
  const dock=document.getElementById('dockCal');
  if(dock&&!dock.dataset.bound){dock.dataset.bound='1';dock.addEventListener('click',e=>{e.preventDefault();window.paperToggleCal()})}
  document.addEventListener('click',e=>{
    const b=e.target.closest('#bkCalBtn');if(!b)return;e.preventDefault();window.paperToggleCal();
  });
  calLoad();ensureCal();setCalOpen(!!CAL.open);
}

function drawCard(opts){
  const c=document.createElement('canvas');c.width=1200;c.height=675;const g=c.getContext('2d');if(!g)return c;
  g.fillStyle='#0b0c0e';g.fillRect(0,0,1200,675);
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
  g.fillText('Simulation only. Not financial advice. No wallet was connected and no order was sent.',64,600);
  return c}

function openShare(opts){
  const c=drawCard(opts);
  let ov=document.getElementById('pbShare');
  if(!ov){ov=document.createElement('div');ov.id='pbShare';ov.innerHTML='<div class="pb-sharebox" role="dialog" aria-label="Paper PnL card"><img alt="Paper trading PnL card"><div class="pb-sharerow"><button type="button" class="primary" id="pbShareDl">Download PNG</button><button type="button" class="ghost" id="pbShareX">Close</button></div><p class="muted">Simulation only. Not financial advice.</p></div>';
    document.body.appendChild(ov);
    ov.addEventListener('click',e=>{if(e.target===ov||e.target.id==='pbShareX')ov.classList.remove('on')})}
  c.toBlob(blob=>{if(!blob){toast('Could not draw the card.','dn');return}
    if(PB.shareUrl)URL.revokeObjectURL(PB.shareUrl);PB.shareUrl=URL.createObjectURL(blob);
    ov.querySelector('img').src=PB.shareUrl;ov.classList.add('on');
    document.getElementById('pbShareDl').onclick=()=>{const a=document.createElement('a');a.href=PB.shareUrl;a.download='xscanner-paper-'+(opts.symbol||'pnl').replace(/[^A-Za-z0-9]+/g,'')+'.png';a.click()}})}

function sharePosition(mint){const p=posOf(mint),r=rowOf(mint);if(!p)return;
  openShare({symbol:p.symbol,pnlPct:r&&r.upnl_pct,pnlSol:r&&r.upnl_sol,entry:p.avg_px_usd,mark:r&&r.mark_px,label:'Unrealised'})}
function shareTrade(id){const t=((PB.acct&&PB.acct.trades)||[]).find(x=>x.id===id);if(!t)return;
  openShare({symbol:t.symbol,pnlPct:t.pnl_pct,pnlSol:t.realized_sol,entry:t.avg_px_usd,mark:t.fill_px_usd,label:t.side==='sell'?'Realised':'Fill',spent:t.side==='buy'?t.sol:null})}

let estT=0;
function scheduleEst(){clearTimeout(estT);estT=setTimeout(()=>{runEst().catch(()=>{})},280)}

async function onClick(e){
  const t=e.target.closest('button');if(!t)return;
  if(t.id==='paperBar'||t.id==='dockPaper'||t.id==='toMyPaper'||t.id==='pbOpenBook'){e.preventDefault();if(window.show)window.show('book');return}
  if(t.dataset.dep){const scope=t.closest('.pb,.card')||document;const input=scope.querySelector('[data-depinput]');if(input){input.value=t.dataset.dep;input.focus()}return}
  if(t.dataset.sol){const input=document.getElementById('pbSol');if(input){input.value=t.dataset.sol;scheduleEst()}return}
  if(t.dataset.pbt){if(PB.tab==='limit'||PB._mode==='limit'){PB.tab='limit';PB.limSide=t.dataset.pbt;PB._mode='limit'}else{PB.tab=t.dataset.pbt;PB._mode='market'}const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.pbmode){PB._mode=t.dataset.pbmode;if(PB._mode==='limit'){PB.limSide=PB.tab==='sell'?'sell':'buy';PB.tab='limit'}else{PB.tab=PB.limSide||PB.tab||'buy';if(PB.tab==='limit')PB.tab=PB.limSide||'buy'}const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.limside){PB.limSide=t.dataset.limside;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();return}
  if(t.dataset.pct&&t.dataset.go&&PB.token){const pct=+t.dataset.pct;await guard(()=>market(Object.assign({action:'sell',mint:PB.token.mint,pct,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbBuy'&&PB.token){const sol=+(document.getElementById('pbSol')||{}).value;await guard(()=>market(Object.assign({action:'buy',mint:PB.token.mint,sol,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbSell'&&PB.token){const pct=+(document.getElementById('pbPct')||{}).value;if(!pct){toast('Enter a percent, or use 25 / 50 / 100.','dn');return}await guard(()=>market(Object.assign({action:'sell',mint:PB.token.mint,pct,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbLimGo'&&PB.token){const limit=+(document.getElementById('pbLim')||{}).value;
    const payload=Object.assign({action:'limit',side:PB.limSide,mint:PB.token.mint,limit_px:limit},meta());
    if(PB.limSide==='buy')payload.sol=+(document.getElementById('pbLimSol')||{}).value;else payload.pct=+(document.getElementById('pbLimPct')||{}).value||100;
    await guard(()=>act(payload));return}
  if(t.id==='pbDepToggle'){const box=document.getElementById('pbDepBox');if(box)box.classList.toggle('hidden');return}
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

function onInput(e){if(e.target&&(e.target.id==='pbSol'||e.target.id==='pbPct'||e.target.id==='pbLim'))scheduleEst()}

function snapTime(ts,candles){if(!candles||!candles.length)return null;let best=candles[0].t;for(const k of candles){if(k.t<=ts)best=k.t;else break}return best}
function paintChart(){
  if(typeof CH==='undefined'||!CH.cs||!CH.coin||!PB.acct)return;
  const mint=CH.coin.mint;if(!PB.candles||PB.chartMint!==mint)return;
  const grouped=new Map();
  for(const t of PB.acct.trades||[]){if(t.mint!==mint||(t.side!=='buy'&&t.side!=='sell'))continue;const tm=snapTime(t.ts,PB.candles);if(tm==null)continue;
    const g=grouped.get(tm)||{time:tm,buy:0,sell:0};g[t.side]++;grouped.set(tm,g)}
  const markers=[...grouped.values()].sort((a,b)=>a.time-b.time).map(g=>{
    const both=g.buy&&g.sell;
    return {time:g.time,position:both?'inBar':g.buy?'belowBar':'aboveBar',color:both?'#e6c35c':g.buy?'#00f5a0':'#ff3b7a',size:2,
      shape:both?'circle':g.buy?'arrowUp':'arrowDown',
      text:(g.buy?('B'+(g.buy>1?'×'+g.buy:'')):'')+(both?' · ':'')+(g.sell?('S'+(g.sell>1?'×'+g.sell:'')):'')}})
  const series=CH.style==='line'?(CH.area||CH.cs):CH.cs;
  try{if(CH.cs)CH.cs.setMarkers(CH.style==='line'?[]:markers)}catch(e){}
  try{if(CH.area&&CH.style==='line')CH.area.setMarkers(markers)}catch(e){}
  try{if(PB.entryLine){ (PB.entryHost||CH.cs).removePriceLine(PB.entryLine) }}catch(e){}
  PB.entryLine=null;PB.entryHost=null;
  const pos=posOf(mint);
  if(pos&&pos.avg_px_usd>0&&series){
    const row=rowOf(mint);
    const scaled=(CH.unit==='mcap'&&typeof validSupply==='function'&&validSupply(CH.supply))||(CH.unit==='mcap'&&CH.supply);
    const px=scaled?pos.avg_px_usd*CH.supply:pos.avg_px_usd;
    const live=livePx(mint);
    let upnl=row&&row.upnl_sol, upct=row&&row.upnl_pct;
    if(live>0&&PB.sol>0&&pos.qty){upnl=(pos.qty*live/PB.sol)-(pos.cost_sol||0);upct=pos.cost_sol?upnl/pos.cost_sol*100:null}
    const up=!(upnl<0);
    const pnl=upnl!=null?solTxt(upnl,true):'';
    const pc=upct!=null?((upct>=0?'+':'')+upct.toFixed(1)+'%'):'';
    const title='Avg'+(pnl?(' · '+pnl):'')+(pc?(' '+pc):'');
    try{PB.entryLine=series.createPriceLine({price:px,color:up?'#00f5a0':'#ff3b7a',lineWidth:1,lineStyle:2,axisLabelVisible:true,title});PB.entryHost=series}catch(e){}}}

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

/* Optional Instant Paper Trade card. 100% simulated via /api/paper/book/act. Never a wallet or a real swap. */
const DEF_PRESETS={P1:[0.1,0.5,1,2],P2:[0.2,1,2,5],P3:[0.5,2,5,10]};
const IT={
  open:false,tab:'P1',speed:0,editing:false,snap:null,bound:false,
};
function itLoad(){
  try{IT.open=localStorage.getItem('xs_instant_open_v1')==='1'}catch(e){IT.open=false}
  try{IT.tab=localStorage.getItem('xs_instant_preset_tab_v1')||'P1'}catch(e){}
  if(!DEF_PRESETS[IT.tab])IT.tab='P1';
  try{const s=localStorage.getItem('xs_instant_speed_v1');IT.speed=s==null?0:(+s||0)}catch(e){IT.speed=0}
  try{const p=JSON.parse(localStorage.getItem('xs_instant_presets_v1')||'null');IT.presets=p&&typeof p==='object'?p:{}}catch(e){IT.presets={}}
  for(const k of Object.keys(DEF_PRESETS)){if(!Array.isArray(IT.presets[k])||IT.presets[k].length!==4)IT.presets[k]=DEF_PRESETS[k].slice()}
}
function itSave(k,v){try{localStorage.setItem(k,typeof v==='string'?v:JSON.stringify(v))}catch(e){}}
function itTarget(){if(PB.token&&PB.token.mint)return PB.token;return IT.snap}
function itMint(){const t=itTarget();return t&&t.mint||null}
function itSym(){const t=itTarget();return (t&&(t.symbol||t.s))||'TOKEN'}
function itAmounts(){return (IT.presets&&IT.presets[IT.tab])||DEF_PRESETS.P1}
function itMintStats(mint){
  let bought=0,sold=0;
  for(const tr of (PB.acct&&PB.acct.trades)||[]){
    if(tr.mint!==mint)continue;
    if(tr.side==='buy')bought+=+(tr.sol||0);
    else if(tr.side==='sell')sold+=+(tr.sol||0);
  }
  const pos=posOf(mint),row=rowOf(mint);
  const holdQty=pos?+(pos.qty||0):0;
  const px=livePx(mint),sol=PB.sol;
  let holdSol=row&&row.mark_sol!=null?+row.mark_sol:null;
  let holdUsd=(row&&row.mark_px&&holdQty)?row.mark_px*holdQty:null;
  let pnl=row&&row.upnl_sol!=null?+row.upnl_sol:null;
  if(holdQty>0&&px>0){
    holdUsd=px*holdQty;
    if(sol>0){holdSol=holdUsd/sol;pnl=holdSol-(pos.cost_sol||0)}
  }
  return {bought,sold,holdQty,holdSol,holdUsd,pnl,pos,row};
}
function itCardHTML(){
  const sm=typeof solMark==='function'?solMark():'';
  const am=itAmounts();
  const st=settings();
  return `<div class="it-head" data-itdrag="1">
      <div class="it-tabs" role="tablist" aria-label="Instant presets">${['P1','P2','P3'].map(k=>`<button type="button" data-ittab="${k}" class="${k===IT.tab?'on':''}">${k}</button>`).join('')}</div>
      <span class="pb-tag">PAPER</span>
      <button type="button" class="it-ico" id="itEdit" title="Edit presets" aria-label="Edit presets">✎</button>
      <button type="button" class="it-ico" id="itClose" title="Close Instant card" aria-label="Close">×</button>
    </div>
    <div class="it-edit ${IT.editing?'':'hidden'}" id="itEditBox">
      <p class="muted">Custom ${IT.tab} buy amounts (paper SOL). Saved in this browser.</p>
      <div class="it-edit-row">${[0,1,2,3].map(i=>`<input data-itedit="${i}" type="number" min="0" step="any" value="${am[i]}">`).join('')}</div>
      <button type="button" class="ghost" id="itEditSave">Save ${IT.tab}</button>
    </div>
    <div class="it-buy">
      <div class="it-rowlab"><b>Buy</b>${sm}</div>
      <div class="it-presets it-buy-presets">${am.map(v=>`<button type="button" data-itbuy="${v}">${sm}<span>${v}</span></button>`).join('')}</div>
      <div class="it-meta"><span>${(st.slippage_bps/100)}% · ${st.priority_fee_sol} SOL · ${(st.platform_fee_bps/100)}%</span>
        <div class="it-speed" role="group" aria-label="Simulated fill speed">
          <button type="button" data-itspeed="0" class="${IT.speed===0?'on':''}">0ms</button>
          <button type="button" data-itspeed="700" class="${IT.speed===700?'on':''}">700ms</button>
        </div>
      </div>
    </div>
    <div class="it-sell">
      <div class="it-rowlab"><b>Sell</b><span id="itHold" class="it-hold">0.00 TOKEN · $0.00</span></div>
      <div class="it-presets it-sell-presets">${[10,25,50,100].map(p=>`<button type="button" data-itsell="${p}">${p}%</button>`).join('')}
        <button type="button" class="it-init hidden" id="itSellInit" data-itsellinit="1">Sell Init</button>
      </div>
    </div>
    <div class="it-foot">
      <div class="it-kpis">
        <div class="it-kpi"><span>Bought</span><b id="itBought">—</b></div>
        <div class="it-kpi"><span>Sold</span><b id="itSold">—</b></div>
        <div class="it-kpi"><span>Holding</span><b id="itHolding">—</b></div>
        <div class="it-kpi"><span>PnL</span><b id="itPnl">—</b></div>
      </div>
      <div class="it-foot-l"><small>PAPER · Simulation</small><button type="button" class="linkbtn hidden" id="itDepGo">+ Deposit</button></div>
      <div class="it-dep hidden" id="itDepBox"><span class="sol-amt">${sm}<input id="itDep" type="number" min="0" step="any" placeholder="10" inputmode="decimal" aria-label="Paper SOL to deposit"></span>
        <button type="button" class="ghost" id="itDepOk">Deposit</button></div>
    </div>`;
}
function ensureInstant(){
  let el=document.getElementById('itCard');
  if(!el){
    el=document.createElement('aside');
    el.id='itCard';
    el.className='it-card hidden';
    el.setAttribute('role','dialog');
    el.setAttribute('aria-label','Instant Paper Trade');
    document.body.appendChild(el);
    el.innerHTML=itCardHTML();
    bindInstant(el);
  }
  return el;
}
function bindInstant(el){
  if(IT.bound)return;IT.bound=true;
  el.addEventListener('click',e=>{
    const t=e.target.closest('button, [data-itbuy], [data-itsell]');if(!t)return;
    if(t.id==='itClose'){e.preventDefault();setInstantOpen(false);return}
    if(t.id==='itEdit'){e.preventDefault();IT.editing=!IT.editing;const box=document.getElementById('itEditBox');if(box)box.classList.toggle('hidden',!IT.editing);return}
    if(t.id==='itEditSave'){e.preventDefault();const next=[0,1,2,3].map(i=>{const inp=el.querySelector('[data-itedit="'+i+'"]');return Math.max(0,+(inp&&inp.value)||0)});IT.presets[IT.tab]=next;itSave('xs_instant_presets_v1',IT.presets);IT.editing=false;rebuildInstant();return}
    if(t.dataset.ittab){IT.tab=t.dataset.ittab;itSave('xs_instant_preset_tab_v1',IT.tab);IT.editing=false;rebuildInstant();return}
    if(t.dataset.itspeed!=null){IT.speed=+t.dataset.itspeed||0;itSave('xs_instant_speed_v1',String(IT.speed));el.querySelectorAll('[data-itspeed]').forEach(b=>b.classList.toggle('on',+b.dataset.itspeed===IT.speed));return}
    if(t.id==='itDepGo'){e.preventDefault();const box=document.getElementById('itDepBox');if(box)box.classList.toggle('hidden');return}
    if(t.id==='itDepOk'){e.preventDefault();const inp=document.getElementById('itDep');doDeposit(inp);return}
    if(t.dataset.itbuy!=null){e.preventDefault();instantBuy(+t.dataset.itbuy);return}
    if(t.dataset.itsell!=null){e.preventDefault();instantSell(+t.dataset.itsell);return}
    if(t.dataset.itsellinit){e.preventDefault();instantSellInit();return}
  });
  // drag from the header
  el.addEventListener('pointerdown',e=>{
    if(!e.target.closest('[data-itdrag]'))return;
    if(e.target.closest('button'))return;
    const r=el.getBoundingClientRect();
    IT.drag={dx:e.clientX-r.left,dy:e.clientY-r.top,pid:e.pointerId};
    try{el.setPointerCapture(e.pointerId)}catch(err){}
    e.preventDefault();
  });
  el.addEventListener('pointermove',e=>{
    if(!IT.drag||(IT.drag.pid!=null&&e.pointerId!==IT.drag.pid))return;
    const x=Math.max(8,Math.min(window.innerWidth-el.offsetWidth-8,e.clientX-IT.drag.dx));
    const y=Math.max(8,Math.min(window.innerHeight-el.offsetHeight-8,e.clientY-IT.drag.dy));
    el.style.left=x+'px';el.style.top=y+'px';el.style.right='auto';el.style.bottom='auto';
  });
  const end=e=>{
    if(!IT.drag)return;
    if(IT.drag.pid!=null&&e.pointerId!==IT.drag.pid&&e.type==='pointerup')return;
    try{itSave('xs_instant_pos_v1',{left:el.style.left,top:el.style.top})}catch(err){}
    IT.drag=null;
  };
  el.addEventListener('pointerup',end);
  el.addEventListener('pointercancel',end);
}
function rebuildInstant(){
  const el=document.getElementById('itCard');if(!el)return;
  el.innerHTML=itCardHTML();
  IT.bound=false;bindInstant(el);paintInstant();
}
function defaultInstantPos(el){
  try{
    const pos=JSON.parse(localStorage.getItem('xs_instant_pos_v1')||'null');
    if(pos&&pos.left&&pos.top){el.style.left=pos.left;el.style.top=pos.top;el.style.right='auto';el.style.bottom='auto';return}
  }catch(e){}
  // Float over the chart, left of the ~320px trade panel when present.
  const side=document.querySelector('#tokBody .tk-side');
  const chart=document.querySelector('.chartcard')||document.querySelector('#chWrap');
  const w=el.offsetWidth||320,h=el.offsetHeight||280;
  let left,top;
  if(chart){
    const r=chart.getBoundingClientRect();
    left=Math.max(16,r.right-w-16);
    top=Math.max(64,r.top+48);
  }else{left=Math.max(16,window.innerWidth-w-360);top=120}
  if(side){
    const sr=side.getBoundingClientRect();
    if(left+w>sr.left-8)left=Math.max(16,sr.left-w-12);
  }
  el.style.left=left+'px';el.style.top=top+'px';el.style.right='auto';el.style.bottom='auto';
}
function setInstantOpen(on){
  IT.open=!!on;itSave('xs_instant_open_v1',IT.open?'1':'0');
  const el=ensureInstant();
  el.classList.toggle('hidden',!IT.open);
  el.setAttribute('aria-hidden',IT.open?'false':'true');
  if(IT.open){if(!el.style.left)defaultInstantPos(el);paintInstant()}
  document.querySelectorAll('#itToggle,#dockInstant').forEach(b=>{
    if(!b)return;b.classList.toggle('on',IT.open);b.setAttribute('aria-pressed',IT.open?'true':'false');
  });
}
function paintInstant(){
  document.querySelectorAll('#itToggle,#dockInstant').forEach(b=>{
    if(!b)return;b.classList.toggle('on',IT.open);b.setAttribute('aria-pressed',IT.open?'true':'false');
  });
  const el=document.getElementById('itCard');
  if(!el)return;
  el.classList.toggle('hidden',!IT.open);
  if(!IT.open)return;
  const mint=itMint(),sym=itSym(),st=itMintStats(mint);
  const hold=document.getElementById('itHold');
  if(hold){
    const usd=st.holdUsd!=null?(' · $'+st.holdUsd.toFixed(2)):' · $0.00';
    hold.textContent=(st.holdQty?fmtQty(st.holdQty):'0.00')+' '+sym+usd;
  }
  const init=document.getElementById('itSellInit');
  if(init)init.classList.toggle('hidden',!(st.holdQty>0));
  const set=(id,txt,cls)=>{const n=document.getElementById(id);if(!n)return;n.textContent=txt;n.className=cls||''};
  set('itBought',mint?solCompact(st.bought):'—','buy');
  set('itSold',mint?solCompact(st.sold):'—','sell');
  set('itHolding',st.holdSol!=null?solCompact(st.holdSol):(st.holdUsd!=null?usdCompact(st.holdUsd):'—'),'hold');
  set('itPnl',st.pnl==null?'—':solCompact(st.pnl,true),clsPn(st.pnl));
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  const dep=document.getElementById('itDepGo');
  if(dep)dep.classList.toggle('hidden',cash>0);
  el.classList.toggle('it-empty',!mint);
}
async function instantBuy(sol){
  const t=itTarget();
  if(!t||!t.mint){toast('Open a token to paper-buy.','dn');return}
  if(!sol||sol<=0)return;
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  if(cash<=0){toast('Deposit paper SOL first. This is a simulation.','dn');const box=document.getElementById('itDepBox');const go=document.getElementById('itDepGo');if(go)go.classList.remove('hidden');if(box)box.classList.remove('hidden');return}
  const px=livePx(t.mint);
  await market(Object.assign({action:'buy',mint:t.mint,sol,px_usd:px}, {symbol:t.symbol||t.s||'',name:t.name||'',pool:t.pool||''}),{delay:IT.speed});
}
async function instantSell(pct){
  const t=itTarget();
  if(!t||!t.mint){toast('Open a token to paper-sell.','dn');return}
  const pos=posOf(t.mint);
  if(!(pos&&pos.qty>0)){toast('No paper position in $'+(t.symbol||'TOKEN')+'.','dn');return}
  const px=livePx(t.mint);
  await market(Object.assign({action:'sell',mint:t.mint,pct,px_usd:px},{symbol:t.symbol||t.s||'',name:t.name||'',pool:t.pool||''}),{delay:IT.speed});
}
function instantSellInit(){
  const t=itTarget();if(!t||!t.mint)return;
  const pos=posOf(t.mint);if(!pos||!(pos.qty>0)){toast('No paper position to sell.','dn');return}
  const px=livePx(t.mint);
  const avg=+pos.avg_px_usd||0;
  // Recover the initial cost basis: sell the share of the bag whose mark equals cost.
  // If price is unchanged that is 100%. If it's up, sell a smaller %. Floor at 1%.
  let pct=100;
  if(px&&px>0&&avg>0)pct=Math.min(100,Math.max(1,(avg/px)*100));
  instantSell(pct);
}
window.paperToggleInstant=function(force){
  itLoad();
  setInstantOpen(force==null?!IT.open:!!force);
};
window.paperSyncInstant=function(){itLoad();ensureInstant();setInstantOpen(IT.open)};
window.paperQuickBuy=async function(info){
  if(!info||!info.mint)return;
  IT.snap={mint:info.mint,symbol:info.symbol||'',name:info.name||'',pool:info.pool||'',px:info.px};
  if(info.px)PB.quotes[info.mint]={px:+info.px,t:Date.now()/1000};
  const inp=document.getElementById('pqAmt');const sol=(inp&&+inp.value>0)?+inp.value:(function(){try{const v=+localStorage.getItem('xs_pulse_qbuy_amt_v1');return v>0?v:0.1}catch(e){return 0.1}})();
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  if(cash<=0){toast('Deposit paper SOL first to Quick Buy. Simulation only.','dn');if(window.show)window.show('book');return}
  const px=livePx(info.mint)||info.px||null;
  await market(Object.assign({action:'buy',mint:info.mint,sol,px_usd:px},{symbol:info.symbol||'',name:info.name||'',pool:info.pool||''}),{delay:0});
};

function bindInstantChrome(){
  const dock=document.getElementById('dockInstant');
  if(dock&&!dock.dataset.bound){dock.dataset.bound='1';dock.addEventListener('click',e=>{e.preventDefault();window.paperToggleInstant()})}
  itLoad();ensureInstant();
  if(IT.open)setInstantOpen(true);
  else setInstantOpen(false);
}


window.paperPosition=function(mint){if(!mint||!PB.acct)return null;return{pos:(PB.acct.positions||{})[mint]||null,row:((PB.summary&&PB.summary.positions)||[]).find(r=>r.mint===mint)||null}};
window.paperOnToken=function(d){if(!d||!d.mint)return;PB.token=d;
  IT.snap={mint:d.mint,symbol:d.symbol||'',name:d.name||'',pool:d.pool||'',px:d.market&&d.market.px};
  if(d.market&&d.market.px)PB.quotes[d.mint]={px:+d.market.px,t:Date.now()/1000};
  const el=ensurePanel();if(!el)return;
  if(PB.panelMint!==d.mint||!el.querySelector('.pb')){PB.panelMint=d.mint;PB.tab='buy';el.dataset.tab='';el.innerHTML=panelShell();paintForm()}
  updatePanelFigures();paintChart();paintInstant();
  if((PB.acct&&PB.acct.orders||[]).some(o=>o.mint===d.mint)){clearTimeout(PB.markSoon);PB.markSoon=setTimeout(()=>maybeMark(true),600)}};
window.paperLeaveToken=function(){PB.token=null;PB.entryLine=null;paintInstant()};

document.addEventListener('click',e=>{onClick(e).catch(()=>{})});
document.addEventListener('input',onInput);
document.addEventListener('keydown',e=>{if(e.key!=='Enter')return;
  if(e.target.id==='pbSol'){e.preventDefault();const b=document.getElementById('pbBuy');if(b)b.click()}
  if(e.target.id==='pbPct'){e.preventDefault();const b=document.getElementById('pbSell');if(b)b.click()}
  if(e.target.id==='pbDep'){e.preventDefault();const b=document.getElementById('pbDepGo');if(b)b.click()}
  if(e.target.id==='bkDep'){e.preventDefault();const b=document.getElementById('bkDepGo');if(b)b.click()}});
document.addEventListener('xschart',e=>{const d=e.detail||{};PB.chartMint=d.mint;PB.candles=d.candles||[];if(d.last&&d.mint)PB.quotes[d.mint]={px:d.last,t:Date.now()/1000,src:'chart'};paintChart()});

async function boot(){
  bindInstantChrome();
  bindCalChrome();
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


window.paperOnTick=function(j){
  if(!j||!j.mint||!(+j.px>0))return;
  const now=Date.now()/1000;
  PB.quotes[j.mint]={px:+j.px,t:now,mc:j.mc,src:'tick'};
  if(+j.sol_usd>0)PB.sol=+j.sol_usd;
  if(PB.token&&PB.token.mint===j.mint&&PB.token.market){PB.token.market.px=+j.px;if(j.mc!=null)PB.token.market.mc=j.mc}
  try{paintInstant();paintBar();if(typeof paintChart==='function')paintChart();if(typeof updatePanelFigures==='function')updatePanelFigures()}catch(e){}
};
