'use strict';
const operations = document.getElementById('operation');
const pathInput = document.getElementById('path');
const bodyInput = document.getElementById('body');
const result = document.getElementById('result');
let csrf = null;
async function initialize() {
  try {
    const [spec, status] = await Promise.all([
      fetch('/api/v1/openapi.json', {credentials:'same-origin'}).then(r=>r.json()),
      fetch('/api/v1/auth/status', {credentials:'same-origin'}).then(r=>r.json()),
    ]);
    csrf = status.csrf_token;
    for (const [path, verbs] of Object.entries(spec.paths)) {
      for (const [verb, operation] of Object.entries(verbs)) {
        const option = document.createElement('option');
        option.value = JSON.stringify([verb.toUpperCase(), path]);
        option.textContent = `${verb.toUpperCase()} ${path} — ${operation.summary} (${operation['x-permission']})`;
        operations.append(option);
      }
    }
    operations.addEventListener('change', () => {
      const [method,path] = JSON.parse(operations.value);
      pathInput.value = path;
      bodyInput.disabled = method === 'GET' || method === 'DELETE';
    });
    operations.dispatchEvent(new Event('change'));
  } catch (error) { result.textContent = 'Could not load the API contract: '+error.message; }
}
document.getElementById('request-form').addEventListener('submit', async event => {
  event.preventDefault();
  const [method] = JSON.parse(operations.value);
  const path = pathInput.value;
  if (!path.startsWith('/api/v1/') || path.startsWith('//')) {
    result.textContent = 'Only local /api/v1/ paths are allowed.';
    return;
  }
  const headers = {'Accept':'application/json'};
  const key = document.getElementById('key').value;
  if (key) headers.Authorization = 'Bearer '+key;
  else if (csrf) headers['X-CSRF-Token'] = csrf;
  const options = {method, credentials:'same-origin', headers};
  if (method !== 'GET' && method !== 'DELETE') {
    try { options.body = JSON.stringify(JSON.parse(bodyInput.value)); }
    catch { result.textContent = 'Request body must be valid JSON.'; return; }
    headers['Content-Type'] = 'application/json';
  }
  result.textContent = 'Sending…';
  try {
    const response = await fetch(path, options);
    const text = await response.text();
    result.textContent = `${response.status} ${response.statusText}\n${text.slice(0,20000)}`;
  } catch (error) { result.textContent = error.message; }
});
initialize();
