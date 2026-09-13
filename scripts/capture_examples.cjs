// Development-only: npm install --no-save playwright, or provide it through NODE_PATH.
// Screenshots are of the real generated HTML, with no network resources.
const { chromium } = require('playwright');
const path = require('node:path');
const fs = require('node:fs');
const { pathToFileURL } = require('node:url');
(async () => {
  const browser = await chromium.launch(process.env.BROWSER_CHANNEL
    ? {channel: process.env.BROWSER_CHANNEL} : {});
  const root = path.resolve(__dirname, '..', 'docs', 'examples');
  const results = [];
  for (const locale of ['en', 'zh-Hant', 'zh-Hans', 'ja']) {
    for (const width of [390, 900]) {
      for (const theme of ['light', 'dark']) {
        const page = await browser.newPage({viewport:{width, height:1100}, colorScheme:theme});
        const errors = [];
        page.on('pageerror', e => errors.push(String(e)));
        await page.goto(pathToFileURL(path.join(root, 'turn-' + locale + '.html')).href);
        await page.locator('details.report-context').evaluate(el => el.open = true);
        const overflow = await page.evaluate(() =>
          document.documentElement.scrollWidth > window.innerWidth);
        if (overflow || errors.length) throw new Error(JSON.stringify({locale,width,theme,errors,overflow}));
        const totals = await page.locator('[data-metric]').allTextContents();
        if (!totals.join(' ').includes('126,800')) throw new Error('Missing Task total: ' + locale);
        // Every disclosure opens independently and remains accessible by keyboard.
        for (const summary of await page.locator('summary').all()) {
          await summary.focus();
          const before = await summary.evaluate(el => el.parentElement.open);
          await page.keyboard.press('Enter');
          const after = await summary.evaluate(el => el.parentElement.open);
          if (before === after) throw new Error('Disclosure did not toggle');
          await page.keyboard.press('Enter');
        }
        if (width === 900 && theme === 'light') {
          await page.locator('summary').last().evaluate(el => el.blur());
          await page.locator('main').screenshot({path:path.join(root,'turn-' + locale + '.png')});
        }
        results.push({locale,width,theme,overflow,errors:errors.length});
        await page.close();
      }
    }
  }
  const page = await browser.newPage({viewport:{width:1000,height:800}});
  await page.goto(pathToFileURL(path.join(root,'selected-task.html')).href);
  await page.locator('main').screenshot({path:path.join(root,'selected-task.png')});
  await browser.close();
  if (process.env.EXAMPLE_QA_OUTPUT) fs.writeFileSync(process.env.EXAMPLE_QA_OUTPUT,
    JSON.stringify(results, null, 2));
  console.log('Verified ' + results.length + ' locale/theme/width cases and captured 5 screenshots.');
})().catch(err => {console.error(err);process.exitCode=1;});
