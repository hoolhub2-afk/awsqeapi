// Kiro 导入/认证模块 (仅支持 Builder ID)
let currentKiroAuth = null;
let kiroAuthPolling = null;

function getKiroFormValues() {
  return {
    label: (document.getElementById('kiro_label')?.value || '').trim() || null,
    region: (document.getElementById('kiro_region')?.value || '').trim() || null,
    startUrl: (document.getElementById('kiro_start_url')?.value || '').trim() || null,
    enabled: !!document.getElementById('kiro_enabled')?.checked
  };
}

function setKiroInfo(html) {
  const el = document.getElementById('kiro_info');
  if (el) el.innerHTML = html;
}

function stopKiroPolling() {
  if (kiroAuthPolling) {
    clearInterval(kiroAuthPolling);
    kiroAuthPolling = null;
  }
}

async function startKiroAuth(provider) {
  stopKiroPolling();
  const vals = getKiroFormValues();

  setKiroInfo('<div class="loading-state"><span class="loading-spinner"></span><span>正在请求 Kiro Builder ID 授权链接...</span></div>');

  try {
    const r = await authFetch(api('/v2/kiro/auth/start'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        label: vals.label,
        enabled: vals.enabled,
        region: vals.region,
        startUrl: vals.startUrl
      })
    });

    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();
    currentKiroAuth = j;

    const authUrl = j.authUrl || '';
    const authId = j.authId || '';
    const expiresIn = j.expiresIn ?? '-';
    const userCode = j.userCode || '';

    setKiroInfo(`
      <div class="auth-card">
        <div class="auth-top">
          <div class="auth-title">
            <div class="auth-icon" aria-hidden="true"><i class="fa-solid fa-shield"></i></div>
            <div>
              <div class="auth-heading">Kiro Builder ID Device Code</div>
              <div class="auth-subtitle">将在新窗口完成 AWS Builder ID 授权，完成后自动入库</div>
            </div>
          </div>
          <div class="auth-badges">
            <span class="badge badge-secondary"><span class="mono">Auth</span>: <span class="mono">${escapeHtml(authId || '-') }</span></span>
            <span class="badge badge-warning">有效期: ${escapeHtml(expiresIn)}s</span>
            ${userCode ? `<span class="badge badge-info"><span class="mono">Code</span>: <span class="mono">${escapeHtml(userCode)}</span></span>` : ''}
          </div>
        </div>
        <div class="auth-layout">
          <div class="auth-left">
            <div class="auth-timeline" role="list">
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">1</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">打开授权页面</div>
                  <div class="auth-step-desc">在新窗口完成 AWS Builder ID 授权</div>
                </div>
              </div>
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">2</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">授权并返回</div>
                  <div class="auth-step-desc">完成授权后保持页面打开，系统将自动创建账号</div>
                </div>
              </div>
              <div class="auth-step" role="listitem">
                <div class="auth-step-dot">3</div>
                <div class="auth-step-body">
                  <div class="auth-step-title">自动入库</div>
                  <div class="auth-step-desc">本页面会自动轮询并显示结果</div>
                </div>
              </div>
            </div>
          </div>
          <div class="auth-right">
            <div class="auth-code-card">
              <div class="auth-code-label">授权链接</div>
              <div class="code-block mono" style="font-size: 12px; word-break: break-all;">${escapeHtml(SensitiveDataMasker?.maskUrl?.(authUrl) || authUrl)}</div>
              ${userCode ? `
                <div class="auth-code-label" style="margin-top: 12px;">User Code</div>
                <div class="code-block mono">${escapeHtml(userCode)}</div>
              ` : ''}
              <div class="auth-actions auth-actions-2" style="margin-top: 12px;">
                <button class="btn btn-primary" type="button" data-action="open-kiro" ${authUrl ? '' : 'disabled'}>
                  <i class="fa-solid fa-external-link-alt" aria-hidden="true"></i>
                  <span>打开授权页面</span>
                </button>
                <button class="btn btn-secondary" type="button" data-action="copy-kiro" ${authUrl ? '' : 'disabled'}>
                  <i class="fa-regular fa-copy" aria-hidden="true"></i>
                  <span>复制链接</span>
                </button>
              </div>
              <div class="auth-hint">
                <span class="text-muted">无法自动打开？</span>
                <a class="auth-link" href="${escapeHtml(authUrl)}" target="_blank" rel="noopener noreferrer">点击这里手动打开</a>
              </div>
            </div>
            <div class="auth-status" id="kiro-polling-status">
              <span class="spinner-sm" aria-hidden="true"></span>
              <span>正在等待授权...</span>
            </div>
          </div>
        </div>
      </div>
    `);

    const mount = document.getElementById('kiro_info');
    const openBtn = mount?.querySelector('[data-action="open-kiro"]');
    if (openBtn && authUrl) {
      openBtn.addEventListener('click', () => {
        try { window.open(authUrl, '_blank'); }
        catch { Toast.warning('无法自动打开窗口，请手动点击下方链接'); }
      });
    }
    const copyBtn = mount?.querySelector('[data-action="copy-kiro"]');
    if (copyBtn && authUrl) {
      copyBtn.addEventListener('click', async () => {
        try {
          await navigator.clipboard.writeText(authUrl);
          Toast.success('已复制授权链接');
        } catch {
          Toast.warning('复制失败，请手动选择复制');
        }
      });
    }

    try { if (authUrl) window.open(authUrl, '_blank'); } catch {}

    Toast.success('已生成 Kiro Builder ID 授权链接，请在新窗口完成授权');
    startKiroPolling();
  } catch (e) {
    setKiroInfo(`<div class="alert alert-danger"><i class="fa-solid fa-circle-exclamation"></i> 启动失败：${escapeHtml(e.message)}</div>`);
    Toast.error('启动 Kiro 授权失败：' + e.message);
  }
}

