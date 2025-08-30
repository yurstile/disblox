import re
import httpx
import discord
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Query, Path, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload
from typing import List, Optional
import json
from datetime import datetime
from pydantic import ValidationError

from database import get_db
from models import User, ServerConfig as ServerConfigModel, GroupRole, BotServer, UserServer
from schemas import (
    ServerConfig,
    ServerSetupStep,
    ServerSetupResponse,
    APIResponse,
    ErrorResponse,
    ServerIdPath,
    ValidationErrorResponse
)
from auth import auth_manager
from bot_manager import bot_manager

router = APIRouter(prefix="/api/v1/server", tags=["server"])

async def create_discord_role_safely(guild, role_name, reason, color=discord.Color.blue()):
    try:
        result = await bot_manager.safe_discord_operation(
            guild.create_role,
                    name=role_name,
                    color=color,
                    reason=reason
            )
        
        return result
    except Exception as e:
        return None

async def edit_discord_role_name(guild, role_id, new_name, reason):
    try:
        role = guild.get_role(int(role_id))
        if not role:
            return None
        
        result = await bot_manager.safe_discord_operation(
            role.edit,
            name=new_name,
            reason=reason
        )
        
        return result
    except Exception as e:
        return None

def extract_group_id_from_url(group_url: str) -> Optional[str]:
    patterns = [
        r'https?://www\.roblox\.com/groups/(\d+)',
        r'https?://www\.roblox\.com/communities/(\d+)',
        r'https?://web\.roblox\.com/groups/(\d+)',
        r'https?://web\.roblox\.com/communities/(\d+)',
        r'https?://roblox\.com/groups/(\d+)',
        r'https?://roblox\.com/communities/(\d+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, group_url)
        if match:
            return match.group(1)
    
    if group_url.isdigit():
        return group_url
    
    return None

async def get_roblox_group_info(group_id: str) -> dict:
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(f"https://groups.roblox.com/v1/groups/{group_id}")
            if response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail="Invalid group ID or group not found"
                )
            
            group_data = response.json()
            
            roles_response = await client.get(f"https://groups.roblox.com/v1/groups/{group_id}/roles")
            if roles_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST, 
                    detail="Failed to fetch group roles"
                )
            
            roles_data = roles_response.json()
            
            return {
                "group_id": group_id,
                "group_name": group_data.get("name", "Unknown"),
                "group_description": group_data.get("description", ""),
                "group_owner": group_data.get("owner", {}),
                "roles": roles_data.get("roles", [])
            }
        except Exception as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Failed to fetch group information: {str(e)}"
            )

