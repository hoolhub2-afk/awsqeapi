// 账号管理模块
let accountsData = [];
let autoDetectTimer = null;
const AUTO_DETECT_INTERVAL = 30 * 60 * 1000; // 30分钟

function createAccountCard(acc) {
  const card = document.createElement('div');
  card.className = 'card';
  card.dataset.accountId = acc.id;

  const header = document.createElement('div');
  header.style.cssText = 'display:flex;align-items:center;gap:8px;margin-bottom:12px;flex-wrap:wrap;';

  const name = document.createElement('strong');
  name.textContent = acc.label || '(无标签)';
  name.style.fontSize = '15px';

  const id = document.createElement('div');
  id.className = 'mono';
  id.style.cssText = 'font-size:11px;color:var(--text-muted);';
  id.textContent = acc.id.substring(0, 8) + '...';

  const successCount = acc.success_count ?? 0;
  const errorCount = acc.error_count ?? 0;
  const quotaExhausted = acc.quota_exhausted ?? 0;
  let statusChip = document.createElement('div');
  statusChip.id = `status-chip-${acc.id}`;

  if (quotaExhausted || acc.last_refresh_status === 'quota_exhausted') {
    statusChip.className = 'chip chip-danger';
    statusChip.textContent = '🚫 配额耗尽';
  } else if (acc.last_refresh_status === 'suspended') {
    statusChip.className = 'chip chip-danger';
    statusChip.textContent = '⛔ 已封禁';
  } else if (!acc.enabled) {
    statusChip.className = 'chip chip-danger';
    statusChip.textContent = '🔴 已禁用';
  } else if (acc.last_refresh_status === 'failed') {
    statusChip.className = 'chip chip-danger';
    statusChip.textContent = '🔴 ⚠ 刷新失败';
  } else if (acc.last_refresh_status === 'unauthorized') {
    statusChip.className = 'chip chip-warning';
    statusChip.textContent = '⚠️ Token失效';
  } else if (acc.last_refresh_status === 'timeout' || acc.last_refresh_status === 'network_error') {
    statusChip.className = 'chip chip-warning';
    statusChip.textContent = '⏱️ 网络异常';
  } else if (errorCount > 10 && successCount > 0) {
    statusChip.className = 'chip chip-warning';
    statusChip.textContent = `🟡 ⚠ 错误 ${errorCount}`;
  } else if (successCount > 0) {
    statusChip.className = 'chip chip-success';
    statusChip.textContent = `🟢 ✓ 正常 (${successCount})`;
  } else {
    statusChip.className = 'chip chip-info';
    statusChip.textContent = '🔵 ⏳ 未使用';
  }

  const spacer = document.createElement('div');
  spacer.style.flex = '1';

  const toggle = document.createElement('label');
  toggle.className = 'switch';
  const chk = document.createElement('input');
  chk.type = 'checkbox';
  chk.id = `account-enabled-${acc.id}`;
  chk.name = chk.id;
  chk.setAttribute('aria-label', `启用账号 ${acc.label || acc.id}`);
  chk.checked = !!acc.enabled;
  chk.onchange = async () => {
    const oldValue = !chk.checked;
    try {
      await updateAccount(acc.id, { enabled: chk.checked });
      Toast.success(`账号已${chk.checked ? '启用' : '禁用'}`);
    } catch(e) {
      chk.checked = oldValue;
    }
  };
  const slider = document.createElement('span');
  slider.className = 'slider';
  toggle.appendChild(chk);
  toggle.appendChild(slider);

  header.appendChild(name);
  header.appendChild(id);
  header.appendChild(statusChip);
  header.appendChild(spacer);
  header.appendChild(toggle);
  card.appendChild(header);

  const statsRow = document.createElement('div');
  statsRow.className = 'kvs';
  statsRow.style.cssText = 'font-size:13px;margin-bottom:12px;';

  function row(k, v, vStyle, vId) {
    const kEl = document.createElement('div');
    kEl.className = 'kvs-key';
    kEl.textContent = k;
    const vEl = document.createElement('div');
    vEl.className = 'kvs-value';
    vEl.textContent = v ?? '-';
    if (vStyle) Object.assign(vEl.style, vStyle);
    if (vId) vEl.id = vId;
    statsRow.appendChild(kEl);
    statsRow.appendChild(vEl);
  }

  const total = successCount + errorCount;
  let healthText = '未知';
  let healthStyle = { color: 'var(--text-muted)' };

  if (total > 0) {
    const healthPercent = Math.round((successCount / total) * 100);
    if (healthPercent >= 80) {
      healthText = `${healthPercent}% 🟢`;
      healthStyle = { color: 'var(--success)', fontWeight: '600' };
    } else if (healthPercent >= 50) {
      healthText = `${healthPercent}% 🟡`;
      healthStyle = { color: 'var(--warning)', fontWeight: '600' };
    } else if (healthPercent >= 1) {
      healthText = `${healthPercent}% 🟠`;
      healthStyle = { color: '#ff8c00', fontWeight: '600' };
    } else {
      healthText = `${healthPercent}%`;
      healthStyle = { color: 'var(--text-muted)' };
    }
  }

  row('健康度', healthText, healthStyle);
  row('成功/错误', `${successCount} / ${errorCount}`);

  let refreshStatus = '⏳ 从未刷新';
  if (acc.last_refresh_status === 'missing_credentials') {
    refreshStatus = '⚠️ 凭证缺失';
  } else if (acc.last_refresh_status === 'success') {
    refreshStatus = '✅ 成功';
  } else if (acc.last_refresh_status === 'failed') {
    refreshStatus = '❌ 失败';
  } else if (acc.last_refresh_status === 'suspended') {
    refreshStatus = '⛔ 账号封禁';
  } else if (acc.last_refresh_status === 'quota_exhausted') {
    refreshStatus = '🚫 配额耗尽';
  } else if (acc.last_refresh_status === 'unauthorized') {
    refreshStatus = '⚠️ Token失效';
  } else if (acc.last_refresh_status === 'timeout') {
    refreshStatus = '⏱️ 请求超时';
  } else if (acc.last_refresh_status === 'network_error') {
    refreshStatus = '🌐 网络错误';
  } else if (acc.last_refresh_status === 'unknown') {
    refreshStatus = '❓ 状态未知';
  } else if (!acc.clientId || !acc.clientSecret) {
    if (!acc.refreshToken && !acc.accessToken) {
      refreshStatus = '⚠️ 凭证缺失';
    }
  }
  row('刷新状态', refreshStatus, null, `refresh-status-${acc.id}`);
  row('刷新时间', acc.last_refresh_time ? acc.last_refresh_time.replace('T', ' ').substring(0, 19) : '-');
  row('Client ID', acc.clientId || '-');
  row('Refresh Token', acc.refreshToken ? '已设置' : '-');
  row('Access Token', acc.accessToken ? '已设置' : '-');
  row('创建时间', acc.created_at ? acc.created_at.replace('T', ' ').substring(0, 19) : '-');
  row('更新时间', acc.updated_at ? acc.updated_at.replace('T', ' ').substring(0, 19) : '-');

  card.appendChild(statsRow);

  const realStatusDiv = document.createElement('div');
  realStatusDiv.id = `real-status-${acc.id}`;
  realStatusDiv.style.cssText = 'margin-top:10px;padding:10px 12px;border-radius:8px;display:none;';
  card.appendChild(realStatusDiv);

  const actions = document.createElement('div');
  actions.style.cssText = 'display:flex;gap:6px;margin-top:12px;align-items:center;';

  const labelField = document.createElement('input');
  labelField.type = 'text';
  labelField.className = 'form-control';
  labelField.id = `account-label-${acc.id}`;
  labelField.name = labelField.id;
  labelField.setAttribute('aria-label', '账号标签');
  labelField.placeholder = '标签';
  labelField.value = acc.label || '';
  labelField.style.cssText = 'flex:1;min-width:60px;font-size:13px;padding:6px 10px;';

  const saveBtn = document.createElement('button');
  saveBtn.className = 'btn-secondary btn-sm';
  saveBtn.textContent = '保存';
  saveBtn.style.cssText = 'white-space:nowrap;padding:6px 10px;font-size:12px;';
  saveBtn.onclick = async () => { await updateAccount(acc.id, { label: labelField.value }); };

  const refreshBtn = document.createElement('button');
  refreshBtn.className = 'btn-warn btn-sm';
  refreshBtn.textContent = '刷新';
  refreshBtn.style.cssText = 'white-space:nowrap;padding:6px 10px;font-size:12px;';
  refreshBtn.onclick = () => refreshAccount(acc.id);

  const checkBtn = document.createElement('button');
  checkBtn.className = 'btn-primary btn-sm';
  checkBtn.textContent = '检测';
  checkBtn.style.cssText = 'white-space:nowrap;padding:6px 10px;font-size:12px;';
  checkBtn.onclick = () => checkAccountStatus(acc.id);

  const delBtn = document.createElement('button');
  delBtn.className = 'btn-danger btn-sm';
  delBtn.textContent = '删除';
  delBtn.style.cssText = 'white-space:nowrap;padding:6px 10px;font-size:12px;';
  delBtn.onclick = () => deleteAccount(acc.id);

  actions.appendChild(labelField);
  actions.appendChild(saveBtn);
  actions.appendChild(refreshBtn);
  actions.appendChild(checkBtn);
  actions.appendChild(delBtn);
  card.appendChild(actions);

  return card;
}

