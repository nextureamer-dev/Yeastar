from pydantic_settings import BaseSettings
from functools import lru_cache
from typing import Optional


class Settings(BaseSettings):
    # Yeastar PBX Configuration
    yeastar_host: str = "192.168.1.100"
    yeastar_port: int = 8088
    yeastar_username: str = "api"
    yeastar_password: str = ""
    yeastar_client_id: str = ""
    yeastar_client_secret: str = ""
    yeastar_webhook_token: str = ""

    # Database Configuration
    # Use SQLite by default for easy local development
    # Set db_type to "mysql" and configure mysql settings for production
    db_type: str = "sqlite"  # "sqlite" or "mysql"

    # SQLite settings
    sqlite_path: str = "yeastar_crm.db"

    # MySQL settings (used when db_type="mysql")
    db_host: str = "localhost"
    db_port: int = 3306
    db_name: str = "yeastar_crm"
    db_user: str = "root"
    db_password: str = ""

    # Application Settings
    secret_key: str = "change-me-in-production"
    api_port: int = 8000
    webhook_port: int = 8001

    # ASR Configuration (faster-whisper)
    whisper_model_size: str = "large-v3"
    whisper_device: str = "cuda"  # cuda or cpu
    whisper_compute_type: str = "float16"  # float16, int8, int8_float16

    # LLM Configuration (Ollama with Llama 3.1 8B - fast and accurate)
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"
    ollama_context_length: int = 16384  # Increased for detailed analysis prompt

    # Auto-processing Configuration
    auto_process_calls: bool = True
    process_internal_calls: bool = False
    processing_timeout_seconds: int = 300

    @property
    def database_url(self) -> str:
        if self.db_type == "sqlite":
            return f"sqlite:///./{self.sqlite_path}"
        return f"mysql+pymysql://{self.db_user}:{self.db_password}@{self.db_host}:{self.db_port}/{self.db_name}"

    @property
    def yeastar_base_url(self) -> str:
        # Use HTTPS for cloud PBX (port 443), HTTP for on-premise
        protocol = "https" if self.yeastar_port == 443 else "http"
        if self.yeastar_port in (443, 80):
            return f"{protocol}://{self.yeastar_host}"
        return f"{protocol}://{self.yeastar_host}:{self.yeastar_port}"

    @property
    def is_cloud_pbx(self) -> bool:
        """Check if this is a cloud PBX (uses OAuth2) vs on-premise (uses username/password)."""
        return bool(self.yeastar_client_id and self.yeastar_client_secret)

    class Config:
        env_file = ".env"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
