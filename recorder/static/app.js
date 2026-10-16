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
let active = null, currentSource = null, hls = null, refreshing = false, deleteId = null, renameId = null;
const busyStates = ['recording', 'reconnecting', 'recovering', 'processing'];
const bytes = n => {n=Number(n)||0; if(n<1024)return `${n} B`; const units=['KiB','MiB','GiB','TiB']; let i=-1; do{n/=1024;i++;}while(n>=1024&&i<3);return `${n.toFixed(1)} ${units[i]}`;};
const date = value => value ? new Date(value).toLocaleString() : 'Until stopped';
function notice(message='') {$('notice').textContent=message;$('notice').hidden=!message;}
async function api(path, options={}) {const response=await recorderFetch(path,{...options,headers:{'Content-Type':'application/json',...options.headers}});const raw=await response.text();let body;try{body=JSON.parse(raw);}catch{body={message:raw};}if(!response.ok)throw Error(body.error?.message||body.error||body.message||`Request failed (${response.status})`);return body;}
function preview(source) {if(source===currentSource)return;currentSource=source;$('source-label').textContent=source?'Configured HLS stream':'Not configured';$('open-settings').hidden=!!source;if(hls){hls.destroy();hls=null;}$('preview-message').textContent='Preview is independent of recording.';const video=$('preview');if(!source){video.removeAttribute('src');video.load();$('preview-message').textContent='Configure a stream in Settings';return;}if(window.Hls?.isSupported()){hls=new Hls();hls.loadSource(source);hls.attachMedia(video);hls.on(Hls.Events.ERROR,(_,data)=>{if(data.fatal)$('preview-message').textContent='Preview unavailable. Check the configured source and credentials in Settings, or contact your administrator. Recording runs separately on the server.';});}else if(video.canPlayType('application/vnd.apple.mpegurl'))video.src=source;else $('preview-message').textContent='This browser cannot play HLS. Try a browser with Media Source Extensions or native HLS support.';}
function status(data) {active=data.active;const state=active?.state||'idle';$('state').textContent=state[0].toUpperCase()+state.slice(1);$('state').className=`pill ${state}`;$('start').disabled=!!active||!currentSource;$('duration').disabled=!!active;$('recording-title').disabled=!!active;$('stop').hidden=!active||state==='processing';$('stop').disabled=false;$('session-details').hidden=!active;$('start-form').hidden=!!active;$('recording-description').textContent=active?({recording:'Capturing your stream. You can safely close this page.',reconnecting:'The source was interrupted. Reconnecting automatically; missing footage cannot be recovered.',recovering:'Recovering the saved session after a service restart.',processing:'Preparing your MP4. A new recording can start when processing finishes.'}[state]||'Review the session details below.'):'Ready when you are. Recording continues even after you close this page.';if(active){$('started').textContent=date(active.started_at);$('elapsed').textContent=`${Math.max(0,Math.floor((Date.now()-Date.parse(active.started_at))/60000))} min`;$('deadline').textContent=date(active.deadline);$('processing').hidden=state!=='processing';const p=Math.round(active.progress||0);$('progress').value=p;$('progress-label').textContent=`${p}%`;$('session-error').textContent=active.error||'';$('interruptions').replaceChildren();renderInterruptions($('interruptions'),active.interruptions);}const s=data.storage||{};$('usage-label').textContent=`${bytes(s.media_bytes)} of ${bytes(s.cap_bytes)} media cap${s.cap_exceeded?' — protected media exceeds cap; free space or unpin/delete recordings.':''}`;$('usage-bar').style.width=`${Math.min(100,100*(s.media_bytes||0)/(s.cap_bytes||1))}%`;$('disk-label').textContent=`Disk: ${bytes(s.free_bytes)} free of ${bytes(s.total_bytes)}`;}
function element(tag,text,className) {const el=document.createElement(tag);if(text!==undefined)el.textContent=text;if(className)el.className=className;return el;}
function list(records) {$('count').textContent=records.length;const box=$('recordings');box.replaceChildren();if(!records.length){box.append(element('div','No recordings yet. Start a capture to build your library.','empty'));return;}for(const record of records){const row=element('div',undefined,'record-row');const info=element('div',undefined,'record-info');info.append(element('div',record.title||record.filename||`Recording ${record.id}`,'record-name'),element('div',`${date(record.started_at)} · ${record.state}${record.pinned?' · Pinned':''}`,'record-meta'));if(record.error)info.append(element('p',record.error,'record-meta'));renderInterruptions(info,record.interruptions);const actions=element('div',undefined,'record-actions');if(record.state==='ready'){const a=element('a','↓ Download');a.href=`/api/recordings/${encodeURIComponent(record.id)}/download`;a.setAttribute('download','');actions.append(a);}const rename=element('button','Rename');rename.setAttribute('aria-label',`Rename ${record.title||record.id}`);rename.onclick=()=>{renameId=record.id;$('rename-title').value=record.title||'';$('rename-dialog').showModal();$('rename-title').focus();};actions.append(rename);const pin=element('button',record.pinned?'Unpin':'Pin');pin.setAttribute('aria-label',`${record.pinned?'Unpin':'Pin'} ${record.filename||record.id}`);pin.disabled=busyStates.includes(record.state);pin.onclick=()=>act(()=>api(`/api/recordings/${encodeURIComponent(record.id)}`,{method:'PATCH',body:JSON.stringify({pinned:!record.pinned})}));const del=element('button','Delete');del.className='danger';del.disabled=busyStates.includes(record.state);del.setAttribute('aria-label',`Delete ${record.filename||record.id}`);del.onclick=()=>{deleteId=record.id;$('delete-description').textContent=record.filename||date(record.started_at);$('delete-dialog').showModal();};actions.append(pin,del);window.renderUploadActions?.(record,info,actions);row.append(info,actions);box.append(row);}}
function renderViewerLive(data){const card=$('viewer-count-card');if(!data){card.hidden=true;return;}card.hidden=false;const disabled=data.state==='disabled';$('viewer-count').textContent=disabled?'—':String(data.count);$('viewer-state').textContent=disabled?'Tracking disabled':data.collector_health==='healthy'?'Collection healthy':'Collection unavailable';$('viewer-as-of').textContent=disabled?'Enable tracking in Settings':`As of ${date(data.as_of)} · last playback report ${date(data.last_report_at)}`;}
async function refreshViewerLive(){try{renderViewerLive(await api('/api/v1/viewers/live'));}catch(_){renderViewerLive(null);}}
async function refresh() {if(refreshing||$('delete-dialog').open||$('rename-dialog').open)return;refreshing=true;try{const [s,records,uploads]=await Promise.all([api('/api/v1/status'),api('/api/recordings'),api('/api/v1/uploads')]);window.uploadJobs=uploads.items;preview(s.preview_url||'');$('source-label').textContent=s.source_configured?'Configured HLS stream':'Not configured';status(s);list(Array.isArray(records)?records:records.recordings);document.querySelectorAll('.record-actions .danger').forEach(button=>button.hidden=accessState.role!=='admin');$('connection').textContent='Connected';}catch(e){$('connection').textContent='Disconnected';$('start').disabled=true;$('stop').disabled=true;notice(e.message);}finally{refreshing=false;}}
async function act(fn) {try{notice();await fn();$('stop').disabled=false;await refresh();}catch(e){notice(e.message);}}
$('start-form').onsubmit=e=>{e.preventDefault();const value=$('duration').value;const seconds=value?Number(value)*60:null;if(value&&(!Number.isFinite(seconds)||seconds<=0)){notice('Enter a positive recording duration.');return;}$('start').disabled=true;const title=$('recording-title').value.trim();act(async()=>{await api('/api/recordings',{method:'POST',body:JSON.stringify({...seconds?{duration_seconds:seconds}:{},...title?{title}:{}})});$('recording-title').value='';}).finally(()=>refresh());};
$('stop').onclick=()=>{if(!active)return;const id=active.id;$('stop').disabled=true;act(()=>api(`/api/recordings/${encodeURIComponent(id)}/stop`,{method:'POST',body:'{}'}));};
$('viewer-form').onsubmit=e=>{e.preventDefault();act(async()=>{const result=await api('/api/v1/viewers/config',{method:'PUT',body:JSON.stringify({player_url:$('viewer-player-url').value.trim(),allowed_origin:$('viewer-origin').value.trim(),public_tracking_url:$('viewer-public-url').value.trim(),timezone:$('viewer-timezone').value.trim(),enabled:$('viewer-enabled').checked})});loadViewerSettings(result);notice(result.tracking_key?'Viewer settings saved. Copy the generated snippet now; the tracking key is shown only once.':'Viewer settings saved.');});};
$('viewer-rotate').onclick=()=>act(async()=>{const result=await api('/api/v1/viewers/key/rotate',{method:'POST',body:'{}'});$('viewer-snippet').value=result.snippet;notice('Tracking key rotated. Copy the new snippet now; the previous key is revoked.');});
$('settings-form').onsubmit=e=>{e.preventDefault();act(async()=>{const body={storage_cap_bytes:Math.floor(Number($('cap').value)*1024**3),source_basic_username:$('source_basic_username').value};if($('source').value.trim())body.source_url=$('source').value.trim();if($('clear-source').checked)body.source_url='';for(const key of ['source_basic_password','source_bearer_token'])if($(key).value)body[key]=$(key).value;if($('source_headers').value.trim())body.source_headers=JSON.parse($('source_headers').value);body.clear_source_secrets=[...document.querySelectorAll('[data-clear-secret]:checked')].map(e=>e.dataset.clearSecret);const settings=await api('/api/settings',{method:'PUT',body:JSON.stringify(body)});loadSource(settings);notice('Settings saved.');});};
$('rename-cancel').onclick=()=>$('rename-dialog').close();
$('rename-form').onsubmit=e=>{e.preventDefault();if(!renameId)return;const id=renameId;const title=$('rename-title').value.trim();if(!title){notice('Enter a recording title.');return;}$('rename-dialog').close();act(()=>api(`/api/recordings/${encodeURIComponent(id)}`,{method:'PATCH',body:JSON.stringify({title})}));};
$('rename-dialog').addEventListener('close',()=>{renameId=null;});
$('delete-dialog').addEventListener('close',()=>{if($('delete-dialog').returnValue==='delete'&&deleteId){const id=deleteId;act(()=>api(`/api/recordings/${encodeURIComponent(id)}`,{method:'DELETE'}));}deleteId=null;});
(async()=>{const access=await accessReady;if(access.enabled&&!access.role)return;try{if(access.role==='admin'){const settings=await api('/api/settings');$('cap').value=settings.storage_cap_bytes/1024**3;loadSource(settings);loadViewerSettings(await api('/api/v1/viewers/config'));}}catch(e){notice(e.message);}await refresh();refreshViewerLive();setInterval(refresh,2000);setInterval(refreshViewerLive,30000);})();