function renderAccounts(list) {
  accountsData = list;
  const root = document.getElementById('accounts');

  if (!Array.isArray(list) || list.length === 0) {
    root.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📭</div><div>暂无账号</div></div>';
    return;
  }

  root.innerHTML = '';
  // Root is already a grid container in index.html; append cards directly for horizontal/grid layout.
  list.forEach(acc => root.appendChild(createAccountCard(acc)));
}

async function loadAccounts() {
  const loading = document.getElementById('accounts-loading');
  const text = document.getElementById('accounts-refresh-text');
  loading.style.display = 'inline-flex';
  text.style.display = 'none';

  try {
    const r = await authFetch(api('/v2/accounts'));
    const j = await r.json();
    renderAccounts(j);
    Toast.success(`已加载 ${j.length} 个账号`);
    Logger.success(`已载入 ${j.length} 个账号`, { total: j.length });
  } catch(e) {
    Toast.error('加载账号失败：' + e.message);
    Logger.error(`拉取账号失败: ${e.message}`);
  } finally {
    loading.style.display = 'none';
    text.style.display = 'inline';
  }
}

async function createAccount() {
  const body = {
    label: document.getElementById('new_label').value.trim() || null,
    clientId: document.getElementById('new_clientId').value.trim(),
    clientSecret: document.getElementById('new_clientSecret').value.trim(),
    refreshToken: document.getElementById('new_refreshToken').value.trim() || null,
    accessToken: document.getElementById('new_accessToken').value.trim() || null,
    enabled: document.getElementById('new_enabled').checked,
    other: (() => {
      const t = document.getElementById('new_other').value.trim();
      if (!t) return null;
      try { return JSON.parse(t); } catch { Toast.error('其他信息必须是有效的JSON格式'); throw new Error('invalid JSON'); }
    })()
  };

  if (!body.clientId || !body.clientSecret) {
    Toast.warning('Client ID 和 Client Secret 为必填项');
    return;
  }

  try {
    const r = await authFetch(api('/v2/accounts'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (!r.ok) throw new Error(await r.text());

    Toast.success('账号创建成功');
    Logger.success('创建账号成功', { label: body.label, clientId: body.clientId });

    document.getElementById('new_label').value = '';
    document.getElementById('new_clientId').value = '';
    document.getElementById('new_clientSecret').value = '';
    document.getElementById('new_refreshToken').value = '';
    document.getElementById('new_accessToken').value = '';
    document.getElementById('new_other').value = '';

    await loadAccounts();
    switchTab('accounts');
    document.querySelector('.tab[onclick*="accounts"]').click();
  } catch(e) {
    if (e.message !== 'invalid JSON') {
      Toast.error('创建账号失败：' + e.message);
      Logger.error('创建账号失败: ' + e.message);
    }
  }
}

async function deleteAccount(id) {
  const confirmed = await Modal.danger('确认删除该账号吗？此操作不可撤销。', '删除账号');
  if (!confirmed) return;

  try {
    const r = await authFetch(api('/v2/accounts/' + encodeURIComponent(id)), { method: 'DELETE' });
    if (!r.ok) throw new Error(await r.text());
    Toast.success('账号已删除');
    Logger.success('删除账号成功', { accountId: id });
    await loadAccounts();
  } catch(e) {
    Toast.error('删除账号失败：' + e.message);
    Logger.error('删除账号失败: ' + e.message, { accountId: id });
  }
}

async function deleteBannedAccounts() {
  const confirmed = await Modal.danger('确认删除所有已禁用的账号吗?此操作不可撤销。', '批量删除已禁用账号');
  if (!confirmed) return;

  try {
    const r = await authFetch(api('/v2/accounts/delete-banned'), { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const result = await r.json();
    Toast.success(`已删除 ${result.deleted_count} 个已禁用的账号`);
    await loadAccounts();
  } catch(e) {
    Toast.error('批量删除失败：' + e.message);
  }
}

async function updateAccount(id, patch) {
  const cleaned = { ...(patch || {}) };
  if (typeof cleaned.label === 'string') {
    cleaned.label = cleaned.label.trim();
    if (!cleaned.label) delete cleaned.label;
  }
  if (Object.keys(cleaned).length === 0) {
    Toast.warning('没有可更新内容');
    return;
  }

  try {
    const r = await authFetch(api('/v2/accounts/' + encodeURIComponent(id)), {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(cleaned)
    });
    if (!r.ok) throw new Error(await r.text());
    Toast.success('账号信息已更新');
    Logger.success('更新账号成功', { accountId: id, changes: cleaned });
    await loadAccounts();
  } catch(e) {
    Toast.error('更新账号失败：' + e.message);
    Logger.error('更新账号失败: ' + e.message, { accountId: id });
  }
}

async function refreshAccount(id) {
  try {
    const r = await authFetch(api('/v2/accounts/' + encodeURIComponent(id) + '/refresh'), { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    Toast.success('Token刷新成功');
    Logger.success('刷新Token成功', { accountId: id });
    await loadAccounts();
  } catch(e) {
    Toast.error('刷新Token失败：' + e.message);
    Logger.error('刷新Token失败: ' + e.message, { accountId: id });
  }
}

async function checkAccountStatus(accountId) {
  const statusDiv = document.getElementById(`real-status-${accountId}`);
  if (!statusDiv) return;

  statusDiv.style.display = 'block';
  statusDiv.style.background = 'rgba(59,130,246,.1)';
  statusDiv.style.border = '1px solid rgba(59,130,246,.3)';
  statusDiv.innerHTML = '<span style="color:var(--accent-secondary);">⏳ 正在检测账号真实状态...</span>';

  try {
    const response = await authFetch(api(`/v2/accounts/${accountId}/check`), { method: 'POST' });
    if (!response.ok) throw new Error(await response.text());

    const result = await response.json();
    let bgColor, borderColor, icon, textColor;

    switch(result.status) {
      case 'success':
        bgColor = 'rgba(16,185,129,.15)'; borderColor = 'rgba(16,185,129,.5)'; textColor = '#10b981'; icon = '✅'; break;
      case 'quota_exhausted':
      case 'suspended':
        bgColor = 'rgba(239,68,68,.2)'; borderColor = 'rgba(239,68,68,.6)'; textColor = '#ff6b6b'; icon = result.status === 'suspended' ? '⛔' : '🚫'; break;
      case 'unauthorized':
      case 'token_error':
        bgColor = 'rgba(245,158,11,.2)'; borderColor = 'rgba(245,158,11,.6)'; textColor = '#ffc107'; icon = '⚠️'; break;
      case 'timeout':
      case 'network_error':
        bgColor = 'rgba(245,158,11,.15)'; borderColor = 'rgba(245,158,11,.4)'; textColor = '#f59e0b'; icon = '⏱️'; break;
      default:
        bgColor = 'rgba(156,163,175,.15)'; borderColor = 'rgba(156,163,175,.4)'; textColor = '#9ca3af'; icon = '❓';
    }

    statusDiv.style.background = bgColor;
    statusDiv.style.border = `2px solid ${borderColor}`;

    const safeMessage = typeof escapeHTML === 'function' ? escapeHTML(result.message) : result.message;
    let html = `<div style="color:${textColor};font-weight:700;font-size:14px;margin-bottom:6px;">${icon} ${safeMessage}</div>`;
    html += `<div style="color:var(--text-secondary);font-size:12px;">检测时间: ${result.checked_at} | 延迟: ${result.latency_ms}ms`;
    if (result.detail) {
      const safeDetail = typeof escapeHTML === 'function' ? escapeHTML(result.detail.substring(0, 150)) : result.detail.substring(0, 150);
      html += `<br><span style="color:${result.status !== 'success' ? '#ff6b6b' : 'inherit'};">详情: ${safeDetail}${result.detail.length > 150 ? '...' : ''}</span>`;
    }
    html += `</div>`;
    statusDiv.innerHTML = html;

    const statusChip = document.getElementById(`status-chip-${accountId}`);
    const refreshStatusEl = document.getElementById(`refresh-status-${accountId}`);
    if (statusChip) {
      switch(result.status) {
        case 'success':
          statusChip.className = 'chip chip-success'; statusChip.textContent = '🟢 ✓ 正常';
          if (refreshStatusEl) refreshStatusEl.textContent = '✅ 账号正常'; break;
        case 'quota_exhausted':
          statusChip.className = 'chip chip-danger'; statusChip.textContent = '🚫 配额耗尽';
          if (refreshStatusEl) refreshStatusEl.textContent = '🚫 配额耗尽'; break;
        case 'suspended':
          statusChip.className = 'chip chip-danger'; statusChip.textContent = '⛔ 已封禁';
          if (refreshStatusEl) refreshStatusEl.textContent = '⛔ 账号封禁'; break;
        case 'unauthorized':
        case 'token_error':
          statusChip.className = 'chip chip-warning'; statusChip.textContent = '⚠️ Token失效';
          if (refreshStatusEl) refreshStatusEl.textContent = '⚠️ Token失效'; break;
        case 'timeout':
        case 'network_error':
          statusChip.className = 'chip chip-warning'; statusChip.textContent = '⏱️ 网络异常';
          if (refreshStatusEl) refreshStatusEl.textContent = '⏱️ 网络异常'; break;
        default:
          statusChip.className = 'chip chip-info'; statusChip.textContent = '❓ 未知';
          if (refreshStatusEl) refreshStatusEl.textContent = '❓ 状态未知';
      }
    }

    if (result.status === 'success') Toast.success(`账号检测正常 (${result.latency_ms}ms)`);
    else Toast.error(`${result.message}`);
  } catch(e) {
    statusDiv.style.background = 'rgba(239,68,68,.15)';
    statusDiv.style.border = '2px solid rgba(239,68,68,.5)';
    const safeErrorMsg = typeof escapeHTML === 'function' ? escapeHTML(e.message) : e.message;
    statusDiv.innerHTML = `<span style="color:#ff6b6b;font-weight:600;">❌ 检测失败: ${safeErrorMsg}</span>`;
    Toast.error('检测失败: ' + e.message);

    const statusChip = document.getElementById(`status-chip-${accountId}`);
    const refreshStatusEl = document.getElementById(`refresh-status-${accountId}`);
    if (statusChip) { statusChip.className = 'chip chip-danger'; statusChip.textContent = '❌ 检测失败'; }
    if (refreshStatusEl) refreshStatusEl.textContent = '❌ 检测失败';
  }
}

// 自动检测问题账号（封禁、配额耗尽等）
async function autoDetectProblemAccounts() {
  const resultDiv = document.getElementById('problem-accounts-result');
  if (!resultDiv) return;

  // 筛选需要检测的账号：已启用但状态异常或未知的账号
  const candidateAccounts = accountsData.filter(acc => {
    if (!acc.enabled) return false;
    // 检测这些状态的账号
    const needCheck = [
      'quota_exhausted', 'suspended', 'failed', 'unauthorized',
      'timeout', 'network_error', 'unknown'
    ];
    // 未使用过的账号也需要检测
    const neverUsed = (acc.success_count ?? 0) === 0 && (acc.error_count ?? 0) === 0;
    // 错误率高的账号
    const highErrorRate = (acc.error_count ?? 0) > 5;
    return needCheck.includes(acc.last_refresh_status) || neverUsed || highErrorRate;
  });

  if (candidateAccounts.length === 0) {
    resultDiv.style.display = 'block';
    resultDiv.innerHTML = `
      <div class="alert" style="background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.4);">
        <i class="fa-solid fa-circle-check text-success"></i>
        <span>所有启用的账号状态正常，无需检测</span>
      </div>
    `;
    Toast.success('所有账号状态正常');
    setTimeout(() => { resultDiv.style.display = 'none'; }, 5000);
    return;
  }

  resultDiv.style.display = 'block';
  resultDiv.innerHTML = `
    <div class="alert" style="background:rgba(59,130,246,.1);border:1px solid rgba(59,130,246,.3);">
      <div class="flex items-center gap-2">
        <span class="spinner-sm"></span>
        <span>正在检测 <strong>${candidateAccounts.length}</strong> 个可疑账号...</span>
      </div>
      <div id="detect-progress" class="mt-2 text-sm text-muted"></div>
    </div>
  `;

  const progressDiv = document.getElementById('detect-progress');
  const problems = { suspended: [], quota_exhausted: [], unauthorized: [], error: [], normal: [] };
  let checked = 0;

  // 并发检测，限制并发数为 3
  const concurrency = 3;
  for (let i = 0; i < candidateAccounts.length; i += concurrency) {
    const batch = candidateAccounts.slice(i, i + concurrency);
    const results = await Promise.allSettled(
      batch.map(async (acc) => {
        try {
          const r = await authFetch(api(`/v2/accounts/${acc.id}/check`), { method: 'POST' });
          if (!r.ok) throw new Error(await r.text());
          const result = await r.json();
          return { acc, result };
        } catch (e) {
          return { acc, result: { status: 'error', message: e.message } };
        }
      })
    );

    results.forEach(r => {
      checked++;
      if (r.status === 'fulfilled') {
        const { acc, result } = r.value;
        const label = acc.label || acc.id.substring(0, 8);
        switch (result.status) {
          case 'suspended':
            problems.suspended.push({ label, id: acc.id, message: result.message });
            break;
          case 'quota_exhausted':
            problems.quota_exhausted.push({ label, id: acc.id, message: result.message });
            break;
          case 'unauthorized':
          case 'token_error':
            problems.unauthorized.push({ label, id: acc.id, message: result.message });
            break;
          case 'success':
            problems.normal.push({ label, id: acc.id });
            break;
          default:
            problems.error.push({ label, id: acc.id, message: result.message });
        }
      }
    });

    if (progressDiv) {
      progressDiv.textContent = `已检测 ${checked}/${candidateAccounts.length}`;
    }
  }

  // 显示检测结果
  const totalProblems = problems.suspended.length + problems.quota_exhausted.length + problems.unauthorized.length;

  let html = '<div class="space-y-2">';

  if (totalProblems === 0) {
    html += `
      <div class="alert" style="background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.4);">
        <i class="fa-solid fa-circle-check text-success"></i>
        <span>检测完成，所有账号状态正常</span>
        <span class="text-muted ml-2">(${problems.normal.length} 个正常)</span>
      </div>
    `;
  } else {
    html += `
      <div class="alert" style="background:rgba(239,68,68,.15);border:1px solid rgba(239,68,68,.4);">
        <i class="fa-solid fa-triangle-exclamation text-danger"></i>
        <span>检测完成，发现 <strong class="text-danger">${totalProblems}</strong> 个问题账号</span>
      </div>
    `;

    if (problems.suspended.length > 0) {
      html += `
        <div class="card" style="padding:12px;border-left:3px solid var(--color-danger);">
          <div class="flex items-center gap-2 mb-2">
            <span class="badge badge-danger">⛔ 已封禁 (${problems.suspended.length})</span>
          </div>
          <div class="text-sm space-y-1">
            ${problems.suspended.map(p => `
              <div class="flex items-center gap-2">
                <span class="mono">${escapeHTML(p.label)}</span>
                <button class="btn btn-danger btn-sm" style="padding:2px 8px;font-size:11px;" onclick="deleteAccount('${p.id}')">删除</button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    if (problems.quota_exhausted.length > 0) {
      html += `
        <div class="card" style="padding:12px;border-left:3px solid var(--color-warning);">
          <div class="flex items-center gap-2 mb-2">
            <span class="badge badge-warning">🚫 配额耗尽 (${problems.quota_exhausted.length})</span>
          </div>
          <div class="text-sm space-y-1">
            ${problems.quota_exhausted.map(p => `
              <div class="flex items-center gap-2">
                <span class="mono">${escapeHTML(p.label)}</span>
                <button class="btn btn-secondary btn-sm" style="padding:2px 8px;font-size:11px;" onclick="updateAccount('${p.id}', {enabled:false})">禁用</button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }

    if (problems.unauthorized.length > 0) {
      html += `
        <div class="card" style="padding:12px;border-left:3px solid var(--color-warning);">
          <div class="flex items-center gap-2 mb-2">
            <span class="badge badge-warning">⚠️ Token失效 (${problems.unauthorized.length})</span>
          </div>
          <div class="text-sm space-y-1">
            ${problems.unauthorized.map(p => `
              <div class="flex items-center gap-2">
                <span class="mono">${escapeHTML(p.label)}</span>
                <button class="btn btn-warn btn-sm" style="padding:2px 8px;font-size:11px;" onclick="refreshAccount('${p.id}')">刷新</button>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    }
  }

  html += `
    <div class="flex gap-2 mt-3">
      <button class="btn btn-secondary btn-sm" onclick="document.getElementById('problem-accounts-result').style.display='none'">
        <i class="fa-solid fa-xmark"></i> 关闭
      </button>
      ${problems.suspended.length > 0 ? `
        <button class="btn btn-danger btn-sm" onclick="deleteBannedAccounts()">
          <i class="fa-solid fa-trash-can"></i> 一键清理封禁账号
        </button>
      ` : ''}
    </div>
  `;

  html += '</div>';
  resultDiv.innerHTML = html;

  // 刷新账号列表
  await loadAccounts();

  if (totalProblems > 0) {
    Toast.warning(`发现 ${totalProblems} 个问题账号`);
  } else {
    Toast.success('所有账号状态正常');
  }
}

// 定时自动检测并清理封禁账号
function toggleAutoDetect(enabled) {
  const statusDiv = document.getElementById('auto-detect-status');
  const toggle = document.getElementById('auto-detect-toggle');

  // 保存设置到 localStorage
  localStorage.setItem('auto_detect_enabled', enabled ? 'true' : 'false');

  if (enabled) {
    // 启动定时检测
    startAutoDetect();
    if (statusDiv) {
      statusDiv.style.display = 'block';
      updateAutoDetectStatus('已启用自动清理，每30分钟检测一次');
    }
    Toast.success('已开启自动清理，每30分钟检测并删除封禁账号');
  } else {
    // 停止定时检测
    stopAutoDetect();
    if (statusDiv) {
      statusDiv.style.display = 'none';
    }
    Toast.info('已关闭自动清理');
  }

  if (toggle) toggle.checked = enabled;
}

function startAutoDetect() {
  stopAutoDetect();

  // 立即执行一次
  runAutoDetectAndClean();

  // 设置定时器
  autoDetectTimer = setInterval(runAutoDetectAndClean, AUTO_DETECT_INTERVAL);
}

function stopAutoDetect() {
  if (autoDetectTimer) {
    clearInterval(autoDetectTimer);
    autoDetectTimer = null;
  }
}

function updateAutoDetectStatus(message, type = 'info') {
  const statusDiv = document.getElementById('auto-detect-status');
  if (!statusDiv) return;

  const colors = {
    info: { bg: 'rgba(59,130,246,.1)', border: 'rgba(59,130,246,.3)', icon: 'fa-circle-info', iconColor: 'text-info' },
    success: { bg: 'rgba(16,185,129,.15)', border: 'rgba(16,185,129,.4)', icon: 'fa-circle-check', iconColor: 'text-success' },
    warning: { bg: 'rgba(245,158,11,.15)', border: 'rgba(245,158,11,.4)', icon: 'fa-triangle-exclamation', iconColor: 'text-warning' },
    danger: { bg: 'rgba(239,68,68,.15)', border: 'rgba(239,68,68,.4)', icon: 'fa-circle-xmark', iconColor: 'text-danger' }
  };

  const c = colors[type] || colors.info;
  const now = new Date().toLocaleTimeString('zh-CN');

  statusDiv.innerHTML = `
    <div class="alert flex items-center gap-2" style="background:${c.bg};border:1px solid ${c.border};">
      <i class="fa-solid ${c.icon} ${c.iconColor}"></i>
      <span>${message}</span>
      <span class="text-muted text-sm ml-2">${now}</span>
      <div class="flex-1"></div>
      <span class="text-muted text-sm">下次检测: <span id="next-detect-countdown">30:00</span></span>
    </div>
  `;

  // 启动倒计时
  startCountdown();
}

let countdownTimer = null;
let countdownSeconds = 1800; // 30分钟

function startCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  countdownSeconds = 1800;

  countdownTimer = setInterval(() => {
    countdownSeconds--;
    const el = document.getElementById('next-detect-countdown');
    if (el && countdownSeconds >= 0) {
      const min = Math.floor(countdownSeconds / 60);
      const sec = countdownSeconds % 60;
      el.textContent = `${min}:${sec.toString().padStart(2, '0')}`;
    }
    if (countdownSeconds <= 0) {
      clearInterval(countdownTimer);
    }
  }, 1000);
}

async function runAutoDetectAndClean() {
  updateAutoDetectStatus('正在检测封禁账号...', 'info');

  // 获取所有启用的账号
  const enabledAccounts = accountsData.filter(acc => acc.enabled);

  if (enabledAccounts.length === 0) {
    updateAutoDetectStatus('没有启用的账号需要检测', 'info');
    return;
  }

  const suspendedAccounts = [];
  const quotaExhaustedAccounts = [];

  // 并发检测，限制并发数为 3
  const concurrency = 3;
  for (let i = 0; i < enabledAccounts.length; i += concurrency) {
    const batch = enabledAccounts.slice(i, i + concurrency);
    const results = await Promise.allSettled(
      batch.map(async (acc) => {
        try {
          const r = await authFetch(api(`/v2/accounts/${acc.id}/check`), { method: 'POST' });
          if (!r.ok) throw new Error(await r.text());
          const result = await r.json();
          return { acc, result };
        } catch (e) {
          return { acc, result: { status: 'error', message: e.message } };
        }
      })
    );

    results.forEach(r => {
      if (r.status === 'fulfilled') {
        const { acc, result } = r.value;
        if (result.status === 'suspended') {
          suspendedAccounts.push(acc);
        } else if (result.status === 'quota_exhausted') {
          quotaExhaustedAccounts.push(acc);
        }
      }
    });
  }

  // 自动删除封禁账号
  let deletedCount = 0;
  for (const acc of suspendedAccounts) {
    try {
      const r = await authFetch(api('/v2/accounts/' + encodeURIComponent(acc.id)), { method: 'DELETE' });
      if (r.ok) {
        deletedCount++;
        Logger.warn(`自动清理: 删除封禁账号 ${acc.label || acc.id}`);
      }
    } catch (e) {
      Logger.error(`自动清理: 删除账号失败 ${acc.id}: ${e.message}`);
    }
  }

  // 自动禁用配额耗尽账号
  let disabledCount = 0;
  for (const acc of quotaExhaustedAccounts) {
    try {
      const r = await authFetch(api('/v2/accounts/' + encodeURIComponent(acc.id)), {
        method: 'PATCH',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ enabled: false })
      });
      if (r.ok) {
        disabledCount++;
        Logger.warn(`自动清理: 禁用配额耗尽账号 ${acc.label || acc.id}`);
      }
    } catch (e) {
      Logger.error(`自动清理: 禁用账号失败 ${acc.id}: ${e.message}`);
    }
  }

  // 更新状态显示
  if (deletedCount > 0 || disabledCount > 0) {
    const msgs = [];
    if (deletedCount > 0) msgs.push(`删除 ${deletedCount} 个封禁账号`);
    if (disabledCount > 0) msgs.push(`禁用 ${disabledCount} 个配额耗尽账号`);
    updateAutoDetectStatus(`自动清理完成: ${msgs.join('，')}`, 'warning');
    Toast.warning(`自动清理: ${msgs.join('，')}`);
    // 刷新账号列表
    await loadAccounts();
  } else {
    updateAutoDetectStatus(`检测完成，${enabledAccounts.length} 个账号状态正常`, 'success');
  }
}

// 页面加载时恢复自动检测设置
function restoreAutoDetectSetting() {
  const enabled = localStorage.getItem('auto_detect_enabled') === 'true';
  const toggle = document.getElementById('auto-detect-toggle');
  if (toggle) toggle.checked = enabled;
  if (enabled) {
    toggleAutoDetect(true);
  }
}

// 在 DOMContentLoaded 后调用
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', restoreAutoDetectSetting);
} else {
  setTimeout(restoreAutoDetectSetting, 100);
}
