// API 请求工具
function api(path) {
  return ('/' + (path || '').replace(/^\/+/, '')).replace(/\/{2,}/g, '/');
}

let _authExpiredToastAt = 0;
function notifyAuthExpiredOnce() {
  const now = Date.now();
  if (now - _authExpiredToastAt < 5000) return;
  _authExpiredToastAt = now;
  try { Toast.error('未登录或登录已过期, 请手动访问 /login 重新登录'); } catch {}
}

// 敏感字段列表
const SENSITIVE_FIELDS = ['password', 'token', 'secret', 'accessToken', 'refreshToken', 'clientSecret', 'api_key', 'apiKey'];

function sanitizeForLog(obj) {
  if (!obj || typeof obj !== 'object') return obj;
  // 使用深度拷贝避免修改原对象
  const cloned = JSON.parse(JSON.stringify(obj));
  function traverse(o) {
    if (Array.isArray(o)) {
      return o.map(item => traverse(item));
    }
    if (typeof o === 'object' && o !== null) {
      const result = {};
      for (const key of Object.keys(o)) {
        if (SENSITIVE_FIELDS.some(f => key.toLowerCase().includes(f.toLowerCase()))) {
          result[key] = '[REDACTED]';
        } else {
          result[key] = traverse(o[key]);
        }
      }
      return result;
    }
    return o;
  }
  return traverse(cloned);
}

async function authFetch(url, options = {}) {
  options.credentials = 'include';
  const startTime = performance.now();
  const method = options.method || 'GET';

  let requestBody = null;
  if (options.body) {
    try { requestBody = sanitizeForLog(JSON.parse(options.body)); } catch { requestBody = '[non-JSON body]'; }
  }

  try {
    const response = await fetch(url, options);
    const duration = Math.round(performance.now() - startTime);

    const clonedResponse = response.clone();
    let responseBody = null;
    try {
      const text = await clonedResponse.text();
      if (text) {
        try { responseBody = JSON.parse(text); } catch { responseBody = text.length > 500 ? text.substring(0, 500) + '...' : text; }
      }
    } catch {}

    Logger.api(method, url, response.status, duration, requestBody, responseBody);

    if (response.status === 403 || response.status === 401) {
      throw new Error('未登录或登录已过期');
    }

    return response;
  } catch(e) {
    const duration = Math.round(performance.now() - startTime);
    if (e.message !== '未登录或登录已过期') {
      Logger.api(method, url, 0, duration, requestBody, { error: e.message });
    }
    throw e;
  }
}

async function checkAuth(retryCount = 0) {
  try {
    const response = await fetch('/v2/auth/check', { credentials: 'include', cache: 'no-cache' });

    if (response.ok) return true;

    if (response.status === 401 || response.status === 403) {
      if (retryCount === 0) {
        await new Promise(resolve => setTimeout(resolve, 1000));
        return await checkAuth(1);
      }
      notifyAuthExpiredOnce();
      return false;
    }

    if (retryCount === 0) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      return await checkAuth(1);
    }
  } catch (e) {
    if (retryCount === 0) {
      await new Promise(resolve => setTimeout(resolve, 1000));
      return await checkAuth(1);
    }
    Logger.warn('认证检查失败', e.message);
  }
  return false;
}

async function logout() {
  const confirmed = await Modal.confirm('确认退出登录吗？退出后需要重新登录才能访问控制台。', '退出登录');

  if (confirmed) {
    try {
      await fetch('/v2/auth/logout', { method: 'POST', credentials: 'include' });
    } catch (e) { Logger.warn('登出请求失败', e.message); }
    sessionStorage.removeItem('chat_api_key');
    sessionStorage.removeItem('chat_account_id');
    Toast.success('已安全退出登录，正在跳转...');
    setTimeout(() => { window.location.href = '/login'; }, 1000);
  }
}
