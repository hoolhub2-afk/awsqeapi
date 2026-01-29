// Toast 通知系统
const Toast = {
  container: null,

  init() {
    this.container = document.getElementById('toastContainer');
  },

  show(message, type = 'info', title = '', duration = 5000) {
    if (!this.container) this.init();

    const icons = {
      success: '<i class="fa-solid fa-circle-check"></i>',
      error: '<i class="fa-solid fa-circle-xmark"></i>',
      warning: '<i class="fa-solid fa-triangle-exclamation"></i>',
      info: '<i class="fa-solid fa-circle-info"></i>'
    };

    const titles = { success: title || '成功', error: title || '错误', warning: title || '警告', info: title || '提示' };

    const toast = document.createElement('div');
    toast.className = `toast toast-${type} slide-in`;

    // 使用 escapeHTML 防止 XSS 攻击
    const safeMessage = typeof escapeHTML === 'function' ? escapeHTML(message) : message;
    const safeTitle = typeof escapeHTML === 'function' ? escapeHTML(titles[type]) : titles[type];

    toast.innerHTML = `
      <div class="toast-icon">${icons[type]}</div>
      <div class="toast-content">
        <div class="toast-title">${safeTitle}</div>
        ${safeMessage ? `<div class="toast-message">${safeMessage}</div>` : ''}
      </div>
      <button class="toast-close" onclick="this.parentElement.remove()"><i class="fa-solid fa-xmark"></i></button>
      <div class="toast-progress" style="animation-duration: ${duration}ms"></div>
    `;

    this.container.appendChild(toast);

    if (duration > 0) {
      setTimeout(() => {
        toast.style.animation = 'fadeOut 0.3s ease-out forwards';
        setTimeout(() => toast.remove(), 300);
      }, duration);
    }

    return toast;
  },

  success(message, title) { return this.show(message, 'success', title); },
  error(message, title) { return this.show(message, 'error', title); },
  warning(message, title) { return this.show(message, 'warning', title); },
  info(message, title) { return this.show(message, 'info', title); }
};
