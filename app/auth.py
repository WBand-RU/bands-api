import base64
from datetime import datetime, timedelta
from typing import Annotated, Optional

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


async def _fetch_jwks() -> dict:
    if settings.keycloak_issuer_url:
        jwks_url = f"{settings.keycloak_issuer_url}/protocol/openid-connect/certs"
    else:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Issuer not configured")

    cached = _jwks_cache.get(jwks_url)
    if cached and cached[1] > datetime.utcnow():
        return cached[0]

    async with httpx.AsyncClient() as client:
        resp = await client.get(jwks_url, timeout=5)
        resp.raise_for_status()
        jwks = resp.json()
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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
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
