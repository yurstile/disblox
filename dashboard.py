from fastapi import APIRouter, Depends, HTTPException, status, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_
from typing import List, Optional
from datetime import datetime, timedelta
import logging
from pydantic import ValidationError

from database import get_db
from auth import auth_manager
from models import User, UserServer, BotServer, ServerConfig as ServerConfigModel, LinkedAccount
from schemas import (
    DashboardData, UserServer as UserServerSchema, LinkedAccount as LinkedAccountSchema, 
    BotStatusResponse, APIResponse, VerifyInServerRequest, ServerIdPath, AccountIdPath,
    PaginationParams, SearchParams, ValidationErrorResponse
)
from bot_manager import bot_manager
from cache_manager import discord_cache

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/dashboard", tags=["dashboard"])

def check_discord_token_expiration(current_user: User):
    try:
        return False
    except Exception as e:
        return True

@router.get("/user", response_model=DashboardData)
async def get_user_dashboard(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == current_user.id)
        result = await db.execute(linked_accounts_query)
        linked_accounts = result.scalars().all()
        
        user_servers_query = select(UserServer).where(UserServer.user_id == current_user.id)
        result = await db.execute(user_servers_query)
        user_servers = result.scalars().all()
        
        total_linked_accounts = len(linked_accounts)
        total_servers = len(user_servers)
        servers_with_bot = sum(1 for server in user_servers if server.bot_added)
        
        return DashboardData(
            user=current_user,
            linked_accounts=linked_accounts,
            user_servers=user_servers,
            total_linked_accounts=total_linked_accounts,
            total_servers=total_servers,
            servers_with_bot=servers_with_bot
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/user/servers", response_model=List[UserServerSchema])
async def get_user_servers(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db),
    page: Optional[int] = Query(1, ge=1, le=1000, description="Page number"),
    limit: Optional[int] = Query(50, ge=1, le=100, description="Items per page")
):
    try:
        pagination = PaginationParams(page=page, limit=limit)
        
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        cached_servers = discord_cache.get_cached_user_guilds(current_user.discord_id)
        
        if cached_servers:
            logger.info(f"Using cached servers for user {current_user.username}")
            servers = []
            for guild in cached_servers:
                is_owner = guild.get("owner", False)
                permissions = int(guild.get("permissions", "0"))
                has_manage_permissions = (permissions & 0x8) != 0
                
                if is_owner or has_manage_permissions:
                    bot_guilds = []
                    if bot_manager.is_ready():
                        try:
                            bot_guilds = [guild.id for guild in bot_manager.get_guilds()]
                        except Exception as e:
                            logger.warning(f"Error getting bot guilds: {e}")
                    
                    bot_added = int(guild["id"]) in bot_guilds
                    
                    server_data = {
                        "id": len(servers) + 1,
                        "user_id": current_user.id,
                        "server_id": guild["id"],
                        "server_name": guild["name"],
                        "server_icon": guild.get("icon"),
                        "owner": is_owner,
                        "permissions": str(permissions),
                        "bot_added": bot_added,
                        "added_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow()
                    }
                    servers.append(UserServerSchema(**server_data))
            
            start_idx = (pagination.page - 1) * pagination.limit
            end_idx = start_idx + pagination.limit
            paginated_servers = servers[start_idx:end_idx]
            
            return paginated_servers
        else:
            user_servers_query = select(UserServer).where(UserServer.user_id == current_user.id)
            result = await db.execute(user_servers_query)
            user_servers = result.scalars().all()
            
            start_idx = (pagination.page - 1) * pagination.limit
            end_idx = start_idx + pagination.limit
            paginated_servers = user_servers[start_idx:end_idx]
            
            return paginated_servers
            
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting user servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/user/linked-accounts", response_model=List[LinkedAccountSchema])
async def get_user_linked_accounts(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == current_user.id)
        result = await db.execute(linked_accounts_query)
        linked_accounts = result.scalars().all()
        
        return linked_accounts
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting linked accounts: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/bot/status/{server_id}", response_model=BotStatusResponse)
async def check_bot_status(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        user_server_query = select(UserServer).where(
            and_(
                UserServer.user_id == current_user.id,
                UserServer.server_id == server_data.server_id
            )
        )
        result = await db.execute(user_server_query)
        user_server = result.scalar_one_or_none()
        
        if not user_server:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found or you don't have access"
            )
        
        bot_guilds = []
        if bot_manager.is_ready():
            try:
                bot_guilds = [guild.id for guild in bot_manager.get_guilds()]
            except Exception as e:
                logger.warning(f"Error getting bot guilds: {e}")
        
        bot_present = int(server_data.server_id) in bot_guilds
        bot_added = user_server.bot_added
        can_add_bot = user_server.owner or (int(user_server.permissions or "0") & 0x8) != 0
        
        return BotStatusResponse(
            server_id=server_data.server_id,
            server_name=user_server.server_name,
            bot_present=bot_present,
            bot_added=bot_added,
            can_add_bot=can_add_bot,
            permissions=user_server.permissions
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error checking bot status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/bot/status", response_model=dict)
async def get_bot_status(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        bot_ready = bot_manager.is_ready()
        bot_user = bot_manager.get_user()
        bot_latency = bot_manager.get_latency()
        bot_uptime = bot_manager.get_uptime()
        
        bot_guilds = []
        if bot_ready:
            try:
                bot_guilds = [guild.id for guild in bot_manager.get_guilds()]
            except Exception as e:
                logger.warning(f"Error getting bot guilds: {e}")
        
        return {
            "ready": bot_ready,
            "user": {
                "id": bot_user.id if bot_user else None,
                "username": bot_user.name if bot_user else None,
                "discriminator": bot_user.discriminator if bot_user else None
            } if bot_user else None,
            "latency": bot_latency,
            "uptime": bot_uptime,
            "guild_count": len(bot_guilds),
            "guilds": bot_guilds
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bot status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/bot/servers", response_model=List[dict])
async def get_bot_servers(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        if not bot_manager.is_ready():
            return []
        
        bot_guilds = bot_manager.get_guilds()
        servers = []
        
        for guild in bot_guilds:
            server_data = {
                "id": guild.id,
                "name": guild.name,
                "icon": str(guild.icon) if guild.icon else None,
                "member_count": guild.member_count,
                "owner_id": guild.owner_id
            }
            servers.append(server_data)
        
        return servers
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting bot servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/user/sync-servers", response_model=APIResponse)
async def sync_user_servers(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        success = await auth_manager.sync_user_servers_with_token(current_user, db)
        
        if success:
            discord_cache.invalidate_user_guilds_cache(current_user.discord_id)
            return APIResponse(
                success=True,
                message="User servers synced successfully"
            )
        else:
            return APIResponse(
                success=False,
                message="Failed to sync servers. Discord token may be expired."
            )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing user servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.delete("/user/linked-account/{account_id}", response_model=APIResponse)
async def unlink_account(
    account_id: int = Path(..., gt=0, description="Account ID to unlink"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        account_data = AccountIdPath(account_id=account_id)
        
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        account_query = select(LinkedAccount).where(
            and_(
                LinkedAccount.id == account_data.account_id,
                LinkedAccount.user_id == current_user.id
            )
        )
        result = await db.execute(account_query)
        linked_account = result.scalar_one_or_none()
        
        if not linked_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Linked account not found"
            )
        
        await db.delete(linked_account)
        await db.commit()
        
        discord_cache.invalidate_all_user_caches(current_user.discord_id)
        
        return APIResponse(
            success=True,
            message="Account unlinked successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error unlinking account: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/user/token-status", response_model=APIResponse)
async def get_token_status(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        token_expired = check_discord_token_expiration(current_user)
        
        return APIResponse(
            success=True,
            message="Token status retrieved",
            data={
                "expired": token_expired,
                "user_id": current_user.discord_id,
                "username": current_user.username
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting token status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/bot/ready", response_model=APIResponse)
async def check_bot_ready():
    try:
        bot_ready = bot_manager.is_ready()
        bot_user = bot_manager.get_user()
        
        return APIResponse(
            success=True,
            message="Bot status checked",
            data={
                "ready": bot_ready,
                "user": {
                    "id": bot_user.id if bot_user else None,
                    "username": bot_user.name if bot_user else None,
                    "discriminator": bot_user.discriminator if bot_user else None
                } if bot_user else None
            }
        )
        
    except Exception as e:
        logger.error(f"Error checking bot ready status: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/bot/manual-sync", response_model=APIResponse)
async def manual_sync_guilds(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        if not bot_manager.is_ready():
            return APIResponse(
                success=False,
                message="Bot is not ready"
            )
        
        await bot_manager.sync_guilds_to_database()
        
        return APIResponse(
            success=True,
            message="Bot guilds synced to database"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error manually syncing guilds: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/bot/test-sync", response_model=APIResponse)
async def test_bot_sync(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        if not bot_manager.is_ready():
            return APIResponse(
                success=False,
                message="Bot is not ready"
            )
        
        bot_guilds = bot_manager.get_guilds()
        guild_count = len(bot_guilds)
        
        return APIResponse(
            success=True,
            message=f"Bot sync test completed",
            data={
                "guild_count": guild_count,
                "guilds": [{"id": guild.id, "name": guild.name} for guild in bot_guilds]
            }
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error testing bot sync: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/bot/sync-guilds", response_model=APIResponse)
async def sync_bot_guilds(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        if not bot_manager.is_ready():
            return APIResponse(
                success=False,
                message="Bot is not ready"
            )
        
        await bot_manager.sync_guilds_to_database()
        
        return APIResponse(
            success=True,
            message="Bot guilds synced successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error syncing bot guilds: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/bot/verify-in-server", response_model=APIResponse)
async def verify_in_server(
    req: VerifyInServerRequest,
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        account_query = select(LinkedAccount).where(
            and_(
                LinkedAccount.id == req.account_id,
                LinkedAccount.user_id == current_user.id
            )
        )
        result = await db.execute(account_query)
        linked_account = result.scalar_one_or_none()
        
        if not linked_account:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Linked account not found"
            )
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == req.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        if not bot_manager.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Bot is not ready"
            )
        
        guild = bot_manager.get_guild(int(req.server_id))
        if not guild:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server not found or bot not in server"
            )
        
        member = guild.get_member(int(current_user.discord_id))
        if not member:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="User not found in server"
            )
        
        await bot_manager.apply_server_config_sync_with_tracking(
            member, server_config, linked_account, db, is_update=True
        )
        
        discord_cache.invalidate_user_guilds_cache(current_user.discord_id)
        
        return APIResponse(
            success=True,
            message="User verified in server successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error verifying user in server: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/user/verification-servers", response_model=List[UserServerSchema])
async def get_user_verification_servers(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        print(f"Getting verification servers for user {current_user.discord_id} ({current_user.username})")
        
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        from models import VerificationServer
        verification_servers_query = select(VerificationServer).where(VerificationServer.user_id == current_user.id)
        result = await db.execute(verification_servers_query)
        all_verification_servers = result.scalars().all()
        
        print(f"Found {len(all_verification_servers)} total verification servers")
        for server in all_verification_servers:
            print(f"Server: {server.server_name} (ID: {server.server_id}), Bot added: {server.bot_added}")
        
        if bot_manager.is_ready():
            bot_guilds = {str(guild.id): guild.member_count for guild in bot_manager.get_guilds()}
            print(f"Bot is ready, found {len(bot_guilds)} bot guilds")
            print(f"Bot guild IDs: {list(bot_guilds.keys())}")
            
            verification_servers = []
            for server in all_verification_servers:
                if server.bot_added:
                    server.member_count = bot_guilds.get(server.server_id)
                    verification_servers.append(server)
                    print(f"Server {server.server_name} (ID: {server.server_id}) - Bot added, member count: {server.member_count}")
                else:
                    print(f"Server {server.server_name} (ID: {server.server_id}) - Bot NOT added")
        else:
            print("Bot is not ready")
            verification_servers = []
        
        print(f"Returning {len(verification_servers)} verification servers")
        return verification_servers
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting verification servers: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/cache/stats", response_model=dict)
async def get_cache_stats(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        cache_stats = discord_cache.get_cache_stats()
        
        return cache_stats
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting cache stats: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/cache/clear", response_model=APIResponse)
async def clear_cache(
    current_user: User = Depends(auth_manager.get_current_user)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        discord_cache.clear()
        
        return APIResponse(
            success=True,
            message="All caches cleared successfully"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing cache: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/debug/state", response_model=dict)
async def get_debug_state(
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        if check_discord_token_expiration(current_user):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Discord token expired",
                headers={"x-token-expired": "true"}
            )
        
        user_servers_query = select(UserServer).where(UserServer.user_id == current_user.id)
        result = await db.execute(user_servers_query)
        user_servers = result.scalars().all()
        
        bot_guilds = []
        if bot_manager.is_ready():
            bot_guilds = [{"id": str(guild.id), "name": guild.name} for guild in bot_manager.get_guilds()]
        
        debug_data = {
            "user": {
                "discord_id": current_user.discord_id,
                "username": current_user.username
            },
            "user_servers": [
                {
                    "server_id": server.server_id,
                    "server_name": server.server_name,
                    "bot_added": server.bot_added,
                    "owner": server.owner,
                    "permissions": server.permissions
                } for server in user_servers
            ],
            "bot_guilds": bot_guilds,
            "bot_ready": bot_manager.is_ready(),
            "cache_stats": discord_cache.get_cache_stats()
        }
        
        return debug_data
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting debug state: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )