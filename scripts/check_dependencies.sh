#!/bin/bash
# 依赖安全扫描脚本
# 使用 pip-audit 检查已知漏洞

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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

# 检查 Python
if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON="python"
else
    log_error "Python not installed"
    exit 1
fi

# 检查 pip-audit 是否安装
if ! $PYTHON -m pip show pip-audit >/dev/null 2>&1; then
    log_warning "pip-audit not installed, installing..."
    $PYTHON -m pip install pip-audit --quiet || {
        log_error "Failed to install pip-audit"
        exit 1
    }
fi

log_info "Scanning dependencies for security vulnerabilities..."
cd "$PROJECT_ROOT"

# 扫描主项目依赖
if [[ -f "requirements-production.txt" ]]; then
    log_info "Scanning requirements-production.txt..."
    if $PYTHON -m pip_audit -r requirements-production.txt --desc --format json > /tmp/audit_results.json 2>&1; then
        log_success "✓ No vulnerabilities found in main dependencies"
    else
        log_error "✗ Vulnerabilities found!"
        cat /tmp/audit_results.json
        rm -f /tmp/audit_results.json
        exit 1
    fi
fi

# 扫描 account-feeder 依赖
if [[ -f "account-feeder/requirements.txt" ]]; then
    log_info "Scanning account-feeder/requirements.txt..."
    if $PYTHON -m pip_audit -r account-feeder/requirements.txt --desc --format json > /tmp/audit_feeder.json 2>&1; then
        log_success "✓ No vulnerabilities found in account-feeder dependencies"
    else
        log_warning "✗ Vulnerabilities found in account-feeder!"
        cat /tmp/audit_feeder.json
        rm -f /tmp/audit_feeder.json
    fi
fi

log_success "Security scan completed successfully"
