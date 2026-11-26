// Live coin chart for the detail view: /api/ohlcv (cached backend proxy) drawn with TradingView
// lightweight-charts; DexScreener embed as a toggle / fallback. Market data only. No Axiom assets.
const CH={tf:localStorage.getItem('chartTf')||'15m',mode:'native',coin:null,chart:null,cs:null,vs:null,ls:null,timer:null,ro:null,seq:0,lastT:null,fit:true,
  style:'candles',unit:'px',tool:'cross',magnet:true,log:false,drawings:[],lines:[],pending:null,supply:null,clock:null,lastBars:[]};
const TFS=['1m','5m','15m','1h','4h','1d'];
const UP='#26a69a', DN='#ef5350', GRID='#161920', BG='#0b0c0f';
const SUBS='₀₁₂₃₄₅₆₇₈₉';
const tinyNum=a=>{const z=-Math.floor(Math.log10(a))-1;let sig=Math.round(a*Math.pow(10,z+3));let zz=z;if(sig>=10000){sig=Math.round(sig/10);zz--}
  return zz<=3?a.toFixed(zz+3+1).replace(/0+$/,''):'0.0'+String(zz).split('').map(d=>SUBS[d]).join('')+String(sig).replace(/0+$/,'')};
const fmtNum=v=>{if(v==null||!isFinite(v))return'—';const a=Math.abs(v);if(a===0)return'0';if(a>=1)return v.toLocaleString('en-US',{maximumFractionDigits:a>=1000?2:4});
  if(a>=0.01)return String(parseFloat(v.toPrecision(4)));return (v<0?'-':'')+tinyNum(a)};