function loadViewerSettings(settings){$('viewer-player-url').value=settings.player_url||'';$('viewer-origin').value=settings.allowed_origin||'';$('viewer-public-url').value=settings.public_tracking_url||'';$('viewer-timezone').value=settings.timezone_configured?settings.timezone:(Intl.DateTimeFormat().resolvedOptions().timeZone||settings.timezone||'UTC');$('viewer-enabled').checked=!!settings.enabled;$('viewer-health').textContent=`Collector health: ${settings.collector_health||'unknown'}`;$('viewer-last-report').textContent=`Last playback report: ${settings.last_report_at?date(settings.last_report_at):'none received'}`;$('viewer-snippet').value=settings.snippet||'';}
function loadSource(settings){$('source').value='';$('source').placeholder=settings.source_configured?'Saved URL hidden; enter to replace':'https://example.org/live.m3u8';$('clear-source').checked=false;for(const key of ['source_basic_password','source_bearer_token','source_headers']){$(key).value='';$(key).placeholder=settings[key+'_configured']?'Saved secret; leave blank to retain':'';}$('source_basic_username').value=settings.source_basic_username||'';document.querySelectorAll('[data-clear-secret]').forEach(e=>e.checked=false);preview(settings.preview_url);$('source-label').textContent=settings.source_configured?'Configured HLS stream':'Not configured';}

