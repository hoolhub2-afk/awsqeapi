@echo off
REM 国内服务器Docker构建优化脚本 (Windows版本)

echo 🚀 开始国内优化Docker构建...

REM 1. 清理旧的构建缓存
echo 🧹 清理Docker构建缓存...
docker system prune -f
docker builder prune -f

REM 2. 设置构建环境变量
set DOCKER_BUILDKIT=1
set COMPOSE_DOCKER_CLI_BUILD=1

REM 3. 执行构建
echo 📦 开始构建镜像...
docker compose build --parallel

REM 4. 缓存构建结果
echo 💾 缓存构建结果...
FOR /F "tokens=*" %%i IN ('docker compose images -q q2api') DO docker tag %%i q2api:latest

echo ✅ 构建完成！
echo 🎯 可以使用以下命令启动服务:
echo    docker compose up -d

pause