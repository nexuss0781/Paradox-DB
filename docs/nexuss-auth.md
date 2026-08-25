# Nexuss Auth integration

Paradox now uses **Nexuss Auth** as its interactive identity provider. Google remains enabled through the Nexuss project configuration; Paradox does not receive Google client secrets or provider credentials.

Configure the gateway with the non-secret routing values below. The Nexuss project must be active, have Google enabled, and contain exact Paradox web callback and origin entries before a browser sign-in flow is enabled.

```text
NEXUSS_AUTH_URL=https://nexuss-auth.vercel.app
NEXUSS_AUTH_PROJECT_ID=your-paradox-project-id
```

For CLI and SDK use, create a Nexuss project token after signing in with Google, then exchange it once through Paradox. The exchange returns a Paradox `pk_` key, which is the value stored in `PARADOX_API_KEY` or local Paradox configuration. The original `nxa_` credential is neither stored nor logged by Paradox.

```bash
parad auth login --api-key nxa_example
export PARADOX_API_KEY=pk_example
```

An `nxa_` key is also accepted directly in the gateway `X-API-Key` header for trusted server-to-server use. Each request is checked with Nexuss Auth against the configured project. For browser applications, use the Nexuss one-time server handoff model: begin Google sign-in through Nexuss with `handoff=1`, receive the callback on a trusted Paradox server route, and send the one-time token to `POST /v1/auth/nexuss/handoff`. Do not send `nxa_` keys or handoff tokens to browser code, logs, source control, or URLs.

Paradox does not automatically attach a Nexuss identity to a pre-existing password-era account with the same email. That case returns a conflict and requires an explicit account-linking migration, preventing silent account takeover.
