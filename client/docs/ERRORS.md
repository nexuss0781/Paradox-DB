# Errors

Every error the SDK throws is a subclass of the standard `Error`. The full set
is exported from the package root.

```ts
import {
  DecryptionError, DatabaseNotOpenError, SQLiteError, EncryptionError,
  ConfigError, ConflictError, RateLimitError, AuthenticationError,
  NetworkError, GatewayError,
} from 'parad';
```

## Error classes

| Class | When thrown | Notes |
| --- | --- | --- |
| `DecryptionError` | Opening a database: wrong passphrase, corrupt/truncated file, invalid padding, or payload that isn't a SQLite database. | Thrown by `decryptFile` and `ClientEngine.open`. |
| `EncryptionError` | Wrong passphrase or corrupted database on the encrypt path. | Parity class with the Python SDK. |
| `DatabaseNotOpenError` | Calling `execute` (or other SQL) while the engine is closed. | Usually means `close()` was called early. |
| `SQLiteError` | A SQL statement failed (`better-sqlite3` error). | Carries the original error in `.originalError`. |
| `GatewayError` | A gateway request returned an HTTP error (>= 400) or failed to connect. | Carries `.statusCode` and `.detail`. |
| `ConflictError` | A 409 conflict surfaced in API form. | Exposes `.remoteVersion`, `.yourVersion`, `.remoteMessageId`. The sync layer handles this internally (local-wins). |
| `RateLimitError` | The gateway rate-limited the request. | Exposes `.retryAfterSeconds`. |
| `AuthenticationError` | Credentials were rejected. | e.g. failed login. |
| `NetworkError` | A network-level failure. | The daemon converts these into offline mode. |
| `ConfigError` | Invalid configuration. | e.g. malformed config.json. |

## `GatewayError` shape

```ts
new GatewayError(statusCode, message, detail?);
err.statusCode;   // HTTP status, or 0 for transport failures
err.detail;       // parsed JSON error body, if any
```

Common status codes you'll see from the gateway:

| Code | Meaning |
| --- | --- |
| `401` | Missing/invalid bearer token. Re-run `connect` with fresh credentials. |
| `409` | Version conflict — the remote moved on. Local-wins logic resolves it. |
| `429` | Rate limited. Check `.retryAfterSeconds`. |
| `5xx` | Gateway outage or cold start. Treated as offline by the daemon. |
| `0` | Transport failure (DNS, refused, reset, timeout). Treated as offline. |

## Detecting offline conditions

Use `isConnectivityError` to decide whether a failure means the network/gateway
is unreachable (as opposed to a deterministic client error like 409/400):

```ts
import { GatewayClient, isConnectivityError } from 'parad';

const gw = new GatewayClient(gatewayUrl, apiKey);
try {
  await gw.status();
} catch (err) {
  if (isConnectivityError(err)) {
    // gateway unreachable — mark offline, retry later
  } else {
    // deterministic error (401, 409, ...) — handle accordingly
  }
}
```

## Handling patterns

```ts
try {
  const db = await connect('parad://local/todos?passphrase=hunter2');
  await db.close();
} catch (err) {
  if (err instanceof DecryptionError) {
    console.error('Wrong passphrase or corrupt file');
  } else if (err instanceof SQLiteError) {
    console.error('SQL failed:', err.message, err.originalError);
  } else if (err instanceof GatewayError && err.statusCode === 401) {
    console.error('Bad credentials — re-authenticate');
  } else {
    throw err;
  }
}
```
