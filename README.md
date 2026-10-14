# startng-be

FastAPI backend for start.ng. Postgres + SQLAlchemy + Alembic. Email/password and Google sign-in (returns a JWT).

## Endpoints
- `GET /health`
- `POST /auth/register`, `POST /auth/login`, `POST /auth/google` (`{id_token}`), `GET /auth/me`

## Run locally
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

## Deploy (Coolify)
Dockerfile build pack, port 8000. The container runs `alembic upgrade head` before starting.
Set the variables from `.env.example`. GitHub secrets: `COOLIFY_WEBHOOK_URL` (optional `COOLIFY_TOKEN`);
the `Deploy (Coolify)` workflow runs only after `Malware Scan (CI Gate)` passes on `main`.
