import discord
from discord.ext import commands
import threading
import time
from typing import Optional, List
from config import config
import asyncio
import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_, or_, update
from sqlalchemy.orm import selectinload
from typing import List, Optional, Tuple, Dict, Any
import asyncio
import discord
from discord.ext import commands
import logging
import time
from datetime import datetime, timedelta

from database import get_db, get_sync_db
from models import ServerConfig as ServerConfigModel, GroupRole, LinkedAccount, User
from datetime import datetime

class BotManager:
    def __init__(self):
        self.bot = None
        self.bot_thread = None
        self.bot_ready = False
        self.bot_guilds = []
        self.bot_user = None
        self.start_time = None
        self._lock = threading.Lock()
        self._bot_loop = None
        
    def create_bot(self):
        self.bot = commands.Bot(
            command_prefix="d!",
            intents=discord.Intents.all(),
            application_id=config.DISCORD_APPLICATION_ID
        )
        self.start_time = time.time()
        
        @self.bot.event
        async def on_ready():
            with self._lock:
                self.bot_ready = True
                self.bot_guilds = list(self.bot.guilds)
                self.bot_user = self.bot.user
                self._bot_loop = asyncio.get_event_loop()
            
            print(f"Bot is ready! Logged in as {self.bot.user}")
            print(f"Bot is in {len(self.bot.guilds)} guilds:")
            for guild in self.bot.guilds:
                print(f"  - {guild.name} (ID: {guild.id})")
            
            try:
                await self.sync_slash_commands()
            except Exception as e:
                print(f"Failed to sync slash commands: {e}")
            
            total_members = sum(guild.member_count for guild in self.bot.guilds)
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users")
            await self.bot.change_presence(activity=activity)
        
        @self.bot.event
        async def on_guild_join(guild):
            with self._lock:
                self.bot_guilds = list(self.bot.guilds)
            
            total_members = sum(guild.member_count for guild in self.bot.guilds)
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users")
            await self.bot.change_presence(activity=activity)
        
        @self.bot.event
        async def on_guild_remove(guild):
            with self._lock:
                self.bot_guilds = list(self.bot.guilds)
            await self.cleanup_server_config(guild.id)
            
            total_members = sum(guild.member_count for guild in self.bot.guilds)
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users")
            await self.bot.change_presence(activity=activity)
        
        @self.bot.event
        async def on_member_join(member):
            await self.handle_member_join(member)
            # Update presence with new member count
            total_members = sum(guild.member_count for guild in self.bot.guilds)
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users")
            await self.bot.change_presence(activity=activity)
        
        @self.bot.event
        async def on_member_update(before, after):
            if before.nick != after.nick:
                await self.handle_member_update(before, after)
        
        @self.bot.event
        async def on_member_remove(member):
            await self.handle_member_remove(member)
            # Update presence with new member count
            total_members = sum(guild.member_count for guild in self.bot.guilds)
            activity = discord.Activity(type=discord.ActivityType.watching, name=f"{total_members} users")
            await self.bot.change_presence(activity=activity)
        
        @self.bot.event
        async def on_member_ban(guild, user):
            await self.handle_member_remove(user)
        
        @self.bot.event
        async def on_member_unban(guild, user):
            pass
        
        @self.bot.tree.command(name="verify", description="Verify your Roblox account")
        async def verify_command(interaction: discord.Interaction):
            try:
                if not interaction.guild:
                    await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
                    return
                
                # Defer the response immediately to prevent timeout
                await interaction.response.defer(ephemeral=True)
                
                with get_sync_db() as db:
                    user_query = select(User).where(User.discord_id == str(interaction.user.id))
                    result = db.execute(user_query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        await interaction.followup.send("You need to link your Discord account first. Visit our dashboard to get started. https://your-frontend.com/dashboard", ephemeral=True)
                        return
                    
                    accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                    result = db.execute(accounts_query)
                    linked_accounts = result.scalars().all()
                    
                    if not linked_accounts:
                        await interaction.followup.send("You don't have any Roblox accounts linked. Visit our dashboard to link your account. https://your-frontend.com/dashboard", ephemeral=True)
                        return
                    
                    config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(interaction.guild.id))
                    result = db.execute(config_query)
                    server_config = result.scalar_one_or_none()
                    
                    if not server_config or not server_config.setup_completed:
                        await interaction.followup.send("This server is not configured for verification. Please contact an administrator.", ephemeral=True)
                        return
                    
                    success, message, embed_data = await self.verify_user(interaction.user)
                    
                    if success:
                        if embed_data:
                            embed = self.create_verification_embed(embed_data)
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        else:
                            await interaction.followup.send("Verification completed successfully!", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Verification failed: {message}", ephemeral=True)
                        
            except Exception as e:
                try:
                    await interaction.followup.send("An error occurred during verification. Please try again.", ephemeral=True)
                except:
                    pass
        
        @self.bot.tree.command(name="update", description="Update your roles and nickname")
        async def update_command(interaction: discord.Interaction, user: discord.Member = None):
            try:
                if not interaction.guild:
                    await interaction.response.send_message("This command can only be used in a server.", ephemeral=True)
                    return
                
                # Defer the response immediately to prevent timeout
                await interaction.response.defer(ephemeral=True)
                
                target_user = user or interaction.user
                
                # Check permission if updating someone else
                if target_user != interaction.user:
                    if not interaction.user.guild_permissions.manage_roles:
                        await interaction.followup.send("You need 'Manage Roles' permission to update other users.", ephemeral=True)
                        return
                
                with get_sync_db() as db:
                    user_query = select(User).where(User.discord_id == str(target_user.id))
                    result = db.execute(user_query)
                    user = result.scalar_one_or_none()
                    
                    if not user:
                        await interaction.followup.send("This user doesn't have a linked Discord account.", ephemeral=True)
                        return
                
                    accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                    result = db.execute(accounts_query)
                    linked_accounts = result.scalars().all()
                    
                    if not linked_accounts:
                        await interaction.followup.send("This user doesn't have any Roblox accounts linked.", ephemeral=True)
                        return
                    
                    config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(interaction.guild.id))
                    result = db.execute(config_query)
                    server_config = result.scalar_one_or_none()
                    
                    if not server_config or not server_config.setup_completed:
                        await interaction.followup.send("This server is not configured for verification. Please contact an administrator.", ephemeral=True)
                        return
                    
                    success, message, embed_data = await self.update_user(target_user)
                    
                    if success:
                        if embed_data:
                            embed = self.create_update_embed(embed_data)
                            await interaction.followup.send(embed=embed, ephemeral=True)
                        else:
                            await interaction.followup.send("Update completed successfully!", ephemeral=True)
                    else:
                        await interaction.followup.send(f"Update failed: {message}", ephemeral=True)
                        
            except Exception as e:
                try:
                    await interaction.followup.send("An error occurred during update. Please try again.", ephemeral=True)
                except:
                    pass

        @self.bot.tree.command(name="verifychannel", description="Send verification message to current channel")
        async def verifychannel_command(interaction: discord.Interaction):
            """Send verification message with buttons"""
            try:
                
                if not interaction.user.guild_permissions.manage_messages:
                    await interaction.response.send_message("You need 'Manage Messages' permission to use this command.", ephemeral=True)
                    return
                
                with get_sync_db() as db:
                    config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(interaction.guild.id))
                    result = db.execute(config_query)
                    server_config = result.scalar_one_or_none()
                    
                    if not server_config or not server_config.setup_completed:
                        await interaction.response.send_message("This server is not configured for verification. Please set up the bot first.", ephemeral=True)
                        return
                
                embed = discord.Embed(
                    title=f"👋 Welcome to {interaction.guild.name}!",
                    description=f"Click the button below to verify with Disblox and gain access to the rest of the server.",
                    color=0x5865F2,
                    timestamp=datetime.utcnow()
                )
                embed.set_footer(text="Disblox Verification System")
                
                verify_button = discord.ui.Button(
                    style=discord.ButtonStyle.primary,
                    label="Verify with Disblox",
                    custom_id="verify_button",
                    emoji="✅"
                )
                
                help_button = discord.ui.Button(
                    style=discord.ButtonStyle.secondary,
                    label="Need help?",
                    custom_id="help_button",
                    emoji="❓"
                )
                
                view = discord.ui.View(timeout=None)
                view.add_item(verify_button)
                view.add_item(help_button)
                
                await interaction.channel.send(embed=embed, view=view)
                await interaction.response.send_message("Verification message sent!", ephemeral=True)
                
            except Exception as e:
                await interaction.response.send_message("An error occurred while sending the verification message.", ephemeral=True)

        @self.bot.tree.command(name="invite", description="Get the Disblox dashboard link")
        async def invite_command(interaction: discord.Interaction):
            try:
                embed = discord.Embed(
                    title="🔗 Disblox Dashboard",
                    description="Click the link below to access the Disblox dashboard and invite the bot to your server.",
                    color=0x5865F2,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="Dashboard Link",
                    value="[https://your-frontend.com/dashboard](https://your-frontend.com/dashboard)",
                    inline=False
                )
                embed.set_footer(text="Disblox Verification System")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                await interaction.response.send_message("An error occurred while getting the invite link.", ephemeral=True)

        @self.bot.tree.command(name="support", description="Get support links and social media")
        async def support_command(interaction: discord.Interaction):
            try:
                embed = discord.Embed(
                    title="🆘 Support & Social Links",
                    description="Need help? Here are our support channels and social media links.",
                    color=0x5865F2,
                    timestamp=datetime.utcnow()
                )
                embed.add_field(
                    name="Discord Support Server",
                    value="[https://discord.gg/AvwzSe5XRT](https://discord.gg/AvwzSe5XRT)",
                    inline=False
                )
                embed.add_field(
                    name="Twitter/X",
                    value="[https://x.com/disblox](https://x.com/disblox)",
                    inline=False
                )
                embed.set_footer(text="Disblox Verification System")
                
                await interaction.response.send_message(embed=embed, ephemeral=True)
                
            except Exception as e:
                await interaction.response.send_message("An error occurred while getting the support links.", ephemeral=True)

        @self.bot.event
        async def on_interaction(interaction: discord.Interaction):
            if interaction.type == discord.InteractionType.component:
                custom_id = interaction.data.get("custom_id", "")
                
                if custom_id == "verify_button":
                    await self.handle_verify_button(interaction)
                elif custom_id == "help_button":
                    await self.handle_help_button(interaction)

    async def handle_verify_button(self, interaction: discord.Interaction):
        try:
            if not interaction.guild:
                await interaction.response.send_message("This button can only be used in a server.", ephemeral=True)
                return
            
            # Defer the response immediately to prevent timeout
            await interaction.response.defer(ephemeral=True)
            
            with get_sync_db() as db:
                user_query = select(User).where(User.discord_id == str(interaction.user.id))
                result = db.execute(user_query)
                user = result.scalar_one_or_none()
                
                if not user:
                    await interaction.followup.send("You need to link your Discord account first. Visit our dashboard to get started. https://your-frontend.com/dashboard", ephemeral=True)
                    return
                
                accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                result = db.execute(accounts_query)
                linked_accounts = result.scalars().all()
                
                if not linked_accounts:
                    await interaction.followup.send("You don't have any Roblox accounts linked. Visit our dashboard to link your account. https://your-frontend.com/dashboard", ephemeral=True)
                    return
                
                config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(interaction.guild.id))
                result = db.execute(config_query)
                server_config = result.scalar_one_or_none()
                
                if not server_config or not server_config.setup_completed:
                    await interaction.followup.send("This server is not configured for verification. Please contact an administrator.", ephemeral=True)
                    return
                
                success, message, embed_data = await self.verify_user(interaction.user)
                
                if success:
                    if embed_data:
                        embed = self.create_verification_embed(embed_data)
                        await interaction.followup.send(embed=embed, ephemeral=True)
                    else:
                        await interaction.followup.send("Verification completed successfully!", ephemeral=True)
                else:
                    await interaction.followup.send(f"Verification failed: {message}", ephemeral=True)
                    
        except Exception as e:
            try:
                await interaction.followup.send("An error occurred during verification. Please try again.", ephemeral=True)
            except:
                pass

    async def handle_help_button(self, interaction: discord.Interaction):
        try:
            # Defer the response immediately to prevent timeout
            await interaction.response.defer(ephemeral=True)
            
            embed = discord.Embed(
                title="❓ Need Help with Verification?",
                description="Here's how to get verified:",
                color=0x5865F2
            )
            embed.add_field(
                name="📋 Step 1: Link Your Account",
                value="Visit our dashboard: https://your-frontend.com/dashboard and link your Roblox account with your Discord account.",
                inline=False
            )
            embed.add_field(
                name="🔗 Step 2: Verify",
                value="Click the 'Verify with Disblox' button above to complete verification.",
                inline=False
            )
            embed.add_field(
                name="🎯 Step 3: Access Granted",
                value="Once verified, you'll receive the appropriate roles and access to the server.",
                inline=False
            )
            embed.set_footer(text="Disblox Verification System")
            
            await interaction.followup.send(embed=embed, ephemeral=True)
            
        except Exception as e:
            try:
                await interaction.followup.send("An error occurred while showing help. Please contact staff.", ephemeral=True)
            except:
                pass

    async def run_in_bot_loop(self, coro):
        if self._bot_loop and self._bot_loop.is_running():
            if asyncio.get_event_loop() == self._bot_loop:
                return await coro
            else:
                future = asyncio.run_coroutine_threadsafe(coro, self._bot_loop)
                return await asyncio.wrap_future(future)
        else:
            return await coro

    async def safe_discord_operation(self, operation, *args, **kwargs):
        try:
            coro = operation(*args, **kwargs)
            return await self.run_in_bot_loop(coro)
        except Exception as e:
            raise e

    async def handle_member_join(self, member):
        try:
            from database import SessionLocal
            
            db = SessionLocal()
            
            try:
                config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(member.guild.id))
                result = db.execute(config_query)
                server_config = result.scalar_one_or_none()
                
                if not server_config or not server_config.setup_completed:
                    return
                
                user_query = select(User).where(User.discord_id == str(member.id))
                user_result = db.execute(user_query)
                user = user_result.scalar_one_or_none()
                
                if not user:
                    try:
                        embed = discord.Embed(
                            title="👋 Link Your Roblox Account",
                            description="Welcome! To access this server's features, you need to link your Roblox account.",
                            color=discord.Color.blue()
                        )
                        embed.add_field(
                            name="How to link:",
                            value="1. Visit our website https://your-frontend.com/dashboard\n2. Log in with Discord\n3. Link your Roblox account\n4. Use `/verify` command or verify on the website.",
                            inline=False
                        )
                        embed.set_footer(text="Disblox Verification System")
                        await self.safe_discord_operation(member.send, embed=embed)
                    except discord.Forbidden:
                        pass
                    return
                
                linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                linked_result = db.execute(linked_accounts_query)
                linked_accounts = linked_result.scalars().all()
                
                if not linked_accounts:
                    try:
                        embed = discord.Embed(
                            title="👋 Link Your Roblox Account",
                            description="Welcome! To access this server's features, you need to link your Roblox account.",
                            color=discord.Color.blue()
                        )
                        embed.add_field(
                            name="How to link:",
                            value="1. Visit our website https://your-frontend.com/dashboard\n2. Log in with Discord\n3. Link your Roblox account\n4. Use `/verify` command or verify on the website.",
                            inline=False
                        )
                        embed.set_footer(text="Disblox Verification System")
                        await self.safe_discord_operation(member.send, embed=embed)
                    except discord.Forbidden:
                        pass
                    return
                
                linked_account = next((acc for acc in linked_accounts if acc.verified), linked_accounts[0])
                
                embed_data = await self.apply_server_config_sync_with_tracking(member, server_config, linked_account, db)
                
            finally:
                db.close()
                
        except Exception as e:
            pass
    
    async def apply_server_config(self, member, server_config, linked_account, db):
        """Apply server configuration to a member"""
        try:
            if server_config.nickname_format != "none":
                new_nickname = await self.get_formatted_nickname(member, linked_account, server_config.nickname_format)
                if new_nickname and new_nickname != member.nick:
                    try:
                        await member.edit(nick=new_nickname)
                    except discord.Forbidden:
                        pass
                    except Exception as e:
                        pass
            
            if server_config.verified_role_enabled and server_config.verified_role_id:
                try:
                    verified_role = member.guild.get_role(int(server_config.verified_role_id))
                    if verified_role and verified_role not in member.roles:
                        if server_config.roles_to_remove:
                            roles_to_remove_ids = server_config.roles_to_remove.split(',')
                            roles_to_remove = []
                            for role_id in roles_to_remove_ids:
                                role = member.guild.get_role(int(role_id.strip()))
                                if role and role in member.roles:
                                    roles_to_remove.append(role)
                            
                            if roles_to_remove:
                                await member.remove_roles(*roles_to_remove)
                        
                        await member.add_roles(verified_role)
                except Exception as e:
                    pass
            
            if server_config.group_roles_enabled and server_config.group_id:
                await self.assign_group_roles(member, server_config, linked_account, db)
                
        except Exception as e:
            pass

    async def apply_server_config_sync(self, member, server_config, linked_account, db):
        try:
            if server_config.nickname_format != "none":
                new_nickname = await self.get_formatted_nickname(member, linked_account, server_config.nickname_format)
                if new_nickname and new_nickname != member.nick:
                    try:
                        await self.safe_discord_operation(member.edit, nick=new_nickname)
                    except discord.Forbidden:
                        pass
                    except Exception as e:
                        pass
            
            if server_config.verified_role_enabled and server_config.verified_role_id:
                try:
                    verified_role = member.guild.get_role(int(server_config.verified_role_id))
                    if verified_role and verified_role not in member.roles:
                        if server_config.roles_to_remove:
                            roles_to_remove_ids = server_config.roles_to_remove.split(',')
                            roles_to_remove = []
                            for role_id in roles_to_remove_ids:
                                role = member.guild.get_role(int(role_id.strip()))
                                if role and role in member.roles:
                                    roles_to_remove.append(role)
                            
                            if roles_to_remove:
                                await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
                        
                        await self.safe_discord_operation(member.add_roles, verified_role)
                except Exception as e:
                    pass
            
            if server_config.group_roles_enabled and server_config.group_id:
                await self.assign_group_roles_sync(member, server_config, linked_account, db)
                
        except Exception as e:
            pass

    async def send_verification_dm(self, member, embed_data, is_update=False):
        try:
            if is_update:
                embed = self.create_update_embed(embed_data)
            else:
                embed = self.create_verification_embed(embed_data)
            
            await self.safe_discord_operation(member.send, embed=embed)
        except discord.Forbidden:
            pass
        except Exception as e:
            pass

    async def apply_server_config_sync_with_tracking(self, member, server_config, linked_account, db, is_update=False):
        print(f"Starting apply_server_config_sync_with_tracking for {member.display_name}")
        print(f"Server config: nickname_format={server_config.nickname_format}, verified_role_enabled={server_config.verified_role_enabled}, group_roles_enabled={server_config.group_roles_enabled}")
        
        embed_data = {
            'nickname_updated': None,
            'roles_added': [],
            'roles_removed': [],
            'group_roles_added': [],
            'group_roles_removed': []
        }
        
        try:
            if server_config.nickname_format != "none":
                print(f"Processing nickname format: {server_config.nickname_format}")
                new_nickname = await self.get_formatted_nickname(member, linked_account, server_config.nickname_format)
                print(f"New nickname: {new_nickname}, current nick: {member.nick}")
                if new_nickname and new_nickname != member.nick:
                    try:
                        await self.safe_discord_operation(member.edit, nick=new_nickname)
                        embed_data['nickname_updated'] = new_nickname
                        print(f"Nickname updated to: {new_nickname}")
                    except discord.Forbidden:
                        print("Forbidden to change nickname")
                    except Exception as e:
                        print(f"Error changing nickname: {e}")
                else:
                    print("Nickname not changed")
            else:
                print("Nickname format is 'none', skipping")
            
            if server_config.verified_role_enabled and server_config.verified_role_id:
                print(f"Processing verified role: {server_config.verified_role_id}")
                try:
                    verified_role = member.guild.get_role(int(server_config.verified_role_id))
                    if verified_role:
                        print(f"Found verified role: {verified_role.name}")
                        if verified_role not in member.roles:
                            if server_config.roles_to_remove:
                                roles_to_remove_ids = server_config.roles_to_remove.split(',')
                                roles_to_remove = []
                                for role_id in roles_to_remove_ids:
                                    role = member.guild.get_role(int(role_id.strip()))
                                    if role and role in member.roles:
                                        roles_to_remove.append(role)
                                
                                if roles_to_remove:
                                    await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
                                    embed_data['roles_removed'].extend([role.name for role in roles_to_remove])
                                    print(f"Removed roles: {[role.name for role in roles_to_remove]}")
                            
                            await self.safe_discord_operation(member.add_roles, verified_role)
                            embed_data['roles_added'].append(verified_role.name)
                            print(f"Added verified role: {verified_role.name}")
                        else:
                            print(f"User already has verified role: {verified_role.name}")
                    else:
                        print(f"Verified role not found for ID: {server_config.verified_role_id}")
                except Exception as e:
                    print(f"Error processing verified role: {e}")
            else:
                print("Verified role disabled or not configured")
            
            if server_config.group_roles_enabled and server_config.group_id:
                print("Processing group roles")
                group_data = await self.assign_group_roles_sync_with_tracking(member, server_config, linked_account, db, is_update)
                embed_data['group_roles_added'].extend(group_data.get('roles_added', []))
                embed_data['group_roles_removed'].extend(group_data.get('roles_removed', []))
                print(f"Group roles result: {group_data}")
            else:
                print("Group roles disabled or no group ID")
            
            await self.send_verification_dm(member, embed_data, is_update)
            print(f"Final embed data: {embed_data}")
                
        except Exception as e:
            print(f"Error in apply_server_config_sync_with_tracking: {e}")
        
        return embed_data

    async def get_formatted_nickname(self, member, linked_account, format_type):
        try:
            if format_type == "roblox_username":
                return linked_account.roblox_username
            elif format_type == "roblox_display":
                async with httpx.AsyncClient() as client:
                    response = await client.get(f"https://users.roblox.com/v1/users/{linked_account.roblox_id}")
                    if response.status_code == 200:
                        data = response.json()
                        display_name = data.get("displayName", linked_account.roblox_username)
                        return display_name
                    else:
                        return linked_account.roblox_username
            elif format_type == "discord_display":
                return member.display_name
            elif format_type == "discord_username":
                return member.name
            elif format_type == "discord_display_with_roblox":
                return f"{member.display_name} (@{linked_account.roblox_username})"
            elif format_type == "none":
                return None
            else:
                return linked_account.roblox_username
        except Exception as e:
            return linked_account.roblox_username

    async def assign_group_roles(self, member, server_config, linked_account, db):
        try:
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            group_roles_result = await db.execute(group_roles_query)
            group_roles = group_roles_result.scalars().all()
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
            
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://groups.roblox.com/v1/users/{linked_account.roblox_id}/groups/roles")
                if response.status_code != 200:
                    return
                
                user_groups = response.json().get("data", [])
                target_group = None
                
                for group in user_groups:
                    if str(group["group"]["id"]) == server_config.group_id:
                        target_group = group
                        break
                
                if not target_group:
                    await self.assign_default_group_role(member, server_config, db)
                    return
                
                user_role_id = str(target_group["role"]["id"])
                matching_role = None
                
                for group_role in group_roles:
                    if group_role.roblox_role_id == user_role_id:
                        matching_role = member.guild.get_role(int(group_role.discord_role_id))
                        break
                
                if matching_role:
                    try:
                        await member.add_roles(matching_role)
                    except Exception as e:
                        pass
                
        except Exception as e:
            pass
    
    async def assign_default_group_role(self, member, server_config, db):
        try:
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            group_roles_result = await db.execute(group_roles_query)
            group_roles = group_roles_result.scalars().all()
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
            
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove)
            
            default_role_names = [server_config.group_name, "Newcomers", "Member"]
            
            for role_name in default_role_names:
                if role_name:
                    role = discord.utils.get(member.guild.roles, name=role_name)
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role)
                            break
                        except Exception as e:
                            pass
        except Exception as e:
            pass

    async def assign_group_roles_sync(self, member, server_config, linked_account, db):
        try:
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            group_roles_result = await db.execute(group_roles_query)
            group_roles = group_roles_result.scalars().all()
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
            
            if roles_to_remove:
                await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
            
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://groups.roblox.com/v1/users/{linked_account.roblox_id}/groups/roles")
                if response.status_code != 200:
                    return
                
                user_groups = response.json().get("data", [])
                target_group = None
                
                for group in user_groups:
                    if str(group["group"]["id"]) == server_config.group_id:
                        target_group = group
                        break
                
                if not target_group:
                    await self.assign_default_group_role_sync(member, server_config, db)
                    return
                
                user_role_id = str(target_group["role"]["id"])
                matching_role = None
                
                for group_role in group_roles:
                    if group_role.roblox_role_id == user_role_id:
                        matching_role = member.guild.get_role(int(group_role.discord_role_id))
                        break
                
                if matching_role:
                    try:
                        await self.safe_discord_operation(member.add_roles, matching_role)
                    except Exception as e:
                        pass
                
        except Exception as e:
            pass

    async def assign_group_roles_sync_with_tracking(self, member, server_config, linked_account, db, is_update=False):
        """Assign group roles with tracking for embed data"""
        embed_data = {
            'roles_added': [],
            'roles_removed': []
        }
        
        try:
            print(f"Starting group role assignment for {member.display_name} in {member.guild.name}")
            print(f"Server config: group_id={server_config.group_id}, group_name={server_config.group_name}")
            print(f"Linked account: roblox_id={linked_account.roblox_id}, roblox_username={linked_account.roblox_username}")
            
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            
            # Handle both async and sync database contexts
            if hasattr(db, 'execute') and asyncio.iscoroutinefunction(db.execute):
                group_roles_result = await db.execute(group_roles_query)
            else:
                group_roles_result = db.execute(group_roles_query)
            
            group_roles = group_roles_result.scalars().all()
            
            print(f"Found {len(group_roles)} group roles configured")
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
                        print(f"Found Discord role: {discord_role.name} (ID: {discord_role.id})")
                    else:
                        print(f"Discord role not found for ID: {group_role.discord_role_id}")
                else:
                    print(f"Group role {group_role.roblox_role_name} has no Discord role ID")
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
                    print(f"Will remove role: {role.name}")
            
            if roles_to_remove:
                await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
                embed_data['roles_removed'].extend([role.name for role in roles_to_remove])
                print(f"Removed {len(roles_to_remove)} roles")
            
            if not server_config.group_id:
                print("No group ID configured, skipping group role assignment")
                return embed_data
            
            print(f"Fetching Roblox groups for user {linked_account.roblox_id}")
            async with httpx.AsyncClient() as client:
                response = await client.get(f"https://groups.roblox.com/v1/users/{linked_account.roblox_id}/groups/roles")
                print(f"Roblox API response status: {response.status_code}")
                
                if response.status_code != 200:
                    print(f"Failed to fetch Roblox groups: {response.status_code}")
                    return embed_data
                
                user_groups = response.json().get("data", [])
                print(f"User is in {len(user_groups)} Roblox groups")
                
                target_group = None
                for group in user_groups:
                    print(f"Checking group: {group['group']['name']} (ID: {group['group']['id']})")
                    if str(group["group"]["id"]) == server_config.group_id:
                        target_group = group
                        print(f"Found target group: {group['group']['name']}")
                        break
                
                if not target_group:
                    print(f"User not in target group {server_config.group_id}, assigning default role")
                    await self.assign_default_group_role_sync_with_tracking(member, server_config, db)
                    return embed_data
                
                user_role_id = str(target_group["role"]["id"])
                user_role_name = target_group["role"]["name"]
                print(f"User role in group: {user_role_name} (ID: {user_role_id})")
                
                matching_role = None
                for group_role in group_roles:
                    print(f"Checking group role: {group_role.roblox_role_name} (ID: {group_role.roblox_role_id})")
                    if group_role.roblox_role_id == user_role_id:
                        matching_role = member.guild.get_role(int(group_role.discord_role_id))
                        if matching_role:
                            print(f"Found matching Discord role: {matching_role.name}")
                        else:
                            print(f"Discord role not found for ID: {group_role.discord_role_id}")
                        break
                
                if matching_role:
                    try:
                        await self.safe_discord_operation(member.add_roles, matching_role)
                        embed_data['roles_added'].append(matching_role.name)
                        print(f"Successfully added role: {matching_role.name}")
                    except Exception as e:
                        print(f"Failed to add role {matching_role.name}: {e}")
                else:
                    print(f"No matching Discord role found for Roblox role {user_role_name}")
                
        except Exception as e:
            print(f"Error in assign_group_roles_sync_with_tracking: {e}")
        
        print(f"Group role assignment complete. Added: {embed_data['roles_added']}, Removed: {embed_data['roles_removed']}")
        return embed_data

    async def assign_default_group_role_sync(self, member, server_config, db):
        try:
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            group_roles_result = await db.execute(group_roles_query)
            group_roles = group_roles_result.scalars().all()
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
            
            if roles_to_remove:
                await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
            
            default_role_names = [server_config.group_name, "Newcomers", "Member"]
            
            for role_name in default_role_names:
                if role_name:
                    role = discord.utils.get(member.guild.roles, name=role_name)
                    if role and role not in member.roles:
                        try:
                            await member.add_roles(role)
                            break
                        except Exception as e:
                            pass
        except Exception as e:
            pass

    async def assign_default_group_role_sync_with_tracking(self, member, server_config, db):
        try:
            group_roles_query = select(GroupRole).where(GroupRole.server_config_id == server_config.id)
            
            # Handle both async and sync database contexts
            if hasattr(db, 'execute') and asyncio.iscoroutinefunction(db.execute):
                group_roles_result = await db.execute(group_roles_query)
            else:
                group_roles_result = db.execute(group_roles_query)
            
            group_roles = group_roles_result.scalars().all()
            
            discord_group_roles = []
            for group_role in group_roles:
                if group_role.discord_role_id:
                    discord_role = member.guild.get_role(int(group_role.discord_role_id))
                    if discord_role:
                        discord_group_roles.append(discord_role)
            
            roles_to_remove = []
            for role in discord_group_roles:
                if role in member.roles:
                    roles_to_remove.append(role)
            
            if roles_to_remove:
                await self.safe_discord_operation(member.remove_roles, *roles_to_remove)
            
            default_role_names = [server_config.group_name, "Newcomers", "Member"]
            
            for role_name in default_role_names:
                if role_name:
                    role = discord.utils.get(member.guild.roles, name=role_name)
                    if role and role not in member.roles:
                        try:
                            await self.safe_discord_operation(member.add_roles, role)
                            break
                        except Exception as e:
                            pass
        except Exception as e:
            pass

    async def handle_member_update(self, before, after):
        pass

    async def handle_member_remove(self, member):
        try:
            from database import get_sync_db
            from models import User, LinkedAccount, UserServer
            from sqlalchemy import select
            
            db = get_sync_db()
            try:
                user_query = select(User).where(User.discord_id == str(member.id))
                result = db.execute(user_query)
                user = result.scalar_one_or_none()
                
                if user:
                    user_server_query = select(UserServer).where(
                        UserServer.user_id == user.id,
                        UserServer.server_id == str(member.guild.id)
                    )
                    result = db.execute(user_server_query)
                    user_server = result.scalar_one_or_none()
                    
                    if user_server:
                        db.delete(user_server)
                    
                    linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                    result = db.execute(linked_accounts_query)
                    linked_accounts = result.scalars().all()
                    
                    remaining_servers_query = select(UserServer).where(UserServer.user_id == user.id)
                    result = db.execute(remaining_servers_query)
                    remaining_servers = result.scalars().all()
                    
                    if not linked_accounts and not remaining_servers:
                        db.delete(user)
                    
                    db.commit()
                else:
                    pass
                    
            except Exception as e:
                db.rollback()
                raise
            finally:
                db.close()
                    
        except Exception as e:
            pass

    async def cleanup_server_config(self, guild_id: int):
        try:
            from database import get_sync_db
            from models import ServerConfig as ServerConfigModel, GroupRole, UserServer, BotServer
            from sqlalchemy import select
            
            db = get_sync_db()
            try:
                group_roles_query = select(GroupRole).join(ServerConfigModel).where(ServerConfigModel.server_id == str(guild_id))
                result = db.execute(group_roles_query)
                group_roles = result.scalars().all()
                
                for group_role in group_roles:
                    db.delete(group_role)
                
                config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(guild_id))
                result = db.execute(config_query)
                server_config = result.scalar_one_or_none()
                
                if server_config:
                    db.delete(server_config)
                
                user_servers_query = select(UserServer).where(UserServer.server_id == str(guild_id))
                result = db.execute(user_servers_query)
                user_servers = result.scalars().all()
                
                for user_server in user_servers:
                    db.delete(user_server)
                
                bot_server_query = select(BotServer).where(BotServer.server_id == str(guild_id))
                result = db.execute(bot_server_query)
                bot_server = result.scalar_one_or_none()
                
                if bot_server:
                    db.delete(bot_server)
                
                db.commit()
                
            except Exception as e:
                db.rollback()
                raise
            finally:
                db.close()
                    
        except Exception as e:
            pass

    async def verify_user(self, member):
        try:
            print(f"Starting verification for user {member.display_name} in {member.guild.name}")
            from database import SessionLocal
            
            db = SessionLocal()
            
            try:
                config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(member.guild.id))
                result = db.execute(config_query)
                server_config = result.scalar_one_or_none()
                
                print(f"Server config found: {server_config is not None}")
                if server_config:
                    print(f"Server config: setup_completed={server_config.setup_completed}")
                
                if not server_config or not server_config.setup_completed:
                    print("Server not configured for verification")
                    return False, "Server not configured for verification", None
                
                user_query = select(User).where(User.discord_id == str(member.id))
                user_result = db.execute(user_query)
                user = user_result.scalar_one_or_none()
                
                print(f"User found: {user is not None}")
                if user:
                    print(f"User: {user.username} (ID: {user.discord_id})")
                
                if not user:
                    try:
                        embed = discord.Embed(
                            title="👋 Link Your Roblox Account",
                            description="You need to link your Roblox account to use this server's features.",
                            color=discord.Color.blue()
                        )
                        embed.add_field(
                            name="How to link:",
                            value="1. Visit our website https://your-frontend.com/dashboard\n2. Log in with Discord\n3. Link your Roblox account\n4. Come back and try `/verify` again or verify on the website.",
                            inline=False
                        )
                        embed.set_footer(text="Disblox Verification System")
                        await member.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    return False, "No linked Roblox accounts found. Check your DMs for instructions.", None
                
                linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                linked_result = db.execute(linked_accounts_query)
                linked_accounts = linked_result.scalars().all()
                
                print(f"Found {len(linked_accounts)} linked accounts")
                for account in linked_accounts:
                    print(f"  - {account.roblox_username} (verified: {account.verified})")
                
                if not linked_accounts:
                    try:
                        embed = discord.Embed(
                            title="👋 Link Your Roblox Account",
                            description="You need to link your Roblox account to use this server's features.",
                            color=discord.Color.blue()
                        )
                        embed.add_field(
                            name="How to link:",
                            value="1. Visit our website https://your-frontend.com/dashboard\n2. Log in with Discord\n3. Link your Roblox account\n4. Come back and try `/verify` again or verify on the website.",
                            inline=False
                        )
                        embed.set_footer(text="Disblox Verification System")
                        await member.send(embed=embed)
                    except discord.Forbidden:
                        pass
                    return False, "No linked Roblox accounts found. Check your DMs for instructions.", None
                
                linked_account = next((acc for acc in linked_accounts if acc.verified), linked_accounts[0])
                print(f"Selected account: {linked_account.roblox_username} (verified: {linked_account.verified})")
                
                embed_data = await self.apply_server_config_sync_with_tracking(member, server_config, linked_account, db)
                print(f"Verification complete. Embed data: {embed_data}")
                return True, "User verified successfully", embed_data
                
            finally:
                db.close()
                
        except Exception as e:
            print(f"Error in verify_user: {e}")
            return False, f"Verification failed: {str(e)}", None

    async def update_user(self, member):
        try:
            print(f"Starting update for user {member.display_name} in {member.guild.name}")
            from database import SessionLocal
            
            db = SessionLocal()
            
            try:
                config_query = select(ServerConfigModel).where(ServerConfigModel.server_id == str(member.guild.id))
                result = db.execute(config_query)
                server_config = result.scalar_one_or_none()
                
                print(f"Server config found: {server_config is not None}")
                if server_config:
                    print(f"Server config: setup_completed={server_config.setup_completed}")
                
                if not server_config or not server_config.setup_completed:
                    print("Server not configured for verification")
                    return False, "Server not configured for verification", None
                
                user_query = select(User).where(User.discord_id == str(member.id))
                user_result = db.execute(user_query)
                user = user_result.scalar_one_or_none()
                
                print(f"User found: {user is not None}")
                if user:
                    print(f"User: {user.username} (ID: {user.discord_id})")
                
                if not user:
                    return False, "No linked Roblox accounts found", None
                
                linked_accounts_query = select(LinkedAccount).where(LinkedAccount.user_id == user.id)
                linked_result = db.execute(linked_accounts_query)
                linked_accounts = linked_result.scalars().all()
                
                print(f"Found {len(linked_accounts)} linked accounts")
                for account in linked_accounts:
                    print(f"  - {account.roblox_username} (verified: {account.verified})")
                
                if not linked_accounts:
                    return False, "No linked Roblox accounts found", None
                
                linked_account = next((acc for acc in linked_accounts if acc.verified), linked_accounts[0])
                print(f"Selected account: {linked_account.roblox_username} (verified: {linked_account.verified})")
                
                embed_data = await self.apply_server_config_sync_with_tracking(member, server_config, linked_account, db, is_update=True)
                print(f"Update complete. Embed data: {embed_data}")
                return True, "User updated successfully", embed_data
                
            finally:
                db.close()
                
        except Exception as e:
            print(f"Error in update_user: {e}")
            return False, f"Update failed: {str(e)}", None

    def start_bot(self):
        if not config.DISCORD_TOKEN:
            return
        
        def run_bot():
            try:
                self.bot.run(config.DISCORD_TOKEN)
            except Exception as e:
                pass
        
        self.bot_thread = threading.Thread(target=run_bot, daemon=True)
        self.bot_thread.start()
    
    def is_ready(self) -> bool:
        with self._lock:
            return self.bot_ready and self.bot is not None
    
    def get_guilds(self) -> List[discord.Guild]:
        with self._lock:
            guilds = self.bot_guilds.copy() if self.bot_ready else []
            print(f"get_guilds() called, returning {len(guilds)} guilds")
            for guild in guilds:
                print(f"  - {guild.name} (ID: {guild.id})")
            return guilds
    
    def get_guild(self, guild_id: int) -> Optional[discord.Guild]:
        with self._lock:
            if not self.bot_ready:
                return None
            return next((guild for guild in self.bot_guilds if guild.id == guild_id), None)
    
    def get_user(self):
        with self._lock:
            return self.bot_user if self.bot_ready else None
    
    def get_latency(self) -> float:
        with self._lock:
            return self.bot.latency if self.bot_ready and self.bot else 0
    
    def get_uptime(self) -> float:
        return time.time() - self.start_time if self.start_time else 0

    async def sync_slash_commands(self):
        try:
            await self.bot.tree.sync()
        except Exception as e:
            pass
            import traceback
            traceback.print_exc()

    async def sync_guilds_to_database(self):
        try:
            from database import SessionLocal
            from models import BotServer
            from sqlalchemy import select
            
            db = SessionLocal()
            try:
                current_guilds = list(self.bot.guilds)
                
                existing_servers_query = select(BotServer)
                existing_servers = db.execute(existing_servers_query).scalars().all()
                
                for guild in current_guilds:
                    existing_server = next((s for s in existing_servers if s.server_id == str(guild.id)), None)
                    
                    if not existing_server:
                        new_server = BotServer(
                            server_id=str(guild.id),
                            server_name=guild.name,
                            server_icon=guild.icon.key if guild.icon else None,
                            owner_id=str(guild.owner_id),
                            member_count=guild.member_count
                        )
                        db.add(new_server)
                    else:
                        existing_server.server_name = guild.name
                        existing_server.server_icon = guild.icon.key if guild.icon else None
                        existing_server.owner_id = str(guild.owner_id)
                        existing_server.member_count = guild.member_count
                
                db.commit()
                
            finally:
                db.close()
                
        except Exception as e:
            pass

    def create_verification_embed(self, data):
        embed = discord.Embed(
            title="Verification Complete",
            description="Your Roblox account has been successfully verified!",
            color=discord.Color.green()
        )
        
        if data.get('nickname_updated'):
            embed.add_field(
                name="Nickname Updated",
                value=f"New nickname: {data['nickname_updated']}",
                inline=False
            )
        
        if data.get('roles_added'):
            embed.add_field(
                name="Roles Added",
                value="\n".join([f"• {role}" for role in data['roles_added']]),
                inline=True
            )
        
        if data.get('group_roles_added'):
            embed.add_field(
                name="Group Roles Added",
                value="\n".join([f"• {role}" for role in data['group_roles_added']]),
                inline=True
            )
        
        embed.set_footer(text="Disblox Verification System")
        return embed

    def create_update_embed(self, data):
        embed = discord.Embed(
            title="Update Complete",
            description="Your roles and nickname have been updated!",
            color=discord.Color.blue()
        )
        
        if data.get('roles_added'):
            embed.add_field(
                name="Roles Added",
                value="\n".join([f"• {role}" for role in data['roles_added']]),
                inline=True
            )
        
        if data.get('roles_removed'):
            embed.add_field(
                name="Roles Removed",
                value="\n".join([f"• {role}" for role in data['roles_removed']]),
                inline=True
            )
        
        if data.get('group_roles_added'):
            embed.add_field(
                name="Group Roles Added",
                value="\n".join([f"• {role}" for role in data['group_roles_added']]),
                inline=True
            )
        
        if data.get('group_roles_removed'):
            embed.add_field(
                name="Group Roles Removed",
                value="\n".join([f"• {role}" for role in data['group_roles_removed']]),
                inline=True
            )
        
        if data.get('nickname_updated'):
            embed.add_field(
                name="Nickname Updated",
                value=f"New nickname: {data['nickname_updated']}",
                inline=False
            )
        
        embed.set_footer(text="Disblox Update System")
        return embed

bot_manager = BotManager() 