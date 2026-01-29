#!/bin/bash

# Q2API production deployment helper
# Usage: ./deploy.sh [init|update|restart|stop|logs|status|backup]

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[WARNING]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Docker Compose 命令（支持 v1 和 v2）
DOCKER_COMPOSE=""

check_docker() {
    command -v docker >/dev/null 2>&1 || { log_error "Docker not installed"; exit 1; }
    
    # 检测 Docker Compose 版本（v1: docker-compose, v2: docker compose）
    if command -v docker-compose >/dev/null 2>&1; then
        DOCKER_COMPOSE="docker-compose"
        log_info "Using Docker Compose v1 (docker-compose)"
    elif docker compose version >/dev/null 2>&1; then
        DOCKER_COMPOSE="docker compose"
        log_info "Using Docker Compose v2 (docker compose)"
    else
        log_error "Docker Compose not installed (neither v1 nor v2)"
        exit 1
    fi
}

check_env_file() {
    if [[ ! -f ".env" ]]; then
        log_warning "No .env found, copying from .env.example..."
        cp .env.example .env
        log_error "Edit .env before deploying (ADMIN_API_KEY, ADMIN_PASSWORD, JWT_SECRET, DB_PASSWORD, CORS_ORIGINS)"
        exit 1
    fi

    # 检查 .env 文件权限
    if [[ -f ".env" ]]; then
        local env_perm
        if stat -c %a .env >/dev/null 2>&1; then
            env_perm=$(stat -c %a .env)  # Linux
        else
            env_perm=$(stat -f %A .env 2>/dev/null || echo "644")  # macOS/BSD
        fi
        
        if [[ "$env_perm" != "600" && "$env_perm" != "400" ]]; then
            log_warning ".env has insecure permissions: $env_perm (should be 600 or 400)"
            log_info "Fixing .env permissions..."
            chmod 600 .env || true
        fi
    fi

    # shellcheck disable=SC1091
    source .env
    if [[ -z "$ADMIN_API_KEY" || -z "$ADMIN_PASSWORD" || -z "$JWT_SECRET" ]]; then
        log_error "Critical env vars missing; check .env"
        exit 1
    fi

    # master.key 管理（增强版）
    if [[ ! -f "master.key" ]]; then
        log_info "Generating master.key..."
        if command -v python3 >/dev/null 2>&1; then
            python3 - <<'PY'
import secrets
import os
import sys

# 验证系统熵源
if not os.path.exists("/dev/urandom"):
    print("ERROR: No secure random source available", file=sys.stderr)
    sys.exit(1)

# 生成密钥
key = secrets.token_bytes(64)
with open("master.key", "wb") as f:
    f.write(key)

# 设置安全权限
os.chmod("master.key", 0o600)
print("master.key generated with 600 permissions", file=sys.stderr)
PY
        else
            python - <<'PY'
import secrets
import os
import sys

# 验证系统熵源
if not os.path.exists("/dev/urandom"):
    print("ERROR: No secure random source available", file=sys.stderr)
    sys.exit(1)

# 生成密钥
key = secrets.token_bytes(64)
with open("master.key", "wb") as f:
    f.write(key)

# 设置安全权限
os.chmod("master.key", 0o600)
print("master.key generated with 600 permissions", file=sys.stderr)
PY
        fi
        
        if [[ $? -ne 0 ]]; then
            log_error "Failed to generate master.key"
            exit 1
        fi
        
        # 备份到 backups 目录
        mkdir -p backups
        cp master.key "backups/master.key.$(date +%Y%m%d_%H%M%S).bak"
        chmod 600 "backups/master.key."*.bak 2>/dev/null || true
        log_success "master.key created and backed up"
    else
        # 检查现有 master.key 的权限
        local key_perm
        if stat -c %a master.key >/dev/null 2>&1; then
            key_perm=$(stat -c %a master.key)  # Linux
        else
            key_perm=$(stat -f %A master.key 2>/dev/null || echo "644")  # macOS/BSD
        fi
        
        if [[ "$key_perm" != "600" && "$key_perm" != "400" ]]; then
            log_error "master.key has insecure permissions: $key_perm (must be 600 or 400)"
            log_info "Fixing master.key permissions..."
            chmod 600 master.key || {
                log_error "Failed to fix master.key permissions"
                exit 1
            }
            log_success "master.key permissions fixed"
        fi
        
        # 检查文件大小（应该是 64 字节）
        local key_size
        if stat -c %s master.key >/dev/null 2>&1; then
            key_size=$(stat -c %s master.key)
        else
            key_size=$(stat -f %z master.key 2>/dev/null || echo "0")
        fi
        
        if [[ "$key_size" -lt 32 ]]; then
            log_error "master.key is too small ($key_size bytes, expected 64)"
            log_error "Please regenerate master.key or restore from backup"
            exit 1
        fi
    fi

    if [[ -z "$DB_PASSWORD" || "$DB_PASSWORD" == "changeme" ]]; then
        log_error "DB_PASSWORD not set or insecure"
        exit 1
    fi

    [[ "$DEBUG" = "true" ]] && log_warning "DEBUG is true; set to false for production"
}

