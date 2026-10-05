# ENVX sign-in through Nexuss Auth

Paradox's new browser sign-in uses **ENVX as the OIDC identity provider**, with Nexuss Auth validating the identity before Paradox creates its local session. Google and GitHub remain available in Nexuss Auth for other projects; the Paradox project must be configured to require ENVX. Paradox does not receive the ENVX password, Supabase client secret, or Google/GitHub provider credentials.

## Browser entry and callbacks

The browser entry point is:

```text
https://paradox-db.wasmer.app/v1/auth/nexuss/login
```

It is default-off. Once the Paradox feature flag is enabled and configuration is complete, the endpoint starts a Nexuss Auth authorization-code flow with PKCE, state, nonce, and the fixed form-post callback below. It does not derive redirects from the request `Host` header.

| Registration/configuration | Exact URI or value |
| --- | --- |
| Supabase OAuth Server consent screen URL | `https://envx-lemon.vercel.app/oauth/consent` |
| ENVX email-confirmation redirect | `https://envx-lemon.vercel.app/auth/callback` |
| Supabase OAuth client redirect URI for Nexuss Auth | `https://nexuss-auth.vercel.app/oauth/callback` |
| Nexuss Auth project allowed redirect URI for Paradox | `https://paradox-db.wasmer.app/v1/auth/nexuss/callback` |
| Nexuss Auth project allowed origin | `https://paradox-db.wasmer.app` |
| ENVX OIDC issuer | `https://<existing-supabase-project-ref>.supabase.co/auth/v1` (or that existing project's configured Auth domain) |

Supabase OAuth Server must be enabled on the user's **existing** Supabase project and an OIDC client must be registered for Nexuss Auth. Do not invent a project reference or client credentials, create a paid project, or change auth-provider settings before the user enables the pending Supabase connector/configuration approval. ENVX's `/oauth/consent` route uses Supabase's OAuth authorization-detail/approve/deny APIs; it does not implement a custom token issuer.

## Required deployment configuration

Configure these on the existing deployments only after the project/client records have been inspected and approved. Keep values in the deployment secret manager; never commit them.

**Nexuss Auth (Vercel):**

- `DATABASE_URL` — canonical secret-bearing `parad://<API_KEY>@local/<PROJECT>/<DATABASE>?passphrase=<URL_ENCODED_PASSPHRASE>&gateway=https://paradox-db.wasmer.app/v1` URL for the existing Paradox-backed auth database; it is **not** a raw PostgreSQL URL and belongs only in Vercel's encrypted environment settings. The current production API was previously blocked without this canonical value.
- `ENVX_OIDC_ISSUER_URL`, `ENVX_OIDC_CLIENT_ID`, `ENVX_OIDC_CLIENT_SECRET` — obtained from the existing Supabase project and its OAuth-client registration.
- Keep existing `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GITHUB_CLIENT_ID`, and `GITHUB_CLIENT_SECRET` intact.

**Paradox (Wasmer):**

- `NEXUSS_AUTH_URL=https://nexuss-auth.vercel.app`
- `NEXUSS_AUTH_PROJECT_ID=<the existing Paradox project id>`
- `PARADOX_AUTH_CALLBACK_URL=https://paradox-db.wasmer.app/v1/auth/nexuss/callback`
- `ENVX_OIDC_ISSUER_URL=<exact issuer configured in Nexuss Auth>`
- `PARADOX_ENVX_ONLY_AUTH_ENABLED=true` only after the readiness checks below pass; the checked-in default remains `false`.

**ENVX (existing Vercel project):**

- `NEXT_PUBLIC_SUPABASE_URL` and `NEXT_PUBLIC_SUPABASE_ANON_KEY`
- `ENVX_ENCRYPTION_KEY` (server-only)
- Supabase Auth OAuth Server consent URL set to `https://envx-lemon.vercel.app/oauth/consent`

The current ENVX Vercel project was observed with no environment variables configured, so its deployed setup-required page cannot currently serve Auth or consent requests.

## Staged behavior and compatibility

- `/v1/auth/register` and `/v1/auth/login` return `410`; no new local-password account path is enabled.
- `/v1/auth/nexuss/login` is the ENVX browser entry. Callback state is cookie-bound and checked before a one-time server-to-server handoff exchange. The handoff token is not put in a redirect URL; Paradox sets an HttpOnly, Secure, SameSite=Lax `pk_` cookie and redirects to `/v1/auth/me`.
- While `PARADOX_ENVX_ONLY_AUTH_ENABLED=false`, existing direct `nxa_` API requests keep their previous validation path; they are **not** evidence of an ENVX browser login. When the flag is enabled, direct `nxa_` access and new handoff/exchange flows require verified ENVX provenance, the configured issuer, the configured Paradox project, a stable OIDC subject, and `project:<project-id>:access` permission.
- Existing local `pk_` credentials remain valid in either mode. Before enabling strict cutover, inventory existing `nxa_` tokens: legacy tokens that lack the required ENVX/project claims will be rejected, and must be deliberately reissued or migrated. The flag is default-off and no key is revoked by this code while it remains off.
- Paradox never silently attaches an ENVX identity to a password-era account just because the email matches. Such a collision returns `409` and needs a separate, authenticated identity-linking operation. That operation is not implemented in this change; do not replace it with automatic email linking.
- The applications currently model user-owned projects/resources rather than a global `superuser` role. No cross-app admin privilege is inferred from the email address. ENVX owner onboarding is through the existing signup and email-confirmation flow; any later owner/admin designation must be a server-controlled role bound to the immutable Supabase user id/subject.

## Readiness sequence

1. Approve the pending Supabase connector/configuration card and inspect the existing project; do not create a project or change providers.
2. Configure the existing ENVX Vercel project and Supabase OAuth Server consent screen; create the ENVX owner account through `/signup` and verify its email using the normal callback.
3. Register the Nexuss Auth OIDC client on that same Supabase project with the exact callback above; populate the three `ENVX_OIDC_*` values and `DATABASE_URL` on the existing Nexuss Auth deployment.
4. Sign in to Nexuss Auth with ENVX and configure the existing Paradox project to allow only `envx`, require `envx`, and use the exact Paradox redirect URI/origin.
5. Apply the checked-in Paradox database migration and configure Wasmer's routing values. Inventory existing users and both `pk_` and `nxa_` credentials before enabling the flag; verify each legacy key's owner and planned migration.
6. Run the OIDC, callback, and token-scope smoke tests against the configured deployments, then enable `PARADOX_ENVX_ONLY_AUTH_ENABLED`. Keep legacy `pk_` access until a separate migration decision is approved.

No production deployment, database migration, user creation, provider change, or Wasmer secret change is performed by the current code-only work.
