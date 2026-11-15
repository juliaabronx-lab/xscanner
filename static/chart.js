// Live coin chart for the detail drawer: GeckoTerminal OHLCV via /api/ohlcv (cached backend proxy),
// drawn with TradingView lightweight-charts; DexScreener embed as a toggle / fallback. Market data only.
const CH={tf:localStorage.getItem('chartTf')||'15m',mode:'native',coin:null,chart:null,cs:null,vs:null,timer:null,ro:null,seq:0,lastT:null,fit:true};
const TFS=['1m','5m','15m','1h','4h','1d'];
const fmtPx=v=>{if(v==null||!isFinite(v))return'—';const a=Math.abs(v);if(a===0)return'$0';if(a>=1)return'$'+v.toLocaleString(undefined,{maximumFractionDigits:4});
  if(a>=0.0001)return'$'+v.toPrecision(4);const z=Math.floor(-Math.log10(a));const sig=(a*Math.pow(10,z+3)).toFixed(0);return`$0.0<sub>${z-1}</sub>${sig}`};
const fmtAxis=v=>{const a=Math.abs(v);if(a>=1)return v.toFixed(4);if(a<1e-12)return'0';return v.toPrecision(3)};
function chartBlockHTML(){
  return `<div class="chartcard"><div class="chhead">
    <div class="chpx"><b id="chPrice">—</b> <span id="chChg"></span><small id="chUpd" class="muted"></small></div>
    <div class="chctl"><div class="tfs">${TFS.map(t=>`<button data-tf="${t}" class="${t===CH.tf?'on':''}">${t}</button>`).join('')}</div>
    <div class="tfs"><button data-mode="native" class="on">Chart</button><button data-mode="dex">DexScreener</button></div></div></div>
    <div id="chWrap" class="chwrap"><div id="chNative" class="chnative"></div><div id="chMsg" class="chmsg hidden"></div><div id="chDex" class="chdex off"></div></div>
    <div class="chfoot muted"><span id="chSrc">Candles: GeckoTerminal · refreshes every 15s while open</span></div></div>`;
}
function chMsg(t){const m=document.getElementById('chMsg');if(!m)return;if(!t){m.classList.add('hidden');return}m.classList.remove('hidden');m.innerHTML=t}
function setMode(mode){
  CH.mode=mode;document.querySelectorAll('.chartcard [data-mode]').forEach(b=>b.classList.toggle('on',b.dataset.mode===mode));
  const dex=document.getElementById('chDex'),nat=document.getElementById('chNative');if(!dex)return;
  if(mode==='dex'){nat.classList.add('off');nat.classList.remove('hidden');chMsg('');dex.classList.remove('off','hidden');
    if(!dex.firstChild&&CH.coin?.pool)dex.innerHTML=`<iframe src="https://dexscreener.com/solana/${encodeURIComponent(CH.coin.pool)}?embed=1&theme=dark&info=0&trades=0" loading="lazy" allow="clipboard-write"></iframe>`;
  }else{dex.classList.add('off');dex.classList.remove('hidden');nat.classList.remove('off','hidden');load(true)}
}
function mountChart(c){
  unmountChart();CH.coin=c;CH.lastT=null;CH.lastPx=null;CH.fit=true;
  document.querySelectorAll('.chartcard [data-tf]').forEach(b=>b.onclick=()=>{CH.tf=b.dataset.tf;localStorage.setItem('chartTf',CH.tf);
    document.querySelectorAll('.chartcard [data-tf]').forEach(x=>x.classList.toggle('on',x===b));CH.fit=true;CH.lastT=null;if(CH.mode!=='native')setMode('native');else load(true)});
  document.querySelectorAll('.chartcard [data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
  if(!c.pool){chMsg('No trading pair known for this coin, so there is no chart yet.');return}
  const el=document.getElementById('chNative');
  if(!window.LightweightCharts){chMsg('Chart library failed to load. Showing the DexScreener chart instead.');setMode('dex');return}
  const LC=window.LightweightCharts;
  CH.chart=LC.createChart(el,{width:el.clientWidth,height:300,layout:{background:{type:'solid',color:'#12151d'},textColor:'#8a91a6',fontSize:11},
    grid:{vertLines:{color:'#1b2030'},horzLines:{color:'#1b2030'}},rightPriceScale:{borderColor:'#232838'},
    timeScale:{borderColor:'#232838',timeVisible:true,secondsVisible:false},crosshair:{mode:0},
    localization:{priceFormatter:fmtAxis,timeFormatter:t=>new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}});
  CH.cs=CH.chart.addCandlestickSeries({upColor:'#2ee59d',downColor:'#ff5c7a',borderVisible:false,wickUpColor:'#2ee59d',wickDownColor:'#ff5c7a',
    priceFormat:{type:'custom',formatter:fmtAxis,minMove:1e-12}});
  CH.vs=CH.chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
  CH.chart.priceScale('vol').applyOptions({scaleMargins:{top:0.8,bottom:0},visible:false});
  CH.cs.priceScale().applyOptions({scaleMargins:{top:0.08,bottom:0.24}});
  CH.ro=new ResizeObserver(()=>CH.chart&&CH.chart.applyOptions({width:el.clientWidth}));CH.ro.observe(el);
  load(true);
  CH.timer=setInterval(()=>{if(!document.hidden&&CH.mode==='native')load(false)},15000);
}
function unmountChart(){clearInterval(CH.timer);CH.timer=null;CH.ro?.disconnect();CH.ro=null;try{CH.chart?.remove()}catch(e){}CH.chart=CH.cs=CH.vs=null;CH.coin=null;CH.lastPx=null;CH.seq++}
async function load(showLoading){
  const c=CH.coin;if(!c||!c.pool||!CH.cs)return;const seq=++CH.seq,tf=CH.tf;
  if(showLoading&&!CH.lastT)chMsg('<span class="spin"></span> Loading chart…');
  let j,r;
  try{r=await fetch(`/api/ohlcv/${encodeURIComponent(c.pool)}?tf=${tf}&token=${encodeURIComponent(c.mint||'')}`);j=await r.json().catch(()=>({}))}
  catch(e){if(seq===CH.seq&&!CH.lastT)chMsg('Could not reach the server for chart data. Retrying in 15s.');return}
  if(seq!==CH.seq||!CH.cs)return;
  if(!r.ok){
    if(CH.lastT){document.getElementById('chUpd').textContent=' · '+(j.detail||'refresh failed')+' — keeping last data';return}
    if(r.status===404)chMsg(`${esc(j.detail||'No chart data for this pair yet.')} <a href="#" onclick="setMode('dex');return false">Try the DexScreener chart</a>`);
    else{if(r.status===429){const k=CH.seq;setTimeout(()=>{if(k===CH.seq)load(false)},6000)}chMsg(`${esc(j.detail||'Chart unavailable')} <a href="#" onclick="setMode('dex');return false">Use the DexScreener chart</a>`)}
    return}
  const cd=j.candles||[];
  if(!cd.length){chMsg(`No trades in this timeframe yet. Try a longer timeframe or <a href="#" onclick="setMode('dex');return false">the DexScreener chart</a>.`);CH.cs.setData([]);CH.vs.setData([]);return}
  chMsg('');
  CH.cs.setData(cd.map(k=>({time:k.t,open:k.o,high:k.h,low:k.l,close:k.c})));
  CH.vs.setData(cd.map(k=>({time:k.t,value:k.v,color:k.c>=k.o?'#2ee59d55':'#ff5c7a55'})));
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
  const ft=j.fetched_at?new Date(j.fetched_at*1000):new Date();
  document.getElementById('chUpd').textContent=' · updated '+ft.toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT'+(j.stale?' (delayed)':'');
}