function startKiroPolling() {
  stopKiroPolling();
  if (!currentKiroAuth?.authId) return;

  kiroAuthPolling = setInterval(async () => {
    try {
      const r = await authFetch(api('/v2/kiro/auth/status/' + encodeURIComponent(currentKiroAuth.authId)));
      if (!r.ok) return;
      const status = await r.json();
      const statusEl = document.getElementById('kiro-polling-status');

      if (status.status === 'completed') {
        stopKiroPolling();
        if (statusEl) statusEl.innerHTML = '<span class="text-success"><i class="fa-solid fa-check-circle"></i> 授权成功！正在获取账号...</span>';
        await claimKiroAuth();
      } else if (status.status === 'timeout' || status.status === 'error') {
        stopKiroPolling();
        if (statusEl) statusEl.innerHTML = `<span class="text-danger"><i class="fa-solid fa-circle-xmark"></i> 授权失败: ${escapeHtml(status.error || status.status)}</span>`;
        Toast.error('Kiro 授权失败');
      } else if (status.remaining <= 0) {
        stopKiroPolling();
        if (statusEl) statusEl.innerHTML = '<span class="text-warning"><i class="fa-solid fa-clock"></i> 授权超时，请重新开始</span>';
        Toast.warning('Kiro 授权超时');
      }
    } catch (e) { Logger.warn('轮询 Kiro 授权状态失败', e.message); }
  }, 3000);
}

