# 生产环境部署指南

## 部署前准备

### 1. 修改敏感配置 (必须)

```bash
cd /path/to/awsq

# 生成新的密钥
# ADMIN_API_KEY
openssl rand -hex 32

# MASTER_KEY
python3 -c "import secrets,base64;print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"

# JWT_SECRET
openssl rand -hex 64

# ADMIN_PASSWORD
python3 -c "import secrets,string;print(''.join(secrets.choice(string.ascii_letters+string.digits+string.punctuation) for _ in range(20)))"
```

编辑 `.env` 文件，替换以下配置：
```bash
# 使用上面生成的值替换
ADMIN_API_KEY=<生成的32位hex>
MASTER_KEY=<生成的base64密钥>
JWT_SECRET=<生成的64位hex>
ADMIN_PASSWORD=<生成的强密码>

# 生产环境配置
DEBUG="false"
FORCE_HTTPS="true"
CORS_ORIGINS="https://yourdomain.com"
LOG_LEVEL="INFO"

# 可选: 限制管理员IP
ADMIN_IP_WHITELIST="your.ip.address"
```

### 2. 安装依赖

#### 使用 Docker (推荐)
```bash
# 构建镜像
docker-compose build

# 启动服务
docker-compose up -d
```

#### 直接运行
```bash
# 安装 Python 依赖
python3 -m venv venv
source venv/bin/activate
pip install -r requirements-production.txt

# 启动服务
uvicorn app:app --host 0.0.0.0 --port 8000 --workers 4
```

### 3. 配置 Nginx + HTTPS

#### 安装 Nginx 和 Certbot
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install nginx certbot python3-certbot-nginx

# CentOS/RHEL
sudo yum install nginx certbot python3-certbot-nginx
```

#### 获取 SSL 证书
```bash
sudo certbot --nginx -d yourdomain.com
```

#### 配置 Nginx
```bash
# 复制配置模板
sudo cp nginx.conf.example /etc/nginx/sites-available/awsq

# 修改域名
sudo sed -i 's/yourdomain.com/your-actual-domain.com/g' /etc/nginx/sites-available/awsq

# 启用配置
sudo ln -s /etc/nginx/sites-available/awsq /etc/nginx/sites-enabled/

# 测试配置
sudo nginx -t

# 重启 Nginx
sudo systemctl restart nginx
```

### 4. 配置数据库备份

```bash
# 复制备份脚本
cp scripts/backup_database.sh /usr/local/bin/backup_awsq
chmod +x /usr/local/bin/backup_awsq

# 修改脚本中的路径
sudo vi /usr/local/bin/backup_awsq
# 修改 PROJECT_ROOT="/path/to/awsq"

# 添加到 crontab (每天凌晨2点备份)
crontab -e
# 添加以下行:
0 2 * * * /usr/local/bin/backup_awsq >> /var/log/awsq_backup.log 2>&1
```

### 5. 配置 systemd 服务 (可选)

创建服务文件:
```bash
sudo vi /etc/systemd/system/awsq.service
```

内容:
```ini
[Unit]
Description=AWSQ API Service
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/path/to/awsq
Environment="PATH=/path/to/awsq/venv/bin"
ExecStart=/path/to/awsq/venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000 --workers 4
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

启用服务:
```bash
sudo systemctl daemon-reload
sudo systemctl enable awsq
sudo systemctl start awsq
sudo systemctl status awsq
```

---

## 部署验证

### 1. 健康检查
```bash
curl https://yourdomain.com/health
# 预期输出: {"status":"healthy",...}
```

### 2. API 测试
```bash
# 测试 OpenAI 兼容端点
curl -X POST https://yourdomain.com/v1/chat/completions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4",
    "messages": [{"role": "user", "content": "Hello"}]
  }'
```

### 3. 管理控制台
访问 `https://yourdomain.com` 并使用 ADMIN_PASSWORD 登录

### 4. SSL 测试
```bash
# 检查 SSL 配置
curl -I https://yourdomain.com

# 或使用在线工具
# https://www.ssllabs.com/ssltest/
```

---

## 监控和维护

### 1. 查看日志
```bash
# 应用日志
tail -f /path/to/awsq/logs/app.log

# Nginx 访问日志
sudo tail -f /var/log/nginx/awsq_access.log

# Nginx 错误日志
sudo tail -f /var/log/nginx/awsq_error.log

# systemd 日志
sudo journalctl -u awsq -f
```

### 2. 监控指标
```bash
# 检查服务状态
systemctl status awsq
systemctl status nginx

# 检查端口
netstat -tlnp | grep 8000
netstat -tlnp | grep 443

# 检查进程
ps aux | grep uvicorn

# 检查内存使用
docker stats  # 如果使用 Docker
```

### 3. 性能监控

使用 Prometheus + Grafana (可选):
```bash
# 安装 Prometheus
docker run -d -p 9090:9090 prom/prometheus

# 配置 FastAPI 暴露指标
pip install prometheus-fastapi-instrumentator
```

### 4. 告警配置

配置邮件/短信告警 (根据实际需求):
- 服务宕机告警
- CPU/内存超过 80% 告警
- 错误率超过 1% 告警
- 磁盘空间不足告警

