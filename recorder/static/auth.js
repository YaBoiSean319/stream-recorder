'use strict';
let accessState = {enabled:false,role:null,csrf_token:null};
const originalFetch = window.fetch.bind(window);
function recorderFetch(input,options={}) {
 const url = new URL(typeof input==='string'?input:input.url,location.href);
 const headers = new Headers(options.headers);
 if(url.origin===location.origin && accessState.csrf_token)headers.set('X-CSRF-Token',accessState.csrf_token);
 return originalFetch(input,{...options,headers});
}
 function node(tag,text){const el=document.createElement(tag);if(text)el.textContent=text;return el;}
 function field(form,id,label,type='text'){
  const l=node('label',label);l.htmlFor=id;
  const input=node('input');input.id=id;input.type=type;
  if(type==='checkbox'){l.prepend(input);form.append(l);}
  else form.append(l,input);
  return input;
 }
 function button(form,text){const el=node('button',text);el.type='submit';form.append(el);return el;}
 async function request(path,body,method='POST'){const r=await recorderFetch(path,{method,headers:{'Content-Type':'application/json'},body:body===undefined?undefined:JSON.stringify(body)});if(!r.ok)throw Error(await r.text());return r.json();}
 function report(error){document.getElementById('notice').hidden=false;document.getElementById('notice').textContent=error.message;}
 function buildSignOut() {
 const logout=node('button','Sign out');logout.type='button';logout.hidden=!accessState.enabled||!accessState.role;logout.onclick=async()=>{try{await request('/api/auth/logout',{});location.reload();}catch(e){report(e);}};
 document.querySelector('header').append(logout);
 }
 function buildSignIn() {
  document.getElementById('recorder-screen').hidden=true;document.getElementById('settings-screen').hidden=true;
  const login=node('section');login.className='card settings';const image=node('img');image.className='login-brand-image';image.alt='';image.hidden=true;login.append(image);const title=node('h1','Sign in to '),identity=node('span',document.querySelector('[data-app-name]')?.textContent||'Stream Recorder');identity.className='login-brand-name';title.append(identity);login.append(title);window.paintBrandingAssets?.();
  const form=node('form'),username=field(form,'login-username','Username'),password=field(form,'login-password','Password','password');password.autocomplete='current-password';button(form,'Sign in');form.onsubmit=async e=>{e.preventDefault();try{await request('/api/auth/login',{username:username.value,password:password.value});location.reload();}catch(e){report(e);}};login.append(form);
  if(accessState.sso_enabled){
   const sso=node('button');sso.type='button';sso.className='sso-button';
   const icon=node('img');icon.className='sso-icon';icon.alt='';icon.hidden=true;
   const label=node('span','Sign in with SSO');label.className='sso-label';
   sso.append(icon,label);sso.onclick=()=>{location.assign('/api/auth/oidc/login');};login.append(sso);
  }
  document.querySelector('main').prepend(login);
  window.paintBrandingAssets?.();window.paintSSOButton?.();
 }
 async function buildLocalAuthentication(section) {
 const config=await request('/api/auth/config',undefined,'GET');
 const form=node('form');const username=field(form,'admin-username','Local administrator username');username.value=config.username||'';const password=field(form,'admin-password','New password (at least 12 characters; leave blank to retain)','password');password.autocomplete='new-password';const enabled=field(form,'auth-enabled','Enable built-in authentication','checkbox');enabled.checked=config.enabled;const publicUrl=field(form,'auth-public-url','HTTPS public origin (for reverse proxy secure cookies)');publicUrl.value=config.public_url||'';
 const requireKey=field(form,'auth-require-api-key','Require API key for machine requests','checkbox');requireKey.checked=!!config.require_api_key;
 form.append(node('p','Signed-in browsers continue using their session and CSRF token. Configure a local recovery administrator before enabling this setting.'));
 button(form,'Save authentication');form.onsubmit=async e=>{e.preventDefault();try{const body={enabled:enabled.checked,require_api_key:requireKey.checked};if(password.value){body.username=username.value;body.password=password.value;}if(publicUrl.value)body.public_url=publicUrl.value;await request('/api/auth/config',body);location.reload();}catch(e){report(e);}};section.append(form);
 }
 async function buildApiKeys(section) {
 section.append(node('h3','API keys'));
 const available=await request('/api/v1/auth/permissions',undefined,'GET');
 const keyForm=node('form'),label=field(keyForm,'key-label','Key label'),role=node('select');role.id='key-role';const roleLabel=node('label','Maximum key role');roleLabel.htmlFor=role.id;for(const value of ['operator','admin']){const option=node('option',value);option.value=value;role.append(option);}keyForm.append(roleLabel,role);
 const expires=field(keyForm,'key-expires','Expires at (optional, local time)','datetime-local');
 const scopeLabel=node('h4','Allowed operations'),scopes=node('fieldset');scopes.className='key-scopes';scopes.setAttribute('aria-label','Allowed operations');keyForm.append(scopeLabel,scopes);
 function showPermissions(){scopes.replaceChildren();for(const name of available[role.value]){const line=node('label'),choice=node('input');choice.type='checkbox';choice.value=name;choice.checked=true;line.append(choice,document.createTextNode(' '+name));scopes.append(line);}}
 role.onchange=showPermissions;showPermissions();
 button(keyForm,'Create API key');const keyResult=node('output');keyResult.id='created-api-key';keyResult.setAttribute('role','status');keyResult.className='key-result';keyForm.onsubmit=async e=>{e.preventDefault();try{const permissions=[...scopes.querySelectorAll('input:checked')].map(input=>input.value);const payload={label:label.value,role:role.value,permissions};if(expires.value){const timestamp=new Date(expires.value).getTime();if(!Number.isFinite(timestamp)||timestamp<=Date.now())throw Error('Choose a future expiry date');payload.expires_at=timestamp/1000;}const result=await request('/api/v1/auth/keys',payload);keyResult.textContent=`Copy this key now; it cannot be shown again: ${result.key}`;label.value='';expires.value='';await loadKeys();}catch(e){report(e);}};section.append(keyForm,keyResult);const keys=node('div');section.append(keys);
 async function loadKeys(){keys.replaceChildren();for(const key of await request('/api/v1/auth/keys',undefined,'GET')){const row=node('div');row.className='key-row';const details=node('span',`${key.label} (${key.role}) · ${key.permissions.join(', ')} · Expires: ${key.expires_at?new Date(key.expires_at*1000).toLocaleString():'Never'} · Last used: ${key.last_used_at?new Date(key.last_used_at*1000).toLocaleString():'Never'}`),revoke=node('button','Revoke');revoke.type='button';revoke.setAttribute('aria-label','Revoke '+key.label);revoke.onclick=async()=>{try{await request('/api/v1/auth/keys/'+encodeURIComponent(key.id),undefined,'DELETE');keyResult.textContent='';await loadKeys();}catch(e){report(e);}};row.append(details,revoke);keys.append(row);}}await loadKeys();
 }
 async function buildOidcSettings(section) {
 section.append(node('h3','OpenID Connect SSO'));
 const oidc=await request('/api/auth/oidc/config',undefined,'GET'),ssoForm=node('form'),fields={};
 for(const [name,label,type] of [['issuer','Provider issuer'],['client_id','Client ID'],['client_secret','Client secret (blank retains saved value)','password'],['public_url','Public application URL'],['group_claim','Group claim'],['operator_groups','Operator groups (comma-separated)'],['admin_groups','Administrator groups (comma-separated)'],['enabled','Enable SSO','checkbox']]){const input=field(ssoForm,'oidc-'+name,label,type||'text');fields[name]=input;if(type==='checkbox')input.checked=!!oidc[name];else input.value=name==='client_secret'?'':Array.isArray(oidc[name])?oidc[name].join(', '):oidc[name]||'';}
 fields.remove_client_secret=field(ssoForm,'oidc-remove_client_secret','Remove saved client secret','checkbox');
 fields.remove_client_secret.disabled=!oidc.client_secret_saved;
 button(ssoForm,'Save SSO');ssoForm.onsubmit=async e=>{e.preventDefault();try{const body={};for(const [name,input] of Object.entries(fields)){body[name]=input.type==='checkbox'?input.checked:name.endsWith('_groups')?input.value.split(',').map(x=>x.trim()).filter(Boolean):input.value;}await request('/api/auth/oidc/config',body);location.reload();}catch(e){report(e);}};section.append(ssoForm);
 }

async function initializeAccess() {
 accessState = await (await originalFetch('/api/auth/status')).json();
 const section=document.getElementById('access-settings');
 section.replaceChildren(node('h2','Authentication and API access'));
 buildSignOut();
 if(accessState.enabled&&!accessState.role){buildSignIn();return accessState;}
 if(accessState.role!=='admin'){
  document.getElementById('settings-form').hidden=true;
  section.append(node('p','Shared settings and access management require administrator permission.'));
  return accessState;
 }
 await buildLocalAuthentication(section);
 await buildApiKeys(section);
 await buildOidcSettings(section);
 return accessState;
}
const accessReady = initializeAccess();
