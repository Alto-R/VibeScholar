"""Configuration management for vibe-paper-search."""

from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="VIBE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Storage paths
    data_dir: Path = Field(
        default=Path.home() / ".vibe-paper-search",
        description="Base directory for all data storage",
    )
    papers_dir: Path | None = Field(
        default=None,
        description="Directory for downloaded PDFs (defaults to data_dir/papers)",
    )

    # Browser settings
    browser_type: Literal["chromium", "firefox", "webkit"] = Field(
        default="chromium",
        description="Browser type to use for automation",
    )
    headless: bool = Field(
        default=True,
        description="Run browser in headless mode",
    )
    user_data_dir: Path | None = Field(
        default=None,
        description="Chrome user data directory for persistent sessions",
    )

    # Proxy settings
    proxy_url: str | None = Field(
        default=None,
        description="HTTP proxy URL (e.g., http://127.0.0.1:7890)",
    )
    auto_detect_proxy: bool = Field(
        default=True,
        description="Auto-detect local proxy (v2ray, clash)",
    )

    # AI settings
    openai_api_key: str | None = Field(default=None)
    anthropic_api_key: str | None = Field(default=None)
    llm_provider: Literal["openai", "anthropic"] = Field(
        default="openai",
        description="LLM provider for AI features",
    )
    llm_model: str = Field(
        default="gpt-4o-mini",
        description="LLM model to use",
    )

    # MCP Server settings
    mcp_host: str = Field(default="localhost")
    mcp_port: int = Field(default=8765)

    # Search settings
    default_max_results: int = Field(default=20)
    search_timeout: int = Field(default=60, description="Search timeout in seconds")
    download_timeout: int = Field(default=120, description="Download timeout in seconds")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        # Set default papers_dir if not specified
        if self.papers_dir is None:
            self.papers_dir = self.data_dir / "papers"

    @property
    def storage_state_dir(self) -> Path:
        """Directory for browser storage states."""
        return self.data_dir / "auth"

    @property
    def database_path(self) -> Path:
        """Path to SQLite database."""
        return self.data_dir / "papers.db"

    @property
    def logs_dir(self) -> Path:
        """Directory for log files."""
        return self.data_dir / "logs"

    def ensure_dirs(self) -> None:
        """Create all necessary directories."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.papers_dir.mkdir(parents=True, exist_ok=True)
        self.storage_state_dir.mkdir(parents=True, exist_ok=True)
        self.logs_dir.mkdir(parents=True, exist_ok=True)


# Global settings instance
settings = Settings()
