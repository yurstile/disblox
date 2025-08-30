import asyncio
import uvicorn
import threading
import os
import secrets
import base64
from fastapi import FastAPI, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from starlette.middleware.base import BaseHTTPMiddleware
from bot_manager import bot_manager
from config import config
from database import init_async_db
from dashboard import router as dashboard_router
from auth_routes import router as auth_router
from roblox_routes import router as roblox_router
from server_routes import router as server_router
import time
from collections import defaultdict
import asyncio

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app):
        super().__init__(app)
        self.nonce_cache = {}
        self.nonce_cache_cleanup_time = time.time()
    
    def generate_nonce(self):
        return base64.b64encode(secrets.token_bytes(16)).decode('utf-8')
    
    def cleanup_nonce_cache(self):
        current_time = time.time()
        if current_time - self.nonce_cache_cleanup_time > 3600:
            self.nonce_cache.clear()
            self.nonce_cache_cleanup_time = current_time
    
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        
        self.cleanup_nonce_cache()
        
        nonce = self.generate_nonce()
        request_id = id(request)
        self.nonce_cache[request_id] = nonce
        
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=(), payment=(), usb=(), magnetometer=(), gyroscope=(), accelerometer=()"
        response.headers["X-CSP-Nonce"] = nonce
        
        csp_policy = (
            f"default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' 'strict-dynamic'; "
            f"style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            f"font-src 'self' https://fonts.gstatic.com; "
            f"img-src 'self' data: https: https://cdn.discordapp.com https://www.roblox.com; "
            f"connect-src 'self' https://discord.com https://discordapp.com https://api.roblox.com https://your-api-url.com https://your-frontend-url.com; "
            f"frame-ancestors 'none'; "
            f"base-uri 'self'; "
            f"form-action 'self'; "
            f"object-src 'none'; "
            f"media-src 'self'; "
            f"worker-src 'self'; "
            f"manifest-src 'self'; "
            f"prefetch-src 'self'; "
            f"frame-src 'none'; "
            f"upgrade-insecure-requests;"
        )
        
        response.headers["Content-Security-Policy"] = csp_policy
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains; preload"
        response.headers["X-Download-Options"] = "noopen"
        response.headers["X-Permitted-Cross-Domain-Policies"] = "none"
        
        return response

class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60, requests_per_hour: int = 1000):
        super().__init__(app)
        self.requests_per_minute = requests_per_minute
        self.requests_per_hour = requests_per_hour
        self.minute_requests = defaultdict(list)
        self.hour_requests = defaultdict(list)
        self._lock = asyncio.Lock()
    
    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        
        user_id = None
        try:
            auth_header = request.headers.get("Authorization")
            if auth_header and auth_header.startswith("Bearer "):
                user_id = auth_header.split(" ")[1][:10]
        except:
            pass
        
        identifier = user_id or client_ip
        
        async with self._lock:
            current_time = time.time()
            
            self._cleanup_old_requests(current_time)
            
            if not self._check_rate_limit(identifier, current_time):
                return Response(
                    content="Rate limit exceeded. Please try again later.",
                    status_code=429,
                    headers={"Retry-After": "60"}
                )
            
            self.minute_requests[identifier].append(current_time)
            self.hour_requests[identifier].append(current_time)
        
        return await call_next(request)
    
    def _cleanup_old_requests(self, current_time: float):
        for identifier in list(self.minute_requests.keys()):
            self.minute_requests[identifier] = [
                req_time for req_time in self.minute_requests[identifier]
                if current_time - req_time < 60
            ]
            if not self.minute_requests[identifier]:
                del self.minute_requests[identifier]
        
        for identifier in list(self.hour_requests.keys()):
            self.hour_requests[identifier] = [
                req_time for req_time in self.hour_requests[identifier]
                if current_time - req_time < 3600
            ]
            if not self.hour_requests[identifier]:
                del self.hour_requests[identifier]
    
    def _check_rate_limit(self, identifier: str, current_time: float) -> bool:
        minute_count = len(self.minute_requests.get(identifier, []))
        if minute_count >= self.requests_per_minute:
            return False
        
        hour_count = len(self.hour_requests.get(identifier, []))
        if hour_count >= self.requests_per_hour:
            return False
        
        return True

app = FastAPI(
    title="Disblox API",
    version="1.0.0"
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RateLimitMiddleware, requests_per_minute=60, requests_per_hour=1000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(roblox_router)
app.include_router(server_router)

@app.get("/api/csp-nonce")
async def get_csp_nonce(request: Request):
    nonce = request.headers.get("X-CSP-Nonce", "")
    return {"nonce": nonce}

@app.on_event("startup")
async def startup_event():
    await init_async_db()

def run_fastapi():
    uvicorn.run(
        "server:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,
        log_level="info"
    )

def run_discord_bot():
    bot_manager.create_bot()
    bot_manager.start_bot()

def main():
    if config.DISCORD_TOKEN:
        bot_thread = threading.Thread(target=run_discord_bot, daemon=True)
        bot_thread.start()
    
    run_fastapi()

if __name__ == "__main__":
    main()