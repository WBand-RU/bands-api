import uuid
from datetime import datetime, timedelta

import httpx
import pytest

from app import auth as auth_module
from app.models import BandMember, Invite, InviteStatus, Role


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_health_open(client):
    res = await client.get("/health")
    assert res.status_code == 200
    assert res.json()["message"] == "ok"


@pytest.mark.asyncio
async def test_create_and_list_bands(client, token_factory):
    token = token_factory("user1")
    res = await client.post("/bands", json={"name": "MyBand"}, headers=_auth(token))
    assert res.status_code == 201, res.text
    data = res.json()
    assert data["name"] == "MyBand"
    assert data["role"] == Role.owner.value

    res = await client.get("/bands", headers=_auth(token))
    assert res.status_code == 200
    bands = res.json()
    assert len(bands) == 1
    assert bands[0]["role"] == Role.owner.value


@pytest.mark.asyncio
async def test_get_band_by_id_requires_membership(client, token_factory):
    owner = token_factory("owner-get")
    outsider = token_factory("outsider-get")

    created = await client.post("/bands", json={"name": "One"}, headers=_auth(owner))
    band_id = created.json()["id"]

    res = await client.get(f"/bands/{band_id}", headers=_auth(owner))
    assert res.status_code == 200
    assert res.json()["id"] == band_id
    assert res.json()["role"] == Role.owner.value

    res = await client.get(f"/bands/{band_id}", headers=_auth(outsider))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_rename_band(client, token_factory):
    token = token_factory("user1")
    create = await client.post("/bands", json={"name": "Old"}, headers=_auth(token))
    band_id = create.json()["id"]

    res = await client.patch(
        f"/bands/{band_id}/rename",
        json={"name": "New"},
        headers=_auth(token),
    )
    assert res.status_code == 200
    assert res.json()["name"] == "New"


@pytest.mark.asyncio
async def test_delete_band_owner_only(client, token_factory):
    owner_token = token_factory("owner")
    member_token = token_factory("member")

    create = await client.post("/bands", json={"name": "Del"}, headers=_auth(owner_token))
    band_id = create.json()["id"]

    res = await client.delete(f"/bands/{band_id}", headers=_auth(member_token))
    assert res.status_code == 403

    res = await client.delete(f"/bands/{band_id}", headers=_auth(owner_token))
    assert res.status_code == 200


@pytest.mark.asyncio
async def test_members_listing_and_removal(client, session, token_factory):
    owner_token = token_factory("owner")
    other_token = token_factory("other")

    create = await client.post("/bands", json={"name": "Members"}, headers=_auth(owner_token))
    band_id = create.json()["id"]

    # Seed another member directly in DB
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="other", role=Role.member))
    await session.commit()

    res = await client.get(f"/bands/{band_id}/members", headers=_auth(owner_token))
    assert res.status_code == 200
    members = res.json()
    assert {m["user_id"] for m in members} == {"owner", "other"}

    # Non-owner (member) cannot remove; owner can remove member (not self)
    res = await client.delete(
        f"/bands/{band_id}/members/owner",
        headers=_auth(other_token),
    )
    assert res.status_code == 403

    res = await client.delete(
        f"/bands/{band_id}/members/other",
        headers=_auth(owner_token),
    )
    assert res.status_code == 200

    res = await client.get(f"/bands/{band_id}/members", headers=_auth(owner_token))
    assert {m["user_id"] for m in res.json()} == {"owner"}


