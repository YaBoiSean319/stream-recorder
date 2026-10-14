/* Appearance behavior through the public browser UI. */
const assert = require('node:assert/strict');
module.exports = async function appearance(browser, base) {
  const context = await browser.newContext();
  const page = await context.newPage();
  try {
    await page.goto(base+'/#settings');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.getByLabel('Theme').selectOption('light');
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'light');
    await page.reload();
    assert.equal(await page.getByLabel('Theme').inputValue(), 'light');
    await page.getByLabel('Accent color').selectOption('rose');
    await page.reload();
    assert.equal(await page.getByLabel('Accent color').inputValue(), 'rose');
    assert.equal(await page.locator('html').getAttribute('data-accent'), 'rose');
    for (const theme of ['light','dark']) for (const accent of ['mint','blue','rose']) {
      await page.getByLabel('Theme').selectOption(theme);
      await page.getByLabel('Accent color').selectOption(accent);
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
      for (const ratio of contrasts) assert.ok(ratio >= 4.5, `${theme}/${accent} text contrast ${ratio}`);
    }
    // A second device/context must not inherit the first browser's preference.
    const second = await browser.newContext();
    try {
      const other = await second.newPage();
      await other.goto(base+'/#settings');
      assert.equal(await other.getByLabel('Theme').inputValue(), 'dark');
      assert.equal(await other.getByLabel('Accent color').inputValue(), 'mint');
    } finally { await second.close(); }
    await page.getByLabel('Theme').selectOption('system');
    await page.emulateMedia({colorScheme:'dark'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await page.emulateMedia({colorScheme:'light'});
    await page.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    await page.getByLabel('Theme').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.getByLabel('Accent color').evaluate(el => el === document.activeElement), true);
    await page.setViewportSize({width:390,height:844});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true);
    await page.getByRole('link',{name:'Recorder',exact:true}).click();
    await page.getByRole('link',{name:'Settings',exact:true}).click();
    await page.locator('#settings-screen').waitFor({state:'visible'});
    // Corrupt/stale local values safely restore the established default.
    await page.evaluate(() => localStorage.setItem('stream-recorder.appearance', '{"theme":"obsolete","accent":"obsolete"}'));
    await page.reload();
    assert.equal(await page.getByLabel('Theme').inputValue(), 'dark');
    assert.equal(await page.getByLabel('Accent color').inputValue(), 'mint');
  } finally { await context.close(); }
};
