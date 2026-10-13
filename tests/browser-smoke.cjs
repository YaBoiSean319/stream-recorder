/* Public browser + HTTP acceptance, local source supplied by the Python harness. */
const assert = require('node:assert/strict');
require('./preview-selection.cjs');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch({headless:true, ...(process.env.CHROMIUM_PATH ? {executablePath:process.env.CHROMIUM_PATH} : {}), args:['--no-sandbox']});
  const page = await browser.newPage();
  const base = process.env.RECORDER_URL;
  try {
    await page.goto(base);
    await page.locator('#connection').filter({hasText:'Connected'}).waitFor();
    assert.equal(await page.locator('#start').isEnabled(), true);
    await page.locator('#preview').evaluate(video => video.play());
    await page.waitForFunction(() => document.querySelector('#preview').currentTime > 0, null, {timeout:15000});
    await page.locator('#duration').fill('1');
    await page.locator('#start').click();
    await page.locator('#state').filter({hasText:'Recording'}).waitFor();
    let response = await page.request.get(base+'/api/status');
    const id = (await response.json()).active.id;
    await page.close();
    await new Promise(resolve => setTimeout(resolve,3500));
    const reopened = await browser.newPage();
    await reopened.goto(base);
    await reopened.locator('#stop').waitFor({state:'visible'});
    response = await reopened.request.get(base+'/api/status');
    assert.equal((await response.json()).active.id,id, 'Browser closure must preserve capture');
    await reopened.locator('#stop').click();
    await reopened.locator('a:has-text("Download")').waitFor({timeout:30000});
    await reopened.getByRole('button',{name:/^Pin /}).click();
    await reopened.getByRole('button',{name:/^Unpin /}).waitFor();
    await reopened.getByRole('button',{name:/^Delete /}).click();
    await reopened.locator('#delete-dialog').waitFor({state:'visible'});
    await reopened.locator('#delete-dialog button[value="cancel"]').click();
    assert.equal(await reopened.locator('.record-row').count(),1, 'Cancel must preserve recording');
    await reopened.getByRole('button',{name:/^Delete /}).click();
    await reopened.locator('#delete-dialog button[value="delete"]').click();
    await reopened.locator('.empty').waitFor();
    console.log('Browser preview, capture independence, stop, download, pin and deletion confirmation passed');
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exit(1); });
