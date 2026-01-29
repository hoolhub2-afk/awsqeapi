#!/bin/bash

# 国内服务器Docker构建优化脚本
# 使用方法: ./scripts/build-cn.sh

set -e

echo "🚀 开始国内优化Docker构建..."

# 1. 配置Docker镜像加速器
echo "⚙️ 配置Docker镜像加速器..."
sudo mkdir -p /etc/docker
sudo cp docker/daemon.json /etc/docker/daemon.json
sudo systemctl restart docker

# 2. 清理旧的构建缓存
echo "🧹 清理Docker构建缓存..."
docker system prune -f
docker builder prune -f

# 3. 使用buildx进行优化构建
echo "🔨 使用buildx进行优化构建..."
docker buildx create --name cn-builder --use --bootstrap 2>/dev/null || docker buildx use cn-builder

# 4. 设置构建参数
export DOCKER_BUILDKIT=1
export COMPOSE_DOCKER_CLI_BUILD=1
export BUILDKIT_PROGRESS=plain

# 5. 执行构建
echo "📦 开始构建镜像..."
docker compose build --parallel

# 6. 推送镜像到本地缓存以便下次使用
echo "💾 缓存构建结果..."
docker tag $(docker compose images -q q2api) q2api:latest

echo "✅ 构建完成！"
echo "🎯 可以使用以下命令启动服务:"
echo "   docker compose up -d"