function navigate(){if(accessState.enabled&&!accessState.role)return;const settings=location.hash==='#settings',analytics=location.hash==='#analytics';$('settings-screen').hidden=!settings;$('analytics-screen').hidden=!analytics;$('recorder-screen').hidden=settings||analytics;document.querySelectorAll('nav a').forEach(a=>a.setAttribute('aria-current',a.hash===(settings?'#settings':analytics?'#analytics':'#recorder')?'page':'false'));if(settings)$('settings-title').focus();if(analytics){$('analytics-title').focus();window.loadViewerHistory?.();}}
window.addEventListener('hashchange',navigate);navigate();

// Theme is a browser preference; prior browser-local accent selections never
// write shared branding. Retain valid theme choices through the migration.
const appearanceKey = 'stream-recorder.appearance';
const systemTheme = window.matchMedia('(prefers-color-scheme: dark)');
let appearance = {theme:'system'};
try {
 const saved = JSON.parse(localStorage.getItem(appearanceKey));
 if (saved && ['dark','light','system'].includes(saved.theme)) appearance = {theme:saved.theme};
} catch (_) { /* Unavailable storage or stale preferences keep safe defaults. */ }
function applyAppearance() {
 document.documentElement.dataset.theme = appearance.theme === 'system' ? (systemTheme.matches ? 'dark' : 'light') : appearance.theme;
 $('theme').value = appearance.theme;
 paintBrandingAssets();
}
function saveAppearance() {
 appearance = {theme:$('theme').value};
 try { localStorage.setItem(appearanceKey, JSON.stringify(appearance)); } catch (_) { /* Still usable for this page session. */ }
 applyAppearance();
}
$('theme').addEventListener('change',saveAppearance);
systemTheme.addEventListener('change',() => {if(appearance.theme === 'system')applyAppearance();});

