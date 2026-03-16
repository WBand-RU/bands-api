import enum
import uuid
from datetime import datetime, timedelta

from sqlalchemy import DateTime, Enum, ForeignKey, String, UniqueConstraint, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Role(str, enum.Enum):
    owner = "owner"
    admin = "admin"
    member = "member"


class Band(Base):
    __tablename__ = "bands"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    members: Mapped[list["BandMember"]] = relationship(back_populates="band", cascade="all, delete-orphan")
    invites: Mapped[list["Invite"]] = relationship(back_populates="band", cascade="all, delete-orphan")


class BandMember(Base):
    __tablename__ = "band_members"
    __table_args__ = (
        UniqueConstraint("band_id", "user_id", name="uq_band_member"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    band_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bands.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[Role] = mapped_column(Enum(Role), nullable=False, default=Role.member)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    band: Mapped[Band] = relationship(back_populates="members")


class InviteStatus(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    expired = "expired"
    declined = "declined"
    revoked = "revoked"


class Invite(Base):
    __tablename__ = "invites"
    __table_args__ = (
        UniqueConstraint("band_id", "email", name="uq_invite_band_email"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    band_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bands.id", ondelete="CASCADE"), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, default=lambda: uuid.uuid4().hex)
    status: Mapped[InviteStatus] = mapped_column(Enum(InviteStatus), default=InviteStatus.pending, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=lambda: datetime.utcnow() + timedelta(days=7))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    band: Mapped[Band] = relationship(back_populates="invites")

    @property
    def is_expired(self) -> bool:
        return datetime.utcnow() >= self.expires_at

    def mark_expired(self) -> None:
        if self.status == InviteStatus.pending and self.is_expired:
            self.status = InviteStatus.expired


__all__ = ["Band", "BandMember", "Invite", "Role", "InviteStatus"]
