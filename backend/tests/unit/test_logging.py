from __future__ import annotations

import json
import logging

import pytest

from nexusflow.core.logging import REDACTED, configure_logging, get_logger, redact_sensitive


def test_redacts_sensitive_top_level_keys() -> None:
    event = redact_sensitive(
        None, "info", {"event": "x", "password": "p", "api_key": "k", "user": "ana"}
    )
    assert event["password"] == REDACTED
    assert event["api_key"] == REDACTED
    assert event["user"] == "ana"


def test_redacts_nested_structures() -> None:
    event = redact_sensitive(
        None,
        "info",
        {
            "event": "x",
            "payload": {"headers": {"Authorization": "Bearer abc"}, "items": [{"token": "t"}]},
        },
    )
    assert event["payload"]["headers"]["Authorization"] == REDACTED
    assert event["payload"]["items"][0]["token"] == REDACTED


def test_json_output_contains_context_and_redaction(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_output=True)
    get_logger("test").info("login_attempt", email="ana@example.com", password="hunter2")
    line = capsys.readouterr().out.strip().splitlines()[-1]
    record = json.loads(line)
    assert record["event"] == "login_attempt"
    assert record["password"] == REDACTED
    assert record["level"] == "info"
    assert "timestamp" in record
    assert "hunter2" not in line


def test_stdlib_loggers_use_same_format(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", json_output=True)
    logging.getLogger("thirdparty").warning("hello from stdlib")
    record = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert record["event"] == "hello from stdlib"
    assert record["logger"] == "thirdparty"
