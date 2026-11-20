// Paper book: play-money buys and sells. No wallet, no keys, no transactions.
// Prices come from the token page and /api/quotes (both already cached server-side).
// The fill math lives in papertrade.py; this file only previews, waits, and draws.
(function(){
'use strict';
const PB={acct:null,email:null,sol:null,summary:null,token:null,quotes:{},tab:'buy',limSide:'buy',
  busy:false,marking:false,seq:0,entryLine:null,candles:null,chartMint:null,shareUrl:null,panelMint:null};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const read=k=>{try{return JSON.parse(localStorage.getItem(k)||'null')}catch(e){return null}};
const write=(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}};
const key=()=>'xs_paper_book_v1:'+(PB.email||'anon');
const used=a=>!!(a&&((a.cash_sol||0)>0||(a.deposited_sol||0)>0||(a.trades||[]).length||Object.keys(a.positions||{}).length||(a.orders||[]).length));
function toast(msg,cls){let b=document.getElementById('toasts');if(!b){b=document.createElement('div');b.id='toasts';document.body.appendChild(b)}
  const t=document.createElement('div');t.className='toast '+(cls||'');t.textContent=msg;b.appendChild(t);setTimeout(()=>t.classList.add('out'),4200);setTimeout(()=>t.remove(),4800)}
function solTxt(v,signed){if(v==null||!isFinite(+v))return'—';let n=+v;if(Math.abs(n)<5e-7)n=0;const a=Math.abs(n);const d=a>=100?2:a>=1?3:a>=0.01?4:6;const body=a.toFixed(d);
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

async function act(payload,opts){
  opts=opts||{};
  const body=Object.assign({},payload);
  if(!body.rid)body.rid='r'+Math.random().toString(36).slice(2)+Date.now().toString(36);
  if(body.action==='adopt')body.account=payload.account;
  else if(!PB.email)body.account=PB.acct;
  if(body.sol_usd==null&&PB.sol)body.sol_usd=PB.sol;
  const r=await fetch('/api/paper/book/act',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const j=await r.json().catch(()=>({}));
  if(!r.ok||j.ok===false){const msg=typeof j.detail==='string'?j.detail:(j.error||'Paper trade failed');
    if(!opts.quiet)toast(msg,'dn');throw new Error(msg)}
  if(j.sol_usd)PB.sol=j.sol_usd;
  if(!j.dry){PB.acct=j.account;PB.summary=j.summary;saveLocal();
    paintBar();paintChart();updatePanelFigures();if(bookVisible())fillBook();
    if(!opts.silent){const rows=j.fills&&j.fills.length?j.fills:(j.fill?[j.fill]:[]);
      rows.forEach(f=>{if(!f||!f.side||f.side==='mark')return;const up=f.side==='sell'?(f.realized_sol||0)>=0:f.side!=='cancel';toast(fillMsg(f),f.side==='sell'?(up?'up':'dn'):'')})}}
  return j}

function paintBar(){
  const bar=document.getElementById('paperBar');if(!bar)return;bar.hidden=false;
  const a=PB.acct,s=PB.summary;
  const cash=a?a.cash_sol:0, res=a?a.reserved_sol:0;
  document.getElementById('pbBarCash').textContent=a?solTxt(cash)+(res>0?' · '+solTxt(res)+' reserved':''):'Paper';
  const u=s&&s.unrealized_known_sol;
  const uEl=document.getElementById('pbBarU');
  if(a&&Object.keys(a.positions||{}).length){uEl.className=clsPn(u);uEl.textContent='uPnL '+(u==null?'—':solTxt(u,true))+(s&&s.unpriced&&s.unpriced.length?' ·':'')}
  else{uEl.className='';uEl.textContent='no open position'}
  const r=a?a.realized_sol:0;
  const rEl=document.getElementById('pbBarR');rEl.className=clsPn(r);rEl.textContent='rPnL '+solTxt(r||0,true);
  const hint=document.getElementById('pbBarHint');
  if(hint)hint.textContent=PB.email?'saved to your sign-in · simulation · not advice':'saved in this browser · simulation · not advice'}

function ensurePanel(){
  const side=document.querySelector('#tokBody .tk-side');if(!side)return null;
  let el=document.getElementById('tkPaper');
  if(!el){el=document.createElement('div');el.id='tkPaper';el.className='card pb-card';side.insertBefore(el,side.firstChild)}
  return el}

function panelShell(){
  return `<div class="pb"><div class="pb-h"><h3>Paper trade</h3><span class="pb-tag">PAPER</span></div>
    <p class="pb-note">Play money only. Not financial advice. This cannot move real SOL, and it never asks for a wallet or a seed phrase.</p>
    <div class="pb-px"><span>Price <b id="pbPx">—</b></span><span>SOL <b id="pbSolUsd">—</b></span></div>
    <div class="pb-bal"><div><span>Cash</span><b id="pbCash">—</b></div><div><span>This token</span><b id="pbPos">—</b><small id="pbUpnl"></small></div></div>
    <div class="pb-dep"><label>Deposit paper SOL<input id="pbDep" data-depinput type="number" min="0" step="any" placeholder="10" inputmode="decimal"></label>
      <div class="pb-presets"><button type="button" data-dep="1">1</button><button type="button" data-dep="5">5</button><button type="button" data-dep="10">10</button><button type="button" data-dep="25">25</button></div>
      <button type="button" class="primary" id="pbDepGo" style="width:100%;margin-bottom:8px">Deposit</button></div>
    <div class="pb-tabs" role="tablist"><button type="button" data-pbt="buy">Buy</button><button type="button" data-pbt="sell">Sell</button><button type="button" data-pbt="limit">Limit</button></div>
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
    <button type="button" class="pb-sell" id="pbSell">Sell</button>`;
  if(t==='limit')return `<div class="pb-tabs" role="tablist"><button type="button" data-limside="buy">Buy limit</button><button type="button" data-limside="sell">Sell limit</button></div>
    <label>Trigger price, USD per token<input id="pbLim" type="number" min="0" step="any" inputmode="decimal"></label>
    <div id="pbLimAmt"></div>
    <button type="button" class="pb-limit" id="pbLimGo">Place limit</button>
    <p class="muted" style="font-size:11px;margin:8px 0 0">A buy limit reserves the SOL now and fills when the live price is at or under the trigger. A sell limit reserves tokens and fills at or above it. The fill then uses that live price, plus slippage and fees.</p>`;
  return `<label>Spend paper SOL<input id="pbSol" type="number" min="0" step="any" inputmode="decimal" placeholder="0.5"></label>
    <div class="pb-presets"><button type="button" data-sol="0.1">0.1</button><button type="button" data-sol="0.5">0.5</button><button type="button" data-sol="1">1</button><button type="button" data-sol="2">2</button></div>
    <button type="button" class="pb-buy" id="pbBuy">Buy</button>`}

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
  if(store)store.textContent=PB.email?'Signed in: this book is stored for your account on the server, and a copy stays in this browser. The server file is local to the instance.'
    :'Signed out: this book stays in this browser only (localStorage). Sign in with Google, when it is configured, to keep a copy on the server.';
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
  if(t.id==='paperBar'||t.id==='toMyPaper'||t.id==='pbOpenBook'){e.preventDefault();if(window.show)window.show('book');return}
  if(t.dataset.dep){const scope=t.closest('.pb,.card')||document;const input=scope.querySelector('[data-depinput]');if(input){input.value=t.dataset.dep;input.focus()}return}
  if(t.dataset.sol){const input=document.getElementById('pbSol');if(input){input.value=t.dataset.sol;scheduleEst()}return}
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
    return {time:g.time,position:both?'inBar':g.buy?'belowBar':'aboveBar',color:both?'#e6c35c':g.buy?'#00c703':'#e66565',size:2,
      shape:both?'circle':g.buy?'arrowUp':'arrowDown',
      text:(g.buy?('Buy'+(g.buy>1?' ×'+g.buy:'')):'')+(both?' · ':'')+(g.sell?('Sell'+(g.sell>1?' ×'+g.sell:'')):'')}})
  try{CH.cs.setMarkers(markers)}catch(e){}
  try{if(PB.entryLine)CH.cs.removePriceLine(PB.entryLine)}catch(e){}
  PB.entryLine=null;
  const pos=posOf(mint);
  if(pos&&pos.avg_px_usd>0){try{PB.entryLine=CH.cs.createPriceLine({price:pos.avg_px_usd,color:'#e6c35c',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'avg entry'})}catch(e){}}}

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
    if(j.signed_in&&j.account&&!PB.busy&&(j.account.updated_at||0)>((PB.acct&&PB.acct.updated_at)||0)){PB.acct=j.account;PB.summary=j.summary||PB.summary;saveLocal()}
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
  try{const me=await (await fetch('/api/me')).json();PB.email=me&&me.user&&me.user.email||null}catch(e){PB.email=null}
  let state={};try{state=await (await fetch('/api/paper/book')).json()}catch(e){}
  PB.sol=state.sol_usd||null;
  const local=read(key());
  if(PB.email&&state.account)PB.acct=state.account;
  else if(PB.email&&!state.account&&used(local)){try{const j=await act({action:'adopt',account:local},{silent:true});PB.acct=j.account}catch(e){PB.acct=local}}
  else PB.acct=local||state.blank||null;
  const anon=PB.email?read('xs_paper_book_v1:anon'):null;
  PB.offerAnon=!!(PB.email&&used(anon)&&!state.account&&!used(local));
  if(state.summary&&!PB.summary)PB.summary=state.summary;
  saveLocal();paintBar();updatePanelFigures();paintChart();
  const bar=document.getElementById('paperBar');if(bar)bar.hidden=false;
  const jump=document.getElementById('toMyPaper');if(jump)jump.addEventListener('click',ev=>{ev.preventDefault();if(window.show)window.show('book')});
  setInterval(()=>{pull();maybeMark(false)},20000);
  maybeMark(true)}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',boot);else boot();
})();
