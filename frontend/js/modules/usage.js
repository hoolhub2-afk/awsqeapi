function usageEl(id) {
  return document.getElementById(id);
}

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  }[c] || c));
}

function toNumber(v) {
  if (v === null || v === undefined) return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

function usagePercent(used, limit) {
  if (used == null || limit == null || limit <= 0) return 0;
  return Math.min(100, Math.round((used / limit) * 100));
}

function setUsageLoading(show) {
  usageEl('usage_loading').classList.toggle('hidden', !show);
}

function setUsageError(msg) {
  const el = usageEl('usage_error');
  if (!msg) { el.classList.add('hidden'); el.textContent = ''; return; }
  el.textContent = msg;
  el.classList.remove('hidden');
}

function setUsageEmpty(show) {
  usageEl('usage_empty').classList.toggle('hidden', !show);
}

function formatTs(ts) {
  if (!ts) return '--';
  const d = new Date(ts);
  return Number.isNaN(d.getTime()) ? '--' : d.toLocaleString();
}

function usageMetaText(data) {
  const count = Array.isArray(data?.accounts) ? data.accounts.length : 0;
  return `更新时间: ${formatTs(data?.timestamp)} · ${count} 个账号`;
}

function usageBarHtml(used, limit) {
  const pct = usagePercent(used, limit);
  const cls = pct >= 90 ? 'danger' : (pct >= 70 ? 'warning' : 'normal');
  return `<div class="usage-bar ${cls}"><div class="usage-bar-fill" style="width:${pct}%"></div></div>`;
}

function usageBreakdownRow(b) {
  const name = escapeHtml(b.displayName || b.resourceType || 'unknown');
  const cur = toNumber(b.currentUsageWithPrecision ?? b.currentUsage);
  const lim = toNumber(b.usageLimitWithPrecision ?? b.usageLimit);
  const text = (cur == null || lim == null) ? '--' : `${cur} / ${lim}`;
  return `<div class="usage-row"><div class="usage-row-name">${name}</div><div class="usage-row-value">${text}</div>${usageBarHtml(cur, lim)}</div>`;
}

function usageBreakdownHtml(list) {
  if (!list || list.length === 0) return '<div class="text-muted text-sm">无用量明细</div>';
  return `<div class="usage-breakdown">${list.map(usageBreakdownRow).join('')}</div>`;
}

function usageStatusBadge(item) {
  const status = item.status || 'error';
  const ok = status === 'ok';
  const exhausted = status === 'exhausted';
  const cls = exhausted ? 'badge-warning' : (ok ? 'badge-success' : 'badge-danger');
  const text = exhausted ? '配额耗尽' : (ok ? '正常' : '异常');
  return `<span class="badge ${cls}">${text}</span>`;
}

function usageSummaryHtml(item) {
  const used = toNumber(item.usedCount);
  const limit = toNumber(item.limitCount);
  const text = (used == null || limit == null) ? '--' : `${used} / ${limit}`;
  return `<div class="usage-summary">${usageBarHtml(used, limit)}<div class="mono">${text}</div></div>`;
}

function usageCardHtml(item) {
  const label = escapeHtml(item.label || item.accountId || '-');
  const acc = escapeHtml(item.accountId || '-');
  const error = item.error ? `<div class="alert alert-danger">${escapeHtml(item.error)}</div>` : '';
  return `<div class="card"><div class="row"><span class="chip mono">${label}</span><span class="text-muted text-sm">${acc}</span><span class="right">${usageStatusBadge(item)}</span></div>${usageSummaryHtml(item)}${error}${usageBreakdownHtml(item.usageBreakdown || [])}</div>`;
}

function renderUsage(data) {
  const list = Array.isArray(data?.accounts) ? data.accounts : [];
  usageEl('usage_meta').textContent = usageMetaText(data);
  if (list.length === 0) { setUsageEmpty(true); usageEl('usage_list').innerHTML = ''; return; }
  setUsageEmpty(false);
  usageEl('usage_list').innerHTML = list.map(usageCardHtml).join('');
}

async function fetchUsage(refresh) {
  const url = api('/v2/usage' + (refresh ? '?refresh=true' : ''));
  const r = await authFetch(url);
  if (!r.ok) throw new Error(await r.text());
  return await r.json();
}

async function loadUsage(refresh = false) {
  setUsageError('');
  setUsageLoading(true);
  setUsageEmpty(false);
  usageEl('usage_list').innerHTML = '';
  try {
    const data = await fetchUsage(refresh);
    renderUsage(data);
  } catch (e) {
    setUsageError(e.message || '加载失败');
  } finally {
    setUsageLoading(false);
  }
}
