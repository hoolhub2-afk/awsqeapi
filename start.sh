#!/bin/bash

# Q2API local development start script

set -e

PID_FILE="q2api.pid"
LOG_FILE="logs/app.log"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

check_running() {
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
    if check_running; then
        log_warning "Service already running (PID: $(cat $PID_FILE))"
        exit 0
    fi

    log_info "Starting service..."
    mkdir -p logs

    # 使用 run.py 启动，自动从 .env 读取日志级别等配置
    nohup uv run python run.py --reload >"$LOG_FILE" 2>&1 &
    echo $! >"$PID_FILE"

    sleep 2

    if check_running; then
        log_success "Service started (PID: $(cat $PID_FILE))"
        log_info "Visit: http://localhost:8000"
        log_info "Logs: tail -f $LOG_FILE"
    else
        log_error "Service failed to start; check $LOG_FILE"
        exit 1
    fi
}

stop() {
    if ! check_running; then
        log_warning "Service not running"
        exit 0
    fi

    local pid
    pid=$(cat "$PID_FILE")
    log_info "Stopping service (PID: $pid)..."
    kill "$pid"
    rm -f "$PID_FILE"
    log_success "Service stopped"
}

restart() {
    stop
    sleep 1
    start
}

status() {
    if check_running; then
        log_success "Service running (PID: $(cat $PID_FILE))"
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

case "${1:-start}" in
start)
    start
    ;;
stop)
    stop
    ;;
restart)
    restart
    ;;
status)
    status
    ;;
logs)
    logs
    ;;
*)
    echo "Usage: $0 {start|stop|restart|status|logs}"
    exit 1
    ;;
esac
