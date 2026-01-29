// 账号状态管理
async function loadAccountStatus() {
  const container = document.getElementById('account-status-list');
  const summary = document.getElementById('status-summary');

  try {
    container.innerHTML = '<div class="flex flex-col items-center justify-center p-8"><span class="spinner"></span><div class="mt-2 text-muted">正在检查账号状态...</div></div>';
    summary.innerHTML = '';

    const response = await authFetch(api('/v2/accounts/status'));
    if (!response.ok) throw new Error(await response.text());

    const data = await response.json();

    summary.innerHTML = `
      <div class="badge badge-success">正常: ${data.summary.active}</div>
      <div class="badge badge-secondary">禁用: ${data.summary.disabled}</div>
      <div class="badge badge-danger">异常: ${data.summary.error}</div>
      <div class="badge badge-warning">未知: ${data.summary.unknown}</div>
      <div class="text-muted ml-2 text-sm">总计: ${data.total}</div>
    `;

    if (data.accounts.length === 0) {
      container.innerHTML = '<div class="empty-state"><div class="empty-state-icon">📭</div><div>暂无账号</div></div>';
      return;
    }

    container.innerHTML = `<div class="grid-container">${data.accounts.map(account => createStatusCard(account)).join('')}</div>`;
    Toast.success('账号状态已更新');
  } catch (e) {
    container.innerHTML = `<div class="empty-state text-danger"><i class="fa-solid fa-circle-exclamation"></i><div>加载失败: ${e.message}</div></div>`;
    Toast.error('加载账号状态失败: ' + e.message);
  }
}

function createStatusCard(account) {
  const statusClass = getStatusClass(account.status);
  const statusText = getStatusText(account.status);

  // 使用 escapeHTML 防止 XSS 攻击
  const safeLabel = typeof escapeHTML === 'function' ? escapeHTML(account.label || account.id) : (account.label || account.id);
  const safeStatusMsg = typeof escapeHTML === 'function' ? escapeHTML(account.status_message) : account.status_message;
  const safeId = typeof escapeHTML === 'function' ? escapeHTML(account.id) : account.id;

  let lastRefreshTime = '从未刷新';
  if (account.last_refresh_time) {
    try {
      let date = typeof account.last_refresh_time === 'string'
        ? new Date(account.last_refresh_time)
        : new Date(account.last_refresh_time * 1000);
      if (!isNaN(date.getTime())) lastRefreshTime = date.toLocaleString('zh-CN');
    } catch { lastRefreshTime = account.last_refresh_time.toString(); }
  }

  // Map status class to color
  const colorMap = {
    'active': 'success',
    'disabled': 'secondary',
    'error': 'danger',
    'unknown': 'warning'
  };
  const color = colorMap[statusClass] || 'secondary';

  return `
    <div class="card status-card hover-pulse" id="status-card-${safeId}" style="border-top: 3px solid var(--color-${color});">
      <div class="flex justify-between items-center mb-3">
        <div class="font-bold text-lg truncate" style="max-width: 120px;" title="${safeLabel}">${safeLabel.substring(0, 8)}</div>
        <span class="badge badge-${color}">${statusText}</span>
      </div>

      <div class="status-details text-sm space-y-2">
        <div class="flex justify-between">
          <span class="text-muted">状态信息:</span>
          <span id="status-msg-${safeId}" class="text-right truncate" style="max-width: 150px;" title="${safeStatusMsg}">${safeStatusMsg}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-muted">启用:</span>
          <span>${account.enabled ? '<i class="fa-solid fa-check text-success"></i>' : '<i class="fa-solid fa-xmark text-danger"></i>'}</span>
        </div>
        <div class="flex justify-between">
          <span class="text-muted">成功/错误:</span>
          <span><span class="text-success">${account.success_count}</span> / <span class="text-danger">${account.error_count}</span></span>
        </div>
        <div class="flex justify-between">
          <span class="text-muted">最后刷新:</span>
          <span class="text-xs" title="${lastRefreshTime}">${lastRefreshTime.split(' ')[0]}</span>
        </div>
      </div>

      <div class="mt-4 pt-3 border-t border-color flex justify-end">
        <button class="btn btn-sm btn-primary w-full" onclick="checkAccountRealStatus('${safeId}')">
          <i class="fa-solid fa-stethoscope"></i> 检测状态
        </button>
      </div>

      <div id="check-result-${safeId}" class="hidden mt-3 p-3 rounded text-sm fade-in"></div>
    </div>
  `;
}

