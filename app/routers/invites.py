"""Router for invite management endpoints."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import schemas
from app.auth import User, get_current_user
from app.database import get_session
from app.models import Band, BandMember, Invite, InviteStatus, Role

router = APIRouter()


async def _get_membership_or_404(
    session: AsyncSession, band_id: uuid.UUID, user_id: str
) -> BandMember:
    result = await session.execute(
        select(BandMember).where(BandMember.band_id == band_id, BandMember.user_id == user_id)
    )
    member = result.scalar_one_or_none()
    if not member:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Band not found")
    return member


def _ensure_admin_or_owner(member: BandMember) -> None:
    if member.role not in {Role.owner, Role.admin}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient rights")


@router.get("/me/invites", response_model=List[schemas.MyInviteOut])
async def list_my_invites(
    status_filter: InviteStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List all invites for the current user's email."""
    if not user.email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email not available in token")

    stmt = (
        select(Invite, Band.name)
        .join(Band, Invite.band_id == Band.id)
        .where(Invite.email == user.email)
        .order_by(Invite.created_at.desc())
    )
    if status_filter:
        stmt = stmt.where(Invite.status == status_filter)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    invites = []
    for invite, band_name in result.all():
        if invite.is_expired:
            invite.mark_expired()
        invites.append(
            schemas.MyInviteOut(
                id=invite.id,
                band_id=invite.band_id,
                band_name=band_name,
                email=invite.email,
                status=invite.status,
                token=invite.token,
                expires_at=invite.expires_at,
            )
        )
    if any(i.status == InviteStatus.expired for i in invites):
        await session.commit()
    return invites


@router.post("/bands/{band_id}/invites", response_model=schemas.InviteOut, status_code=status.HTTP_201_CREATED)
async def create_invite(
    band_id: uuid.UUID,
    payload: schemas.InviteCreate,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Create a new invite to join a band."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = Invite(band_id=band_id, email=payload.email)
    session.add(invite)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite already exists")
    await session.refresh(invite)
    return invite


@router.get("/bands/{band_id}/invites", response_model=List[schemas.InviteOut])
async def list_invites(
    band_id: uuid.UUID,
    status_filter: InviteStatus | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """List all invites for a band (admin/owner only)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    stmt = select(Invite).where(Invite.band_id == band_id).order_by(Invite.created_at.desc())
    if status_filter:
        stmt = stmt.where(Invite.status == status_filter)
    stmt = stmt.offset(offset).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


@router.post("/invites/{token}/accept", response_model=schemas.Message)
async def accept_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Accept an invite to join a band."""
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.is_expired:
        invite.mark_expired()
        await session.commit()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite expired")
    if invite.status != InviteStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite not active")

    # add member if not exists
    existing = await session.execute(
        select(BandMember).where(BandMember.band_id == invite.band_id, BandMember.user_id == user.sub)
    )
    if existing.scalar_one_or_none():
        invite.status = InviteStatus.accepted
        await session.commit()
        return schemas.Message(message="Already a member")

    member = BandMember(band_id=invite.band_id, user_id=user.sub, role=Role.member)
    session.add(member)
    invite.status = InviteStatus.accepted
    await session.commit()
    return schemas.Message(message="Invite accepted")


@router.post("/invites/{token}/decline", response_model=schemas.Message)
async def decline_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Decline an invite."""
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status != InviteStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite not active")
    invite.status = InviteStatus.declined
    await session.commit()
    return schemas.Message(message="Invite declined")


@router.get("/invites/{token}/status", response_model=schemas.InviteOut)
async def invite_status(token: str, session: AsyncSession = Depends(get_session)):
    """Get invite status by token (public endpoint)."""
    invite = await session.execute(select(Invite).where(Invite.token == token))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.is_expired:
        invite.mark_expired()
        await session.commit()
    return invite


@router.post("/bands/{band_id}/invites/{invite_id}/revoke", response_model=schemas.Message)
async def revoke_invite(
    band_id: uuid.UUID,
    invite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Revoke an invite (admin/owner only)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = await session.execute(select(Invite).where(Invite.id == invite_id, Invite.band_id == band_id))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status == InviteStatus.revoked:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite already revoked")
    if invite.status == InviteStatus.accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot revoke accepted invite")
    invite.status = InviteStatus.revoked
    await session.commit()
    return schemas.Message(message="Invite revoked")


@router.post("/bands/{band_id}/invites/{invite_id}/resend", response_model=schemas.InviteOut)
async def resend_invite(
    band_id: uuid.UUID,
    invite_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    user: User = Depends(get_current_user),
):
    """Resend an invite (admin/owner only)."""
    membership = await _get_membership_or_404(session, band_id, user.sub)
    _ensure_admin_or_owner(membership)

    invite = await session.execute(select(Invite).where(Invite.id == invite_id, Invite.band_id == band_id))
    invite = invite.scalar_one_or_none()
    if not invite:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Invite not found")
    if invite.status == InviteStatus.accepted:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Cannot resend accepted invite")
    if invite.status == InviteStatus.pending:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invite already active")
    # regenerate token and extend expiration for non-pending (declined/revoked/expired)
    invite.token = uuid.uuid4().hex
    invite.status = InviteStatus.pending
    invite.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    await session.commit()
    await session.refresh(invite)
    return invite
