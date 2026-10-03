const { chromium } = require('/home/rob/JukeOS/node_modules/playwright');
const fs = require('fs');
const D = __dirname + '/';
const auth = JSON.parse(fs.readFileSync(D + 'ha_auth.json'));
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 1.5, isMobile: true, hasTouch: true });
  await ctx.addInitScript(([url, tok]) => { localStorage.setItem('hassTokens', JSON.stringify({ access_token: tok, token_type: 'Bearer', expires_in: 1800, hassUrl: url, clientId: url + '/', expires: Date.now() + 1e10, refresh_token: 'x' })); }, [auth.url, auth.token]);
  const page = await ctx.newPage();
  for (const [name, path] of [['old_home_phone', 'dashboard-test/phone'], ['old_overview', 'lovelace/0']]) {
    await page.goto(`${auth.url}/${path}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(9000);
    await page.screenshot({ path: D + `shots/${name}.png`, fullPage: false });
  }
  await browser.close();
})();
