from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        case_sensitive=True,
    )

    PROJECT_NAME: str = "LaTeX API"
    VERSION: str = "2.0.0"

    # Compilation
    TIMEOUT_SECONDS: int = 20
    TEX_BIN_PATH: str = "pdflatex"  # Assumes pdflatex is in PATH

    # Resource limits
    MAX_UPLOAD_SIZE: int = 20 * 1024 * 1024  # 20 MB (bumped from 10 MB for v2)
    MAX_FILE_COUNT: int = 500
    MAX_PASSES: int = 5
    MAX_LOG_SIZE: int = 64 * 1024  # 64 KB
    MAX_PATH_LENGTH: int = 300


settings = Settings()
