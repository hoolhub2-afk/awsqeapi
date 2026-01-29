-- 创建会话粘性表
CREATE TABLE IF NOT EXISTS session_accounts (
    session_key TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at INTEGER DEFAULT (strftime('%s', 'now'))
);

-- 创建索引
CREATE INDEX IF NOT EXISTS idx_session_expires ON session_accounts(expires_at);
CREATE INDEX IF NOT EXISTS idx_session_account ON session_accounts(account_id);
