const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = new Map();
function element(key) {
  if (!elements.has(key)) elements.set(key, {innerHTML:'',textContent:'',hidden:false,value:'',dataset:{},classList:{toggle(){},add(){},remove(){}},listeners:{},addEventListener(k,f){this.listeners[k]=f;},setAttribute(){}});
  return elements.get(key);
}
const at=Math.floor(Date.now()/60000)*60;
const fixture={start:at-3600,end:at,step:300,hosts:[{name:'host',status:'recording',users:['demo']}],
  summary:{gpu_hours:1,active_hours:1,gpu_count:1,memory_peak:1024**3,coverage:1},
  devices:[{host:'host',uuid:'uuid',index:0,name:'<script>bad</script>',seconds:3600}],
  sessions:[{host:'host',uuid:'uuid',index:0,name:'GPU',start:at-3600,end:at,seconds:3600,shared:0,memory:1024**3,util_sum:1800,util_seconds:3600}],
  series:[[at-300,300,300,150,300]]};
const context={document:{querySelector:element,querySelectorAll:()=>[],addEventListener(){},documentElement:{dataset:{}}},window:null,
 location:{search:''},localStorage:{getItem(){return null;}},setInterval(){},URLSearchParams,AbortSignal,console,
 state:{data:{servers:[{name:'host'}]}},fetch:async(url, options={})=>{
  if(options.method==='POST') {
    assert.equal(url,'/api/agents'); assert.equal(options.headers['X-GPU-Monitor'],'1');
    assert.deepEqual(JSON.parse(options.body),{action:'install',servers:['host']});
    const job={id:'job',status:'running',servers:['host'],results:{host:{step:'checking'}}};
    fixture.jobs=[job]; return {ok:true,json:async()=>job};
  }
  return {ok:true,json:async()=>JSON.parse(JSON.stringify(fixture))};
 },
 esc:value=>String(value??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
 num:(v,n=0)=>Number.isFinite(v)?v.toFixed(n):'—',gib:v=>Number.isFinite(v)?(v/1024**3).toFixed(1):'—'};
context.window=context;
vm.createContext(context);
vm.runInContext(fs.readFileSync('web/i18n.js','utf8'),context);
context.t=(...args)=>context.I18N.t(...args);
vm.runInContext(fs.readFileSync('web/usage.js','utf8'),context);
(async()=>{
 context.ComputeWatch.show(true);
 await new Promise(resolve=>setImmediate(resolve));
 const page=element('#usage-page');
 assert.ok(page.innerHTML.includes('算力观察'));
 assert.ok(page.innerHTML.includes('&lt;script&gt;bad&lt;/script&gt;'));
 assert.ok(!page.innerHTML.includes('<script>bad'));
 assert.ok(page.innerHTML.includes('1.00'));
 assert.equal(element('#overview-page').hidden,true);
 // A short recording fills the chart even when the requested window is longer.
 const trend=page.innerHTML.match(/<svg class="usage-trend"[\s\S]*?<\/svg>/)[0];
 assert.match(trend, /class="usage-trend-line" d="M[^"]+ L/);
 assert.ok(!trend.includes("usage-trend-point"));
 const heatRows=[...page.innerHTML.matchAll(/class="usage-cells" style="--columns:(\d+)"/g)];
 assert.equal(heatRows.length, 1);
 assert.ok(Number(heatRows[0][1]) <= 2);
 assert.ok(!page.innerHTML.includes('usage-cell future'));
 const axis=page.innerHTML.match(/<div class="usage-axis">([\s\S]*?)<\/div>/)[1];
 const expectedStart=new Date((at-300)*1000).toLocaleString(context.I18N.timeLocale(), {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
 assert.ok(axis.includes(expectedStart));
 page.listeners.click({target:{closest:()=>({dataset:{device:'missing|uuid'}})}});
 assert.ok(page.innerHTML.includes('清除筛选'));
 assert.ok(page.innerHTML.includes('所选范围暂无个人使用记录'));
 context.ComputeWatch.show(false);
 assert.equal(page.hidden,true);
 assert.equal(element('#overview-page').hidden,false);
 for (const status of ['connecting', 'unreachable', 'stopped']) {
  fixture.hosts=[{name:'host',status}];
  context.ComputeWatch.show(true);
  await new Promise(resolve=>setImmediate(resolve));
  assert.ok(!page.innerHTML.includes('尚未部署持续采集'));
  assert.ok(!page.innerHTML.includes('data-deploy-host='));
 }
 fixture.hosts=[{name:'host',status:'not_installed',configured_users:['demo']}];
 fixture.devices=[]; fixture.sessions=[]; fixture.series=[];
 context.ComputeWatch.show(true);
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(page.innerHTML.includes('还没有部署采集程序'));
 assert.ok(page.innerHTML.includes('data-deploy-host="host"'));
 assert.ok(page.innerHTML.includes('确认下方服务器与采集账号'));
 page.listeners.click({target:{closest:()=>({dataset:{deployHost:'host'}})}});
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(page.innerHTML.includes('正在检查 SSH、Python 与 NVML'));
 assert.ok(page.innerHTML.includes('disabled'));
 fixture.jobs=[{status:'failed',servers:['host'],results:{host:{step:'failed',error:'<denied>'}}}];
 context.ComputeWatch.show(true);
 await new Promise(resolve=>setImmediate(resolve));
 assert.ok(page.innerHTML.includes('&lt;denied&gt;'));
 assert.ok(page.innerHTML.includes('部署失败'));
 assert.ok(!page.innerHTML.includes('<denied>'));
 console.log('PASS: usage, navigation, undeployed guide, real deploy POST, progress and escaped errors.');
})().catch(error=>{console.error(error);process.exitCode=1;});