deploy_init() {
    log_info "Initializing deployment..."
    mkdir -p data logs backups

    if [[ -d "data/database" ]]; then
        log_info "Backing up existing data..."
        tar -czf "backups/backup_$(date +%Y%m%d_%H%M%S).tar.gz" data/
    fi

    log_info "Building and starting containers..."
    $DOCKER_COMPOSE down || true
    $DOCKER_COMPOSE build --no-cache
    $DOCKER_COMPOSE up -d

    log_info "Waiting for services..."
    sleep 15
    check_service_health
    log_success "Deployment initialized"
    show_service_info
}

deploy_update() {
    log_info "Updating deployment..."
    backup_database
    log_info "Pulling latest code..."
    git pull origin main || log_warning "Git pull failed; using local code"

    $DOCKER_COMPOSE build --no-cache
    $DOCKER_COMPOSE up -d
    sleep 15
    check_service_health
    log_success "Update completed"
}

deploy_restart() {
    log_info "Restarting services..."
    $DOCKER_COMPOSE restart
    sleep 10
    check_service_health
    log_success "Restart completed"
}

deploy_stop() {
    log_info "Stopping services..."
    $DOCKER_COMPOSE down
    log_success "Services stopped"
}

deploy_logs() {
    $DOCKER_COMPOSE logs -f "${1:-}"
}

check_service_health() {
    log_info "Checking health..."
    local max_retries=30
    local count=0

    while [ $count -lt $max_retries ]; do
        if curl -f http://localhost:${PORT:-8000}/healthz >/dev/null 2>&1; then
            log_success "App is healthy"
            break
        fi
        count=$((count + 1))
        echo -n "."
        sleep 2
    done

    if [[ $count -eq $max_retries ]]; then
        log_error "App failed to become healthy"
        $DOCKER_COMPOSE logs --tail=50
        return 1
    fi

    if $DOCKER_COMPOSE exec -T postgres pg_isready -U q2api >/dev/null 2>&1; then
        log_success "Database is healthy"
    else
        log_error "Database health check failed"
        return 1
    fi

    log_info "Container status:"
    $DOCKER_COMPOSE ps
}

show_service_info() {
    echo ""
    echo "=========================================="
    echo "Deployment successful"
    echo "=========================================="
    echo "App URL: http://localhost:${PORT:-8000}"
    echo "Console: http://localhost:${PORT:-8000}/console"
    echo "Health:  http://localhost:${PORT:-8000}/healthz"
    echo ""
}

backup_database() {
    log_info "Backing up database..."
    mkdir -p backups
    
    # 检查磁盘空间（至少需要 100MB）
    local required_space_mb=100
    local available_space_mb
    if df -m backups >/dev/null 2>&1; then
        available_space_mb=$(df -m backups | awk 'NR==2 {print $4}')
    else
        available_space_mb=1000  # 无法检测时假设有足够空间
    fi
    
    if [[ $available_space_mb -lt $required_space_mb ]]; then
        log_error "Insufficient disk space for backup (need ${required_space_mb}MB, have ${available_space_mb}MB)"
        return 1
    fi
    
    local backup_file="backups/db_backup_$(date +%Y%m%d_%H%M%S).sql"
    local backup_failed=0

    if $DOCKER_COMPOSE exec -T postgres pg_dump -U q2api q2api >"$backup_file" 2>/dev/null; then
        # 验证备份文件大小
        local backup_size
        if stat -c %s "$backup_file" >/dev/null 2>&1; then
            backup_size=$(stat -c %s "$backup_file")
        else
            backup_size=$(stat -f %z "$backup_file" 2>/dev/null || echo "0")
        fi
        
        if [[ $backup_size -lt 100 ]]; then
            log_error "Backup file is too small ($backup_size bytes), backup may be incomplete"
            backup_failed=1
        else
            log_success "Database backup: $backup_file ($(numfmt --to=iec $backup_size 2>/dev/null || echo "${backup_size} bytes"))"
        fi
    else
        log_error "Database backup command failed"
        backup_failed=1
    fi
    
    if [[ $backup_failed -eq 1 ]]; then
        log_error "Backup failed! Continuing may result in data loss."
        read -p "Continue anyway? [y/N] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            log_info "Aborting operation"
            return 1
        fi
        log_warning "Proceeding without valid backup..."
    fi
    
    return 0
}

show_help() {
    echo "Usage: $0 [command]"
    echo ""
    echo "Commands:"
    echo "  init     Initial deployment"
    echo "  update   Pull code and rebuild"
    echo "  restart  Restart services"
    echo "  stop     Stop services"
    echo "  logs     Tail logs (optionally specify service)"
    echo "  status   Check health"
    echo "  backup   Backup database"
}

main() {
    check_docker
    [[ ! -f "docker-compose.yml" ]] && { log_error "Run from project root"; exit 1; }

    case "${1:-help}" in
    init)
        check_env_file
        deploy_init
        ;;
    update)
        check_env_file
        deploy_update
        ;;
    restart)
        deploy_restart
        ;;
    stop)
        deploy_stop
        ;;
    logs)
        deploy_logs "$2"
        ;;
    status)
        check_service_health
        ;;
    backup)
        backup_database
        ;;
    *)
        show_help
        ;;
    esac
}

main "$@"