const brandingFields = ['accent','background','surface','text'];
let savedBranding, draftBranding;
const brandingControls = new Map();
const assetDraft = new Map();
const assetControls = new Map();
const assetNames = {light_logo:'Light logo', dark_logo:'Dark logo', favicon:'Favicon', sso_icon:'SSO button icon'};
function paintBrandingAssets() {
 const theme = document.documentElement.dataset.theme || 'dark';
 const logoSlot=`${theme}_logo`, logoUrl = assetDraft.has(logoSlot) ? assetDraft.get(logoSlot).url : draftBranding?.assets?.[logoSlot];
 document.querySelectorAll('.brand-image,.login-brand-image').forEach(image => {image.hidden=!logoUrl;if(logoUrl)image.src=logoUrl;else image.removeAttribute('src');});
 document.querySelectorAll('.brand-mark').forEach(mark => mark.hidden=!!logoUrl);
 const url = assetDraft.has('favicon') ? assetDraft.get('favicon').url : draftBranding?.assets?.favicon;
 $('brand-favicon').href=url || 'data:,';
 const icon=assetDraft.has('sso_icon') ? assetDraft.get('sso_icon').url : draftBranding?.assets?.sso_icon;
 document.querySelectorAll('.sso-icon').forEach(image=>{image.hidden=!icon;if(icon)image.src=icon;else image.removeAttribute('src');});
 for(const [slot,control] of assetControls) {
  const pending=assetDraft.get(slot), current=assetDraft.has(slot)?pending.url:draftBranding?.assets?.[slot];
  control.preview.hidden=!current;
  if(current)control.preview.src=current;else control.preview.removeAttribute('src');
  control.clear.disabled=!current;
  control.state.textContent=pending?.file?'Unsaved replacement':pending?.url===null?'Will clear on save':current?'Configured':'No image';
 }
}
window.paintBrandingAssets=paintBrandingAssets;
function resetAssetDraft() {
 for(const item of assetDraft.values())if(item?.url)URL.revokeObjectURL(item.url);
 assetDraft.clear();
 for(const control of assetControls.values())control.input.value='';
}
for(const [slot,name] of Object.entries(assetNames)) {
 const row=element('div',undefined,'brand-asset');
 const label=element('label',name);label.htmlFor=`asset-${slot}`;
 const input=element('input');input.id=`asset-${slot}`;input.type='file';input.accept='image/png,image/jpeg,image/webp';
 const preview=element('img');preview.alt=`${name} preview`;preview.hidden=true;
 const state=element('span','No image','muted');
 const clear=element('button',`Clear ${name.toLowerCase()}`);clear.type='button';clear.disabled=true;
 input.addEventListener('change',()=>{
  if(!input.files[0])return;
  const old=assetDraft.get(slot);if(old?.url)URL.revokeObjectURL(old.url);
  assetDraft.set(slot,{file:input.files[0],url:URL.createObjectURL(input.files[0])});paintBrandingAssets();
 });
 clear.addEventListener('click',()=>{
  const old=assetDraft.get(slot);if(old?.url)URL.revokeObjectURL(old.url);
  assetDraft.set(slot,{url:null});input.value='';paintBrandingAssets();
 });
 row.append(label,input,preview,state,clear);$('asset-controls').append(row);
 assetControls.set(slot,{input,preview,state,clear});
}
applyAppearance();
function normalizeColor(value) {
 if(/^#[0-9a-f]{3}$/i.test(value))return '#'+[...value.slice(1)].map(c=>c+c).join('').toLowerCase();
 if(/^#[0-9a-f]{6}$/i.test(value))return value.toLowerCase();
 const match=/^rgb\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*\)$/i.exec(value);
 if(match && match.slice(1).every(n=>Number(n)<=255))return '#'+match.slice(1).map(n=>Number(n).toString(16).padStart(2,'0')).join('');
 return null;
}
function rgb(hex) {return [1,3,5].map(i=>parseInt(hex.slice(i,i+2),16));}
function luminance(hex) {const v=rgb(hex).map(n=>{const c=n/255;return c<=.04045?c/12.92:((c+.055)/1.055)**2.4;});return .2126*v[0]+.7152*v[1]+.0722*v[2];}
function contrast(a,b) {const x=luminance(a),y=luminance(b);return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);}
function paintSSOButton() {
 const label=draftBranding?.sso_button?.label || 'Sign in with SSO';
 document.querySelectorAll('.sso-label').forEach(el=>el.textContent=label);
}
window.paintSSOButton=paintSSOButton;
function paintBranding(branding) {
 document.title=branding.name;
 document.querySelectorAll('[data-app-name]').forEach(el=>el.textContent=branding.name);
 document.querySelectorAll('.login-brand-name').forEach(el=>el.textContent=branding.name);
 paintBrandingAssets();
 paintSSOButton();
 for(const theme of ['light','dark']) {
  for(const field of brandingFields)document.documentElement.style.setProperty(`--brand-${theme}-${field}`,branding[theme][field]);
  const accent=branding[theme].accent;
  document.documentElement.style.setProperty(`--brand-${theme}-on-accent`,contrast(accent,'#111111')>=contrast(accent,'#ffffff')?'#111111':'#ffffff');
  for(const field of ['background','text'])document.documentElement.style.setProperty(`--sso-${theme}-${field}`,branding.sso_button[theme][field]);
 }
 const warnings=[];
 for(const theme of ['light','dark']) {
  for(const [foreground,background] of [['text','background'],['text','surface'],['accent','background']])
   if(contrast(branding[theme][foreground],branding[theme][background])<4.5)warnings.push(`${theme} ${foreground} on ${background} has low contrast`);
 }
 for(const theme of ['light','dark'])if(contrast(branding.sso_button[theme].text,branding.sso_button[theme].background)<4.5)warnings.push(`${theme} SSO button text on background has low contrast`);
 $('contrast-warnings').textContent=warnings.length?'Contrast warning: '+warnings.join('; ')+'.':'Text and accent contrast meet 4.5:1 on both themes.';
}
function showBranding(branding) {
 resetAssetDraft();
 draftBranding=structuredClone(branding);
 $('branding-name').value=branding.name;
 $('sso-button-label').value=branding.sso_button.label;
 for(const [key,control] of brandingControls) {
  const parts=key.split('-'),sso=parts.length===3,theme=parts[sso?1:0],field=parts[sso?2:1];
  const value=sso?branding.sso_button[theme][field]:branding[theme][field];
  control.hex.value=value;control.picker.value=value;control.rgb.value=`rgb(${rgb(value).join(', ')})`;
 }
 paintBranding(draftBranding);
}
function brandingControl(theme,field,sso=false) {
 const group=element('div',undefined,'brand-color');
 const label=element('strong',`${sso?'SSO ':''}${theme[0].toUpperCase()+theme.slice(1)} ${field}`);
 const hex=element('input'),picker=element('input'),rgbInput=element('input');
 hex.id=`brand-${sso?'sso-':''}${theme}-${field}-hex`;hex.setAttribute('aria-label',`${sso?'SSO ':''}${theme[0].toUpperCase()+theme.slice(1)} ${field} hex`);
 picker.id=`brand-${sso?'sso-':''}${theme}-${field}-picker`;picker.type='color';picker.setAttribute('aria-label',`${sso?'SSO ':''}${theme} ${field} color picker`);
 rgbInput.id=`brand-${sso?'sso-':''}${theme}-${field}-rgb`;rgbInput.setAttribute('aria-label',`${sso?'SSO ':''}${theme} ${field} RGB`);
 hex.placeholder='#RRGGBB';rgbInput.placeholder='rgb(0, 0, 0)';
 const controls={hex,picker,rgb:rgbInput};brandingControls.set(`${sso?'sso-':''}${theme}-${field}`,controls);
 for(const [format,input] of Object.entries(controls))input.addEventListener('input',()=>{
  const normalized=normalizeColor(input.value);
  input.setCustomValidity(normalized?'':'Enter #RGB, #RRGGBB, or rgb(0–255, 0–255, 0–255)');
  if(!normalized)return;
  (sso?draftBranding.sso_button[theme]:draftBranding[theme])[field]=normalized;
  for(const [other,el] of Object.entries(controls))if(other!==format){el.value=other==='rgb'?`rgb(${rgb(normalized).join(', ')})`:normalized;el.setCustomValidity('');}
  paintBranding(draftBranding);
 });
 group.append(label,hex,picker,rgbInput);
 return group;
}
for(const theme of ['light','dark'])for(const field of brandingFields)$('branding-colors').append(brandingControl(theme,field));
const ssoOptions=element('div');ssoOptions.className='sso-options';
const ssoHeading=element('h3','SSO sign-in button'),ssoHelp=element('p','The SSO icon and label appear on the sign-in screen when SSO is enabled. Local administrator sign-in stays available.','muted');
const ssoLabel=element('label','Button text');ssoLabel.htmlFor='sso-button-label';
const ssoInput=element('input');ssoInput.id='sso-button-label';ssoInput.maxLength=100;ssoInput.required=true;
const ssoColors=element('div');ssoColors.className='branding-colors';
for(const theme of ['light','dark'])for(const field of ['background','text'])ssoColors.append(brandingControl(theme,field,true));
const ssoPreview=element('button');ssoPreview.type='button';ssoPreview.className='sso-button';ssoPreview.setAttribute('aria-label','SSO button preview');
const previewIcon=element('img');previewIcon.className='sso-icon';previewIcon.alt='';previewIcon.hidden=true;
const previewLabel=element('span','Sign in with SSO');previewLabel.className='sso-label';ssoPreview.append(previewIcon,previewLabel);
ssoOptions.append(ssoHeading,ssoHelp,ssoLabel,ssoInput,ssoColors,ssoPreview);
$('branding-form').insertBefore(ssoOptions,$('branding-assets'));
ssoInput.addEventListener('input',()=>{if(!draftBranding)return;draftBranding.sso_button.label=ssoInput.value;paintBranding(draftBranding);});
$('branding-name').addEventListener('input',()=>{if(!draftBranding)return;draftBranding.name=$('branding-name').value;paintBranding(draftBranding);});
$('branding-form').addEventListener('submit',async event=>{
 event.preventDefault();
 try {
  await api('/api/v1/branding',{method:'PUT',body:JSON.stringify({name:draftBranding.name,light:draftBranding.light,dark:draftBranding.dark,sso_button:draftBranding.sso_button})});
  for(const [slot,pending] of assetDraft) {
   const path=`/api/v1/branding/assets/${slot}`;
   const response=await recorderFetch(path,pending.file?{method:'PUT',headers:{'Content-Type':pending.file.type},body:pending.file}:{method:'DELETE'});
   if(!response.ok)throw Error(await response.text());
  }
  savedBranding=await api('/api/v1/branding');showBranding(savedBranding);notice('Branding saved.');
 }catch(error){notice(error.message);}
});
$('branding-reset').onclick=async()=>{
 try {const result=await api('/api/v1/branding/reset',{method:'POST',body:'{}'});savedBranding=result;showBranding(result);notice('Branding reset.');}
 catch(error){notice(error.message);}
};
$('branding-cancel').onclick=()=>{if(savedBranding)showBranding(savedBranding);};
(async()=>{
 try {
  const response=await api('/api/v1/branding');
  savedBranding=response;showBranding(response);
  const access=await accessReady;
  if(access.role!=='admin')$('branding-form').hidden=true;
 }catch(error){notice(error.message);}
})();
