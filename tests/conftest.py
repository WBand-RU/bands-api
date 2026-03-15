import asyncio
import os
import sys
from datetime import datetime, timedelta
from typing import Callable

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from jose import jwt
from sqlalchemy.ext.asyncio import async_sessionmaker

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

# Configure test database and auth defaults before app imports
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("KEYCLOAK_ISSUER_URL", "https://keycloak.example.com/realms/example")
os.environ.setdefault("KEYCLOAK_AUDIENCE", "bands-service")
os.environ.setdefault("AUTH_DISABLE_VERIFICATION", "true")

from app.main import app  # noqa: E402
from app.database import Base, engine, get_session  # noqa: E402


@pytest_asyncio.fixture(scope="session")
def event_loop() -> asyncio.AbstractEventLoop:  # pytest-asyncio uses this
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture()
async def session():
    # Clean database for each test
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with SessionLocal() as db:
        yield db


@pytest_asyncio.fixture()
async def client(session):
    # Override FastAPI dependency to use test session
    async def _override_session():
        yield session

    app.dependency_overrides[get_session] = _override_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture()
def token_factory() -> Callable[[str], str]:
    def _make(sub: str) -> str:
        payload = {
            "sub": sub,
            "email": f"{sub}@example.com",
            "iss": os.environ["KEYCLOAK_ISSUER_URL"],
            "aud": os.environ["KEYCLOAK_AUDIENCE"],
            "exp": datetime.utcnow() + timedelta(hours=1),
        }
        return jwt.encode(payload, key="secret", algorithm="HS256")

    return _make
