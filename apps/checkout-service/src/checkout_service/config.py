"""Settings for checkout-service."""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class CheckoutServiceSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    payment_service_url: str = "http://payment-service:8000"
    request_timeout_seconds: float = 3.0
    service_version: str = "unknown"
