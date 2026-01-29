"""
高级安全模块 - 军用级API密钥安全管理系统
提供极其安全且难破解的API密钥管理功能
支持数据库持久化
"""

import os
import hashlib

# 确保 .env 已加载
from src.core.env import env_loaded  # noqa: F401
import hmac
import time
import secrets
import base64
import ipaddress
import threading
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple, TYPE_CHECKING
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from enum import Enum
import json
import logging

import string
from cryptography.fernet import Fernet, InvalidToken

if TYPE_CHECKING:
    from ..core.database import DatabaseBackend

# 设置安全日志
security_logger = logging.getLogger('advanced_security')

class SecurityLevel(Enum):
    """安全级别枚举"""
    DEVELOPMENT = "development"
    PRODUCTION = "production"
    MILITARY = "military"

class KeyStatus(Enum):
    """密钥状态枚举"""
    ACTIVE = "active"
    INACTIVE = "inactive"
    COMPROMISED = "compromised"
    EXPIRED = "expired"

@dataclass
class SecureKeyInfo:
    """安全密钥信息"""
    key_id: str
    key_hash: str
    salt: str
    created_at: datetime
    expires_at: Optional[datetime]
    last_used: Optional[datetime]
    usage_count: int
    max_uses: Optional[int]
    allowed_ips: List[str]
    allowed_user_agents: List[str]
    rate_limit_per_minute: int
    status: KeyStatus
    security_level: SecurityLevel
    metadata: Dict[str, Any]
    allowed_account_ids: List[str] = field(default_factory=list)
    default_account_id: Optional[str] = None
    encrypted_key: Optional[str] = None  # 加密存储的原始密钥

