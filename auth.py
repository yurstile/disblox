import httpx
import jwt
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
import secrets
import logging
import hashlib

from config import config
from database import get_db
from models import User, UserSession, UserServer
from cache_manager import discord_cache
from bot_manager import bot_manager

logger = logging.getLogger(__name__)

security = HTTPBearer()

discord_tokens = {}

class DiscordOAuth2:
    def __init__(self):
        self.client_id = config.DISCORD_CLIENT_ID
        self.client_secret = config.DISCORD_CLIENT_SECRET
        self.redirect_uri = config.DISCORD_REDIRECT_URI
        self.token_url = "https://discord.com/api/oauth2/token"
        self.user_url = "https://discord.com/api/users/@me"
        self.guilds_url = "https://discord.com/api/users/@me/guilds"
    
    def get_authorization_url(self, state: str = None) -> str:
        if not state:
            state = secrets.token_urlsafe(32)
        
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "identify email guilds",
            "state": state
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        return f"https://discord.com/api/oauth2/authorize?{query_string}"
    
    async def exchange_code_for_token(self, code: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri
            }
            
            response = await client.post(self.token_url, data=data)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange code for token"
                )
            
            return response.json()
    
    async def refresh_discord_token(self, refresh_token: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "refresh_token",
                "refresh_token": refresh_token
            }
            
            response = await client.post(self.token_url, data=data)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to refresh Discord token"
                )
            
            return response.json()
    
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        if not discord_cache.check_rate_limit("user_info"):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded for Discord API"
            )
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(self.user_url, headers=headers)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to get user information"
                )
            
            user_data = response.json()
            
            if user_data.get('id'):
                cache_key = f"user_info:{user_data['id']}"
                discord_cache.cache.set(cache_key, user_data, 900)
            
            return user_data
    
    async def get_user_guilds(self, access_token: str) -> list:
        if not discord_cache.check_rate_limit("user_guilds"):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded for Discord API"
            )
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(self.guilds_url, headers=headers)
            
            if response.status_code != 200:
                return []
            
            guilds = response.json()
            
            token_hash = hashlib.md5(access_token.encode()).hexdigest()
            cache_key = f"user_guilds:{token_hash}"
            discord_cache.cache.set(cache_key, guilds, 1800)
            
            return guilds

class JWTManager:
    @staticmethod
    def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=config.JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(days=7)
        
        to_encode.update({"exp": expire, "type": "refresh"})
        encoded_jwt = jwt.encode(to_encode, config.JWT_SECRET_KEY, algorithm=config.JWT_ALGORITHM)
        return encoded_jwt
    
    @staticmethod
    def verify_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
            return payload
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
    
    @staticmethod
    def verify_refresh_token(token: str) -> dict:
        try:
            payload = jwt.decode(token, config.JWT_SECRET_KEY, algorithms=[config.JWT_ALGORITHM])
            if payload.get("type") != "refresh":
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid token type",
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return payload
        except jwt.PyJWTError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Could not validate refresh token",
                headers={"WWW-Authenticate": "Bearer"},
            )

