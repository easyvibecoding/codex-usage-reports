// Development regression for the report fragment, without the documentation frame.
// Requires Playwright. BROWSER_CHANNEL=chrome uses an installed Chromium browser.
const {chromium, webkit} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const locales = process.env.CARD_HTML ? ['en'] : ['en','zh-Hant','zh-Hans','ja'];
const engines = process.env.CARD_BROWSER ? [process.env.CARD_BROWSER] : ['chromium','webkit'];
const out = process.env.CARD_QA_OUTPUT || path.join(root,'work','mobile-card');
const results=[];
function fragment(locale) {
  const file=process.env.CARD_HTML || path.join(root,'docs/examples','turn-'+locale+'.html');
  const html=fs.readFileSync(file,'utf8');
  const match=html.match(/<section id="(?:usage-reports|run-budget)-[^"]+"[\s\S]*<\/section>/);
  if(!match) throw Error('No card fragment in '+file);
  return match[0];
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});
  for(const engine of engines) {
    const browser=await ({chromium,webkit}[engine]).launch(
      engine==='chromium'&&process.env.BROWSER_CHANNEL ? {channel:process.env.BROWSER_CHANNEL}:{});
    try {
      for(const locale of locales) for(const width of [390,900])
      for(const theme of ['light','dark']) for(const host of ['inverted','missing','normal']) {
        const page=await browser.newPage({viewport:{width,height:1000},colorScheme:theme});
        const errors=[];page.on('pageerror',e=>errors.push(String(e)));
        const bg=theme==='light'?'#fff':'#000', fg=theme==='light'?'#111':'#eee';
        const tokens=host==='missing'?'':`--foreground:${host==='inverted'?bg:fg};
          --muted-foreground:${theme==='light'?'#666':'#aaa'};--border:#888;`;
        await page.setContent(`<!doctype html><html lang="${locale}" style="color-scheme:${theme}">
          <meta name="viewport" content="width=device-width,initial-scale=1">
          <style>:root{${tokens}}body{margin:0;padding:16px;background:${bg};color:${fg};
          font:16px/1.5 system-ui}h2,.viz-stat-value{color:var(--foreground)}</style>
          <body>${fragment(locale)}</body></html>`);
        const check=async()=>page.evaluate(()=>{
          const root=document.querySelector('section[id]');
          function rgb(value) {
            const numbers=value.match(/[\d.]+/g).map(Number);
            return numbers.slice(0,3);
          }
          function lum(values) {
            const v=values.map(x=>x/255).map(x=>x<=.04045?x/12.92:((x+.055)/1.055)**2.4);
            return v[0]*.2126+v[1]*.7152+v[2]*.0722;
          }
          let background=[255,255,255];
          const ancestors=[];
          for(let node=root;node;node=node.parentElement) ancestors.unshift(node);
          for(const node of ancestors) {
            const paint=getComputedStyle(node).backgroundColor;
            const values=paint.match(/[\d.]+/g).map(Number);
            const alpha=values.length===4?values[3]:1;
            background=values.slice(0,3).map((x,i)=>x*alpha+background[i]*(1-alpha));
          }
          const samples=[...root.querySelectorAll('h2,p,span,dt,dd,summary,th,td,li')].filter(el=>
            el.getClientRects().length && el.textContent.trim() &&
            !el.closest('details:not([open]) :not(summary)'));
          const failures=[];
          let minimum=Infinity;
          for(const el of samples) {
            const style=getComputedStyle(el);
            const paint=style.webkitTextFillColor || style.color;
            const color=rgb(paint);
            let opacity=1, node=el;
            while(node && node!==root.parentElement) {
              opacity*=Number(getComputedStyle(node).opacity);node=node.parentElement;
            }
            const effective=color.map((x,i)=>x*opacity+background[i]*(1-opacity));
            const a=lum(effective),b=lum(background);
            const contrast=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
            minimum=Math.min(minimum,contrast);
            if(contrast<4.5 || style.visibility!=='visible') failures.push({tag:el.tagName,text:el.textContent.slice(0,40),contrast});
          }
          return {minimum,failures,overflow:document.documentElement.scrollWidth>innerWidth,
            samples:samples.length,background:getComputedStyle(root).backgroundColor,
            foreground:getComputedStyle(root).color};
        });
        let state=await check();
        if(state.failures.length||state.overflow||errors.length) {
          await page.screenshot({path:path.join(out,'failure-'+engine+'-'+host+'-'+theme+'.png'),fullPage:true});
          throw Error(JSON.stringify({engine,locale,width,theme,host,...state,errors}));
        }
        await page.locator('details').evaluateAll(nodes=>nodes.forEach(n=>n.open=true));
        state=await check();
        if(state.failures.length||state.overflow||errors.length)
          throw Error(JSON.stringify({engine,locale,width,theme,host,expanded:true,...state,errors}));
        // Exercise native keyboard disclosure semantics, without any host JavaScript.
        const summary=page.locator('summary').first();await summary.focus();
        await page.keyboard.press('Enter');
        if(await summary.evaluate(el=>el.parentElement.open)) throw Error('Disclosure did not close');
        await page.keyboard.press('Enter');
        await summary.evaluate(el=>el.blur());
        if(locale==='zh-Hant'&&width===390&&host==='inverted')
          await page.locator('section[id]').screenshot({path:path.join(out,engine+'-'+theme+'.png')});
        results.push({engine,locale,width,theme,host,...state});await page.close();
      }
    } finally {await browser.close();}
  }
  fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));
  console.log('Passed '+results.length+' fragment contrast/layout/disclosure cases.');
})().catch(error=>{console.error(error);process.exitCode=1;});
