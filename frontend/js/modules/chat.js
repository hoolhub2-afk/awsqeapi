// Chat 测试模块
function restoreChatInputs() {
  const key = sessionStorage.getItem('chat_api_key');
  if (key) {
    const el = document.getElementById('chat_api_key');
    if (el) el.value = key;
  }

  const account = sessionStorage.getItem('chat_account_id');
  if (account) {
    const el = document.getElementById('chat_account_id');
    if (el) el.value = account;
  }

  const endUser = sessionStorage.getItem('chat_end_user_id');
  if (endUser) {
    const el = document.getElementById('chat_end_user_id');
    if (el) el.value = endUser;
  }
}

async function send() {
  const apiKey = document.getElementById('chat_api_key').value.trim();
  const accountId = document.getElementById('chat_account_id').value.trim();
  const endUserId = document.getElementById('chat_end_user_id').value.trim();
  const model = document.getElementById('model').value.trim();
  const stream = document.getElementById('stream').value === 'true';
  const out = document.getElementById('out');
  
  // Clear previous output and set loading state
  out.innerHTML = '<span class="loading-dots">发送中</span>';
  out.className = 'form-control flex-1 font-mono output-area';
  
  if (!apiKey) {
    out.textContent = '❌ API Key required';
    out.style.color = 'var(--color-danger)';
    Toast.warning('Please provide an API key first');
    return;
  }

  // Save inputs to session storage
  sessionStorage.setItem('chat_api_key', apiKey);
  if (accountId) sessionStorage.setItem('chat_account_id', accountId);
  else sessionStorage.removeItem('chat_account_id');
  if (endUserId) sessionStorage.setItem('chat_end_user_id', endUserId);
  else sessionStorage.removeItem('chat_end_user_id');

  let messages;
  try {
    messages = JSON.parse(document.getElementById('messages').value);
  } catch(e) {
    out.innerHTML = '<span class="text-danger">❌ Invalid JSON in messages field</span>';
    Toast.error('Invalid messages JSON payload');
    return;
  }

  const body = { model, messages, stream };
  const headers = {
    'content-type': 'application/json',
    'Authorization': `Bearer ${apiKey}`
  };

  if (accountId) headers['X-Account-Id'] = accountId;
  if (endUserId) {
    headers['X-End-User-Id'] = endUserId;
    body.user = endUserId;
  }

  try {
    if (!stream) {
      const r = await fetch(api('/v1/chat/completions'), {
        method: 'POST',
        headers,
        body: JSON.stringify(body)
      });

      const textBody = await r.text();
      
      if (!r.ok) {
        formatError(out, textBody, r.status);
        return;
      }

      try {
        const json = JSON.parse(textBody);
        out.textContent = JSON.stringify(json, null, 2);
        // Apply simple syntax highlighting if possible (optional enhancement)
        Toast.success('Request completed');
      } catch {
        out.textContent = textBody;
      }
    } else {
      const r = await fetch(api('/v1/chat/completions'), {
        method: 'POST',
        headers,
        body: JSON.stringify(body)
      });

      if (!r.ok) {
        const textBody = await r.text();
        formatError(out, textBody, r.status);
        return;
      }

      out.textContent = '';
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        
        const chunk = decoder.decode(value, {stream:true});
        // Simply append raw stream for now, or could parse SSE events if needed
        out.textContent += chunk;
        
        // Auto-scroll to bottom
        out.scrollTop = out.scrollHeight;
      }

      Toast.success('Stream request completed');
    }
  } catch(e) {
    out.innerHTML = `<span class="text-danger">❌ Network Error: ${e.message}</span>`;
    Toast.error('Request failed: ' + e.message);
  }
}

function formatError(element, text, status) {
  try {
    const parsed = JSON.parse(text);
    element.textContent = JSON.stringify(parsed, null, 2);
    element.style.border = '1px solid var(--color-danger)';
    Toast.error(parsed.detail || `Request failed (${status})`);
  } catch {
    element.textContent = text || `Request failed (${status})`;
    element.style.border = '1px solid var(--color-danger)';
    Toast.error(`Request failed (${status})`);
  }
}