async function claimKiroAuth() {
  if (!currentKiroAuth?.authId) return;

  try {
    const r = await authFetch(api('/v2/kiro/auth/claim/' + encodeURIComponent(currentKiroAuth.authId)), { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());
    const j = await r.json();

    setKiroInfo(`
      <div class="success-banner">
        <div class="success-header"><i class="fa-solid fa-check-circle"></i> Kiro 授权完成</div>
        <div class="mono" style="font-size: 13px; margin-top: 10px;">${escapeHtml(JSON.stringify(SensitiveDataMasker.maskObject(j), null, 2))}</div>
        <div style="margin-top: 15px; display: flex; gap: 8px;">
          <button class="btn btn-primary btn-sm" onclick="switchTab('accounts')">前往账号列表</button>
          <button class="btn btn-secondary btn-sm" onclick="loadAccounts()">刷新账号列表</button>
        </div>
      </div>
    `);

    Toast.success('Kiro 账号已创建');
    try { await loadAccounts(); } catch {}
    currentKiroAuth = null;
  } catch (e) {
    Toast.error('获取 Kiro 账号失败：' + e.message);
  }
}

function createCustomModal({ title, bodyHtml, footerHtml, maxWidthPx = 760 }) {
  const overlay = document.createElement('div');
  overlay.className = 'modal-overlay';
  overlay.innerHTML = `
    <div class="modal" style="max-width: ${maxWidthPx}px;">
      <div class="modal-header">
        <div class="modal-icon" style="font-size: 1.5rem;"><i class="fa-solid fa-circle-info text-info"></i></div>
        <div class="modal-title">${escapeHtml(title)}</div>
        <div class="flex-1"></div>
        <button class="btn btn-secondary btn-sm" type="button" data-action="close" aria-label="关闭">×</button>
      </div>
      <div class="modal-body">${bodyHtml}</div>
      <div class="modal-footer">${footerHtml}</div>
    </div>
  `;
  document.body.appendChild(overlay);

  const cleanup = () => {
    overlay.style.animation = 'fadeOut 0.2s ease-out forwards';
    const modalBox = overlay.querySelector('.modal');
    if (modalBox) modalBox.style.animation = 'scaleOut 0.2s ease-in forwards';
    setTimeout(() => overlay.remove(), 200);
  };

  overlay.addEventListener('click', (e) => {
    if (e.target === overlay) cleanup();
  });
  overlay.querySelector('[data-action="close"]')?.addEventListener('click', cleanup);

  return { overlay, cleanup };
}

function parseTokensFromText(text) {
  return (text || '')
    .split(/[\r\n\s,]+/g)
    .map(s => s.trim())
    .filter(Boolean);
}

function showKiroBatchImportModal() {
  const vals = getKiroFormValues();
  const { overlay, cleanup } = createCustomModal({
    title: '批量导入 Kiro refreshToken (Builder ID)',
    maxWidthPx: 820,
    bodyHtml: `
      <div class="alert alert-info">
        <div><strong>注意：</strong>批量导入需要提供 clientId 和 clientSecret 用于 Builder ID Token 刷新</div>
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div class="input-group">
          <label class="input-label" for="kiro_batch_client_id">Client ID <span class="text-danger">*</span></label>
          <input id="kiro_batch_client_id" class="form-control" placeholder="必填" />
        </div>
        <div class="input-group">
          <label class="input-label" for="kiro_batch_client_secret">Client Secret <span class="text-danger">*</span></label>
          <input id="kiro_batch_client_secret" class="form-control" placeholder="必填" />
        </div>
      </div>

      <div class="input-group">
        <label class="input-label" for="kiro_batch_tokens">refreshToken 列表（每行一条）</label>
        <textarea id="kiro_batch_tokens" class="form-control font-mono" rows="8" placeholder="aor...\n...\n..."></textarea>
        <div class="text-muted text-sm mt-2">支持换行 / 空格 / 逗号分隔</div>
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div class="input-group">
          <label class="input-label" for="kiro_batch_prefix">Label Prefix</label>
          <input id="kiro_batch_prefix" class="form-control" placeholder="Kiro" value="Kiro" />
        </div>
        <div class="input-group">
          <label class="input-label" for="kiro_batch_region">Region</label>
          <input id="kiro_batch_region" class="form-control" placeholder="us-east-1" value="${escapeHtml(vals.region || '')}" />
        </div>
      </div>

      <div class="flex justify-between items-center mt-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input id="kiro_batch_enabled" type="checkbox" class="form-checkbox" ${vals.enabled ? 'checked' : ''}/>
          <span>创建后启用</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input id="kiro_batch_skip_dup" type="checkbox" class="form-checkbox"/>
          <span>跳过去重检查</span>
        </label>
      </div>

      <div id="kiro_batch_result" class="mt-4"></div>
    `,
    footerHtml: `
      <button class="btn btn-secondary" type="button" data-action="cancel">取消</button>
      <button class="btn btn-primary" type="button" data-action="submit">开始导入</button>
    `
  });

  overlay.querySelector('[data-action="cancel"]')?.addEventListener('click', cleanup);

  overlay.querySelector('[data-action="submit"]')?.addEventListener('click', async () => {
    const clientId = overlay.querySelector('#kiro_batch_client_id')?.value?.trim() || '';
    const clientSecret = overlay.querySelector('#kiro_batch_client_secret')?.value?.trim() || '';
    const textarea = overlay.querySelector('#kiro_batch_tokens');
    const prefix = overlay.querySelector('#kiro_batch_prefix')?.value?.trim() || 'Kiro';
    const region = overlay.querySelector('#kiro_batch_region')?.value?.trim() || null;
    const enabled = !!overlay.querySelector('#kiro_batch_enabled')?.checked;
    const skipDuplicateCheck = !!overlay.querySelector('#kiro_batch_skip_dup')?.checked;
    const resultDiv = overlay.querySelector('#kiro_batch_result');

    if (!clientId || !clientSecret) {
      resultDiv.innerHTML = '<div class="alert alert-warning">请填写 Client ID 和 Client Secret</div>';
      return;
    }

    const tokens = parseTokensFromText(textarea?.value);
    if (!tokens.length) {
      resultDiv.innerHTML = '<div class="alert alert-warning">请粘贴至少 1 条 refreshToken</div>';
      return;
    }

    resultDiv.innerHTML = '<div class="loading-state"><span class="loading-spinner"></span><span>正在导入...</span></div>';

    try {
      const r = await authFetch(api('/v2/kiro/import/refresh-tokens'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          refreshTokens: tokens,
          clientId,
          clientSecret,
          labelPrefix: prefix,
          enabled,
          region,
          skipDuplicateCheck
        })
      });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      resultDiv.innerHTML = `
        <div class="alert alert-success">
          导入完成：创建 ${j.created_count}，跳过 ${j.skipped_count}，失败 ${j.failed_count}
        </div>
        <pre class="form-control font-mono" style="overflow:auto; background: rgba(0,0,0,0.25);">${escapeHtml(JSON.stringify(SensitiveDataMasker.maskObject(j), null, 2))}</pre>
      `;
      Toast.success(`批量导入完成：创建 ${j.created_count}`);
      try { await loadAccounts(); } catch {}
    } catch (e) {
      resultDiv.innerHTML = `<div class="alert alert-danger">导入失败：${escapeHtml(e.message)}</div>`;
      Toast.error('批量导入失败：' + e.message);
    }
  });
}

