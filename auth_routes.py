from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from pydantic import ValidationError
from datetime import datetime, timedelta

from database import get_db
from auth import auth_manager, discord_tokens
from schemas import APIResponse, ErrorResponse, AuthCallbackRequest, ValidationErrorResponse, RefreshTokenRequest

router = APIRouter(prefix="/auth", tags=["authentication"])

@router.get("/login")
async def login():
    try:
        auth_url = auth_manager.oauth2.get_authorization_url()
        return RedirectResponse(url=auth_url)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate authorization URL: {str(e)}"
        )

@router.get("/callback")
async def auth_callback(
    code: Optional[str] = Query(None, min_length=1, max_length=1000, description="Authorization code from Discord"),
    state: Optional[str] = Query(None, max_length=100, description="State parameter"),
    error: Optional[str] = Query(None, max_length=200, description="Error parameter"),
    error_description: Optional[str] = Query(None, max_length=500, description="Error description"),
    guild_id: Optional[str] = Query(None, max_length=50, description="Discord guild ID for bot invites"),
    db: AsyncSession = Depends(get_db)
):
    try:
        if error or not code:
            if error == "access_denied":
                error_msg = "Authorization was cancelled by the user"
            elif error:
                error_msg = f"Discord authorization failed: {error_description or error}"
            else:
                error_msg = "Authorization was cancelled or failed"
            
            frontend_url = "https://www.disblox.xyz/login?error=" + error_msg.replace(" ", "+")
            return RedirectResponse(url=frontend_url)
        
        callback_data = AuthCallbackRequest(code=code, state=state)
        auth_result = await auth_manager.authenticate_user(callback_data.code, db)
        
        access_token = auth_result.get("access_token")
        refresh_token = auth_result.get("refresh_token")
        
        frontend_url = f"https://www.disblox.xyz/auth/callback?code={code}&access_token={access_token}&refresh_token={refresh_token}"
        if guild_id:
            frontend_url += f"&guild_id={guild_id}"
        return RedirectResponse(url=frontend_url)
        
    except ValidationError as e:
        frontend_url = "https://www.disblox.xyz/login?error=validation+error"
        return RedirectResponse(url=frontend_url)
    except HTTPException:
        frontend_url = "https://www.disblox.xyz/login?error=authentication+failed"
        return RedirectResponse(url=frontend_url)
    except Exception as e:
        frontend_url = "https://www.disblox.xyz/login?error=authentication+failed"
        return RedirectResponse(url=frontend_url)

@router.get("/me")
async def get_current_user_info(
    current_user = Depends(auth_manager.get_current_user)
):
    try:
        return {
            "success": True,
            "data": {
                "discord_id": current_user.discord_id,
                "username": current_user.username,
                "avatar": current_user.avatar,
                "created_at": current_user.created_at.isoformat()
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user information: {str(e)}"
        )

@router.post("/logout")
async def logout(
    current_user = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        from cache_manager import discord_cache
        discord_cache.invalidate_all_user_caches(current_user.discord_id)
        
        return APIResponse(
            success=True,
            message="Logged out successfully"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout failed: {str(e)}"
        )

@router.post("/refresh")
async def refresh_token(
    refresh_request: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        auth_result = await auth_manager.refresh_session(refresh_request.refresh_token, db)
        
        return {
            "success": True,
            "message": "Token refreshed successfully",
            "data": auth_result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Token refresh failed: {str(e)}"
        )

@router.post("/discord-refresh")
async def refresh_discord_token(
    current_user = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        token_data = discord_tokens.get(current_user.discord_id)
        if not token_data or not token_data.get("refresh_token"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No Discord refresh token available"
            )
        
        new_token_data = await auth_manager.oauth2.refresh_discord_token(token_data["refresh_token"])
        discord_tokens[current_user.discord_id] = {
            "access_token": new_token_data["access_token"],
            "refresh_token": new_token_data.get("refresh_token", token_data["refresh_token"]),
            "expires_at": datetime.utcnow() + timedelta(hours=1)
        }
        
        from cache_manager import discord_cache
        discord_cache.invalidate_all_user_caches(current_user.discord_id)
        
        return {
            "success": True,
            "message": "Discord token refreshed successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Discord token refresh failed: {str(e)}"
        )

@router.get("/discord-url")
async def get_discord_auth_url():
    try:
        auth_url = auth_manager.oauth2.get_authorization_url()
        return {
            "success": True,
            "data": {
                "auth_url": auth_url
            }
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate Discord auth URL: {str(e)}"
        ) 