const fmtPx=v=>{const t=fmtNum(v);return t==='—'?t:'$'+t};
const fmtAxis=v=>v<0?'':fmtNum(v);
const ico=d=>`<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${d}</svg>`;
const RAIL=[
  ['cross','Crosshair',ico('<path d="M12 2v20M2 12h20"/><circle cx="12" cy="12" r="2"/>')],
  ['trend','Trend line',ico('<path d="M4 18L20 6"/><circle cx="4" cy="18" r="1.4"/><circle cx="20" cy="6" r="1.4"/>')],
  ['fib','Retracement',ico('<path d="M4 6h16M4 10h16M4 14h16M4 18h16"/>')],
  ['ruler','Measure',ico('<path d="M3 21L21 3"/><path d="M7 21h.01M11 17h.01M15 13h.01M19 9h.01"/>')],
  ['zoom','Fit',ico('<path d="M8 3H3v5M16 3h5v5M8 21H3v-5M21 16v5h-5"/>')],
  ['magnet','Magnet',ico('<path d="M7 13v-3a5 5 0 0 1 10 0v3M7 13v5h3v-5M14 13v5h3v-5"/>')],
  ['log','Log scale',ico('<path d="M4 20c4-12 8-12 16-16"/><path d="M4 20h16"/>')],
  ['trash','Clear drawings',ico('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>')]
];
function chartBlockHTML(){
  return `<div class="chartcard">
    <div class="chhead">
      <div class="chctl-left">
        <div class="tfs" data-role="tf">${TFS.map(t=>`<button data-tf="${t}" class="${t===CH.tf?'on':''}">${t}</button>`).join('')}</div>
        <span class="chdiv"></span>
        <div class="tfs" data-role="unit"><button data-unit="px" class="on">Price</button><button data-unit="mcap">MCap</button></div>
        <div class="tfs" data-role="style"><button data-style="candles" class="on">Candles</button><button data-style="line">Line</button></div>
      </div>
      <div class="chctl-right tfs">
        <button type="button" id="itToggle" class="ch-instant" aria-pressed="false" title="Optional Instant Paper Trade card. Simulation only.">⚡ Instant</button>
        <button data-mode="native" class="on">Chart</button>
        <button data-mode="dex">DexScreener</button>
      </div>
    </div>
    <div class="chbody">
      <aside class="ch-rail" id="chRail" aria-label="Chart tools">${RAIL.map(([id,t,s])=>`<button type="button" data-tool="${id}" title="${t}" class="${id==='cross'?'on':''}">${s}</button>`).join('')}</aside>
      <div id="chWrap" class="chwrap">
        <div id="chLegend" class="ch-legend"><b id="chLegTitle">—</b> <span id="chLegOHLC"></span>
          <b id="chPrice" hidden>—</b><span id="chChg" hidden></span><small id="chUpd" hidden></small></div>
        <div id="chNative" class="chnative"></div>
        <div id="chMsg" class="chmsg hidden"></div>
        <div id="chDex" class="chdex off"></div>
      </div>
    </div>
    <div class="chfoot muted">
      <div class="ch-range">${[['1h',3600],['6h',21600],['1d',86400],['5d',432000],['All',0]].map(([l,s])=>`<button type="button" data-range="${s}">${l}</button>`).join('')}</div>
      <button type="button" data-log class="ch-tog">log</button>
      <button type="button" data-auto class="ch-tog">auto</button>
      <span id="chSrc">Candles · refreshes every 15s while open</span>
      <span id="chLim" class="chlim hidden" title="">Limited history</span>
      <span id="chClock"></span>
    </div>
  </div>`;
}
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
function scalePx(v){if(v==null||!isFinite(v))return v;return CH.unit==='mcap'&&CH.supply?v*CH.supply:v}
function barOf(k){return {time:k.t,open:scalePx(k.o),high:scalePx(k.h),low:scalePx(k.l),close:scalePx(k.c)}}
function paintLegend(bar,chg){
  const el=document.getElementById('chLegOHLC'),title=document.getElementById('chLegTitle');
  const sym=(CH.coin&&CH.coin.symbol)||'TOKEN';
  const unit=CH.unit==='mcap'?'MCAP':'USD';
  if(title)title.textContent=`${sym}/${unit} · ${CH.tf} · xScanner`;
  if(!el)return;
  if(!bar){el.textContent='';return}
  const pct=chg==null?'':` (${chg>=0?'+':''}${chg.toFixed(2)}%)`;
  const cls=bar.close>=bar.open?'up':'dn';
  el.innerHTML=`O ${fmtNum(bar.open)}  H ${fmtNum(bar.high)}  L ${fmtNum(bar.low)}  C <span class="${cls}">${fmtNum(bar.close)}${pct}</span>`;
}
function clearDrawings(){
  (CH.drawings||[]).forEach(s=>{try{CH.chart.removeSeries(s)}catch(e){}});
  (CH.lines||[]).forEach(l=>{try{CH.cs.removePriceLine(l)}catch(e){}});
  CH.drawings=[];CH.lines=[];CH.pending=null;
}
function applyStyle(){
  if(!CH.cs||!CH.ls)return;
  const candles=CH.style!=='line';
  CH.cs.applyOptions({visible:candles,lastValueVisible:candles,priceLineVisible:candles});
  CH.ls.applyOptions({visible:!candles,lastValueVisible:!candles,priceLineVisible:!candles});
  document.querySelectorAll('.chartcard [data-style]').forEach(b=>b.classList.toggle('on',b.dataset.style===CH.style));
}
function applyLog(){
  if(!CH.chart)return;
  try{CH.cs.priceScale().applyOptions({mode:CH.log?1:0})}catch(e){}
  document.querySelectorAll('.chartcard [data-log], #chRail [data-tool="log"]').forEach(b=>b.classList.toggle('on',!!CH.log));
}
function onChartClick(param){
  if(!param||!param.point||!CH.cs)return;
  const price=CH.cs.coordinateToPrice(param.point.y);
  const time=param.time;
  if(price==null||time==null)return;
  if(CH.tool!=='trend'&&CH.tool!=='fib'&&CH.tool!=='ruler')return;
  if(!CH.pending){CH.pending={time,price};return}
  const a=CH.pending,b={time,price};CH.pending=null;
  if(CH.tool==='trend'||CH.tool==='ruler'){
    const ls=CH.chart.addLineSeries({color:'#526fff',lineWidth:CH.tool==='ruler'?1:2,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
    const t0=a.time<=b.time?a:b,t1=a.time<=b.time?b:a;
    ls.setData([{time:t0.time,value:t0.price},{time:t1.time,value:t1.price}]);
    CH.drawings.push(ls);
    if(CH.tool==='ruler'){
      const d=((b.price-a.price)/a.price)*100;
      ls.applyOptions({title:(d>=0?'+':'')+d.toFixed(2)+'%'});
    }
  }else if(CH.tool==='fib'){
    const lo=Math.min(a.price,b.price),hi=Math.max(a.price,b.price),span=hi-lo;
    [0,0.382,0.5,0.618,1].forEach((f,i)=>{
      const pl=CH.cs.createPriceLine({price:lo+span*f,color:i===0||i===4?'#76798b':'#526fff',lineWidth:1,lineStyle:2,axisLabelVisible:true,title:(f*100).toFixed(1)+'%'});
      CH.lines.push(pl);
    });
  }
}
function bindTools(){
  const rail=document.getElementById('chRail');
  if(rail)rail.onclick=e=>{
    const b=e.target.closest('[data-tool]');if(!b)return;
    const t=b.dataset.tool;
    if(t==='zoom'||t==='auto'){CH.chart&&CH.chart.timeScale().fitContent();return}
    if(t==='trash'){clearDrawings();return}
    if(t==='log'){CH.log=!CH.log;applyLog();return}
    if(t==='magnet'){CH.magnet=!CH.magnet;if(CH.chart)CH.chart.applyOptions({crosshair:{mode:CH.magnet?1:0}});b.classList.toggle('on',CH.magnet);return}
    CH.tool=t;CH.pending=null;
    rail.querySelectorAll('[data-tool]').forEach(x=>{if(!['magnet','log'].includes(x.dataset.tool))x.classList.toggle('on',x===b)});
  };
  document.querySelectorAll('.chartcard [data-range]').forEach(b=>b.onclick=()=>{
    if(!CH.chart||!CH.lastT)return;
    const sec=+b.dataset.range;
    if(!sec){CH.chart.timeScale().fitContent();return}
    CH.chart.timeScale().setVisibleRange({from:CH.lastT-sec,to:CH.lastT+Math.max(60,sec*0.02)});
  });
  document.querySelectorAll('.chartcard [data-log]').forEach(b=>b.onclick=()=>{CH.log=!CH.log;applyLog()});
  document.querySelectorAll('.chartcard [data-auto]').forEach(b=>b.onclick=()=>CH.chart&&CH.chart.timeScale().fitContent());
  document.querySelectorAll('.chartcard [data-unit]').forEach(b=>b.onclick=()=>{
    CH.unit=b.dataset.unit;
    document.querySelectorAll('.chartcard [data-unit]').forEach(x=>x.classList.toggle('on',x===b));
    if(CH.lastBars&&CH.lastBars.length)pushBars(CH.lastBars);
  });
  document.querySelectorAll('.chartcard [data-style]').forEach(b=>b.onclick=()=>{CH.style=b.dataset.style;applyStyle()});
}
function tickClock(){
  const el=document.getElementById('chClock');if(!el)return;
  el.textContent=new Date().toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' MYT';
}
function mountChart(c){
  unmountChart();CH.coin=c;CH.lastT=null;CH.lastPx=null;CH.fit=true;CH.pending=null;CH.drawings=[];CH.lines=[];CH.lastBars=[];
  if(c&&c.supply)CH.supply=c.supply;
  document.querySelectorAll('.chartcard [data-tf]').forEach(b=>b.onclick=()=>{CH.tf=b.dataset.tf;localStorage.setItem('chartTf',CH.tf);
    document.querySelectorAll('.chartcard [data-tf]').forEach(x=>x.classList.toggle('on',x===b));CH.fit=true;CH.lastT=null;if(CH.mode!=='native')setMode('native');else load(true)});
  document.querySelectorAll('.chartcard [data-mode]').forEach(b=>b.onclick=()=>setMode(b.dataset.mode));
  bindTools();tickClock();CH.clock=setInterval(tickClock,1000);
  const it=document.getElementById('itToggle');
  if(it)it.onclick=e=>{e.preventDefault();e.stopPropagation();if(window.paperToggleInstant)window.paperToggleInstant()};
  if(window.paperSyncInstant)window.paperSyncInstant();
  paintLegend(null);
  if(!c.pool){chMsg('No trading pair known for this coin, so there is no chart yet.');return}
  const el=document.getElementById('chNative');
  if(!window.LightweightCharts){chMsg('<span class="spin"></span> Loading chart…');ensureCharts().then(ok=>{if(CH.coin!==c)return;if(!ok||!window.LightweightCharts){chMsg('Chart library failed to load. Showing the DexScreener chart instead.');setMode('dex');return}mountChart(c)});return}
  const LC=window.LightweightCharts;
  const h=el.clientHeight||CH.h||360;
  const opts={width:el.clientWidth,height:h,
    layout:{background:{type:'solid',color:BG},textColor:'#76798b',fontFamily:'Geist Mono, Geist, system-ui, sans-serif',fontSize:11},
    grid:{vertLines:{color:GRID},horzLines:{color:GRID}},
    rightPriceScale:{borderColor:'#1e222b',scaleMargins:{top:0.08,bottom:0.22}},
    timeScale:{borderColor:'#1e222b',timeVisible:true,secondsVisible:false,rightOffset:4},
    crosshair:{mode:CH.magnet?1:0,vertLine:{color:'#3a4150',style:3,width:1},horzLine:{color:'#3a4150',style:3,width:1}},
    localization:{priceFormatter:fmtAxis,timeFormatter:t=>new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}};
  try{opts.layout.attributionLogo=false}catch(e){}
  CH.chart=LC.createChart(el,opts);
  CH.cs=CH.chart.addCandlestickSeries({upColor:UP,downColor:DN,borderVisible:false,wickUpColor:UP,wickDownColor:DN,
    priceFormat:{type:'custom',formatter:fmtAxis,minMove:1e-12},
    autoscaleInfoProvider:orig=>{const r=orig();if(r&&r.priceRange&&r.priceRange.minValue<0)r.priceRange.minValue=0;return r}});
  CH.ls=CH.chart.addLineSeries({color:UP,lineWidth:2,visible:false,lastValueVisible:false,priceLineVisible:false,priceFormat:{type:'custom',formatter:fmtAxis,minMove:1e-12}});
  CH.vs=CH.chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
  CH.chart.priceScale('vol').applyOptions({scaleMargins:{top:0.8,bottom:0},visible:false});
  CH.cs.priceScale().applyOptions({scaleMargins:{top:0.06,bottom:0.22}});
  applyStyle();applyLog();
  CH.chart.subscribeCrosshairMove(param=>{
    const d=param&&param.seriesData&&CH.cs?param.seriesData.get(CH.cs):null;
    if(d&&d.close!=null){const chg=d.open?((d.close-d.open)/d.open)*100:null;paintLegend(d,chg)}
    else if(CH.lastBars&&CH.lastBars.length){const k=CH.lastBars[CH.lastBars.length-1],bar=barOf(k);const chg=k.o?((scalePx(k.c)-scalePx(k.o))/scalePx(k.o))*100:null;paintLegend(bar,chg)}
  });
  CH.chart.subscribeClick(onChartClick);
  CH.ro=new ResizeObserver(()=>{if(!CH.chart)return;CH.chart.applyOptions({width:el.clientWidth,height:el.clientHeight||CH.h||360})});CH.ro.observe(el);
  load(true);
  CH.timer=setInterval(()=>{if(!document.hidden&&CH.mode==='native')load(false)},15000);
}
function unmountChart(){clearInterval(CH.timer);CH.timer=null;clearInterval(CH.clock);CH.clock=null;CH.ro?.disconnect();CH.ro=null;clearDrawings();try{CH.chart?.remove()}catch(e){}CH.chart=CH.cs=CH.vs=CH.ls=null;CH.coin=null;CH.lastPx=null;CH.seq++}
const GT_TF={'1m':['minute',1],'5m':['minute',5],'15m':['minute',15],'1h':['hour',1],'4h':['hour',4],'1d':['day',1]};
async function directGT(c,tf){
  const [p,a]=GT_TF[tf]||GT_TF['5m'];
  const u=`https://api.geckoterminal.com/api/v2/networks/solana/pools/${encodeURIComponent(c.pool)}/ohlcv/${p}?aggregate=${a}&limit=300&currency=usd`+(c.mint?`&token=${encodeURIComponent(c.mint)}`:'');
  const r=await fetch(u,{headers:{Accept:'application/json;version=20230302'}});if(!r.ok)return null;
  const d=await r.json();const rows=(((d||{}).data||{}).attributes||{}).ohlcv_list||[];const m={};
  for(const x of rows)if(Array.isArray(x)&&x.length>=6&&x[4]!=null)m[x[0]]={t:+x[0],o:+x[1],h:+x[2],l:+x[3],c:+x[4],v:+x[5]};
  return {candles:Object.values(m).sort((a,b)=>a.t-b.t),source:'geckoterminal-direct'};
}
function pushBars(cd){
  CH.lastBars=cd;
  const bars=cd.map(barOf);
  CH.cs.setData(bars);
  CH.ls.setData(bars.map(k=>({time:k.time,value:k.close})));
  CH.vs.setData(cd.map(k=>({time:k.t,value:k.v,color:k.c>=k.o?UP+'40':DN+'40'})));
  const last=bars[bars.length-1],first=bars[0];
  const chg=first&&first.open?((last.close-first.open)/first.open)*100:null;
  paintLegend(last,chg);
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
  if(ok&&(j.source==='derived'||j.source==='snapshots')&&Date.now()>(CH.dgUntil||0)){
    try{const d=await directGT(c,tf);if(d&&d.candles&&d.candles.length>2){j=d}else CH.dgUntil=Date.now()+60000}catch(e){CH.dgUntil=Date.now()+60000}
    if(seq!==CH.seq||!CH.cs)return;
  }
  if(!ok){
    if(CH.lastT){const u=document.getElementById('chUpd');if(u)u.textContent=' · '+(j.detail||'still loading')+' — keeping last data';return}
    if(r&&r.status===404)chMsg(`${esc(j.detail||'No chart data for this pair yet.')} <a href="#" onclick="setMode('dex');return false">Try the DexScreener chart</a>`);
    else{
      const k=CH.seq;setTimeout(()=>{if(k===CH.seq)load(false)},8000);
      chMsg(`<span class="spin"></span> Chart loading… <a href="#" onclick="setMode('dex');return false">Use the DexScreener chart</a>`);
    }
    return}
  const cd=saneCandles(j.candles||[]);
  if(!cd.length){chMsg(`No trades in this timeframe yet. Try a longer timeframe or <a href="#" onclick="setMode('dex');return false">the DexScreener chart</a>.`);CH.cs.setData([]);if(CH.vs)CH.vs.setData([]);if(CH.ls)CH.ls.setData([]);return}
  chMsg('');
  pushBars(cd);
  if(CH.fit){CH.chart.timeScale().fitContent();CH.fit=false}
  CH.lastT=cd[cd.length-1].t;
  const last=scalePx(cd[cd.length-1].c),first=scalePx(cd[0].o),chg=first?((last-first)/first*100):null;
  const priceEl=document.getElementById('chPrice');
  const prevPx=CH.lastPx;
  const simple=isFinite(last)&&Math.abs(last)>=1e-4&&(prevPx==null||Math.abs(prevPx)>=1e-4);
  if(priceEl){
    if(prevPx!=null&&prevPx!==last&&simple&&window.XS&&XS.motionOn()){
      priceEl.dataset.v=prevPx;
      XS.tick(priceEl,last,v=>fmtPx(v));
      XS.flash(priceEl);
    }else{priceEl.innerHTML=fmtPx(last)}
  }
  CH.lastPx=last;
  const winH=(cd[cd.length-1].t-cd[0].t)/3600, winTxt=winH<48?Math.max(1,Math.round(winH))+'h':Math.round(winH/24)+'d';
  const chgEl=document.getElementById('chChg');
  if(chgEl)chgEl.innerHTML=chg==null?'':`${pct(chg)} <small class="muted">over ${winTxt}</small>`;
  const srcEl=document.getElementById('chSrc');
  if(srcEl){const nm={jupiter:'Jupiter (real candles)',geckoterminal:'GeckoTerminal','geckoterminal-direct':'GeckoTerminal (your browser)','geckoterminal+snapshots':'GeckoTerminal + our price snapshots',snapshots:'our own price snapshots (no volume)',derived:'approximate, from DexScreener 5m/1h/24h changes'}[j.source]||j.source||'GeckoTerminal';
    srcEl.textContent='Candles: '+nm+(j.approximate?' · approximate':'')+' · 15s'}
  const lim=document.getElementById('chLim');
  if(lim){const on=!!j.limited||j.source==='derived'||j.source==='snapshots';lim.classList.toggle('hidden',!on);
    lim.title=on?(j.limited_reason||'Exchange candles are rate-limited right now, so this chart is built from our own price snapshots (no volume).'):''}
  const ft=j.fetched_at?new Date(j.fetched_at*1000):new Date();
  const upd=document.getElementById('chUpd');
  if(upd)upd.textContent=' · updated '+ft.toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT'+(j.stale?' (delayed)':'');
  try{document.dispatchEvent(new CustomEvent('xschart',{detail:{mint:c.mint,last:last,candles:cd}}))}catch(e){}
}
