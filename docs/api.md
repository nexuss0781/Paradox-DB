# Paradox-DB Gateway API Reference

Base URL: `https://your-domain.com/v1`

Authentication: `X-API-Key` header or `Authorization: Bearer <jwt>`

---

## Health Check

```
GET /health
```

**Response:** `200 OK`
```json
{"status": "healthy"}
```

---

## Authentication

### Register

```
POST /v1/auth/register
```

Register a new user and receive an API key.

**Response:** `200 OK`
```json
{
  "user_id": "uuid",
  "api_key": "pk_...",
  "channel_id": ""
}
```

---

## Sync

### Upload (Push)

```
POST /v1/upload
Authorization: Bearer <token>
Content-Type: application/json

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

**Errors:**
- `409` — Conflict detected (version mismatch)
- `429` — Rate limited
- `502` — Telegram upload failed

### Download (Pull)

```
GET /v1/download?database_name=mydb&version=latest
Authorization: Bearer <token>
```

**Response:** `200 OK` — Binary SQLite file

### Versions

```
GET /v1/versions?database_name=mydb
Authorization: Bearer <token>
```

**Response:** `200 OK`
```json
{
  "database_name": "mydb",
  "versions": [
    {"version": 1, "message_id": "100", "uploaded_at": "...", "size_bytes": 8192},
    {"version": 2, "message_id": "200", "uploaded_at": "...", "size_bytes": 12288}
  ]
}
```

### Rollback

```
POST /v1/rollback
Authorization: Bearer <token>
Content-Type: application/json

{
  "database_name": "mydb",
  "target_version": 1
}
```

**Response:** `200 OK`
```json
{
  "request_id": "uuid",
  "rolled_back_to": 1,
  "new_message_id": "300"
}
```

### Status

```
GET /v1/status
Authorization: Bearer <token>
```

**Response:** `200 OK`
```json
{
  "user_id": "uuid",
  "databases": [
    {
      "name": "mydb",
      "latest_version": 3,
      "latest_message_id": "300",
      "pending_changesets": 0,
      "last_sync_at": "2026-07-23T12:00:00Z"
    }
  ]
}
```

---

## Rate Limits

| Endpoint | Limit | Burst |
|----------|-------|-------|
| `/v1/upload` | 15/min | 20 |
| `/v1/auth/*` | 10/min | 5 |
| Other | 60/min | 20 |
