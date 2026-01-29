#!/bin/bash
# 验证所有代码修复
# 快速检查所有关键修复是否正常工作

set -e

echo "========================================="
echo "  q2api 代码修复验证脚本"
echo "========================================="
echo ""

PROJECT_ROOT="/e/SRC/AI-API/09/03/q2api"
cd "$PROJECT_ROOT" || exit 1

# 颜色定义
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

success_count=0
fail_count=0

# 检查函数
check() {
    local name="$1"
    local command="$2"

    echo -n "检查: $name ... "

    if eval "$command" > /dev/null 2>&1; then
        echo -e "${GREEN}✅ 通过${NC}"
        ((success_count++))
        return 0
    else
        echo -e "${RED}❌ 失败${NC}"
        ((fail_count++))
        return 1
    fi
}

# ========== 依赖检查 ==========
echo "========== 1. 依赖检查 =========="

check "Python版本 >= 3.10" "python --version | grep -E 'Python 3\.(1[0-9]|[2-9][0-9])'"
check "aiofiles已安装" "python -c 'import aiofiles'"
check "psutil已安装" "python -c 'import psutil'"
check "fastapi已安装" "python -c 'import fastapi'"

echo ""

# ========== 文件检查 ==========
echo "========== 2. 新增文件检查 =========="

check "分布式锁模块" "test -f src/core/distributed_lock.py"
check "错误检测器模块" "test -f src/core/error_detector.py"
check "异步文件工具" "test -f src/core/async_file_utils.py"
check "健康检查路由" "test -f src/routers/health.py"

echo ""

# ========== 代码修复检查 ==========
echo "========== 3. 代码修复检查 =========="

check "全局异常处理器已注册" "grep -q 'global_exception_handler' app.py"
check "信号处理器已添加" "grep -q 'setup_signal_handlers' app.py"
check "SecurityValidator已创建" "grep -q 'class SecurityValidator' src/core/security_utils.py"
check "DistributedLock已创建" "grep -q 'class DistributedLock' src/core/distributed_lock.py"
check "AccountErrorDetector已创建" "grep -q 'class AccountErrorDetector' src/core/error_detector.py"
check "健康检查路由已注册" "grep -q 'health.router' app.py"

echo ""

# ========== 配置检查 ==========
echo "========== 4. 配置文件检查 =========="

check ".env文件存在" "test -f .env"
check "锁目录存在" "test -d .locks"
check "日志目录存在" "test -d logs"
check "数据目录存在" "test -d data"

echo ""

# ========== 导入检查 ==========
echo "========== 5. Python导入检查 =========="

check "导入distributed_lock" "python -c 'from src.core.distributed_lock import get_lock_manager'"
check "导入error_detector" "python -c 'from src.core.error_detector import AccountErrorDetector'"
check "导入async_file_utils" "python -c 'from src.core.async_file_utils import AsyncFileManager'"
check "导入SecurityValidator" "python -c 'from src.core.security_utils import SecurityValidator'"

echo ""

# ========== 测试文件检查 ==========
echo "========== 6. 测试文件检查 =========="

check "安全验证器测试" "test -f tests/test_security_validator.py"
check "错误检测器测试" "test -f tests/test_error_detector.py"
check "分布式锁测试" "test -f tests/test_distributed_lock.py"
check "异步文件工具测试" "test -f tests/test_async_file_utils.py"

echo ""

# ========== 总结 ==========
echo "========================================="
echo "  验证总结"
echo "========================================="
echo -e "${GREEN}通过: $success_count${NC}"
echo -e "${RED}失败: $fail_count${NC}"
echo ""

if [ $fail_count -eq 0 ]; then
    echo -e "${GREEN}🎉 所有检查通过！代码修复验证成功！${NC}"
    echo ""
    echo "下一步："
    echo "  1. 运行测试: pytest tests/ -v"
    echo "  2. 启动服务: python run.py"
    echo "  3. 验证健康: curl http://localhost:8000/health"
    echo ""
    exit 0
else
    echo -e "${RED}⚠️  有 $fail_count 项检查失败，请查看上述输出${NC}"
    echo ""
    echo "常见解决方案："
    echo "  1. 安装依赖: pip install -e ."
    echo "  2. 创建目录: mkdir -p .locks data logs"
    echo "  3. 检查Python版本: python --version"
    echo ""
    exit 1
fi
