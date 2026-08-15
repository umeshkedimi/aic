"""Settings for payment-service.

``db_pool_size`` deliberately maps to the bare ``DB_POOL_SIZE`` env var (no
``AIC_``-style prefix) per the design doc §1.2 scenario — the deploy script
(T2) and the K8s Deployment manifest both set that exact variable, since it's
the literal knob the signature scenario's bad deploy misconfigures.
"""

from __future__ import annotations

from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class PaymentServiceSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="")

    database_url: str = "postgresql://payment:payment@localhost:5433/payment"
    db_pool_size: int = 20
    request_timeout_seconds: float = 2.0
    work_seconds: float = 0.1
    service_version: str = "unknown"
