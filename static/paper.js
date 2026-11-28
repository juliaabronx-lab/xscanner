// Paper book: play-money buys and sells. No wallet, no keys, no transactions.
// Prices come from the token page and /api/quotes (both already cached server-side).
// The fill math lives in papertrade.py; this file only previews, waits, and draws.
(function(){
'use strict';
const PB={acct:null,user:null,signed:false,csrf:'',sol:null,summary:null,token:null,quotes:{},tab:'buy',limSide:'buy',ptab:'P1',
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
function mcTxt(v){const n=+v;if(!isFinite(n)||n<=0)return'—';if(n>=1e9)return'$'+(n/1e9).toFixed(2)+'B';if(n>=1e6)return'$'+(n/1e6).toFixed(2)+'M';if(n>=1e3)return'$'+(n/1e3).toFixed(1)+'K';return '$'+n.toFixed(0)}
function posOf(mint){return PB.acct&&PB.acct.positions&&PB.acct.positions[mint]}
function rowOf(mint){return((PB.summary&&PB.summary.positions)||[]).find(r=>r.mint===mint)}
function livePx(mint){const q=PB.quotes[mint];if(q&&q.px&&Date.now()/1000-q.t<45)return q.px;
  if(PB.token&&PB.token.mint===mint&&PB.token.market&&PB.token.market.px)return+PB.token.market.px;
  if(PB.chartMint===mint&&typeof CH!=='undefined'){const raw=(CH.lastUsd!=null&&isFinite(+CH.lastUsd))?+CH.lastUsd:((CH.unit!=='mcap'&&CH.lastPx)?+CH.lastPx:null);if(raw>0)return raw}return q&&q.px||null}
function bookVisible(){const s=document.getElementById('v-book');return !!(s&&!s.classList.contains('hidden'))}
function saveLocal(){if(PB.acct)write(key(),PB.acct)}
function fillMsg(f){if(!f)return'Paper book updated.';
  if(f.side==='deposit')return'Deposited '+solTxt(f.sol)+' (play money).';
  if(f.side==='reset')return'Paper book reset.';
  if(f.side==='settings')return'Paper settings saved.';
  if(f.side==='cancel')return'Limit cancelled. Reserved paper funds are back in cash.';
  if(f.side==='limit')return'Limit placed. It fills only if the live price crosses it.';
  if(f.side==='buy'){const mc=PB.token&&PB.token.mint===f.mint&&PB.token.market&&PB.token.market.mc;return 'BUY: '+solTxt(f.sol)+' @ MC '+(mc?mcTxt(mc):pxTxt(f.fill_px_usd))}
  if(f.side==='sell'){const mc=PB.token&&PB.token.mint===f.mint&&PB.token.market&&PB.token.market.mc;return 'SELL @ MC '+(mc?mcTxt(mc):pxTxt(f.fill_px_usd))}
  return'Paper book updated.'}

async function refreshCsrf(){
  try{const d=await (await fetch('/api/auth/status',{credentials:'same-origin'})).json();if(d.csrf)PB.csrf=d.csrf;return d}catch(e){return null}}
// fix19: reuse the page's single /api/me bootstrap (chart.js) so one fresh session = one CSRF token (no first-click 403)
PB.csrfReady=window.xsMe?window.xsMe().then(({d})=>{if(d&&d.csrf)PB.csrf=d.csrf;return d}).catch(()=>refreshCsrf()):refreshCsrf();
/* fix19: right after page load the tick cache can be empty. Fetch one live quote instead of failing with
   "Live price is not available yet". */
async function ensurePx(mint){
  let px=livePx(mint);
  if(px>0&&PB.sol>0)return px;
  try{const r=await fetch('/api/tick/'+encodeURIComponent(mint),{credentials:'same-origin',cache:'no-store'});const j=await r.json().catch(()=>({}));
    if(j&&+j.px>0){PB.quotes[mint]={px:+j.px,t:Date.now()/1000};if(j.sol_usd>0)PB.sol=+j.sol_usd;px=+j.px}}catch(e){}
  if(!(PB.sol>0)&&window.XS_SOL>0)PB.sol=+window.XS_SOL;
  return px||null;
}
async function act(payload,opts,retried){
  opts=opts||{};
  const body=Object.assign({},payload);
  if(!body.rid)body.rid='r'+Math.random().toString(36).slice(2)+Date.now().toString(36);
  if(body.action==='adopt')body.account=payload.account;
  else if(!PB.signed)body.account=PB.acct;
  if(body.sol_usd==null&&PB.sol)body.sol_usd=PB.sol;
  try{await PB.csrfReady}catch(e){}
  if(!PB.csrf){PB.csrfReady=refreshCsrf();await PB.csrfReady}
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
function paintPnlCard(){
  let el=document.getElementById('pnlCard');
  if(!el){el=document.createElement('div');el.id='pnlCard';el.className='pnl-card';el.innerHTML='<div class="pnl-row"><div><span>BALANCE</span><b id="pnlBal">0</b></div><div><span>PNL</span><b id="pnlPnL">0</b></div></div><div class="pnl-pos" id="pnlPos"></div>';document.body.appendChild(el)}
  const show=onTokenPage();
  el.classList.toggle('hidden',!show);
  const a=PB.acct,cash=a?a.cash_sol:0,u=liveUnrealized()||0,r=a?a.realized_sol:0;
  const bal=document.getElementById('pnlBal'),pn=document.getElementById('pnlPnL');
  if(bal)bal.textContent=solTxt(cash);
  if(pn){const n=(u||0)+(r||0);pn.textContent=solTxt(n,true);pn.className=clsPn(n)}
  const pos=document.getElementById('pnlPos');
  if(pos&&a){const rows=Object.values(a.positions||{}).slice(0,5);
    pos.innerHTML=rows.map(p=>`<div>$${esc(p.symbol||'?')} ${fmtQty(p.qty)}</div>`).join('')+'<button type="button" class="linkbtn" id="pnlBook">Portfolio</button>';
    const b=document.getElementById('pnlBook');if(b)b.onclick=()=>{if(window.show)window.show('book')}}
}
function paintBar(){
  paintPnlCard();
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
    <div class="pb-ptabs" role="tablist" aria-label="Presets and advanced"><button type="button" data-pbp="P1">P1</button><button type="button" data-pbp="P2">P2</button><button type="button" data-pbp="P3">P3</button><button type="button" data-pbp="Adv">Adv</button></div>
    <div id="pbForm"></div>
    <p id="pbEst" class="pb-est"></p><p id="pbErr" class="pb-err"></p>
    <div id="pbMine" class="pb-orders"></div>
    <p class="pb-foot"><button type="button" class="linkbtn" id="pbOpenBook">Portfolio, history and calendar</button>
      <button type="button" class="linkbtn" id="pbReset">Reset</button>
      <button type="button" class="linkbtn" id="pbCard">PnL card</button></p></div>`}

function formHTML(){
  const t=PB.tab;
  if((PB.ptab||'P1')==='Adv')return `<div class="pb-adv">
      <label>Slippage %<input id="pbSlip" type="number" min="0" max="30" step="0.1"></label>
      <label>Priority fee (SOL)<input id="pbPri" type="number" min="0" max="0.05" step="0.0001"></label>
      <label>Platform fee %<input id="pbPlat" type="number" min="0" max="10" step="0.1"></label>
      <label>Simulated fill delay (ms)<input id="pbDelay" type="number" min="0" max="5000" step="50"></label>
      <button type="button" class="ghost" id="pbSetSave">Save settings</button>
      <p class="muted" style="margin:6px 0 0">A buy spends the SOL you type. The priority fee, then the platform fee, come out of it, and slippage worsens the fill. Sells take the same fees from the proceeds.</p>
    </div>`;
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
  const am=(typeof itAmounts==='function'?itAmounts():[0.1,0.5,1,2]);
  return `<label>AMOUNT<span class="sol-amt">${sm}<input id="pbSol" type="number" min="0" step="any" inputmode="decimal" placeholder="${am[0]||0.5}"></span></label>
    <div class="pb-presets">${am.map(v=>`<button type="button" data-sol="${v}">${sm}${v}</button>`).join('')}</div>
    <p class="pb-fee" id="pbFee"></p><button type="button" class="pb-buy" id="pbBuy">Paper Buy</button>`}

function paintForm(){
  const box=document.getElementById('pbForm');if(!box)return;
  const host=document.getElementById('tkPaper');
  if(host&&host.dataset.tab===PB.tab&&box.childElementCount)return;
  if(host)host.dataset.tab=PB.tab;
  box.innerHTML=formHTML();
  document.querySelectorAll('#tkPaper [data-pbt]').forEach(b=>b.classList.toggle('on',b.dataset.pbt===PB.tab));
  document.querySelectorAll('#tkPaper [data-pbp]').forEach(b=>b.classList.toggle('on',b.dataset.pbp===(PB.ptab||'P1')));
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
  toast(payload.action==='buy'?('Buying '+(payload.sol||'')+' SOL...'):('Selling '+(payload.pct||'')+'%...'));
  const delay=opts.delay!=null?Math.max(0,+opts.delay):(+settings().exec_delay_ms||0);
  const est=document.getElementById('pbEst');if(est)est.textContent=delay?`Simulating a ${delay} ms fill…`:'Filling…';
  try{
    if(delay)await sleep(delay);
    if(gen!==PB.seq)return;
    const px=livePx(payload.mint);if(px)payload.px_usd=px;payload.waited_ms=delay;payload.sol_usd=PB.sol;
    await act(payload);
  }finally{
    if(gen===PB.seq){PB.busy=false;updatePanelFigures();paintInstant();scheduleEst()}
    else{const buy=document.getElementById('pbBuy');if(buy&&buy.textContent==='Filling…'){buy.disabled=false;updatePanelFigures()}}
  }}

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
  root.innerHTML=`<div id="bkOffer"></div>
    <div class="pb-port">
      <div class="card"><h3>Balance</h3><div id="bkKpis"></div><button type="button" class="linkbtn" id="bkWallet">Wallet</button><div class="hidden" id="bkWalletBox"><div class="pb-row"><input id="bkDep" data-depinput type="number" min="0" step="any" placeholder="Amount" aria-label="Paper SOL to deposit"><button type="button" class="primary" id="bkDepGo">Deposit</button></div><p id="bkStore" class="muted"></p><button type="button" class="linkbtn" id="bkReset">Reset book</button></div></div>
      <div class="card"><h3>Realised PnL</h3><canvas id="bkChart" width="360" height="88"></canvas></div>
      <div class="card"><h3>Performance</h3><div id="bkBuckets"></div></div>
    </div>
    <div class="bk-tabs"><button type="button" data-bktab="pos" class="on">Active Positions</button><button type="button" data-bktab="hist">History</button></div>
    <div class="card" id="bkPosCard"><h3>Open positions</h3><div id="bkPos"></div></div>
    <div class="card hidden" id="bkHistCard"><h3>History <button type="button" class="ghost" id="bkCalBtn">PnL calendar</button></h3><div id="bkHist"></div></div>
    <div class="card"><h3>Limit orders</h3><div id="bkOrd"></div></div>
    <div id="bkStats" class="muted"></div>`}

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
    trades.map(t=>`<tr><td>${when(t.ts)}</td><td class="${t.side==='buy'?'up':'dn'}">${esc(t.side)}${t.kind==='limit'?' lim':''}</td><td><img class="tokico" alt="" src="${typeof imgUrl==='function'?imgUrl(t.mint,t.symbol||''):''}"><a href="/token/${esc(t.mint)}" data-tok="${esc(t.mint)}">$${esc(t.symbol||'?')}</a></td><td>${solTxt(t.sol)}</td><td>${pxTxt(t.fill_px_usd)}</td><td>${solTxt(t.fee_sol)}</td><td class="${clsPn(t.realized_sol)}">${t.realized_sol==null?'—':solTxt(t.realized_sol,true)}</td><td>${t.side==='sell'?`<button type="button" class="linkbtn" data-share="${esc(t.id)}">Card</button>`:''}</td></tr>`).join('')+'</tbody></table></div>'
    :'<p class="muted">No paper trades yet.</p>';
  paintBookChart();paintBuckets()}
function paintBookChart(){
  const c=document.getElementById('bkChart');if(!c)return;const g=c.getContext('2d');if(!g)return;
  const w=c.width,h=c.height;g.clearRect(0,0,w,h);
  const sells=(PB.acct&&PB.acct.trades||[]).filter(t=>t.side==='sell'&&t.realized_sol!=null).slice().sort((a,b)=>a.ts-b.ts);
  let y=0;const pts=sells.map(t=>{y+=+t.realized_sol;return y});if(!pts.length){g.fillStyle='#9ca3af';g.font='12px Inter,sans-serif';g.fillText('No realised trades yet',8,h/2);return}
  const min=Math.min(0,...pts),max=Math.max(0,...pts),span=(max-min)||1;
  g.beginPath();pts.forEach((v,i)=>{const x=i/(pts.length-1||1)*(w-8)+4,yy=h-8-((v-min)/span)*(h-16);i?g.lineTo(x,yy):g.moveTo(x,yy)});
  g.strokeStyle='#f04b92';g.lineWidth=2;g.stroke();
  g.lineTo(w-4,h);g.lineTo(4,h);g.closePath();g.fillStyle='rgba(240,75,146,.25)';g.fill()}
function paintBuckets(){
  const box=document.getElementById('bkBuckets');if(!box)return;
  const sells=(PB.acct&&PB.acct.trades||[]).filter(t=>t.side==='sell'&&t.pnl_pct!=null);
  const bands=[['>500',p=>p>500],['200–500',p=>p>200&&p<=500],['0–200',p=>p>=0&&p<=200],['0 to −50',p=>p<0&&p>=-50],['<−50',p=>p<-50]];
  box.innerHTML=bands.map(([name,fn])=>`<div>${name} <b>${sells.filter(t=>fn(+t.pnl_pct)).length}</b></div>`).join('')}

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
  document.addEventListener('click',e=>{if(e.target.closest('#calClose')){e.preventDefault();e.stopPropagation();setCalOpen(false)}},true);
  document.addEventListener('keydown',e=>{if(e.key==='Escape'&&CAL.open)setCalOpen(false)});
  /* fix19: click / tap outside the calendar closes it (its own toggle buttons excluded) */
  document.addEventListener('pointerdown',e=>{if(!CAL.open)return;const t=e.target;if(t&&t.closest&&t.closest('#calCard,#dockCal,#bkCalBtn'))return;setCalOpen(false)},true);

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
  const it=document.getElementById('itCard');
  const itOpen=it&&!it.classList.contains('hidden');
  el.style.left=(itOpen?16:Math.max(16,window.innerWidth-392))+'px';
  el.style.top=(itOpen?400:96)+'px';el.style.right='auto';el.style.bottom='auto';
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

function paintBg(g,kind){
  const W=1200,H=675;
  if(kind==='aurora'){const grd=g.createLinearGradient(0,0,W,H);grd.addColorStop(0,'#06281f');grd.addColorStop(.5,'#12324a');grd.addColorStop(1,'#2a1030');g.fillStyle=grd;g.fillRect(0,0,W,H);return}
  if(kind==='moon'){g.fillStyle='#070b16';g.fillRect(0,0,W,H);g.fillStyle='#f4efe4';g.beginPath();g.arc(980,140,70,0,6.3);g.fill();g.fillStyle='#070b16';g.beginPath();g.arc(1010,120,58,0,6.3);g.fill();return}
  if(kind==='candles'){g.fillStyle='#10131a';g.fillRect(0,0,W,H);for(let i=0;i<28;i++){const x=40+i*42,up=i%3!==1,h=40+((i*37)%160);g.fillStyle=up?'#37d6bd':'#f04b92';g.fillRect(x,200+(i%5)*18,16,h)}return}
  if(kind==='grid'){g.fillStyle='#0c1220';g.fillRect(0,0,W,H);g.strokeStyle='rgba(55,214,189,.25)';for(let x=0;x<W;x+=40){g.beginPath();g.moveTo(x,0);g.lineTo(x,H);g.stroke()}for(let y=0;y<H;y+=40){g.beginPath();g.moveTo(0,y);g.lineTo(W,y);g.stroke()}return}
  if(kind==='noise'){g.fillStyle='#14161c';g.fillRect(0,0,W,H);for(let i=0;i<1800;i++){g.fillStyle='rgba(255,255,255,'+(0.02+((i*17)%10)/80)+')';g.fillRect((i*97)%W,(i*53)%H,2,2)}return}
  const grd=g.createLinearGradient(0,0,0,H);grd.addColorStop(0,'#1b2436');grd.addColorStop(1,'#0a0c10');g.fillStyle=grd;g.fillRect(0,0,W,H)}
function drawCard(opts){
  const c=document.createElement('canvas');c.width=1200;c.height=675;const g=c.getContext('2d');if(!g)return c;
  paintBg(g,opts.bg||PB.shareBg||'gradient');
  const up=opts.pnlSol==null?(opts.pnlPct==null||opts.pnlPct>=0):opts.pnlSol>=0;
  const pill=up?'#3ee08f':'#f04b92';
  if(opts.showName!==false){g.fillStyle='#fff';g.font='600 28px Inter, system-ui, sans-serif';g.fillText('@'+(PB.user||'you'),64,70)}
  g.fillStyle='#fff';g.font='700 44px Inter, system-ui, sans-serif';g.fillText(opts.symbol||'TOKEN',64,140);
  const solLine=(opts.pnlSol==null?'0':(opts.pnlSol>=0?'+':'')+Number(opts.pnlSol).toFixed(3))+' SOL';
  g.font='700 64px Inter, system-ui, sans-serif';
  const tw=g.measureText(solLine).width;
  g.fillStyle=pill;g.beginPath();g.roundRect(64,180,tw+64,96,18);g.fill();
  g.fillStyle='#102018';g.fillText(solLine,96,248);
  g.fillStyle='#fff';g.font='500 28px Inter, system-ui, sans-serif';
  const pct=opts.pnlPct==null?'':((opts.pnlPct>=0?'+':'')+Number(opts.pnlPct).toFixed(1)+'%');
  g.fillText('PnL    '+pct,64,360);
  g.fillText('Invested    '+(opts.invested!=null?Number(opts.invested).toFixed(3):'—')+' SOL',64,410);
  g.fillText('Sold    '+(opts.sold!=null?Number(opts.sold).toFixed(3):'—')+' SOL',64,460);
  g.fillStyle='#d4d4d4';g.font='600 26px Inter, system-ui, sans-serif';g.fillText('xSCANNER',900,620);
  g.font='400 20px Inter, system-ui, sans-serif';g.fillStyle='#9ca3af';
  g.fillText('Simulation only. Not financial advice.',64,620);
  return c}

function openShare(opts){
  PB.shareOpts=opts;
  const c=drawCard(opts);
  let ov=document.getElementById('pbShare');
  if(!ov){ov=document.createElement('div');ov.id='pbShare';ov.innerHTML='<div class="pb-sharebox" role="dialog" aria-label="Paper PnL card"><img alt="Paper trading PnL card"><div class="pb-bgs" id="pbBgs"></div><div class="pb-sharerow"><button type="button" class="primary" id="pbShareDl">Download PNG</button><button type="button" class="ghost" id="pbShareCopy">Copy</button><button type="button" class="ghost" id="pbShareX">Close</button></div><p class="muted">Simulation only. Not financial advice.</p></div>';
    document.body.appendChild(ov);
    const bgs=ov.querySelector('#pbBgs');
    ['gradient','aurora','candles','moon','grid','noise'].forEach(k=>{const b=document.createElement('button');b.type='button';b.title=k;b.style.background=k==='aurora'?'linear-gradient(120deg,#06281f,#2a1030)':k==='moon'?'#070b16':k==='candles'?'#37d6bd':k==='grid'?'#0c1220':'#1b2436';b.onclick=()=>{PB.shareBg=k;if(PB.shareOpts)openShare(PB.shareOpts)};bgs.appendChild(b)});
    ov.addEventListener('click',e=>{if(e.target===ov||e.target.id==='pbShareX')ov.classList.remove('on')});
    document.getElementById('pbShareCopy').onclick=async()=>{try{const blob=await (await fetch(ov.querySelector('img').src)).blob();await navigator.clipboard.write([new ClipboardItem({'image/png':blob})]);toast('Copied the card.');}catch(err){toast('Copy is not available in this browser.','dn')}}}
  c.toBlob(blob=>{if(!blob){toast('Could not draw the card.','dn');return}
    if(PB.shareUrl)URL.revokeObjectURL(PB.shareUrl);PB.shareUrl=URL.createObjectURL(blob);
    ov.querySelector('img').src=PB.shareUrl;ov.classList.add('on');
    document.getElementById('pbShareDl').onclick=()=>{const a=document.createElement('a');a.href=PB.shareUrl;a.download='xscanner-paper-'+(opts.symbol||'pnl').replace(/[^A-Za-z0-9]+/g,'')+'.png';a.click()}})}

function mintFlows(mint){let invested=0,sold=0;for(const t of (PB.acct&&PB.acct.trades)||[]){if(t.mint!==mint)continue;if(t.side==='buy')invested+=+t.sol||0;else if(t.side==='sell')sold+=+t.sol||0}return{invested,sold}}
function sharePosition(mint){const p=posOf(mint),r=rowOf(mint);if(!p)return;const f=mintFlows(mint);
  openShare({symbol:p.symbol,pnlPct:r&&r.upnl_pct,pnlSol:r&&r.upnl_sol,invested:f.invested,sold:f.sold,entry:p.avg_px_usd,mark:r&&r.mark_px,label:'Unrealised'})}
function shareTrade(id){const t=((PB.acct&&PB.acct.trades)||[]).find(x=>x.id===id);if(!t)return;const f=mintFlows(t.mint);
  openShare({symbol:t.symbol,pnlPct:t.pnl_pct,pnlSol:t.realized_sol,invested:f.invested,sold:f.sold,entry:t.avg_px_usd,mark:t.fill_px_usd,label:t.side==='sell'?'Realised':'Fill',spent:t.side==='buy'?t.sol:null})}

let estT=0;
function scheduleEst(){clearTimeout(estT);estT=setTimeout(()=>{runEst().catch(()=>{})},280)}

async function onClick(e){
  const t=e.target.closest('button');if(!t)return;
  if(t.id==='paperBar'||t.id==='dockPaper'||t.id==='toMyPaper'||t.id==='pbOpenBook'){e.preventDefault();if(window.show)window.show('book');return}
  if(t.id==='bkWallet'){e.preventDefault();const box=document.getElementById('bkWalletBox');if(box)box.classList.toggle('hidden');return}
  if(t.dataset.bktab){e.preventDefault();const hist=t.dataset.bktab==='hist';const pc=document.getElementById('bkPosCard'),hc=document.getElementById('bkHistCard');if(pc)pc.classList.toggle('hidden',hist);if(hc)hc.classList.toggle('hidden',!hist);document.querySelectorAll('[data-bktab]').forEach(b=>b.classList.toggle('on',b===t));return}
  if(t.dataset.dep){const scope=t.closest('.pb,.card')||document;const input=scope.querySelector('[data-depinput]');if(input){input.value=t.dataset.dep;input.focus()}return}
  if(t.dataset.sol){const input=document.getElementById('pbSol');if(input){input.value=t.dataset.sol;scheduleEst()}return}
  if(t.dataset.pbt){if(PB.tab==='limit'||PB._mode==='limit'){PB.tab='limit';PB.limSide=t.dataset.pbt;PB._mode='limit'}else{PB.tab=t.dataset.pbt;PB._mode='market'}const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.pbmode){PB._mode=t.dataset.pbmode;if(PB._mode==='limit'){PB.limSide=PB.tab==='sell'?'sell':'buy';PB.tab='limit'}else{PB.tab=PB.limSide||PB.tab||'buy';if(PB.tab==='limit')PB.tab=PB.limSide||'buy'}const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.pbp){PB.ptab=t.dataset.pbp;if(PB.ptab!=='Adv'&&typeof IT!=='undefined'){IT.tab=PB.ptab;try{localStorage.setItem('xs_instant_preset_tab_v1',IT.tab)}catch(e){}}const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();scheduleEst();return}
  if(t.dataset.limside){PB.limSide=t.dataset.limside;const host=document.getElementById('tkPaper');if(host)host.dataset.tab='';paintForm();updatePanelFigures();return}
  if(t.dataset.pct&&t.dataset.go&&PB.token){const pct=+t.dataset.pct;await guard(()=>market(Object.assign({action:'sell',mint:PB.token.mint,pct,px_usd:livePx(PB.token.mint)},meta())));return}
  if(t.id==='pbBuy'&&PB.token){const inp=document.getElementById('pbSol')||{};let sol=+inp.value;
    if(!(sol>0))sol=+(inp.placeholder||0);   /* fix19: an empty box buys the amount shown as the placeholder, never sol:0 */
    if(!(sol>0)){toast('Enter a paper SOL amount.','dn');return}
    const mint=PB.token.mint;await guard(async()=>market(Object.assign({action:'buy',mint,sol,px_usd:await ensurePx(mint)},meta())));return}
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
  const series=CH.style==='line'?(CH.area||CH.cs):CH.cs;
  try{if(CH.cs)CH.cs.setMarkers([])}catch(e){}
  try{if(CH.area)CH.area.setMarkers([])}catch(e){}
  try{if(PB.entryLine){ (PB.entryHost||CH.cs).removePriceLine(PB.entryLine) }}catch(e){}
  PB.entryLine=null;PB.entryHost=null;
  const pos=posOf(mint);
  if(pos&&pos.avg_px_usd>0&&series){
    const scaled=(CH.unit==='mcap'&&typeof validSupply==='function'&&validSupply(CH.supply))||(CH.unit==='mcap'&&CH.supply);
    const px=scaled?pos.avg_px_usd*CH.supply:pos.avg_px_usd;
    try{PB.entryLine=series.createPriceLine({price:px,color:'#37d6bd',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Entry'});PB.entryHost=series}catch(e){}}
  try{if(PB.exitLine&&series){(PB.exitHost||series).removePriceLine(PB.exitLine)}}catch(e){}
  PB.exitLine=null;
  const sells=((PB.acct&&PB.acct.trades)||[]).filter(t=>t.mint===mint&&t.side==='sell'&&t.fill_px_usd>0);
  if(sells.length&&series){
    const avg=sells.reduce((s,t)=>s+(+t.fill_px_usd),0)/sells.length;
    const scaled=(CH.unit==='mcap'&&CH.supply);
    const ep=scaled?avg*CH.supply:avg;
    try{PB.exitLine=series.createPriceLine({price:ep,color:'#f04b92',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Exit'});PB.exitHost=series}catch(e){}}
  const lims=((PB.acct&&PB.acct.orders)||[]).filter(o=>o.mint===mint&&o.limit_px>0);
  try{if(PB.limLines)PB.limLines.forEach(ln=>{try{series.removePriceLine(ln)}catch(e){}})}catch(e){}
  PB.limLines=[];
  lims.forEach(o=>{const scaled=(CH.unit==='mcap'&&CH.supply);const lp=scaled?o.limit_px*CH.supply:o.limit_px;
    try{PB.limLines.push(series.createPriceLine({price:lp,color:'#e6b35c',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:'Limit'}))}catch(e){}})
  paintBadges(mint,series);
}
function paintBadges(mint,series){
  const parent=document.getElementById('chNative');
  if(!parent||typeof CH==='undefined'||!CH.chart||!series)return;
  let layer=document.getElementById('pbBadges');
  if(!layer){layer=document.createElement('div');layer.id='pbBadges';parent.appendChild(layer);
    if(!PB._badgeHook){PB._badgeHook=true;try{CH.chart.timeScale().subscribeVisibleLogicalRangeChange(()=>{if(CH.coin)paintBadges(CH.coin.mint,CH.style==='line'?(CH.area||CH.cs):CH.cs)})}catch(e){}}}
  const bits=[];
  for(const t of (PB.acct&&PB.acct.trades)||[]){
    if(t.mint!==mint||(t.side!=='buy'&&t.side!=='sell'))continue;
    const tm=snapTime(t.ts,PB.candles);if(tm==null)continue;
    const candle=(PB.candles||[]).find(k=>k.t===tm);
    const raw=candle?candle.c:(t.fill_px_usd||0);
    const py=typeof scalePx==='function'?scalePx(raw):raw;
    let x=null,y=null;
    try{x=CH.chart.timeScale().timeToCoordinate(tm);y=series.priceToCoordinate(py)}catch(e){}
    if(x==null||y==null)continue;
    const buy=t.side==='buy';
    const top=buy?y+8:y-28;
    bits.push(`<i class="pb-badge ${buy?'b':'s'}" style="left:${Math.round(x-10)}px;top:${Math.round(top)}px">${buy?'B':'S'}</i>`);
  }
  layer.innerHTML=bits.join('');
}

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
  try{const v=localStorage.getItem('xs_instant_open_v1');IT.open=v==null?true:v==='1'}catch(e){IT.open=true}
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
  const am=itAmounts();
  const st=settings();
  const cash=PB.acct?solTxt(PB.acct.cash_sol):'0';
  const mark=typeof solMark==='function'?solMark():'';
  const more=[2,3,5,10].filter(v=>am.indexOf(v)<0);
  return `<div class="it-head" data-itdrag="1">
      <span class="it-brand">xScanner</span>
      <span class="it-bal" id="itBal">${mark}<b>${cash}</b></span>
      <button type="button" class="it-ico" id="itLim" title="Limit orders" aria-label="Limit orders">⇄</button>
      <span class="it-gear"><button type="button" class="it-ico" id="itGear" title="Settings" aria-label="Settings">⚙</button>
        <div class="it-gearbox hidden" id="itGearBox">
          <label>Slip %<input id="itSlip" type="number" min="0" max="30" step="0.1" value="${st.slippage_bps/100}"></label>
          <label>Priority<input id="itPri" type="number" min="0" step="0.0001" value="${st.priority_fee_sol}"></label>
          <label>Delay ms<input id="itDelay" type="number" min="0" step="50" value="${IT.speed}"></label>
          <button type="button" class="ghost" id="itSetSave">Save</button>
          <button type="button" class="ghost" id="itEdit">Edit amounts</button>
        </div></span>
      <button type="button" class="it-ico" id="itClose" title="Close" aria-label="Close">×</button>
    </div>
    <div class="it-tabs" role="tablist">${['P1','P2','P3'].map(k=>`<button type="button" data-ittab="${k}" class="${k===IT.tab?'on':''}">${k}</button>`).join('')}</div>
    <div class="it-edit ${IT.editing?'':'hidden'}" id="itEditBox">
      <div class="it-edit-row">${[0,1,2,3].map(i=>`<input data-itedit="${i}" type="number" min="0" step="any" value="${am[i]}">`).join('')}</div>
      <button type="button" class="ghost" id="itEditSave">Save ${IT.tab}</button>
    </div>
    <div class="it-buy">
      <div class="it-rowlab"><b>Buy (SOL)</b><span class="it-mc" id="itEntry">Entry MC —</span></div>
      <div class="it-presets it-buy-presets">${am.map(v=>`<button type="button" data-itbuy="${v}">${v}</button>`).join('')}</div>
      <div class="it-more it-presets it-buy-presets">${more.map(v=>`<button type="button" data-itbuy="${v}">${v}</button>`).join('')}</div>
      <div class="it-meta"><span>${(st.slippage_bps/100)}% slip · ${st.priority_fee_sol} SOL</span></div>
    </div>
    <div class="it-sell">
      <div class="it-rowlab"><b>Sell (%)</b><span id="itHold" class="it-hold">0</span></div>
      <div class="it-presets it-sell-presets">${[10,25,50,100].map(p=>`<button type="button" data-itsell="${p}">${p}%</button>`).join('')}</div>
      <div class="it-meta"><button type="button" class="it-init hidden" id="itSellInit" data-itsellinit="1">Sell Init.</button><span class="it-mc" id="itExit">Exit MC —</span></div>
    </div>
    <div class="it-foot">
      <div class="it-kpis">
        <div class="it-kpi"><span>Bought</span><b id="itBought" class="buy">—</b></div>
        <div class="it-kpi"><span>Sold</span><b id="itSold" class="sell">—</b></div>
        <div class="it-kpi"><span>Holding</span><b id="itHolding">—</b></div>
        <div class="it-kpi"><span>PnL</span><b id="itPnl">—</b></div>
      </div>
      <div class="it-dep hidden" id="itDepBox"><input id="itDep" type="number" min="0" step="any" placeholder="10" aria-label="Paper SOL to deposit"><button type="button" class="ghost" id="itDepOk">Deposit</button><button type="button" class="linkbtn hidden" id="itDepGo">+ Deposit</button></div>
    </div>
    <div class="it-lim hidden" id="itLimBox">
      <h4>LIMIT ORDERS</h4>
      <div class="it-tabs"><button type="button" data-limside="buy" class="on">Buy</button><button type="button" data-limside="sell">Sell</button></div>
      <div class="it-presets it-buy-presets">${[10,25,50,100].map(p=>`<button type="button" data-limp="${p}">${p}%</button>`).join('')}</div>
      <input id="itLimPx" type="number" min="0" step="any" placeholder="Target MC" aria-label="Target market cap">
      <button type="button" class="it-place" id="itLimGo">Place Sell Order</button>
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
    if(t.id==='itGear'){e.preventDefault();const box=document.getElementById('itGearBox');if(box)box.classList.toggle('hidden');return}
    if(t.id==='itLim'){e.preventDefault();const box=document.getElementById('itLimBox');if(box)box.classList.toggle('hidden');return}
    if(t.id==='itSetSave'){e.preventDefault();const slip=document.getElementById('itSlip'),pri=document.getElementById('itPri'),del=document.getElementById('itDelay');
      const cur=Object.assign({},settings());if(slip)cur.slippage_bps=Math.round((+slip.value||0)*100);if(pri)cur.priority_fee_sol=+pri.value||0;if(del){IT.speed=+del.value||0;itSave('xs_instant_speed_v1',String(IT.speed));cur.exec_delay_ms=IT.speed}
      act({action:'settings',settings:cur}).then(()=>rebuildInstant()).catch(()=>{});return}
    if(t.id==='itLimGo'){e.preventDefault();placeLimitFromCard();return}
    if(t.dataset.limside){PB.limSide=t.dataset.limside;el.querySelectorAll('[data-limside]').forEach(b=>b.classList.toggle('on',b.dataset.limside===PB.limSide));const go=document.getElementById('itLimGo');if(go){go.textContent=PB.limSide==='buy'?'Place Buy Order':'Place Sell Order';go.classList.toggle('buy',PB.limSide==='buy')}return}
    if(t.dataset.limp){IT.limp=+t.dataset.limp;return}
    if(t.id==='itEdit'){e.preventDefault();IT.editing=!IT.editing;const box=document.getElementById('itEditBox');if(box)box.classList.toggle('hidden',!IT.editing);return}
    if(t.id==='itEditSave'){e.preventDefault();const next=[0,1,2,3].map(i=>{const inp=el.querySelector('[data-itedit="'+i+'"]');return +(inp&&inp.value)});if(next.some(v=>!(v>0))){toast('Preset amounts must be greater than 0 SOL.','dn');return}IT.presets[IT.tab]=next;itSave('xs_instant_presets_v1',IT.presets);IT.editing=false;rebuildInstant();return}
    if(t.dataset.ittab){IT.tab=t.dataset.ittab;itSave('xs_instant_preset_tab_v1',IT.tab);IT.editing=false;rebuildInstant();return}
    if(t.dataset.itspeed!=null){IT.speed=+t.dataset.itspeed||0;itSave('xs_instant_speed_v1',String(IT.speed));el.querySelectorAll('[data-itspeed]').forEach(b=>b.classList.toggle('on',+b.dataset.itspeed===IT.speed));return}
    if(t.id==='itDepGo'){e.preventDefault();const box=document.getElementById('itDepBox');if(box)box.classList.toggle('hidden');return}
    if(t.id==='itDepOk'){e.preventDefault();const inp=document.getElementById('itDep');doDeposit(inp);return}
    if(t.dataset.itbuy!=null){e.preventDefault();instantBuy(+t.dataset.itbuy).catch(()=>{});return}
    if(t.dataset.itsell!=null){e.preventDefault();instantSell(+t.dataset.itsell).catch(()=>{});return}
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
    el.classList.add('it-drag');
    const x=Math.max(8,Math.min(window.innerWidth-el.offsetWidth-8,e.clientX-IT.drag.dx));
    const y=Math.max(8,Math.min(window.innerHeight-el.offsetHeight-8,e.clientY-IT.drag.dy));
    el.style.left=x+'px';el.style.top=y+'px';el.style.right='auto';el.style.bottom='auto';
  });
  const end=e=>{
    if(!IT.drag)return;
    if(IT.drag.pid!=null&&e.pointerId!==IT.drag.pid&&e.type==='pointerup')return;
    el.classList.remove('it-drag');
    try{itSave('xs_instant_pos_v1',{left:el.style.left,top:el.style.top,width:el.style.width})}catch(err){}
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
function onTokenPage(){if(PB.nav&&PB.nav!=='token')return false;return /^\/token\//.test(location.pathname)||document.documentElement.dataset.view==='token'}
function defaultInstantPos(el){
  if(window.matchMedia('(max-width:700px)').matches){el.style.left='';el.style.top='';el.style.right='';el.style.bottom='';el.style.width='';return}
  try{
    const pos=JSON.parse(localStorage.getItem('xs_instant_pos_v1')||'null');
    if(pos&&pos.left&&pos.top){el.style.left=pos.left;el.style.top=pos.top;if(pos.width)el.style.width=pos.width;el.style.right='auto';el.style.bottom='auto'}
  }catch(e){}
  if(!el.style.left){
    const chart=document.querySelector('.chartcard')||document.querySelector('#chWrap');
    const w=290;
    let left=16,top=72;
    if(chart){const r=chart.getBoundingClientRect();left=Math.max(16,r.left+12);top=Math.max(64,r.top+12)}
    el.style.left=left+'px';el.style.top=top+'px';el.style.width=w+'px';el.style.right='auto';el.style.bottom='auto';
  }
  const w=el.offsetWidth||290,h=el.offsetHeight||320;
  const left=Math.max(8,Math.min(window.innerWidth-w-8,parseFloat(el.style.left)||16));
  const top=Math.max(8,Math.min(window.innerHeight-h-8,parseFloat(el.style.top)||72));
  el.style.left=left+'px';el.style.top=top+'px';
}
async function placeLimitFromCard(){
  const t=itTarget();if(!t||!t.mint){toast('Open a token first.','dn');return}
  const raw=+(document.getElementById('itLimPx')||{}).value;
  if(!(raw>0)){toast('Enter a target price.','dn');return}
  let px=raw;
  const mkt=PB.token&&PB.token.market;
  if(raw>1000&&mkt&&mkt.px>0&&mkt.mc>0)px=mkt.px*(raw/mkt.mc);
  const side=PB.limSide||'sell';
  const body={action:'limit',side,mint:t.mint,limit_px:px,symbol:t.symbol||'',name:t.name||'',pool:t.pool||'',px_usd:livePx(t.mint),sol_usd:PB.sol};
  if(side==='buy')body.sol=itAmounts()[0]||0.1;else body.pct=IT.limp||100;
  await act(body);
}
function setInstantOpen(on){
  IT.open=!!on;itSave('xs_instant_open_v1',IT.open?'1':'0');
  const el=ensureInstant();
  el.classList.toggle('hidden',!(IT.open&&onTokenPage()));
  el.setAttribute('aria-hidden',IT.open?'false':'true');
  if(IT.open&&onTokenPage()){defaultInstantPos(el);paintInstant()}
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
  el.classList.toggle('hidden',!(IT.open&&onTokenPage()));
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
  const bal=document.getElementById('itBal');
  if(bal)bal.innerHTML=(typeof solMark==='function'?solMark():'')+'<b>'+solTxt(PB.acct?PB.acct.cash_sol:0)+'</b>';
  const mc=PB.token&&PB.token.market&&PB.token.market.mc;
  const entry=document.getElementById('itEntry');if(entry)entry.textContent='Entry MC '+(mc?mcTxt(mc):'—');
  const exitMc=document.getElementById('itExit');if(exitMc)exitMc.textContent='Exit MC '+(mc?mcTxt(mc):'—');
  if(!el.style.left&&onTokenPage())defaultInstantPos(el);
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  const dep=document.getElementById('itDepGo');
  if(dep){dep.classList.toggle('hidden',cash>0);if(cash<=0)dep.textContent='+ Deposit paper SOL'}
  const box=document.getElementById('itDepBox');if(box)box.classList.toggle('hidden',cash>0);
  el.classList.toggle('it-empty',!mint);
}
async function instantBuy(sol){
  const t=itTarget();
  if(!t||!t.mint){toast('Open a token to paper-buy.','dn');return}
  if(!sol||sol<=0)return;
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  if(cash<=0){toast('Deposit paper SOL first. This is a simulation.','dn');const box=document.getElementById('itDepBox');const go=document.getElementById('itDepGo');if(go)go.classList.remove('hidden');if(box)box.classList.remove('hidden');return}
  const px=await ensurePx(t.mint);
  if(!(px>0)){toast('Live price is still loading. Try again in a second.','dn');return}
  try{await market(Object.assign({action:'buy',mint:t.mint,sol,px_usd:px}, {symbol:t.symbol||t.s||'',name:t.name||'',pool:t.pool||''}),{delay:IT.speed})}catch(e){/* toast already shown */}
}
async function instantSell(pct){
  const t=itTarget();
  if(!t||!t.mint){toast('Open a token to paper-sell.','dn');return}
  const pos=posOf(t.mint);
  if(!(pos&&pos.qty>0)){toast('No paper position in $'+(t.symbol||'TOKEN')+'.','dn');return}
  const px=await ensurePx(t.mint);
  if(!(px>0)){toast('Live price is still loading. Try again in a second.','dn');return}
  try{await market(Object.assign({action:'sell',mint:t.mint,pct,px_usd:px},{symbol:t.symbol||t.s||'',name:t.name||'',pool:t.pool||''}),{delay:IT.speed})}catch(e){/* toast already shown */}
}
async function instantSellInit(){
  const t=itTarget();if(!t||!t.mint)return;
  const pos=posOf(t.mint);if(!pos||!(pos.qty>0)){toast('No paper position to sell.','dn');return}
  const px=await ensurePx(t.mint),sol=PB.sol,st=settings(),cost=+(pos.cost_sol||0),qty=+(pos.qty||0);
  if(!(px>0)||!(sol>0)||!(qty>0)){toast('Need a live price to compute Sell Init.','dn');return}
  const slip=1-((st.slippage_bps||0)/10000);
  const feeBps=(st.platform_fee_bps||0)/10000;
  const pri=+(st.priority_fee_sol||0);
  const gross100=qty*(px*slip)/sol;
  const net100=gross100*(1-feeBps)-pri;
  if(!(net100>cost)){
    toast("Position isn't in profit yet — Sell Init needs profit above your initial cost",'dn');
    return;
  }
  const denom=gross100*(1-feeBps);
  let frac=denom>0?((cost+pri)/denom):1;
  const pct=Math.min(99,Math.max(1,frac*100));
  instantSell(pct);
}
document.addEventListener('keydown',e=>{
  if(!IT.open||e.metaKey||e.ctrlKey||e.altKey)return;
  const tag=(e.target&&e.target.tagName||'').toLowerCase();
  if(tag==='input'||tag==='textarea'||e.target.isContentEditable)return;
  const am=typeof itAmounts==='function'?itAmounts():[0.1,0.5,1,2];
  if(e.key>='1'&&e.key<='4'){e.preventDefault();const sol=am[+e.key-1];if(sol>0)instantBuy(sol).catch(()=>{});return}
  const sell={q:25,w:50,e:75,r:100}[e.key.toLowerCase()];
  if(sell){e.preventDefault();instantSell(sell).catch(()=>{})}
});
window.paperToggleInstant=function(force){
  itLoad();
  setInstantOpen(force==null?!IT.open:!!force);
};
window.paperSyncInstant=function(){itLoad();ensureInstant();setInstantOpen(IT.open)};
window.paperQuickBuy=async function(info){
  if(!info||!info.mint)return;
  IT.snap={mint:info.mint,symbol:info.symbol||'',name:info.name||'',pool:info.pool||'',px:info.px};
  if(info.px)PB.quotes[info.mint]={px:+info.px,t:Date.now()/1000};
  const sol=(info.sol>0)?+info.sol:((document.getElementById('pqAmt')&&+document.getElementById('pqAmt').value>0)?+document.getElementById('pqAmt').value:0.1);
  const cash=PB.acct?+(PB.acct.cash_sol||0):0;
  if(cash<=0){toast('Deposit paper SOL first to Quick Buy. Simulation only.','dn');if(window.show)window.show('book');return}
  const px=livePx(info.mint)||info.px||await ensurePx(info.mint);
  if(!(PB.sol>0))await ensurePx(info.mint);
  try{await market(Object.assign({action:'buy',mint:info.mint,sol,px_usd:px},{symbol:info.symbol||'',name:info.name||'',pool:info.pool||''}),{delay:0})}catch(e){}
};

function bindInstantChrome(){
  const dock=document.getElementById('dockInstant');
  if(dock&&!dock.dataset.bound){dock.dataset.bound='1';dock.addEventListener('click',e=>{e.preventDefault();window.paperToggleInstant()})}
  itLoad();ensureInstant();
  if(IT.open)setInstantOpen(true);
  else setInstantOpen(false);
}


window.paperPosition=function(mint){if(!mint||!PB.acct)return null;return{pos:(PB.acct.positions||{})[mint]||null,row:((PB.summary&&PB.summary.positions)||[]).find(r=>r.mint===mint)||null}};
window.paperOnToken=function(d){if(!d||!d.mint)return;PB.nav='token';PB.token=d;
  IT.snap={mint:d.mint,symbol:d.symbol||'',name:d.name||'',pool:d.pool||'',px:d.market&&d.market.px};
  if(d.market&&d.market.px)PB.quotes[d.mint]={px:+d.market.px,t:Date.now()/1000};
  const el=ensurePanel();if(!el)return;
  if(PB.panelMint!==d.mint||!el.querySelector('.pb')){PB.panelMint=d.mint;PB.tab='buy';el.dataset.tab='';el.innerHTML=panelShell();paintForm()}
  updatePanelFigures();paintChart();paintInstant();
  if((PB.acct&&PB.acct.orders||[]).some(o=>o.mint===d.mint)){clearTimeout(PB.markSoon);PB.markSoon=setTimeout(()=>maybeMark(true),600)}};

function ensureLocalBook(){
  if(PB.signed)return;
  const loc=read('xs_paper_book_v1:anon');
  if(used(loc)&&!used(PB.acct)){PB.acct=loc;try{paintBar();paintInstant();if(bookVisible())fillBook()}catch(e){}}
}
window.paperOnNavigate=function(v){
  if(v)PB.nav=v;
  try{if(typeof setCalOpen==='function')setCalOpen(false)}catch(e){}
  ensureLocalBook();
  const el=document.getElementById('itCard');
  if(el)el.classList.toggle('hidden',!(IT.open&&onTokenPage()));
  const pnl=document.getElementById('pnlCard');
  if(pnl)pnl.classList.toggle('hidden',!onTokenPage());
};
window.paperEnsureCsrf=function(){return PB.csrf?Promise.resolve(PB.csrf):(PB.csrfReady=refreshCsrf())};
window.paperLeaveToken=function(){try{if(typeof setCalOpen==='function')setCalOpen(false)}catch(e){}PB.token=null;PB.entryLine=null;paintInstant()};

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
  const local=read(key())||(!PB.signed&&read('xs_paper_book_v1:anon'));
  if(PB.signed&&state.account)PB.acct=state.account;
  else if(PB.signed&&!state.account&&used(local)){try{const j=await act({action:'adopt',account:local},{silent:true});PB.acct=j.account}catch(e){PB.acct=local}}
  else if(used(local))PB.acct=local;
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
(function paperWrapShow(){
  const hook=()=>{const s=window.show;if(!s||s._paperWrapped)return;const w=function(){try{window.paperOnNavigate&&window.paperOnNavigate.apply(null,arguments)}catch(e){}return s.apply(this,arguments)};w._paperWrapped=true;window.show=w};
  hook();document.addEventListener('DOMContentLoaded',hook);
})();
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
