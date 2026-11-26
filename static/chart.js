// Live coin chart for the detail drawer: GeckoTerminal OHLCV via /api/ohlcv (cached backend proxy),
// drawn with TradingView lightweight-charts; DexScreener embed as a toggle / fallback. Market data only.
const CH={tf:localStorage.getItem('chartTf')||'15m',mode:'native',coin:null,chart:null,cs:null,vs:null,timer:null,ro:null,seq:0,lastT:null,fit:true};
const TFS=['1m','5m','15m','1h','4h','1d'];
// tiny prices: subscript-zero notation, e.g. 0.0000234 -> $0.0₄234 (4 zeros after the point), plain text (no HTML)
const SUBS='₀₁₂₃₄₅₆₇₈₉';
const tinyNum=a=>{const z=-Math.floor(Math.log10(a))-1;let sig=Math.round(a*Math.pow(10,z+3));let zz=z;if(sig>=10000){sig=Math.round(sig/10);zz--}
  return zz<=3?a.toFixed(zz+3+1).replace(/0+$/,''):'0.0'+String(zz).split('').map(d=>SUBS[d]).join('')+String(sig).replace(/0+$/,'')};
const fmtNum=v=>{if(v==null||!isFinite(v))return'—';const a=Math.abs(v);if(a===0)return'0';if(a>=1)return v.toLocaleString('en-US',{maximumFractionDigits:a>=1000?2:4});
  if(a>=0.01)return String(parseFloat(v.toPrecision(4)));return (v<0?'-':'')+tinyNum(a)};
