from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/zenith_fintech"
    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    DEBUG: bool = True

    # Ask Zenith AI assistant (Groq free tier). Provider-agnostic: point GROQ_BASE_URL
    # at any OpenAI-compatible endpoint to swap providers.
    GROQ_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-120b"
    GROQ_BASE_URL: str = "https://api.groq.com"

    class Config:
        env_file = ".env"


settings = Settings()
