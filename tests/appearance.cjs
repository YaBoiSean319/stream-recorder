/* Appearance behavior through the public browser UI. */
const assert = require('node:assert/strict');
module.exports = async function appearance(browser, base) {
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(base+'/#settings');
    assert.equal(await page.getByLabel('Theme').inputValue(), 'system');
    await page.getByLabel('Theme').selectOption('light');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    await page.reload();
    assert.equal(await page.getByLabel('Theme').inputValue(), 'light');
    for (const theme of ['light','dark']) {
      await page.getByLabel('Theme').selectOption(theme);
      const contrasts = await page.evaluate(() => {
        function luminance(color) {
          const parts = color.match(/[\d.]+/g).slice(0,3).map(Number).map(v => {
            v /= 255; return v <= .04045 ? v/12.92 : ((v+.055)/1.055)**2.4;
          });
          return .2126*parts[0]+.7152*parts[1]+.0722*parts[2];
        }
        return [['body','body'], ['.muted','.card'], ['nav a','body'], ['.primary','.primary'], ['.danger','.danger']].map(([fg,bg]) => {
          const text = luminance(getComputedStyle(document.querySelector(fg)).color);
          const background = luminance(getComputedStyle(document.querySelector(bg)).backgroundColor);
          return (Math.max(text,background)+.05)/(Math.min(text,background)+.05);
        });
      });
      for (const ratio of contrasts) assert.ok(ratio >= 4.5, `${theme} text contrast ${ratio}`);
    }
    // A second device/context must not inherit the first browser's preference.
    const second = await browser.newContext();
    try {
      const other = await second.newPage();
      await other.goto(base+'/#settings');
      assert.equal(await other.getByLabel('Theme').inputValue(), 'system');
    } finally { await second.close(); }
    await page.getByLabel('Theme').selectOption('system');
    await page.emulateMedia({colorScheme:'dark'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.emulateMedia({colorScheme:'light'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.getByLabel('Theme').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.getByLabel('Application name').evaluate(el => el === document.activeElement), true);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.getByRole('link',{name:'Recorder',exact:true}).click();
    await page.getByRole('link',{name:'Settings',exact:true}).click();
    await page.locator('#settings-screen').waitFor({state:'visible'});
    // Corrupt/stale local values safely restore the established default.
    await page.evaluate(() => localStorage.setItem('stream-recorder.appearance', '{"theme":"obsolete","accent":"obsolete"}'));
    await page.reload();
    assert.equal(await page.getByLabel('Theme').inputValue(), 'system');
    await page.evaluate(() => localStorage.setItem('stream-recorder.appearance', '{"theme":"light","accent":"rose"}'));
    await page.reload();
    assert.equal(await page.getByLabel('Theme').inputValue(), 'light');
    // A prior browser-local accent is deliberately ignored when migrating to shared branding.
    assert.equal(await page.locator('html').getAttribute('data-accent'), null);
    const name = page.getByLabel('Application name');
    await name.fill('Studio One');
    assert.equal(await page.title(), 'Studio One');
    await page.getByRole('button',{name:'Save branding'}).click();
    await page.getByText('Branding saved.').waitFor();
    await page.reload();
    assert.equal(await page.title(), 'Studio One');
    const otherContext = await browser.newContext();
    try {
      const other = await otherContext.newPage();
      await other.goto(base+'/#settings');
      await other.waitForFunction(() => document.title === 'Studio One');
      assert.equal(await other.title(), 'Studio One');
      assert.equal(await other.getByLabel('Theme').inputValue(), 'system');
    } finally { await otherContext.close(); }
    await page.getByLabel('Dark background hex').fill('#252525');
    assert.equal(await page.locator('html').evaluate(el => getComputedStyle(el).getPropertyValue('--brand-dark-background').trim()), '#252525');
    await page.getByLabel('light accent RGB').fill('rgb(10, 20, 30)');
    assert.equal(await page.getByLabel('Light accent hex').inputValue(), '#0a141e');
    await page.getByLabel('dark accent color picker').fill('#778899');
    assert.equal(await page.getByLabel('Dark accent hex').inputValue(), '#778899');
    await page.getByLabel('Light text hex').fill('#ffffff');
    await page.getByText('Contrast warning:').waitFor();
    await page.getByRole('button',{name:'Discard preview'}).click();
    assert.equal(await page.getByLabel('Light text hex').inputValue(), '#171717');
    const red = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGP8z8DAwMDAxMDAwMDAAAANHQEDasKb6QAAAABJRU5ErkJggg==','base64');
    const blue = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAIAAAACCAIAAAD91JpzAAAAFklEQVR4nGNkYPjPwMDAxMDAwMDAAAALHwEDmIWXfgAAAABJRU5ErkJggg==','base64');
    await page.getByLabel('Light logo').setInputFiles({name:'light.png',mimeType:'image/png',buffer:red});
    assert.equal(await page.locator('.brand-image').isVisible(),true);
    await page.getByLabel('Dark logo').setInputFiles({name:'dark.png',mimeType:'image/png',buffer:blue});
    await page.getByLabel('Favicon').setInputFiles({name:'icon.png',mimeType:'image/png',buffer:red});
    await page.getByRole('button',{name:'Save branding'}).click();
    await page.getByText('Branding saved.').waitFor();
    const lightSource=await page.locator('.brand-image').getAttribute('src');
    assert.match(lightSource,/assets\/light_logo\?v=/);
    assert.match(await page.locator('#brand-favicon').getAttribute('href'),/assets\/favicon\?v=/);
    await page.getByLabel('Theme').selectOption('dark');
    assert.match(await page.locator('.brand-image').getAttribute('src'),/assets\/dark_logo\?v=/);
    await page.reload();
    assert.match(await page.locator('.brand-image').getAttribute('src'),/assets\/dark_logo\?v=/);
    await page.getByLabel('Theme').selectOption('light');
    await page.getByRole('button',{name:'Clear light logo'}).click();
    assert.equal(await page.locator('.brand-image').isVisible(),false);
    await page.getByRole('button',{name:'Discard preview'}).click();
    assert.equal(await page.locator('.brand-image').isVisible(),true);
    await page.getByRole('button',{name:'Reset branding'}).click();
    await page.getByText('Branding reset.').waitFor();
    assert.equal(await page.title(), 'Stream Recorder');
    assert.equal(await page.locator('.brand-image').isVisible(),false);
  } finally { await context.close(); }
};
