// Verify the appended report in the local offline and source entry points.
const { chromium } = require('C:/Users/inari/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { pathToFileURL } = require('node:url');
const root = path.resolve(__dirname, '../..');
const output = path.join(root, '.aster/frontend-eval-round2');
fs.mkdirSync(output, {recursive:true});
(async()=>{
  const browser=await chromium.launch({channel:'msedge',headless:true});
  const checks=[];
  try {
    const page=await browser.newPage({viewport:{width:1440,height:1040}});
    const errors=[];page.on('pageerror',error=>errors.push(error.message));
    for(const entry of ['dashboard.html','index.html']) {
      await page.goto(pathToFileURL(path.join(root,'frontend/eval-round1',entry)).href);
      assert.equal(await page.locator('#case-rows tr').count(),30);
      assert.equal(await page.locator('#round2-rows tr').count(),20);
      assert.deepEqual(await page.evaluate(()=>Object.values(window.EVAL_ROUND2.raw_strict_dimensions)),[13,20,6,20,17]);
      assert.deepEqual(await page.evaluate(()=>Object.values(window.EVAL_ROUND2.display_strict_dimensions)),[18,20,6,20,17]);
      assert.match(await page.locator('#round2-dimensions .kpi-value').first().innerText(), /18\s*\/ 20/);
      assert.equal(await page.evaluate(()=>window.EVAL_ROUND2.display_overall.passed),5);
      assert.equal(await page.evaluate(()=>window.EVAL_ROUND1.displayPassed),15);
      assert.equal(await page.evaluate(()=>new Set([...document.querySelectorAll('[id]')].map(el=>el.id)).size===document.querySelectorAll('[id]').length),true);
      assert.equal(await page.evaluate(()=>document.getElementById('round2').compareDocumentPosition(document.getElementById('sources')) & Node.DOCUMENT_POSITION_PRECEDING),2);
      await page.locator('.section-nav a[href="#round2"]').click();
      await page.locator('#round2-dimensions').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output,entry+'-dimensions.png')});
      assert.equal(await page.locator('#round2-split option[value="retained"]').count(),0);
      for (const [split,count] of [['development',8],['test',7],['regression',5]]) {
        await page.selectOption('#round2-split',split);
        assert.equal(await page.locator('#round2-rows tr').count(),count);
      }
      await page.click('#round2-reset');
      assert.match(await page.locator('#round2-updates').innerText(),/缓存命中率/);
      assert.equal(await page.locator('#round2 > :last-child').getAttribute('id'),'round2-updates');
      assert.equal(await page.locator('#round2 #next-efficiency-policy').count(),1);
      await page.selectOption('#round2-status','review');
      assert.equal(await page.locator('#round2-rows tr').count(),5);
      assert.equal(await page.locator('#round2-rows td:nth-child(3) .fail').count(),0);
      assert.equal(await page.locator('#round2-rows td:nth-child(5) .fail').count(),5);
      assert.equal(await page.locator('#round2-rows td:last-child .fail').count(),5);
      const downloadPromise=page.waitForEvent('download');await page.click('#round2-export');
      const download=await downloadPromise;await download.saveAs(path.join(output,entry+'-review.csv'));
      assert.equal(fs.readFileSync(path.join(output,entry+'-review.csv'),'utf8').trim().split(/\r?\n/).length,6);
      const csvLines=fs.readFileSync(path.join(output,entry+'-review.csv'),'utf8').trim().split(/\r?\n/);
      for(const line of csvLines.slice(1))assert.match(line, /"通过","通过","未通过","通过","通过","未通过"/);
      await page.locator('#round2-rows .case-link').first().click();
      assert.equal(await page.locator('#round2-detail .detail-score').first().innerText(),'结果\n通过');
      assert.match(await page.locator('#round2-detail').innerText(),/纠正后仍未通过 · 1 项/);
      assert.doesNotMatch(await page.locator('#round2-detail').innerText(),/TypeError/);
      await page.getByText('原始未通过检查（仅追溯，不重复扣分）',{exact:true}).click();
      assert.match(await page.locator('#round2-detail').innerText(),/TypeError/);
      await page.keyboard.press('Escape');
      await page.click('#round2-reset');
      await page.selectOption('#round2-status','passed');
      assert.equal(await page.locator('#round2-rows tr').count(),5);
      await page.selectOption('#round2-split','regression');
      assert.equal(await page.locator('#round2-empty').isVisible(),true);
      await page.click('#round2-reset');
      await page.selectOption('#round2-dimension','outcome');
      assert.equal(await page.locator('#round2-rows tr').count(),2);
      assert.match(await page.locator('#round2-rows').innerText(),/阶梯发票/);
      assert.match(await page.locator('#round2-rows').innerText(),/依赖锁/);
      await page.click('#round2-reset');
      await page.selectOption('#round2-dimension','efficiency');
      assert.equal(await page.locator('#round2-rows tr').count(),14);
      await page.click('#round2-reset');
      await page.selectOption('#round2-category','recall');
      assert.equal(await page.locator('#round2-rows tr').count(),3);
      await page.click('#round2-reset');
      await page.click('#round2-sort');
      assert.match(await page.locator('#round2-rows tr').first().innerText(),/原子批量预留/);
      await page.locator('#round2-rows .case-link').first().click();
      assert.match(await page.locator('#round2-detail').innerText(),/重复扣库存/);
      assert.equal(await page.locator('#round2-detail .detail-score').first().innerText(),'结果\n通过');
      await page.screenshot({path:path.join(output,entry+'-case-detail.png')});
      await page.click('#round2-close');
      await page.fill('#round2-search','依赖锁');
      assert.equal(await page.locator('#round2-rows tr').count(),1);
      assert.equal(await page.locator('#case-rows tr').count(),30);
      await page.click('#round2-reset');
      await page.locator('#round2 .source-section summary').click();
      const jsonPromise=page.waitForEvent('download');await page.click('#round2-download');
      const jsonDownload=await jsonPromise;await jsonDownload.saveAs(path.join(output,entry+'-evidence.json'));
      const evidence=JSON.parse(fs.readFileSync(path.join(output,entry+'-evidence.json'),'utf8'));
      assert.equal(evidence.cases.length,20);assert.equal(evidence.raw_overall.passed,5);
      assert.equal(evidence.display_overall.passed,5);
      assert.equal(evidence.raw_strict_dimensions.outcome,13);
      assert.equal(evidence.display_strict_dimensions.outcome,18);
      const corrections=evidence.cases.filter(c=>c.display_corrections.length);
      assert.equal(corrections.length,5);
      for(const c of evidence.cases){
        assert.equal(c.display_passed,Object.values(c.display_dimensions).every(Boolean));
        for(const dim of ['process','efficiency','safety','reliability'])assert.equal(c.display_dimensions[dim],c.raw_dimensions[dim]);
      }
      for(const c of corrections){
        assert.equal(c.raw_dimensions.outcome,false);
        assert.equal(c.display_dimensions.outcome,true);
        assert.equal(c.display_failures.some(f=>f.dimension==='outcome'),false);
        assert.equal(fs.existsSync(path.join(root,c.display_corrections[0].source)),true);
      }
      for(const source of evidence.sources)assert.equal(fs.existsSync(path.join(root,source.path)),true);
      await page.setViewportSize({width:390,height:844});
      await page.locator('#round2').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output,entry+'-mobile.png')});
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
      await page.locator('#round2-cases').scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(output,entry+'-mobile-cases.png')});
      await page.locator('#round2-rows .case-link').first().click();
      assert.equal(await page.locator('#round2-dialog').isVisible(),true);
      await page.click('#round2-close');
      assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
      await page.setViewportSize({width:1440,height:1040});
      checks.push({entry,passed:true,firstRoundCases:30,secondRoundCases:20});
    }
    assert.deepEqual(errors,[]);
    fs.writeFileSync(path.join(output,'checks.json'),JSON.stringify({checks,errors,scope:'Offline rendering, counts, filters, details, downloads and mobile layout; no model evaluation rerun.'},null,2));
    console.log(JSON.stringify({passed:true,entries:checks.length,errors}));
  } finally {await browser.close()}
})().catch(error=>{console.error(error);process.exitCode=1});
