// 敏感信息脱敏工具
const SensitiveDataMasker = {
  sensitiveFields: [
    'password', 'secret', 'token', 'key', 'api_key', 'apikey',
    'accesstoken', 'access_token', 'refreshtoken', 'refresh_token',
    'clientsecret', 'client_secret', 'authorization', 'bearer',
    'credential', 'private', 'admin_token', 'x-admin-key'
  ],

  isSensitiveField(fieldName) {
    if (!fieldName) return false;
    const lower = fieldName.toLowerCase().replace(/[-_]/g, '');
    return this.sensitiveFields.some(sf => lower.includes(sf.replace(/[-_]/g, '')));
  },

  maskValue(value) {
    if (value === null || value === undefined) return value;
    if (typeof value !== 'string') value = String(value);
    if (value.length <= 8) return '********';
    return value.substring(0, 4) + '****' + value.substring(value.length - 4);
  },

  maskObject(obj, depth = 0) {
    if (depth > 10) return obj;
    if (obj === null || obj === undefined) return obj;
    if (typeof obj !== 'object') return obj;

    if (Array.isArray(obj)) {
      return obj.map(item => this.maskObject(item, depth + 1));
    }

    const masked = {};
    for (const [key, value] of Object.entries(obj)) {
      if (this.isSensitiveField(key)) {
        masked[key] = this.maskValue(value);
      } else if (typeof value === 'object' && value !== null) {
        masked[key] = this.maskObject(value, depth + 1);
      } else {
        masked[key] = value;
      }
    }
    return masked;
  },

  maskUrl(url) {
    if (!url) return url;
    try {
      const urlObj = new URL(url, window.location.origin);
      const params = new URLSearchParams(urlObj.search);
      let hasSensitive = false;
      for (const [key] of params.entries()) {
        if (this.isSensitiveField(key)) {
          params.set(key, '****');
          hasSensitive = true;
        }
      }
      if (hasSensitive) {
        urlObj.search = params.toString();
        return urlObj.pathname + urlObj.search;
      }
      return url;
    } catch {
      return url;
    }
  },

  maskMessage(message) {
    if (!message) return message;
    return message
      .replace(/Bearer\s+[^\s]+/gi, 'Bearer ****')
      .replace(/sk-[a-zA-Z0-9]{8,}/g, 'sk-****')
      .replace(/api[_-]?key[=:]\s*[^\s&]+/gi, 'api_key=****')
      .replace(/token[=:]\s*[^\s&]+/gi, 'token=****')
      .replace(/secret[=:]\s*[^\s&]+/gi, 'secret=****');
  }
};