@pytest.mark.asyncio
async def test_update_member_role_owner_only(client, session, token_factory):
    owner_token = token_factory("owner")
    admin_token = token_factory("admin")
    res = await client.post("/bands", json={"name": "Roles"}, headers=_auth(owner_token))
    band_id = res.json()["id"]

    # add member for update
    member_id = "member1"
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id=member_id, role=Role.member))
    await session.commit()

    # admin (non-owner) cannot change role
    res = await client.put(
        f"/bands/{band_id}/members/{member_id}/role",
        json={"role": Role.admin.value},
        headers=_auth(admin_token),
    )
    assert res.status_code == 404 or res.status_code == 403

    # owner can promote member to admin
    res = await client.put(
        f"/bands/{band_id}/members/{member_id}/role",
        json={"role": Role.admin.value},
        headers=_auth(owner_token),
    )
    assert res.status_code == 200
    assert res.json()["role"] == Role.admin.value

    # cannot assign owner role
    res = await client.put(
        f"/bands/{band_id}/members/{uuid.uuid4()}/role",
        json={"role": Role.owner.value},
        headers=_auth(owner_token),
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_create_invite_admin_or_owner(client, token_factory):
    owner_token = token_factory("owner")
    res = await client.post("/bands", json={"name": "Inv"}, headers=_auth(owner_token))
    band_id = res.json()["id"]

    res = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "test@example.com"},
        headers=_auth(owner_token),
    )
    assert res.status_code == 201
    assert res.json()["email"] == "test@example.com"


@pytest.mark.asyncio
async def test_requires_auth_returns_401(client):
    res = await client.get("/bands")
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_identity_provider_timeout_returns_503(client, token_factory, monkeypatch):
    token = token_factory("jwks-timeout")
    auth_module._jwks_cache.clear()
    monkeypatch.setattr(auth_module.settings, "auth_disable_verification", False)

    class _FailingAsyncClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def get(self, *args, **kwargs):
            raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(auth_module.httpx, "AsyncClient", _FailingAsyncClient)

    res = await client.get("/bands", headers=_auth(token))
    assert res.status_code == 503
    assert res.json()["detail"] == "Identity provider unavailable"


@pytest.mark.asyncio
async def test_duplicate_band_name_returns_400(client, token_factory):
    token = token_factory("u1")
    res = await client.post("/bands", json={"name": "Dup"}, headers=_auth(token))
    assert res.status_code == 201
    res = await client.post("/bands", json={"name": "Dup"}, headers=_auth(token))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_rename_forbidden_for_member(client, token_factory, session):
    owner = token_factory("owner2")
    member = token_factory("member2")
    create = await client.post("/bands", json={"name": "Protected"}, headers=_auth(owner))
    band_id = create.json()["id"]

    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="member2", role=Role.member))
    await session.commit()

    res = await client.patch(
        f"/bands/{band_id}/rename",
        json={"name": "Nope"},
        headers=_auth(member),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_invite_duplicate_email_returns_400(client, token_factory):
    owner = token_factory("owner3")
    create = await client.post("/bands", json={"name": "InvDup"}, headers=_auth(owner))
    band_id = create.json()["id"]

    first = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "dup@example.com"},
        headers=_auth(owner),
    )
    assert first.status_code == 201

    second = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "dup@example.com"},
        headers=_auth(owner),
    )
    assert second.status_code == 400


@pytest.mark.asyncio
async def test_list_bands_pagination(client, token_factory):
    token = token_factory("pager")
    for name in ("Band1", "Band2", "Band3"):
        res = await client.post("/bands", json={"name": name}, headers=_auth(token))
        assert res.status_code == 201

    res = await client.get("/bands?limit=2&offset=1", headers=_auth(token))
    assert res.status_code == 200
    items = res.json()
    assert len(items) == 2


