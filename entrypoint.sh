#!/bin/sh
set -e

# 日志函数（兼容 sh）
log_info() { echo "[INFO] $1"; }
log_success() { echo "[SUCCESS] $1"; }
log_warning() { echo "[WARNING] $1"; }
log_error() { echo "[ERROR] $1"; }

# Ensure writable mount points for the app user
log_info "Initializing directories..."
mkdir -p /app/logs /app/data /app/backups

# 尝试修复权限，如果失败则记录警告但继续运行
if ! chown -R q2api:q2api /app/logs /app/data /app/backups 2>/dev/null; then
    log_warning "Failed to change ownership of directories, application may have write issues"
    log_warning "This might happen when volumes are mounted from host with different permissions"
    # 尝试至少设置可写权限
    chmod -R 777 /app/logs /app/data /app/backups 2>/dev/null || true
fi

# 验证目录可写
for dir in /app/logs /app/data /app/backups; do
    if [ ! -w "$dir" ]; then
        log_error "Directory $dir is not writable"
        exit 1
    fi
done

log_success "Directory permissions verified, starting application..."

# Drop privileges and start the app
exec su -s /bin/sh q2api -c "$*"
