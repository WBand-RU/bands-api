# Bands Service

FastAPI web API helping musicians manage bands and membership. Authenticated via Keycloak bearer tokens, stores data in Postgres using SQLAlchemy (async).

## Features

- List bands where caller is a member (returns caller role)
- Create band (creator becomes owner)
- Rename band (owner/admin)
- Delete band (owner only)
- List members
- Remove member (admin/owner; cannot remove owner unless caller owner)
- Update member role (owner only; cannot set/modify owner)
- Create invite (admin/owner) issuing magic-link token placeholder
- Health check

## Quickstart

1. Install deps (ideally in venv):

```bash
pip install -r requirements.txt
```

2. Provide env (or .env) values:

- `DATABASE_URL` e.g. `postgresql+asyncpg://postgres:postgres@localhost:5432/bands`
- `KEYCLOAK_ISSUER_URL` e.g. `https://keycloak.example.com/realms/example`
- `KEYCLOAK_AUDIENCE` (resource/client) e.g. `bands-service`

3. Run migrations (minimal auto-create):
   The app auto-creates tables on startup via `init_db()`. For production, wire Alembic later.

4. Run app:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Auth

The demo decoder validates issuer/audience/exp but skips signature verification (expects gateway/keycloak to verify). Replace with proper JWKS verification for production.

## Data model (simplified)

- `bands` (id, name, created_at)
- `band_members` (id, band_id, user_id, role)
- `invites` (id, band_id, email, token, status, expires_at)

Roles: `owner`, `admin`, `member`.

## API sketch

- `GET /bands`
- `POST /bands` {name}
- `PATCH /bands/{band_id}/rename` {name}
- `DELETE /bands/{band_id}`
- `GET /bands/{band_id}/members`
- `DELETE /bands/{band_id}/members/{user_id}`
- `PUT /bands/{band_id}/members/{user_id}/role` {role}
- `POST /bands/{band_id}/invites` {email}
- `GET /health`

All endpoints require bearer auth except `/health`.

