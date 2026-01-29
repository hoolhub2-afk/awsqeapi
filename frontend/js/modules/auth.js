// URL登录授权模块
let currentAuth = null;
let authPolling = null;

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, (c) => {
    switch (c) {
      case '&': return '&amp;';
      case '<': return '&lt;';
      case '>': return '&gt;';
      case '"': return '&quot;';
      case "'": return '&#39;';
      default: return c;
    }
  });
}

async function startAuth() {
  const body = {
    label: document.getElementById('auth_label').value.trim() || null,
    enabled: document.getElementById('auth_enabled').checked
  };

  const infoEl = document.getElementById('auth_info');
  infoEl.innerHTML = '<div class="loading-state"><span class="loading-spinner"></span><span>正在请求授权服务器...</span></div>';

  try {
    const r = await authFetch(api('/v2/auth/start'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (!r.ok) throw new Error(await r.text());

    const j = await r.json();
    currentAuth = j;

    const userCode = j.userCode || '----';
    const authId = j.authId || '';
    const expiresIn = j.expiresIn ?? '-';
    const verifyUrl = j.verificationUriComplete || '';

    const safeUserCode = escapeHtml(userCode);
    const safeAuthId = escapeHtml(authId);
    const safeExpires = escapeHtml(expiresIn);
    const safeVerifyUrl = escapeHtml(verifyUrl);

    const authHtml = `
      <div class="auth-card">
        <div class="auth-top">
          <div class="auth-title">
            <div class="auth-icon" aria-hidden="true"><i class="fa-solid fa-lock"></i></div>
            <div>
              <div class="auth-heading">设备授权流程</div>
              <div class="auth-subtitle">将在新窗口完成验证，系统会自动创建账号</div>
            </div>
          </div>
          <div class="auth-badges">
            <span class="badge badge-secondary"><span class="mono">Auth</span>: <span class="mono">${safeAuthId || '-'}</span></span>
            <span class="badge badge-warning">有效期: ${safeExpires}s</span>
          </div>
        </div>

        <div class="auth-layout">
          <div class="auth-left">
            <div class="auth-timeline" role="list">
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">1</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">打开授权页面</div>
                  <div class="auth-step-desc">点击右侧按钮在新窗口打开</div>
                </div>
              </div>
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">2</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">输入用户代码</div>
                  <div class="auth-step-desc">在授权页面输入：<span class="mono highlight">${safeUserCode}</span></div>
                </div>
              </div>
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">3</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">等待自动完成</div>
                  <div class="auth-step-desc">完成后将自动创建账号并跳转提示</div>
                </div>
              </div>
            </div>
          </div>

          <div class="auth-right">
            <div class="auth-code-card">
              <div class="auth-code-label">用户代码</div>
              <div class="auth-code-pill mono" id="auth-user-code" aria-label="用户代码">${safeUserCode}</div>
              <div class="auth-actions auth-actions-2">
                <button class="btn btn-primary" type="button" data-action="open-auth" ${verifyUrl ? '' : 'disabled'}>
                  <i class="fa-solid fa-external-link-alt" aria-hidden="true"></i>
                  <span>打开授权页面</span>
                </button>
                <button class="btn btn-secondary" type="button" data-action="copy-code">
                  <i class="fa-regular fa-copy" aria-hidden="true"></i>
                  <span>复制用户代码</span>
                </button>
              </div>
              <div class="auth-hint">
                <span class="text-muted">无法自动打开？</span>
                <a class="auth-link" href="${safeVerifyUrl}" target="_blank" rel="noopener noreferrer">点击这里手动打开</a>
              </div>
            </div>

            <div class="auth-status" id="polling-status">
              <span class="spinner-sm" aria-hidden="true"></span>
              <span>正在等待授权...</span>
            </div>
          </div>
        </div>
      </div>
    `;

    infoEl.innerHTML = authHtml;

    const openBtn = infoEl.querySelector('[data-action="open-auth"]');
    if (openBtn && verifyUrl) {
      openBtn.addEventListener('click', () => {
        try { window.open(verifyUrl, '_blank'); }
        catch { Toast.warning('无法自动打开窗口，请手动点击下方链接'); }
      });
    }

    const copyBtn = infoEl.querySelector('[data-action="copy-code"]');
    if (copyBtn) {
      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(userCode);
          Toast.success('已复制用户代码');
        } catch {
          Toast.warning('复制失败，请手动选择复制');
        }
      });
    }

    Toast.success('授权链接已生成，请在新窗口完成验证');
    Logger.success('已生成设备授权链接', { authId: j.authId, userCode: j.userCode });

    try { if (verifyUrl) window.open(verifyUrl, '_blank'); }
    catch(e) { Toast.warning('无法自动打开窗口，请手动点击按钮'); }

    startAuthPolling();
  } catch(e) {
    const safeErrorMsg = escapeHtml(e.message);
    infoEl.innerHTML = `<div class="alert alert-danger"><i class="fa-solid fa-circle-exclamation"></i> 启动失败：${safeErrorMsg}</div>`;
    Toast.error('启动授权失败：' + e.message);
    Logger.error('启动授权失败: ' + e.message);
  }
}

