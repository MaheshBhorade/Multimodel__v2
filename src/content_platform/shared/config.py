from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CRP_", extra="ignore")

    database_url: str = Field(default="sqlite:///./content_platform.db")
    platform_database_url: str = Field(default="sqlite:///./platform_library.db")
    server_host: str = Field(default="0.0.0.0")
    server_port: int = Field(default=8000)
    edge_server_url: str = Field(default="http://100.78.128.49:8000")
    device_id: str = Field(default="pi-agent")
    capture_interval_seconds: int = Field(default=10, ge=5)
    snapshot_base_url: str = Field(default="http://localhost:8000/snapshots")
    edge_mode: str = Field(default="simulated")
    qdrant_url: str = Field(default="http://localhost:6333")


@lru_cache
def get_settings() -> Settings:
    return Settings()
