"""Smoke test for the config flow helper functions.

Full HA config-flow tests need pytest-homeassistant-custom-component; this file
exercises the framework-agnostic helpers so basic regressions still get caught
by `pytest -q`.
"""
from __future__ import annotations

from custom_components.jmap.config_flow import _normalize_credentials, cv_ensure_list
from custom_components.jmap.const import (
    CONF_PASSWORD,
    CONF_SERVER_URL,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)


def test_normalize_credentials_token_only():
    out = _normalize_credentials(
        {CONF_SERVER_URL: "https://x", CONF_TOKEN: "abc"}
    )
    assert out[CONF_USERNAME] is None
    assert out[CONF_PASSWORD] is None
    assert out[CONF_VERIFY_SSL] is True


def test_normalize_credentials_basic():
    out = _normalize_credentials(
        {
            CONF_SERVER_URL: "https://x",
            CONF_USERNAME: "u",
            CONF_PASSWORD: "p",
            CONF_VERIFY_SSL: False,
        }
    )
    assert out[CONF_TOKEN] is None
    assert out[CONF_USERNAME] == "u"
    assert out[CONF_VERIFY_SSL] is False


def test_cv_ensure_list_passthrough():
    assert cv_ensure_list(None) == []
    assert cv_ensure_list("a") == ["a"]
    assert cv_ensure_list(["a", "b"]) == ["a", "b"]