function showKiroAwsImportModal() {
  const vals = getKiroFormValues();
  const { overlay, cleanup } = createCustomModal({
    title: '导入 AWS SSO cache（Builder ID）',
    maxWidthPx: 880,
    bodyHtml: `
      <div class="alert alert-info">
        <div><strong>提示：</strong>AWS SSO cache 目录通常为 <span class="mono">C:\\Users\\{username}\\.aws\\sso\\cache</span></div>
        <div class="text-sm text-muted mt-2">可上传多个 JSON 文件，系统会自动合并出 clientId/clientSecret/accessToken/refreshToken</div>
      </div>

      <div class="grid grid-cols-2 gap-4">
        <div class="input-group">
          <label class="input-label" for="kiro_aws_label">标签名称</label>
          <input id="kiro_aws_label" class="form-control" placeholder="Kiro(Builder ID)" value="${escapeHtml(vals.label || '')}" />
        </div>
        <div class="input-group">
          <label class="input-label" for="kiro_aws_region">Region</label>
          <input id="kiro_aws_region" class="form-control" placeholder="us-east-1" value="${escapeHtml(vals.region || '')}" />
        </div>
      </div>

      <div class="flex justify-between items-center mt-2">
        <label class="flex items-center gap-2 cursor-pointer">
          <input id="kiro_aws_enabled" type="checkbox" class="form-checkbox" ${vals.enabled ? 'checked' : ''}/>
          <span>创建后启用</span>
        </label>
        <label class="flex items-center gap-2 cursor-pointer">
          <input id="kiro_aws_skip_dup" type="checkbox" class="form-checkbox"/>
          <span>跳过去重检查</span>
        </label>
      </div>

      <div class="flex gap-2 mt-4">
        <button class="btn btn-primary" type="button" data-mode="file">文件上传</button>
        <button class="btn btn-secondary" type="button" data-mode="json">粘贴 JSON</button>
      </div>

      <div class="mt-3" data-section="file">
        <div class="input-group">
          <label class="input-label">选择文件（可多选 .json）</label>
          <input type="file" id="kiro_aws_files" class="form-control" multiple accept=".json" />
          <div class="text-muted text-sm mt-2" id="kiro_aws_files_hint"></div>
        </div>
      </div>

      <div class="mt-3 hidden" data-section="json">
        <div class="input-group">
          <label class="input-label" for="kiro_aws_json">JSON 内容</label>
          <textarea id="kiro_aws_json" class="form-control font-mono" rows="10" placeholder='{"clientId":"...","clientSecret":"...","accessToken":"...","refreshToken":"..."}'></textarea>
        </div>
      </div>

      <div id="kiro_aws_validate" class="mt-3"></div>
      <div id="kiro_aws_result" class="mt-4"></div>
    `,
    footerHtml: `
      <button class="btn btn-secondary" type="button" data-action="cancel">取消</button>
      <button class="btn btn-primary" type="button" data-action="import" disabled>导入账号</button>
    `
  });

  let mode = 'file';
  let merged = null;

  const modeFileBtn = overlay.querySelector('[data-mode="file"]');
  const modeJsonBtn = overlay.querySelector('[data-mode="json"]');
  const fileSection = overlay.querySelector('[data-section="file"]');
  const jsonSection = overlay.querySelector('[data-section="json"]');
  const importBtn = overlay.querySelector('[data-action="import"]');
  const validateDiv = overlay.querySelector('#kiro_aws_validate');
  const filesHint = overlay.querySelector('#kiro_aws_files_hint');
  const fileInput = overlay.querySelector('#kiro_aws_files');
  const jsonTextarea = overlay.querySelector('#kiro_aws_json');
  const resultDiv = overlay.querySelector('#kiro_aws_result');

  function setMode(next) {
    mode = next;
    if (mode === 'file') {
      modeFileBtn.className = 'btn btn-primary';
      modeJsonBtn.className = 'btn btn-secondary';
      fileSection.classList.remove('hidden');
      jsonSection.classList.add('hidden');
    } else {
      modeFileBtn.className = 'btn btn-secondary';
      modeJsonBtn.className = 'btn btn-primary';
      fileSection.classList.add('hidden');
      jsonSection.classList.remove('hidden');
    }
    validateAndUpdate();
  }

  function mergeCredentials(obj) {
    if (!obj || typeof obj !== 'object') return;
    merged = merged || {};
    const clientId = obj.clientId ?? obj.client_id;
    const clientSecret = obj.clientSecret ?? obj.client_secret;
    const accessToken = obj.accessToken ?? obj.access_token;
    const refreshToken = obj.refreshToken ?? obj.refresh_token;

    const cid = String(clientId ?? '').trim();
    const csec = String(clientSecret ?? '').trim();
    const at = String(accessToken ?? '').trim();
    const rt = String(refreshToken ?? '').trim();

    if (cid) merged.clientId = cid;
    if (csec) merged.clientSecret = csec;
    if (at) merged.accessToken = at;
    if (rt) merged.refreshToken = rt;
  }

  function validateMerged() {
    const missing = [];
    const c = merged || {};
    if (!c.clientId) missing.push('clientId');
    if (!c.clientSecret) missing.push('clientSecret');
    if (!c.accessToken) missing.push('accessToken');
    if (!c.refreshToken) missing.push('refreshToken');
    return { ok: missing.length === 0, missing, cleaned: c };
  }

  async function readJsonFile(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => {
        try {
          resolve(JSON.parse(String(reader.result || '')));
        } catch (e) {
          reject(new Error(`${file.name}: JSON 解析失败`));
        }
      };
      reader.onerror = () => reject(new Error(`${file.name}: 读取失败`));
      reader.readAsText(file);
    });
  }

  fileInput?.addEventListener('change', async () => {
    resultDiv.innerHTML = '';
    merged = null;
    const files = Array.from(fileInput.files || []);
    if (!files.length) {
      filesHint.textContent = '';
      validateAndUpdate();
      return;
    }
    filesHint.textContent = `已选择 ${files.length} 个文件：${files.map(f => f.name).join(', ')}`;
    try {
      for (const f of files) {
        const obj = await readJsonFile(f);
        mergeCredentials(obj);
      }
    } catch (e) {
      validateDiv.innerHTML = `<div class="alert alert-danger">${escapeHtml(e.message)}</div>`;
    } finally {
      validateAndUpdate();
    }
  });

  jsonTextarea?.addEventListener('input', () => {
    resultDiv.innerHTML = '';
    merged = null;
    const t = (jsonTextarea.value || '').trim();
    if (!t) {
      validateAndUpdate();
      return;
    }
    try {
      mergeCredentials(JSON.parse(t));
    } catch {
      merged = { _invalidJson: true };
    }
    validateAndUpdate();
  });

  function validateAndUpdate() {
    if (merged?._invalidJson) {
      importBtn.disabled = true;
      validateDiv.innerHTML = '<div class="alert alert-danger">JSON 格式无效</div>';
      return;
    }
    const v = validateMerged();
    if (v.ok) {
      importBtn.disabled = false;
      validateDiv.innerHTML = `<div class="alert alert-success">已识别凭据字段：clientId/clientSecret/accessToken/refreshToken</div><pre class="form-control font-mono" style="overflow:auto; background: rgba(0,0,0,0.25);">${escapeHtml(JSON.stringify(SensitiveDataMasker.maskObject({
        clientId: v.cleaned.clientId,
        clientSecret: v.cleaned.clientSecret,
        accessToken: v.cleaned.accessToken,
        refreshToken: v.cleaned.refreshToken
      }), null, 2))}</pre>`;
    } else {
      importBtn.disabled = true;
      if (!merged) validateDiv.innerHTML = '<div class="text-muted text-sm">请上传文件或粘贴 JSON 后再导入</div>';
      else validateDiv.innerHTML = `<div class="alert alert-warning">缺少字段：${escapeHtml(v.missing.join(', '))}</div>`;
    }
  }

  setMode('file');

  modeFileBtn?.addEventListener('click', () => setMode('file'));
  modeJsonBtn?.addEventListener('click', () => setMode('json'));

  overlay.querySelector('[data-action="cancel"]')?.addEventListener('click', cleanup);
  importBtn?.addEventListener('click', async () => {
    const v = validateMerged();
    if (!v.ok) return;
    resultDiv.innerHTML = '<div class="loading-state"><span class="loading-spinner"></span><span>正在导入...</span></div>';

    const label = overlay.querySelector('#kiro_aws_label')?.value?.trim() || null;
    const region = overlay.querySelector('#kiro_aws_region')?.value?.trim() || null;
    const enabled = !!overlay.querySelector('#kiro_aws_enabled')?.checked;
    const skipDuplicateCheck = !!overlay.querySelector('#kiro_aws_skip_dup')?.checked;

    try {
      const r = await authFetch(api('/v2/kiro/import/aws-credentials'), {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ credentials: {
          clientId: v.cleaned.clientId,
          clientSecret: v.cleaned.clientSecret,
          accessToken: v.cleaned.accessToken,
          refreshToken: v.cleaned.refreshToken,
          authMethod: 'builder-id'
        }, label, enabled, region, skipDuplicateCheck })
      });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();
      resultDiv.innerHTML = `
        <div class="alert alert-success">导入成功（refreshed=${j.refreshed ? 'true' : 'false'}）</div>
        <pre class="form-control font-mono" style="overflow:auto; background: rgba(0,0,0,0.25);">${escapeHtml(JSON.stringify(SensitiveDataMasker.maskObject(j), null, 2))}</pre>
      `;
      Toast.success('AWS cache 导入成功');
      try { await loadAccounts(); } catch {}
    } catch (e) {
      resultDiv.innerHTML = `<div class="alert alert-danger">导入失败：${escapeHtml(e.message)}</div>`;
      Toast.error('导入失败：' + e.message);
    }
  });
}
