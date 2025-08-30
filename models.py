from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text, JSON
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime

Base = declarative_base()

class User(Base):
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    discord_id = Column(String(255), unique=True, index=True, nullable=False)
    username = Column(String(255), nullable=False)
    discriminator = Column(String(10), nullable=True)
    avatar = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    linked_accounts = relationship("LinkedAccount", back_populates="user")
    user_servers = relationship("UserServer", back_populates="user")
    verification_servers = relationship("VerificationServer", back_populates="user")
    sessions = relationship("UserSession", back_populates="user")

class UserSession(Base):
    __tablename__ = "user_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    session_token = Column(String(255), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="sessions")

class LinkedAccount(Base):
    __tablename__ = "linked_accounts"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    roblox_username = Column(String(255), nullable=False)
    roblox_id = Column(String(255), nullable=True)
    roblox_avatar = Column(String(500), nullable=True)
    verified = Column(Boolean, default=False)
    verification_code = Column(String(255), nullable=True)
    linked_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="linked_accounts")

class UserServer(Base):
    __tablename__ = "user_servers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    server_id = Column(String(255), nullable=False)
    server_name = Column(String(255), nullable=False)
    server_icon = Column(String(500), nullable=True)
    owner = Column(Boolean, default=False)
    permissions = Column(String(255), nullable=True)
    bot_added = Column(Boolean, default=False)
    added_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="user_servers")
    
    @property
    def member_count(self):
        return getattr(self, '_member_count', None)
    
    @member_count.setter
    def member_count(self, value):
        self._member_count = value

class BotServer(Base):
    __tablename__ = "bot_servers"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(String(255), unique=True, index=True, nullable=False)
    server_name = Column(String(255), nullable=False)
    server_icon = Column(String(500), nullable=True)
    owner_id = Column(String(255), nullable=False)
    member_count = Column(Integer, default=0)
    joined_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class ServerConfig(Base):
    __tablename__ = "server_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    server_id = Column(String(255), unique=True, index=True, nullable=False)
    
    nickname_format = Column(String(50), nullable=False, default="roblox_username")
    
    verified_role_enabled = Column(Boolean, default=True)
    verified_role_name = Column(String(100), default="Verified")
    verified_role_id = Column(String(255), nullable=True)
    roles_to_remove = Column(String(500), nullable=True)
    
    group_id = Column(String(255), nullable=True)
    group_name = Column(String(255), nullable=True)
    group_roles_enabled = Column(Boolean, default=False)
    
    setup_completed = Column(Boolean, default=False)
    setup_step = Column(String(50), default="nickname")
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    group_roles = relationship("GroupRole", back_populates="server_config")

class GroupRole(Base):
    __tablename__ = "group_roles"
    
    id = Column(Integer, primary_key=True, index=True)
    server_config_id = Column(Integer, ForeignKey("server_configs.id"), nullable=False)
    
    roblox_role_id = Column(String(255), nullable=False)
    roblox_role_name = Column(String(255), nullable=False)
    roblox_role_rank = Column(Integer, nullable=False)
    
    discord_role_id = Column(String(255), nullable=True)
    discord_role_name = Column(String(255), nullable=False)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    server_config = relationship("ServerConfig", back_populates="group_roles")

class VerificationServer(Base):
    __tablename__ = "verification_servers"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    server_id = Column(String(255), nullable=False)
    server_name = Column(String(255), nullable=False)
    server_icon = Column(String(500), nullable=True)
    owner = Column(Boolean, default=False)
    permissions = Column(String(255), nullable=True)
    bot_added = Column(Boolean, default=False)
    member_count = Column(Integer, default=0)
    added_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    user = relationship("User", back_populates="verification_servers")