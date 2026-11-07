'use strict';
(() => {
  let visible = false, days = 1, host = 'all', data = null, loading = false, failed = false;
  let selection = null, limit = 40, serial = 0;
  let deploying = false, deploymentError = null;
  const page = document.querySelector('#usage-page');
  const tr = key => t(`usage.${key}`);
  const date = at => new Date(at * 1000);
  const formatTime = at => date(at).toLocaleString(I18N.timeLocale(), {month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false});
  const hours = seconds => num(seconds / 3600, 2);
  function windowRange() {
    const end = new Date();
    const start = new Date(end); start.setHours(0,0,0,0); start.setDate(start.getDate() - days + 1);
    return {start:Math.max(start.getTime()/1000, Math.ceil((end.getTime()/1000-168*3600)/60)*60), end:end.getTime()/1000};
  }
  function syncNav() {
    document.querySelector('#usage-nav').classList.toggle('selected', visible);
    if (visible) {
      document.querySelectorAll('[data-server]').forEach(n => n.classList.remove('selected'));
      document.querySelector('#breadcrumb-title').textContent = tr('title');
    }
  }
  function show(next) {
    visible = next;
    window.history?.replaceState(null, '', next ? '#compute-watch' : location.pathname + location.search);
    document.querySelector('#overview-page').hidden = next;
    page.hidden = !next;
    syncNav();
    if (next) { render(); refresh(); }
  }
  async function refresh() {
    if (!visible || loading) return;
    loading = true; failed = false; const token = ++serial;
    const range = windowRange();
    if (!data) render();
    try {
      const response = await fetch(`/api/usage?${new URLSearchParams({...range,server:host})}`, {cache:'no-store',signal:AbortSignal.timeout(8000)});
      if (!response.ok) throw new Error('history unavailable');
      const next = await response.json();
      if (token === serial) data = next;
    } catch { if (token === serial) failed = true; }
    finally { if(token===serial) { loading = false; render(); } }
  }
  const busy = name => (data?.jobs ?? []).some(j => j.status === 'running' && j.servers.includes(name));
  async function deploy(names) {
    if (deploying || !names.length || names.some(busy)) return;
    deploying = true; deploymentError = null; render();
    try {
      const response = await fetch('/api/agents', {method:'POST', headers:{'Content-Type':'application/json','X-GPU-Monitor':'1'},
        body:JSON.stringify({action:'install',servers:names}), signal:AbortSignal.timeout(10000)});
      if (!response.ok) {
        let error; try { error = (await response.json()).error; } catch {}
        throw new Error(error || `HTTP ${response.status}`);
      }
      const job = await response.json();
      if (data) data.jobs = [...(data.jobs??[]),job];
    } catch(error) { deploymentError = error.message; }
    finally { deploying = false; render(); refresh(); }
  }
  function setupPanel(panels) {
    const needs = panels.filter(h => h.status !== 'recording');
    const jobs = data?.jobs ?? [];
    if (!needs.length && !deploymentError) return jobs.some(j=>j.status==='done') ? `<p class="usage-job" role="status">${esc(tr('deploy_done'))}</p>` : ''; 
    const names = needs.map(h=>h.name);
    const working = deploying || names.some(busy);
    return `<section class="usage-setup" aria-label="${esc(tr('setup'))}">
      <div class="usage-setup-heading"><div><h2>${esc(tr(needs.length?'not_deployed':'setup'))}</h2><p>${esc(tr('setup_hint'))}</p></div>
      ${needs.length>1?`<button type="button" class="button" id="usage-deploy-all" ${working?'disabled':''}>${esc(tr(working?'deploying':'deploy_all'))}</button>`:''}</div>
      <ol class="usage-deploy-guide"><li>${esc(tr('guide_account'))}</li><li>${esc(tr('guide_install'))}</li><li>${esc(tr('guide_done'))}</li></ol>
      <div class="usage-deploy-hosts">${needs.map(h=>{
        const job=[...jobs].reverse().find(j=>j.servers.includes(h.name));
        const result=job?.results?.[h.name];
        return `<div class="usage-deploy-host"><div><strong>${esc(h.name)}</strong><p>${esc(tr('users'))}: ${esc((h.configured_users?.length?h.configured_users:h.users??[]).join(', ')||tr('account_missing'))}</p><p>${esc(tr(h.status))}</p>
        ${result?`<p class="usage-job ${result.step==='failed'?'usage-job-error':''}" role="status">${esc(tr('step_'+result.step))}${result.error?`: ${esc(result.error)}`:''}</p>`:''}</div>
        <button type="button" class="button" data-deploy-host="${esc(h.name)}" ${deploying||busy(h.name)?'disabled':''}>${esc(tr(busy(h.name)||deploying?'deploying':'deploy_one'))}</button></div>`;
      }).join('')}</div>
      ${jobs.filter(j=>j.status==='done' && j.servers.some(s=>panels.some(h=>h.name===s))).slice(-1).map(()=>`<p class="usage-job" role="status">${esc(tr('deploy_done'))}</p>`).join('')}
      ${deploymentError?`<p class="usage-job-error" role="alert">${esc(deploymentError)}</p>`:''}
      <details><summary>${esc(tr('manual_commands'))}</summary><code>python3 scripts/manage.py agent install${host==='all'?'':' '+esc(host)}</code></details>
    </section>`;
  }
  const empty = () => `<div class="usage-empty"><strong>${esc(tr('empty'))}</strong><p>${esc(tr('empty_hint'))}</p></div>`;
  function stat(label, value, unit) {
    return `<div class="stat"><span class="stat-label">${esc(tr(label))}</span><div><strong>${value}</strong><span class="stat-unit">${esc(unit)}</span></div></div>`;
  }
  function trend() {
    if (!data?.series.length) return empty();
    const {start,end,step} = data, width=900, height=160, left=38, right=12, top=15, bottom=30;
    const max = Math.max(1,...data.series.map(p=>p[1]/step));
    const x = at => left+(at-start)/(end-start)*(width-left-right);
    const y = v => height-bottom-v/max*(height-top-bottom);
    let path='', previous=null;
    for (const p of data.series) {
      if (!p[2]) { previous=null; continue; }
      const at=Math.max(start,p[0])+Math.min(step,end-p[0])/2;
      path += `${previous !== null && p[0]-previous<=step ? 'L':'M'}${x(at).toFixed(1)},${y(p[1]/step).toFixed(1)} `;
      previous=p[0];
    }
    const grid=[0,.5,1].map(r=>`<path class="usage-grid" d="M${left},${y(max*r)}H${width-right}"/><text x="${left-8}" y="${y(max*r)+4}" text-anchor="end">${num(max*r,1)}</text>`).join('');
    const ticks=[0,.25,.5,.75,1].map((r,i)=>`<text x="${x(start+(end-start)*r)}" y="${height-6}" text-anchor="${i===0?'start':i===4?'end':'middle'}">${esc(days===1 ? date(start+(end-start)*r).toLocaleTimeString(I18N.timeLocale(),{hour:'2-digit',minute:'2-digit',hour12:false}) : date(start+(end-start)*r).toLocaleDateString(I18N.timeLocale(),{month:'2-digit',day:'2-digit'}))}</text>`).join('');
    return `<svg class="usage-trend" viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(tr('trend_hint'))}">${grid}<path class="usage-trend-line" d="${path}"/>${ticks}</svg>`;
  }
  function timeline() {
    const devices = (data?.devices??[]).filter(d=>d.seconds>0);
    if (!devices.length) return empty();
    return `<div class="usage-timeline">${devices.map(d=>{
      const sessions=data.sessions.filter(s=>s.host===d.host&&s.uuid===d.uuid);
      return `<div class="usage-lane"><div class="usage-device-label">${esc(d.host)}<small>GPU ${d.index} · ${esc(d.name.replace('NVIDIA ',''))}</small></div><div class="usage-track">${sessions.map(s=>{
        const left=Math.max(0,(s.start-data.start)/(data.end-data.start)*100);
        const width=Math.max(.15,Math.min(100-left,(s.end-s.start)/(data.end-data.start)*100));
        const label=`${d.host} / GPU ${d.index} · ${formatTime(s.start)} – ${formatTime(s.end)} · ${hours(s.seconds)} ${tr('hours')}${s.shared?' · '+tr('shared'):''}`;
        return `<button type="button" class="usage-span ${s.shared?'shared':''}" style="left:${left}%;width:${width}%" data-session="${data.sessions.indexOf(s)}" aria-label="${esc(label)}" title="${esc(label)}"></button>`;
      }).join('')}</div></div>`;
    }).join('')}<div class="usage-axis"><span>${esc(formatTime(data.start))}</span><span>${esc(formatTime(data.end))}</span></div></div>`;
  }
  function heatmap() {
    if (!data) return '';
    const range=windowRange(), start=new Date(range.start*1000); start.setHours(0,0,0,0);
    const rows=[];
    // Construct real local hours, including DST repeats, rather than assuming 24h days.
    for(let d=0;d<days;d++) {
      const day=new Date(start); day.setDate(day.getDate()+d);
      const next=new Date(day); next.setDate(next.getDate()+1);
      const cells=[];
      for(let at=day.getTime()/1000;at<next.getTime()/1000;at+=3600) {
        const future=at>=data.end;
        const points=data.series.filter(p=>p[0]>=at&&p[0]<at+3600);
        const used=points.reduce((n,p)=>n+p[1],0)/3600;
        const covered=points.reduce((n,p)=>n+p[2],0);
        const expected=Math.max(0,Math.min(at+3600,data.end)-Math.max(at,data.start)) * data.devices.length;
        const partial=!future && (!expected || covered/expected<.9);
        const label=`${formatTime(at)} · ${future?tr('future'):num(used,2)+' GPU·h'}${partial?' · '+tr('unknown'):''}`;
        cells.push(`<button type="button" class="usage-cell ${partial?'partial':''} ${future?'future':''}" style="--intensity:${Math.min(.95,.08+used/Math.max(1,data.summary.gpu_count)*.87)}" data-hour="${at}" ${future?'disabled':''} aria-pressed="${selection?.hour===at}" title="${esc(label)}" aria-label="${esc(label)}"><span>${date(at).getHours()}</span></button>`);
      }
      rows.push(`<div class="usage-heat-row"><span>${esc(day.toLocaleDateString(I18N.timeLocale(),{month:'2-digit',day:'2-digit'}))}</span><div class="usage-cells">${cells.join('')}</div></div>`);
    }
    return `<div class="usage-heatmap">${rows.join('')}</div>`;
  }
  function distribution() {
    const devices=[...(data?.devices??[])].filter(d=>d.seconds>0).sort((a,b)=>b.seconds-a.seconds);
    if (!devices.length) return empty();
    const max=devices[0].seconds;
    return devices.map(d=>`<button class="usage-distribution" type="button" data-device="${esc(d.host+'|'+d.uuid)}"><span>${esc(d.host)} / GPU ${d.index}<small>${esc(d.name)}</small></span><span class="usage-bar"><i style="width:${d.seconds/max*100}%"></i></span><strong>${hours(d.seconds)} GPU·h</strong></button>`).join('');
  }
  function records() {
    let rows=data?.sessions??[];
    if(selection?.device) rows=rows.filter(s=>s.host+'|'+s.uuid===selection.device);
    if(selection?.hour!==undefined) rows=rows.filter(s=>s.start<selection.hour+3600&&s.end>selection.hour);
    if(selection?.session) rows=rows.filter(s=>s.host===selection.session.host && s.uuid===selection.session.uuid && s.start===selection.session.start);
    if(!rows.length) return empty();
    return `<div class="table-scroll"><table><thead><tr>${['host_gpu','time','duration','memory','load','state'].map(k=>`<th>${esc(tr(k))}</th>`).join('')}</tr></thead><tbody>${rows.slice(0,limit).map(s=>`<tr><td>${esc(s.host)} / GPU ${s.index}<small class="usage-muted">${esc(s.name)}</small></td><td>${esc(formatTime(s.start))}<br>${esc(formatTime(s.end))}</td><td>${hours(s.seconds)} h</td><td>${gib(s.memory)} GiB</td><td>${s.util_seconds?num(s.util_sum/s.util_seconds*100,1)+'%':'—'}</td><td>${esc(tr(s.shared?'shared':'exclusive'))}</td></tr>`).join('')}</tbody></table></div>${rows.length>limit?`<button class="button secondary" id="usage-more">${esc(tr('more'))}</button>`:''}`;
  }
  function render() {
    if(!visible) return;
    syncNav();
    const hosts=state.data?.servers??[];
    const summary=data?.summary, known=!!data?.devices.length;
    const panels = data?.hosts??[];
    const undeployed = panels.length && panels.every(h=>h.status==='not_installed');
    page.innerHTML=`<div class="page-heading"><div><div class="eyebrow">COMPUTE WATCH <span class="eyebrow-line"></span> PERSONAL</div><h1>${esc(tr('title'))}</h1><p>${esc(tr('subtitle'))}</p></div><button class="button secondary" id="usage-refresh" ${loading?'disabled':''}>↻ ${esc(tr('refresh'))}</button></div>
      <div class="usage-toolbar"><div class="usage-periods" role="group" aria-label="${esc(tr('range'))}">${[[1,'today'],[3,'three'],[7,'week']].map(([value,key])=>`<button type="button" class="button ${days===value?'selected':'secondary'}" data-days="${value}" aria-pressed="${days===value}">${esc(tr(key))}</button>`).join('')}</div><label>${esc(tr('host_gpu'))} <select id="usage-host"><option value="all">${esc(tr('all'))}</option>${hosts.map(h=>`<option value="${esc(h.name)}" ${h.name===host?'selected':''}>${esc(h.name)}</option>`).join('')}</select></label></div>
      ${failed?`<div class="connection-error" role="alert">${esc(tr('error'))}</div>`:''}
      <div class="usage-agents" aria-live="polite">${panels.map(h=>`<div><span class="host-status ${h.status==='recording'?'online':''}"></span><strong>${esc(h.name)}</strong><span>${esc(tr(h.status))}</span>${h.users?.length?`<small>${esc(tr('users'))}: ${esc(h.users.join(', '))}</small>`:''}${h.fetched_at?`<small>${esc(tr('fetched'))}: ${esc(formatTime(h.fetched_at))}</small>`:''}${h.error?`<small>${esc(h.error)}</small>`:''}</div>`).join('')}</div>
      ${setupPanel(panels)}
      ${!data?`<div class="usage-empty" role="status">${esc(tr('loading'))}</div>`:undeployed?`<div class="usage-empty"><strong>${esc(tr('no_deployment'))}</strong><p>${esc(tr('no_deployment_hint'))}</p></div>`:`<section class="stats">${stat('gpu_hours',known?num(summary.gpu_hours,2):'—','GPU·h')}${stat('active',known?num(summary.active_hours,2):'—',tr('hours'))}${stat('devices',known?summary.gpu_count:'—',tr('cards'))}${stat('memory',gib(summary.memory_peak),'GiB')}</section>
      <div class="usage-meta"><span>${esc(tr('coverage'))}: ${summary.coverage===null?'—':num(summary.coverage*100,1)+'%'}</span><span>${esc(formatTime(data.start))} — ${esc(formatTime(data.end))} · ${esc(Intl.DateTimeFormat().resolvedOptions().timeZone)}</span></div>
      <section class="usage-section"><div class="section-heading"><div><div class="eyebrow">ACTIVITY</div><h2>${esc(tr('trend'))}</h2><p>${esc(tr('trend_hint'))}</p></div></div>${trend()}</section>
      <section class="usage-section"><div class="section-heading"><div><div class="eyebrow">TIMELINE</div><h2>${esc(tr('timeline'))}</h2><p>${esc(tr('timeline_hint'))}</p></div><span class="usage-legend"><i></i>${esc(tr('personal'))}<i class="shared"></i>${esc(tr('shared'))}</span></div>${timeline()}</section>
      <div class="usage-columns"><section class="usage-section"><div class="section-heading"><div><div class="eyebrow">RHYTHM</div><h2>${esc(tr('heatmap'))}</h2><p>${esc(tr('heatmap_hint'))}</p></div></div>${heatmap()}</section><section class="usage-section"><div class="section-heading"><div><div class="eyebrow">DEVICES</div><h2>${esc(tr('distribution'))}</h2></div></div>${distribution()}</section></div>
      <section class="usage-section"><div class="section-heading"><div><div class="eyebrow">HISTORY</div><h2>${esc(tr('records'))}</h2><p>${esc(tr('load_hint'))}</p></div>${selection?`<button class="button secondary" id="usage-clear">${esc(tr('clear'))}</button>`:''}</div>${records()}</section>`}
      <footer><span>${esc(tr('retention'))}</span><span>NVML / SSH / SQLite</span></footer>`;
  }
  document.querySelector('#usage-nav').addEventListener('click',()=>show(true));
  page.addEventListener('click', event=>{
    const button=event.target.closest('button'); if(!button) return;
    if(button.id==='usage-refresh') refresh();
    if(button.dataset.deployHost) deploy([button.dataset.deployHost]);
    if(button.id==='usage-deploy-all') deploy((data?.hosts??[]).filter(h=>h.status!=='recording').map(h=>h.name));
    if(button.dataset.days) { days=Number(button.dataset.days); selection=null; data=null; serial++; loading=false; refresh(); }
    if(button.dataset.session!==undefined) { selection={session:data.sessions[Number(button.dataset.session)]}; limit=40; render(); document.querySelector('#usage-clear')?.scrollIntoView({block:'nearest'}); }
    if(button.dataset.hour!==undefined) { selection={hour:Number(button.dataset.hour)}; limit=40; render(); }
    if(button.dataset.device) { selection={device:button.dataset.device}; limit=40; render(); }
    if(button.id==='usage-clear') { selection=null; render(); }
    if(button.id==='usage-more') { limit+=40; render(); }
  });
  page.addEventListener('change',event=>{ if(event.target.id==='usage-host') { host=event.target.value; selection=null; data=null; serial++; loading=false; refresh(); } });
  I18N.onChange(render);
  setInterval(()=>{if(visible) refresh();},30000);
  setInterval(()=>{if(visible && (data?.jobs??[]).some(j=>j.status==='running')) refresh();},1500);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&visible) refresh();});
  window.ComputeWatch={show,syncNav};
  if (location.hash === '#compute-watch') show(true);
})();
