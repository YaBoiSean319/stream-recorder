function renderInterruptions(target,interruptions) {
 if(!interruptions?.length)return;
 const detail=element('details',undefined,'record-meta');
 detail.append(element('summary',`${interruptions.length} interruption(s) — unavailable footage was not captured.`));
 const events=element('ul');
 for(const event of interruptions)events.append(element('li',`${date(event.at)} · ${event.reason||'Capture interrupted'}`));
 detail.append(events);target.append(detail);
}
'use strict';
const $ = id => document.getElementById(id);
let active = null, currentSource = null, hls = null, refreshing = false, deleteId = null;
const busyStates = ['recording', 'reconnecting', 'recovering', 'processing'];
const bytes = n => {n=Number(n)||0; if(n<1024)return `${n} B`; const units=['KiB','MiB','GiB','TiB']; let i=-1; do{n/=1024;i++;}while(n>=1024&&i<3);return `${n.toFixed(1)} ${units[i]}`;};
const date = value => value ? new Date(value).toLocaleString() : 'Until stopped';
function notice(message='') {$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path, options={}) {const response=await recorderFetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});const raw=await response.text();let body;try{body=JSON.parse(raw);}catch{body={message:raw};}if(!response.ok)throw Error(body.error?.message||body.error||body.message||`Request failed (${response.status})`);return body;}
function preview(source) {if(source===currentSource)return;currentSource=source;$('source-label').textContent=source||'Not configured';$('open-settings').hidden=!!source;if(hls){hls.destroy();hls=null;}$('preview-message').textContent='Preview is independent of recording.';const video=$('preview');if(!source){video.removeAttribute('src');video.load();$('preview-message').textContent='Configure a stream in Settings';return;}if(window.Hls?.isSupported()){hls=new Hls();hls.loadSource(source);hls.attachMedia(video);hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal)$('preview-message').textContent='Preview unavailable. Check the source URL, browser access, and stream CORS policy. Recording runs separately on the server.';});}else if(video.canPlayType('application/vnd.apple.mpegurl'))video.src=source;else $('preview-message').textContent='This browser cannot play HLS. Try a browser with Media Source Extensions or native HLS support.';}
function status(data) {active=data.active;const state=active?.state||'idle';$('state').textContent=state[0].toUpperCase()+state.slice(1);$('state').className=`pill ${state}`;$('start').disabled=!!active||!currentSource;$('duration').disabled=!!active;$('stop').hidden=!active||state==='processing';$('stop').disabled=false;$('session-details').hidden=!active;$('start-form').hidden=!!active;$('recording-description').textContent=active?({recording:'Capturing your stream. You can safely close this page.',reconnecting:'The source was interrupted. Reconnecting automatically; missing footage cannot be recovered.',recovering:'Recovering the saved session after a service restart.',processing:'Preparing your MP4. A new recording can start when processing finishes.'}[state]||'Review the session details below.'):'Ready when you are. Recording continues even after you close this page.';if(active){$('started').textContent=date(active.started_at);$('elapsed').textContent=`${Math.max(0,Math.floor((Date.now()-Date.parse(active.started_at))/60000))} min`;$('deadline').textContent=date(active.deadline);$('processing').hidden=state!=='processing';const p=Math.round(active.progress||0);$('progress').value=p;$('progress-label').textContent=`${p}%`;$('session-error').textContent=active.error||'';$('interruptions').replaceChildren();renderInterruptions($('interruptions'),active.interruptions);}const s=data.storage||{};$('usage-label').textContent=`${bytes(s.media_bytes)} of ${bytes(s.cap_bytes)} media cap${s.cap_exceeded?' — protected media exceeds cap; free space or unpin/delete recordings.':''}`;$('usage-bar').style.width=`${Math.min(100,100*(s.media_bytes||0)/(s.cap_bytes||1))}%`;$('disk-label').textContent=`Disk: ${bytes(s.free_bytes)} free of ${bytes(s.total_bytes)}`;}
function element(tag,text,className) {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function list(records) {$('count').textContent=records.length;const box=$('recordings');box.replaceChildren();if(!records.length){box.append(element('div','No recordings yet. Start a capture to build your library.','empty'));return;}for(const record of records){const row=element('div',undefined,'record-row');const info=element('div',undefined,'record-info');info.append(element('div',record.filename||`Recording ${record.id}`,'record-name'),element('div',`${date(record.started_at)} · ${record.state}${record.pinned?' · Pinned':''}`,'record-meta'));if(record.error)info.append(element('p',record.error,'record-meta'));renderInterruptions(info,record.interruptions);const actions=element('div',undefined,'record-actions');if(record.state==='ready'){const a=element('a','↓ Download');a.href=`/api/recordings/${encodeURIComponent(record.id)}/download`;a.setAttribute('download','');actions.append(a);}const pin=element('button',record.pinned?'Unpin':'Pin');pin.setAttribute('aria-label',`${record.pinned?'Unpin':'Pin'} ${record.filename||record.id}`);pin.disabled=busyStates.includes(record.state);pin.onclick=()=>act(()=>api(`/api/recordings/${encodeURIComponent(record.id)}`,{method:'PATCH',body:JSON.stringify({pinned:!record.pinned})}));const del=element('button','Delete');del.className='danger';del.disabled=busyStates.includes(record.state);del.setAttribute('aria-label',`Delete ${record.filename||record.id}`);del.onclick=()=>{deleteId=record.id;$('delete-description').textContent=record.filename||date(record.started_at);$('delete-dialog').showModal();};actions.append(pin,del);row.append(info,actions);box.append(row);}}
async function refresh() {if(refreshing||$('delete-dialog').open)return;refreshing=true;try{const [s,records]=await Promise.all([api('/api/status'),api('/api/recordings')]);preview(s.preview_url||'');$('source-label').textContent=s.source_configured?'Configured HLS stream':'Not configured';status(s);list(Array.isArray(records)?records:records.recordings);document.querySelectorAll('.record-actions .danger').forEach(button=>button.hidden=accessState.role!=='admin');$('connection').textContent='Connected';}catch(e){$('connection').textContent='Disconnected';$('start').disabled=true;$('stop').disabled=true;notice(e.message);}finally{refreshing=false;}}
async function act(fn) {try{notice();await fn();$('stop').disabled=false;await refresh();}catch(e){notice(e.message);}}
$('start-form').onsubmit=e=>{e.preventDefault();const value=$('duration').value;const seconds=value?Number(value)*60:null;if(value&&(!Number.isFinite(seconds)||seconds<=0)){notice('Enter a positive recording duration.');return;}$('start').disabled=true;act(()=>api('/api/recordings',{method:'POST',body:JSON.stringify(seconds?{duration_seconds:seconds}:{})})).finally(()=>refresh());};
$('stop').onclick=()=>{if(!active)return;const id=active.id;$('stop').disabled=true;act(()=>api(`/api/recordings/${encodeURIComponent(id)}/stop`,{method:'POST',body:'{}'}));};
$('settings-form').onsubmit=e=>{e.preventDefault();act(async()=>{const body={storage_cap_bytes:Math.floor(Number($('cap').value)*1024**3),source_basic_username:$('source_basic_username').value};if($('source').value.trim())body.source_url=$('source').value.trim();if($('clear-source').checked)body.source_url='';for(const key of ['source_basic_password','source_bearer_token'])if($(key).value)body[key]=$(key).value;if($('source_headers').value.trim())body.source_headers=JSON.parse($('source_headers').value);body.clear_source_secrets=[...document.querySelectorAll('[data-clear-secret]:checked')].map(e=>e.dataset.clearSecret);const settings=await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});loadSource(settings);notice('Settings saved.');});};
$('delete-dialog').addEventListener('close',()=>{if($('delete-dialog').returnValue==='delete'&&deleteId){const id=deleteId;act(()=>api(`/api/recordings/${encodeURIComponent(id)}`,{method:'DELETE'}));}deleteId=null;});
(async()=>{const access=await accessReady;if(access.enabled&&!access.role)return;try{if(access.role==='admin'){const settings=await api('/api/settings');$('cap').value=settings.storage_cap_bytes/1024**3;loadSource(settings);}}catch(e){notice(e.message);}await refresh();setInterval(refresh,2000);})();

