from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.database import init_db
from app.routers import bands, health, invites, members, users

app = FastAPI(title="Bands Service")
settings = get_settings()

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.on_event("startup")
async def _startup() -> None:
    await init_db()


# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(users.router, tags=["Users"])
app.include_router(bands.router, prefix="/bands", tags=["Bands"])
app.include_router(members.router, prefix="/bands", tags=["Members"])
app.include_router(invites.router, tags=["Invites"])
