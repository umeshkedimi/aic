from aic_common.config import BaseServiceSettings


class ApiSettings(BaseServiceSettings):
    service_name: str = "aic-api"


settings = ApiSettings()
