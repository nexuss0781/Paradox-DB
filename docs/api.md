# Paradox-DB Gateway API Reference

Base URL: `https://your-domain.com/v1`

Authentication uses the `X-API-Key` header. Paradox `pk_` keys are accepted normally. A verified Nexuss `nxa_` key may be supplied directly only for trusted server-to-server clients; CLI and SDK clients should exchange it once and persist the returned Paradox key instead. Do not use `Authorization: Bearer` for Paradox gateway requests.

---

## Health Check

```text
GET /health
```

**Response:** `200 OK`

```json
{"status":"healthy"}
```

---

## Nexuss Auth

Interactive Google sign-in is performed by the configured Nexuss project. The Paradox gateway only validates the resulting Nexuss identity; it never receives Google client credentials.

### Exchange a Nexuss API key

```text
POST /v1/auth/nexuss/exchange
Content-Type: application/json
```

```json
{"api_key":"nxa_..."}
```

**Response:** `200 OK`

```json
{
  "user_id": "uuid",
  "email": "user@example.com",
  "username": "user-identity",
  "api_key": "pk_..."
}
```

The Nexuss key is verified against `NEXUSS_AUTH_PROJECT_ID` and is not persisted by Paradox. Store only the returned `pk_` key.

### Exchange a trusted browser handoff

```text
POST /v1/auth/nexuss/handoff
Content-Type: application/json
```

```json
{"handoff_token":"one-time-token"}
```

This endpoint is for a trusted Paradox server callback after Nexuss Google sign-in with `handoff=1`. A handoff token is single-use and must never be exposed to browser code, logs, or URLs.

---

## Sync

### Upload (Push)

```text
POST /v1/upload
X-API-Key: pk_...
Content-Type: application/json
```

```json
{
  "database_name": "mydb",
  "file_data": "<base64-encoded-sqlite>",
  "version": 1,
  "version_type": "auto"
}
```

**Response:** `200 OK`

```json
{
  "request_id": "uuid",
  "message_id": "12345",
  "version": 2,
  "uploaded_at": "2026-07-23T12:00:00Z"
}
```

**Errors:** `401` invalid key, `409` version conflict, `429` rate limited, `502` upstream upload failure.

### Download (Pull)

```text
GET /v1/download?database_name=mydb&version=latest
X-API-Key: pk_...
```

**Response:** `200 OK` — Binary SQLite file.

### Versions, rollback, and status

All authenticated requests use `X-API-Key: pk_...`:

```text
GET  /v1/versions?database_name=mydb
POST /v1/rollback
GET  /v1/status
```

---

## Rate Limits

| Endpoint family | Limit | Burst |
|---|---:|---:|
| `/v1/upload` | 15/min | 20 |
| `/v1/auth/*` | 10/min | 5 |
| Other | 60/min | 20 |
