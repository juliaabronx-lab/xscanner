// xScanner accounts + sync. Guests keep everything in localStorage (works as before). Signed-in users get the watchlist, saved wallets and
// Pulse presets mirrored to their account on the server (per user, behind login). Writes carry the session's CSRF token.
(()=>{
'use strict';
const MAP={watch:['xs_watch_v1','xs_watch_cfg_v1'],wallets:['xs_wallets_v1'],prefs:['xs_pulse_presets_v1']};
const KEYKIND={};Object.keys(MAP).forEach(k=>MAP[k].forEach(x=>KEYKIND[x]=k));
const MARK='xs_sync_v1';
const S={local:false,google:false,user:null,csrf:'',signup:'closed',storage:null,state:'guest',vers:{},timers:{},dirty:{},applying:false,last:{},pullT:null};
const LG=(k,d)=>{try{const v=JSON.parse(localStorage.getItem(k));return v==null?d:v}catch(e){return d}};
const rawSet=Storage.prototype.setItem.bind(localStorage);
const here=()=>location.pathname+location.search+location.hash;
const nextQ=()=>'?next='+encodeURIComponent(location.pathname==='/login'?'/':here());

// ---- bodies <-> localStorage
function body(kind){
  if(kind==='watch')return {list:LG('xs_watch_v1',[]),cfg:LG('xs_watch_cfg_v1',{pct:20,notify:false})};
  if(kind==='wallets')return {list:LG('xs_wallets_v1',[])};
  return {presets:LG('xs_pulse_presets_v1',{})};
}
const hasItems=(kind,b)=>kind==='prefs'?Object.keys(b.presets||{}).length>0:((b.list||[]).length>0);
function merge(kind,loc,srv){
  if(!srv)return loc;
  if(kind==='prefs')return {presets:{...(loc.presets||{}),...(srv.presets||{})}};
  const key=kind==='watch'?'m':'a',seen=new Set(),out=[];
  for(const it of [...(srv.list||[]),...(loc.list||[])]){if(!it||seen.has(it[key]))continue;seen.add(it[key]);out.push(it)}
  return kind==='watch'?{list:out.slice(0,300),cfg:srv.cfg||loc.cfg}:{list:out.slice(0,25)};
}
function apply(kind,b){
  S.applying=true;
  try{
    if(kind==='watch'){const v=JSON.stringify(b.list||[]),c=JSON.stringify(b.cfg||{pct:20,notify:false});rawSet('xs_watch_v1',v);rawSet('xs_watch_cfg_v1',c);S.last.xs_watch_v1=v;S.last.xs_watch_cfg_v1=c}
    else if(kind==='wallets'){const v=JSON.stringify(b.list||[]);rawSet('xs_wallets_v1',v);S.last.xs_wallets_v1=v}
    else{const v=JSON.stringify(b.presets||{});rawSet('xs_pulse_presets_v1',v);S.last.xs_pulse_presets_v1=v}
  }finally{S.applying=false}
}
function snapshot(){Object.keys(KEYKIND).forEach(k=>{S.last[k]=localStorage.getItem(k)})}

// ---- network
async function api(method,url,payload){
  const h={'Accept':'application/json'};if(payload!==undefined)h['Content-Type']='application/json';if(method!=='GET')h['X-CSRF-Token']=S.csrf;
  const r=await fetch(url,{method,credentials:'same-origin',headers:h,body:payload!==undefined?JSON.stringify(payload):undefined});
  const d=await r.json().catch(()=>({}));if(d&&typeof d.csrf==='string')S.csrf=d.csrf;return {r,d};
}
async function loadMe(){
  try{const {r,d}=await api('GET','/api/me');
    S.local=!!d.local;S.google=!!d.google;S.signup=d.signup||'closed';S.storage=d.storage||null;S.csrf=d.csrf||S.csrf;
    const was=S.user;S.user=d.user||null;
    return {was,now:S.user}}catch(e){return {was:S.user,now:S.user,offline:true}}
}

// ---- push / pull
function schedule(kind){
  if(!S.user)return;S.dirty[kind]=true;setState('syncing');
  clearTimeout(S.timers[kind]);S.timers[kind]=setTimeout(()=>push(kind),900);
}
async function push(kind,retry){
  if(!S.user)return;
  try{
    const {r,d}=await api('PUT','/api/user/data/'+kind,{body:body(kind)});
    if(r.status===403&&d.csrf_failed&&!retry){await loadMe();return push(kind,true)}
    if(r.status===401){await signedOut(true);return}
    if(!r.ok||!d.ok)throw new Error(d.error||('HTTP '+r.status));
    S.vers[kind]=d.v;S.dirty[kind]=false;saveMark();
    if(!Object.values(S.dirty).some(Boolean))setState('ok');
  }catch(e){setState('error');clearTimeout(S.timers[kind]);S.timers[kind]=setTimeout(()=>push(kind),30000)}
}
function saveMark(){rawSet(MARK,JSON.stringify({u:S.user&&S.user.name,vers:S.vers}))}
async function pull(){
  if(!S.user)return;
  try{
    const {r,d}=await api('GET','/api/user/data');
    if(r.status===401){await signedOut(true);return}
    if(!r.ok||!d.ok)throw new Error(d.error||'HTTP '+r.status);
    const mark=LG(MARK,{}),first=mark.u!==S.user.name;let changed=false;
    if(first)S.vers={};else S.vers=mark.vers||{};
    for(const kind of Object.keys(MAP)){
      const srv=d.data&&d.data[kind],loc=body(kind);
      if(first){
        const m=merge(kind,loc,srv&&srv.body);
        if(JSON.stringify(m)!==JSON.stringify(loc)){apply(kind,m);changed=true}
        if(!srv||JSON.stringify(m)!==JSON.stringify(srv.body)){if(hasItems(kind,m))schedule(kind)}
        if(srv)S.vers[kind]=srv.v;
      }else if(srv&&srv.v>(S.vers[kind]||0)&&!S.dirty[kind]){
        apply(kind,srv.body);S.vers[kind]=srv.v;changed=true;
      }else if(!srv&&hasItems(kind,loc)&&!S.dirty[kind]){schedule(kind)}
    }
    saveMark();snapshot();
    if(changed&&window.XS_reload)window.XS_reload();
    if(!Object.values(S.dirty).some(Boolean))setState('ok');
  }catch(e){setState('error')}
}
function startPolling(){clearInterval(S.pullT);S.pullT=setInterval(()=>{if(!document.hidden)pull()},60000)}

// ---- UI state
function setState(st){S.state=st;paint()}
function pill(){const p=$('#syncPill');if(!p)return;
  if(!S.user){p.textContent='';p.className='syncpill hidden';return}
  const t={ok:['✓ Synced','ok'],syncing:['⟳ Syncing…','busy'],error:['⚠ Sync issue','err'],guest:['','']}[S.state]||['✓ Synced','ok'];
  p.textContent=t[0];p.className='syncpill '+t[1];p.title=S.state==='error'?'Could not reach the server. Your changes are kept in this browser and retried.':'Your watchlist, wallets and presets are saved to your account.'}
function notes(){
  const warn=S.storage==='ephemeral'?' <span class="amber">Heads up: this server keeps accounts in a temporary database.</span>':'';
  const html=S.user?`<span class="up">✓</span> Synced to your account <b>${esc(S.user.name)}</b>. Your watchlist, wallets and presets follow you to any device you sign in on.${warn}`
    :S.local?`Guest mode: this list is saved in this browser only. <a href="/login${nextQ()}">Sign in</a> or <a href="/login${nextQ()}#signup">create an account</a> to sync it across devices.`
    :'Saved in this browser only.';
  $$('[data-sync-note]').forEach(e=>e.innerHTML=html)}
function box(){
  const b=$('#authBox');if(!b)return;
  if(S.user){
    if(S.user.provider==='local'){
      b.innerHTML=`<button type="button" class="uchip" id="uBtn" aria-haspopup="menu" aria-expanded="false"><span class="udot"></span><span class="uname">${esc(S.user.name)}</span><span class="ucar">▾</span></button>
      <div class="umenu hidden" id="uMenu" role="menu"><div class="umh">Signed in as <b>${esc(S.user.name)}</b>${S.user.email?'<small>'+esc(S.user.email)+'</small>':''}</div>
        <button type="button" role="menuitem" data-a="pw">Change password</button><button type="button" role="menuitem" data-a="out">Sign out</button><button type="button" role="menuitem" class="danger" data-a="del">Delete account…</button></div>`;
    }else{
      b.innerHTML=`<span class="uchip"><span class="udot"></span><span class="uname">${esc(S.user.name||S.user.email||'Signed in')}</span></span><a class="ghost ulogout" href="/logout">Sign out</a>`;
    }
  }else if(S.local||S.google){
    b.innerHTML=`<a class="ghost signin" href="/login${nextQ()}">Sign in</a>${S.signup!=='closed'?`<a class="primary signup" href="/login${nextQ()}#signup">Sign up</a>`:''}`;
  }else b.innerHTML='';
}
function paint(){pill();notes();box()}

document.addEventListener('click',e=>{
  const ub=e.target.closest('#uBtn');const menu=$('#uMenu');
  if(ub){const open=menu.classList.toggle('hidden')===false;ub.setAttribute('aria-expanded',String(open));return}
  const a=e.target.closest('#uMenu [data-a]');
  if(a){menu.classList.add('hidden');const k=a.dataset.a;if(k==='out')signOut();else dialog(k);return}
  if(menu&&!menu.classList.contains('hidden')&&!e.target.closest('#uMenu'))menu.classList.add('hidden');
});
document.addEventListener('keydown',e=>{if(e.key==='Escape'){const m=$('#uMenu');if(m)m.classList.add('hidden')}});

async function signedOut(clear){
  S.user=null;S.dirty={};Object.values(S.timers).forEach(clearTimeout);clearInterval(S.pullT);
  if(clear){Object.keys(KEYKIND).forEach(k=>{try{localStorage.removeItem(k)}catch(_){}});try{localStorage.removeItem(MARK)}catch(_){}snapshot();if(window.XS_reload)window.XS_reload()}
  S.state='guest';await loadMe();paint();
}
async function signOut(){
  try{await api('POST','/api/auth/logout',{})}catch(e){}
  await signedOut(true);   // a shared browser must not keep the previous user's lists
  if(window.toast)toast('Signed out. Lists synced to your account stay there; sign in again to get them back.');
}

// ---- change password / delete account dialog
function dialog(kind){
  let dlg=$('#acctDlg');if(dlg)dlg.remove();
  dlg=document.createElement('dialog');dlg.id='acctDlg';dlg.className='acctdlg';
  const pw=kind==='pw';
  dlg.innerHTML=`<form method="dialog" class="adf" autocomplete="off"><h3>${pw?'Change password':'Delete account'}</h3>
   ${pw?'<label>Current password<input type="password" name="cur" autocomplete="current-password" required></label><label>New password<input type="password" name="new" autocomplete="new-password" minlength="8" required></label><p class="muted">At least 8 characters, not a common password. Other devices are signed out.</p>'
        :'<p class="muted">This permanently deletes your account and the watchlist, wallets and presets stored on the server. This browser keeps nothing after sign-out. This cannot be undone.</p><label>Confirm with your password<input type="password" name="cur" autocomplete="current-password" required></label>'}
   <div class="adm hidden" role="alert"></div>
   <div class="ada"><button type="button" class="ghost" data-x>Cancel</button><button type="submit" class="${pw?'primary':'primary danger'}">${pw?'Update password':'Delete my account'}</button></div></form>`;
  document.body.appendChild(dlg);dlg.showModal();
  const f=dlg.querySelector('form'),m=dlg.querySelector('.adm');
  dlg.querySelector('[data-x]').onclick=()=>dlg.close();dlg.addEventListener('close',()=>dlg.remove());
  f.onsubmit=async ev=>{ev.preventDefault();m.className='adm hidden';
    const {r,d}=await api('POST',pw?'/api/auth/password':'/api/auth/delete',pw?{current:f.cur.value,new:f.new.value}:{password:f.cur.value}).catch(()=>({r:{},d:{error:'Network error.'}}));
    if(d.ok){dlg.close();if(pw){if(window.toast)toast('Password updated.')}else{await signedOut(true);if(window.toast)toast('Account deleted.')}}
    else{m.textContent=d.error||'Something went wrong.';m.className='adm'}};
}

// ---- hook localStorage writes so any edit (from trader.js or elsewhere) is pushed
const origSet=Storage.prototype.setItem;
Storage.prototype.setItem=function(k,v){
  origSet.apply(this,arguments);
  if(this===localStorage&&KEYKIND[k]&&!S.applying&&S.user&&S.last[k]!==String(v)){S.last[k]=String(v);schedule(KEYKIND[k])}
};

document.addEventListener('visibilitychange',()=>{if(!document.hidden&&S.user)pull()});
window.XS_account={state:()=>({user:S.user,state:S.state,storage:S.storage})};

(async()=>{
  snapshot();paint();
  const {now}=await loadMe();paint();
  if(now){startPolling();await pull();paint()}
})();
})();
