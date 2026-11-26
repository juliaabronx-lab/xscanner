// Live coin chart for the detail view: /api/ohlcv (cached backend proxy) drawn with TradingView
// lightweight-charts; DexScreener embed as a toggle / fallback. Market data only. No Axiom assets.
const CH={tf:localStorage.getItem('chartTf')||'15m',mode:'native',coin:null,chart:null,cs:null,vs:null,ls:null,area:null,ema9:null,ema21:null,timer:null,ro:null,seq:0,lastT:null,fit:true,
  style:(localStorage.getItem('xs_chart_style_v1')||'candles'),unit:'px',tool:'cross',magnet:true,log:false,ema:localStorage.getItem('xs_chart_ema_v1')!=='0',
  drawings:[],lines:[],pending:null,supply:null,clock:null,lastBars:[],lastBarUp:true};
if(!['candles','glow','line'].includes(CH.style))CH.style='candles';
const TFS=['1m','5m','15m','1h','4h','1d'];
const UP='#00f5a0', UPW='#5effc8', DN='#ff3b7a', DNW='#ff7aa8', GRID='rgba(255,255,255,0.045)', BG='rgba(9,11,16,0)';
const EMA9='#38bdf8', EMA21='#a78bfa';
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
        <div class="tfs" data-role="style"><button data-style="candles" class="${CH.style==='candles'?'on':''}">Candles</button><button data-style="glow" class="${CH.style==='glow'?'on':''}">3D Glow</button><button data-style="line" class="${CH.style==='line'?'on':''}">Line</button></div>
        <div class="tfs" data-role="ema"><button type="button" data-ema="1" class="${CH.ema?'on':''}" title="Toggle EMA 9 and EMA 21 ribbons">EMA</button></div>
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
        <div id="chLegend" class="ch-legend ch-hud"><b id="chLegTitle">—</b> <span id="chLegOHLC"></span>
          <b id="chPrice" hidden>—</b><span id="chChg" hidden></span><small id="chUpd" hidden></small></div>
        <div id="chLive" class="ch-live hidden" aria-hidden="true"><i class="ch-live-dot"></i><span id="chLivePx">—</span></div>
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
  if(title)title.textContent=`$${sym} · ${CH.tf}`;
  if(!el)return;
  if(!bar){el.textContent='';return}
  const cls=bar.close>=bar.open?'up':'dn';
  const badge=chg==null?'':`<span class="ch-pct ${cls}">${chg>=0?'+':''}${chg.toFixed(2)}%</span>`;
  el.innerHTML=`<span class="ch-ohlc">O ${fmtNum(bar.open)}  H ${fmtNum(bar.high)}  L ${fmtNum(bar.low)}  C <span class="${cls}">${fmtNum(bar.close)}</span></span> ${badge}`;
}
function clearDrawings(){
  (CH.drawings||[]).forEach(s=>{try{CH.chart.removeSeries(s)}catch(e){}});
  (CH.lines||[]).forEach(l=>{try{CH.cs.removePriceLine(l)}catch(e){}});
  CH.drawings=[];CH.lines=[];CH.pending=null;
}
function volColor(up){return up?'rgba(0, 245, 160, 0.28)':'rgba(255, 59, 122, 0.28)'}
function ribbonColors(){
  const up=CH.lastBarUp!==false, glow=CH.style==='glow', line=CH.style==='line';
  if(line)return{line:up?UP:DN,top:up?'rgba(0,245,160,0.42)':'rgba(255,59,122,0.42)',bot:up?'rgba(0,245,160,0.00)':'rgba(255,59,122,0.00)',lw:2.25};
  if(glow)return{line:up?UPW:DNW,top:up?'rgba(0,245,160,0.38)':'rgba(255,59,122,0.36)',bot:up?'rgba(0,245,160,0.02)':'rgba(255,59,122,0.02)',lw:1.75};
  return{line:up?'rgba(0,245,160,0.35)':'rgba(255,59,122,0.35)',top:up?'rgba(0,245,160,0.14)':'rgba(255,59,122,0.12)',bot:'rgba(9,11,16,0)',lw:1};
}
function applyStyle(){
  if(!CH.cs||!CH.ls)return;
  const line=CH.style==='line', glow=CH.style==='glow', up=CH.lastBarUp!==false;
  const pxLine=up?UP:DN;
  CH.cs.applyOptions({
    visible:!line,lastValueVisible:!line,priceLineVisible:!line,
    priceLineColor:pxLine,priceLineWidth:1,priceLineStyle:2,
    upColor:glow?'rgba(0,245,160,0.82)':UP,downColor:glow?'rgba(255,59,122,0.82)':DN,
    borderVisible:true,borderUpColor:UPW,borderDownColor:DNW,wickUpColor:UPW,wickDownColor:DNW
  });
  CH.ls.applyOptions({visible:false,lastValueVisible:false,priceLineVisible:false});
  if(CH.area){
    const r=ribbonColors();
    CH.area.applyOptions({
      visible:true,lineWidth:r.lw,lineColor:r.line,topColor:r.top,bottomColor:r.bot,
      lastValueVisible:line,priceLineVisible:line,priceLineColor:pxLine,priceLineWidth:1,priceLineStyle:2,
      crosshairMarkerVisible:line,crosshairMarkerRadius:line?5:3,
      crosshairMarkerBorderColor:'#090b10',crosshairMarkerBackgroundColor:pxLine
    });
  }
  document.querySelectorAll('.chartcard [data-style]').forEach(b=>b.classList.toggle('on',b.dataset.style===CH.style));
  const wrap=document.querySelector('.chwrap');
  if(wrap){wrap.classList.toggle('ch-glow',glow);wrap.classList.toggle('ch-line',line)}
  try{localStorage.setItem('xs_chart_style_v1',CH.style)}catch(e){}
  applyEma();syncLivePrice();
}
function applyEma(){
  const on=!!CH.ema;
  document.querySelectorAll('.chartcard [data-ema]').forEach(b=>b.classList.toggle('on',on));
  if(CH.ema9)CH.ema9.applyOptions({visible:on});
  if(CH.ema21)CH.ema21.applyOptions({visible:on});
  try{localStorage.setItem('xs_chart_ema_v1',on?'1':'0')}catch(e){}
}
function emaPts(cd,period){
  const out=[];if(!cd||cd.length<period)return out;
  let s=0;for(let i=0;i<period;i++)s+=scalePx(cd[i].c);
  let e=s/period;out.push({time:cd[period-1].t,value:e});
  const k=2/(period+1);
  for(let i=period;i<cd.length;i++){e=scalePx(cd[i].c)*k+e*(1-k);out.push({time:cd[i].t,value:e})}
  return out;
}
function syncLivePrice(){
  const el=document.getElementById('chLive');if(!el)return;
  const series=CH.style==='line'?(CH.area||CH.ls):CH.cs;
  if(!series||CH.lastPx==null||!CH.chart){el.classList.add('hidden');return}
  let y=null;try{y=series.priceToCoordinate(CH.lastPx)}catch(e){y=null}
  if(y==null||!isFinite(y)||y<0){el.classList.add('hidden');return}
  el.classList.remove('hidden');
  el.classList.toggle('up',CH.lastBarUp!==false);
  el.classList.toggle('dn',CH.lastBarUp===false);
  el.style.transform='translateY('+y+'px)';
  const px=document.getElementById('chLivePx');if(px)px.textContent=fmtPx(CH.lastPx);
  el.setAttribute('aria-hidden','false');
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
  document.querySelectorAll('.chartcard [data-ema]').forEach(b=>b.onclick=()=>{CH.ema=!CH.ema;applyEma()});
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
    layout:{background:{type:'solid',color:BG},textColor:'#8b90a0',fontFamily:'Geist Mono, Geist, system-ui, sans-serif',fontSize:11},
    grid:{vertLines:{color:GRID,style:1},horzLines:{color:GRID,style:1}},
    rightPriceScale:{borderColor:'rgba(255,255,255,0.06)',scaleMargins:{top:0.08,bottom:0.22}},
    timeScale:{borderColor:'rgba(255,255,255,0.06)',timeVisible:true,secondsVisible:false,rightOffset:6},
    crosshair:{mode:CH.magnet?1:0,vertLine:{color:'rgba(0,245,160,0.28)',style:2,width:1},horzLine:{color:'rgba(0,245,160,0.28)',style:2,width:1}},
    localization:{priceFormatter:fmtAxis,timeFormatter:t=>new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}};
  try{opts.layout.attributionLogo=false}catch(e){}
  CH.chart=LC.createChart(el,opts);
  const pxFmt={type:'custom',formatter:fmtAxis,minMove:1e-12};
  CH.area=CH.chart.addAreaSeries({lineColor:UP,topColor:'rgba(0,245,160,0.14)',bottomColor:'rgba(9,11,16,0)',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt});
  CH.cs=CH.chart.addCandlestickSeries({upColor:UP,downColor:DN,borderVisible:true,borderUpColor:UPW,borderDownColor:DNW,wickUpColor:UPW,wickDownColor:DNW,
    priceFormat:pxFmt,
    autoscaleInfoProvider:orig=>{const r=orig();if(r&&r.priceRange&&r.priceRange.minValue<0)r.priceRange.minValue=0;return r}});
  CH.ls=CH.chart.addLineSeries({color:UP,lineWidth:2,visible:false,lastValueVisible:false,priceLineVisible:false,priceFormat:pxFmt});
  CH.ema9=CH.chart.addLineSeries({color:EMA9,lineWidth:2,visible:!!CH.ema,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt});
  CH.ema21=CH.chart.addLineSeries({color:EMA21,lineWidth:2,visible:!!CH.ema,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt});
  CH.vs=CH.chart.addHistogramSeries({priceScaleId:'vol',priceFormat:{type:'volume'},lastValueVisible:false,priceLineVisible:false});
  CH.chart.priceScale('vol').applyOptions({scaleMargins:{top:0.82,bottom:0},visible:false});
  CH.cs.priceScale().applyOptions({scaleMargins:{top:0.08,bottom:0.22}});
  applyStyle();applyLog();
  CH.chart.subscribeCrosshairMove(param=>{
    const d=param&&param.seriesData&&CH.cs?param.seriesData.get(CH.cs):null;
    if(d&&d.close!=null){const chg=d.open?((d.close-d.open)/d.open)*100:null;paintLegend(d,chg)}
    else if(CH.lastBars&&CH.lastBars.length){const k=CH.lastBars[CH.lastBars.length-1],bar=barOf(k);const chg=k.o?((scalePx(k.c)-scalePx(k.o))/scalePx(k.o))*100:null;paintLegend(bar,chg)}
  });
  CH.chart.subscribeClick(onChartClick);
  try{CH.chart.timeScale().subscribeVisibleLogicalRangeChange(()=>syncLivePrice())}catch(e){}
  CH.ro=new ResizeObserver(()=>{if(!CH.chart)return;CH.chart.applyOptions({width:el.clientWidth,height:el.clientHeight||CH.h||360});syncLivePrice()});CH.ro.observe(el);
  load(true);
  CH.timer=setInterval(()=>{if(!document.hidden&&CH.mode==='native')load(false)},15000);
}
function unmountChart(){clearInterval(CH.timer);CH.timer=null;clearInterval(CH.clock);CH.clock=null;CH.ro?.disconnect();CH.ro=null;clearDrawings();try{CH.chart?.remove()}catch(e){}CH.chart=CH.cs=CH.vs=CH.ls=CH.area=CH.ema9=CH.ema21=null;CH.coin=null;CH.lastPx=null;CH.seq++;const live=document.getElementById('chLive');if(live)live.classList.add('hidden')}
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
  const last=bars[bars.length-1],first=bars[0];
  CH.lastBarUp=!(last&&last.close<last.open);
  if(last)CH.lastPx=last.close;
  if(CH.area)CH.area.setData(bars.map(k=>({time:k.time,value:k.close})));
  CH.cs.setData(bars);
  CH.ls.setData(bars.map(k=>({time:k.time,value:k.close})));
  if(CH.ema9)CH.ema9.setData(emaPts(cd,9));
  if(CH.ema21)CH.ema21.setData(emaPts(cd,21));
  CH.vs.setData(cd.map(k=>({time:k.t,value:k.v||0,color:volColor(k.c>=k.o)})));
  const chg=first&&first.open?((last.close-first.open)/first.open)*100:null;
  paintLegend(last,chg);
  applyStyle();
  syncLivePrice();
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