---

## 故障排查

### 问题 1: 服务无法启动
```bash
# 检查日志
sudo journalctl -u awsq -n 50

# 检查端口占用
sudo lsof -i :8000

# 检查配置文件
python3 -c "from src.core.env import env_loaded; print('Config OK')"
```

### 问题 2: 502 Bad Gateway
```bash
# 检查后端服务是否运行
systemctl status awsq

# 检查 Nginx 配置
sudo nginx -t

# 检查 Nginx 错误日志
sudo tail -f /var/log/nginx/error.log
```

### 问题 3: HTTPS 证书问题
```bash
# 检查证书有效期
sudo certbot certificates

# 续期证书
sudo certbot renew

# 测试自动续期
sudo certbot renew --dry-run
```

### 问题 4: 数据库连接失败
```bash
# 检查数据库文件权限
ls -la /path/to/awsq/data/database/

# 检查数据库连接
sqlite3 data/database/data.sqlite3 "SELECT COUNT(*) FROM accounts;"

# 如果使用 PostgreSQL
psql -h localhost -U username -d dbname -c "SELECT 1;"
```

---

## 性能优化

### 1. Nginx 优化
```nginx
# /etc/nginx/nginx.conf
worker_processes auto;
worker_rlimit_nofile 65535;

events {
    worker_connections 4096;
    use epoll;
}

http {
    # 开启 gzip 压缩
    gzip on;
    gzip_types text/plain text/css application/json application/javascript;
    gzip_min_length 1000;
    
    # 连接保活
    keepalive_timeout 65;
    keepalive_requests 100;
}
```

### 2. FastAPI 优化
```bash
# 增加 worker 数量 (CPU 核心数 * 2 + 1)
uvicorn app:app --workers 8 --host 127.0.0.1 --port 8000

# 使用 Gunicorn + Uvicorn
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker
```

### 3. 数据库优化

对于 SQLite:
```bash
# 定期优化数据库
sqlite3 data/database/data.sqlite3 "VACUUM; ANALYZE;"
```

对于 PostgreSQL:
```bash
# 配置连接池
DATABASE_URL="postgresql://user:pass@host/db?pool_size=20&max_overflow=0"
```

### 4. 缓存优化

添加 Redis 缓存 (可选):
```python
# 安装依赖
pip install redis

# 配置缓存
REDIS_URL="redis://localhost:6379/0"
```

---

## 安全加固

### 1. 防火墙配置
```bash
# UFW (Ubuntu)
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# 或 firewalld (CentOS)
sudo firewall-cmd --permanent --add-service=http
sudo firewall-cmd --permanent --add-service=https
sudo firewall-cmd --reload
```

### 2. Fail2ban 防暴力破解
```bash
# 安装 Fail2ban
sudo apt install fail2ban

# 配置 Nginx 规则
sudo vi /etc/fail2ban/jail.local
```

```ini
[nginx-limit-req]
enabled = true
filter = nginx-limit-req
logpath = /var/log/nginx/awsq_error.log
maxretry = 5
bantime = 3600
```

### 3. 定期更新
```bash
# 系统更新
sudo apt update && sudo apt upgrade

# Python 依赖更新
pip install --upgrade -r requirements-production.txt

# 检查安全漏洞
pip install safety
safety check
```

---

## 备份和恢复

### 恢复数据库
```bash
# 恢复 SQLite
gunzip -c backups/data_20251210.sqlite3.gz > data/database/data.sqlite3

# 恢复 PostgreSQL
gunzip -c backups/postgres_20251210.sql.gz | psql -h localhost -U username dbname
```

### 灾难恢复计划
1. 保持至少 30 天的数据库备份
2. 定期测试恢复流程
3. 备份 .env 配置文件 (脱敏后)
4. 文档化恢复步骤

---

## 扩展和升级

### 水平扩展
```bash
# 使用 Docker Swarm 或 Kubernetes
docker swarm init
docker stack deploy -c docker-compose.yml awsq
```

### 滚动更新
```bash
# 1. 构建新版本
docker build -t awsq:v2.1 .

# 2. 更新服务
docker service update --image awsq:v2.1 awsq_app

# 3. 验证
curl https://yourdomain.com/health
```

---

## 合规性

### GDPR 合规
- 用户数据加密存储 ✅
- 支持数据导出
- 支持数据删除
- 访问日志记录

### 安全审计
```bash
# 定期审计日志
grep "ERROR\|CRITICAL" logs/app.log | tail -100

# 检查异常登录
grep "login\|auth" /var/log/nginx/awsq_access.log | grep -v "200"

# 审计账号变更
grep "account" logs/app.log | grep "create\|delete\|update"
```

---

## 联系和支持

遇到问题请查阅:
- CODE_REVIEW_COMPREHENSIVE.md - 代码审查
- FIXES_APPLIED.md - 已修复问题
- UPGRADE_GUIDE.md - 升级指南
- PRODUCTION_READINESS_CHECKLIST.md - 就绪性清单

---

部署完成后请填写:
- 部署日期: ____________
- 部署人员: ____________
- 域名: ____________
- 服务器: ____________
- 备份策略: ____________
