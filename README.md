# Bands Service

FastAPI‑сервис для управления группами и участниками музыкантов. Аутентификация через Bearer JWT (Keycloak), хранение в Postgres, SQLAlchemy async.

## Что умеет сервис

- Аутентификация по Bearer JWT (Keycloak), проверка iss/aud/exp; JWKS с кэшированием, graceful 503 при недоступном IdP
- Список групп пользователя с их ролью + пагинация
- Получение группы по id (только участники)
- Создание/переименование/удаление группы (creator → owner; rename admin/owner; delete owner)
- Список участников с расширением профилей через Keycloak Admin API (кэш) + пагинация
- Удаление участника (admin/owner; владелец не удаляем)
- Обновление роли участника (owner; запрещено назначать/менять owner)
- Инвайты: создание, список с фильтром по статусу и пагинацией, accept/decline/status по токену, revoke, resend (с перевыпуском токена и продлением)
- Передача владения (owner → другой участник)
- CORS по списку из env
- Health check

## Быстрый старт

1. Установить зависимости (лучше в venv):

```bash
pip install -r requirements.txt
```

2. Настроить переменные окружения (или `.env`):

- `DATABASE_URL` например `postgresql+asyncpg://postgres:postgres@localhost:5432/bands`
- `KEYCLOAK_ISSUER_URL` публичный issuer (как в токене), например `https://keycloak.example.com/realms/example`
- `KEYCLOAK_INTERNAL_URL` необязательно: внутренний адрес для вызовов JWKS/token/profile, если внешний недоступен изнутри (например `http://keycloak:8080/realms/example`)
- `KEYCLOAK_AUDIENCE` (resource/client) например `bands-service`
- `KEYCLOAK_CLIENT_ID` / `KEYCLOAK_CLIENT_SECRET` — для profile enrichment (admin API)
- `CORS_ORIGINS` JSON-массив строк, например `["http://localhost:3000"]`
- `AUTH_DISABLE_VERIFICATION` (bool) — для тестов/локали, отключает проверку подписи

3. Миграции: минимально таблицы создаются при старте через `init_db()`. В проде подключите Alembic.

4. Запуск:

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

## Аутентификация

- JWT проверяются по iss/aud/exp и подписи через JWKS (с кэшем). При недоступности IdP возвращается 503.
- Для тестов/локали можно выставить `AUTH_DISABLE_VERIFICATION=true`.

## Модель данных (упрощённо)

- `bands` (id, name, created_at)
- `band_members` (id, band_id, user_id, role)
- `invites` (id, band_id, email, token, status, expires_at)

Roles: `owner`, `admin`, `member`.

## API (сводка)

- `GET /bands?limit&offset` — список групп пользователя, с ролью
- `GET /bands/{band_id}` — данные группы (только участники)
- `POST /bands` — создать, возвращает роль=owner
- `PATCH /bands/{band_id}/rename` — owner/admin
- `DELETE /bands/{band_id}` — owner

### Участники

- `GET /bands/{band_id}/members?limit&offset`
- `DELETE /bands/{band_id}/members/{user_id}` — admin/owner, owner не удаляем
- `PUT /bands/{band_id}/members/{user_id}/role` — owner, нельзя назначить owner

### Инвайты

- `POST /bands/{band_id}/invites` — admin/owner
- `GET /bands/{band_id}/invites?status_filter&limit&offset` — admin/owner
- `POST /invites/{token}/accept`
- `POST /invites/{token}/decline`
- `GET /invites/{token}/status`
- `POST /bands/{band_id}/invites/{invite_id}/revoke` — нельзя повторно/revoked или accepted
- `POST /bands/{band_id}/invites/{invite_id}/resend` — нельзя из pending/accepted, перевыпуск токена, статус → pending

### Владение

- `POST /bands/{band_id}/transfer-ownership` — owner → другой участник

### Тех

- `GET /health`

Все, кроме `/health`, требуют Bearer JWT.
