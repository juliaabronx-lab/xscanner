(()=>{
const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const B=document.body, local=B.dataset.local==='1', signup=B.dataset.signup||'closed', next=(B.dataset.next||'/');
let csrf='';
const msg=(t,ok)=>{const m=$('#lgMsg');m.textContent=t||'';m.classList.toggle('hidden',!t);m.classList.toggle('ok',!!ok)};
const mode=t=>{
  const up=t==='up';
  $('#tabIn').classList.toggle('on',!up);$('#tabUp').classList.toggle('on',up);
  $('#tabIn').setAttribute('aria-selected',String(!up));$('#tabUp').setAttribute('aria-selected',String(up));
  $('#formIn').classList.toggle('hidden',up);$('#formUp').classList.toggle('hidden',!up);
  $('#lgTitle').textContent=up?'Create your account':'Welcome back';
  $('#lgLead').textContent=up?'Pick an ID and your own password. Your watchlist and wallets then follow you across devices.':'Sign in to sync your watchlist, saved wallets and Pulse presets across devices.';
  msg('');const f=up?$('#formUp'):$('#formIn');const i=f.querySelector('input');i&&i.focus({preventScroll:true})};
async function status(){
  try{const r=await fetch('/api/auth/status',{credentials:'same-origin'});const d=await r.json();csrf=d.csrf||'';
    if(d.user){location.replace(next);return}
    if(d.storage==='ephemeral')msg('Heads up: this server stores accounts in a temporary database, so they can be wiped on restart.',false);
  }catch(e){msg('Cannot reach the server. Check your connection and reload.')}}
async function post(url,body){
  const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(body)});
  const d=await r.json().catch(()=>({}));
  if(d&&typeof d.csrf==='string')csrf=d.csrf;
  if(r.status===403&&d.csrf_failed){await status();d.error='Session refreshed. Please press the button again.'}
  return {r,d}}
function busy(f,on){const b=f.querySelector('.lg-go');b.disabled=on;b.dataset.t=b.dataset.t||b.textContent;b.textContent=on?'Please wait…':b.dataset.t}
const strength=p=>{let s=0;if(p.length>=8)s++;if(p.length>=12)s++;if(/[a-z]/.test(p)&&/[A-Z]/.test(p))s++;if(/\d/.test(p))s++;if(/[^A-Za-z0-9]/.test(p))s++;return Math.min(4,s)};
if(!local){$$('#formIn,#formUp,#lgTabs,.lg-lead').forEach(e=>e.classList.add('hidden'));$('#lgTitle').textContent='Sign in'}
else{
  if(signup==='closed'){$('#tabUp').classList.add('hidden')}
  if(signup==='invite')$('#inviteRow').classList.remove('hidden');
  $$('#lgTabs button').forEach(b=>b.onclick=()=>mode(b.dataset.t));
  $$('[data-eye]').forEach(b=>b.onclick=()=>{const i=b.previousElementSibling;const show=i.type==='password';i.type=show?'text':'password';b.textContent=show?'Hide':'Show';b.setAttribute('aria-label',show?'Hide password':'Show password')});
  const pw=$('#formUp input[name=password]');pw.addEventListener('input',()=>{const s=strength(pw.value);$('#pwBar').style.width=(pw.value?(s+1)*20:0)+'%';$('#pwBar').dataset.s=s});
  $('#formIn').addEventListener('submit',async e=>{e.preventDefault();const f=e.target;msg('');
    const id=f.id.value.trim(),p=f.password.value;if(!id||!p){msg('Enter your username and password.');return}
    busy(f,true);const {r,d}=await post('/api/auth/login',{id,password:p}).catch(()=>({r:{},d:{error:'Network error. Try again.'}}));busy(f,false);
    if(d.ok){msg('Signed in. Redirecting…',true);location.replace(next)}else{msg(d.error||'Could not sign in.');f.password.value='';f.password.focus()}});
  $('#formUp').addEventListener('submit',async e=>{e.preventDefault();const f=e.target;msg('');
    const body={username:f.username.value.trim(),email:f.email.value.trim(),password:f.password.value,invite:f.invite?f.invite.value.trim():''};
    if(!/^[A-Za-z0-9][A-Za-z0-9_.-]{2,23}$/.test(body.username)){msg('Username: 3-24 letters, digits, . - _ (start with a letter or digit).');return}
    if(body.password.length<8){msg('Use at least 8 characters for the password.');return}
    busy(f,true);const {r,d}=await post('/api/auth/signup',body).catch(()=>({r:{},d:{error:'Network error. Try again.'}}));busy(f,false);
    if(d.ok){msg('Account created. Redirecting…',true);location.replace(next)}else{msg(d.error||'Could not create the account.');if(d.field){const i=f.querySelector('[name='+d.field+']');i&&i.focus()}}});
  if(location.hash==='#signup'&&signup!=='closed')mode('up');
  status()}
})();
