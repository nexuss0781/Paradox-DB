# Deploy Paradox-DB on Wasmer

Paradox-DB now has one root-level Python entrypoint for Wasmer:

```text
app.py
```

Use the **Python** or **FastAPI** preset. Wasmer should detect `requirements.txt` and start the application with:

```bash
python app.py
```

The equivalent ASGI command is:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT --workers 1
```

The application listens on `PORT` and defaults to `8000`. The root `app.py` exposes every existing gateway route; no route functionality was removed.

## Environment variables

Configure these Wasmer secrets or environment variables:

```text
DATABASE_URL
REDIS_URL
TELEGRAM_BOT_TOKEN
TELEGRAM_API_ID
TELEGRAM_API_HASH
JWT_SECRET
API_KEY_SALT
```

`DATABASE_URL` must point to an external PostgreSQL instance and `REDIS_URL` to an external Redis instance. Wasmer Apps are stateless and should not use the local default database values in production.

## Wasmer settings

| Setting | Value |
|---|---|
| Runtime/preset | Python or FastAPI |
| Python version | 3.11+ |
| Dependency file | `requirements.txt` |
| Start command | `python app.py` |
| Port | `$PORT` |
| Health check | `/health` |
