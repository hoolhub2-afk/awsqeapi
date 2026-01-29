# Amazon Q to API Bridge - Main Service

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=CassiopeiaCode/q2api&type=date&legend=top-left)](https://www.star-history.com/#CassiopeiaCode/q2api&type=date&legend=top-left)

将 Amazon Q Developer 转换为兼容 OpenAI 和 Claude API 的服务，支持多账号管理、流式响应和智能负载均衡。

**项目地址：**
- GitHub: https://github.com/CassiopeiaCode/q2api
- Codeberg: https://codeberg.org/Korieu/amazonq2api

**致谢：**
- 感谢 [amq2api](https://github.com/mucsbr/amq2api) 项目提供的 Claude 消息格式转换参考

## ✨ 核心特性

### API 兼容性
- **OpenAI Chat Completions API** - 完全兼容 `/v1/chat/completions` 端点
- **Claude Messages API** - 完全兼容 `/v1/messages` 端点，支持流式和非流式
- **Tool Use 支持** - 完整支持 Claude 格式的工具调用和结果返回
- **System Prompt** - 支持系统提示词和多模态内容（文本、图片）

### 账号管理
- **多账号支持** - 管理多个 Amazon Q 账号，灵活启用/禁用
- **自动令牌刷新** - 后台定时刷新过期令牌，请求时自动重试
- **智能统计** - 自动统计成功/失败次数，错误超阈值自动禁用
- **设备授权登录** - 通过 URL 快速登录并自动创建账号（5分钟超时）

### 负载与监控
- **随机负载均衡** - 从启用的账号中随机选择，均衡分配负载
- **健康检查** - 实时监控服务状态
- **Web 控制台** - 美观的前端界面，支持账号管理和 Chat 测试

### 网络与安全
- **HTTP 代理支持** - 可配置代理服务器，支持所有 HTTP 请求
- **API Key 白名单** - 可选的访问控制，支持开发模式
- **持久化存储** - 支持 SQLite（默认）、PostgreSQL、MySQL 数据库

## 🚀 部署

### 方式一：Docker Compose

```bash
# 1. 复制环境变量配置
cp .env.example .env

# 2. 编辑 .env 文件（可选）
# 配置 OPENAI_KEYS、MAX_ERROR_COUNT 等

# 3. 启动服务
docker-compose up -d

# 4. 查看日志
docker-compose logs -f

# 5. 停止服务
docker-compose down
```

服务访问地址：
- 🏠 Web 控制台：http://localhost:8000/
- 💚 健康检查：http://localhost:8000/healthz
- 📘 API 文档：http://localhost:8000/docs

### 方式二：本地部署

#### 1. 安装依赖

推荐使用 `uv` 进行环境管理和依赖安装。

```bash
# 安装 uv
pip install uv

# 创建虚拟环境并安装依赖
uv venv
uv pip install -r requirements.txt
```

#### 2. 配置环境变量

```bash
# 复制示例配置
cp .env.example .env

# 根据需要编辑 .env 文件
```

**.env 配置说明：**

```bash
# 数据库连接URL（留空使用本地SQLite）
# PostgreSQL: DATABASE_URL="postgres://user:password@host:5432/dbname?sslmode=require"
# MySQL: DATABASE_URL="mysql://user:password@host:3306/dbname"
DATABASE_URL=""

# OpenAI 风格 API Key 白名单（仅用于授权，与账号无关）
# 多个用逗号分隔，例如：OPENAI_KEYS="key1,key2,key3"
# 留空则为开发模式，不校验 Authorization
OPENAI_KEYS=""

# Token 计数倍率（影响 /v1/messages/count_tokens 和 /v1/messages 的输入 token 统计）
# 默认值为 1.0，可根据实际需要调整（如设置为 1.5 表示返回 1.5 倍的 token 数）
TOKEN_COUNT_MULTIPLIER="1.0"

# 主密钥（必须至少 32 字节，建议使用 base64）
# python - <<'PY' 可快速生成：import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
MASTER_KEY=""

# 出错次数阈值，超过此值自动禁用账号
MAX_ERROR_COUNT=100

# HTTP代理设置（留空不使用代理）
# 例如：HTTP_PROXY="http://127.0.0.1:7890"
HTTP_PROXY=""

# 管理控制台开关（默认启用）
# 设置为 "false" 或 "0" 可禁用管理控制台和相关API端点
ENABLE_CONSOLE="true"

# 主服务端口（默认 8000）
PORT=8000
```

**配置要点：**
- `OPENAI_KEYS` 为空：开发模式，不校验 Authorization
- `OPENAI_KEYS` 设置后：仅白名单中的 key 可访问 API
- API Key 仅用于访问控制，不映射到特定账号
- 账号选择策略：从所有启用账号中随机选择
- `ENABLE_CONSOLE` 设为 `false` 或 `0`：禁用 Web 管理控制台和账号管理 API
- `MASTER_KEY` 必须在生产环境中显式设置，用于加密存储所有 sk- 密钥

#### 3. 启动服务

```bash
# 启动服务 (带热重载)
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

服务启动后，即可通过 `http://localhost:8000` 访问。该模式适用于开发，修改代码后服务会自动重启。

## 📖 使用指南

### 账号管理

#### 方式一：Web 控制台（推荐）

访问 http://localhost:8000/ 使用可视化界面：
- 查看所有账号及详细状态
- URL 登录（设备授权）快速添加账号
- 创建/删除/编辑账号
- 启用/禁用账号切换
- 手动刷新 Token
- Chat 功能测试

#### 方式二：URL 登录（最简单）

快速添加账号的推荐方式：

1. **启动登录流程**
```bash
curl -X POST http://localhost:8000/v2/auth/start \
  -H "Content-Type: application/json" \
  -d '{"label": "我的账号", "enabled": true}'
```

2. **在浏览器中打开返回的 `verificationUriComplete` 完成登录**

3. **等待并创建账号**（最多5分钟）
```bash
curl -X POST http://localhost:8000/v2/auth/claim/{authId}
```

成功后自动创建并启用账号，立即可用。

#### 方式三：REST API 手动管理

**创建账号**
```bash
curl -X POST http://localhost:8000/v2/accounts \
  -H "Content-Type: application/json" \
  -d '{
    "label": "手动创建的账号",
    "clientId": "your-client-id",
    "clientSecret": "your-client-secret",
    "refreshToken": "your-refresh-token",
    "enabled": true
  }'
```

**列出所有账号**
```bash
curl http://localhost:8000/v2/accounts
```

**更新账号**
```bash
curl -X PATCH http://localhost:8000/v2/accounts/{account_id} \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'
```

**刷新 Token**
```bash
curl -X POST http://localhost:8000/v2/accounts/{account_id}/refresh
```

**删除账号**
```bash
curl -X DELETE http://localhost:8000/v2/accounts/{account_id}
```

### OpenAI 兼容 API

#### 非流式请求

```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-sonnet-4",
    "stream": false,
    "messages": [
      {"role": "system", "content": "你是一个乐于助人的助手"},
      {"role": "user", "content": "你好，请讲一个简短的故事"}
    ]
  }'
```

#### 流式请求（SSE）

```bash
curl -N -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-api-key" \
  -d '{
    "model": "claude-sonnet-4",
    "stream": true,
    "messages": [
      {"role": "user", "content": "讲一个笑话"}
    ]
  }'
```

#### Python 示例

```python
import openai

client = openai.OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="your-api-key"  # 如果配置了 OPENAI_KEYS
)

response = client.chat.completions.create(
    model="claude-sonnet-4",
    messages=[
        {"role": "user", "content": "你好"}
    ]
)

print(response.choices[0].message.content)
```

### Claude Messages API

本项目完整支持 Claude Messages API 格式，包括流式响应、工具调用、多模态内容等。

#### 基础文本对话

```bash
curl -X POST http://localhost:8000/v1/messages \
  -H "Content-Type: application/json" \
  -H "x-api-key: your-api-key" \
  -d '{
    "model": "claude-sonnet-4.5",
    "max_tokens": 1024,
    "messages": [
      {"role": "user", "content": "你好"}
    ]
  }'
```

#### Python SDK 示例

```python
from anthropic import Anthropic

client = Anthropic(
    base_url="http://localhost:8000/v1",
    api_key="your-api-key"
)

# 基础对话
message = client.messages.create(
    model="claude-sonnet-4.5",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "你好"}
    ]
)
print(message.content[0].text)

# 流式响应
with client.messages.stream(
    model="claude-sonnet-4.5",
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "写一首诗"}
    ]
) as stream:
    for text in stream.text_stream:
        print(text, end="", flush=True)
```

## 🔐 授权与账号选择

### 授权机制
- **开发模式**（`OPENAI_KEYS` 未设置）：不校验 Authorization
- **生产模式**（`OPENAI_KEYS` 已设置）：必须提供白名单中的 key

### 账号选择策略
- 从所有 `enabled=1` 的账号中**随机选择**
- API Key 不映射到特定账号（与 AWS 账号解耦）
- 无可用账号时返回 401

### Token 自动刷新
- **后台刷新**：每5分钟检查一次，超过25分钟未刷新的令牌自动刷新
- **请求时刷新**：若账号缺少 accessToken，自动刷新后重试
- **手动刷新**：支持通过 API 或 Web 控制台手动刷新

## 🏗️ 架构设计

### 核心模块

- **app.py** - FastAPI 主应用，RESTful API 端点定义
- **replicate.py** - Amazon Q 请求复刻
- **auth_flow.py** - 设备授权登录
- **claude_types.py** - Claude API 类型定义
- **claude_converter.py** - Claude 到 Amazon Q 转换
- **claude_parser.py** - Event Stream 解析
- **claude_stream.py** - Claude SSE 流式处理

## 📁 项目结构

```
v2/
├── app.py                          # FastAPI 主应用
├── db.py                           # 数据库抽象层 (SQLite/PG/MySQL)
├── replicate.py                    # Amazon Q 请求复刻
├── auth_flow.py                    # 设备授权登录
├── claude_types.py                 # Claude API 类型定义
├── claude_converter.py             # Claude 到 Amazon Q 转换
├── claude_parser.py                # Event Stream 解析
├── claude_stream.py                # Claude SSE 流式处理
├── requirements.txt                # Python 依赖
├── .env.example                    # 环境变量示例
├── .env                            # 环境变量配置（需自行创建）
├── docker-compose.yml              # Docker Compose 配置
├── Dockerfile                      # Docker 镜像配置
├── data.sqlite3                    # SQLite 数据库（自动创建）
├── templates/
├── frontend/
│   └── index.html                  # Web 控制台
└── scripts/
    ├── account_stats.py            # 账号统计脚本
    ├── retry_failed_accounts.py    # 重试失败账号脚本
    └── reset_accounts.py           # 重置账号脚本
```

## 🛠️ 技术栈

- **后端框架**: FastAPI + Python 3.11+
- **数据库**: SQLite3 (aiosqlite) / PostgreSQL (asyncpg) / MySQL (aiomysql)
- **HTTP 客户端**: httpx（支持异步和代理）
- **Token 计数**: tiktoken
- **前端**: 纯 HTML/CSS/JavaScript（无依赖）
- **认证**: AWS OIDC 设备授权流程

## 🔧 高级配置

### 环境变量

| 变量 | 说明 | 默认值 | 示例 |
|------|------|--------|------|
| `DATABASE_URL` | 数据库连接URL | 空（使用SQLite） | `"postgres://user:pass@host:5432/db"` |
| `OPENAI_KEYS` | API Key 白名单（逗号分隔） | 空（开发模式） | `"key1,key2"` |
| `TOKEN_COUNT_MULTIPLIER` | Token 计数倍率 | `1.0` | `"1.5"` |
| `MASTER_KEY` | 安全密钥主材料（base64/hex） | 自动生成并写入 `master.key`（仅限开发） | `"bXktc2VjdXJlLWtleS1iYXNlNjQ="` |
| `MAX_ERROR_COUNT` | 错误次数阈值 | 100 | `50` |
| `HTTP_PROXY` | HTTP代理地址 | 空 | `"http://127.0.0.1:7890"` |
| `ENABLE_CONSOLE` | 管理控制台开关 | `"true"` | `"false"` |
| `PORT` | 服务端口 | 8000 | `8080` |

### 数据库结构

```sql
CREATE TABLE accounts (
    id TEXT PRIMARY KEY,                -- UUID
    label TEXT,                         -- 账号标签
    clientId TEXT,                      -- OIDC 客户端 ID
    clientSecret TEXT,                  -- OIDC 客户端密钥
    refreshToken TEXT,                  -- 刷新令牌
    accessToken TEXT,                   -- 访问令牌
    other TEXT,                         -- JSON 格式的额外信息
    last_refresh_time TEXT,             -- 最后刷新时间
    last_refresh_status TEXT,           -- 最后刷新状态
    created_at TEXT,                    -- 创建时间
    updated_at TEXT,                    -- 更新时间
    enabled INTEGER DEFAULT 1,          -- 1=启用, 0=禁用
    error_count INTEGER DEFAULT 0,      -- 连续错误次数
    success_count INTEGER DEFAULT 0     -- 成功请求次数
);
```

## 📝 完整 API 端点列表

### 账号管理（需启用 ENABLE_CONSOLE）
- `POST /v2/accounts` - 创建账号
- `POST /v2/accounts/batch` - 批量创建账号
- `GET /v2/accounts` - 列出所有账号
- `GET /v2/accounts/{id}` - 获取账号详情
- `PATCH /v2/accounts/{id}` - 更新账号
- `DELETE /v2/accounts/{id}` - 删除账号
- `POST /v2/accounts/{id}/refresh` - 刷新 Token

### 设备授权（需启用 ENABLE_CONSOLE）
- `POST /v2/auth/start` - 启动登录流程
- `GET /v2/auth/status/{authId}` - 查询登录状态
- `POST /v2/auth/claim/{authId}` - 等待并创建账号（最多5分钟）

### OpenAI 兼容
- `POST /v1/chat/completions` - Chat Completions API

### Claude 兼容
- `POST /v1/messages` - Messages API（支持流式、工具调用、多模态）
- `POST /v1/messages/count_tokens` - Token 计数接口（预先统计消息的 token 数量）

### 其他
- `GET /` - Web 控制台（需启用 ENABLE_CONSOLE）
- `GET /healthz` - 健康检查
- `GET /docs` - API 文档（Swagger UI）

## 🐛 故障排查

### 401 Unauthorized
**可能原因：**
- API Key 不在 `OPENAI_KEYS` 白名单中
- 没有启用的账号（`enabled=1`）

**解决方法：**
1. 检查 `.env` 中的 `OPENAI_KEYS` 配置
2. 访问 `/v2/accounts` 确认至少有一个启用的账号

### Token 刷新失败
**可能原因：**
- refreshToken 已过期
- 网络连接问题

**解决方法：**
1. 查看账号的 `last_refresh_status` 字段
2. 检查网络连接和代理配置
3. 删除旧账号，通过 URL 登录重新添加

## 🚀 生产环境部署

### Uvicorn 多进程模式

```bash
# 使用多个 worker 提高并发性能
python -m uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

### Nginx 反向代理

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        
        # SSE 支持
        proxy_buffering off;
        proxy_cache off;
    }
}
```

## 🔒 安全建议

1. **生产环境必须配置 `OPENAI_KEYS`**
2. **使用 HTTPS 反向代理（Nginx + Let's Encrypt）**
3. **定期备份数据库**（SQLite: `data.sqlite3`，或 PG/MySQL 数据库）
4. **限制数据库访问权限**
5. **配置防火墙规则，限制访问来源**

## 📄 许可证

本项目仅供学习和测试使用。

## 🤝 贡献

欢迎提交 Issue 和 Pull Request！

## 🙏 致谢

- [amq2api](https://github.com/mucsbr/amq2api) - Claude 消息格式转换参考
- FastAPI - 现代 Python Web 框架
- Amazon Q Developer - 底层 AI 服务
