/* Local instrumented-player browser checks; runs against the acceptance HLS fixture. */
const assert = require('node:assert/strict');
module.exports = async function(browser, page, base, previewUrl) {
  const context = await browser.newContext();
  const origin = new URL(base).origin;
  const settings = await (await page.request.put(base+'/api/v1/viewers/config', {data:{
    player_url:origin+'/player-fixture', allowed_origin:origin,
    public_tracking_url:origin, timezone:'America/Indiana/Indianapolis', enabled:true
  }})).json();
  assert.ok(settings.tracking_key && settings.snippet.includes('/api/v1/viewers/report'));
  const snippet = settings.snippet.replaceAll('30000', '500');
  async function player() {
    const tab = await context.newPage();
    await tab.goto(base);
    await tab.evaluate(({snippet,previewUrl}) => {
      document.body.innerHTML = `<video id="external-player" muted playsinline controls></video>`;
      const hlsScript = document.createElement('script'); hlsScript.src='/static/hls.min.js';
      hlsScript.onload=()=>{const video=document.querySelector('video'); if(window.Hls?.isSupported()){const hls=new Hls();hls.loadSource(previewUrl);hls.attachMedia(video);}else video.src=previewUrl; (new Function(snippet.replace(/^<script>|<\/script>$/g,'')))();};
      document.head.append(hlsScript);
    }, {snippet,previewUrl});
    await tab.locator('video').waitFor();
    return tab;
  }
  const first=await player(), second=await player();
  await first.locator('video').evaluate(v=>v.play());
  assert.equal(await first.locator('video').evaluate(v=>v.muted),true,'Muted playback is included');
  await second.locator('video').evaluate(v=>v.play());
  await first.waitForFunction(async url=>(await (await fetch(url)).json()).count===1,base+'/api/v1/viewers/live',{timeout:15000});
  await page.evaluate(()=>window.refreshViewerLive());
  await page.waitForFunction(()=>document.querySelector('#viewer-count')?.textContent==='1',null,{timeout:5000});
  await first.waitForFunction(()=>document.querySelector('video').currentTime>1,null,{timeout:15000});
  await first.locator('video').evaluate(v=>v.pause());
  await second.waitForTimeout(800);
  assert.equal((await (await second.request.get(base+'/api/v1/viewers/live')).json()).count,1,'Pausing one tab must retain the browser while another tab plays');
  await second.locator('video').evaluate(v=>{v.dispatchEvent(new Event('stalled'))});
  await first.waitForFunction(async url=>(await (await fetch(url)).json()).count===0,base+'/api/v1/viewers/live',{timeout:5000});
  await first.close(); await second.close();
  const endedTab=await player();
  await endedTab.locator('video').evaluate(v=>v.play());
  await endedTab.waitForFunction(async url=>(await (await fetch(url)).json()).count===1,base+'/api/v1/viewers/live',{timeout:15000});
  await endedTab.locator('video').evaluate(v=>{if(Number.isFinite(v.duration))v.currentTime=Math.max(0,v.duration-.2)});
  await endedTab.locator('video').evaluate(v=>v.play());
  await endedTab.waitForFunction(async url=>(await (await fetch(url)).json()).count===0,base+'/api/v1/viewers/live',{timeout:10000});
  await endedTab.close();
  const rotated=await (await page.request.post(base+'/api/v1/viewers/key/rotate',{data:{}})).json();
  const oldKey=await page.request.post(base+'/api/v1/viewers/report',{headers:{Origin:origin,'X-Tracking-Key':settings.tracking_key},data:{browser_id:'browser-id-123456','tab_id':'tab-id-123456789','playing':true}});
  assert.equal(oldKey.status(),401,'Rotating key must revoke previous player snippets');
  assert.ok(rotated.snippet.includes(rotated.tracking_key));
  await page.request.put(base+'/api/v1/viewers/config',{data:{player_url:'',allowed_origin:'',public_tracking_url:'',enabled:false}});
  await context.close();
};
