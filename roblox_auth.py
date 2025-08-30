import httpx
import secrets
import hashlib
import base64
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from fastapi import HTTPException, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
import jwt

from config import config
from database import get_db
from models import User, LinkedAccount

class RobloxOAuth2:
    def __init__(self):
        self.client_id = config.ROBLOX_CLIENT_ID
        self.client_secret = config.ROBLOX_CLIENT_SECRET
        self.redirect_uri = config.ROBLOX_REDIRECT_URI
        self.auth_url = "https://apis.roblox.com/oauth/v1/authorize"
        self.token_url = "https://apis.roblox.com/oauth/v1/token"
        self.user_info_url = "https://apis.roblox.com/oauth/v1/userinfo"
    
    def generate_code_verifier(self) -> str:
        code_verifier = secrets.token_urlsafe(32)
        return code_verifier
    
    def generate_code_challenge(self, code_verifier: str) -> str:
        sha256_hash = hashlib.sha256(code_verifier.encode()).digest()
        code_challenge = base64.urlsafe_b64encode(sha256_hash).decode().rstrip('=')
        return code_challenge
    
    def get_authorization_url(self, state: str = None, code_verifier: str = None) -> Dict[str, str]:
        if not state:
            state = secrets.token_urlsafe(32)
        
        if not code_verifier:
            code_verifier = self.generate_code_verifier()
        
        code_challenge = self.generate_code_challenge(code_verifier)
        
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "scope": "openid profile",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256"
        }
        
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        auth_url = f"{self.auth_url}?{query_string}"
        
        return {
            "auth_url": auth_url,
            "state": state,
            "code_verifier": code_verifier
        }
    
    async def exchange_code_for_token(self, code: str, code_verifier: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.redirect_uri,
                "code_verifier": code_verifier
            }
            
            response = await client.post(self.token_url, data=data)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange code for token"
                )
            
            return response.json()
    
    async def get_user_info(self, access_token: str) -> Dict[str, Any]:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Authorization": f"Bearer {access_token}"}
            response = await client.get(self.user_info_url, headers=headers)
            
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to get user information"
                )
            
            return response.json()
    
    async def get_user_avatar(self, user_id: str) -> Optional[str]:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=150x150&format=Png&isCircular=false")
                
                if response.status_code == 200:
                    data = response.json()
                    if data.get("data") and len(data["data"]) > 0:
                        return data["data"][0]["imageUrl"]
                return None
        except Exception as e:
            return None
    
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
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
                    detail="Failed to refresh token"
                )
            
            return response.json()

class RobloxAuthManager:
    def __init__(self):
        self.oauth2 = RobloxOAuth2()
        self.code_verifiers = {}
    
    def is_configured(self) -> bool:
        return bool(self.oauth2.client_id and self.oauth2.client_secret)
    
    @property
    def client_id(self) -> str:
        return self.oauth2.client_id
    
    @property
    def redirect_uri(self) -> str:
        return self.oauth2.redirect_uri
    
    def get_authorization_url(self, state: str = None) -> Dict[str, str]:
        if not self.is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Roblox OAuth2 is not configured"
            )
        
        auth_data = self.oauth2.get_authorization_url(state=state)
        
        self.code_verifiers[auth_data["state"]] = auth_data["code_verifier"]
        
        return {
            "auth_url": auth_data["auth_url"],
            "state": auth_data["state"]
        }
    
    async def link_roblox_account(self, discord_user: User, code: str, code_verifier: str, db: AsyncSession) -> Dict[str, Any]:
        try:
            token_data = await self.oauth2.exchange_code_for_token(code, code_verifier)
            access_token = token_data["access_token"]
            
            user_info = await self.oauth2.get_user_info(access_token)
            
            roblox_id = user_info.get("sub")
            roblox_username = user_info.get("preferred_username") or user_info.get("name")
            
            if not roblox_id or not roblox_username:
                return {
                    "success": False,
                    "message": "Invalid user information from Roblox"
                }
            
            existing_account = await self.get_linked_account_by_roblox_id(roblox_id, db)
            if existing_account:
                if existing_account.user_id == discord_user.id:
                    return {
                        "success": False,
                        "message": "This Roblox account is already linked to your Discord account"
                    }
                else:
                    return {
                        "success": False,
                        "message": "This Roblox account is already linked to another Discord account"
                    }
            
            avatar_url = await self.oauth2.get_user_avatar(roblox_id)
            
            linked_account = LinkedAccount(
                user_id=discord_user.id,
                roblox_username=roblox_username,
                roblox_id=roblox_id,
                roblox_avatar=avatar_url,
                verified=True
            )
            
            db.add(linked_account)
            await db.commit()
            await db.refresh(linked_account)
            
            return {
                "success": True,
                "message": "Roblox account linked successfully",
                "account": {
                    "id": linked_account.id,
                    "roblox_username": linked_account.roblox_username,
                    "roblox_id": linked_account.roblox_id,
                    "roblox_avatar": linked_account.roblox_avatar
                }
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to link Roblox account: {str(e)}"
            }
    
    async def get_linked_account_by_roblox_id(self, roblox_id: str, db: AsyncSession) -> Optional[LinkedAccount]:
        query = select(LinkedAccount).where(LinkedAccount.roblox_id == roblox_id)
        result = await db.execute(query)
        return result.scalar_one_or_none()
    
    async def unlink_roblox_account(self, discord_user: User, account_id: int, db: AsyncSession) -> Dict[str, Any]:
        try:
            query = select(LinkedAccount).where(
                and_(
                    LinkedAccount.id == account_id,
                    LinkedAccount.user_id == discord_user.id
                )
            )
            result = await db.execute(query)
            linked_account = result.scalar_one_or_none()
            
            if not linked_account:
                return {
                    "success": False,
                    "message": "Linked account not found"
                }
            
            await db.delete(linked_account)
            await db.commit()
            
            return {
                "success": True,
                "message": "Roblox account unlinked successfully"
            }
            
        except Exception as e:
            return {
                "success": False,
                "message": f"Failed to unlink Roblox account: {str(e)}"
            }

roblox_auth_manager = RobloxAuthManager() 