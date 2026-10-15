'use strict';
const operations = document.getElementById('operation');
const pathInput = document.getElementById('path');
const bodyInput = document.getElementById('body');
const bodyLabel = document.getElementById('body-label');
const assetInput = document.getElementById('asset-file');
const assetLabel = document.getElementById('asset-label');
const warning = document.getElementById('mutation-warning');
const result = document.getElementById('result');
const responseImage = document.getElementById('response-image');
const responseDownload = document.getElementById('response-download');
let csrf = null;
let imageUrl = null;
let downloadUrl = null;
function selected() { return JSON.parse(operations.value); }
function isAssetUpload(method, template) {
  return method === 'PUT' && template === '/api/v1/branding/assets/{slot}';
}
async function initialize() {
  try {
    const [spec, status] = await Promise.all([
      fetch('/api/v1/openapi.json', {credentials:'same-origin'}).then(r=>r.json()),
      fetch('/api/v1/auth/status', {credentials:'same-origin'}).then(r=>r.json()),
    ]);
    csrf = status.csrf_token;
    for (const [path, verbs] of Object.entries(spec.paths)) {
      for (const [verb, operation] of Object.entries(verbs)) {
        if (verb === 'parameters') continue;
        const option = document.createElement('option');
        option.value = JSON.stringify([verb.toUpperCase(), path]);
        option.textContent = `${verb.toUpperCase()} ${path} — ${operation.summary} (${operation['x-permission'] || 'session / interactive flow'})`;
        operations.append(option);
      }
    }
    operations.addEventListener('change', () => {
      const [method, template] = selected();
      pathInput.value = template;
      const binary = isAssetUpload(method, template);
      const bodyless = method === 'GET' || method === 'DELETE' || binary;
      bodyInput.disabled = bodyless;
      bodyLabel.hidden = binary;
      assetLabel.hidden = !binary;
      assetInput.required = binary;
      warning.hidden = method === 'GET';
      if (method === 'DELETE') {
        warning.textContent = 'DELETE: this can revoke access, disconnect a service, clear an asset, or delete a receiver/recording. Review the selected operation before sending.';
      } else if (method !== 'GET') {
        warning.textContent = `${method}: this request changes settings or starts/stops work. It runs only when you submit this form.`;
      }
    });
    operations.dispatchEvent(new Event('change'));
  } catch (error) { result.textContent = 'Could not load the API contract: '+error.message; }
}
function matchesSelectedPath(template, value) {
  const actual = new URL(value, location.origin);
  if (actual.origin !== location.origin || !actual.pathname.startsWith('/api/v1/')) return false;
  const pattern = '^' + template.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\{[^/]+?\\\}/g, '[^/]+') + '$';
  return new RegExp(pattern).test(actual.pathname);
}
document.getElementById('request-form').addEventListener('submit', async event => {
  event.preventDefault();
  const [method, template] = selected();
  const path = pathInput.value.trim();
  let url;
  try { url = new URL(path, location.origin); }
  catch { result.textContent = 'Enter a valid local API path.'; return; }
  if (!matchesSelectedPath(template, path)) {
    result.textContent = 'The path must match the selected operation and stay on this application.';
    return;
  }
  if (method === 'DELETE' && !window.confirm(`Run DELETE ${url.pathname}? This may remove or disconnect data.`)) return;
  const headers = {'Accept':'application/json'};
  const key = document.getElementById('key').value;
  if (key) headers.Authorization = 'Bearer '+key;
  else if (csrf) headers['X-CSRF-Token'] = csrf;
  const options = {method, credentials:'same-origin', headers};
  const binary = isAssetUpload(method, template);
  if (binary) {
    const file = assetInput.files[0];
    if (!file) { result.textContent = 'Choose a PNG, JPEG, or WebP image file.'; return; }
    headers['Content-Type'] = file.type;
    options.body = file;
  } else if (method !== 'GET' && method !== 'DELETE') {
    try { options.body = JSON.stringify(JSON.parse(bodyInput.value)); }
    catch { result.textContent = 'Request body must be valid JSON.'; return; }
    headers['Content-Type'] = 'application/json';
  }
  result.textContent = 'Sending…';
  responseImage.hidden = true;
  responseDownload.hidden = true;
  if (imageUrl) { URL.revokeObjectURL(imageUrl); imageUrl = null; }
  if (downloadUrl) { URL.revokeObjectURL(downloadUrl); downloadUrl = null; }
  try {
    const response = await fetch(url.pathname + url.search, options);
    const type = response.headers.get('content-type') || '';
    if (type.startsWith('image/') || type === 'video/mp4') {
      const blob = await response.blob();
      if (type === 'video/mp4') {
        downloadUrl = URL.createObjectURL(blob);
        responseDownload.href = downloadUrl;
        responseDownload.download = 'recording.mp4';
        responseDownload.textContent = `${response.status} ${response.statusText} — Download ${type}`;
        responseDownload.hidden = false;
        result.textContent = `${response.status} ${response.statusText}\nBinary response ready to download`;
        return;
      }
      imageUrl = URL.createObjectURL(blob);
      responseImage.src = imageUrl;
      responseImage.hidden = false;
      result.textContent = `${response.status} ${response.statusText}\n${type} image response`;
      return;
    }
    const text = await response.text();
    result.textContent = `${response.status} ${response.statusText}\n${text.slice(0,20000)}`;
  } catch (error) { result.textContent = error.message; }
});
initialize();
