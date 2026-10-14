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
 byId('youtube-disconnect').onclick = async () => {
  if(!window.confirm('Disconnect the shared YouTube channel? Pending uploads will pause.')) return;
  try {show(await api('/api/v1/youtube/connection',{method:'DELETE'}));}
  catch(e) {error.textContent = e.message;}
 };
})();
