from fastapi import APIRouter, Depends, HTTPException, Query, status, Path
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
import logging
from pydantic import ValidationError

from database import get_db
from auth import auth_manager
from models import User, LinkedAccount
from schemas import APIResponse, RobloxCallbackRequest, AccountIdPath, ValidationErrorResponse
from roblox_auth import roblox_auth_manager
from cache_manager import discord_cache
from sqlalchemy import select

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/roblox", tags=["roblox"])

@router.get("/auth")
async def roblox_auth(
    current_user = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if not roblox_auth_manager.is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Roblox OAuth2 is not configured"
            )
        
        state = auth_manager.generate_state()
        roblox_auth_manager.user_states = getattr(roblox_auth_manager, 'user_states', {})
        roblox_auth_manager.user_states[state] = {
            'user_id': current_user.id,
            'discord_id': current_user.discord_id,
            'username': current_user.username
        }
        
        auth_data = roblox_auth_manager.get_authorization_url(state=state)
        
        return APIResponse(
            success=True,
            message="Roblox authorization URL generated",
            data=auth_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Roblox auth URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/callback")
async def roblox_callback(
    code: Optional[str] = Query(None, min_length=1, max_length=1000, description="Authorization code from Roblox"),
    state: Optional[str] = Query(None, max_length=100, description="State parameter"),
    error: Optional[str] = Query(None, max_length=200, description="Error parameter"),
    error_description: Optional[str] = Query(None, max_length=500, description="Error description"),
    db: AsyncSession = Depends(get_db)
):
    try:
        callback_data = RobloxCallbackRequest(
            code=code, 
            state=state, 
            error=error, 
            error_description=error_description
        )
        
        if error:
            error_msg = error.replace(" ", "%20")
            return RedirectResponse(url=f"https://disblox.xyz/roblox/callback?error={error_msg}")
        
        if not roblox_auth_manager.is_configured():
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=Roblox OAuth2 is not configured")
        
        if not state:
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=Invalid or expired state parameter")
        
        if not code:
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=Invalid or expired state parameter")
        
        code_verifier = roblox_auth_manager.code_verifiers.get(callback_data.state)
        if not code_verifier:
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=Invalid or expired state parameter")
        
        user_states = getattr(roblox_auth_manager, 'user_states', {})
        user_info = user_states.get(callback_data.state)
        if not user_info:
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=Invalid or expired state parameter")
        
        user_query = select(User).where(User.id == user_info['user_id'])
        result = await db.execute(user_query)
        current_user = result.scalar_one_or_none()
        
        if not current_user:
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?error=User not found")
        
        result = await roblox_auth_manager.link_roblox_account(
            discord_user=current_user,
            code=callback_data.code,
            code_verifier=code_verifier,
            db=db
        )
        
        if result["success"]:
            discord_cache.invalidate_all_user_caches(current_user.discord_id)
            
            try:
                await auth_manager.sync_user_servers_with_token(current_user, db)
            except Exception as sync_error:
                pass
            
            if callback_data.state in roblox_auth_manager.code_verifiers:
                del roblox_auth_manager.code_verifiers[callback_data.state]
            if callback_data.state in roblox_auth_manager.user_states:
                del roblox_auth_manager.user_states[callback_data.state]
            
            return RedirectResponse(url="https://disblox.xyz/roblox/callback?code=success&state=linked")
        else:
            return RedirectResponse(url=f"https://disblox.xyz/roblox/callback?error={result['message']}")
        
    except ValidationError as e:
        logger.error(f"Validation error in Roblox callback: {e}")
        return RedirectResponse(url=f"https://disblox.xyz/roblox/callback?error=Invalid request parameters")
    except Exception as e:
        logger.error(f"Error in Roblox callback: {e}")
        return RedirectResponse(url=f"https://disblox.xyz/roblox/callback?error=Internal server error: {str(e)}")

@router.get("/auth-url")
async def get_roblox_auth_url(
    current_user = Depends(auth_manager.get_current_user)
):
    try:
        if not roblox_auth_manager.is_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Roblox OAuth2 is not configured"
            )
        
        state = auth_manager.generate_state()
        roblox_auth_manager.user_states = getattr(roblox_auth_manager, 'user_states', {})
        roblox_auth_manager.user_states[state] = {
            'user_id': current_user.id,
            'discord_id': current_user.discord_id,
            'username': current_user.username
        }
        
        auth_data = roblox_auth_manager.get_authorization_url(state=state)
        
        return APIResponse(
            success=True,
            message="Roblox authorization URL generated",
            data=auth_data
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating Roblox auth URL: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.delete("/unlink/{account_id}")
async def unlink_roblox_account(
    account_id: int = Path(..., gt=0, description="Account ID to unlink"),
    current_user = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        account_data = AccountIdPath(account_id=account_id)
        
        result = await roblox_auth_manager.unlink_roblox_account(
            discord_user=current_user,
            account_id=account_data.account_id,
            db=db
        )
        
        if result["success"]:
            discord_cache.invalidate_all_user_caches(current_user.discord_id)
            
            return APIResponse(
                success=True,
                message="Roblox account unlinked successfully"
            )
        else:
            return APIResponse(
                success=False,
                message=result["message"]
            )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unlinking Roblox account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/status")
async def get_roblox_auth_status(
    current_user = Depends(auth_manager.get_current_user)
):
    try:
        is_configured = roblox_auth_manager.is_configured()
        
        return APIResponse(
            success=True,
            message="Roblox OAuth2 status retrieved",
            data={
                "configured": is_configured,
                "client_id": roblox_auth_manager.client_id if is_configured else None,
                "redirect_uri": roblox_auth_manager.redirect_uri if is_configured else None
            }
        )
        
    except Exception as e:
        logger.error(f"Error getting Roblox auth status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        ) 