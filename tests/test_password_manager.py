"""
密码管理器测试

测试 bcrypt 密码哈希和验证功能
"""
import pytest
import bcrypt
from src.security.password import PasswordManager


def test_password_verification():
    """测试密码验证功能"""
    # 重置单例状态
    PasswordManager._password_hash = None
    PasswordManager._initialized = False

    # 模拟密码初始化
    test_password = "MySecurePassword123!"
    PasswordManager._password_hash = bcrypt.hashpw(
        test_password.encode('utf-8'),
        bcrypt.gensalt(rounds=12)
    )
    PasswordManager._initialized = True

    # 测试正确密码
    assert PasswordManager.verify_password(test_password) is True

    # 测试错误密码
    assert PasswordManager.verify_password("WrongPassword") is False
    assert PasswordManager.verify_password("") is False


def test_timing_attack_resistance():
    """测试时序攻击抵抗性"""
    import time

    # 重置单例状态
    PasswordManager._password_hash = None
    PasswordManager._initialized = False

    PasswordManager._password_hash = bcrypt.hashpw(
        "correct".encode('utf-8'),
        bcrypt.gensalt(rounds=12)
    )
    PasswordManager._initialized = True

    # 测试多次，确保时间差异不明显
    times_correct = []
    times_wrong = []

    for _ in range(10):
        start = time.perf_counter()
        PasswordManager.verify_password("correct")
        times_correct.append(time.perf_counter() - start)

        start = time.perf_counter()
        PasswordManager.verify_password("wrong")
        times_wrong.append(time.perf_counter() - start)

    # bcrypt 的时间应该相对稳定（差异小于 20%）
    avg_correct = sum(times_correct) / len(times_correct)
    avg_wrong = sum(times_wrong) / len(times_wrong)

    # 时间差异应该在合理范围内
    time_diff_ratio = abs(avg_correct - avg_wrong) / max(avg_correct, avg_wrong)
    assert time_diff_ratio < 0.2, "时序攻击风险：正确和错误密码的验证时间差异过大"


def test_is_configured():
    """测试配置检查"""
    # 重置单例状态
    PasswordManager._password_hash = None
    PasswordManager._initialized = False

    # 未配置时
    PasswordManager._password_hash = None
    PasswordManager._initialized = True
    assert PasswordManager.is_configured() is False

    # 已配置时
    PasswordManager._password_hash = bcrypt.hashpw(
        "test".encode('utf-8'),
        bcrypt.gensalt(rounds=12)
    )
    assert PasswordManager.is_configured() is True


def test_empty_password_handling():
    """测试空密码处理"""
    # 重置单例状态
    PasswordManager._password_hash = None
    PasswordManager._initialized = False

    PasswordManager._password_hash = bcrypt.hashpw(
        "valid".encode('utf-8'),
        bcrypt.gensalt(rounds=12)
    )
    PasswordManager._initialized = True

    # 空字符串应该返回 False
    assert PasswordManager.verify_password("") is False
    assert PasswordManager.verify_password("   ") is False  # 空格也应该失败