function startAuthPolling() {
  if (authPolling) clearInterval(authPolling);

  authPolling = setInterval(async () => {
    try {
      const r = await authFetch(api('/v2/auth/status/' + encodeURIComponent(currentAuth.authId)));
      if (!r.ok) { stopAuthPolling(); return; }

      const status = await r.json();
      const statusEl = document.getElementById('polling-status');

      if (status.status === 'completed') {
        stopAuthPolling();
        if (statusEl) statusEl.innerHTML = '<span class="text-success"><i class="fa-solid fa-check-circle"></i> 授权成功！正在创建账号...</span>';
        await claimAuth();
      } else if (status.status === 'timeout' || status.status === 'error') {
        stopAuthPolling();
        const safeError = escapeHtml(status.error || status.status);
        if (statusEl) statusEl.innerHTML = `<span class="text-danger"><i class="fa-solid fa-circle-xmark"></i> 授权失败: ${safeError}</span>`;
        Toast.error('授权失败');
      } else if (status.remaining <= 0) {
        stopAuthPolling();
        if (statusEl) statusEl.innerHTML = '<span class="text-warning"><i class="fa-solid fa-clock"></i> 授权超时，请重新开始</span>';
        Toast.warning('授权超时');
      }
    } catch(e) { Logger.warn('轮询授权状态失败', e.message); }
  }, 3000);
}

function stopAuthPolling() {
  if (authPolling) { clearInterval(authPolling); authPolling = null; }
}

async function claimAuth() {
  if (!currentAuth || !currentAuth.authId) {
    Toast.warning('请先点击"开始登录"按钮');
    return;
  }

  stopAuthPolling();
  const infoEl = document.getElementById('auth_info');
  
  try {
    const r = await authFetch(api('/v2/auth/claim/' + encodeURIComponent(currentAuth.authId)), { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());

    const j = await r.json();
    const status = j?.status;
    if (status === 'pending') {
      const statusEl = document.getElementById('polling-status');
      if (statusEl) statusEl.innerHTML = '<span class="spinner-sm" aria-hidden="true"></span><span>仍在等待授权...</span>';
      Toast.info('授权尚未完成，继续等待...');
      startAuthPolling();
      return;
    }
    if (status && status !== 'completed') {
      const statusEl = document.getElementById('polling-status');
      const safeErrorMsg = escapeHtml(j?.error || status);
      if (statusEl) statusEl.innerHTML = `<span class="text-danger">❌ 授权失败: ${safeErrorMsg}</span>`;
      Toast.error('创建账号失败：' + (j?.error || status));
      return;
    }

    const safeJson = escapeHtml(JSON.stringify(j, null, 2));

    infoEl.innerHTML = `
      <div class="success-banner">
        <div class="success-header"><i class="fa-solid fa-check-circle"></i> 账号创建成功</div>
        <div class="mono" style="font-size: 13px; margin-top: 10px;">${safeJson}</div>
        <div style="margin-top: 15px;">
          <button class="btn btn-primary btn-sm" onclick="switchTab('accounts')">前往账号列表</button>
        </div>
      </div>
    `;
    
    Toast.success('账号创建成功');
    Logger.success('设备授权完成', j);
    await loadAccounts();
    currentAuth = null;
  } catch(e) {
    const statusEl = document.getElementById('polling-status');
    const safeErrorMsg = escapeHtml(e.message);
    if (statusEl) statusEl.innerHTML = `<span class="text-danger">❌ 领取失败: ${safeErrorMsg}</span>`;
    Toast.error('创建账号失败：' + e.message);
    Logger.error('领取授权失败: ' + e.message, { authId: currentAuth?.authId });
  }
}

async function quickAddAccount() {
  const infoEl = document.getElementById('auth_info');
  infoEl.innerHTML = '<div class="loading-state"><span class="spinner"></span> 正在获取号池网站地址...</div>';
  Toast.info('正在新窗口打开号池网站');

  try {
    const r = await authFetch(api('/v2/auth/quick-add'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ label: null, enabled: true })
    });

    if (!r.ok) throw new Error(await r.text());

    const j = await r.json();

    // 验证 URL 安全性
    if (!isSafeURL(j.verificationUriComplete)) {
      throw new Error('返回的 URL 不安全');
    }

    window.open(j.verificationUriComplete, '_blank');
    const safeUrl = escapeHtml(j.verificationUriComplete);

    infoEl.innerHTML = `
      <div class="info-card">
        <h3><i class="fa-solid fa-bolt text-warning"></i> 号池快速添加</h3>
        <p>已在新窗口打开号池网站。请在号池网站上：</p>
        <ol>
          <li>登录您的账号</li>
          <li>授权本应用访问</li>
          <li>获取账号信息后，系统将自动添加</li>
        </ol>
        <div class="mt-4">
          <a href="${safeUrl}" target="_blank" class="btn btn-secondary btn-sm">重新打开链接</a>
        </div>
      </div>
    `;
  } catch(e) {
    const safeErrorMsg = escapeHtml(e.message);
    infoEl.innerHTML = `<div class="alert alert-danger">❌ 获取号池地址失败：${safeErrorMsg}</div>`;
    Toast.error('获取号池地址失败：' + e.message);
  }
}
