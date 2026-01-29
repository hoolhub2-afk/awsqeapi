# 多阶段构建优化版本 - 用于生产环境
# 构建参数：是否使用中国镜像源（默认 false，可在构建时通过 --build-arg USE_CN_MIRROR=true 启用）
ARG USE_CN_MIRROR=false

# 阶段1: 构建依赖
FROM python:3.11-slim AS builder

# 设置环境变量优化
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 重新声明 ARG（FROM 后需要重新声明）
ARG USE_CN_MIRROR

# 安装系统依赖，根据构建参数选择镜像源
RUN set -ex && \
    if [ "$USE_CN_MIRROR" = "true" ]; then \
        echo "Using China mirrors..."; \
        APT_MIRROR="mirrors.aliyun.com"; \
    else \
        echo "Using default mirrors..."; \
        APT_MIRROR="deb.debian.org"; \
    fi && \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        build-essential \
        && rm -rf /var/lib/apt/lists/* \
        && apt-get clean

# 创建虚拟环境并安装依赖
COPY requirements-production.txt .
ARG USE_CN_MIRROR
RUN set -ex && \
    python -m venv /opt/venv && \
    if [ "$USE_CN_MIRROR" = "true" ]; then \
        /opt/venv/bin/pip install --no-cache-dir \
            --default-timeout=180 \
            --retries=15 \
            -i https://mirrors.aliyun.com/pypi/simple/ \
            --trusted-host mirrors.aliyun.com \
            -r requirements-production.txt; \
    else \
        /opt/venv/bin/pip install --no-cache-dir \
            --default-timeout=180 \
            --retries=15 \
            -r requirements-production.txt; \
    fi

# 阶段2: 运行时环境
FROM python:3.11-slim AS production

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH"

# 重新声明 ARG
ARG USE_CN_MIRROR

# 只安装运行时必需的系统包
RUN set -ex && \
    if [ "$USE_CN_MIRROR" = "true" ]; then \
        APT_MIRROR="mirrors.aliyun.com"; \
    else \
        APT_MIRROR="deb.debian.org"; \
    fi && \
    if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ca-certificates \
        && rm -rf /var/lib/apt/lists/* \
        && apt-get clean

# 创建非root用户
RUN useradd -m -u 1000 q2api && \
    mkdir -p /app/logs /app/data /app/backups && \
    chown -R q2api:q2api /app

# 从构建阶段复制虚拟环境
COPY --from=builder /opt/venv /opt/venv

# 复制应用代码
WORKDIR /app
COPY --chown=q2api:q2api *.py ./
COPY --chown=q2api:q2api src ./src
COPY --chown=q2api:q2api templates ./templates
COPY --chown=q2api:q2api frontend ./frontend
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x /app/entrypoint.sh

# 保持root用户权限用于启动时权限修正
EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/healthz || exit 1

# 启动命令
ENTRYPOINT ["/app/entrypoint.sh"]
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]