'use strict';
const $ = (selector) => document.querySelector(selector);
const GiB = 1024 ** 3;
const MiB = 1024 ** 2;
const t = (key, params) => I18N.t(key, params);
const gpuFilterOptions = [['mine-only', 'mine'], ['idle-only', 'idle'], ['in-use-only', 'in-use']];
const state = { data: null, server: 'all', gpuFilter: 'all', sort: 'index', search: '', failed: false, detail: null };
const esc = (value) => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (value, decimals = 0) => Number.isFinite(value) ? value.toFixed(decimals) : '—';
const gib = (value) => Number.isFinite(value) ? (value / GiB).toFixed(1) : '—';
const pct = (value) => Number.isFinite(value) ? Math.round(value * 100) : null;
const sum = (items, field) => items.reduce((total, item) => total + (item[field] ?? 0), 0);
const clockTime = (seconds) => new Date(seconds * 1000).toLocaleTimeString(I18N.timeLocale(), {hour12: false});
const ownUsers = () => state.data?.own_users ?? [];
const fresh = (server) => !state.failed && server.status === 'online' && Date.now() / 1000 - server.last_success <= (state.data?.stale_after_seconds ?? 12);
const idle = (gpu) => gpu.processes_available && gpu.process_count === 0 && gpu.memory_used_bytes != null && gpu.memory_used_bytes <= 512 * MiB && gpu.utilization_gpu != null && gpu.utilization_gpu <= .1;
const inUse = (gpu) => (gpu.processes_available && gpu.process_count > 0) || (gpu.memory_used_bytes != null && gpu.memory_used_bytes > 512 * MiB) || (gpu.utilization_gpu != null && gpu.utilization_gpu > .1);
const mine = (gpu) => gpu.processes?.some(proc => ownUsers().includes(proc.user)) ?? false;
const selectedServers = () => (state.data?.servers ?? []).filter(s => state.server === 'all' || state.server === s.name);
function matches(gpu) {
  return state.gpuFilter === 'all' ||
    (state.gpuFilter === 'mine' && mine(gpu)) ||
    (state.gpuFilter === 'idle' && idle(gpu)) ||
    (state.gpuFilter === 'in-use' && inUse(gpu));
}
function gpuStatus(gpu, server) {
  if (!fresh(server)) return { key: 'offline', label: t('status.offline') };
  if (idle(gpu)) return { key: 'idle', label: t('status.idle') };
  if ((gpu.utilization_gpu ?? 0) >= .7) return { key: 'busy', label: t('status.busy') };
  if (!gpu.processes_available || (!inUse(gpu) && gpu.utilization_gpu == null)) return { key: 'unknown', label: t('status.unknown') };
  return { key: 'occupied', label: t('status.occupied') };
}
function sortGpus(gpus) {
  return [...gpus].sort((a, b) => state.sort === 'free' ? (b.memory_free_bytes ?? -1) - (a.memory_free_bytes ?? -1) || a.index - b.index : state.sort === 'util' ? (a.utilization_gpu ?? 2) - (b.utilization_gpu ?? 2) || a.index - b.index : a.index - b.index);
}
function elapsed(seconds) {
  if (!Number.isFinite(seconds)) return '—';
  const days = Math.floor(seconds / 86400), hours = Math.floor(seconds % 86400 / 3600), minutes = Math.floor(seconds % 3600 / 60);
  return days ? `${days}d ${hours}h` : hours ? `${hours}h ${minutes}m` : `${minutes}m`;
}
const serverError = (server) => (server.error_code && I18N.has(`err.${server.error_code}`)) ? t(`err.${server.error_code}`) : (server.error || t('err.collect_error'));
const serverWarning = (server) => (server.warning_code && I18N.has(`warn.${server.warning_code}`)) ? t(`warn.${server.warning_code}`) : (server.warning || '');
function sparkline(gpu, large = false) {
  const width = large ? 720 : 260, height = large ? 130 : 34;
  const end = Date.now() / 1000, windowSeconds = state.data.history_seconds;
  const pathFor = (field, scale) => {
    let started = false, previousAt = null;
    return (gpu.history ?? []).map(point => {
      const value = point[field];
      if (!Number.isFinite(value) || point.at < end - windowSeconds) { started = false; return ''; }
      const x = Math.max(0, Math.min(width, (point.at - end + windowSeconds) / windowSeconds * width));
      const y = height - 3 - Math.max(0, Math.min(1, value / scale)) * (height - 6);
      const gap = previousAt !== null && point.at - previousAt > state.data.interval_seconds * 3;
      const command = started && !gap ? 'L' : 'M'; started = true; previousAt = point.at;
      return `${command}${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(' ');
  };
  return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none" class="sparkline ${large ? 'large' : ''}" role="img" aria-label="${esc(t('card.trend_aria', {minutes: Math.round(windowSeconds / 60)}))}"><path class="chart-grid" d="M0 ${height - 3}H${width}${large ? ` M0 ${height / 2}H${width} M0 3H${width}` : ''}"/><path class="memory-line" d="${pathFor('memory_used_bytes', gpu.memory_total_bytes || 1)}"/><path class="util-line" d="${pathFor('utilization', 1)}"/></svg>`;
}
function renderStats(servers) {
  const online = servers.filter(fresh), gpus = online.flatMap(s => s.gpus);
  $('#stat-gpus').textContent = gpus.length;
  $('#stat-hosts').textContent = t('stats.hosts', {online: online.length, total: servers.length});
  $('#nav-total').textContent = (state.data?.servers ?? []).filter(fresh).reduce((n, s) => n + s.gpus.length, 0);
  $('#stat-idle').textContent = gpus.filter(idle).length;
  $('#stat-free').textContent = gib(sum(gpus, 'memory_free_bytes'));
  $('#stat-total-memory').textContent = t('stats.total_mem', {value: gib(sum(gpus, 'memory_total_bytes'))});
  const known = gpus.filter(g => g.utilization_gpu != null);
  $('#stat-util').textContent = known.length ? num(sum(known, 'utilization_gpu') / known.length * 100) : '—';
  const latest = Math.max(...online.map(s => s.last_success));
  $('#stat-updated').textContent = online.length ? t('stats.updated', {time: clockTime(latest)}) : t('stats.waiting');
  const allOnline = state.data.servers.every(fresh);
  $('#live-badge').className = `live-badge ${allOnline ? '' : 'degraded'}`;
  $('#live-badge').innerHTML = `<i></i>${esc(allOnline ? t('badge.live') : state.failed ? t('badge.failed') : t('badge.degraded'))}`;
}
function renderNav() {
  $('#server-nav').innerHTML = state.data.servers.map(server => `<button class="nav-item ${state.server === server.name ? 'selected' : ''}" data-server="${esc(server.name)}"><span class="host-status ${fresh(server) ? 'online' : 'offline'}"></span><span>${esc(server.name)}<small>${server.gpus.length || '—'} × ${esc(server.gpus[0]?.name.replace('NVIDIA GeForce ', '') || 'GPU')}</small></span><span class="nav-arrow">›</span></button>`).join('');
  document.querySelector('[data-server="all"]').classList.toggle('selected', state.server === 'all');
  $('#breadcrumb-title').textContent = state.server === 'all' ? t('topbar.overview') : state.server;
}
function utilizationSummary(gpu) {
  const window = gpu.utilization_window;
  if (gpu.utilization_status === 'unsupported' || gpu.utilization_status === 'unavailable') {
    return `<div class="util-window muted">${esc(t(`card.util_${gpu.utilization_status}`))}</div>`;
  }
  if (gpu.utilization_history_status === 'unsupported') {
    return `<div class="util-window muted">${esc(t('card.util_history_unsupported'))}</div>`;
  }
  if (gpu.utilization_source === 'nvml_samples' && window && window.mean == null) {
    return `<div class="util-window muted">${esc(t('card.util_warming'))}</div>`;
  }
  if (gpu.utilization_source !== 'nvml_samples' || !window || window.mean == null) {
    return `<div class="util-window muted">${esc(t('card.util_snapshot'))}</div>`;
  }
  return `<div class="util-window" title="${esc(t('card.util_coverage_hint'))}"><span>${esc(t('card.util_mean', {value: num(window.mean * 100, 1)}))}</span><span>${esc(t('card.util_peak', {value: num(pct(window.peak))}))}</span><span>${esc(t('card.util_coverage', {value: num(window.coverage * 100)}))}</span></div>`;
}
function gpuCard(gpu, server) {
  const status = gpuStatus(gpu, server), usedPct = gpu.memory_total_bytes ? gpu.memory_used_bytes / gpu.memory_total_bytes * 100 : 0;
  const minutes = Math.round(state.data.history_seconds / 60);
  return `<article class="gpu-card ${status.key}" data-host="${esc(server.name)}" data-gpu="${gpu.index}">
    <div class="card-header"><button class="card-title" data-detail="${gpu.index}" data-host="${esc(server.name)}"><span>GPU</span><strong>${String(gpu.index).padStart(2,'0')}</strong></button><span class="status-tag ${status.key}"><i></i>${status.label}</span></div>
    <div class="card-model">${esc(gpu.name.replace('NVIDIA GeForce ', ''))}<span aria-hidden="true">↗</span></div>
    <div class="card-primary"><span>${esc(t('card.free_mem'))}</span><div><strong>${gib(gpu.memory_free_bytes)}</strong><small>GiB</small></div></div>
    <div class="memory-heading"><span>${esc(t('card.mem_used'))}</span><span><b>${gib(gpu.memory_used_bytes)}</b> / ${gib(gpu.memory_total_bytes)} GiB</span></div>
    <div class="memory-track"><span style="width:${Math.max(0, Math.min(100, usedPct))}%"></span></div>
    <div class="card-metrics"><div><span title="${esc(t('card.util_latest'))}">${esc(t('card.util'))}</span><strong>${num(pct(gpu.utilization_gpu))}<small>%</small></strong></div><div><span>${esc(t('card.temp'))}</span><strong>${num(gpu.temperature_c)}<small>°C</small></strong></div><div><span>${esc(t('card.power'))}</span><strong>${num(gpu.power_watts)}<small>W</small></strong></div></div>
    ${utilizationSummary(gpu)}
    ${sparkline(gpu)}
    <div class="card-bottom"><span>${esc(t('card.trend', {minutes}))}</span><span>${gpu.processes_available ? esc(t('card.processes', {count: num(gpu.process_count)})) : esc(t('card.processes_unknown'))} <span class="card-chevron">→</span></span></div>
  </article>`;
}
function renderGroups(servers) {
  let total = 0;
  $('#server-groups').innerHTML = servers.map(server => {
    const online = fresh(server), visible = sortGpus(server.gpus.filter(gpu => online ? matches(gpu) : state.gpuFilter === 'all'));
    total += online ? visible.length : 0;
    const chip = online ? t('group.connected') : server.status === 'connecting' ? t('group.connecting') : t('group.error');
    const summary = online ? `${visible.length} / ${server.gpus.length} GPU <span>·</span> ${esc(t('group.free', {value: gib(sum(server.gpus, 'memory_free_bytes'))}))}` : `${esc(t('group.last_sample', {time: server.last_success ? clockTime(server.last_success) : t('group.none')}))}`;
    const warning = serverWarning(server);
    return `<section class="server-group"><div class="host-heading"><div class="host-name"><span class="host-status ${online ? 'online' : 'offline'}"></span><h3>${esc(server.name)}</h3><span class="host-chip">${esc(chip)}</span></div><div class="host-summary">${summary}</div></div>
      ${!online ? `<div class="host-error">${esc(serverError(server))} · ${esc(t('group.stale_note'))}</div>` : ''}
      ${warning && online ? `<div class="host-warning">${esc(warning)}</div>` : ''}
      <div class="gpu-grid">${visible.map(gpu => gpuCard(gpu, server)).join('')}</div>
      ${online && !visible.length ? `<div class="empty-filter">${esc(t('group.empty_filter'))}</div>` : ''}</section>`;
  }).join('');
  $('#result-count').textContent = total;
}
function processRows(servers) {
  return servers.flatMap(server => server.gpus.flatMap(gpu => gpu.processes.map(proc => ({server,gpu,proc}))));
}
function renderProcesses(servers) {
  const rows = processRows(servers).filter(({server,gpu,proc}) => `${server.name} ${gpu.index} ${proc.user ?? ''} ${proc.pid} ${proc.command ?? ''} ${proc.name}`.toLowerCase().includes(state.search.toLowerCase()));
  $('#process-count').textContent = rows.length;
  $('#process-body').innerHTML = rows.map(({server,gpu,proc}) => `<tr class="${fresh(server) ? '' : 'stale-row'}"><td><span class="table-host">${esc(server.name)}</span><span class="gpu-number">${gpu.index}</span>${fresh(server) ? '' : `<small> ${esc(t('table.stale'))}</small>`}</td><td><span class="user-tag ${ownUsers().includes(proc.user) ? 'self' : ''}">${esc(proc.user)}</span></td><td class="mono muted">${proc.pid}</td><td class="process-command" title="${esc(proc.command ?? proc.name)}"><strong>${esc(proc.name.split('/').pop())}</strong><small>${esc(proc.command ?? t('table.no_perm'))}</small></td><td class="mono">${Number.isFinite(proc.memory_bytes) ? num(proc.memory_bytes / MiB) + ' MiB' : '—'}</td><td class="mono muted">${num(proc.cpu_percent,1)}%</td><td class="mono muted">${elapsed(proc.elapsed_seconds)}</td></tr>`).join('') || `<tr><td colspan="7" class="empty-row">${esc(t('table.empty'))}</td></tr>`;
}
function renderDetail() {
  if (!state.detail) return;
  const server = state.data.servers.find(s => s.name === state.detail.host), gpu = server?.gpus.find(g => g.index === state.detail.index);
  if (!gpu) { $('#dialog-body').textContent = t('detail.unavailable'); return; }
  const status = gpuStatus(gpu,server);
  const minutes = Math.round(state.data.history_seconds / 60);
  $('#dialog-title').textContent = `${server.name} · GPU ${gpu.index}`;
  $('#dialog-body').innerHTML = `<div class="detail-subtitle">${esc(gpu.name)} <span class="status-tag ${status.key}">${status.label}</span></div><div class="detail-stats"><div><span>${esc(t('detail.free_mem'))}</span><strong>${gib(gpu.memory_free_bytes)} <small>GiB</small></strong></div><div><span>${esc(t('detail.util'))}</span><strong>${num(pct(gpu.utilization_gpu))}<small>%</small></strong></div><div><span>${esc(t('detail.temp_power'))}</span><strong>${num(gpu.temperature_c)}<small>°C / ${num(gpu.power_watts)} W</small></strong></div></div>
    ${utilizationSummary(gpu)}
    <div class="detail-chart"><div><h3>${esc(t('detail.trend_title'))}</h3><span class="chart-legend" title="${esc(t('detail.util_partial_hint'))}"><span><i></i>${esc(t('detail.legend_util'))}</span><span><i class="memory"></i>${esc(t('detail.legend_mem'))}</span></span></div>${sparkline(gpu,true)}<div class="chart-labels"><span>${esc(t('detail.ago', {minutes}))}</span><span>${esc(t('detail.now'))}</span></div></div>
    <dl class="gpu-info"><dt>${esc(t('detail.uuid'))}</dt><dd class="mono">${esc(gpu.uuid)}</dd><dt>${esc(t('detail.driver'))}</dt><dd>${esc(gpu.driver_version)} / ${esc(gpu.cuda_version)}</dd><dt>${esc(t('detail.mem'))}</dt><dd>${gib(gpu.memory_used_bytes)} / ${gib(gpu.memory_total_bytes)} GiB</dd><dt>${esc(t('detail.mem_util'))}</dt><dd>${num(pct(gpu.utilization_memory))}%</dd><dt>${esc(t('detail.fan'))}</dt><dd>${num(pct(gpu.fan_ratio))}% / ${num(gpu.power_limit_watts)} W</dd></dl>
    <div class="detail-processes"><h3>${gpu.processes_available ? esc(t('detail.processes', {count: num(gpu.process_count)})) : esc(t('detail.processes_unknown'))}</h3>${gpu.processes.map(proc => `<div><span>${esc(proc.user)} <code>PID ${proc.pid}</code> <b>${Number.isFinite(proc.memory_bytes) ? num(proc.memory_bytes / MiB) + ' MiB' : '—'}</b></span><p>${esc(proc.command ?? proc.name)}</p></div>`).join('') || `<p>${esc(t('detail.no_processes'))}</p>`}</div>
    <div class="detail-copy"><p>${esc(t('detail.copy_hint_pre'))}<code>ssh ${esc(server.ssh_host)}</code>${esc(t('detail.copy_hint_post'))}</p><button class="copy-code" id="copy-detail" ${fresh(server) ? '' : 'disabled'}><code>CUDA_VISIBLE_DEVICES=${gpu.index} python your_script.py</code><span>⧉</span></button><small>${esc(t('detail.copy_note'))}</small></div>`;
}
function render() {
  if (!state.data) return;
  const servers = selectedServers();
  $('#mine-filter').hidden = !ownUsers().length;
  $('#mine-filter').title = ownUsers().length ? '' : t('filters.mine_hint');
  if (!ownUsers().length && state.gpuFilter === 'mine') { state.gpuFilter = 'all'; $('#mine-only').checked = false; }
  renderNav(); renderStats(servers); renderGroups(servers); renderProcesses(servers); renderDetail();
  $('#footer-interval').textContent = t('footer.interval', {seconds: state.data.interval_seconds, minutes: num(state.data.history_seconds / 60)});
}
let inFlight = false;
async function fetchStatus() {
  if (inFlight) return;
  inFlight = true; $('#refresh').classList.add('refreshing');
  try {
    const response = await fetch('/api/status', {cache:'no-store', signal:AbortSignal.timeout(7000)});
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json(); state.failed = false; $('#connection-error').hidden = true;
  } catch (error) {
    state.failed = true;
    $('#connection-error').textContent = t('error.service_unreachable');
    $('#connection-error').hidden = false;
    if (!state.data) $('#server-groups').innerHTML = `<div class="empty-filter">${esc(t('group.waiting_service'))}</div>`;
  } finally { inFlight = false; $('#refresh').classList.remove('refreshing'); render(); }
}
let toastTimer;
async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    $('#toast').textContent = t('toast.copied');
  } catch {
    $('#toast').textContent = t('toast.manual', {value});
  }
  $('#toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').hidden = true, 3500);
}
document.addEventListener('click', event => {
  const nav = event.target.closest('[data-server]');
  if (nav) { state.server = nav.dataset.server; render(); }
  const card = event.target.closest('.gpu-card');
  if (card) { state.detail = {host:card.dataset.host,index:Number(card.dataset.gpu)}; renderDetail(); if (!$('#gpu-dialog').open) $('#gpu-dialog').showModal(); }
  if (event.target.closest('#copy-detail') && state.detail) copyText(`CUDA_VISIBLE_DEVICES=${state.detail.index} python your_script.py`);
});
for (const [id, filter] of gpuFilterOptions) {
  $(`#${id}`).addEventListener('change', event => {
    state.gpuFilter = event.target.checked ? filter : 'all';
    for (const [otherId, otherFilter] of gpuFilterOptions) {
      $(`#${otherId}`).checked = state.gpuFilter === otherFilter;
    }
    render();
  });
}
$('#sort').addEventListener('change', event => { state.sort = event.target.value; render(); });
$('#process-search').addEventListener('input', event => { state.search = event.target.value; if (state.data) renderProcesses(selectedServers()); });
$('#refresh').addEventListener('click', fetchStatus);
$('#dialog-close').addEventListener('click', () => $('#gpu-dialog').close());
$('#gpu-dialog').addEventListener('click', event => { if (event.target === $('#gpu-dialog')) $('#gpu-dialog').close(); });
$('#gpu-dialog').addEventListener('close', () => { state.detail = null; });
I18N.onChange(() => render());
setInterval(() => { $('#clock').textContent = new Date().toLocaleTimeString(I18N.timeLocale(), {hour12:false}); if (state.data?.servers.some(s => s.status === 'online' && !fresh(s))) render(); }, 1000);
setInterval(fetchStatus, 3000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) fetchStatus(); });
fetchStatus();