const fmtPx=v=>{const t=fmtNum(v);return t==='—'?t:'$'+t};
const fmtAxis=v=>v<0?'':fmtNum(v);   // price can't be negative: hide ticks below 0
function chartBlockHTML(){
  return `<div class="chartcard"><div class="chhead">
    <div class="chpx"><b id="chPrice">—</b> <span id="chChg"></span><small id="chUpd" class="muted"></small></div>
    <div class="chctl"><div class="tfs">${TFS.map(t=>`<button data-tf="${t}" class="${t===CH.tf?'on':''}">${t}</button>`).join('')}</div>
    <div class="tfs"><button data-mode="native" class="on">Chart</button><button data-mode="dex">DexScreener</button></div></div></div>
    <div id="chWrap" class="chwrap"><div id="chNative" class="chnative"></div><div id="chMsg" class="chmsg hidden"></div><div id="chDex" class="chdex off"></div></div>
    <div class="chfoot muted"><span id="chSrc">Candles: GeckoTerminal · refreshes every 15s while open</span> <span id="chLim" class="chlim hidden" title="">Limited history</span></div></div>`;
}
// same rules as the server (chart.py sanitize): low <= min(open,close), high >= max(open,close), drop >10x spikes vs neighbours
function saneCandles(cs){
  let a=(cs||[]).filter(k=>k&&[k.o,k.h,k.l,k.c].every(x=>typeof x==='number'&&isFinite(x)&&x>0));
  const med=xs=>{const s=[...xs].sort((p,q)=>p-q),n=s.length;return n?(n%2?s[(n-1)/2]:(s[n/2-1]+s[n/2])/2):null};
  if(a.length>=5)a=a.filter((k,i)=>{const nb=[];for(let j=Math.max(0,i-3);j<Math.min(a.length,i+4);j++)if(j!==i)nb.push(a[j].c);const m=med(nb);
    if(m&&(Math.min(k.o,k.c)>m*10||Math.max(k.o,k.c)<m/10))return false;if(m&&k.h>m*10)k.h=Math.max(k.o,k.c);if(m&&k.l<m/10)k.l=Math.min(k.o,k.c);return true});
  return a.map(k=>({...k,h:Math.max(k.h,k.o,k.c,k.l),l:Math.min(k.l,k.o,k.c,k.h)}));
}
function chMsg(t){const m=document.getElementById('chMsg');if(!m)return;if(!t){m.classList.add('hidden');return}m.classList.remove('hidden');m.innerHTML=t}
function setMode(mode){
  CH.mode=mode;document.querySelectorAll('.chartcard [data-mode]').forEach(b=>b.classList.toggle('on',b.dataset.mode===mode));
  const dex=document.getElementById('chDex'),nat=document.getElementById('chNative');if(!dex)return;
  if(mode==='dex'){nat.classList.add('off');nat.classList.remove('hidden');chMsg('');dex.classList.remove('off','hidden');
    if(!dex.firstChild&&CH.coin?.pool)dex.innerHTML=`<iframe src="https://dexscreener.com/solana/${encodeURIComponent(CH.coin.pool)}?embed=1&theme=dark&info=0&trades=0" loading="lazy" allow="clipboard-write"></iframe>`;
  }else{dex.classList.add('off');dex.classList.remove('hidden');nat.classList.remove('off','hidden');load(true)}
}
let _lcLoad=null;
function loadScript(src){return new Promise(res=>{const s=document.createElement('script');s.src=src;s.onload=()=>res(!!window.LightweightCharts);s.onerror=()=>res(false);document.head.appendChild(s)})}
function ensureCharts(){
  if(window.LightweightCharts) return Promise.resolve(true);
  if(_lcLoad) return _lcLoad;
  _lcLoad=loadScript('/static/vendor/lightweight-charts.js?v=4.2.3').then(ok=>ok?true:loadScript('https://cdn.jsdelivr.net/npm/lightweight-charts@4.2.3/dist/lightweight-charts.standalone.production.js'));
  return _lcLoad;
}
function mountChart(c){
  unmountChart();CH.coin=c;CH.lastT=null;CH.lastPx=null;CH.fit=true;
  document.querySelectorAll('.chartcard [data-tf]').forEach(b=>b.onclick=()=>{CH.tf=b.dataset.tf;localStorage.setItem('chartTf',CH.tf);
    document.querySelectorAll('.chartcard [data-tf]').forEach(x=>x.classList.toggle('on',x===b));CH.fit=true;CH.lastT=null;if(CH.mode!=='native')setMode('native');else load(true)});
  document.querySelectorAll('.chartcard [data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
  if(!c.pool){chMsg('No trading pair known for this coin, so there is no chart yet.');return}
  const el=document.getElementById('chNative');
  if(!window.LightweightCharts){chMsg('<span class="spin"></span> Loading chart…');ensureCharts().then(ok=>{if(CH.coin!==c)return;if(!ok||!window.LightweightCharts){chMsg('Chart library failed to load. Showing the DexScreener chart instead.');setMode('dex');return}mountChart(c)});return}
  const LC=window.LightweightCharts;
  CH.chart=LC.createChart(el,{width:el.clientWidth,height:CH.h||300,layout:{background:{type:'solid',color:'#0a0a0a'},textColor:'#7d8187',fontFamily:'Geist, system-ui, sans-serif',fontSize:11},
    grid:{vertLines:{color:'#1f2228'},horzLines:{color:'#1f2228'}},rightPriceScale:{borderColor:'#1f2228'},
    timeScale:{borderColor:'#1f2228',timeVisible:true,secondsVisible:false},crosshair:{mode:0},
    localization:{priceFormatter:fmtAxis,timeFormatter:t=>new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}});
  CH.cs=CH.chart.addCandlestickSeries({upColor:'#00c703',downColor:'#e66565',borderVisible:false,wickUpColor:'#00c703',wickDownColor:'#e66565',
    priceFormat:{type:'custom',formatter:fmtAxis,minMove:1e-12},
    autoscaleInfoProvider:orig=>{const r=orig();if(r&&r.priceRange&&r.priceRange.minValue<0)r.priceRange.minValue=0;return r}});
  CH.vs=CH.chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
  CH.chart.priceScale('vol').applyOptions({scaleMargins:{top:0.8,bottom:0},visible:false});
  CH.cs.priceScale().applyOptions({scaleMargins:{top:0.08,bottom:0.24}});
  CH.ro=new ResizeObserver(()=>CH.chart&&CH.chart.applyOptions({width:el.clientWidth}));CH.ro.observe(el);
  load(true);
  CH.timer=setInterval(()=>{if(!document.hidden&&CH.mode==='native')load(false)},15000);
}
function unmountChart(){clearInterval(CH.timer);CH.timer=null;CH.ro?.disconnect();CH.ro=null;try{CH.chart?.remove()}catch(e){}CH.chart=CH.cs=CH.vs=null;CH.coin=null;CH.lastPx=null;CH.seq++}
// Browser-side fallback: GeckoTerminal allows CORS, so when the server's shared IP is rate-limited
// the visitor's own browser fetches the candles directly (their own free quota). Market data only.
const GT_TF={'1m':['minute',1],'5m':['minute',5],'15m':['minute',15],'1h':['hour',1],'4h':['hour',4],'1d':['day',1]};
async function directGT(c,tf){
  const [p,a]=GT_TF[tf]||GT_TF['5m'];
  const u=`https://api.geckoterminal.com/api/v2/networks/solana/pools/${encodeURIComponent(c.pool)}/ohlcv/${p}?aggregate=${a}&limit=300&currency=usd`+(c.mint?`&token=${encodeURIComponent(c.mint)}`:'');
  const r=await fetch(u,{headers:{Accept:'application/json;version=20230302'}});if(!r.ok)return null;
  const d=await r.json();const rows=(((d||{}).data||{}).attributes||{}).ohlcv_list||[];const m={};
  for(const x of rows)if(Array.isArray(x)&&x.length>=6&&x[4]!=null)m[x[0]]={t:+x[0],o:x[1],h:x[2],l:x[3],c:x[4],v:x[5]};
  return {candles:Object.values(m).sort((a,b)=>a.t-b.t),source:'geckoterminal-direct'};
}
async function load(showLoading){
  const c=CH.coin;if(!c||!c.pool||!CH.cs)return;const seq=++CH.seq,tf=CH.tf;
  if(!CH.lastT)chMsg('<span class="spin"></span> '+(showLoading?'Loading chart…':'Chart loading…'));
  let j={},r=null,ok=false;
  for(let i=0;i<4 && seq===CH.seq;i++){
    try{r=await fetch(`/api/ohlcv/${encodeURIComponent(c.pool)}?tf=${tf}&token=${encodeURIComponent(c.mint||'')}`);j=await r.json().catch(()=>({}))}
    catch(e){r=null;j={}}
    if(r&&r.ok){ok=true;break}
    if(r&&[429,502,503].includes(r.status)){
      try{const d=await directGT(c,tf);if(d&&d.candles&&d.candles.length){j=d;r={ok:true,status:200};ok=true;break}}catch(e){}
    }
    if(r&&r.status===404)break;
    if(!CH.lastT)chMsg('<span class="spin"></span> Chart loading…');
    else{const u=document.getElementById('chUpd');if(u)u.textContent=' · retrying… — keeping last data'}
    await new Promise(res=>setTimeout(res,1500*(i+1)));
  }
  if(seq!==CH.seq||!CH.cs)return;
  // the server only had approximate / snapshot candles (its shared IP is rate-limited): try the viewer's own GeckoTerminal quota for real ones
  if(ok&&(j.source==='derived'||j.source==='snapshots')&&Date.now()>(CH.dgUntil||0)){
    try{const d=await directGT(c,tf);if(d&&d.candles&&d.candles.length>2){j=d}else CH.dgUntil=Date.now()+60000}catch(e){CH.dgUntil=Date.now()+60000}
    if(seq!==CH.seq||!CH.cs)return;
  }
  if(!ok){
    if(CH.lastT){document.getElementById('chUpd').textContent=' · '+(j.detail||'still loading')+' — keeping last data';return}
    if(r&&r.status===404)chMsg(`${esc(j.detail||'No chart data for this pair yet.')} <a href="#" onclick="setMode('dex');return false">Try the DexScreener chart</a>`);
    else{
      const k=CH.seq;setTimeout(()=>{if(k===CH.seq)load(false)},8000);
      chMsg(`<span class="spin"></span> Chart loading… <a href="#" onclick="setMode('dex');return false">Use the DexScreener chart</a>`);
    }
    return}
  const cd=saneCandles(j.candles||[]);
  if(!cd.length){chMsg(`No trades in this timeframe yet. Try a longer timeframe or <a href="#" onclick="setMode('dex');return false">the DexScreener chart</a>.`);CH.cs.setData([]);CH.vs.setData([]);return}
  chMsg('');
  CH.cs.setData(cd.map(k=>({time:k.t,open:k.o,high:k.h,low:k.l,close:k.c})));
  CH.vs.setData(cd.map(k=>({time:k.t,value:k.v,color:k.c>=k.o?'#00c70355':'#e6656555'})));
  if(CH.fit){CH.chart.timeScale().fitContent();CH.fit=false}
  CH.lastT=cd[cd.length-1].t;
  const last=cd[cd.length-1].c,first=cd[0].o,chg=first?((last-first)/first*100):null;
  const priceEl=document.getElementById('chPrice');
  const prevPx=CH.lastPx;
  const simple=isFinite(last)&&Math.abs(last)>=1e-4&&(prevPx==null||Math.abs(prevPx)>=1e-4);
  if(prevPx!=null&&prevPx!==last&&simple&&window.XS&&XS.motionOn()){
    priceEl.dataset.v=prevPx;
    XS.tick(priceEl,last,v=>fmtPx(v));
    XS.flash(priceEl);
  }else{priceEl.innerHTML=fmtPx(last)}
  CH.lastPx=last;
  const winH=(cd[cd.length-1].t-cd[0].t)/3600, winTxt=winH<48?Math.max(1,Math.round(winH))+'h':Math.round(winH/24)+'d';
  document.getElementById('chChg').innerHTML=chg==null?'':`${pct(chg)} <small class="muted">over ${winTxt}</small>`;
  const srcEl=document.getElementById('chSrc');
  if(srcEl){const nm={jupiter:'Jupiter (real candles)',geckoterminal:'GeckoTerminal','geckoterminal-direct':'GeckoTerminal (your browser)','geckoterminal+snapshots':'GeckoTerminal + our price snapshots',snapshots:'our own price snapshots (no volume)',derived:'approximate, from DexScreener 5m/1h/24h changes'}[j.source]||j.source||'GeckoTerminal';
    srcEl.textContent='Candles: '+nm+(j.approximate?' · approximate':'')+' · refreshes every 15s while open'}
  const lim=document.getElementById('chLim');
  if(lim){const on=!!j.limited||j.source==='derived'||j.source==='snapshots';lim.classList.toggle('hidden',!on);
    lim.title=on?(j.limited_reason||'Exchange candles are rate-limited right now, so this chart is built from our own price snapshots (no volume).'):''}
  const ft=j.fetched_at?new Date(j.fetched_at*1000):new Date();
  document.getElementById('chUpd').textContent=' · updated '+ft.toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT'+(j.stale?' (delayed)':'');
  try{document.dispatchEvent(new CustomEvent('xschart',{detail:{mint:c.mint,last:last,candles:cd}}))}catch(e){}
}
