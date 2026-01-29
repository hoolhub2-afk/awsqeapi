// 密钥管理模块
async function loadKeys() {
  const loading = document.getElementById('keys-loading');
  const text = document.getElementById('keys-refresh-text');

  loading.style.display = 'inline-flex';
  text.style.display = 'none';

  try {
    const r = await authFetch(api('/v2/security/keys'));
    const j = await r.json();
    renderKeys(j.keys || []);
    if (j.default_rate_limit) {
      document.getElementById('new_key_rate_limit').placeholder = `默认${j.default_rate_limit}`;
    }
    Toast.success(`已加载 ${j.keys?.length || 0} 个密钥`);
  } catch(e) {
    Toast.error('加载密钥失败：' + e.message);
  } finally {
    loading.style.display = 'none';
    text.style.display = 'inline';
  }
}

function renderKeys(keys) {
  const root = document.getElementById('keys');
  root.innerHTML = '';

  if (!Array.isArray(keys) || keys.length === 0) {
    root.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔑</div><div>暂无密钥</div></div>';
    return;
  }

  const activeKeys = keys.filter(k => k.status === 'active');
  if (activeKeys.length === 0) {
    root.innerHTML = '<div class="empty-state"><div class="empty-state-icon">🔑</div><div>暂无活跃密钥</div></div>';
    return;
  }

  let currentlyUsedKeyId = null;
  let latestUsedTime = null;
  activeKeys.forEach(key => {
    if (key.last_used) {
      const usedTime = new Date(key.last_used);
      if (!latestUsedTime || usedTime > latestUsedTime) {
        latestUsedTime = usedTime;
        currentlyUsedKeyId = key.key_id;
      }
    }
  });

  if (!currentlyUsedKeyId && activeKeys.length > 0) {
    currentlyUsedKeyId = activeKeys[0].key_id;
  }

  activeKeys.forEach(key => {
    const isCurrentKey = key.key_id === currentlyUsedKeyId;
    root.appendChild(createKeyCard(key, isCurrentKey));
  });
}

function createKeyCard(key, isCurrentKey = false) {
  const card = document.createElement('div');
  card.className = 'card key-card' + (isCurrentKey ? ' is-current' : '');

  const header = document.createElement('div');
  header.className = 'key-card-header';

  const left = document.createElement('div');
  left.className = 'key-card-left';

  const idRow = document.createElement('div');
  idRow.className = 'key-card-idrow';

  const keyIdPill = document.createElement('div');
  keyIdPill.className = 'key-id-pill mono';
  keyIdPill.textContent = key.key_id;
  keyIdPill.title = key.key_id;

  const copyIdBtn = document.createElement('button');
  copyIdBtn.className = 'btn btn-secondary btn-sm';
  copyIdBtn.type = 'button';
  copyIdBtn.innerHTML = '<i class="fa-regular fa-copy" aria-hidden="true"></i><span>复制ID</span>';
  copyIdBtn.onclick = async () => {
    try {
      await navigator.clipboard.writeText(key.key_id);
      Toast.success('已复制密钥ID');
    } catch {
      Toast.warning('复制失败');
    }
  };

  idRow.appendChild(keyIdPill);
  idRow.appendChild(copyIdBtn);

  const badges = document.createElement('div');
  badges.className = 'key-card-badges';

  const statusBadge = document.createElement('div');
  statusBadge.className = 'badge badge-success';
  statusBadge.innerHTML = '<i class="fa-solid fa-circle-check" aria-hidden="true"></i><span>活跃</span>';
  badges.appendChild(statusBadge);

  if (isCurrentKey) {
    const currentBadge = document.createElement('div');
    currentBadge.className = 'badge badge-success key-badge-current';
    currentBadge.innerHTML = '<i class="fa-solid fa-bolt" aria-hidden="true"></i><span>当前使用</span>';
    badges.appendChild(currentBadge);
  }

  left.appendChild(idRow);
  left.appendChild(badges);

  const actions = document.createElement('div');
  actions.className = 'key-card-actions';

  const copyBtn = document.createElement('button');
  copyBtn.className = 'btn btn-success';
  copyBtn.type = 'button';
  copyBtn.innerHTML = '<i class="fa-solid fa-key" aria-hidden="true"></i><span>复制完整密钥</span>';
  copyBtn.onclick = () => copyFullKey(key.key_id);

  const rotateBtn = document.createElement('button');
  rotateBtn.className = 'btn btn-warn';
  rotateBtn.type = 'button';
  rotateBtn.innerHTML = '<i class="fa-solid fa-rotate" aria-hidden="true"></i><span>轮换</span>';
  rotateBtn.onclick = () => rotateKey(key.key_id);

  const revokeBtn = document.createElement('button');
  revokeBtn.className = 'btn btn-danger';
  revokeBtn.type = 'button';
  revokeBtn.innerHTML = '<i class="fa-solid fa-trash-can" aria-hidden="true"></i><span>销毁</span>';
  revokeBtn.onclick = () => revokeKey(key.key_id);

  actions.appendChild(copyBtn);
  actions.appendChild(rotateBtn);
  actions.appendChild(revokeBtn);

  header.appendChild(left);
  header.appendChild(actions);
  card.appendChild(header);

  const meta = document.createElement('div');
  meta.className = 'kvs key-meta';

  function row(k, v) {
    const kEl = document.createElement('div');
    kEl.className = 'kvs-key';
    kEl.textContent = k;
    const vEl = document.createElement('div');
    vEl.className = 'kvs-value';
    vEl.textContent = v ?? '-';
    meta.appendChild(kEl);
    meta.appendChild(vEl);
  }

  row('创建时间', key.created_at ? new Date(key.created_at).toLocaleString('zh-CN') : '-');
  row('过期时间', key.expires_at ? new Date(key.expires_at).toLocaleString('zh-CN') : '永不过期');
  row('最后使用', key.last_used ? new Date(key.last_used).toLocaleString('zh-CN') : '未使用');
  row('使用次数', key.usage_count ?? 0);
  row('最大使用', key.max_uses ?? '不限制');
  row('速率限制', key.rate_limit_per_minute + ' 次/分钟');
  row('安全级别', key.security_level);

  card.appendChild(meta);
  return card;
}

