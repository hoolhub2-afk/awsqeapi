"""
测试安全验证器
验证 Blocker #3 修复
"""

import pytest
from src.core.security_utils import SecurityValidator


class TestAPIKeyValidation:
    """测试API密钥验证"""

    def test_validates_correct_api_key(self):
        """测试：接受有效的API密钥"""
        valid_keys = [
            "sk-1234567890abcdef",
            "sk-" + "a" * 16,
            "my-valid-api-key-123",
            "a" * 16,  # 最小长度
            "b" * 128,  # 最大长度
        ]

        for key in valid_keys:
            result = SecurityValidator.validate_api_key(key)
            assert result == key

    def test_rejects_short_api_key(self):
        """测试：拒绝过短的API密钥"""
        with pytest.raises(ValueError, match="too short"):
            SecurityValidator.validate_api_key("short")

    def test_rejects_long_api_key(self):
        """测试：拒绝过长的API密钥"""
        long_key = "a" * 129
        with pytest.raises(ValueError, match="too long"):
            SecurityValidator.validate_api_key(long_key)

    def test_rejects_invalid_characters(self):
        """测试：拒绝包含特殊字符的API密钥"""
        invalid_keys = [
            "sk-invalid!@#$%^&*()",  # 足够长度但有特殊字符
            "1234567890123456 with spaces",  # 包含空格
            "1234567890123456\nwith\nnewlines",  # 包含换行
            "1234567890123456;with;semicolons",  # 包含分号
            "1234567890123456'with'quotes",  # 包含引号
        ]

        for key in invalid_keys:
            with pytest.raises(ValueError, match="invalid characters|too short|too long"):
                SecurityValidator.validate_api_key(key)

    def test_rejects_non_string(self):
        """测试：拒绝非字符串输入"""
        with pytest.raises(ValueError, match="must be a non-empty string"):
            SecurityValidator.validate_api_key(None)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            SecurityValidator.validate_api_key(123)

        with pytest.raises(ValueError, match="must be a non-empty string"):
            SecurityValidator.validate_api_key([])

    def test_strips_whitespace(self):
        """测试：自动去除首尾空格"""
        key_with_spaces = "  sk-validkey123456  "
        result = SecurityValidator.validate_api_key(key_with_spaces)
        assert result == "sk-validkey123456"


class TestAccountIDValidation:
    """测试账户ID验证"""

    def test_validates_correct_account_id(self):
        """测试：接受有效的账户ID"""
        valid_ids = [
            "account-123",
            "abc-def-ghi",
            "a1b2c3",
            "a",  # 最小长度
            "a" * 64,  # 最大长度
        ]

        for account_id in valid_ids:
            result = SecurityValidator.validate_account_id(account_id)
            assert result == account_id

    def test_rejects_invalid_account_id(self):
        """测试：拒绝无效的账户ID"""
        invalid_ids = [
            "account_with_underscore",
            "account with space",
            "account!@#",
            "../../../etc/passwd",
            "a" * 65,  # 太长
        ]

        for account_id in invalid_ids:
            with pytest.raises(ValueError):
                SecurityValidator.validate_account_id(account_id)


class TestModelNameValidation:
    """测试模型名称验证"""

    def test_validates_correct_model_name(self):
        """测试：接受有效的模型名称"""
        valid_names = [
            "gpt-4",
            "claude-3.5-sonnet",
            "gemini/pro",
            "model.name-123",
            "anthropic/claude-3-opus-20240229",
        ]

        for model in valid_names:
            result = SecurityValidator.validate_model_name(model)
            assert result == model

    def test_rejects_invalid_model_name(self):
        """测试：拒绝无效的模型名称"""
        invalid_names = [
            "model with spaces",
            "model!@#$%^&*()",  # 特殊字符
            "a" * 129,  # 太长
        ]

        for model in invalid_names:
            with pytest.raises(ValueError):
                SecurityValidator.validate_model_name(model)

        # 注意: "../../../etc/passwd" 包含有效字符（./-），所以单独测试路径验证
        # 模型名称允许点、斜杠和连字符


class TestPathSanitization:
    """测试路径安全化"""

    def test_rejects_directory_traversal(self):
        """测试：拒绝目录遍历攻击"""
        dangerous_paths = [
            "../../../etc/passwd",
            "../../sensitive/file",
            "/etc/shadow",
            "~/sensitive",
            "C:/Windows/System32",
            "/proc/self/environ",
        ]

        for path in dangerous_paths:
            with pytest.raises(ValueError, match="dangerous pattern|Absolute paths"):
                SecurityValidator.sanitize_path(path)

    def test_accepts_safe_paths(self):
        """测试：接受安全的相对路径"""
        safe_paths = [
            "data/file.json",
            "config/settings.yaml",
            "logs/app.log",
        ]

        for path in safe_paths:
            result = SecurityValidator.sanitize_path(path)
            assert result == path


class TestIntegerValidation:
    """测试整数验证"""

    def test_validates_integer_in_range(self):
        """测试：验证范围内的整数"""
        result = SecurityValidator.validate_integer(50, min_val=0, max_val=100, name="test")
        assert result == 50

    def test_rejects_integer_below_min(self):
        """测试：拒绝小于最小值的整数"""
        with pytest.raises(ValueError, match="must be >= 10"):
            SecurityValidator.validate_integer(5, min_val=10, name="test")

    def test_rejects_integer_above_max(self):
        """测试：拒绝大于最大值的整数"""
        with pytest.raises(ValueError, match="must be <= 100"):
            SecurityValidator.validate_integer(150, max_val=100, name="test")

    def test_rejects_non_integer(self):
        """测试：拒绝非整数类型"""
        with pytest.raises(ValueError, match="must be an integer"):
            SecurityValidator.validate_integer("not a number", name="test")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
