#!/bin/bash

# Q2API production deployment without Docker
# Usage: ./server.sh [start|stop|restart|status|logs|install]

set -e

APP_NAME="q2api"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$APP_DIR/$APP_NAME.pid"
LOG_DIR="$APP_DIR/logs"
LOG_FILE="$LOG_DIR/app.log"
WORKERS=${WORKERS:-4}
PORT=${PORT:-8000}
HOST=${HOST:-0.0.0.0}

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_python() {
    if command -v python3 >/dev/null 2>&1; then
        PYTHON="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON="python"
    else
        log_error "Python not installed"
        exit 1
    fi

    if command -v uv >/dev/null 2>&1; then
        RUNNER="uv run"
    else
        RUNNER="$PYTHON -m"
    fi
}

check_env() {
    if [[ ! -f "$APP_DIR/.env" ]]; then
        log_error ".env not found; configure environment first"
        exit 1
    fi

    # 检查 .env 文件权限
    if [[ -f "$APP_DIR/.env" ]]; then
        local env_perm
        if stat -c %a "$APP_DIR/.env" >/dev/null 2>&1; then
            env_perm=$(stat -c %a "$APP_DIR/.env")  # Linux
        else
            env_perm=$(stat -f %Lp "$APP_DIR/.env" 2>/dev/null || echo "644")  # macOS/BSD
        fi
        
        if [[ "$env_perm" != "600" && "$env_perm" != "400" ]]; then
            log_warning ".env has insecure permissions: $env_perm (should be 600 or 400)"
            log_info "Fixing .env permissions..."
            chmod 600 "$APP_DIR/.env" || true
        fi
    fi

    # shellcheck disable=SC1091
    source "$APP_DIR/.env"
    if [[ -z "$ADMIN_API_KEY" || -z "$ADMIN_PASSWORD" ]]; then
        log_error "ADMIN_API_KEY or ADMIN_PASSWORD missing"
        exit 1
    fi

    # master.key 管理（增强版）
    if [[ ! -f "$APP_DIR/master.key" ]]; then
        log_info "Generating master.key..."
        cd "$APP_DIR"
        $PYTHON - <<'PY'
import secrets
import os
import sys
import pathlib

# 验证系统熵源
if not os.path.exists("/dev/urandom"):
    print("ERROR: No secure random source available", file=sys.stderr)
    sys.exit(1)

# 生成密钥
key = secrets.token_bytes(64)
pathlib.Path("master.key").write_bytes(key)

# 设置安全权限
os.chmod("master.key", 0o600)
print("master.key generated with 600 permissions", file=sys.stderr)
PY
        
        if [[ $? -ne 0 ]]; then
            log_error "Failed to generate master.key"
            exit 1
        fi
        
        # 备份
        mkdir -p "$APP_DIR/backups"
        cp "$APP_DIR/master.key" "$APP_DIR/backups/master.key.$(date +%Y%m%d_%H%M%S).bak"
        chmod 600 "$APP_DIR/backups/master.key."*.bak 2>/dev/null || true
        log_success "master.key created and backed up"
    else
        # 检查现有 master.key 的权限
        local key_perm
        if stat -c %a "$APP_DIR/master.key" >/dev/null 2>&1; then
            key_perm=$(stat -c %a "$APP_DIR/master.key")  # Linux
        else
            key_perm=$(stat -f %Lp "$APP_DIR/master.key" 2>/dev/null || echo "644")  # macOS/BSD
        fi
        
        if [[ "$key_perm" != "600" && "$key_perm" != "400" ]]; then
            log_error "master.key has insecure permissions: $key_perm (must be 600 or 400)"
            log_info "Fixing master.key permissions..."
            chmod 600 "$APP_DIR/master.key" || {
                log_error "Failed to fix master.key permissions"
                exit 1
            }
            log_success "master.key permissions fixed"
        fi
        
        # 检查文件大小
        local key_size
        if stat -c %s "$APP_DIR/master.key" >/dev/null 2>&1; then
            key_size=$(stat -c %s "$APP_DIR/master.key")
        else
            key_size=$(stat -f %z "$APP_DIR/master.key" 2>/dev/null || echo "0")
        fi
        
        if [[ "$key_size" -lt 32 ]]; then
            log_error "master.key is too small ($key_size bytes, expected 64)"
            log_error "Please regenerate master.key or restore from backup"
            exit 1
        fi
    fi
}