async function createKey() {
  const expiresInput = document.getElementById('new_key_expires').value.trim();
  const maxUsesInput = document.getElementById('new_key_max_uses').value.trim();
  const rateLimitInput = document.getElementById('new_key_rate_limit').value.trim();
  const allowedIpsInput = document.getElementById('new_key_allowed_ips').value.trim();
  const metadataInput = document.getElementById('new_key_metadata').value.trim();

  const body = {};

  if (expiresInput) {
    const expires = parseInt(expiresInput);
    if (isNaN(expires) || expires < 1 || expires > 365) {
      Toast.error('过期天数必须是 1-365 之间的整数');
      return;
    }
    body.expires_in_days = expires;
  }

  if (maxUsesInput) {
    const maxUses = parseInt(maxUsesInput);
    if (isNaN(maxUses) || maxUses < 1) {
      Toast.error('最大使用次数必须是正整数');
      return;
    }
    body.max_uses = maxUses;
  }

  if (rateLimitInput) {
    const rateLimit = parseInt(rateLimitInput);
    if (isNaN(rateLimit) || rateLimit < 1 || rateLimit > 1000) {
      Toast.error('速率限制必须是 1-1000 之间的整数');
      return;
    }
    body.rate_limit = rateLimit;
  }

  if (allowedIpsInput) {
    body.allowed_ips = allowedIpsInput.split(',').map(ip => ip.trim()).filter(ip => ip);
  }

  if (metadataInput) {
    try {
      body.metadata = JSON.parse(metadataInput);
    } catch(e) {
      Toast.error('备注信息必须是有效的JSON格式');
      return;
    }
  }

  try {
    const r = await authFetch(api('/v2/security/keys/generate'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body)
    });

    if (!r.ok) throw new Error(await r.text());

    const result = await r.json();
    const resultDiv = document.getElementById('new_key_result');

    // 使用 DOM API 安全设置 value，避免 XSS
    const safeKeyId = typeof escapeHTML === 'function' ? escapeHTML(result.key_id) : result.key_id;
    const safeExpiresAt = result.expires_at ? new Date(result.expires_at).toLocaleString('zh-CN') : '永不过期';

    resultDiv.innerHTML = `
      <div class="success-banner">
        <div class="success-header">密钥创建成功</div>
        <div class="field">
          <label for="generated_api_key">API密钥（请妥善保存，仅显示一次）</label>
          <div style="display:flex;gap:8px;">
            <input id="generated_api_key" name="generated_api_key" class="mono" type="text" readonly aria-label="生成的 API 密钥" style="flex:1;"/>
            <button class="btn-success btn-sm" onclick="copyApiKey()" style="white-space:nowrap;">📋 复制密钥</button>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label for="generated_key_id">密钥ID</label>
            <input id="generated_key_id" name="generated_key_id" class="mono" type="text" readonly aria-label="生成的密钥 ID" value="${safeKeyId}"/>
          </div>
          <div class="field">
            <label for="generated_key_expires_at">过期时间</label>
            <input id="generated_key_expires_at" name="generated_key_expires_at" type="text" readonly aria-label="生成的密钥过期时间" value="${safeExpiresAt}"/>
          </div>
        </div>
      </div>
    `;
    // 使用 DOM API 安全设置 API 密钥值
    document.getElementById('generated_api_key').value = result.api_key;
    resultDiv.style.display = 'block';

    document.getElementById('new_key_expires').value = '';
    document.getElementById('new_key_max_uses').value = '';
    document.getElementById('new_key_rate_limit').value = '';
    document.getElementById('new_key_allowed_ips').value = '';
    document.getElementById('new_key_metadata').value = '';

    Toast.success('密钥创建成功，请复制保存');
    await loadKeys();

    setTimeout(() => resultDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
  } catch(e) {
    Toast.error('创建密钥失败：' + e.message);
  }
}

