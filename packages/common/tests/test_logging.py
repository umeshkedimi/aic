import io
import json

import structlog
from aic_common.config import LogLevel
from aic_common.logging import configure_logging, get_logger


def test_configure_logging_emits_json_lines() -> None:
    buffer = io.StringIO()
    configure_logging(LogLevel.INFO)
    structlog.configure(
        processors=structlog.get_config()["processors"],
        wrapper_class=structlog.get_config()["wrapper_class"],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buffer),
        cache_logger_on_first_use=False,
    )

    logger = get_logger("test.logger")
    logger.info("incident_created", incident_id="abc-123", severity="SEV2")

    line = buffer.getvalue().strip()
    payload = json.loads(line)
    assert payload["event"] == "incident_created"
    assert payload["incident_id"] == "abc-123"
    assert payload["severity"] == "SEV2"
    assert payload["level"] == "info"
    assert "timestamp" in payload


def test_configure_logging_respects_level_filtering() -> None:
    buffer = io.StringIO()
    configure_logging(LogLevel.WARNING)
    structlog.configure(
        processors=structlog.get_config()["processors"],
        wrapper_class=structlog.get_config()["wrapper_class"],
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=buffer),
        cache_logger_on_first_use=False,
    )

    logger = get_logger("test.logger")
    logger.info("should_be_filtered_out")
    logger.warning("should_appear")

    output = buffer.getvalue().strip().splitlines()
    assert len(output) == 1
    assert json.loads(output[0])["event"] == "should_appear"
