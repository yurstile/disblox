from pydantic import BaseModel, Field, validator, HttpUrl
from typing import Optional, List
from datetime import datetime
import re

class UserBase(BaseModel):
    discord_id: str
    username: str
    discriminator: Optional[str] = None
    avatar: Optional[str] = None

class UserCreate(UserBase):
    pass

class User(UserBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class LinkedAccountBase(BaseModel):
    roblox_username: str
    roblox_id: Optional[str] = None
    roblox_avatar: Optional[str] = None

class LinkedAccountCreate(LinkedAccountBase):
    pass

class LinkedAccount(LinkedAccountBase):
    id: int
    user_id: int
    verified: bool
    linked_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class UserServerBase(BaseModel):
    server_id: str
    server_name: str
    server_icon: Optional[str] = None
    owner: bool
    permissions: Optional[str] = None

class UserServerCreate(UserServerBase):
    pass

class UserServer(UserServerBase):
    id: int
    user_id: int
    bot_added: bool
    added_at: datetime
    updated_at: datetime
    member_count: Optional[int] = None
    
    class Config:
        from_attributes = True

class BotServerBase(BaseModel):
    server_id: str
    server_name: str
    server_icon: Optional[str] = None
    owner_id: str
    member_count: int

class BotServer(BotServerBase):
    id: int
    joined_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ServerConfigBase(BaseModel):
    server_id: str
    nickname_format: str = "roblox_username"
    verified_role_enabled: bool = True
    verified_role_name: str = "Verified"
    verified_role_id: Optional[str] = None
    group_id: Optional[str] = None
    group_name: Optional[str] = None
    group_roles_enabled: bool = False
    setup_completed: bool = False
    setup_step: str = "nickname"

class ServerConfigCreate(ServerConfigBase):
    pass

class ServerConfig(ServerConfigBase):
    id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class GroupRoleBase(BaseModel):
    roblox_role_id: str
    roblox_role_name: str
    roblox_role_rank: int
    discord_role_id: Optional[str] = None
    discord_role_name: str

class GroupRoleCreate(GroupRoleBase):
    pass

class GroupRole(GroupRoleBase):
    id: int
    server_config_id: int
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

class ServerSetupStep(BaseModel):
    step: Optional[str] = Field(None, max_length=50, description="Setup step")
    nickname_format: Optional[str] = Field(
        None, 
        max_length=50,
        description="Nickname format type"
    )
    verified_role_enabled: Optional[bool] = Field(None, description="Enable verified role")
    verified_role_name: Optional[str] = Field(None, max_length=100, description="Verified role name")
    verified_role_id: Optional[str] = Field(None, max_length=50, description="Discord role ID")
    roles_to_remove: Optional[List[str]] = Field(None, description="Role IDs to remove")
    group_id: Optional[str] = Field(None, max_length=255, description="Roblox group ID")
    group_url: Optional[str] = Field(None, max_length=500, description="Roblox group URL")
    skip: Optional[bool] = Field(None, description="Skip this setup step")
    
    @validator('nickname_format')
    def validate_nickname_format(cls, v):
        if v is not None:
            valid_formats = [
                "roblox_username", "roblox_display", "discord_display", 
                "discord_username", "discord_display_with_roblox", "none"
            ]
            if v not in valid_formats:
                raise ValueError(f'Nickname format must be one of: {", ".join(valid_formats)}')
        return v
    
    @validator('verified_role_id')
    def validate_verified_role_id(cls, v):
        if v is not None and not v.isdigit():
            raise ValueError('Verified role ID must be a numeric string')
        return v
    
    @validator('group_id')
    def validate_group_id(cls, v):
        if v is not None:
            if v.startswith('http'):
                raise ValueError('Group ID should be numeric only. Use group_url for URLs.')
            if not v.isdigit():
                raise ValueError('Group ID must be a numeric string')
        return v
    
    @validator('group_url')
    def validate_group_url(cls, v):
        if v is not None:
            if not (v.isdigit() or 
                   re.match(r'https?://(www\.)?roblox\.com/groups/\d+', v) or
                   re.match(r'https?://(www\.)?roblox\.com/communities/\d+', v)):
                raise ValueError('Group URL must be a valid Roblox group URL or numeric group ID')
        return v
    
    @validator('roles_to_remove')
    def validate_roles_to_remove(cls, v):
        if v is not None:
            for role_id in v:
                if not role_id.isdigit():
                    raise ValueError('All role IDs must be numeric strings')
        return v

class ServerSetupResponse(BaseModel):
    success: bool
    message: str
    current_step: str
    setup_completed: bool
    config: Optional[ServerConfig] = None

class DashboardData(BaseModel):
    user: User
    linked_accounts: List[LinkedAccount]
    user_servers: List[UserServer]
    total_linked_accounts: int
    total_servers: int
    servers_with_bot: int

class BotStatusResponse(BaseModel):
    server_id: str
    server_name: str
    bot_present: bool
    bot_added: bool
    can_add_bot: bool
    permissions: Optional[str] = None

class APIResponse(BaseModel):
    success: bool
    message: str
    data: Optional[dict] = None

class ErrorResponse(BaseModel):
    success: bool
    error: str
    details: Optional[str] = None

class VerifyInServerRequest(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=50, description="Discord server ID")
    account_id: int = Field(..., gt=0, description="Linked account ID to verify")
    
    @validator('server_id')
    def validate_server_id(cls, v):
        if not v.isdigit():
            raise ValueError('Server ID must be a numeric string')
        return v

class AuthCallbackRequest(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=1000, description="Discord authorization code")
    state: Optional[str] = Field(None, max_length=100, description="State parameter for CSRF protection")

class RobloxCallbackRequest(BaseModel):
    code: Optional[str] = Field(None, min_length=1, max_length=1000, description="Roblox authorization code")
    state: Optional[str] = Field(None, max_length=100, description="State parameter for CSRF protection")
    error: Optional[str] = Field(None, max_length=200, description="Error parameter from OAuth")
    error_description: Optional[str] = Field(None, max_length=500, description="Error description from OAuth")
    
    @validator('code')
    def validate_code_or_error(cls, v, values):
        error = values.get('error')
        if v is None and error is None:
            raise ValueError('Either code or error must be provided')
        if v is not None and error is not None:
            raise ValueError('Cannot have both code and error')
        return v

class ServerIdPath(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=50, description="Discord server ID")
    
    @validator('server_id')
    def validate_server_id(cls, v):
        if not v.isdigit():
            raise ValueError('Server ID must be a numeric string')
        return v

class AccountIdPath(BaseModel):
    account_id: int = Field(..., gt=0, description="Account ID to unlink")

class CacheClearRequest(BaseModel):
    confirm: bool = Field(..., description="Confirmation to clear cache")

class ManualSyncRequest(BaseModel):
    force: Optional[bool] = Field(False, description="Force sync even if recently synced")

class BotStatusRequest(BaseModel):
    server_id: str = Field(..., min_length=1, max_length=50, description="Discord server ID")
    
    @validator('server_id')
    def validate_server_id(cls, v):
        if not v.isdigit():
            raise ValueError('Server ID must be a numeric string')
        return v

class PaginationParams(BaseModel):
    page: Optional[int] = Field(1, ge=1, le=1000, description="Page number")
    limit: Optional[int] = Field(50, ge=1, le=100, description="Items per page")

class SearchParams(BaseModel):
    query: Optional[str] = Field(None, max_length=200, description="Search query")
    sort_by: Optional[str] = Field(None, max_length=50, description="Sort field")
    sort_order: Optional[str] = Field(None, pattern='^(asc|desc)$', description="Sort order")

class ValidationErrorResponse(BaseModel):
    success: bool = False
    error: str = Field(..., description="Validation error message")
    details: Optional[dict] = Field(None, description="Validation error details")

class RateLimitResponse(BaseModel):
    success: bool = False
    error: str = "Rate limit exceeded"
    retry_after: int = Field(..., description="Seconds to wait before retrying")

class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, description="Refresh token for authentication")