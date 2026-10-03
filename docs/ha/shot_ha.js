const { chromium } = require('/home/rob/JukeOS/node_modules/playwright');
const fs = require('fs');
const D = __dirname + '/';
const auth = JSON.parse(fs.readFileSync(D + 'ha_auth.json'));
const which = process.argv[2] || 'phone';
(async () => {
  const browser = await chromium.launch();
  const opts = which === 'phone'
    ? { viewport: { width: 412, height: 915 }, deviceScaleFactor: 1.5, isMobile: true, hasTouch: true }
    : { viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 };
  const ctx = await browser.newContext(opts);
  await ctx.addInitScript(([url, tok]) => {
    localStorage.setItem('hassTokens', JSON.stringify({ access_token: tok, token_type: 'Bearer', expires_in: 1800, hassUrl: url, clientId: url + '/', expires: Date.now() + 1e10, refresh_token: 'x' }));
  }, [auth.url, auth.token]);
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 120)));
  for (const v of ['today', 'body', 'riding']) {
    await page.goto(`${auth.url}/rob-health/${v}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(9000);
    await page.screenshot({ path: D + `shots/${which}_${v}.png`, fullPage: true });
    if (which === 'phone') {
      await page.evaluate(() => { const s = document.querySelector('home-assistant')?.shadowRoot; });
    }
  }
  await browser.close();
})();