@router.get("/{server_id}/config", response_model=dict)
async def get_server_config(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        return {
            "id": server_config.id,
            "server_id": server_config.server_id,
            "nickname_format": server_config.nickname_format,
            "verified_role_enabled": server_config.verified_role_enabled,
            "verified_role_name": server_config.verified_role_name,
            "verified_role_id": server_config.verified_role_id,
            "roles_to_remove": server_config.roles_to_remove,
            "group_id": server_config.group_id,
            "group_name": server_config.group_name,
            "group_roles_enabled": server_config.group_roles_enabled,
            "setup_completed": server_config.setup_completed,
            "setup_step": server_config.setup_step,
            "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
            "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
        }
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{server_id}/setup", response_model=ServerSetupResponse)
async def get_server_setup_status(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            return ServerSetupResponse(
                success=True,
                message="Server setup not started",
                current_step="nickname",
                setup_completed=False,
                config=None
            )
    
        return ServerSetupResponse(
            success=True,
            message="Server setup status retrieved",
            current_step=server_config.setup_step,
            setup_completed=server_config.setup_completed,
            config={
                "id": server_config.id,
                "server_id": server_config.server_id,
                "nickname_format": server_config.nickname_format,
                "verified_role_enabled": server_config.verified_role_enabled,
                "verified_role_name": server_config.verified_role_name,
                "verified_role_id": server_config.verified_role_id,
                "roles_to_remove": server_config.roles_to_remove,
                "group_id": server_config.group_id,
                "group_name": server_config.group_name,
                "group_roles_enabled": server_config.group_roles_enabled,
                "setup_completed": server_config.setup_completed,
                "setup_step": server_config.setup_step,
                "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
                "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
            }
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{server_id}/setup/nickname", response_model=ServerSetupResponse)
async def setup_nickname_format(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            server_config = ServerConfigModel(
                server_id=server_data.server_id,
                setup_step="nickname"
            )
            db.add(server_config)
        
        if setup_data.nickname_format:
            server_config.nickname_format = setup_data.nickname_format
        
        server_config.setup_step = "verified_role"
        await db.commit()
        await db.refresh(server_config)
        
        return ServerSetupResponse(
            success=True,
            message="Nickname format configured successfully",
            current_step=server_config.setup_step,
            setup_completed=server_config.setup_completed,
            config={
                "id": server_config.id,
                "server_id": server_config.server_id,
                "nickname_format": server_config.nickname_format,
                "verified_role_enabled": server_config.verified_role_enabled,
                "verified_role_name": server_config.verified_role_name,
                "verified_role_id": server_config.verified_role_id,
                "roles_to_remove": server_config.roles_to_remove,
                "group_id": server_config.group_id,
                "group_name": server_config.group_name,
                "group_roles_enabled": server_config.group_roles_enabled,
                "setup_completed": server_config.setup_completed,
                "setup_step": server_config.setup_step,
                "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
                "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
            }
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{server_id}/setup/verified-role", response_model=ServerSetupResponse)
async def setup_verified_role(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please configure nickname format first"
            )
        
        if setup_data.verified_role_enabled is not None:
            server_config.verified_role_enabled = setup_data.verified_role_enabled
        
        if setup_data.verified_role_name:
            server_config.verified_role_name = setup_data.verified_role_name
        
        if setup_data.roles_to_remove:
            server_config.roles_to_remove = ",".join(setup_data.roles_to_remove)
        
        server_config.setup_step = "group"
        await db.commit()
        await db.refresh(server_config)
        
        return ServerSetupResponse(
            success=True,
            message="Verified role configured successfully",
            current_step=server_config.setup_step,
            setup_completed=server_config.setup_completed,
            config={
                "id": server_config.id,
                "server_id": server_config.server_id,
                "nickname_format": server_config.nickname_format,
                "verified_role_enabled": server_config.verified_role_enabled,
                "verified_role_name": server_config.verified_role_name,
                "verified_role_id": server_config.verified_role_id,
                "roles_to_remove": server_config.roles_to_remove,
                "group_id": server_config.group_id,
                "group_name": server_config.group_name,
                "group_roles_enabled": server_config.group_roles_enabled,
                "setup_completed": server_config.setup_completed,
                "setup_step": server_config.setup_step,
                "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
                "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
            }
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.post("/{server_id}/setup/group", response_model=ServerSetupResponse)
async def setup_group_config(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please configure previous steps first"
            )
        
        group_id = None
        
        if setup_data.skip:
            server_config.group_roles_enabled = False
            server_config.setup_step = "completed"
            server_config.setup_completed = True
            
            try:
                guild = bot_manager.get_guild(int(server_data.server_id))
                if guild:
                    if server_config.verified_role_enabled and server_config.verified_role_name:
                        verified_role = await create_discord_role_safely(
                            guild,
                            server_config.verified_role_name,
                            "Verified role for Disblox bot"
                        )
                        
                        if verified_role:
                            server_config.verified_role_id = str(verified_role.id)
            except Exception as e:
                pass
            
            await db.commit()
            await db.refresh(server_config)
            
            return ServerSetupResponse(
                success=True,
                message="Group setup skipped successfully",
                current_step="completed",
                setup_completed=True,
                config=server_config
            )
        
        if setup_data.group_url:
            group_id = extract_group_id_from_url(setup_data.group_url)
            if not group_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Roblox group URL"
                )
        elif setup_data.group_id:
            group_id = setup_data.group_id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please provide either a group URL or group ID, or set skip to true"
            )
        
        if group_id:
            group_info = await get_roblox_group_info(group_id)
            server_config.group_id = group_id
            server_config.group_name = group_info["group_name"]
            server_config.group_roles_enabled = True
            
            filtered_roles = [role for role in group_info["roles"] if role["rank"] > 0]
            filtered_roles.sort(key=lambda x: x["rank"], reverse=True)
            
            created_roles = []
            for role in filtered_roles:
                group_role = GroupRole(
                    server_config_id=server_config.id,
                    roblox_role_id=str(role["id"]),
                    roblox_role_name=role["name"],
                    roblox_role_rank=role["rank"],
                    discord_role_name=role["name"]
                )
                db.add(group_role)
                created_roles.append(group_role)
            
            await db.commit()
            await db.refresh(server_config)
            
            try:
                guild = bot_manager.get_guild(int(server_data.server_id))
                if guild:
                    successful_creations = 0
                    for i, group_role in enumerate(created_roles, 1):
                        discord_role = await create_discord_role_safely(
                            guild,
                            group_role.discord_role_name,
                            f"Roblox group role: {group_role.roblox_role_name} (Rank: {group_role.roblox_role_rank})"
                        )
                        
                        if discord_role:
                            group_role.discord_role_id = str(discord_role.id)
                            successful_creations += 1
                    
                    await db.commit()
                    if server_config.verified_role_enabled and server_config.verified_role_name and not server_config.verified_role_id:
                        verified_role = await create_discord_role_safely(
                            guild,
                            server_config.verified_role_name,
                            "Verified role for Disblox bot"
                        )
                        
                        if verified_role:
                            server_config.verified_role_id = str(verified_role.id)
            except Exception as e:
                pass
        
        server_config.setup_completed = True
        server_config.setup_step = "completed"
        await db.commit()
        await db.refresh(server_config)
        
        return ServerSetupResponse(
            success=True,
            message="Group configuration completed successfully",
            current_step=server_config.setup_step,
            setup_completed=server_config.setup_completed,
            config={
                "id": server_config.id,
                "server_id": server_config.server_id,
                "nickname_format": server_config.nickname_format,
                "verified_role_enabled": server_config.verified_role_enabled,
                "verified_role_name": server_config.verified_role_name,
                "verified_role_id": server_config.verified_role_id,
                "roles_to_remove": server_config.roles_to_remove,
                "group_id": server_config.group_id,
                "group_name": server_config.group_name,
                "group_roles_enabled": server_config.group_roles_enabled,
                "setup_completed": server_config.setup_completed,
                "setup_step": server_config.setup_step,
                "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
                "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
            }
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{server_id}/group-roles", response_model=List[dict])
async def get_group_roles(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config or not server_config.group_id:
            return []
        
        roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
        result = await db.execute(roles_query)
        group_roles = result.scalars().all()
        
        return [
            {
                "id": role.id,
                "roblox_role_id": role.roblox_role_id,
                "roblox_role_name": role.roblox_role_name,
                "roblox_role_rank": role.roblox_role_rank,
                "discord_role_id": role.discord_role_id,
                "discord_role_name": role.discord_role_name
            }
            for role in group_roles
        ]
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.get("/{server_id}/edit", response_model=dict)
async def get_server_edit_data(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        group_roles = []
        if server_config.group_id:
            roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            result = await db.execute(roles_query)
            group_roles = result.scalars().all()
        
        return {
            "server_config": {
                "id": server_config.id,
                "server_id": server_config.server_id,
                "server_name": user_server.server_name,
                "nickname_format": server_config.nickname_format,
                "verified_role_enabled": server_config.verified_role_enabled,
                "verified_role_name": server_config.verified_role_name,
                "verified_role_id": server_config.verified_role_id,
                "roles_to_remove": server_config.roles_to_remove,
                "group_id": server_config.group_id,
                "group_name": server_config.group_name,
                "group_roles_enabled": server_config.group_roles_enabled,
                "setup_completed": server_config.setup_completed,
                "setup_step": server_config.setup_step,
                "created_at": server_config.created_at.isoformat() if server_config.created_at else None,
                "updated_at": server_config.updated_at.isoformat() if server_config.updated_at else None
            },
            "group_roles": [
                {
                    "id": role.id,
                    "roblox_role_id": role.roblox_role_id,
                    "roblox_role_name": role.roblox_role_name,
                    "roblox_role_rank": role.roblox_role_rank,
                    "discord_role_id": role.discord_role_id,
                    "discord_role_name": role.discord_role_name
                }
                for role in group_roles
            ]
        }
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.put("/{server_id}/edit/nickname", response_model=APIResponse)
async def edit_nickname_format(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        if setup_data.nickname_format:
            server_config.nickname_format = setup_data.nickname_format
        
        await db.commit()
        
        return APIResponse(
            success=True,
            message="Nickname format updated successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        )

@router.put("/{server_id}/edit/verified-role", response_model=APIResponse)
async def edit_verified_role(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
    
        if setup_data.verified_role_enabled is not None:
            server_config.verified_role_enabled = setup_data.verified_role_enabled
        
        if setup_data.verified_role_name:
            if server_config.verified_role_id:
                try:
                    guild = bot_manager.get_guild(int(server_data.server_id))
                    if guild:
                        edited_role = await edit_discord_role_name(
                            guild,
                            server_config.verified_role_id,
                            setup_data.verified_role_name,
                            "Updated verified role name"
                        )
                        if edited_role:
                            pass
                except Exception as e:
                    pass
            server_config.verified_role_name = setup_data.verified_role_name
        
        if setup_data.verified_role_id:
            server_config.verified_role_id = setup_data.verified_role_id
        
        if setup_data.roles_to_remove:
            server_config.roles_to_remove = ",".join(setup_data.roles_to_remove)
        
        await db.commit()
        
        return APIResponse(
            success=True,
            message="Verified role updated successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
    )

@router.put("/{server_id}/edit/group", response_model=APIResponse)
async def edit_group_config(
    setup_data: ServerSetupStep,
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        group_id = None
        
        if setup_data.skip:
            server_config.group_roles_enabled = False
            server_config.group_id = None
            server_config.group_name = None
            
            existing_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            result = await db.execute(existing_roles_query)
            existing_roles = result.scalars().all()
            for role in existing_roles:
                await db.delete(role)
            
            await db.commit()
            await db.refresh(server_config)
            
            return APIResponse(
                success=True,
                message="Group configuration disabled successfully"
            )
        
        if setup_data.group_url:
            group_id = extract_group_id_from_url(setup_data.group_url)
            if not group_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invalid Roblox group URL"
                )
        elif setup_data.group_id:
            group_id = setup_data.group_id
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Please provide either a group URL or group ID, or set skip to true"
            )
        
        if group_id:
            group_info = await get_roblox_group_info(group_id)
            server_config.group_id = group_id
            server_config.group_name = group_info["group_name"]
            server_config.group_roles_enabled = True
            
            existing_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            result = await db.execute(existing_roles_query)
            existing_roles = result.scalars().all()
            for role in existing_roles:
                await db.delete(role)
            
            filtered_roles = [role for role in group_info["roles"] if role["rank"] > 0]
            filtered_roles.sort(key=lambda x: x["rank"], reverse=True)
            
            created_roles = []
            for role in filtered_roles:
                group_role = GroupRole(
                    server_config_id=server_config.id,
                    roblox_role_id=str(role["id"]),
                    roblox_role_name=role["name"],
                    roblox_role_rank=role["rank"],
                    discord_role_name=role["name"]
                )
                db.add(group_role)
                created_roles.append(group_role)
            
            await db.commit()
            await db.refresh(server_config)
            
            try:
                guild = bot_manager.get_guild(int(server_data.server_id))
                if guild:
                    successful_creations = 0
                    for i, group_role in enumerate(created_roles, 1):
                        discord_role = await create_discord_role_safely(
                            guild,
                            group_role.discord_role_name,
                            f"Roblox group role: {group_role.roblox_role_name} (Rank: {group_role.roblox_role_rank})"
                        )
                        
                        if discord_role:
                            group_role.discord_role_id = str(discord_role.id)
                            successful_creations += 1
                    
                    await db.commit()
                    if server_config.verified_role_enabled and server_config.verified_role_name and not server_config.verified_role_id:
                        verified_role = await create_discord_role_safely(
                            guild,
                            server_config.verified_role_name,
                            "Verified role for Disblox bot"
                        )
                        
                        if verified_role:
                            server_config.verified_role_id = str(verified_role.id)
            except Exception as e:
                pass
        
        server_config.setup_completed = True
        server_config.setup_step = "completed"
        await db.commit()
        await db.refresh(server_config)
        
        return APIResponse(
            success=True,
            message="Group configuration updated successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
    )

@router.delete("/{server_id}/config", response_model=APIResponse)
async def reset_server_config(
    server_id: str = Path(..., min_length=1, max_length=50, description="Discord server ID"),
    current_user: User = Depends(auth_manager.get_current_user),
    db: AsyncSession = Depends(get_db)
):
    try:
        server_data = ServerIdPath(server_id=server_id)
        
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
        
        config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == server_data.server_id)
        result = await db.execute(config_query)
        server_config = result.scalar_one_or_none()
        
        if not server_config:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Server configuration not found"
            )
        
        roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
        result = await db.execute(roles_query)
        group_roles = result.scalars().all()
        for role in group_roles:
            await db.delete(role)
        
        await db.delete(server_config)
        await db.commit()
        
        return APIResponse(
            success=True,
            message="Server configuration reset successfully"
        )
        
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Validation error: {str(e)}"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Internal server error: {str(e)}"
        ) 