async function checkAccountRealStatus(accountId) {
  const resultDiv = document.getElementById(`check-result-${accountId}`);
  const statusMsg = document.getElementById(`status-msg-${accountId}`);
  const card = document.getElementById(`status-card-${accountId}`);

  resultDiv.classList.remove('hidden');
  resultDiv.style.background = 'rgba(59,130,246,.1)';
  resultDiv.style.border = '1px solid rgba(59,130,246,.3)';
  resultDiv.innerHTML = '<div class="flex items-center gap-2"><span class="spinner-sm"></span> <span>正在检测...</span></div>';

  try {
    const response = await authFetch(api(`/v2/accounts/${accountId}/check`), { method: 'POST' });
    if (!response.ok) throw new Error(await response.text());

    const result = await response.json();
    let bgColor, borderColor, icon, textColor, statusColor;

    switch(result.status) {
      case 'success':
        statusColor = 'success';
        bgColor = 'rgba(16,185,129,.15)'; borderColor = 'rgba(16,185,129,.5)'; textColor = 'var(--color-success)'; icon = '✅';
        break;
      case 'quota_exhausted':
      case 'suspended':
        statusColor = 'danger';
        bgColor = 'rgba(239,68,68,.15)'; borderColor = 'rgba(239,68,68,.5)'; textColor = 'var(--color-danger)'; icon = '⛔';
        break;
      case 'unauthorized':
      case 'token_error':
        statusColor = 'warning';
        bgColor = 'rgba(245,158,11,.15)'; borderColor = 'rgba(245,158,11,.5)'; textColor = 'var(--color-warning)'; icon = '⚠️';
        break;
      case 'timeout':
      case 'network_error':
        statusColor = 'warning';
        bgColor = 'rgba(245,158,11,.1)'; borderColor = 'rgba(245,158,11,.3)'; textColor = 'var(--color-warning)'; icon = '⏱';
        break;
      default:
        statusColor = 'secondary';
        bgColor = 'rgba(156,163,175,.1)'; borderColor = 'rgba(156,163,175,.3)'; textColor = 'var(--text-muted)'; icon = '❓';
    }

    // Update card border color
    card.style.borderTopColor = `var(--color-${statusColor})`;
    
    resultDiv.style.background = bgColor;
    resultDiv.style.border = `1px solid ${borderColor}`;

    let html = `<div style="color:${textColor};font-weight:600;margin-bottom:4px;display:flex;align-items:center;gap:6px;">`;
    const safeMessage = typeof escapeHTML === 'function' ? escapeHTML(result.message) : result.message;
    html += `<span>${icon}</span><span>${safeMessage}</span></div>`;
    html += `<div class="text-xs text-muted">`;
    html += `延迟: ${result.latency_ms}ms`;
    if (result.detail) {
      const safeDetail = typeof escapeHTML === 'function' ? escapeHTML(result.detail.substring(0, 100)) : result.detail.substring(0, 100);
      html += `<br><span style="color:${statusColor === 'danger' ? 'var(--color-danger)' : 'inherit'};">详情: ${safeDetail}${result.detail.length > 100 ? '...' : ''}</span>`;
    }
    html += `</div>`;

    resultDiv.innerHTML = html;
    statusMsg.textContent = result.message;

    if (result.status === 'success') Toast.success(`账号检测正常`);
    else Toast.error(`${result.message}`);
  } catch(e) {
    resultDiv.style.background = 'rgba(239,68,68,.15)';
    resultDiv.style.border = '1px solid rgba(239,68,68,.5)';
    const safeErrorMsg = typeof escapeHTML === 'function' ? escapeHTML(e.message) : e.message;
    resultDiv.innerHTML = `<div class="text-danger flex items-center gap-2"><i class="fa-solid fa-circle-xmark"></i> <span>检测失败: ${safeErrorMsg}</span></div>`;
    Toast.error('检测失败: ' + e.message);
  }
}

