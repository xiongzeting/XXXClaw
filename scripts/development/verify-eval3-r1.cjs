// Headless local-file UI regression: scores, filtering, exports, and both entry points.
const {chromium}=require('C:/Users/inari/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const crypto=require('node:crypto');
const root=path.resolve(__dirname,'../..');
const front=path.join(root,'frontend/eval-round1');
const out=path.join(root,'.aster/frontend-eval3-r1-checks');
fs.mkdirSync(out,{recursive:true});
const source=JSON.parse(fs.readFileSync(path.join(front,'eval3-r1-data.json'),'utf8'));
const history=JSON.parse(fs.readFileSync(path.join(front,'round2-data.json'),'utf8'));
const archive=path.join(root,'docs/interview/eval no 3/2026-09-07-hardened-r1');
const final=JSON.parse(fs.readFileSync(path.join(archive,'assistant-judged-report.json'),'utf8'));
assert.deepEqual(source.summary,final.summary);
assert.deepEqual(source.summary.dimension_pass_counts,{outcome:10,process:18,efficiency:8,safety:11,reliability:20});
assert.equal(source.cases.filter(c=>c.passed).length,6);
for(const item of JSON.parse(fs.readFileSync(path.join(archive,'archive-inventory.json'),'utf8'))){
 const bytes=fs.readFileSync(path.join(archive,item.path));
 assert.equal(bytes.length,item.bytes);
 assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),item.sha256);
}
async function downloaded(page,id,name){
 const waiting=page.waitForEvent('download');await page.click(id);const file=await waiting;
 assert.equal(file.suggestedFilename(),name);return fs.readFileSync(await file.path(),'utf8');
}
(async()=>{
 const browser=await chromium.launch({channel:'msedge',headless:true});
 const records=[],errors=[];
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1000},acceptDownloads:true});
  page.on('pageerror',e=>errors.push(e.message));
  for(const entry of ['index.html','dashboard.html','eval3-report.html']){
   await page.goto(pathToFileURL(path.join(front,entry)).href);
   await page.locator('#eval3-r1').scrollIntoViewIfNeeded();
   assert.equal(await page.locator('#r1-rows tr').count(),20);
   assert.deepEqual(await page.evaluate(()=>window.EVAL3_R1.summary.dimension_pass_counts),source.summary.dimension_pass_counts);
   assert.match(await page.locator('#eval3-r1').innerText(),/445\.147/);
   assert.match(await page.locator('#eval3-r1').innerText(),/完整账单未知/);
   for(const [dimension,count] of Object.entries({outcome:10,process:2,efficiency:12,safety:9,reliability:0,passed:6,failed:14})){
    await page.selectOption('#r1-filter',dimension);assert.equal(await page.locator('#r1-rows tr').count(),count);
    if(!count) assert.equal(await page.locator('#r1-empty').isVisible(),true);
   }
   await page.click('#r1-reset');await page.fill('#r1-search','revision');
   assert.ok(await page.locator('#r1-rows tr').count()>0);
   await page.click('#r1-reset');await page.fill('#r1-search','no-such-case');assert.equal(await page.locator('#r1-rows tr').count(),0);
   await page.click('#r1-reset');await page.selectOption('#r1-sort','tokens');
   assert.equal(await page.locator('#r1-rows tr').first().getAttribute('data-r1-case'),'shipping_caps_r3');
   await page.selectOption('#r1-sort','time');
   assert.equal(await page.locator('#r1-rows tr').first().getAttribute('data-r1-case'),'audit_bundle_integration_r3');
   await page.locator('[data-r1-open="audit_bundle_integration_r3"]').click();
   assert.equal(await page.locator('#r1-dialog').evaluate(e=>e.open),true);
   assert.match(await page.locator('#r1-detail').innerText(),/原失败 → 复核失败/);
   await page.keyboard.press('Escape');assert.equal(await page.locator('#r1-dialog').evaluate(e=>e.open),false);
   await page.click('#r1-reset');await page.selectOption('#r1-filter','passed');
   const csv=await downloaded(page,'#r1-csv','eval3-hardened-r1-filtered.csv');
   assert.equal(csv.trim().split('\r\n').length,7);
   const columns=csv.trim().split('\r\n')[0].split(',').length;assert.equal(columns,16);
   assert.match(csv,/known_cost_subtotal_usd/);assert.match(csv,/compression_rule_priority_exceptions_r3/);
   const exported=JSON.parse(await downloaded(page,'#r1-json','eval3-hardened-r1.json'));
   assert.deepEqual(exported,source);
   await page.click('#r1-reset');
   const links=await page.locator('#eval3-r1 a').evaluateAll(nodes=>nodes.map(e=>e.href));
   for(const href of links){const u=new URL(href);if(u.protocol==='file:')assert.ok(fs.existsSync(require('node:url').fileURLToPath(u)),href);}
   assert.equal(await page.evaluate(()=>new Set([...document.querySelectorAll('[id]')].map(e=>e.id)).size===document.querySelectorAll('[id]').length),true);
   if(entry!=='eval3-report.html'){
    assert.equal(await page.locator('#case-rows tr').count(),30);
    assert.equal(await page.locator('#round2-rows tr').count(),20);
    assert.equal(await page.locator('#round3-rows tr').count(),0);
    assert.equal(await page.locator('a[href="eval3-legacy-report.html"]').count(),0);
    assert.equal(await page.locator('h2').filter({hasText:'Eval3 历史旧题库'}).count(),0);
    assert.equal(await page.evaluate(()=>window.EVAL_ROUND2.batches.some(b=>b.version==='eval3')),false);
    assert.equal(await page.locator('#effective-changes [data-effective-rank]').count(),5);
    assert.deepEqual(await page.evaluate(()=>window.EVAL_ROUND2),history);
    assert.equal(await page.locator('.section-nav a[href="#eval3-r1"]').count(),1);
    assert.equal(await page.evaluate(()=>document.querySelector('main > section:last-child').id),'eval3-r1');
   }
   await page.locator('#eval3-r1').evaluate(e=>e.scrollIntoView({block:'start',behavior:'instant'}));
   await page.screenshot({path:path.join(out,entry+'-desktop.png')});
   await page.setViewportSize({width:390,height:844});await page.locator('#eval3-r1').evaluate(e=>e.scrollIntoView({block:'start',behavior:'instant'}));
   assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'mobile page overflow');
   await page.screenshot({path:path.join(out,entry+'-mobile.png')});
   await page.setViewportSize({width:1440,height:1000});
   records.push({entry,passed:true});
  }
  assert.deepEqual(errors,[]);
  fs.writeFileSync(path.join(out,'validation.json'),JSON.stringify({records,errors,archiveHashes:true},null,2));
  console.log(JSON.stringify({records,errors,archiveHashes:true}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exit(1);});
