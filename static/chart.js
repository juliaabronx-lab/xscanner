// Live coin chart for the detail view: /api/ohlcv (cached backend proxy) drawn with TradingView
// lightweight-charts; DexScreener embed as a toggle / fallback. Market data only. No Axiom assets.
const CH={tf:localStorage.getItem('chartTf')||'15m',mode:'native',coin:null,chart:null,cs:null,vs:null,ls:null,area:null,ema9:null,ema21:null,timer:null,ro:null,seq:0,lastT:null,fit:true,
  style:(localStorage.getItem('xs_chart_style_v1')||'candles'),unit:'px',tool:'cross',magnet:true,log:false,ema:localStorage.getItem('xs_chart_ema_v1')!=='0',
  drawings:[],lines:[],pending:null,supply:null,clock:null,lastBars:[],lastBarUp:true};
if(!['candles','glow','line'].includes(CH.style))CH.style='candles';
const TFS=['1s','15s','30s','1m','5m','15m','1h','4h','1d'];
const UP='#00f5a0', UPW='#5effc8', DN='#ff3b7a', DNW='#ff7aa8', GRID='rgba(255,255,255,0.045)', BG='rgba(9,11,16,0)';
const EMA9='#38bdf8', EMA21='#a78bfa';
const SUBS='₀₁₂₃₄₅₆₇₈₉';
const tinyNum=a=>{const z=-Math.floor(Math.log10(a))-1;let sig=Math.round(a*Math.pow(10,z+3));let zz=z;if(sig>=10000){sig=Math.round(sig/10);zz--}
  return zz<=3?a.toFixed(zz+3+1).replace(/0+$/,''):'0.0'+String(zz).split('').map(d=>SUBS[d]).join('')+String(sig).replace(/0+$/,'')};
const fmtNum=v=>{if(v==null||!isFinite(v))return'—';const a=Math.abs(v);if(a===0)return'0';if(a>=1)return v.toLocaleString('en-US',{maximumFractionDigits:a>=1000?2:4});
  if(a>=0.01)return String(parseFloat(v.toPrecision(4)));return (v<0?'-':'')+tinyNum(a)};
