const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const read = (relative) => fs.readFileSync(path.join(__dirname, '..', relative), 'utf8');

const element = () => ({
  addEventListener() {}, classList: {add() {}, remove() {}, toggle() {}},
  textContent: '', innerHTML: '', title: '', placeholder: '', value: '', hidden: false, disabled: false,
  setAttribute() {}, dataset: {}
});
const storage = new Map();
const context = {
  document: {
    querySelector: () => element(), querySelectorAll: () => [], addEventListener() {},
    documentElement: {dataset: {}, lang: ''}, title: ''
  },
  window: null,
  location: {search: ''},
  localStorage: {
    getItem: (key) => (storage.has(key) ? storage.get(key) : null),
    setItem: (key, value) => storage.set(key, String(value))
  },
  URLSearchParams, Date, AbortSignal, console,
  fetch: () => new Promise(() => {}),
  setInterval() {}, clearTimeout() {}, setTimeout() {}
};
context.window = context;
vm.createContext(context);
vm.runInContext(read('web/i18n.js'), context);
vm.runInContext(read('web/app.js'), context);

// Availability rules
vm.runInContext(`
  const freeGpu = {index:0,processes_available:true,process_count:0,memory_used_bytes:3*MiB,memory_free_bytes:23*GiB,utilization_gpu:0};
  assert.equal(idle(freeGpu),true);
  assert.equal(idle({...freeGpu,process_count:1,memory_used_bytes:20*GiB}),false,'VLLM with 0% utilization remains occupied');
  assert.equal(idle({...freeGpu,processes_available:false,process_count:null}),false,'Missing process data never means idle');
  state.data = {stale_after_seconds:12, own_users:['demo'], servers:[], interval_seconds:3, history_seconds:300};
  assert.equal(fresh({status:'online',last_success:Date.now()/1000-30}),false,'Old data cannot be recommended');
  state.failed = true;
  assert.equal(fresh({status:'online',last_success:Date.now()/1000}),false,'Local service outage invalidates availability');
  state.failed = false;
  assert.equal(matches(freeGpu),true,'Default filter shows every GPU');
  state.gpuFilter = 'mine';
  assert.equal(matches(freeGpu),false,'The own-user filter needs a matching process owner');
  assert.equal(matches({...freeGpu,processes:[{user:'demo'}]}),true,'Own-user filter matches configured users only');
  assert.equal(esc('<img onerror="x">'), '&lt;img onerror=&quot;x&quot;&gt;');
`, Object.assign(context, {assert}));

// Localization: default language, switch, and dictionary parity
vm.runInContext(`
  assert.equal(I18N.currentLang(), 'zh-CN', 'Default language is Simplified Chinese');
  assert.equal(t('status.idle'), '空闲');
  I18N.setLang('en');
  assert.equal(t('status.idle'), 'Idle');
  assert.equal(t('stats.hosts', {online: 1, total: 2}), '1 / 2 hosts online');
  assert.equal(I18N.timeLocale(), 'en-GB');
  I18N.setLang('zh-CN');
  assert.equal(I18N.timeLocale(), 'zh-CN');
  const base = I18N.messages[I18N.fallback];
  for (const [language, dictionary] of Object.entries(I18N.messages)) {
    assert.deepEqual(Object.keys(dictionary).sort(), Object.keys(base).sort(), language + ' must translate every key');
    for (const [key, value] of Object.entries(dictionary)) {
      assert.ok(value.length > 0, language + ':' + key + ' must not be empty');
      const params = (value.match(/\\{(\\w+)\\}/g) ?? []).sort();
      assert.deepEqual(params, (base[key].match(/\\{(\\w+)\\}/g) ?? []).sort(), language + ':' + key + ' placeholders');
    }
  }
`, Object.assign(context, {assert}));

console.log('PASS: availability, stale data, own-user filter, HTML escaping, and zh-CN/en dictionary parity.');
