import pytest

from src.core.runtime import parse_trusted_hosts, parse_cors_origins


def test_parse_trusted_hosts_requires_config_in_production():
    with pytest.raises(ValueError):
        parse_trusted_hosts("", debug_mode=False, production_mode=True)


def test_parse_trusted_hosts_returns_defaults_in_debug():
    hosts = parse_trusted_hosts("", debug_mode=True)
    assert "localhost" in hosts
    assert "*" in hosts


def test_parse_trusted_hosts_returns_defaults_in_dev_when_not_debug():
    hosts = parse_trusted_hosts("", debug_mode=False, production_mode=False)
    assert "127.0.0.1" in hosts
    assert "*" in hosts


def test_parse_trusted_hosts_accepts_wildcard_in_dev():
    hosts = parse_trusted_hosts("*", debug_mode=False, production_mode=False)
    assert hosts == ["*"]


def test_parse_trusted_hosts_rejects_wildcard_in_production():
    with pytest.raises(ValueError):
        parse_trusted_hosts("*", debug_mode=False, production_mode=True)


def test_parse_cors_origins_accepts_explicit_domains():
    origins = parse_cors_origins("example.com,https://api.example.com", debug_mode=False, production_mode=True)
    assert origins[0] == "https://example.com"
    assert "https://api.example.com" in origins


def test_parse_cors_origins_rejects_wildcards():
    with pytest.raises(ValueError):
        parse_cors_origins("https://*.example.com", debug_mode=True)
