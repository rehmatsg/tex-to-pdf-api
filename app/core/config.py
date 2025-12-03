from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    PROJECT_NAME: str = "LaTeX API"
    VERSION: str = "0.1.0"
    
    # Compilation
    TIMEOUT_SECONDS: int = 20
    MAX_UPLOAD_SIZE: int = 10 * 1024 * 1024  # 10 MB
    
    # Security
    TEX_BIN_PATH: str = "pdflatex" # Assumes in PATH
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
