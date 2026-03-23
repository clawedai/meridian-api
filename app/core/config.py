from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator, Field
from typing import Optional, List
import os
import re


def _get_secret_key() -> str:
    """Get SECRET_KEY from environment, fail if not set in production."""
    import secrets
    secret = os.getenv("SECRET_KEY")
    if not secret:
        if os.getenv("ENVIRONMENT", "development") == "production":
            raise ValueError("SECRET_KEY environment variable must be set in production")
        return secrets.token_urlsafe(32)  # Generate random for dev
    return secret


class Settings(BaseSettings):
    PROJECT_NAME: str = "Almanac API"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT", "development")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() in ("true", "1", "yes")

    # Supabase
    SUPABASE_URL: Optional[str] = os.getenv("SUPABASE_URL")
    SUPABASE_KEY: Optional[str] = os.getenv("SUPABASE_KEY")
    SUPABASE_SERVICE_KEY: Optional[str] = os.getenv("SUPABASE_SERVICE_KEY")
    SUPABASE_PAT: Optional[str] = os.getenv("SUPABASE_PAT")

    # JWT
    SECRET_KEY: str = _get_secret_key()
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days

    # CORS - parsed and validated from env var
    BACKEND_CORS_ORIGINS: List[str] = Field(default=["http://localhost:3000", "http://localhost:3001"])

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def parse_and_validate_cors_origins(cls, v):
        """Parse CORS origins from string and validate for security."""
        if isinstance(v, list):
            origins = v
        elif isinstance(v, str):
            origins = [origin.strip() for origin in v.split(",") if origin.strip()]
        else:
            origins = []

        # Security: Never allow wildcard with credentials
        if "*" in origins:
            raise ValueError(
                "CORS origins cannot include '*' when credentials are allowed. "
                "Please specify explicit allowed origins."
            )

        # Validate URL format
        for origin in origins:
            if not (origin.startswith("http://") or origin.startswith("https://")):
                raise ValueError(
                    f"Invalid CORS origin '{origin}': must start with http:// or https://"
                )

        return origins

    @model_validator(mode="after")
    def validate_production_config(self):
        """Validate that required configuration is set for production."""
        if self.ENVIRONMENT.lower() != "production":
            return self

        # List of required fields with placeholder patterns to check
        required_fields = [
            ("SUPABASE_URL", self.SUPABASE_URL, r"https://[a-z0-9]+\.supabase\.co"),
            ("SUPABASE_KEY", self.SUPABASE_KEY, r"^eyJ[a-zA-Z0-9_-]+\.eyJ"),
            ("SUPABASE_SERVICE_KEY", self.SUPABASE_SERVICE_KEY, r"^eyJ[a-zA-Z0-9_-]+\.eyJ"),
            ("SECRET_KEY", self.SECRET_KEY, None),
        ]

        for field_name, value, pattern in required_fields:
            if not value:
                raise ValueError(f"{field_name} must be set in production environment")

            # Check for placeholder values
            if pattern and re.match(pattern, value):
                # Additional check: ensure it's not a default/example placeholder
                placeholder_patterns = [
                    r"your-project-id",
                    r"your-anon-key",
                    r"your-service-role-key",
                    r"your-super-secret-key",
                    r"generate-",
                ]
                for pp in placeholder_patterns:
                    if re.search(pp, value, re.IGNORECASE):
                        raise ValueError(
                            f"{field_name} contains a placeholder value. "
                            f"Please set a real value for production."
                        )

        return self

    # AI Analysis
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")

    # Email Notifications
    RESEND_API_KEY: Optional[str] = os.getenv("RESEND_API_KEY")
    FROM_EMAIL: str = os.getenv("FROM_EMAIL", "Almanac <alerts@resend.dev>")

    # Stripe Payments
    STRIPE_SECRET_KEY: Optional[str] = os.getenv("STRIPE_SECRET_KEY")
    STRIPE_WEBHOOK_SECRET: Optional[str] = os.getenv("STRIPE_WEBHOOK_SECRET")

    # Sentry Error Monitoring
    SENTRY_DSN: Optional[str] = os.getenv("SENTRY_DSN")
    SENTRY_TRACES_SAMPLE_RATE: float = float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "0.1"))
    SENTRY_PROFILES_SAMPLE_RATE: float = float(os.getenv("SENTRY_PROFILES_SAMPLE_RATE", "0.1"))

    class Config:
        env_file = ".env"
        case_sensitive = True

    def __init__(self, **kwargs):
        # Pre-process BACKEND_CORS_ORIGINS from env var if not in kwargs
        if "BACKEND_CORS_ORIGINS" not in kwargs:
            kwargs["BACKEND_CORS_ORIGINS"] = os.getenv("BACKEND_CORS_ORIGINS", "http://localhost:3000,http://localhost:3001")
        super().__init__(**kwargs)


settings = Settings()
