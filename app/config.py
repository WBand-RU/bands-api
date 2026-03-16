from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_name: str = "Bands Service"
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/bands"
    keycloak_issuer_url: str = "https://keycloak.example.com/realms/example"
    keycloak_audience: str = "bands-service"
    keycloak_client_id: str = "bands-service"
    keycloak_client_secret: str | None = None
    auth_disable_verification: bool = False
    cors_origins: list[str] = []

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


def get_settings() -> Settings:
    return Settings()
