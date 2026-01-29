@echo off
REM 验证所有代码修复 - Windows版本
REM 快速检查所有关键修复是否正常工作

echo =========================================
echo   q2api 代码修复验证脚本 (Windows)
echo =========================================
echo.

cd /d E:\SRC\AI-API\09\03\q2api

set success_count=0
set fail_count=0

REM ========== 依赖检查 ==========
echo ========== 1. 依赖检查 ==========

python --version 2>nul
if %errorlevel% equ 0 (
    echo [√] Python已安装
    set /a success_count+=1
) else (
    echo [X] Python未安装
    set /a fail_count+=1
)

python -c "import aiofiles" 2>nul
if %errorlevel% equ 0 (
    echo [√] aiofiles已安装
    set /a success_count+=1
) else (
    echo [X] aiofiles未安装 - 运行: pip install aiofiles
    set /a fail_count+=1
)

python -c "import psutil" 2>nul
if %errorlevel% equ 0 (
    echo [√] psutil已安装
    set /a success_count+=1
) else (
    echo [X] psutil未安装
    set /a fail_count+=1
)

echo.

REM ========== 文件检查 ==========
echo ========== 2. 新增文件检查 ==========

if exist "src\core\distributed_lock.py" (
    echo [√] 分布式锁模块存在
    set /a success_count+=1
) else (
    echo [X] 分布式锁模块缺失
    set /a fail_count+=1
)

if exist "src\core\error_detector.py" (
    echo [√] 错误检测器模块存在
    set /a success_count+=1
) else (
    echo [X] 错误检测器模块缺失
    set /a fail_count+=1
)

if exist "src\core\async_file_utils.py" (
    echo [√] 异步文件工具存在
    set /a success_count+=1
) else (
    echo [X] 异步文件工具缺失
    set /a fail_count+=1
)

if exist "src\routers\health.py" (
    echo [√] 健康检查路由存在
    set /a success_count+=1
) else (
    echo [X] 健康检查路由缺失
    set /a fail_count+=1
)

echo.

REM ========== 目录检查 ==========
echo ========== 3. 目录检查 ==========

if exist ".locks" (
    echo [√] 锁目录存在
    set /a success_count+=1
) else (
    echo [!] 锁目录不存在，创建中...
    mkdir .locks
    if %errorlevel% equ 0 (
        echo [√] 锁目录已创建
        set /a success_count+=1
    ) else (
        echo [X] 无法创建锁目录
        set /a fail_count+=1
    )
)

if exist "logs" (
    echo [√] 日志目录存在
    set /a success_count+=1
) else (
    echo [√] 日志目录将自动创建
    set /a success_count+=1
)

if exist "data" (
    echo [√] 数据目录存在
    set /a success_count+=1
) else (
    echo [√] 数据目录将自动创建
    set /a success_count+=1
)

echo.

REM ========== Python导入检查 ==========
echo ========== 4. Python导入检查 ==========

python -c "from src.core.distributed_lock import get_lock_manager" 2>nul
if %errorlevel% equ 0 (
    echo [√] distributed_lock可导入
    set /a success_count+=1
) else (
    echo [X] distributed_lock导入失败
    set /a fail_count+=1
)

python -c "from src.core.error_detector import AccountErrorDetector" 2>nul
if %errorlevel% equ 0 (
    echo [√] error_detector可导入
    set /a success_count+=1
) else (
    echo [X] error_detector导入失败
    set /a fail_count+=1
)

python -c "from src.core.async_file_utils import AsyncFileManager" 2>nul
if %errorlevel% equ 0 (
    echo [√] async_file_utils可导入
    set /a success_count+=1
) else (
    echo [X] async_file_utils导入失败
    set /a fail_count+=1
)

python -c "from src.core.security_utils import SecurityValidator" 2>nul
if %errorlevel% equ 0 (
    echo [√] SecurityValidator可导入
    set /a success_count+=1
) else (
    echo [X] SecurityValidator导入失败
    set /a fail_count+=1
)

echo.

REM ========== 总结 ==========
echo =========================================
echo   验证总结
echo =========================================
echo 通过: %success_count%
echo 失败: %fail_count%
echo.

if %fail_count% equ 0 (
    echo [√] 所有检查通过！代码修复验证成功！
    echo.
    echo 下一步:
    echo   1. 运行测试: pytest tests/ -v
    echo   2. 启动服务: python run.py
    echo   3. 验证健康: curl http://localhost:8000/health
    echo.
    exit /b 0
) else (
    echo [X] 有 %fail_count% 项检查失败，请查看上述输出
    echo.
    echo 常见解决方案:
    echo   1. 安装依赖: pip install -e .
    echo   2. 创建目录: mkdir .locks
    echo   3. 检查Python版本: python --version
    echo.
    exit /b 1
)