class AdvancedKeyManager:
    """高级密钥管理器 - 军用级安全，支持数据库持久化"""

    def __init__(self, security_level: SecurityLevel = SecurityLevel.PRODUCTION, db: Optional["DatabaseBackend"] = None):
        self.security_level = security_level
        self.db = db  # 数据库实例（可选）
        self.master_key = self._get_or_create_master_key()
        self._fernet = self._build_fernet(self.master_key)
        self._encryption_prefix = "enc-v1:"
        self.keys: Dict[str, SecureKeyInfo] = {}
        self.failed_attempts: Dict[str, List[datetime]] = {}
        self.rate_limits: Dict[str, List[float]] = {}
        self.key_rotation_history: List[Dict[str, Any]] = []
        self.key_lookup: Dict[str, str] = {}  # lookup_hash -> key_id
        self._lock = threading.RLock()

        # 安全配置
        self.max_failed_attempts = 5 if security_level != SecurityLevel.MILITARY else 3
        self.block_duration_minutes = 30 if security_level != SecurityLevel.MILITARY else 60
        self.key_lifetime_days = 180  # 统一设置为180天
        # 从环境变量读取默认速率限制
        env_rate_limit = int(os.getenv("RATE_LIMIT_PER_MINUTE", "300"))
        self.default_rate_limit = env_rate_limit if security_level != SecurityLevel.MILITARY else env_rate_limit // 2

        if db:
            security_logger.info(f"高级密钥管理器初始化完成，安全级别: {security_level.value}, 数据库持久化: 启用 ✓")
        else:
            security_logger.info(f"高级密钥管理器初始化完成，安全级别: {security_level.value}, 数据库持久化: 待设置...")

    def set_database(self, db: "DatabaseBackend") -> None:
        """设置数据库实例（用于延迟初始化）"""
        self.db = db
        security_logger.info(f"高级密钥管理器数据库持久化: 启用 ✓")

    def _load_master_key_from_env(self) -> Optional[bytes]:
        """尝试从 MASTER_KEY 环境变量读取密钥"""
        raw_value = os.getenv("MASTER_KEY")
        if not raw_value:
            return None
        raw_value = raw_value.strip()

        # 1) urlsafe base64
        try:
            padded = raw_value + "=" * (-len(raw_value) % 4)
            decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
            if len(decoded) >= 32:
                return decoded
        except (ValueError, base64.binascii.Error) as exc:
            security_logger.debug("MASTER_KEY base64 decode failed: %s", exc)

        # 2) hex
        try:
            decoded = bytes.fromhex(raw_value)
            if len(decoded) >= 32:
                return decoded
        except ValueError as exc:
            security_logger.debug("MASTER_KEY hex decode failed: %s", exc)

        # 3) 直接作为字符串
        decoded = raw_value.encode("utf-8")
        if len(decoded) >= 32:
            return decoded

        raise ValueError("MASTER_KEY environment variable must be at least 32 bytes, you can use base64 or hex encoding")

    def _get_or_create_master_key(self) -> bytes:
        """获取或创建主密钥"""
        try:
            env_key = self._load_master_key_from_env()
            if env_key:
                security_logger.info("MASTER_KEY 已从环境变量加载")
                return env_key
        except ValueError as exc:
            security_logger.error(f"MASTER_KEY 配置错误: {exc}")
            raise

        key_path = Path(os.getenv("MASTER_KEY_PATH", "master.key"))
        if key_path.exists():
            try:
                return key_path.read_bytes()
            except Exception as e:
                security_logger.error(f"读取主密钥失败: {e}")
                raise

        master_key = secrets.token_bytes(64)  # 512位主密钥

        try:
            key_path.write_bytes(master_key)
            try:
                os.chmod(key_path, 0o600)
            except (OSError, AttributeError) as exc:
                # Windows 等平台可能不支持 chmod，忽略
                security_logger.debug("chmod master key skipped: %s", exc)
            security_logger.warning(
                "MASTER_KEY 未设置，已在 %s 生成开发环境专用密钥。生产环境请改用环境变量。",
                key_path.resolve()
            )
        except (OSError, IOError) as e:
            security_logger.warning(f"保存主密钥失败（将使用内存中的随机密钥）: {e}")

        return master_key

    def _build_fernet(self, master_key: bytes) -> Fernet:
        """基于 master key 构建 Fernet 实例"""
        digest = hashlib.sha256(master_key).digest()
        fernet_key = base64.urlsafe_b64encode(digest)
        return Fernet(fernet_key)

    def _encrypt_key(self, api_key: str) -> str:
        """使用 Fernet 加密 API 密钥"""
        token = self._fernet.encrypt(api_key.encode('utf-8')).decode('utf-8')
        return f"{self._encryption_prefix}{token}"

    def _decrypt_key_with_metadata(self, encrypted_key: Optional[str]) -> Tuple[Optional[str], bool]:
        """解密API密钥，同时返回是否需要升级旧格式"""
        if not encrypted_key:
            return None, False

        if encrypted_key.startswith(self._encryption_prefix):
            token = encrypted_key[len(self._encryption_prefix):].encode('utf-8')
            try:
                plaintext = self._fernet.decrypt(token).decode('utf-8')
                return plaintext, False
            except InvalidToken as exc:
                security_logger.error(f"解密密钥失败（格式损坏）: {exc}")
                return None, False

        # Legacy fallback（旧版 XOR 实现）
        plaintext = self._legacy_decrypt_key(encrypted_key)
        if plaintext:
            security_logger.warning("检测到旧版密钥加密格式，将在加载后自动升级")
            return plaintext, True
        return None, False

    def _decrypt_key(self, encrypted_key: str) -> Optional[str]:
        """对外兼容的解密接口"""
        plaintext, _ = self._decrypt_key_with_metadata(encrypted_key)
        return plaintext

    def _legacy_decrypt_key(self, encrypted_key: str) -> Optional[str]:
        """兼容旧版 XOR 加密的解密逻辑

        安全警告: XOR 加密是极弱的加密方式，仅用于向后兼容
        - 旧格式密钥会在 load_keys_from_db 时自动升级为 AES-GCM 加密
        - 如果发现旧格式密钥，会记录警告日志
        - 建议定期检查日志确保所有密钥已升级
        """
        try:
            data = base64.b64decode(encrypted_key.encode('utf-8'))
            if len(data) <= 16:
                return None
            encrypted = data[16:]
            aes_key = self.master_key[:32]
            if not aes_key:
                return None
            expanded_key = (aes_key * (len(encrypted) // len(aes_key) + 1))[:len(encrypted)]
            decrypted = bytes(a ^ b for a, b in zip(encrypted, expanded_key))
            # 记录警告：发现使用旧版弱加密的密钥
            security_logger.warning(
                "检测到旧版 XOR 加密密钥，将自动升级为 AES-GCM 加密。"
                "如果此警告持续出现，请检查密钥升级是否成功。"
            )
            return decrypted.decode('utf-8')
        except Exception as e:
            security_logger.error(f"旧版密钥解密失败: {e}")
            return None

    async def _upgrade_legacy_encrypted_key(self, key_info: SecureKeyInfo, plaintext: str) -> None:
        """将旧格式密钥重新加密并写回数据库"""
        try:
            new_cipher = self._encrypt_key(plaintext)
            key_info.encrypted_key = new_cipher
            if self.db:
                await self.db.execute(
                    "UPDATE secure_keys SET encrypted_key=? WHERE key_id=?",
                    (new_cipher, key_info.key_id)
                )
            security_logger.info(f"密钥加密格式已升级: {key_info.key_id}")
        except Exception as exc:
            security_logger.error(f"升级密钥加密格式失败 {key_info.key_id}: {exc}")

    def _calculate_lookup_hash(self, api_key: str) -> str:
        """计算用于查找的哈希值 (HMAC-SHA256)"""
        return hmac.new(self.master_key, api_key.encode('utf-8'), hashlib.sha256).hexdigest()

    def generate_secure_key(self,
                          expires_in_days: Optional[int] = None,
                          max_uses: Optional[int] = None,
                          allowed_ips: Optional[List[str]] = None,
                          allowed_user_agents: Optional[List[str]] = None,
                          rate_limit: Optional[int] = None,
                          metadata: Optional[Dict[str, Any]] = None,
                          allowed_account_ids: Optional[List[str]] = None,
                          default_account_id: Optional[str] = None) -> Tuple[str, str]:
        """
        生成极其安全的API密钥 (sk- + 48位字母数字)

        返回: (key_id, api_key)
        注意: 如果启用了数据库持久化，需要在调用后手动调用 save_key_to_db() 方法
        """
        with self._lock:
            # 生成密钥ID (内部标识)
            key_id = secrets.token_hex(16)  # 32字符密钥ID
            salt = secrets.token_hex(32)    # 64字符盐值

            # 生成符合严格格式的API密钥: sk- + 48位字母数字
            # 总长度 51 字符
            random_chars = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(48))
            api_key = f"sk-{random_chars}"

            # 计算密钥哈希用于存储 (验证用)
            key_hash = self._hash_key(api_key, salt)

            # 计算查找哈希 (索引用)
            lookup_hash = self._calculate_lookup_hash(api_key)

            # 加密原始密钥用于存储
            encrypted_key = self._encrypt_key(api_key)

            # 设置过期时间
            expires_in_days = expires_in_days or self.key_lifetime_days
            expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

            allowed_accounts: List[str] = []
            if allowed_account_ids:
                allowed_accounts = [
                    acc.strip() for acc in allowed_account_ids
                    if isinstance(acc, str) and acc.strip()
                ]
            if default_account_id:
                default_account_id = default_account_id.strip()
                if default_account_id and default_account_id not in allowed_accounts:
                    allowed_accounts.append(default_account_id)

            # 创建密钥信息
            key_info = SecureKeyInfo(
                key_id=key_id,
                key_hash=key_hash,
                salt=salt,
                created_at=datetime.now(timezone.utc),
                expires_at=expires_at,
                last_used=None,
                usage_count=0,
                max_uses=max_uses,
                allowed_ips=allowed_ips or [],
                allowed_user_agents=allowed_user_agents or [],
                rate_limit_per_minute=rate_limit or self.default_rate_limit,
                status=KeyStatus.ACTIVE,
                security_level=self.security_level,
                metadata=metadata or {},
                allowed_account_ids=allowed_accounts,
                default_account_id=default_account_id,
                encrypted_key=encrypted_key
            )

            self.keys[key_id] = key_info
            self.key_lookup[lookup_hash] = key_id

            security_logger.info(f"安全密钥已生成: {key_id}, 格式符合严格规范")

            return key_id, api_key

    async def save_key_to_db(self, key_id: str) -> bool:
        """异步保存密钥到数据库"""
        if not self.db:
            return False

        key_info = self.keys.get(key_id)
        if not key_info:
            return False

        try:
            await self.db.execute("""
                INSERT INTO secure_keys (
                    key_id, key_hash, salt, encrypted_key, created_at, expires_at, last_used,
                    usage_count, max_uses, allowed_ips, allowed_user_agents,
                    allowed_accounts, default_account_id,
                    rate_limit_per_minute, status, security_level, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key_info.key_id,
                key_info.key_hash,
                key_info.salt,
                key_info.encrypted_key,
                key_info.created_at.isoformat(),
                key_info.expires_at.isoformat() if key_info.expires_at else None,
                key_info.last_used.isoformat() if key_info.last_used else None,
                key_info.usage_count,
                key_info.max_uses,
                json.dumps(key_info.allowed_ips),
                json.dumps(key_info.allowed_user_agents),
                json.dumps(key_info.allowed_account_ids),
                key_info.default_account_id,
                key_info.rate_limit_per_minute,
                key_info.status.value,
                key_info.security_level.value,
                json.dumps(key_info.metadata)
            ))
            security_logger.info(f"密钥已保存到数据库: {key_id}")
            return True
        except Exception as e:
            security_logger.error(f"保存密钥到数据库失败: {e}")
            return False

    async def load_keys_from_db(self) -> int:
        """从数据库加载所有活跃密钥，返回加载的密钥数量"""
        if not self.db:
            security_logger.warning("数据库未配置，无法加载密钥")
            return 0

        try:
            rows = await self.db.fetchall("""
                SELECT * FROM secure_keys WHERE status = 'active'
            """)

            loaded_count = 0
            for row in rows:
                try:
                    # 解析时间并确保有时区信息
                    def parse_datetime_utc(val: str | None) -> datetime | None:
                        if not val:
                            return None
                        dt = datetime.fromisoformat(val)
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        return dt

                    key_info = SecureKeyInfo(
                        key_id=row['key_id'],
                        key_hash=row['key_hash'],
                        salt=row['salt'],
                        created_at=parse_datetime_utc(row['created_at']) or datetime.now(timezone.utc),
                        expires_at=parse_datetime_utc(row['expires_at']),
                        last_used=parse_datetime_utc(row['last_used']),
                        usage_count=row['usage_count'] or 0,
                        max_uses=row['max_uses'],
                        allowed_ips=json.loads(row['allowed_ips']) if row['allowed_ips'] else [],
                        allowed_user_agents=json.loads(row['allowed_user_agents']) if row['allowed_user_agents'] else [],
                        allowed_account_ids=json.loads(row['allowed_accounts']) if row.get('allowed_accounts') else [],
                        default_account_id=row.get('default_account_id'),
                        rate_limit_per_minute=row['rate_limit_per_minute'] or self.default_rate_limit,
                        status=KeyStatus(row['status']),
                        security_level=SecurityLevel(row['security_level']),
                        metadata=json.loads(row['metadata']) if row['metadata'] else {},
                        encrypted_key=row.get('encrypted_key')
                    )

                    # 检查是否过期
                    if key_info.expires_at and datetime.now(timezone.utc) > key_info.expires_at:
                        key_info.status = KeyStatus.EXPIRED
                        await self.update_key_status_in_db(key_info.key_id, KeyStatus.EXPIRED)
                        security_logger.info(f"密钥已过期: {key_info.key_id}")
                        continue

                    with self._lock:
                        self.keys[key_info.key_id] = key_info

                    decrypted_key, needs_upgrade = self._decrypt_key_with_metadata(key_info.encrypted_key)
                    if decrypted_key:
                        lookup_hash = self._calculate_lookup_hash(decrypted_key)
                        self.key_lookup[lookup_hash] = key_info.key_id
                        if needs_upgrade:
                            await self._upgrade_legacy_encrypted_key(key_info, decrypted_key)

                    loaded_count += 1
                except Exception as e:
                    security_logger.error(f"加载密钥失败 {row.get('key_id', 'unknown')}: {e}")

            security_logger.info(f"从数据库加载了 {loaded_count} 个活跃密钥")
            return loaded_count
        except Exception as e:
            security_logger.error(f"从数据库加载密钥失败: {e}")
            return 0

    async def update_key_status_in_db(self, key_id: str, status: KeyStatus) -> bool:
        """更新数据库中的密钥状态"""
        if not self.db:
            return False

        try:
            await self.db.execute("""
                UPDATE secure_keys SET status = ? WHERE key_id = ?
            """, (status.value, key_id))
            return True
        except Exception as e:
            security_logger.error(f"更新密钥状态失败: {e}")
            return False

    async def update_key_usage_in_db(self, key_id: str) -> bool:
        """更新数据库中的密钥使用信息"""
        if not self.db:
            return False

        key_info = self.keys.get(key_id)
        if not key_info:
            return False

        try:
            await self.db.execute("""
                UPDATE secure_keys SET last_used = ?, usage_count = ? WHERE key_id = ?
            """, (
                key_info.last_used.isoformat() if key_info.last_used else None,
                key_info.usage_count,
                key_id
            ))
            return True
        except Exception as e:
            security_logger.error(f"更新密钥使用信息失败: {e}")
            return False

    async def delete_key_from_db(self, key_id: str) -> bool:
        """从数据库删除密钥"""
        if not self.db:
            # 没有数据库时，仅内存模式，返回True表示无需数据库操作
            security_logger.warning(f"数据库未配置，跳过数据库删除: {key_id}")
            return True

        try:
            await self.db.execute("""
                DELETE FROM secure_keys WHERE key_id = ?
            """, (key_id,))
            security_logger.info(f"密钥已从数据库删除: {key_id}")
            return True
        except Exception as e:
            security_logger.error(f"删除密钥失败: {e}")
            return False

    def _generate_key_checksum(self, key_id: str, salt: str, timestamp: str) -> str:
        """生成密钥校验和"""
        data = f"{key_id}:{salt}:{timestamp}:{self.security_level.value}"
        hash_obj = hmac.new(self.master_key, data.encode(), hashlib.sha512)
        return hash_obj.hexdigest()[:16]  # 取前16位作为校验和

    def _hash_key(self, api_key: str, salt: str) -> str:
        """使用主密钥对API密钥进行哈希"""
        # 使用双重哈希增强安全性
        first_hash = hashlib.sha512((api_key + salt).encode()).hexdigest()
        final_hash = hmac.new(self.master_key, first_hash.encode(), hashlib.sha512).hexdigest()
        return final_hash

    async def _load_key_from_db_by_api_key(self, api_key: str) -> Optional[SecureKeyInfo]:
        """Lazy-load a key by hashing against DB rows when lookup cache misses."""
        if not self.db:
            return None
        try:
            rows = await self.db.fetchall("SELECT * FROM secure_keys WHERE status = 'active'")
        except Exception as e:
            security_logger.error(f"从数据库加载密钥失败: {e}")
            return None

        for row in rows:
            try:
                expected_hash = self._hash_key(api_key, row["salt"])
                if not hmac.compare_digest(expected_hash, row["key_hash"]):
                    continue

                key_info = SecureKeyInfo(
                    key_id=row["key_id"],
                    key_hash=row["key_hash"],
                    salt=row["salt"],
                    created_at=datetime.fromisoformat(row["created_at"]),
                    expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
                    last_used=datetime.fromisoformat(row["last_used"]) if row["last_used"] else None,
                    usage_count=row["usage_count"] or 0,
                    max_uses=row["max_uses"],
                    allowed_ips=json.loads(row["allowed_ips"]) if row["allowed_ips"] else [],
                    allowed_user_agents=json.loads(row["allowed_user_agents"]) if row["allowed_user_agents"] else [],
                    allowed_account_ids=json.loads(row["allowed_accounts"]) if row.get("allowed_accounts") else [],
                    default_account_id=row.get("default_account_id"),
                    rate_limit_per_minute=row["rate_limit_per_minute"] or self.default_rate_limit,
                    status=KeyStatus(row["status"]),
                    security_level=SecurityLevel(row["security_level"]),
                    metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                    encrypted_key=row.get("encrypted_key"),
                )

                lookup_hash = self._calculate_lookup_hash(api_key)
                with self._lock:
                    self.keys[key_info.key_id] = key_info
                    self.key_lookup[lookup_hash] = key_info.key_id
                return key_info
            except (KeyError, TypeError, ValueError):
                continue
        return None

    async def verify_key(self, api_key: str, client_ip: str = None, user_agent: str = None) -> Optional[SecureKeyInfo]:
        """
        验证API密钥 - 多层安全验证

        返回: 验证成功返回密钥信息，失败返回None
        """
        if not api_key:
            security_logger.warning("API密钥为空")
            return None

        if not api_key.startswith("sk-"):
            security_logger.warning(f"无效的密钥格式 {api_key[:20]}...")
            return None

        usage_update_key: Optional[str] = None
        status_update: Optional[Tuple[str, KeyStatus]] = None
        should_return_none = False
        result: Optional[SecureKeyInfo] = None
        key_info: Optional[SecureKeyInfo] = None

        try:
            with self._lock:
                lookup_hash = self._calculate_lookup_hash(api_key)
                key_id = self.key_lookup.get(lookup_hash)

                if not key_id:
                    if "-" in api_key[3:]:
                        try:
                            parts = api_key.split("-")
                            if len(parts) == 6 and parts[1] in self.keys:
                                cand_key_id = parts[1]
                                cand_info = self.keys[cand_key_id]
                                expected_hash = self._hash_key(api_key, cand_info.salt)
                                if hmac.compare_digest(cand_info.key_hash, expected_hash):
                                    key_id = cand_key_id
                        except (ValueError, IndexError) as exc:
                            security_logger.debug("API key format mismatch during legacy split: %s", exc)

                if (not key_id) or (key_id not in self.keys):
                    # Fallback: try to load from database by hashing against stored salts
                    fetched = await self._load_key_from_db_by_api_key(api_key)
                    if fetched:
                        key_info = fetched
                        key_id = key_info.key_id
                    else:
                        security_logger.warning("密钥未找到或无效")
                        self._record_failed_attempt(client_ip, "unknown")
                        return None

                if key_info is None:
                    key_info = self.keys.get(key_id)

                if not key_info:
                    security_logger.warning("密钥未找到或无效")
                    self._record_failed_attempt(client_ip, key_id or "unknown")
                    return None

                expected_hash = self._hash_key(api_key, key_info.salt)
                if not hmac.compare_digest(key_info.key_hash, expected_hash):
                    security_logger.warning(f"密钥哈希验证失败: {key_id}")
                    self._record_failed_attempt(client_ip, key_id)
                    return None

                if key_info.status != KeyStatus.ACTIVE:
                    security_logger.warning(f"密钥状态异常: {key_id}, 状态 {key_info.status.value}")
                    return None

                if key_info.expires_at and datetime.now(timezone.utc) > key_info.expires_at:
                    security_logger.warning(f"密钥已过期: {key_id}")
                    key_info.status = KeyStatus.EXPIRED
                    return None

                if key_info.max_uses and key_info.usage_count >= key_info.max_uses:
                    security_logger.warning(f"密钥使用次数已达上限: {key_id}")
                    key_info.status = KeyStatus.INACTIVE
                    status_update = (key_info.key_id, KeyStatus.INACTIVE)
                    should_return_none = True
                else:
                    if key_info.allowed_ips and client_ip:
                        if client_ip not in key_info.allowed_ips:
                            security_logger.warning(f"IP不在白名单中: {client_ip}, 密钥: {key_id}")
                            self._record_failed_attempt(client_ip, key_id)
                            return None

                    if key_info.allowed_user_agents and user_agent:
                        ua_match = any(ua.lower() in user_agent.lower() for ua in key_info.allowed_user_agents)
                        if not ua_match:
                            security_logger.warning(f"User-Agent不在白名单中: {user_agent[:50]}..., 密钥: {key_id}")
                            self._record_failed_attempt(client_ip, key_id)
                            return None

                    if not self._check_rate_limit(key_id, key_info.rate_limit_per_minute):
                        security_logger.warning(f"触发速率限制: {key_id}")
                        return None

                    if self._is_blocked(client_ip):
                        security_logger.warning(f"IP已被阻止: {client_ip}")
                        return None

                    key_info.last_used = datetime.now(timezone.utc)
                    key_info.usage_count += 1
                    usage_update_key = key_info.key_id
                    result = key_info
                    self._clear_failed_attempts(client_ip)
                    security_logger.info(f"密钥验证成功: {key_id}, IP: {client_ip}")

        except Exception as e:
            security_logger.error(f"密钥验证过程中发生错误: {e}")
            return None

        if status_update:
            try:
                updated = await self.update_key_status_in_db(status_update[0], status_update[1])
                if not updated:
                    security_logger.warning(f"未能同步密钥状态到数据库: {status_update[0]}")
            except Exception as exc:
                security_logger.error(f"更新密钥状态失败: {exc}")

        if should_return_none:
            return None

        if usage_update_key:
            try:
                persisted = await self.update_key_usage_in_db(usage_update_key)
                if not persisted:
                    security_logger.warning(f"未能持久化密钥使用信息: {usage_update_key}")
            except Exception as exc:
                security_logger.error(f"持久化密钥使用信息失败: {exc}")

        return result
    def _record_failed_attempt(self, client_ip: str, key_id: str):
        """记录失败尝试"""
        if not client_ip:
            return

        now = datetime.now(timezone.utc)
        if client_ip not in self.failed_attempts:
            self.failed_attempts[client_ip] = []

        self.failed_attempts[client_ip].append(now)

        # 清理过期记录
        cutoff_time = now - timedelta(hours=1)
        self.failed_attempts[client_ip] = [
            attempt for attempt in self.failed_attempts[client_ip]
            if attempt > cutoff_time
        ]

        # 检查是否需要标记密钥为已泄露
        recent_failures = len(self.failed_attempts[client_ip])
        if recent_failures >= self.max_failed_attempts:
            security_logger.critical(f"检测到可疑活动，密钥可能已泄露: {key_id}, IP: {client_ip}")
            # 标记密钥为已泄露
            if key_id in self.keys:
                self.keys[key_id].status = KeyStatus.COMPROMISED

    def _is_blocked(self, client_ip: str) -> bool:
        """检查IP是否被阻止"""
        if not client_ip or client_ip not in self.failed_attempts:
            return False

        now = datetime.now(timezone.utc)
        cutoff_time = now - timedelta(minutes=self.block_duration_minutes)

        # 检查最近失败次数
        recent_failures = [
            attempt for attempt in self.failed_attempts[client_ip]
            if attempt > cutoff_time
        ]

        return len(recent_failures) >= self.max_failed_attempts

    def _clear_failed_attempts(self, client_ip: str):
        """清除失败尝试记录"""
        if client_ip in self.failed_attempts:
            del self.failed_attempts[client_ip]

    def _check_rate_limit(self, key_id: str, limit_per_minute: int) -> bool:
        """检查速率限制"""
        now = time.time()
        minute_start = now - (now % 60)

        if key_id not in self.rate_limits:
            self.rate_limits[key_id] = []

        # 清理过期记录
        self.rate_limits[key_id] = [
            timestamp for timestamp in self.rate_limits[key_id]
            if timestamp > minute_start
        ]

        # 检查是否超过限制
        if len(self.rate_limits[key_id]) >= limit_per_minute:
            return False

        # 记录当前请求
        self.rate_limits[key_id].append(now)
        return True

    def revoke_key(self, key_id: str, reason: str = "") -> bool:
        """撤销密钥"""
        with self._lock:
            if key_id not in self.keys:
                return False

            self.keys[key_id].status = KeyStatus.INACTIVE
            security_logger.info(f"密钥已撤销: {key_id}, 原因: {reason}")
            return True

    def rotate_key(self, key_id: str) -> Optional[Tuple[str, str]]:
        """轮换密钥"""
        with self._lock:
            if key_id not in self.keys:
                return None

            old_key_info = self.keys[key_id]

            # 记录轮换历史
            self.key_rotation_history.append({
                "key_id": key_id,
                "rotated_at": datetime.now(timezone.utc).isoformat(),
                "old_key_created": old_key_info.created_at.isoformat(),
                "usage_count": old_key_info.usage_count,
                "reason": "scheduled_rotation"
            })

            # 生成新密钥
            new_key_id, new_api_key = self.generate_secure_key(
                expires_in_days=None,  # 继承原密钥的过期时间
                max_uses=old_key_info.max_uses,
                allowed_ips=old_key_info.allowed_ips,
                allowed_user_agents=old_key_info.allowed_user_agents,
                rate_limit=old_key_info.rate_limit_per_minute,
                metadata=old_key_info.metadata.copy(),
                allowed_account_ids=old_key_info.allowed_account_ids,
                default_account_id=old_key_info.default_account_id
            )

            # 撤销旧密钥
            self.revoke_key(key_id, "密钥轮换")

            security_logger.info(f"密钥轮换完成: {key_id} -> {new_key_id}")
            return new_key_id, new_api_key

    def get_key_stats(self, key_id: str) -> Optional[Dict[str, Any]]:
        """获取密钥统计信息"""
        with self._lock:
            if key_id not in self.keys:
                return None

            key_info = self.keys[key_id]
            return {
                "key_id": key_id,
                "status": key_info.status.value,
                "created_at": key_info.created_at.isoformat(),
                "expires_at": key_info.expires_at.isoformat() if key_info.expires_at else None,
                "last_used": key_info.last_used.isoformat() if key_info.last_used else None,
                "usage_count": key_info.usage_count,
                "max_uses": key_info.max_uses,
                "security_level": key_info.security_level.value,
                "rate_limit_per_minute": key_info.rate_limit_per_minute,
                "allowed_account_ids": key_info.allowed_account_ids,
                "default_account_id": key_info.default_account_id
            }

    def get_decrypted_key(self, key_id: str) -> Optional[str]:
        """获取解密后的完整API密钥（仅对active状态的密钥有效）"""
        with self._lock:
            if key_id not in self.keys:
                return None

            key_info = self.keys[key_id]

            # 只允许获取活跃状态的密钥
            if key_info.status != KeyStatus.ACTIVE:
                security_logger.warning(f"尝试获取非活跃密钥: {key_id}, 状态: {key_info.status.value}")
                return None

            if not key_info.encrypted_key:
                security_logger.warning(f"密钥没有加密存储: {key_id}")
                return None

            decrypted = self._decrypt_key(key_info.encrypted_key)
            if decrypted:
                security_logger.info(f"密钥已解密: {key_id}")
            return decrypted

    def cleanup_expired_keys(self):
        """清理过期密钥"""
        with self._lock:
            now = datetime.now(timezone.utc)
            expired_keys = []

            for key_id, key_info in self.keys.items():
                if key_info.expires_at and now > key_info.expires_at:
                    expired_keys.append(key_id)
                    key_info.status = KeyStatus.EXPIRED

            if expired_keys:
                security_logger.info(f"清理过期密钥: {expired_keys}")

    def export_security_report(self) -> Dict[str, Any]:
        """导出安全报告"""
        with self._lock:
            active_keys = sum(1 for k in self.keys.values() if k.status == KeyStatus.ACTIVE)
            compromised_keys = sum(1 for k in self.keys.values() if k.status == KeyStatus.COMPROMISED)
            expired_keys = sum(1 for k in self.keys.values() if k.status == KeyStatus.EXPIRED)

            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "security_level": self.security_level.value,
                "total_keys": len(self.keys),
                "active_keys": active_keys,
                "compromised_keys": compromised_keys,
                "expired_keys": expired_keys,
                "blocked_ips": len(self.failed_attempts),
                "rotation_history_count": len(self.key_rotation_history),
                "security_config": {
                    "max_failed_attempts": self.max_failed_attempts,
                    "block_duration_minutes": self.block_duration_minutes,
                    "key_lifetime_days": self.key_lifetime_days,
                    "default_rate_limit": self.default_rate_limit
                }
            }

# 全局高级密钥管理器实例
def create_key_manager(security_level: SecurityLevel = SecurityLevel.PRODUCTION, db: Optional["DatabaseBackend"] = None) -> AdvancedKeyManager:
    """创建高级密钥管理器实例，可选传入数据库实例以启用持久化"""
    return AdvancedKeyManager(security_level, db)