@pytest.mark.asyncio
async def test_rename_band_not_found_returns_404(client, token_factory):
    token = token_factory("rename-missing")
    res = await client.patch(
        f"/bands/{uuid.uuid4()}/rename",
        json={"name": "New"},
        headers=_auth(token),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_delete_band_not_found_returns_404(client, token_factory):
    token = token_factory("delete-missing")
    res = await client.delete(f"/bands/{uuid.uuid4()}", headers=_auth(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_list_members_requires_membership_returns_404(client, token_factory):
    token = token_factory("outsider")
    res = await client.get(f"/bands/{uuid.uuid4()}/members", headers=_auth(token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_remove_member_not_found_returns_404(client, token_factory):
    owner = token_factory("owner-rm")
    create = await client.post("/bands", json={"name": "Rm404"}, headers=_auth(owner))
    band_id = create.json()["id"]

    res = await client.delete(f"/bands/{band_id}/members/ghost", headers=_auth(owner))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_remove_owner_is_forbidden(client, token_factory):
    owner = token_factory("owner-self")
    create = await client.post("/bands", json={"name": "NoSelfDelete"}, headers=_auth(owner))
    band_id = create.json()["id"]

    res = await client.delete(f"/bands/{band_id}/members/owner-self", headers=_auth(owner))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_update_member_role_member_not_found_returns_404(client, token_factory):
    owner = token_factory("owner-role404")
    create = await client.post("/bands", json={"name": "Role404"}, headers=_auth(owner))
    band_id = create.json()["id"]

    res = await client.put(
        f"/bands/{band_id}/members/ghost/role",
        json={"role": Role.admin.value},
        headers=_auth(owner),
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_create_invite_forbidden_for_member(client, session, token_factory):
    owner = token_factory("owner-invite-member")
    member = token_factory("member-invite-member")
    create = await client.post("/bands", json={"name": "Invite403"}, headers=_auth(owner))
    band_id = create.json()["id"]
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="member-invite-member", role=Role.member))
    await session.commit()

    res = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "member@example.com"},
        headers=_auth(member),
    )
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_list_invites_success_filter_and_pagination(client, token_factory):
    owner = token_factory("owner-list-inv")
    create = await client.post("/bands", json={"name": "InvList"}, headers=_auth(owner))
    band_id = create.json()["id"]

    emails = ["a@example.com", "b@example.com", "c@example.com"]
    invite_ids = []
    for email in emails:
        res = await client.post(
            f"/bands/{band_id}/invites",
            json={"email": email},
            headers=_auth(owner),
        )
        assert res.status_code == 201
        invite_ids.append(res.json()["id"])

    revoke = await client.post(
        f"/bands/{band_id}/invites/{invite_ids[0]}/revoke",
        headers=_auth(owner),
    )
    assert revoke.status_code == 200

    res = await client.get(
        f"/bands/{band_id}/invites?status_filter={InviteStatus.pending.value}&limit=1&offset=0",
        headers=_auth(owner),
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["status"] == InviteStatus.pending.value


@pytest.mark.asyncio
async def test_list_invites_forbidden_for_member(client, session, token_factory):
    owner = token_factory("owner-inv-list-403")
    member = token_factory("member-inv-list-403")
    create = await client.post("/bands", json={"name": "InvList403"}, headers=_auth(owner))
    band_id = create.json()["id"]
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="member-inv-list-403", role=Role.member))
    await session.commit()

    res = await client.get(f"/bands/{band_id}/invites", headers=_auth(member))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_accept_invite_success_and_already_member(client, token_factory):
    owner = token_factory("owner-accept")
    invitee = token_factory("invitee-accept")
    create = await client.post("/bands", json={"name": "AcceptBand"}, headers=_auth(owner))
    band_id = create.json()["id"]

    invite = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "accept@example.com"},
        headers=_auth(owner),
    )
    token = invite.json()["token"]

    res = await client.post(f"/invites/{token}/accept", headers=_auth(invitee))
    assert res.status_code == 200
    assert res.json()["message"] == "Invite accepted"

    res = await client.post(f"/invites/{token}/accept", headers=_auth(invitee))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_accept_invite_not_found_and_expired(client, session, token_factory):
    user = token_factory("invitee-missing")
    res = await client.post(f"/invites/{uuid.uuid4().hex}/accept", headers=_auth(user))
    assert res.status_code == 404

    owner = token_factory("owner-expired")
    create = await client.post("/bands", json={"name": "ExpiredBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    invite = Invite(
        band_id=uuid.UUID(band_id),
        email="expired@example.com",
        expires_at=datetime.utcnow() - timedelta(days=1),
    )
    session.add(invite)
    await session.commit()
    await session.refresh(invite)

    res = await client.post(f"/invites/{invite.token}/accept", headers=_auth(user))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_decline_invite_success_and_inactive_error(client, token_factory):
    owner = token_factory("owner-decline")
    user = token_factory("user-decline")
    create = await client.post("/bands", json={"name": "DeclineBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    invite = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "decline@example.com"},
        headers=_auth(owner),
    )
    token = invite.json()["token"]

    res = await client.post(f"/invites/{token}/decline", headers=_auth(user))
    assert res.status_code == 200
    assert res.json()["message"] == "Invite declined"

    res = await client.post(f"/invites/{token}/decline", headers=_auth(user))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_invite_status_success_and_not_found(client, token_factory):
    owner = token_factory("owner-status")
    create = await client.post("/bands", json={"name": "StatusBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    invite = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "status@example.com"},
        headers=_auth(owner),
    )
    token = invite.json()["token"]

    res = await client.get(f"/invites/{token}/status")
    assert res.status_code == 200
    assert res.json()["status"] == InviteStatus.pending.value

    res = await client.get(f"/invites/{uuid.uuid4().hex}/status")
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_revoke_invite_success_and_not_found(client, token_factory):
    owner = token_factory("owner-revoke")
    create = await client.post("/bands", json={"name": "RevokeBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    invite = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "revoke@example.com"},
        headers=_auth(owner),
    )
    invite_id = invite.json()["id"]

    res = await client.post(f"/bands/{band_id}/invites/{invite_id}/revoke", headers=_auth(owner))
    assert res.status_code == 200

    res = await client.post(f"/bands/{band_id}/invites/{uuid.uuid4()}/revoke", headers=_auth(owner))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_resend_invite_success_and_accepted_error(client, token_factory):
    owner = token_factory("owner-resend")
    invitee = token_factory("invitee-resend")
    create = await client.post("/bands", json={"name": "ResendBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    invite = await client.post(
        f"/bands/{band_id}/invites",
        json={"email": "resend@example.com"},
        headers=_auth(owner),
    )
    invite_id = invite.json()["id"]
    original_token = invite.json()["token"]

    revoke = await client.post(f"/bands/{band_id}/invites/{invite_id}/revoke", headers=_auth(owner))
    assert revoke.status_code == 200

    resent = await client.post(f"/bands/{band_id}/invites/{invite_id}/resend", headers=_auth(owner))
    assert resent.status_code == 200
    assert resent.json()["token"] != original_token
    new_token = resent.json()["token"]

    accepted = await client.post(f"/invites/{new_token}/accept", headers=_auth(invitee))
    assert accepted.status_code == 200

    res = await client.post(f"/bands/{band_id}/invites/{invite_id}/resend", headers=_auth(owner))
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_transfer_ownership_success_and_errors(client, session, token_factory):
    owner = token_factory("owner-transfer")
    member = token_factory("member-transfer")
    outsider = token_factory("outsider-transfer")
    create = await client.post("/bands", json={"name": "TransferBand"}, headers=_auth(owner))
    band_id = create.json()["id"]
    session.add(BandMember(band_id=uuid.UUID(band_id), user_id="member-transfer", role=Role.member))
    await session.commit()

    res = await client.post(
        f"/bands/{band_id}/transfer-ownership",
        json={"new_owner_user_id": "member-transfer"},
        headers=_auth(outsider),
    )
    assert res.status_code == 404

    res = await client.post(
        f"/bands/{band_id}/transfer-ownership",
        json={"new_owner_user_id": "ghost"},
        headers=_auth(owner),
    )
    assert res.status_code == 404

    res = await client.post(
        f"/bands/{band_id}/transfer-ownership",
        json={"new_owner_user_id": "member-transfer"},
        headers=_auth(owner),
    )
    assert res.status_code == 200
    assert res.json()["role"] == Role.owner.value

    res = await client.post(
        f"/bands/{band_id}/transfer-ownership",
        json={"new_owner_user_id": "owner-transfer"},
        headers=_auth(owner),
    )
    assert res.status_code == 403
