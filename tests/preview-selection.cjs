/* Exercise the application's real preview selection without a browser download. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('recorder/static/app.js', 'utf8').split('function status(')[0];
function previewWith(mse, native) {
  const calls = [];
  const video = {canPlayType: () => native};
  const elements = {'preview': video, 'source-label': {}, 'preview-message': {}, 'open-settings': {}};
  class Hls {
    static isSupported() { return mse; }
    static Events = {ERROR: 'error'};
    loadSource(url) { calls.push(['load', url]); }
    attachMedia(media) { assert.equal(media, video); calls.push(['attach']); }
    on() {}
    destroy() { calls.push(['destroy']); }
  }
  const context = vm.createContext({document: {getElementById: id => elements[id]}, window: {Hls}, Hls});
  vm.runInContext(source, context);
  vm.runInContext("preview('https://example.org/live.m3u8')", context);
  return {calls, video, elements, context};
}
const both = previewWith(true, 'maybe');
assert.deepEqual(both.calls, [['load', 'https://example.org/live.m3u8'], ['attach']],
  'Prefer Hls.js when native HLS is advertised but MSE is supported');
assert.equal(both.video.src, undefined);
vm.runInContext("preview('https://example.org/live.m3u8')", both.context);
assert.equal(both.calls.length, 2, 'Polling must not restart preview');
vm.runInContext("preview('https://example.org/other.m3u8')", both.context);
assert.deepEqual(both.calls.slice(2), [['destroy'], ['load', 'https://example.org/other.m3u8'], ['attach']]);
const native = previewWith(false, 'probably');
assert.equal(native.video.src, 'https://example.org/live.m3u8', 'Retain native HLS fallback');
assert.deepEqual(native.calls, []);
const unsupported = previewWith(false, '');
assert.match(unsupported.elements['preview-message'].textContent, /cannot play HLS/);
console.log('Preview selection regression passed');
