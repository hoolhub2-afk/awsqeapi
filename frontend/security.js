/**
 * 前端安全工具函数
 * 防止 XSS 攻击
 */

/**
 * 转义 HTML 特殊字符
 */
function escapeHTML(str) {
    if (typeof str !== 'string') return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

/**
 * 安全地设置元素的 HTML 内容
 * 会自动转义潜在的恶意代码
 */
function safeSetHTML(element, html) {
    if (!element) return;
    
    // 如果是纯文本，直接使用 textContent (更安全)
    if (typeof html === 'string' && !html.includes('<')) {
        element.textContent = html;
        return;
    }
    
    // 简单的 XSS 过滤 (生产环境建议使用 DOMPurify)
    const sanitized = String(html)
        .replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
        .replace(/javascript:/gi, '')
        .replace(/on\w+\s*=/gi, '');
    
    element.innerHTML = sanitized;
}

/**
 * 安全地设置文本内容
 */
function safeSetText(element, text) {
    if (!element) return;
    element.textContent = String(text || '');
}

/**
 * 创建安全的 DOM 元素
 */
function createSafeElement(tag, attributes = {}, textContent = '') {
    const element = document.createElement(tag);
    
    // 设置属性 (自动过滤危险属性)
    const dangerousAttrs = ['onclick', 'onload', 'onerror', 'onmouseover'];
    Object.entries(attributes).forEach(([key, value]) => {
        if (!dangerousAttrs.includes(key.toLowerCase())) {
            element.setAttribute(key, value);
        }
    });
    
    // 设置文本内容 (安全)
    if (textContent) {
        element.textContent = textContent;
    }
    
    return element;
}

/**
 * 验证 URL 是否安全
 */
function isSafeURL(url) {
    try {
        const parsed = new URL(url, window.location.origin);
        // 只允许 http/https 协议
        return ['http:', 'https:'].includes(parsed.protocol);
    } catch {
        return false;
    }
}

// 导出到全局作用域
window.escapeHTML = escapeHTML;
window.safeSetHTML = safeSetHTML;
window.safeSetText = safeSetText;
window.createSafeElement = createSafeElement;
window.isSafeURL = isSafeURL;
