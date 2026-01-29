#!/bin/bash
# 数据库备份脚本
# 用法: ./backup_database.sh

set -e

# 配置
PROJECT_ROOT="/path/to/awsq"
BACKUP_DIR="${PROJECT_ROOT}/backups"
DATE=$(date +%Y%m%d_%H%M%S)
RETENTION_DAYS=30

# 日志函数
log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $1"
}

log "开始数据库备份..."

# 创建备份目录
mkdir -p "$BACKUP_DIR"

# 备份 SQLite 数据库
if [ -f "${PROJECT_ROOT}/data/database/data.sqlite3" ]; then
    log "备份 SQLite 数据库..."
    cp "${PROJECT_ROOT}/data/database/data.sqlite3" "${BACKUP_DIR}/data_${DATE}.sqlite3"
    
    # 压缩备份
    gzip "${BACKUP_DIR}/data_${DATE}.sqlite3"
    log "SQLite 备份完成: data_${DATE}.sqlite3.gz"
else
    log "警告: SQLite 数据库文件不存在"
fi

# 如果使用 PostgreSQL (根据 DATABASE_URL 判断)
if grep -q "^DATABASE_URL=.*postgres" "${PROJECT_ROOT}/.env" 2>/dev/null; then
    log "备份 PostgreSQL 数据库..."
    
    # 从 .env 读取数据库配置
    source <(grep -v '^#' "${PROJECT_ROOT}/.env" | grep DATABASE_URL)
    
    # 解析连接字符串 (简化版)
    DB_NAME=$(echo $DATABASE_URL | sed 's/.*\/\([^?]*\).*/\1/')
    DB_HOST=$(echo $DATABASE_URL | sed 's/.*@\([^:]*\):.*/\1/')
    DB_PORT=$(echo $DATABASE_URL | sed 's/.*:\([0-9]*\)\/.*/\1/')
    DB_USER=$(echo $DATABASE_URL | sed 's/.*:\/\/\([^:]*\):.*/\1/')
    
    # 执行备份
    PGPASSWORD=$DB_PASS pg_dump -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" | gzip > "${BACKUP_DIR}/postgres_${DATE}.sql.gz"
    log "PostgreSQL 备份完成: postgres_${DATE}.sql.gz"
fi

# 清理旧备份
log "清理 ${RETENTION_DAYS} 天前的旧备份..."
find "$BACKUP_DIR" -name "data_*.sqlite3.gz" -mtime +${RETENTION_DAYS} -delete
find "$BACKUP_DIR" -name "postgres_*.sql.gz" -mtime +${RETENTION_DAYS} -delete

# 统计备份文件
BACKUP_COUNT=$(ls -1 "$BACKUP_DIR" | wc -l)
BACKUP_SIZE=$(du -sh "$BACKUP_DIR" | cut -f1)

log "备份完成! 当前备份: ${BACKUP_COUNT} 个文件, 总大小: ${BACKUP_SIZE}"

# 可选: 上传到远程存储 (S3/OSS)
# if command -v aws &> /dev/null; then
#     log "上传备份到 S3..."
#     aws s3 cp "${BACKUP_DIR}/data_${DATE}.sqlite3.gz" s3://your-bucket/awsq-backups/
# fi

exit 0
