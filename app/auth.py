import base64
from datetime import datetime, timedelta
from typing import Annotated, Optional
from urllib.parse import urlparse

import httpx
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwk, jwt
from jose.utils import base64url_decode
from pydantic import BaseModel

from app.config import get_settings


class User(BaseModel):
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None


bearer_scheme = HTTPBearer(auto_error=False)
settings = get_settings()

_jwks_cache: dict[str, tuple[dict, datetime]] = {}
_jwks_ttl = timedelta(hours=1)
_profile_cache: dict[str, tuple[dict[str, str], datetime]] = {}
_profile_ttl = timedelta(minutes=10)
_admin_token_cache: Optional[tuple[str, datetime]] = None


async def _fetch_jwks() -> dict:
    if settings.keycloak_issuer_url:
        base_url = settings.keycloak_internal_url or settings.keycloak_issuer_url
        jwks_url = f"{base_url}/protocol/openid-connect/certs"
    else:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Issuer not configured")

    cached = _jwks_cache.get(jwks_url)
    if cached and cached[1] > datetime.utcnow():
        return cached[0]

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(jwks_url, timeout=5)
            resp.raise_for_status()
            jwks = resp.json()
    except (httpx.RequestError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Identity provider unavailable",
        ) from exc

    _jwks_cache[jwks_url] = (jwks, datetime.utcnow() + _jwks_ttl)
    return jwks


async def _decode_token(token: str) -> User:
    if settings.auth_disable_verification:
        try:
            payload = jwt.decode(
                token,
                key=None,
                options={"verify_signature": False, "verify_aud": False},
            )
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token adv") from exc
    else:
        try:
            unverified_header = jwt.get_unverified_header(token)
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token header") from exc

        jwks = await _fetch_jwks()
        keys = jwks.get("keys", [])
        key = next((k for k in keys if k.get("kid") == unverified_header.get("kid")), None)
        if not key:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Signing key not found")

        try:
            public_key = jwk.construct(key)
            message, encoded_sig = token.rsplit('.', 1)
            decoded_sig = base64url_decode(encoded_sig.encode())
            if not public_key.verify(message.encode(), decoded_sig):
                raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")

            payload = jwt.decode(
                token,
                key=public_key.to_pem().decode(),
                algorithms=[key.get("alg", "RS256")],
                audience=settings.keycloak_audience,
                issuer=settings.keycloak_issuer_url,
            )
        except JWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    return User(
        sub=payload.get("sub"),
        email=payload.get("email"),
        name=payload.get("name") or payload.get("preferred_username"),
    )


async def fetch_profile(user_id: str) -> dict[str, str]:
    cached = _profile_cache.get(user_id)
    if cached and cached[1] > datetime.utcnow():
        return cached[0]

    if not settings.keycloak_issuer_url or not settings.keycloak_client_id or not settings.keycloak_client_secret:
        return {}

    # Extract realm and base URL
    parsed = urlparse(settings.keycloak_issuer_url)
    network_base = settings.keycloak_internal_url or f"{parsed.scheme}://{parsed.netloc}{parsed.path.rsplit('/', 1)[0]}"
    netloc_parsed = urlparse(network_base)
    parts = parsed.path.rstrip("/").split("/")
    realm = parts[-1] if parts else None
    issuer_base = f"{netloc_parsed.scheme}://{netloc_parsed.netloc}"
    if not realm:
        return {}

    global _admin_token_cache
    token_cached = _admin_token_cache
    admin_token: Optional[str] = None
    if token_cached and token_cached[1] > datetime.utcnow():
        admin_token = token_cached[0]
    else:
        data = {
            "grant_type": "client_credentials",
            "client_id": settings.keycloak_client_id,
            "client_secret": settings.keycloak_client_secret,
        }
        token_base = settings.keycloak_internal_url or settings.keycloak_issuer_url
        token_url = f"{token_base}/protocol/openid-connect/token"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(token_url, data=data, timeout=5)
                if resp.status_code != 200:
                    return {}
                token_body = resp.json()
        except (httpx.RequestError, ValueError):
            return {}

        admin_token = token_body.get("access_token")
        expires_in = token_body.get("expires_in", 300)
        _admin_token_cache = (admin_token, datetime.utcnow() + timedelta(seconds=expires_in - 30))

    if not admin_token:
        return {}

    user_url = f"{issuer_base}/admin/realms/{realm}/users/{user_id}"
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(user_url, headers={"Authorization": f"Bearer {admin_token}"}, timeout=5)
            if resp.status_code != 200:
                return {}
            data = resp.json()
    except (httpx.RequestError, ValueError):
        return {}

    profile = {
        "email": data.get("email"),
        "name": data.get("firstName") or data.get("username"),
    }
    _profile_cache[user_id] = (profile, datetime.utcnow() + _profile_ttl)
    return profile


async def get_current_user(
    credentials: Annotated[Optional[HTTPAuthorizationCredentials], Depends(bearer_scheme)],
) -> User:
    if not credentials:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    token = credentials.credentials
    user = await _decode_token(token)
    if not user.sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid subject")
    return user
