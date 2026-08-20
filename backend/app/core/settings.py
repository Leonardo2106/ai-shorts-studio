from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AI_SHORTS_", env_file=".env", extra="ignore")

    app_name: str = "AI Shorts Studio"
    api_prefix: str = "/api/v1"
    storage_root: Path = Path("storage")
    database_path: Path | None = None
    ffprobe_binary: str = "ffprobe"
    ffmpeg_binary: str = "ffmpeg"
    ffprobe_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    ffmpeg_timeout_seconds: float = Field(default=900.0, gt=0, le=7200)
    whisper_timeout_seconds: float = Field(default=2 * 60 * 60, ge=30, le=8 * 60 * 60)
    vision_timeout_seconds: float = Field(default=300.0, ge=1, le=3600)
    provider_max_response_bytes: int = Field(default=2 * 1024 * 1024, ge=16 * 1024, le=16 * 1024 * 1024)
    max_upload_bytes: int = Field(default=8 * 1024**3, gt=0)
    max_request_bytes: int = Field(default=8 * 1024**3 + 2 * 1024**2, gt=0)
    upload_chunk_bytes: int = Field(default=1024 * 1024, ge=64 * 1024, le=16 * 1024**2)
    max_concurrent_uploads: int = Field(default=1, ge=1, le=4)
    request_body_timeout_seconds: float = Field(default=60.0, ge=1, le=600)
    min_free_space_bytes: int = Field(default=1024**3, ge=64 * 1024**2)
    max_media_duration_ms: int = Field(default=6 * 60 * 60 * 1000, ge=1_000)
    max_video_width: int = Field(default=7680, ge=320, le=16384)
    max_video_height: int = Field(default=4320, ge=240, le=16384)
    allowed_media_formats: set[str] = Field(default_factory=lambda: {"mov", "mp4"})
    probe_workers: int = Field(default=1, ge=1, le=2)
    job_workers: int = Field(default=1, ge=1, le=1)
    job_shutdown_timeout_seconds: float = Field(default=5.0, ge=0.5, le=30)
    max_active_jobs: int = Field(default=4, ge=1, le=16)
    openai_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    groq_api_key: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "[::1]"])

    @field_validator("storage_root", "database_path", mode="before")
    @classmethod
    def expand_path(cls, value: object) -> object:
        if value is None:
            return None
        return Path(str(value)).expanduser()

    @field_validator("cors_origins")
    @classmethod
    def loopback_origins_only(cls, origins: list[str]) -> list[str]:
        from urllib.parse import urlsplit

        for origin in origins:
            parsed = urlsplit(origin)
            if parsed.scheme not in {"http", "https"} or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("CORS origins must use HTTP(S) loopback hosts")
            if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("CORS origins cannot contain credentials, paths, queries, or fragments")
        return origins

    @model_validator(mode="after")
    def request_limit_covers_upload(self) -> "Settings":
        if self.max_request_bytes < self.max_upload_bytes:
            raise ValueError("max_request_bytes must be at least max_upload_bytes")
        return self

    @property
    def resolved_storage_root(self) -> Path:
        return self.storage_root.absolute()

    @property
    def resolved_database_path(self) -> Path:
        candidate = self.database_path or (self.storage_root / "metadata.sqlite3")
        return candidate.resolve()

    def provider_key(self, provider: object) -> str | None:
        name = str(getattr(provider, "value", provider)).lower()
        value = getattr(self, f"{name}_api_key", None)
        return value.get_secret_value() if isinstance(value, SecretStr) else None


@lru_cache
def get_settings() -> Settings:
    return Settings()
