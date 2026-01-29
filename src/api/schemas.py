from typing import Optional, Dict, Any, List

from pydantic import BaseModel, field_validator

from src.security.auth import input_validator


class AccountCreate(BaseModel):
    label: Optional[str] = None
    clientId: str
    clientSecret: str
    refreshToken: Optional[str] = None
    accessToken: Optional[str] = None
    other: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = True

    @field_validator("label")
    @classmethod
    def validate_label(cls, v):
        if v is not None:
            v = input_validator.sanitize_string(v, max_length=200)
            if not v:
                raise ValueError("label cannot be empty")
        return v

    @field_validator("clientId")
    @classmethod
    def validate_client_id(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("clientId cannot be empty")
        if len(v) > 500:
            raise ValueError("clientId length cannot exceed 500 characters")
        return v.strip()

    @field_validator("clientSecret")
    @classmethod
    def validate_client_secret(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("clientSecret cannot be empty")
        if len(v) < 10:
            raise ValueError("clientSecret length cannot be shorter than 10 characters")
        if len(v) > 10000:
            raise ValueError("clientSecret length cannot exceed 10000 characters")
        return v

    @field_validator("refreshToken")
    @classmethod
    def validate_refresh_token(cls, v):
        if v is not None:
            if len(v) < 10:
                raise ValueError("refreshToken length cannot be shorter than 10 characters")
            if len(v) > 10000:
                raise ValueError("refreshToken length cannot exceed 10000 characters")
        return v

    @field_validator("accessToken")
    @classmethod
    def validate_access_token(cls, v):
        if v is not None:
            if len(v) < 10:
                raise ValueError("accessToken length cannot be shorter than 10 characters")
            if len(v) > 10000:
                raise ValueError("accessToken length cannot exceed 10000 characters")
        return v

    @field_validator("other")
    @classmethod
    def validate_other(cls, v):
        if v is not None:
            if not isinstance(v, dict):
                raise ValueError("other must be a dict")
            if not input_validator.validate_json_input(v, max_size=10240):
                raise ValueError("other payload is too large")
        return v


class BatchAccountCreate(BaseModel):
    accounts: List[AccountCreate]

    @field_validator("accounts")
    @classmethod
    def validate_accounts(cls, v):
        if len(v) == 0:
            raise ValueError("accounts list cannot be empty")
        if len(v) > 100:
            raise ValueError("cannot create more than 100 accounts in one request")
        return v


class AccountUpdate(BaseModel):
    label: Optional[str] = None
    clientId: Optional[str] = None
    clientSecret: Optional[str] = None
    refreshToken: Optional[str] = None
    accessToken: Optional[str] = None
    other: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None

    @field_validator("label")
    @classmethod
    def validate_label(cls, v):
        if v is not None:
            if len(v.strip()) == 0:
                raise ValueError("label cannot be empty")
            if len(v) > 100:
                raise ValueError("label length cannot exceed 100 characters")
        return v

    @field_validator("clientId")
    @classmethod
    def validate_client_id(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("clientId cannot be empty")
            if len(v) > 200:
                raise ValueError("clientId length cannot exceed 200 characters")
        return v

    @field_validator("clientSecret")
    @classmethod
    def validate_client_secret(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("clientSecret cannot be empty")
            if len(v) > 10000:
                raise ValueError("clientSecret length cannot exceed 10000 characters")
        return v

    @field_validator("refreshToken")
    @classmethod
    def validate_refresh_token(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("refreshToken cannot be empty")
            if len(v) > 10000:
                raise ValueError("refreshToken length cannot exceed 10000 characters")
        return v

    @field_validator("accessToken")
    @classmethod
    def validate_access_token(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("accessToken cannot be empty")
            if len(v) > 10000:
                raise ValueError("accessToken length cannot exceed 10000 characters")
        return v

    @field_validator("other")
    @classmethod
    def validate_other(cls, v):
        if v is not None and not isinstance(v, dict):
            raise ValueError("other must be a dict")
        return v


class PasswordVerify(BaseModel):
    password: str

    @field_validator("password")
    @classmethod
    def validate_password(cls, v):
        if not v or len(v) == 0:
            raise ValueError("password cannot be empty")
        if len(v) > 1000:
            raise ValueError("password length cannot exceed 1000 characters")
        return v


class AuthStartBody(BaseModel):
    label: Optional[str] = None
    enabled: Optional[bool] = True


class KiroAuthStartBody(BaseModel):
    """Kiro 授权启动请求 (仅支持 Builder ID)"""
    label: Optional[str] = None
    enabled: Optional[bool] = True
    region: Optional[str] = None
    startUrl: Optional[str] = None  # Builder ID Start URL (支持 AWS IAM Identity Center)

    @field_validator("label")
    @classmethod
    def validate_kiro_label(cls, v):
        if v is not None:
            v = input_validator.sanitize_string(v, max_length=200)
            if not v:
                raise ValueError("label cannot be empty")
        return v

    @field_validator("region")
    @classmethod
    def validate_region(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=32)
        if not vv:
            return None
        return vv

    @field_validator("startUrl")
    @classmethod
    def validate_start_url(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=500)
        if not vv:
            return None
        # 验证 URL 格式
        if not vv.startswith("https://"):
            raise ValueError("startUrl must start with https://")
        return vv


class KiroImportRefreshTokens(BaseModel):
    """批量导入 refreshToken (需要 clientId/clientSecret 用于 Builder ID 刷新)"""
    refreshTokens: List[str]
    clientId: str  # Builder ID 必需
    clientSecret: str  # Builder ID 必需
    labelPrefix: Optional[str] = None
    enabled: Optional[bool] = True
    region: Optional[str] = None
    skipDuplicateCheck: bool = False

    @field_validator("clientId")
    @classmethod
    def validate_batch_client_id(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("clientId is required for Builder ID refresh")
        if len(v) > 500:
            raise ValueError("clientId length cannot exceed 500 characters")
        return v.strip()

    @field_validator("clientSecret")
    @classmethod
    def validate_batch_client_secret(cls, v):
        if not v or len(v.strip()) == 0:
            raise ValueError("clientSecret is required for Builder ID refresh")
        if len(v) > 10000:
            raise ValueError("clientSecret length cannot exceed 10000 characters")
        return v

    @field_validator("refreshTokens")
    @classmethod
    def validate_refresh_tokens(cls, v):
        if not v:
            raise ValueError("refreshTokens cannot be empty")
        if len(v) > 200:
            raise ValueError("cannot import more than 200 tokens in one request")
        cleaned: List[str] = []
        for t in v:
            if t is None:
                continue
            s = str(t).strip()
            if not s:
                continue
            if len(s) < 10 or len(s) > 10000:
                raise ValueError("invalid refreshToken length")
            cleaned.append(s)
        if not cleaned:
            raise ValueError("refreshTokens cannot be empty")
        return cleaned

    @field_validator("labelPrefix")
    @classmethod
    def validate_label_prefix(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=200)
        if not vv:
            return None
        return vv

    @field_validator("region")
    @classmethod
    def validate_import_region(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=32)
        if not vv:
            return None
        return vv


class KiroImportAwsCredentials(BaseModel):
    credentials: Dict[str, Any]
    label: Optional[str] = None
    enabled: Optional[bool] = True
    region: Optional[str] = None
    skipDuplicateCheck: bool = False

    @field_validator("credentials")
    @classmethod
    def validate_credentials(cls, v):
        if not isinstance(v, dict) or not v:
            raise ValueError("credentials must be a non-empty object")

        def pick(*keys: str) -> Optional[str]:
            for k in keys:
                val = v.get(k)
                if val is None:
                    continue
                s = str(val).strip()
                if s:
                    return s
            return None

        client_id = pick("clientId", "client_id")
        client_secret = pick("clientSecret", "client_secret")
        refresh_token = pick("refreshToken", "refresh_token")
        access_token = pick("accessToken", "access_token")

        missing = []
        if not client_id:
            missing.append("clientId")
        if not client_secret:
            missing.append("clientSecret")
        if not refresh_token:
            missing.append("refreshToken")
        if not access_token:
            missing.append("accessToken")
        if missing:
            raise ValueError("missing required credentials fields: " + ", ".join(missing))

        cleaned = dict(v)
        cleaned["clientId"] = client_id
        cleaned["clientSecret"] = client_secret
        cleaned["refreshToken"] = refresh_token
        cleaned["accessToken"] = access_token
        return cleaned

    @field_validator("label")
    @classmethod
    def validate_aws_label(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=200)
        if not vv:
            return None
        return vv

    @field_validator("region")
    @classmethod
    def validate_aws_region(cls, v):
        if v is None:
            return None
        vv = input_validator.sanitize_string(v, max_length=32)
        if not vv:
            return None
        return vv
