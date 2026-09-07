const {chromium}=require('C:/Users/inari/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const path=require('node:path');
const fs=require('node:fs');
const {pathToFileURL}=require('node:url');
const root=path.resolve(__dirname,'../..');
const directory=path.resolve(root,process.argv[2]||'.aster/frontend-v1-v10-stage');
const out=path.join(root,'.aster/frontend-v1-v10-checks');fs.mkdirSync(out,{recursive:true});
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000}});const errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  for(const entry of ['index.html','dashboard.html']){
   await page.goto(pathToFileURL(path.join(directory,entry)).href);
   assert.equal(await page.locator('#case-rows tr').count(),30);
   assert.equal(await page.locator('#round2-rows tr').count(),20);
   assert.equal(await page.locator('#round3-rows tr').count(),20);
   assert.equal(await page.locator('#effective-changes [data-effective-rank]').count(),5);
   assert.match(await page.locator('#effective-changes').innerText(),/v10.*撤掉|撤掉.*v10|不能漏记 v10/);
   assert.match(await page.locator('#effective-changes').innerText(),/53→128/);
   assert.equal(await page.evaluate(()=>document.querySelector('#round3 > :last-child').id),'effective-changes');
   assert.deepEqual(await page.evaluate(()=>window.EVAL_ROUND2.batches.map(b=>b.counts.outcome)),[16,18]);
   assert.equal(await page.evaluate(()=>document.querySelector('main > section:last-child').id),'round3');
   assert.equal(await page.evaluate(()=>document.getElementById('round2').compareDocumentPosition(document.getElementById('round3'))&Node.DOCUMENT_POSITION_FOLLOWING),4);
   assert.equal(await page.locator('#round2 .hero-meta').innerText().then(s=>s.includes('20 路并行')),true);
   for(const id of ['round2','round3']){
    await page.selectOption('#'+id+'-split','regression');assert.equal(await page.locator('#'+id+'-rows tr').count(),5);
    await page.click('#'+id+'-reset');await page.selectOption('#'+id+'-dimension','outcome');
    assert.equal(await page.locator('#'+id+'-rows tr').count(),id==='round2'?4:2);
    await page.click('#'+id+'-reset');
    await page.locator('#'+id+'-rows [data-current-case]').first().click();
    assert.equal(await page.locator('#'+id+'-dialog').evaluate(e=>e.open),true);
    assert.equal(await page.locator('#case-dialog').evaluate(e=>e.open),false);
    await page.click('#'+id+'-close');
    const download=page.waitForEvent('download');await page.click('#'+id+'-export');
    const file=await download;assert.ok(file.suggestedFilename().endsWith('.csv'));
   }
   assert.equal(await page.evaluate(()=>new Set([...document.querySelectorAll('[id]')].map(e=>e.id)).size===document.querySelectorAll('[id]').length),true);
   await page.locator('#round3').scrollIntoViewIfNeeded();await page.screenshot({path:path.join(out,entry+'-desktop.png')});
   await page.setViewportSize({width:390,height:844});await page.locator('#round3').scrollIntoViewIfNeeded();
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1));
   await page.screenshot({path:path.join(out,entry+'-mobile.png')});await page.setViewportSize({width:1440,height:1000});
   records.push({entry,passed:true});
  }
  assert.deepEqual(errors,[]);fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify({directory,records,errors},null,2));
  console.log(JSON.stringify({directory,records,errors}));
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
