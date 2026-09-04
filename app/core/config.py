from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://sow:sow@localhost:5432/sow_platform"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-flash-latest"
    chroma_host: str = "localhost"
    chroma_port: int = 8001
    trello_api_key: str = ""
    trello_api_token: str = ""
    plane_base_url: str = ""
    plane_api_key: str = ""
    discord_webhook_url: str = ""
    upload_dir: str = "./uploads"


@lru_cache
def get_settings() -> Settings:
    return Settings()