function copyApiKey() {
  const input = document.getElementById('generated_api_key');
  input.select();
  input.setSelectionRange(0, 99999);

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(input.value).then(() => {
        Toast.success('密钥已复制到剪贴板');
      }).catch(() => {
        document.execCommand('copy');
        Toast.success('密钥已复制到剪贴板');
      });
    } else {
      document.execCommand('copy');
      Toast.success('密钥已复制到剪贴板');
    }
  } catch(e) {
    Toast.error('复制失败，请手动复制');
  }
}

async function copyFullKey(keyId) {
  try {
    const r = await authFetch(api('/v2/security/keys/' + encodeURIComponent(keyId) + '/decrypt'));
    if (!r.ok) throw new Error(await r.text());

    const result = await r.json();
    const apiKey = result.api_key;

    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(apiKey);
      Toast.success('完整密钥已复制到剪贴板');
    } else {
      const tempInput = document.createElement('input');
      tempInput.setAttribute('aria-hidden', 'true');
      tempInput.setAttribute('tabindex', '-1');
      tempInput.name = 'clipboard_helper';
      tempInput.value = apiKey;
      tempInput.style.position = 'absolute';
      tempInput.style.left = '-9999px';
      document.body.appendChild(tempInput);
      tempInput.select();
      document.execCommand('copy');
      document.body.removeChild(tempInput);
      Toast.success('完整密钥已复制到剪贴板');
    }
  } catch(e) {
    Toast.error('获取密钥失败：' + e.message);
  }
}

async function rotateKey(keyId) {
  const confirmed = await Modal.confirm('轮换密钥后，旧密钥将立即失效，并生成新密钥。请确保已做好准备。', '轮换密钥');
  if (!confirmed) return;

  try {
    const r = await authFetch(api('/v2/security/keys/' + encodeURIComponent(keyId) + '/rotate'), { method: 'POST' });
    if (!r.ok) throw new Error(await r.text());

    const result = await r.json();
    const resultDiv = document.getElementById('new_key_result');

    // 使用 escapeHTML 防止 XSS
    const safeOldKeyId = typeof escapeHTML === 'function' ? escapeHTML(result.old_key_id) : result.old_key_id;
    const safeNewKeyId = typeof escapeHTML === 'function' ? escapeHTML(result.new_key_id) : result.new_key_id;

    resultDiv.innerHTML = `
      <div class="success-banner">
        <div class="success-header">密钥轮换成功</div>
        <div class="field">
          <label for="generated_api_key">新API密钥（请妥善保存）</label>
          <div style="display:flex;gap:8px;">
            <input id="generated_api_key" name="generated_api_key" class="mono" type="text" readonly aria-label="轮换后的新 API 密钥" style="flex:1;"/>
            <button class="btn-success btn-sm" onclick="copyApiKey()">📋 复制密钥</button>
          </div>
        </div>
        <div class="row">
          <div class="field">
            <label for="rotated_old_key_id">旧密钥ID</label>
            <input id="rotated_old_key_id" name="rotated_old_key_id" class="mono" type="text" readonly aria-label="轮换前的旧密钥 ID" value="${safeOldKeyId}"/>
          </div>
          <div class="field">
            <label for="rotated_new_key_id">新密钥ID</label>
            <input id="rotated_new_key_id" name="rotated_new_key_id" class="mono" type="text" readonly aria-label="轮换后的新密钥 ID" value="${safeNewKeyId}"/>
          </div>
        </div>
      </div>
    `;
    // 使用 DOM API 安全设置 API 密钥值
    document.getElementById('generated_api_key').value = result.new_api_key;
    resultDiv.style.display = 'block';

    Toast.success('密钥轮换成功，旧密钥已失效');
    await loadKeys();

    setTimeout(() => resultDiv.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 100);
  } catch(e) {
    Toast.error('轮换密钥失败：' + e.message);
  }
}

async function revokeKey(keyId) {
  const confirmed = await Modal.danger('销毁后该密钥将立即失效并从系统中永久删除，所有使用该密钥的请求都将被拒绝。此操作不可恢复！', '销毁密钥');
  if (!confirmed) return;

  try {
    const r = await authFetch(api('/v2/security/keys/' + encodeURIComponent(keyId) + '/revoke'), {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ reason: '管理员手动销毁' })
    });

    if (!r.ok) throw new Error(await r.text());

    Toast.success('密钥已永久销毁');
    await loadKeys();
  } catch(e) {
    Toast.error('销毁密钥失败：' + e.message);
  }
}
