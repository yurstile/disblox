import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

class Config:
    DISCORD_TOKEN: Optional[str] = os.getenv("DISCORD_TOKEN")
    DISCORD_APPLICATION_ID: Optional[str] = os.getenv("DISCORD_APPLICATION_ID")
    
    DISCORD_CLIENT_ID: Optional[str] = os.getenv("DISCORD_CLIENT_ID")
    DISCORD_CLIENT_SECRET: Optional[str] = os.getenv("DISCORD_CLIENT_SECRET")
    DISCORD_REDIRECT_URI: str = os.getenv("DISCORD_REDIRECT_URI", "https://app.disblox.xyz/auth/callback")
    
    ROBLOX_CLIENT_ID: Optional[str] = os.getenv("ROBLOX_CLIENT_ID")
    ROBLOX_CLIENT_SECRET: Optional[str] = os.getenv("ROBLOX_CLIENT_SECRET")
    ROBLOX_REDIRECT_URI: str = os.getenv("ROBLOX_REDIRECT_URI")
    
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY")
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "10080"))
    
    API_HOST: str = os.getenv("API_HOST")
    API_PORT: int = int(os.getenv("API_PORT"))
    
    MYSQL_HOST: str = os.getenv("MYSQL_HOST")
    MYSQL_PORT: int = int(os.getenv("MYSQL_PORT"))
    MYSQL_USER: str = os.getenv("MYSQL_USER")
    MYSQL_PASSWORD: str = os.getenv("MYSQL_PASSWORD")
    MYSQL_DATABASE: str = os.getenv("MYSQL_DATABASE")
    
    @classmethod
    def validate(cls) -> bool:
        missing = []
        
        if not cls.DISCORD_TOKEN:
            missing.append("DISCORD_TOKEN")
        
        if not cls.DISCORD_APPLICATION_ID:
            missing.append("DISCORD_APPLICATION_ID")
        
        if not cls.DISCORD_CLIENT_ID:
            missing.append("DISCORD_CLIENT_ID")
        
        if not cls.DISCORD_CLIENT_SECRET:
            missing.append("DISCORD_CLIENT_SECRET")
        
        if missing:
            return False
        
        return True

config = Config()