function loadSource(settings){$('source').value='';$('source').placeholder=settings.source_configured?'Saved URL hidden; enter to replace':'https://example.org/live.m3u8';$('clear-source').checked=false;for(const key of ['source_basic_password','source_bearer_token','source_headers']){$(key).value='';$(key).placeholder=settings[key+'_configured']?'Saved secret; leave blank to retain':'';}$('source_basic_username').value=settings.source_basic_username||'';document.querySelectorAll('[data-clear-secret]').forEach(e=>e.checked=false);preview(settings.preview_url);$('source-label').textContent=settings.source_configured?'Configured HLS stream':'Not configured';}

function navigate(){if(accessState.enabled&&!accessState.role)return;const settings=location.hash==='#settings';$('settings-screen').hidden=!settings;$('recorder-screen').hidden=settings;document.querySelectorAll('nav a').forEach(a=>a.setAttribute('aria-current',a.hash===(settings?'#settings':'#recorder')?'page':'false'));if(settings)$('settings-title').focus();}
window.addEventListener('hashchange',navigate);navigate();

// Appearance is a browser preference, never shared installation configuration.
const appearanceKey = 'stream-recorder.appearance';
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
let appearance = {theme:'dark', accent:'mint'};
try {
 const saved = JSON.parse(localStorage.getItem(appearanceKey));
 if (saved && ['dark','light','system'].includes(saved.theme) && ['mint','blue','rose'].includes(saved.accent)) appearance = saved;
} catch (_) { /* Unavailable storage or stale preferences keep safe defaults. */ }
function applyAppearance() {
 document.documentElement.dataset.theme = appearance.theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : appearance.theme;
 document.documentElement.dataset.accent = appearance.accent;
 $('theme').value = appearance.theme;
 $('accent').value = appearance.accent;
}
function saveAppearance() {
 appearance = {theme:$('theme').value, accent:$('accent').value};
 try { localStorage.setItem(appearanceKey, JSON.stringify(appearance)); } catch (_) { /* Still usable for this page session. */ }
 applyAppearance();
}
$('theme').addEventListener('change',saveAppearance);
$('accent').addEventListener('change',saveAppearance);
systemTheme.addEventListener('change',() => {if(appearance.theme === 'system')applyAppearance();});
applyAppearance();