class AuthManager:
    def __init__(self):
        self.oauth2 = DiscordOAuth2()
        self.jwt_manager = JWTManager()
    
    def generate_state(self) -> str:
        return secrets.token_urlsafe(32)
    
    async def authenticate_user(self, code: str, db: AsyncSession) -> Dict[str, Any]:
        token_data = await self.oauth2.exchange_code_for_token(code)
        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")
        
        discord_tokens.clear()
        discord_cache.cache.clear()
        discord_cache.user_cache.clear()
        discord_cache.guild_cache.clear()

        user_info = await self.oauth2.get_user_info(access_token)
        user = await self.get_or_create_user(user_info, db)
        
        discord_tokens[user.discord_id] = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": datetime.utcnow() + timedelta(hours=1)
        }
        
        discord_cache.cache_user_data(user.discord_id, user_info)
        
        try:
            await self.sync_user_servers(user, access_token, db)
        except Exception as e:
            pass
        
        session = await self.create_user_session(user, db)
        
        jwt_token = self.jwt_manager.create_access_token(
            data={"sub": str(user.discord_id), "session_id": str(session.id)}
        )
        
        refresh_token = self.jwt_manager.create_refresh_token(
            data={"sub": str(user.discord_id), "session_id": str(session.id)}
        )
        
        return {
            "access_token": jwt_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": config.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "discord_id": user.discord_id,
                "username": user.username,
                "avatar": user.avatar
            }
        }
    
    async def get_or_create_user(self, user_info: Dict[str, Any], db: AsyncSession) -> User:
        discord_id = user_info["id"]
        
        query = select(User).where(User.discord_id == discord_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if user:
            user.username = user_info["username"]
            user.discriminator = user_info.get("discriminator")
            user.avatar = user_info.get("avatar")
            await db.commit()
            return user
        
        user = User(
            discord_id=discord_id,
            username=user_info["username"],
            discriminator=user_info.get("discriminator"),
            avatar=user_info.get("avatar")
        )
        
        db.add(user)
        await db.commit()
        await db.refresh(user)
        
        return user
    
    async def create_user_session(self, user: User, db: AsyncSession) -> UserSession:
        session = UserSession(
            user_id=user.id,
            session_token=secrets.token_urlsafe(32),
            expires_at=datetime.utcnow() + timedelta(days=7)
        )
        
        db.add(session)
        await db.commit()
        await db.refresh(session)
        
        return session
    
    async def sync_user_servers(self, user: User, access_token: str, db: AsyncSession):
        try:
            bot_guilds = []
            guild_member_counts = {}
            if bot_manager.is_ready():
                try:
                    bot_guilds = [guild.id for guild in bot_manager.get_guilds()]
                    for guild in bot_manager.get_guilds():
                        guild_member_counts[str(guild.id)] = guild.member_count
                except Exception as e:
                    pass
            
            guilds = await self.oauth2.get_user_guilds(access_token)
            discord_cache.cache_user_guilds(user.discord_id, guilds)
            
            print(f"Syncing servers for user {user.username} ({user.discord_id})")
            print(f"Found {len(guilds)} total guilds from Discord API")
            print(f"Bot guilds: {bot_guilds}")
            
            delete_query = delete(UserServer).where(UserServer.user_id == user.id)
            await db.execute(delete_query)
            
            # Also clear verification servers
            from models import VerificationServer
            delete_verification_query = delete(VerificationServer).where(VerificationServer.user_id == user.id)
            await db.execute(delete_verification_query)
            
            added_servers = 0
            for guild in guilds:
                is_owner = guild.get("owner", False)
                permissions = int(guild.get("permissions", "0"))
                has_manage_permissions = (permissions & 0x8) != 0
                
                bot_added = int(guild["id"]) in bot_guilds
                member_count = guild_member_counts.get(guild["id"])
                
                print(f"Processing server: {guild['name']} (ID: {guild['id']})")
                print(f"  - Owner: {is_owner}")
                print(f"  - Has manage permissions: {has_manage_permissions}")
                print(f"  - Bot added: {bot_added}")
                print(f"  - Member count: {member_count}")
                
                # Add to UserServer if user is owner or has manage permissions (for dashboard management)
                if is_owner or has_manage_permissions:
                    server = UserServer(
                        user_id=user.id,
                        server_id=guild["id"],
                        server_name=guild["name"],
                        server_icon=guild.get("icon"),
                        owner=is_owner,
                        permissions=str(permissions),
                        bot_added=bot_added,
                        member_count=member_count
                    )
                    db.add(server)
                    added_servers += 1
                    print(f"  -> Added to UserServer (owner/mod)")
                else:
                    print(f"  -> Skipped for UserServer (not owner/mod)")
                
                # Add to VerificationServer for all servers (for verification purposes)
                verification_server = VerificationServer(
                    user_id=user.id,
                    server_id=guild["id"],
                    server_name=guild["name"],
                    server_icon=guild.get("icon"),
                    owner=is_owner,
                    permissions=str(permissions),
                    bot_added=bot_added,
                    member_count=member_count
                )
                db.add(verification_server)
                print(f"  -> Added to VerificationServer")
            
            await db.commit()
            print(f"Added {added_servers} servers to database")
            
        except Exception as e:
            pass
    
    async def sync_user_servers_with_token(self, user: User, db: AsyncSession) -> bool:
        try:
            self._cleanup_expired_tokens()
            
            token_data = discord_tokens.get(user.discord_id)
            if not token_data:
                return False
            
            if token_data["expires_at"] < datetime.utcnow():
                if token_data.get("refresh_token"):
                    try:
                        new_token_data = await self.oauth2.refresh_discord_token(token_data["refresh_token"])
                        discord_tokens[user.discord_id] = {
                            "access_token": new_token_data["access_token"],
                            "refresh_token": new_token_data.get("refresh_token", token_data["refresh_token"]),
                            "expires_at": datetime.utcnow() + timedelta(hours=1)
                        }
                    except Exception as e:
                        return False
                else:
                    return False
            
            access_token = discord_tokens[user.discord_id]["access_token"]
            await self.sync_user_servers(user, access_token, db)
            return True
            
        except Exception as e:
            return False
    
    def _cleanup_expired_tokens(self):
        current_time = datetime.utcnow()
        expired_keys = [
            discord_id for discord_id, token_data in discord_tokens.items()
            if token_data["expires_at"] < current_time
        ]
        for key in expired_keys:
            del discord_tokens[key]
    
    async def get_current_user(self, credentials: HTTPAuthorizationCredentials = Depends(security), 
                             db: AsyncSession = Depends(get_db)) -> User:
        token = credentials.credentials
        payload = self.jwt_manager.verify_token(token)
        
        discord_id = payload.get("sub")
        session_id = payload.get("session_id")
        
        if not discord_id or not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token",
                headers={"x-token-expired": "true"}
            )
        
        query = select(User).where(User.discord_id == discord_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"x-token-expired": "true"}
            )
        
        session_query = select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user.id,
            UserSession.expires_at > datetime.utcnow()
        )
        result = await db.execute(session_query)
        session = result.scalar_one_or_none()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired",
                headers={"x-token-expired": "true"}
            )
        
        token_data = discord_tokens.get(discord_id)
        if not token_data or token_data["expires_at"] < datetime.utcnow():
            if token_data and token_data.get("refresh_token"):
                try:
                    new_token_data = await self.oauth2.refresh_discord_token(token_data["refresh_token"])
                    discord_tokens[discord_id] = {
                        "access_token": new_token_data["access_token"],
                        "refresh_token": new_token_data.get("refresh_token", token_data["refresh_token"]),
                        "expires_at": datetime.utcnow() + timedelta(hours=1)
                    }
                except Exception as e:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail="Discord token expired"
                    )
            else:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Discord token expired"
                )
        
        return user
    
    async def refresh_session(self, refresh_token: str, db: AsyncSession) -> Dict[str, Any]:
        payload = self.jwt_manager.verify_refresh_token(refresh_token)
        
        discord_id = payload.get("sub")
        session_id = payload.get("session_id")
        
        if not discord_id or not session_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )
        
        query = select(User).where(User.discord_id == discord_id)
        result = await db.execute(query)
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found"
            )
        
        session_query = select(UserSession).where(
            UserSession.id == session_id,
            UserSession.user_id == user.id,
            UserSession.expires_at > datetime.utcnow()
        )
        result = await db.execute(session_query)
        session = result.scalar_one_or_none()
        
        if not session:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Session expired"
            )
        
        new_access_token = self.jwt_manager.create_access_token(
            data={"sub": str(user.discord_id), "session_id": str(session.id)}
        )
        
        new_refresh_token = self.jwt_manager.create_refresh_token(
            data={"sub": str(user.discord_id), "session_id": str(session.id)}
        )
        
        return {
            "access_token": new_access_token,
            "refresh_token": new_refresh_token,
            "token_type": "bearer",
            "expires_in": config.JWT_ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "discord_id": user.discord_id,
                "username": user.username,
                "avatar": user.avatar
            }
        }

auth_manager = AuthManager() 