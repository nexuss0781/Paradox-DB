# Paradox-DB Wasmer Health Report

**Last updated:** 2026-10-04 (pre-deployment checkpoint)

## Executive summary

Production is **not yet healthy**. Before the code push, all four requested routes return HTTP 500 with an HTML response. Read-only Wasmer runtime diagnostics identified an import-time Python dependency failure. A second configuration mismatch was also confirmed: Wasmer provides PostgreSQL connection fields as `DB_*`, while the application expected `DATABASE_URL`. The app also required Redis solely for distributed version-write locking, but no `REDIS_URL` is configured.

A tested local fix is ready for a fast-forward-only push to `main`. Wasmer is connected to that branch, so pushing may start an automatic production deployment. No production environment values have been changed and no deployment has been started manually.

## Verified root cause and deployment facts

- Wasmer's active runtime uses Python 3.13.15. The startup log fails while importing FastAPI/Pydantic, before the application or its health routes initialize:

  ```text
  ModuleNotFoundError: No module named 'pydantic_core._pydantic_core'
  ```

- The failure is consistent with the deployed dependency set not containing the native Pydantic Core extension required by the WASIX Python runtime. The Wasix Python index publishes Python 3.13-compatible Pydantic and Pydantic Core wheels; the production resolver successfully selected the explicit pins listed below.
- Wasmer provides `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USERNAME`, and `DB_PASSWORD`, but not `DATABASE_URL`. The attached database is PostgreSQL. The gateway's database layer prepares asyncpg URLs and maps documented `sslmode` input to asyncpg's supported TLS argument; the new `DB_*` fallback emits the driver-native `ssl=require` option.
- The active runtime configuration has no `REDIS_URL`. Previously Redis backed version-write locks and readiness checks. Redis is removed from this app's runtime dependencies and local Compose files; no separate Redis service/URL is required after this change.
- `TELEGRAM_BOT_TOKEN` is absent from the active runtime configuration. It cannot be safely generated. The Telegram storage/API integration and `/health/telegram` require the operator's actual Telegram bot token. Do not put it in this report or a Git commit; provide it only through a secure form before configuring it.
- Existing `JWT_SECRET` and `API_KEY_SALT` values were not read, changed, or rotated.

## Changes in the tested local checkpoint

1. **WASIX dependencies:** pinned `pydantic==2.13.4+wasix.2` and `pydantic-core==2.46.4+wasix.3` in the Wasmer root `requirements.txt`, and added the Wasix index. SQLAlchemy's asyncio extra is declared so `greenlet` is installed for async operation.
2. **Database configuration:** construct a safely URL-encoded `postgresql+asyncpg` URL from Wasmer's existing `DB_*` variables, require TLS (`ssl=require`), preserve explicit `DATABASE_URL` precedence, and report missing variable *names* without exposing values. No database credential is invented or changed.
3. **Redis-free concurrency:** replace Redis locks with PostgreSQL transaction-scoped advisory locks plus a row refresh/`FOR UPDATE`. Upload, restore, rollback, and SQL snapshot writes use the same lock protocol. The lock is released by transaction commit/rollback; each write rechecks the latest version after acquiring the lock.
4. **Actionable, secret-safe failures:** the root ASGI entrypoint returns JSON HTTP 503 startup diagnostics instead of letting import failures become opaque host HTML 500s. Runtime/startup and health diagnostics are redacted and bounded.
5. **Health behavior:** `/health/ready` checks actual PostgreSQL connectivity and advisory-lock support. `/health/telegram` returns a specific not-configured/invalid/unreachable status without exposing a token.
6. **Local configuration/tests:** removed Redis from root and gateway Compose, environment examples, and gateway dependency manifests. `render.yaml` remains unchanged as a separate deployment target; its legacy Redis declaration is not used by this Wasmer rollout.

## Local validation

- **Gateway tests:** `130 passed, 18 skipped`.
- **Focused Ruff:** all changed production modules and targeted regression tests pass.
- **Compile and whitespace:** `compileall` and `git diff --check` pass.
- **WASIX resolver:** the complete root `requirements.txt` resolves successfully for CPython 3.13/WASIX, including Pydantic Core's native wheel.
- **Compose syntax:** both edited Compose files parse as valid YAML; neither app configuration includes Redis.
- **Lock tests:** unit tests cover deterministic lock IDs, lock timeout/retry, and refreshing/locking the current database row. No local PostgreSQL server was available for a live advisory-lock integration test.
- **Repository-wide static checks:** the full gateway Ruff command still reports 98 findings, and mypy reports 147 errors across 13 existing modules. The focused changed-file Ruff check passes; there are no diagnostics in the new `postgres_lock.py` or `version_lock.py` modules. The unrelated repository-wide static-check backlog has not been changed as part of this fix.

## Live endpoint status (before the push)

| Route | HTTP status | Content type | Result |
| --- | ---: | --- | --- |
| `/` | 500 | `text/html; charset=utf-8` | Wasmer workload failure before the app starts |
| `/health` | 500 | `text/html; charset=utf-8` | Wasmer workload failure before the app starts |
| `/health/ready` | 500 | `text/html; charset=utf-8` | Wasmer workload failure before the app starts |
| `/health/telegram` | 500 | `text/html; charset=utf-8` | Wasmer workload failure before the app starts |

**No claim of restored production health is made.** Post-push deployment status and these four endpoints must be rechecked against the actual live service.

## Remaining blockers and next steps

1. Push the tested commit to `main` using a fast-forward-only update. This may trigger Wasmer's connected automatic deployment. Do not manually deploy or change other production settings.
2. Wait for the deployment, then probe `/`, `/health`, `/health/ready`, and `/health/telegram` and record their real HTTP statuses and response types here. Claim health only when the relevant live checks pass.
3. The database and Redis portions require no new operator-supplied secret if Wasmer's existing `DB_*` values remain available. To make Telegram storage and `/health/telegram` healthy, securely obtain the missing `TELEGRAM_BOT_TOKEN`, configure it in Wasmer without logging or committing it, and re-probe.

## Sources

- [Wasmer Python on Edge](https://wasmer.io/posts/python-on-the-edge-powered-by-webassembly)
- [Wasmer supported frameworks and languages](https://docs.wasmer.io/edge/learn/supported-frameworks-and-languages/)
- [Wasmer managed databases](https://docs.wasmer.io/edge/learn/databases/)
- [WASIX Python installation guidance](https://wasix.org/docs/language-guide/python/installation/)
- [WASIX Pydantic Core package index](https://python-registry.wasmer.app/all/simple/pydantic-core/)
- [WASIX Pydantic package index](https://python-registry.wasmer.app/all/simple/pydantic/)
