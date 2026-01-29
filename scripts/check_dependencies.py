#!/usr/bin/env python3
"""
依赖安全扫描脚本
使用 pip-audit 检查 Python 依赖中的已知漏洞
"""

import subprocess
import sys
import os
from pathlib import Path

# 颜色代码
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
NC = '\033[0m'  # No Color

def log_info(msg):
    print(f"{GREEN}[INFO]{NC} {msg}")

def log_error(msg):
    print(f"{RED}[ERROR]{NC} {msg}")

def log_warning(msg):
    print(f"{YELLOW}[WARNING]{NC} {msg}")

def run_command(cmd, capture=True):
    """运行命令并返回结果"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=capture, 
            text=True,
            timeout=120
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def check_pip_audit():
    """检查并安装 pip-audit"""
    code, _, _ = run_command(f"{sys.executable} -m pip show pip-audit")
    if code != 0:
        log_warning("pip-audit not installed, installing...")
        code, _, err = run_command(f"{sys.executable} -m pip install pip-audit --quiet")
        if code != 0:
            log_error(f"Failed to install pip-audit: {err}")
            return False
        log_info("pip-audit installed successfully")
    return True

def scan_requirements(req_file, name):
    """扫描指定的 requirements 文件"""
    if not os.path.exists(req_file):
        log_warning(f"{name} not found: {req_file}")
        return True
    
    log_info(f"Scanning {name}...")
    
    # 使用 pip-audit 扫描
    cmd = f"{sys.executable} -m pip_audit -r \"{req_file}\" --desc"
    code, stdout, stderr = run_command(cmd)
    
    if code == 0:
        log_info(f"✓ No vulnerabilities found in {name}")
        return True
    else:
        log_error(f"✗ Issues found in {name}!")
        if stdout:
            print(stdout)
        if stderr:
            print(stderr)
        return False

def main():
    # 获取项目根目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    log_info(f"Project root: {project_root}")
    log_info("Starting dependency security scan...")
    
    # 检查 pip-audit
    if not check_pip_audit():
        sys.exit(1)
    
    all_passed = True
    
    # 扫描主项目依赖
    main_req = project_root / "requirements-production.txt"
    if not scan_requirements(str(main_req), "requirements-production.txt"):
        all_passed = False
    
    # 扫描 account-feeder 依赖
    feeder_req = project_root / "account-feeder" / "requirements.txt"
    if not scan_requirements(str(feeder_req), "account-feeder/requirements.txt"):
        all_passed = False
    
    print()
    if all_passed:
        log_info("=" * 50)
        log_info("Security scan completed successfully - No vulnerabilities found")
        log_info("=" * 50)
        sys.exit(0)
    else:
        log_error("=" * 50)
        log_error("Security scan found vulnerabilities - Please review and fix")
        log_error("=" * 50)
        sys.exit(1)

if __name__ == "__main__":
    main()
