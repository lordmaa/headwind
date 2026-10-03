const { chromium } = require('/home/rob/JukeOS/node_modules/playwright');
const fs = require('fs'); const D = __dirname + '/';
const auth = JSON.parse(fs.readFileSync(D + 'ha_auth.json'));
const views = process.argv.slice(2);
(async () => {
  const b = await chromium.launch();
  const ctx = await b.newContext({ viewport: { width: 412, height: 915 }, deviceScaleFactor: 1.5, isMobile: true, hasTouch: true });
  await ctx.addInitScript(([url, tok]) => { localStorage.setItem('hassTokens', JSON.stringify({ access_token: tok, token_type: 'Bearer', expires_in: 1800, hassUrl: url, clientId: url + '/', expires: Date.now() + 1e10, refresh_token: 'x' })); }, [auth.url, auth.token]);
  const page = await ctx.newPage();
  page.on('pageerror', e => console.log('PAGEERR', e.message.slice(0, 120)));
  for (const v of views) {
    await page.goto(`${auth.url}/home-beta/${v}`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(16000);
    await page.screenshot({ path: D + `shots/beta_${v}.png`, fullPage: true });
  }
  await b.close();
})();
