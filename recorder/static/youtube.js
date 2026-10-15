'use strict';
(async function youtubeSettings() {
 const access = await accessReady;
 if(access.role !== 'admin') return;
 const section = document.getElementById('youtube-settings');
 section.hidden = false;
 const byId = id => document.getElementById(id);
 const error = byId('youtube-error');
 function show(data) {
  byId('youtube-client-id').value = data.client_id || '';
  byId('youtube-client-secret').value = '';
  byId('youtube-client-secret').placeholder = data.client_secret_saved ? 'Saved secret; leave blank to retain' : 'Enter client secret';
  byId('youtube-public-url').value = data.public_url || '';
  byId('youtube-callback').textContent = data.callback_url || 'Save the public app origin to see the redirect URI';
  byId('youtube-identity').textContent = data.channel ? `Connected channel: ${data.channel.name} (${data.channel.id})${data.reconnect_required ? ' — reconnect required' : ''}` : 'No YouTube channel connected.';
  error.textContent = data.error || '';
  byId('youtube-disconnect').hidden = !data.channel;
  byId('youtube-connect').textContent = data.channel ? 'Reconnect channel' : 'Connect channel';
  byId('youtube-connect').disabled = !data.client_id || !data.client_secret_saved || !data.public_url;
 }
 async function load() {show(await api('/api/v1/youtube/status'));}
 try {await load();} catch(e) {error.textContent = e.message;}
 byId('youtube-config-form').onsubmit = async event => {
  event.preventDefault();
  try {
   const body = {client_id:byId('youtube-client-id').value.trim(), public_url:byId('youtube-public-url').value.trim()};
   if(byId('youtube-client-secret').value) body.client_secret = byId('youtube-client-secret').value;
   show(await api('/api/v1/youtube/config', {method:'PUT',body:JSON.stringify(body)}));
  } catch(e) {error.textContent = e.message;}
 };
 byId('youtube-connect').onclick = async () => {
  try {const result = await api('/api/v1/youtube/connect',{method:'POST',body:'{}'});window.location.assign(result.authorization_url);}
  catch(e) {error.textContent = e.message;}
 };
 function showAutomatic(data) {
  byId('auto-enabled').checked = data.enabled;
  byId('auto-source-name').value = data.source_name;
  byId('auto-title').value = data.title_template;
  byId('auto-description').value = data.description_template;
  byId('auto-privacy').value = data.privacy_status;
  byId('auto-tags').value = data.tags.join(', ');
  byId('auto-category').value = data.category_id;
  byId('auto-audience').value = data.made_for_kids === null ? '' : String(data.made_for_kids);
  byId('auto-notify').checked = data.notify_subscribers;
 }
 try {showAutomatic(await api('/api/v1/youtube/automation'));} catch(e) {error.textContent = e.message;}
 byId('youtube-automation-form').onsubmit = async event => {
  event.preventDefault();
  const audience = byId('auto-audience').value;
  const body = {enabled:byId('auto-enabled').checked, source_name:byId('auto-source-name').value.trim(),
   title_template:byId('auto-title').value, description_template:byId('auto-description').value,
   privacy_status:byId('auto-privacy').value, tags:byId('auto-tags').value.split(',').map(x=>x.trim()).filter(Boolean),
   category_id:byId('auto-category').value.trim(), made_for_kids:audience===''?null:audience==='true',
   notify_subscribers:byId('auto-notify').checked};
  try {showAutomatic(await api('/api/v1/youtube/automation',{method:'PUT',body:JSON.stringify(body)}));error.textContent='Automatic upload settings saved.';}
  catch(e) {error.textContent=e.message;}
 };
 byId('youtube-disconnect').onclick = async () => {
  if(!window.confirm('Disconnect the shared YouTube channel? Pending uploads will pause.')) return;
  try {show(await api('/api/v1/youtube/connection',{method:'DELETE'}));}
  catch(e) {error.textContent = e.message;}
 };
})();
