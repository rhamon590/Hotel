(function(){
'use strict';
const stateNode=document.getElementById('hc-state');if(!stateNode)return;
let state;try{state=JSON.parse(stateNode.textContent);}catch(error){return;}
const root=document.documentElement;
if(document.querySelector('.hc-page'))document.body.classList.add('hc-settings-page');
let applying=true;
function applyTheme(theme){if(typeof window.applyHotelTheme==='function')window.applyHotelTheme(theme);else{root.dataset.theme=theme;root.style.colorScheme=theme;}}
applyTheme(state.tema);applying=false;
root.dataset.hcMotion=state.movimento?'normal':'reduced';
document.body.classList.toggle('hc-compact',!!state.compacto);
const menu=document.getElementById('hc-account-menu'),userbox=document.querySelector('.main-header .userbox');
if(menu&&userbox){userbox.insertBefore(menu,userbox.firstChild);document.body.classList.add('hc-enhanced');}
const brand=document.querySelector('.main-header .brand');
if(brand){const title=brand.querySelector('.brand-text strong'),subtitle=brand.querySelector('.brand-text small'),logo=brand.querySelector('.brand-logo');if(title)title.textContent=state.nome;if(subtitle)subtitle.textContent=state.subtitulo;if(logo&&state.logo){const img=document.createElement('img');img.src=state.logo;img.alt='Logo do hotel';img.className='hc-brand-image';logo.replaceChildren(img);}}
if(menu){document.addEventListener('click',event=>{if(menu.open&&!menu.contains(event.target))menu.open=false;});document.addEventListener('keydown',event=>{if(event.key==='Escape'&&menu.open){menu.open=false;menu.querySelector('summary').focus();}});}
function csrf(){const m=document.querySelector('meta[name="hotel-csrf"]');const i=document.querySelector('input[name="hotel_csrf"]');return m?m.content:i?i.value:'';}
let themeQueue=Promise.resolve();
document.addEventListener('hotelpro:themechange',event=>{if(applying||!event.detail||!['light','dark'].includes(event.detail.theme))return;const tema=event.detail.theme;themeQueue=themeQueue.catch(()=>{}).then(async()=>{const response=await fetch(state.temaUrl,{method:'POST',credentials:'same-origin',keepalive:true,headers:{'Content-Type':'application/json','X-CSRFToken':csrf(),Accept:'application/json'},body:JSON.stringify({tema})});if(!response.ok||response.redirected)throw new Error('Tema salvo somente neste aparelho.');});themeQueue.catch(()=>{});});
const sections=[...document.querySelectorAll('[data-hc-section]')],tabs=[...document.querySelectorAll('[data-hc-tab]')];
function tab(name){if(!sections.some(s=>s.dataset.hcSection===name))name=sections[0]?.dataset.hcSection;if(!name)return;sections.forEach(s=>{s.hidden=s.dataset.hcSection!==name;});tabs.forEach(t=>{if(t.dataset.hcTab===name)t.setAttribute('aria-current','page');else t.removeAttribute('aria-current');});}
if(sections.length){const initial=document.querySelector('[data-hc-initial]').dataset.hcInitial;tab(location.hash.startsWith('#hc-')?location.hash.slice(4):initial);tabs.forEach(t=>t.addEventListener('click',event=>{event.preventDefault();tab(t.dataset.hcTab);history.replaceState(null,'','#hc-'+t.dataset.hcTab);}));window.addEventListener('hashchange',()=>tab(location.hash.slice(4)));}
const dirtyForms=new Set();let submitting=false;
document.querySelectorAll('[data-hc-dirty]').forEach(form=>{let dirty=false;const changed=()=>{dirty=true;dirtyForms.add(form);const note=form.querySelector('.hc-unsaved');if(note)note.hidden=false;};form.addEventListener('input',changed);form.addEventListener('change',changed);form.addEventListener('submit',event=>{if(!form.checkValidity())return;setTimeout(()=>{if(event.defaultPrevented)return;submitting=true;dirtyForms.delete(form);},0);});form.addEventListener('reset',()=>{dirty=false;dirtyForms.delete(form);const note=form.querySelector('.hc-unsaved');if(note)note.hidden=true;});});
window.addEventListener('beforeunload',event=>{if(!submitting&&dirtyForms.size){event.preventDefault();event.returnValue='';}});
window.addEventListener('pageshow',()=>{submitting=false;document.querySelectorAll('[data-hc-sending]').forEach(b=>{b.disabled=false;b.textContent=b.dataset.hcSending;delete b.dataset.hcSending;});});
document.querySelectorAll('.hc-page form[method="post"]').forEach(form=>form.addEventListener('submit',event=>{if(!event.submitter)return;const button=event.submitter;const label=button.textContent;setTimeout(()=>{if(event.defaultPrevented)return;submitting=true;button.dataset.hcSending=label;button.textContent='Salvando…';button.disabled=true;},0);}));
document.querySelectorAll('[data-hc-preview]').forEach(input=>{let old=null;input.addEventListener('change',()=>{if(old)URL.revokeObjectURL(old);const file=input.files[0];if(!file)return;const preview=document.getElementById(input.dataset.hcPreview);if(!preview)return;const image=document.createElement('img');old=URL.createObjectURL(file);image.src=old;image.alt='Prévia da imagem selecionada';image.onload=()=>preview.replaceChildren(image);});});
const test=document.getElementById('hc-test-toast'),toast=document.getElementById('hc-test-message');if(test&&toast){test.addEventListener('click',()=>{toast.hidden=false;});toast.querySelector('button').addEventListener('click',()=>{toast.hidden=true;});}
}());
