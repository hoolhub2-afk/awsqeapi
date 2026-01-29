// 日志系统
const Logger = {
  logs: [],
  container: null,
  maxLogs: 500,
  filter: 'all',

  init() {
    this.container = document.getElementById('logContainer');
    if (this.container) {
      this.injectStyles();
    }
  },

  injectStyles() {
    // Styles are mostly handled by global CSS now, but keeping this for specific log styling
  },

  log(message, type = 'info', details = null) {
    const ts = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const fullTs = new Date().toISOString();
    const maskedMessage = SensitiveDataMasker.maskMessage(message);
    const maskedDetails = details ? SensitiveDataMasker.maskObject(details) : null;
    this.logs.unshift({ time: ts, fullTime: fullTs, message: maskedMessage, type, details: maskedDetails });
    this.logs = this.logs.slice(0, this.maxLogs);
    this.render();
  },

  success(message, details = null) { this.log(message, 'success', details); },
  error(message, details = null) { this.log(message, 'error', details); },
  warn(message, details = null) { this.log(message, 'warning', details); },

  api(method, url, status, duration, requestBody = null, responseBody = null) {
    const statusType = status >= 200 && status < 300 ? 'success' : status >= 400 ? 'error' : 'warning';
    const maskedUrl = SensitiveDataMasker.maskUrl(url);
    const message = `${method} ${maskedUrl} - ${status} (${duration}ms)`;
    this.log(message, statusType, { method, url: maskedUrl, status, duration, request: requestBody, response: responseBody });
  },

  clear() {
    this.logs = [];
    this.render();
    Toast.info('日志已清空');
  },

  setFilter(type) {
    this.filter = type;
    this.render();
  },

  export() {
    if (this.logs.length === 0) {
      Toast.warning('没有日志可导出');
      return;
    }
    const content = this.logs.map(entry => {
      let line = `[${entry.fullTime}] [${entry.type.toUpperCase()}] ${entry.message}`;
      if (entry.details) {
        line += '\n  Details: ' + JSON.stringify(entry.details, null, 2).replace(/\n/g, '\n  ');
      }
      return line;
    }).join('\n\n');

    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `awsq-logs-${new Date().toISOString().slice(0,10)}.txt`;
    a.click();
    URL.revokeObjectURL(url);
    Toast.success('日志已导出');
  },

  render() {
    if (!this.container) return;

    const filteredLogs = this.filter === 'all' 
      ? this.logs 
      : this.logs.filter(l => l.type === this.filter);

    if (filteredLogs.length === 0) {
      this.container.innerHTML = `
        <div class="flex flex-col items-center justify-center h-full text-muted" style="min-height: 200px; opacity: 0.5;">
          <i class="fa-solid fa-terminal" style="font-size: 32px; margin-bottom: 12px;"></i>
          <div>暂无日志记录</div>
        </div>
      `;
      return;
    }

    const typeIcons = { 
      success: '<i class="fa-solid fa-check text-success"></i>', 
      error: '<i class="fa-solid fa-xmark text-danger"></i>', 
      warning: '<i class="fa-solid fa-exclamation text-warning"></i>', 
      info: '<i class="fa-solid fa-info text-info"></i>' 
    };
    
    const typeColors = { 
      success: 'var(--color-success)', 
      error: 'var(--color-danger)', 
      warning: 'var(--color-warning)', 
      info: 'var(--color-info)' 
    };

    this.container.innerHTML = filteredLogs.map((entry, idx) => {
      const icon = typeIcons[entry.type] || typeIcons.info;
      const color = typeColors[entry.type] || typeColors.info;
      const hasDetails = entry.details && Object.keys(entry.details).length > 0;
      const detailsId = `log-details-${idx}`;

      let detailsHtml = '';
      if (hasDetails) {
        detailsHtml = `
          <div id="${detailsId}" class="log-details hidden">
            <pre>${this.highlightJson(entry.details)}</pre>
          </div>
        `;
      }

      return `
        <div class="log-entry fade-in" style="border-left: 3px solid ${color}; background: rgba(0,0,0,0.2); margin-bottom: 8px; border-radius: 4px; overflow: hidden;">
          <div style="padding: 10px; display:flex; align-items:center; gap:10px;">
            <span style="font-family:monospace; color: var(--text-muted); font-size: 12px; min-width: 60px;">${entry.time}</span>
            <span style="width: 20px; text-align: center;">${icon}</span>
            <span style="flex:1; font-family: 'Inter', sans-serif; font-size: 13px; color: #e2e8f0;">${this.escapeHtml(entry.message)}</span>
            ${hasDetails ? `
              <button class="btn-xs btn-secondary" onclick="Logger.toggleDetails('${detailsId}')">
                <i class="fa-solid fa-code"></i> DEBUG
              </button>
            ` : ''}
          </div>
          ${detailsHtml}
        </div>
      `;
    }).join('');
  },

  toggleDetails(id) {
    const el = document.getElementById(id);
    if (el) {
      el.classList.toggle('hidden');
      if (!el.classList.contains('hidden')) {
        el.style.padding = '10px';
        el.style.background = 'rgba(0,0,0,0.3)';
        el.style.borderTop = '1px solid rgba(255,255,255,0.05)';
        el.style.fontSize = '12px';
        el.style.overflowX = 'auto';
      }
    }
  },

  highlightJson(obj) {
    const json = JSON.stringify(obj, null, 2);
    return this.escapeHtml(json).replace(/("(\\u[a-zA-Z0-9]{4}|\\[^u]|[^\\"])*"(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d*)?(?:[eE][+\-]?\d+)?)/g, function (match) {
        let cls = 'number';
        if (/^"/.test(match)) {
            if (/:$/.test(match)) {
                cls = 'key';
                return `<span style="color: #9cdcfe;">${match.replace(':', '')}</span>:`;
            } else {
                cls = 'string';
                return `<span style="color: #ce9178;">${match}</span>`;
            }
        } else if (/true|false/.test(match)) {
            cls = 'boolean';
            return `<span style="color: #569cd6;">${match}</span>`;
        } else if (/null/.test(match)) {
            cls = 'null';
            return `<span style="color: #569cd6;">${match}</span>`;
        }
        return `<span style="color: #b5cea8;">${match}</span>`;
    });
  },

  escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
};

function log(message, type = 'info', details = null) {
  Logger.log(message, type, details);
}
