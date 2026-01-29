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
        if len(v) > 2000:
            raise ValueError("clientSecret length cannot exceed 2000 characters")
        return v

    @field_validator("refreshToken")
    @classmethod
    def validate_refresh_token(cls, v):
        if v is not None:
            if len(v) < 10:
                raise ValueError("refreshToken length cannot be shorter than 10 characters")
            if len(v) > 5000:
                raise ValueError("refreshToken length cannot exceed 5000 characters")
        return v

    @field_validator("accessToken")
    @classmethod
    def validate_access_token(cls, v):
        if v is not None:
            if len(v) < 10:
                raise ValueError("accessToken length cannot be shorter than 10 characters")
            if len(v) > 5000:
                raise ValueError("accessToken length cannot exceed 5000 characters")
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
            if len(v) > 500:
                raise ValueError("clientSecret length cannot exceed 500 characters")
        return v

    @field_validator("refreshToken")
    @classmethod
    def validate_refresh_token(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("refreshToken cannot be empty")
            if len(v) > 1000:
                raise ValueError("refreshToken length cannot exceed 1000 characters")
        return v

    @field_validator("accessToken")
    @classmethod
    def validate_access_token(cls, v):
        if v is not None:
            if not v or len(v.strip()) == 0:
                raise ValueError("accessToken cannot be empty")
            if len(v) > 1000:
                raise ValueError("accessToken length cannot exceed 1000 characters")
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
