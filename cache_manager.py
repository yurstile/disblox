import time
import threading
import json
from typing import Any, Optional, Dict, List
from datetime import datetime, timedelta
from collections import OrderedDict
import asyncio
import logging

logger = logging.getLogger(__name__)

class CacheItem:
    def __init__(self, value: Any, ttl: int):
        self.value = value
        self.created_at = time.time()
        self.ttl = ttl
    
    def is_expired(self) -> bool:
        return time.time() - self.created_at > self.ttl
    
    def time_until_expiry(self) -> float:
        return max(0, self.ttl - (time.time() - self.created_at))

class CacheManager:
    def __init__(self, max_size: int = 1000, cleanup_interval: int = 300):
        self._cache: Dict[str, CacheItem] = {}
        self._lock = threading.RLock()
        self._max_size = max_size
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
        
        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()
    
    def _cleanup_worker(self):
        while True:
            try:
                time.sleep(self._cleanup_interval)
                self._cleanup_expired()
            except Exception as e:
                pass
    
    def _cleanup_expired(self):
        with self._lock:
            expired_keys = [
                key for key, item in self._cache.items()
                if item.is_expired()
            ]
            for key in expired_keys:
                del self._cache[key]
    
    def _evict_if_needed(self):
        if len(self._cache) >= self._max_size:
            oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k].created_at)
            del self._cache[oldest_key]
    
    def set(self, key: str, value: Any, ttl: int = 3600) -> None:
        with self._lock:
            self._evict_if_needed()
            self._cache[key] = CacheItem(value, ttl)
    
    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._cache.get(key)
            if item and not item.is_expired():
                return item.value
            elif item:
                del self._cache[key]
            return None
    
    def delete(self, key: str) -> bool:
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False
    
    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
    
    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            total_items = len(self._cache)
            expired_items = sum(1 for item in self._cache.values() if item.is_expired())
            active_items = total_items - expired_items
            
            return {
                "total_items": total_items,
                "active_items": active_items,
                "expired_items": expired_items,
                "max_size": self._max_size,
                "usage_percentage": (total_items / self._max_size) * 100 if self._max_size > 0 else 0
            }

class DiscordCacheManager:
    def __init__(self):
        self.cache = CacheManager(max_size=2000, cleanup_interval=300)
        
        self.user_cache = CacheManager(max_size=500, cleanup_interval=600)
        self.guild_cache = CacheManager(max_size=1000, cleanup_interval=300)
        self.bot_cache = CacheManager(max_size=500, cleanup_interval=180)
        
        self.rate_limit_cache = CacheManager(max_size=100, cleanup_interval=60)
        self.rate_limits = {}
        self.rate_limit_lock = threading.RLock()
    
    def _get_user_key(self, user_id: str) -> str:
        return f"user:{user_id}"
    
    def _get_guild_key(self, guild_id: str) -> str:
        return f"guild:{guild_id}"
    
    def _get_user_guilds_key(self, user_id: str) -> str:
        return f"user_guilds:{user_id}"
    
    def _get_bot_guilds_key(self) -> str:
        return f"bot_guilds"
    
    def _get_rate_limit_key(self, endpoint: str) -> str:
        return f"rate_limit:{endpoint}"
    
    def cache_user_data(self, user_id: str, user_data: Dict[str, Any], ttl: int = 1800):
        self.user_cache.set(self._get_user_key(user_id), user_data, ttl)
    
    def get_cached_user_data(self, user_id: str) -> Optional[Dict[str, Any]]:
        return self.user_cache.get(self._get_user_key(user_id))
    
    def invalidate_user_cache(self, user_id: str):
        self.user_cache.delete(self._get_user_key(user_id))
    
    def cache_guild_data(self, guild_id: str, guild_data: Dict[str, Any], ttl: int = 900):
        self.guild_cache.set(self._get_guild_key(guild_id), guild_data, ttl)
    
    def get_cached_guild_data(self, guild_id: str) -> Optional[Dict[str, Any]]:
        return self.guild_cache.get(self._get_guild_key(guild_id))
    
    def cache_user_guilds(self, user_id: str, guilds: List[Dict[str, Any]], ttl: int = 1800):
        self.guild_cache.set(self._get_user_guilds_key(user_id), guilds, ttl)
    
    def get_cached_user_guilds(self, user_id: str) -> Optional[List[Dict[str, Any]]]:
        return self.guild_cache.get(self._get_user_guilds_key(user_id))
    
    def invalidate_user_guilds_cache(self, user_id: str):
        self.guild_cache.delete(self._get_user_guilds_key(user_id))
    
    def cache_bot_guilds(self, guilds: List[Dict[str, Any]], ttl: int = 600):
        self.bot_cache.set(self._get_bot_guilds_key(), guilds, ttl)
    
    def get_cached_bot_guilds(self) -> Optional[List[Dict[str, Any]]]:
        return self.bot_cache.get(self._get_bot_guilds_key())
    
    def invalidate_bot_guilds_cache(self):
        self.bot_cache.delete(self._get_bot_guilds_key())
    
    def check_rate_limit(self, endpoint: str, limit: int = 50, window: int = 60) -> bool:
        with self.rate_limit_lock:
            current_time = time.time()
            key = self._get_rate_limit_key(endpoint)
            
            if key not in self.rate_limits:
                self.rate_limits[key] = []
            
            requests = self.rate_limits[key]
            
            requests = [req_time for req_time in requests if current_time - req_time < window]
            self.rate_limits[key] = requests
            
            if len(requests) >= limit:
                return False
            
            requests.append(current_time)
            return True
    
    def get_rate_limit_info(self, endpoint: str) -> Dict[str, Any]:
        with self.rate_limit_lock:
            current_time = time.time()
            key = self._get_rate_limit_key(endpoint)
            
            if key not in self.rate_limits:
                return {
                    "endpoint": endpoint,
                    "requests": 0,
                    "limit": 50,
                    "window": 60,
                    "remaining": 50,
                    "reset_time": current_time + 60
                }
            
            requests = self.rate_limits[key]
            requests = [req_time for req_time in requests if current_time - req_time < 60]
            
            return {
                "endpoint": endpoint,
                "requests": len(requests),
                "limit": 50,
                "window": 60,
                "remaining": max(0, 50 - len(requests)),
                "reset_time": current_time + 60
            }
    
    def invalidate_all_user_caches(self, user_id: str):
        self.invalidate_user_cache(user_id)
        self.invalidate_user_guilds_cache(user_id)
    
    def invalidate_guild_cache(self, guild_id: str):
        self.guild_cache.delete(self._get_guild_key(guild_id))
    
    def get_cache_stats(self) -> Dict[str, Any]:
        return self.cache.get_stats()

discord_cache = DiscordCacheManager() 