function validSupply(s){s=+s;return isFinite(s)&&s>0&&s<1e18}
function resolveSupply(src){
  src=src||{};
  const c=CH.coin||{};
  const candidates=[src.supply,c.supply,CH.supply];
  const px=+src.px||+c.px||+src.price, mc=+src.mc||+c.mc;
  if(validSupply(px)&&validSupply(mc))candidates.unshift(mc/px);
  for(const s of candidates){if(validSupply(s)){CH.supply=+s;return CH.supply}}
  CH.supply=null;return null;
}
function fmtCompact(v){
  if(v==null||!isFinite(+v))return'—';
  const n=+v,a=Math.abs(n),sign=n<0?'-':'';
  for(const[d,s]of[[1e12,'T'],[1e9,'B'],[1e6,'M'],[1e3,'K']]){
    if(a>=d){let x=a/d,str=x>=100?x.toFixed(0):x>=10?x.toFixed(1):x.toFixed(2);
      str=str.replace(/(\.\d*?)0+$/,'$1').replace(/\.$/,'');
      return sign+str+s}
  }
  return sign+fmtNum(a);
}
function fmtPx(v){if(v==null||!isFinite(+v))return'—';if(CH.unit==='mcap')return'$'+fmtCompact(v);const t=fmtNum(v);return t==='—'?t:'$'+t}
function fmtAxis(v){if(v==null||!isFinite(+v)||v<0)return'';return CH.unit==='mcap'?'$'+fmtCompact(v):fmtNum(v)}
function rawPx(){return (CH.lastUsd!=null&&isFinite(+CH.lastUsd))?+CH.lastUsd:((CH.lastPx!=null&&CH.unit!=='mcap')?+CH.lastPx:null)}
function fmtLive(){const raw=rawPx();if(raw==null)return'—';return fmtPx(scalePx(raw))}
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
        <span id="chLimited" class="ch-limited hidden">Limited history</span><div class="tfs" data-role="ema"><button type="button" data-ema="1" class="${CH.ema?'on':''}" title="Toggle EMA 9 and EMA 21 ribbons">EMA</button></div>
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
function scalePx(v){if(v==null||!isFinite(+v))return v;if(CH.unit!=='mcap')return +v;if(!validSupply(CH.supply))return null;return +v*CH.supply}
function fitChart(){
  if(!CH.chart)return;
  try{CH.chart.applyOptions({localization:{priceFormatter:fmtAxis}})}catch(e){}
  const fmt={type:'custom',formatter:fmtAxis,minMove:CH.unit==='mcap'?1:1e-12};
  try{CH.cs&&CH.cs.applyOptions({priceFormat:fmt})}catch(e){}
  try{CH.ls&&CH.ls.applyOptions({priceFormat:fmt})}catch(e){}
  try{CH.area&&CH.area.applyOptions({priceFormat:{type:'custom',formatter:fmtAxis}})}catch(e){}
  try{CH.ema9&&CH.ema9.applyOptions({priceFormat:{type:'custom',formatter:fmtAxis}})}catch(e){}
  try{CH.ema21&&CH.ema21.applyOptions({priceFormat:{type:'custom',formatter:fmtAxis}})}catch(e){}
  try{CH.cs&&CH.cs.priceScale().applyOptions({autoScale:true})}catch(e){}
  try{CH.chart.priceScale('right').applyOptions({autoScale:true})}catch(e){}
  try{CH.chart.timeScale().fitContent()}catch(e){}
}
function syncUnitButtons(){
  const ok=!!validSupply(CH.supply);
  document.querySelectorAll('.chartcard [data-unit]').forEach(x=>{
    x.classList.toggle('on',x.dataset.unit===(CH.unit||'px'));
    if(x.dataset.unit==='mcap'){
      x.disabled=!ok&&CH.unit!=='mcap';
      x.title=ok?'Market cap':'Market cap unavailable — no supply or price × market cap yet';
      x.classList.toggle('off',!ok);
    }
  });
}
function setUnit(unit,opts){
  opts=opts||{};const fit=opts.fit!==false;
  const want=unit==='mcap'?'mcap':'px';
  if(want==='mcap'&&!validSupply(CH.supply)&&!resolveSupply(CH.coin||{})){
    CH.unit='px';
    try{localStorage.setItem('xs_chart_unit_v1','px')}catch(e){}
    syncUnitButtons();
    return false;
  }
  CH.unit=want;
  try{localStorage.setItem('xs_chart_unit_v1',want)}catch(e){}
  syncUnitButtons();
  if(CH.lastBars&&CH.lastBars.length)pushBars(CH.lastBars);
  if(fit)fitChart();
  syncLivePrice();
  try{document.dispatchEvent(new CustomEvent('xschart',{detail:{mint:(CH.coin||{}).mint,last:rawPx(),candles:CH.lastBars,unit:CH.unit}}))}catch(e){}
  return true;
}
function barOf(k){return {time:k.t,open:scalePx(k.o),high:scalePx(k.h),low:scalePx(k.l),close:scalePx(k.c)}}
function windowChg(){
  if(!CH.lastBars||!CH.lastBars.length)return null;
  let from=CH.lastBars[0].o, to=CH.lastBars[CH.lastBars.length-1].c;
  try{
    const r=CH.chart&&CH.chart.timeScale().getVisibleLogicalRange();
    if(r&&isFinite(r.from)){
      const i=Math.max(0,Math.min(CH.lastBars.length-1,Math.floor(r.from)));
      if(CH.lastBars[i]&&isFinite(CH.lastBars[i].o))from=CH.lastBars[i].o;
    }
  }catch(e){}
  if(!from||!isFinite(from))return null;
  return ((to-from)/from)*100;
}
function paintLegend(bar,chg){
  const el=document.getElementById('chLegOHLC'),title=document.getElementById('chLegTitle');
  const sym=(CH.coin&&CH.coin.symbol)||'TOKEN';
  if(title)title.textContent=`$${sym} · ${CH.tf}`;
  if(!el)return;
  if(!bar){el.textContent='';return}
  const win=windowChg();
  const cls=(win==null?(bar.close>=bar.open):(win>=0))?'up':'dn';
  const badge=win==null?'':`<span class="ch-pct ${cls}">${win>=0?'+':''}${win.toFixed(2)}%</span>`;
  const f=v=>CH.unit==='mcap'?('$'+fmtCompact(v)):fmtNum(v);
  const ema=CH.ema?`<span class="ch-ema"><span class="e9">EMA9</span><span class="e21">EMA21</span></span>`:'';
  el.innerHTML=`<span class="ch-ohlc">O ${f(bar.open)}  H ${f(bar.high)}  L ${f(bar.low)}  C <span class="${bar.close>=bar.open?'up':'dn'}">${f(bar.close)}</span></span> ${badge}${ema}`;
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
  const out=[];if(!cd||!cd.length)return out;
  let e=scalePx(cd[0].c);if(!isFinite(e))return out;
  const k=2/(Math.max(1,period)+1);
  out.push({time:cd[0].t,value:e});
  for(let i=1;i<cd.length;i++){
    const v=scalePx(cd[i].c);if(!isFinite(v))continue;
    e=v*k+e*(1-k);out.push({time:cd[i].t,value:e});
  }
  return out;
}
function syncLivePrice(){
  const el=document.getElementById('chLive');if(!el)return;
  const series=CH.style==='line'?(CH.area||CH.ls):CH.cs;
  const live=scalePx(rawPx());
  if(!series||live==null||!isFinite(live)||!CH.chart){el.classList.add('hidden');return}
  let y=null;try{y=series.priceToCoordinate(live)}catch(e){y=null}
  if(y==null||!isFinite(y)||y<0){el.classList.add('hidden');return}
  el.classList.remove('hidden');
  el.classList.toggle('up',CH.lastBarUp!==false);
  el.classList.toggle('dn',CH.lastBarUp===false);
  el.style.transform='translateY('+y+'px)';
  const px=document.getElementById('chLivePx');if(px)px.textContent=fmtLive();
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
  document.querySelectorAll('.chartcard [data-unit]').forEach(b=>b.onclick=()=>{setUnit(b.dataset.unit)});
  syncUnitButtons();
  document.querySelectorAll('.chartcard [data-style]').forEach(b=>b.onclick=()=>{CH.style=b.dataset.style;applyStyle()});
  document.querySelectorAll('.chartcard [data-ema]').forEach(b=>b.onclick=()=>{CH.ema=!CH.ema;applyEma()});
}
function tickClock(){
  const el=document.getElementById('chClock');if(!el)return;
  el.textContent=new Date().toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' MYT';
}
function mountChart(c){
  unmountChart();CH.coin=c;CH.lastT=null;CH.lastPx=null;CH.lastUsd=null;CH.fit=true;CH.pending=null;CH.drawings=[];CH.lines=[];CH.lastBars=[];CH.supply=null;resolveSupply(c);
  try{const u=localStorage.getItem('xs_chart_unit_v1');if(u==='mcap'||u==='px')CH.unit=u}catch(e){}
  if(CH.unit==='mcap'&&!validSupply(CH.supply))CH.unit='px';
  resolveSupply(c);
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
    rightPriceScale:{borderColor:'rgba(255,255,255,0.06)',scaleMargins:{top:0.08,bottom:0.22},minimumWidth:84,entireTextOnly:false},
    timeScale:{borderColor:'rgba(255,255,255,0.06)',timeVisible:true,secondsVisible:false,rightOffset:6},
    crosshair:{mode:CH.magnet?1:0,vertLine:{color:'rgba(0,245,160,0.28)',style:2,width:1},horzLine:{color:'rgba(0,245,160,0.28)',style:2,width:1}},
    localization:{priceFormatter:fmtAxis,timeFormatter:t=>new Date(t*1000).toLocaleString('en-GB',{timeZone:'Asia/Kuala_Lumpur',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'})}};
  try{opts.layout.attributionLogo=false}catch(e){}
  try{opts.devicePixelRatio=Math.max(1,window.devicePixelRatio||1)}catch(e){}
  CH.chart=LC.createChart(el,opts);
  const pxFmt={type:'custom',formatter:fmtAxis,minMove:1e-12};
  CH.area=CH.chart.addAreaSeries({lineColor:UP,topColor:'rgba(0,245,160,0.14)',bottomColor:'rgba(9,11,16,0)',lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt});
  CH.cs=CH.chart.addCandlestickSeries({upColor:UP,downColor:DN,borderVisible:true,borderUpColor:UPW,borderDownColor:DNW,wickUpColor:UPW,wickDownColor:DNW,
    priceFormat:pxFmt,
    autoscaleInfoProvider:orig=>{const r=orig();if(r&&r.priceRange&&r.priceRange.minValue<0)r.priceRange.minValue=0;return r}});
  CH.ls=CH.chart.addLineSeries({color:UP,lineWidth:2,visible:false,lastValueVisible:false,priceLineVisible:false,priceFormat:pxFmt});
  CH.ema9=CH.chart.addLineSeries({color:EMA9,lineWidth:2,visible:!!CH.ema,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt,lineStyle:0});
  CH.ema21=CH.chart.addLineSeries({color:EMA21,lineWidth:2,visible:!!CH.ema,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false,priceFormat:pxFmt,lineStyle:0});
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
  CH.ro=new ResizeObserver(()=>{if(!CH.chart)return;const o={width:el.clientWidth,height:el.clientHeight||CH.h||360};try{o.devicePixelRatio=Math.max(1,window.devicePixelRatio||1)}catch(e){}CH.chart.applyOptions(o);syncLivePrice()});CH.ro.observe(el);
  syncUnitButtons();
  startLiveTick();
  load(true);
  CH.timer=setInterval(()=>{if(!document.hidden&&CH.mode==='native')load(false)},15000);
}
function unmountChart(){stopLiveTick();clearInterval(CH.timer);CH.timer=null;clearInterval(CH.clock);CH.clock=null;CH.ro?.disconnect();CH.ro=null;clearDrawings();try{CH.chart?.remove()}catch(e){}CH.chart=CH.cs=CH.vs=CH.ls=CH.area=CH.ema9=CH.ema21=null;CH.coin=null;CH.lastPx=null;CH.seq++;const live=document.getElementById('chLive');if(live)live.classList.add('hidden')}
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
  const bars=cd.map(barOf).filter(b=>b&&isFinite(b.open)&&isFinite(b.high)&&isFinite(b.low)&&isFinite(b.close));
  if(!bars.length)return;
  const last=bars[bars.length-1],first=bars[0];
  CH.lastBarUp=!(last&&last.close<last.open);
  const raw=cd[cd.length-1]&&cd[cd.length-1].c;
  if(raw!=null&&isFinite(+raw)){CH.lastUsd=+raw;CH.lastPx=+raw}
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
  let cd=saneCandles(j.candles||[]);
  if(['1s','15s','30s'].includes(tf)){
    const syn=synthBars(tf);
    if(syn.length>=cd.length)cd=syn;
  }
  if(!cd.length){chMsg(`No trades in this timeframe yet. Try a longer timeframe or <a href="#" onclick="setMode('dex');return false">the DexScreener chart</a>.`);CH.cs.setData([]);if(CH.vs)CH.vs.setData([]);if(CH.ls)CH.ls.setData([]);return}
  chMsg('');
  pushBars(cd);
  if(CH.fit){fitChart();CH.fit=false}
  CH.lastT=cd[cd.length-1].t;
  const last=scalePx(cd[cd.length-1].c),first=scalePx(cd[0].o),chg=windowChg();
  const priceEl=document.getElementById('chPrice');
  const prevPx=scalePx(rawPx());
  const simple=isFinite(last)&&Math.abs(last)>=1e-4&&(prevPx==null||Math.abs(prevPx)>=1e-4);
  if(priceEl){
    if(prevPx!=null&&prevPx!==last&&simple&&window.XS&&XS.motionOn()){
      priceEl.dataset.v=prevPx;
      XS.tick(priceEl,last,v=>fmtPx(v));
      XS.flash(priceEl);
    }else{priceEl.innerHTML=fmtPx(last)}
  }
  if(isFinite(+cd[cd.length-1].c)){CH.lastUsd=+cd[cd.length-1].c;CH.lastPx=+cd[cd.length-1].c}
  const winH=(cd[cd.length-1].t-cd[0].t)/3600, winTxt=winH<48?Math.max(1,Math.round(winH))+'h':Math.round(winH/24)+'d';
  const chgEl=document.getElementById('chChg');
  if(chgEl)chgEl.innerHTML=chg==null?'':`${pct(chg)} <small class="muted">over ${winTxt}</small>`;
  const srcEl=document.getElementById('chSrc');
  if(srcEl){const nm={jupiter:'Jupiter (real candles)',geckoterminal:'GeckoTerminal','geckoterminal-direct':'GeckoTerminal (your browser)','geckoterminal+snapshots':'GeckoTerminal + our price snapshots',snapshots:'our own price snapshots (no volume)',derived:'approximate, from DexScreener 5m/1h/24h changes'}[j.source]||j.source||'GeckoTerminal';
    srcEl.textContent='Candles: '+nm+(j.approximate?' · approximate':'')+' · 15s'}
  const lim=document.getElementById('chLim');
  const thin=cd.length<=7;
  const on=thin||!!j.limited||j.source==='derived'||j.source==='snapshots';
  if(lim){lim.classList.toggle('hidden',!on);lim.textContent=thin?'Limited history':(j.limited?'Limited history':'Limited history');
    lim.title=thin?'This token has very few candles in this timeframe yet.':(j.limited_reason||'Exchange candles are rate-limited right now, so this chart is built from our own price snapshots (no volume).')}
  const lim2=document.getElementById('chLimited');if(lim2){lim2.classList.toggle('hidden',!thin)}
  const ft=j.fetched_at?new Date(j.fetched_at*1000):new Date();
  const upd=document.getElementById('chUpd');
  if(upd)upd.textContent=' · updated '+ft.toLocaleTimeString('en-GB',{timeZone:'Asia/Kuala_Lumpur'})+' MYT'+(j.stale?' (delayed)':'');
  try{document.dispatchEvent(new CustomEvent('xschart',{detail:{mint:c.mint,last:cd[cd.length-1].c,candles:cd}}))}catch(e){}
}

function tfSec(tf){return ({'1s':1,'15s':15,'30s':30,'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400})[tf]||900}
function bucketT(t,tf){const s=tfSec(tf||CH.tf);return Math.floor(t/s)*s}
function stopLiveTick(){if(CH.tickTimer){clearTimeout(CH.tickTimer);CH.tickTimer=null}CH.tickGen=(CH.tickGen||0)+1}

function recordTick(px,ts){
  if(!(px>0))return;
  CH.tickBuf=CH.tickBuf||[];
  CH.tickBuf.push({t:ts||Date.now()/1000,px:+px});
  if(CH.tickBuf.length>3600)CH.tickBuf=CH.tickBuf.slice(-2400);
}
function synthBars(tf){
  const step=tfSec(tf);const src=CH.tickBuf||[];
  if(!src.length)return [];
  const out=[];
  for(const p of src){
    const t=bucketT(p.t,tf);
    const last=out[out.length-1];
    if(!last||last.t!==t)out.push({t,o:p.px,h:p.px,l:p.px,c:p.px,v:0});
    else{if(p.px>last.h)last.h=p.px;if(p.px<last.l)last.l=p.px;last.c=p.px}
  }
  return out;
}
function startLiveTick(){
  stopLiveTick();
  CH.tickBackoff=1000;
  const gen=CH.tickGen;
  const loop=async()=>{
    if(gen!==CH.tickGen)return;
    const visible=typeof document==='undefined'||document.visibilityState==='visible';
    const mint=(CH.coin&&CH.coin.mint)||(typeof TK!=='undefined'&&TK.cur&&TK.cur.mint);
    if(!visible){CH.tickTimer=setTimeout(loop,1000);return}
    if(!mint){CH.tickTimer=setTimeout(loop,CH.tickBackoff||1000);return}
    try{
      const r=await fetch('/api/tick/'+encodeURIComponent(mint),{credentials:'same-origin',cache:'no-store'});
      const j=await r.json().catch(()=>({}));
      if(gen!==CH.tickGen)return;
      if(r.status===429||j.stale||j.retry_s){
        const rs=Math.max(3,Math.min(5,+(j.retry_s||3)));
        CH.tickBackoff=rs*1000;
      }else CH.tickBackoff=1000;
      if(j&&j.px)applyLiveTick(j);
    }catch(e){CH.tickBackoff=Math.min(5000,Math.max(3000,(CH.tickBackoff||1000)+1000))}
    if(gen===CH.tickGen)CH.tickTimer=setTimeout(loop,CH.tickBackoff||1000);
  };
  loop();
}
function applyLiveTick(j){
  if(!j||!(+j.px>0))return;
  if(CH.coin&&j.mint&&CH.coin.mint&&j.mint!==CH.coin.mint)return;
  if(CH.coin){if(j.px)CH.coin.px=j.px;if(j.mc)CH.coin.mc=j.mc;if(j.supply)CH.coin.supply=j.supply}
  const had=validSupply(CH.supply);
  resolveSupply(j);
  if(CH.unit==='mcap'&&!had&&validSupply(CH.supply)&&CH.lastBars&&CH.lastBars.length){pushBars(CH.lastBars);fitChart()}
  syncUnitButtons();
  const px=+j.px;
  const now=Date.now()/1000;
  const bkt=bucketT(now,CH.tf);
  if(CH.lastBars&&CH.lastBars.length&&CH.mode==='native'){
    const last=CH.lastBars[CH.lastBars.length-1];
    const step=tfSec(CH.tf);
    if(last&&(last.t===bkt||last.t>bkt-step||(last.t<bkt&&bkt-last.t<=step))){
      if(last.t===bkt||(last.t>bkt-step&&last.t<=bkt)){
        last.c=px;if(px>last.h)last.h=px;if(px<last.l)last.l=px;
      }else if(last.t<bkt){
        CH.lastBars.push({t:bkt,o:last.c,h:Math.max(last.c,px),l:Math.min(last.c,px),c:px,v:0});
      }
      const k=CH.lastBars[CH.lastBars.length-1];
      const bar=barOf(k);
      if(bar&&isFinite(bar.close)){
        try{CH.cs&&CH.cs.update(bar)}catch(e){}
        try{CH.area&&CH.area.update({time:bar.time,value:bar.close})}catch(e){}
        try{CH.ls&&CH.ls.update({time:bar.time,value:bar.close})}catch(e){}
        try{
          if(CH.ema){
            const e9=emaPts(CH.lastBars,9),e21=emaPts(CH.lastBars,21);
            if(CH.ema9&&e9.length)CH.ema9.update(e9[e9.length-1]);
            if(CH.ema21&&e21.length)CH.ema21.update(e21[e21.length-1]);
          }
        }catch(e){}
        CH.lastUsd=px;CH.lastPx=px;CH.lastT=bar.time;CH.lastBarUp=bar.close>=bar.open;
        recordTick(px);
        paintLegend(bar,windowChg());syncLivePrice();
      }
    }
  }
  paintTokenLive(j);
  try{if(j.sol_usd&&typeof paintSol==='function')paintSol(j.sol_usd)}catch(e){}
  try{window.paperOnTick&&window.paperOnTick(j)}catch(e){}
}
function paintTokenLive(j){
  if(!j)return;
  const pxEl=document.getElementById('tkPxVal');
  if(pxEl&&j.px){const t=fmtNum(j.px);pxEl.textContent=t==='—'?t:'$'+t}
  const mcEl=document.getElementById('tkMcVal');
  if(mcEl&&j.mc!=null&&typeof usd==='function')mcEl.textContent=usd(j.mc);
  else if(mcEl&&j.mc!=null)mcEl.textContent='$'+fmtCompact(j.mc);
}
if(typeof document!=='undefined'){
  document.addEventListener('visibilitychange',()=>{
    if(!CH.coin)return;
    if(document.visibilityState==='visible'){CH.tickBackoff=1000;startLiveTick()}
    else stopLiveTick();
  });
}