is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if ps -p "$pid" >/dev/null 2>&1; then
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

start() {
    if is_running; then
        log_warning "Service already running (PID: $(cat $PID_FILE))"
        return 0
    fi

    check_python
    check_env

    log_info "Starting $APP_NAME..."
    mkdir -p "$LOG_DIR"
    cd "$APP_DIR"

    if command -v gunicorn >/dev/null 2>&1; then
        log_info "Using gunicorn ($WORKERS workers)..."
        nohup gunicorn app:app \
            -w $WORKERS \
            -k uvicorn.workers.UvicornWorker \
            -b $HOST:$PORT \
            --access-logfile "$LOG_DIR/access.log" \
            --error-logfile "$LOG_DIR/error.log" \
            >>"$LOG_FILE" 2>&1 &
    else
        log_info "Using uvicorn ($WORKERS workers)..."
        nohup $RUNNER uvicorn app:app \
            --host $HOST \
            --port $PORT \
            --workers $WORKERS \
            >>"$LOG_FILE" 2>&1 &
    fi

    echo $! >"$PID_FILE"
    sleep 3

    if is_running; then
        log_success "Service started"
        log_info "  PID: $(cat $PID_FILE)"
        log_info "  URL: http://$HOST:$PORT"
        log_info "  Logs: $LOG_FILE"
    else
        log_error "Service failed to start; check $LOG_FILE"
        exit 1
    fi
}

stop() {
    if ! is_running; then
        log_warning "Service not running"
        return 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    log_info "Stopping service (PID: $pid)..."
    kill "$pid" 2>/dev/null || true

    local count=0
    while ps -p "$pid" >/dev/null 2>&1 && [[ $count -lt 30 ]]; do
        sleep 1
        count=$((count + 1))
    done

    if ps -p "$pid" >/dev/null 2>&1; then
        log_warning "Force killing process..."
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$PID_FILE"
    log_success "Service stopped"
}

restart() {
    stop
    sleep 2
    start
}

status() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        log_success "Service running"
        log_info "  PID: $pid"
        log_info "  Port: $PORT"
        if curl -sf "http://localhost:$PORT/healthz" >/dev/null 2>&1; then
            log_success "  Health: ok"
        else
            log_warning "  Health: failing"
        fi
    else
        log_warning "Service not running"
    fi
}

logs() {
    if [[ -f "$LOG_FILE" ]]; then
        tail -f "$LOG_FILE"
    else
        log_error "Log file not found: $LOG_FILE"
    fi
}

install() {
    check_python
    log_info "Installing dependencies..."
    if command -v uv >/dev/null 2>&1; then
        uv sync
    elif [[ -f "$APP_DIR/requirements-production.txt" ]]; then
        $PYTHON -m pip install -r "$APP_DIR/requirements-production.txt"
    else
        log_error "requirements-production.txt not found"
        exit 1
    fi
    log_success "Dependencies installed"
}

show_help() {
    echo "$APP_NAME deployment script"
    echo ""
    echo "Commands:"
    echo "  start     Start service"
    echo "  stop      Stop service"
    echo "  restart   Restart service"
    echo "  status    Show status"
    echo "  logs      Tail logs"
    echo "  install   Install dependencies"
}

case "${1:-help}" in
start) start ;;
stop) stop ;;
restart) restart ;;
status) status ;;
logs) logs ;;
install) install ;;
*) show_help ;;
esac