// 一键检测所有账号状态
async function checkAllAccountsStatus() {
  const container = document.getElementById('account-status-list');
  const cards = container?.querySelectorAll('.status-card');

  if (!cards || cards.length === 0) {
    Toast.warning('没有可检测的账号');
    return;
  }

  // 获取所有账号ID
  const accountIds = [];
  cards.forEach(card => {
    const id = card.id?.replace('status-card-', '');
    if (id) accountIds.push(id);
  });

  if (accountIds.length === 0) {
    Toast.warning('没有可检测的账号');
    return;
  }

  Toast.info(`开始检测 ${accountIds.length} 个账号...`);

  let successCount = 0;
  let errorCount = 0;

  // 并发检测，但限制并发数为 3
  const concurrency = 3;
  for (let i = 0; i < accountIds.length; i += concurrency) {
    const batch = accountIds.slice(i, i + concurrency);
    const results = await Promise.allSettled(
      batch.map(id => checkAccountRealStatus(id).then(() => 'success').catch(() => 'error'))
    );

    results.forEach(r => {
      if (r.status === 'fulfilled' && r.value === 'success') successCount++;
      else errorCount++;
    });
  }

  Toast.success(`检测完成：${successCount} 成功，${errorCount} 失败`);
}

function getStatusClass(status) {
  switch (status) {
    case 'active': return 'active';
    case 'disabled': return 'disabled';
    case 'no_token': case 'error_limit': case 'refresh_failed': case 'check_error':
    case 'quota_exhausted': case 'suspended': case 'unauthorized': return 'error';
    case 'rate_limited': case 'timeout': case 'network_error': return 'unknown';
    default: return 'unknown';
  }
}

function getStatusText(status) {
  const texts = {
    'active': '正常', 'disabled': '禁用', 'no_token': '无令牌', 'error_limit': '错误过多',
    'refresh_failed': '刷新失败', 'check_error': '检查失败', 'quota_exhausted': '配额耗尽',
    'suspended': '账号封禁', 'unauthorized': '认证失败', 'rate_limited': '频率限制',
    'timeout': '连接超时', 'network_error': '网络错误', 'stale': '可能过期'
  };
  return texts[status] || '未知';
}

// 初始化
window.addEventListener('DOMContentLoaded', async () => {
  Toast.init();
  Logger.init();
  restoreChatInputs();

  // Sidebar collapse/expand
  const appContainer = document.querySelector('.app-container');
  const sidebarToggle = document.querySelector('.sidebar-toggle');
  const storageKey = 'sidebar_collapsed';
  const applyCollapsed = (collapsed) => {
    if (!appContainer) return;
    appContainer.classList.toggle('sidebar-collapsed', collapsed);
    if (sidebarToggle) {
      sidebarToggle.setAttribute('aria-pressed', collapsed ? 'true' : 'false');
      sidebarToggle.title = collapsed ? '展开侧边栏' : '折叠侧边栏';
    }

    // When collapsed, add tooltips for nav buttons
    document.querySelectorAll('.nav-item').forEach((btn) => {
      const label = btn.textContent.trim();
      if (collapsed) {
        if (!btn.getAttribute('title')) btn.setAttribute('title', label);
      } else {
        if (btn.getAttribute('title') === label) btn.removeAttribute('title');
      }
    });
  };

  try {
    const collapsed = localStorage.getItem(storageKey) === '1';
    applyCollapsed(collapsed);
  } catch {}

  if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
      const next = !appContainer?.classList.contains('sidebar-collapsed');
      applyCollapsed(next);
      try { localStorage.setItem(storageKey, next ? '1' : '0'); } catch {}
    });
  }

  // Add event listener for mobile menu toggle if it exists
  const menuBtn = document.querySelector('.mobile-menu-btn');
  if (menuBtn) {
    menuBtn.addEventListener('click', () => {
      document.querySelector('.sidebar').classList.toggle('open');
    });
  }

  // Close sidebar when clicking outside on mobile
  document.addEventListener('click', (e) => {
    const sidebar = document.querySelector('.sidebar');
    const menuBtn = document.querySelector('.mobile-menu-btn');
    if (window.innerWidth <= 768 && sidebar && sidebar.classList.contains('open') &&
        !sidebar.contains(e.target) && menuBtn && !menuBtn.contains(e.target)) {
      sidebar.classList.remove('open');
    }
  });

  if (await checkAuth()) {
    loadAccounts();
    // Preload status monitoring so the tab shows data immediately.
    loadAccountStatus();
  }
});
