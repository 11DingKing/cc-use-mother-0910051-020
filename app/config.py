from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "自行车零部件供应协同系统"
    SQLALCHEMY_DATABASE_URL: str = "sqlite:///./supply_chain.db"

    class Config:
        case_sensitive = True

settings = Settings()
