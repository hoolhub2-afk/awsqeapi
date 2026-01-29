// Modal 对话框系统
const Modal = {
  show(options) {
    return new Promise((resolve) => {
      const { title = '确认', message = '', confirmText = '确定', cancelText = '取消', type = 'info' } = options;

      const icons = {
        success: '<i class="fa-solid fa-circle-check text-success"></i>',
        error: '<i class="fa-solid fa-circle-xmark text-danger"></i>',
        warning: '<i class="fa-solid fa-triangle-exclamation text-warning"></i>',
        info: '<i class="fa-solid fa-circle-info text-info"></i>',
        danger: '<i class="fa-solid fa-triangle-exclamation text-danger"></i>'
      };

      // 使用 escapeHTML 防止 XSS 攻击
      const safeTitle = typeof escapeHTML === 'function' ? escapeHTML(title) : title;
      const safeMessage = typeof escapeHTML === 'function' ? escapeHTML(message) : message;
      const safeConfirmText = typeof escapeHTML === 'function' ? escapeHTML(confirmText) : confirmText;
      const safeCancelText = typeof escapeHTML === 'function' ? escapeHTML(cancelText) : cancelText;

      const overlay = document.createElement('div');
      overlay.className = 'modal-overlay fade-in';
      overlay.innerHTML = `
        <div class="modal">
          <div class="modal-header">
            <div class="modal-icon" style="font-size: 1.5rem;">${icons[type] || icons.info}</div>
            <div class="modal-title">${safeTitle}</div>
          </div>
          <div class="modal-body">${safeMessage}</div>
          <div class="modal-footer">
            <button class="btn btn-secondary modal-cancel">${safeCancelText}</button>
            <button class="btn btn-${type === 'danger' ? 'danger' : 'primary'} modal-confirm">${safeConfirmText}</button>
          </div>
        </div>
      `;

      document.body.appendChild(overlay);

      const confirmBtn = overlay.querySelector('.modal-confirm');
      const cancelBtn = overlay.querySelector('.modal-cancel');
      const modalBox = overlay.querySelector('.modal');

      // Add entrance animation for modal box
      modalBox.style.animation = 'scaleIn 0.3s ease-out forwards';

      const cleanup = () => {
        overlay.style.animation = 'fadeOut 0.2s ease-out forwards';
        modalBox.style.animation = 'scaleOut 0.2s ease-in forwards';
        setTimeout(() => overlay.remove(), 200);
      };

      confirmBtn.onclick = () => { cleanup(); resolve(true); };
      cancelBtn.onclick = () => { cleanup(); resolve(false); };
      overlay.onclick = (e) => { if (e.target === overlay) { cleanup(); resolve(false); } };
      
      // Focus on confirm button for accessibility
      setTimeout(() => confirmBtn.focus(), 100);
    });
  },

  confirm(message, title = '确认操作') {
    return this.show({ title, message, type: 'warning' });
  },

  danger(message, title = '危险操作') {
    return this.show({ title, message, type: 'danger', confirmText: '确认删除', cancelText: '取消' });
  }
};
