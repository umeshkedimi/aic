from aic_common.config import AICBaseSettings
from pydantic_settings import SettingsConfigDict


class ApprovalExpirerSettings(AICBaseSettings):
    model_config = SettingsConfigDict(env_prefix="AIC_APPROVAL_EXPIRER_")

    poll_interval_seconds: float = 5.0
