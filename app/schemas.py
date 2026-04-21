import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr

from app.models import InviteStatus, Role


class User(BaseModel):
    """User profile schema for /me endpoint"""
    sub: str
    email: Optional[str] = None
    name: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class BandCreate(BaseModel):
    name: str


class BandRename(BaseModel):
    name: str


class BandOut(BaseModel):
    id: uuid.UUID
    name: str
    role: Role

    model_config = ConfigDict(from_attributes=True)


class MemberOut(BaseModel):
    id: uuid.UUID
    user_id: str
    role: Role
    created_at: datetime
    name: Optional[str] = None
    email: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class InviteCreate(BaseModel):
    email: EmailStr


class InviteOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    status: InviteStatus
    token: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class MyInviteOut(BaseModel):
    id: uuid.UUID
    band_id: uuid.UUID
    band_name: str
    email: EmailStr
    status: InviteStatus
    token: str
    expires_at: datetime

    model_config = ConfigDict(from_attributes=True)


class Message(BaseModel):
    message: str


class RoleUpdate(BaseModel):
    role: Role


class MemberRemove(BaseModel):
    user_id: str


class TransferOwnership(BaseModel):
    new_owner_user_id: str


class BandCheckName(BaseModel):
    name: str
    available: bool


class Pagination(BaseModel):
    total: int
    items: list
