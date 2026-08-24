---
name: omniroute-endpoint-agent
description: Use a developer-provided deployed OmniRoute Vercel endpoint as an OpenAI-compatible AI gateway. Start when an agent receives an OmniRoute domain or endpoint and needs to discover models, select a model, send chat or other AI requests, verify availability, or use the gateway’s configured Parad persistence without performing infrastructure setup.
---

# OmniRoute Endpoint Agent

Use this skill when the developer has already deployed and configured OmniRoute and gives the agent its endpoint or domain. Treat the deployment as ready to use. The agent’s job is to call the gateway, discover available models, complete AI tasks, and report useful results clearly.

## Operating principle

> The developer supplies one deployed OmniRoute endpoint. The agent uses it immediately; the developer-owned Vercel, Parad, database, provider, and secret configuration stays outside the agent workflow.

Do not begin by asking for Vercel login, Parad login, a database URL, a database passphrase, provider credentials, environment-variable setup, deployment commands, or project creation. Do not reconfigure infrastructure during ordinary AI work. Use the host application’s existing authenticated client or runtime secret when authentication is already provided by the integration.

## Input contract

Accept either a full endpoint or a bare deployment domain:

```text
https://your-deployed-domain.example
https://your-deployed-domain.example/api/v1
```

Store the developer-provided value as `OMNIROUTE_ENDPOINT`. Normalize it by removing a trailing slash. If it already ends in `/api/v1`, use it as the API base. Otherwise append `/api/v1`.

```text
OMNIROUTE_ENDPOINT=https://your-deployed-domain.example
OMNIROUTE_API_BASE=https://your-deployed-domain.example/api/v1
```

Treat the endpoint as a routing location, not as a credential. Never place API keys, database URLs, provider keys, or other secrets in source code, URLs shown in logs, public documentation, or chat responses.

## First-use workflow

Perform these steps in order whenever a new endpoint is supplied:

1. Normalize the endpoint into `OMNIROUTE_API_BASE`.
2. Use the existing authenticated gateway client supplied by the host. Do not create a second configuration system.
3. Request `GET {OMNIROUTE_API_BASE}/models` to discover the live model catalog.
4. For a normal AI request, use the requested model when it is present in the catalog; otherwise use `auto` so the gateway can select an available model.
5. Send the request to `POST {OMNIROUTE_API_BASE}/chat/completions`.
6. Read the assistant response and record the selected model or provider headers when available.
7. Return the useful result to the user, not internal credentials or infrastructure details.

A minimal OpenAI-compatible request is:

```json
{
  "model": "auto",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

A minimal JavaScript client is:

```ts
import OpenAI from "openai";

const client = new OpenAI({
  baseURL: `${OMNIROUTE_ENDPOINT.replace(/\/$/, "")}/api/v1`,
  apiKey: process.env.OMNIROUTE_GATEWAY_KEY,
});

const result = await client.chat.completions.create({
  model: "auto",
  messages: [{ role: "user", content: "Hello" }],
});
```

The client key is consumed from the host’s existing secret configuration. Do not ask the agent user to create or paste a key when the host integration already provides one.

## Model selection

Discover the catalog dynamically instead of maintaining a hard-coded list. Use the following behavior:

| Request | Action |
| --- | --- |
| The user names a model and it appears in `/models` | Send the exact model ID. |
| The user asks for automatic selection or names no model | Send `model: "auto"`. |
| The user asks for Big Pickle | Prefer the live catalog’s `big-pickle` ID. |
| A requested model is unavailable | Choose `auto`, then explain which live model was selected. |
| A model request fails while other catalog models exist | Retry once with `auto` or the next available catalog model, then report the selected fallback. |

For Big Pickle, prefer the short model ID `big-pickle` when the catalog exposes it. The provider-qualified spelling may not be accepted by every deployment even when the short ID works.

Always preserve the user’s requested task, temperature, token limits, streaming preference, tools, and response format when retrying. Change only the model selection required to obtain a working route.

## Metadata-first selection

Treat the live `/models` response as the source of truth for model selection. When metadata fields are present, use them in this order:

1. Filter by `modality` and the requested endpoint capability before comparing model families.
2. Prefer `confidence: "high"` or `"medium"` classifications over `"low"` for automatic selection.
3. Prefer lower-numbered `priority` bands such as `P1-curated-free` and `P2-curated-gateway` before broad community or experimental bands.
4. Use `family` and `task_role` to match requests such as coding, reasoning, vision, image creation, audio, embeddings, or safety.
5. Treat `quality_tier` as a routing hint, not a benchmark claim, and keep the exact provider-qualified `id` unchanged.
6. If metadata is absent, remain backward compatible: use the exact requested ID when listed, otherwise use `auto` for chat.

The metadata API may include `family`, `modality`, `task_role`, `quality_tier`, `priority`, `confidence`, and `taxonomy_source` in addition to the standard model fields. These fields describe the current catalog classification; they do not guarantee that every provider endpoint will accept every operation. Probe specialized routes before production automation.

## Common operations

Use the OpenAI-compatible paths below against `OMNIROUTE_API_BASE`:

| Operation | Method and path | Default behavior |
| --- | --- | --- |
| List live models | `GET /models` | Discover before selecting. |
| Chat | `POST /chat/completions` | Use the requested model or `auto`. |
| Text completion | `POST /completions` | Preserve the caller’s prompt and options. |
| Responses | `POST /responses` | Use when the host client requests the Responses API. |
| Embeddings | `POST /embeddings` | Use the requested embedding model from the catalog. |
| Moderation | `POST /moderations` | Forward the moderation request unchanged. |
| Messages-compatible requests | `POST /messages` | Preserve the caller’s message format. |

The deployment may expose additional AI routes. Discover the model catalog first and follow the endpoint contract already used by the calling client. Keep all provider routing behind OmniRoute; do not call individual providers directly unless the developer explicitly changes the integration.

## Persistence behavior

Parad persistence is already configured by the developer. The agent should treat persistence as an available service behind OmniRoute and should not manage its database.

Do not run database initialization, snapshot creation, migrations, deletion, project creation, URL registration, Vercel environment updates, or deployment commands as part of a normal AI request. Do not request or display the Parad `DATABASE_URL`. The endpoint is the only deployment input needed by this skill.

When an application operation needs durable state, use the application’s existing OmniRoute or project API. Let the deployed service use its configured Parad connection. If the application exposes a documented persistence route, call that route through the same deployed domain rather than opening a direct database connection.

## Verification workflow

After receiving an endpoint, run a small verification before a larger task:

1. `GET /models` must return a successful model list.
2. Send one short `chat/completions` request with `model: "auto"`.
3. Confirm that the response contains an assistant message.
4. Record the selected model from the response or `x-omniroute-model` header when available.
5. Continue with the user’s real request.

For a model-specific request, repeat the short test with the requested model. Report success in terms of HTTP status, selected model/provider, response availability, and user-relevant output. Do not report API keys, database URLs, passphrases, provider credentials, cookies, or authorization headers.

## Request examples

### Automatic model selection

```bash
curl -sS "$OMNIROUTE_API_BASE/chat/completions" \
  -H "Authorization: Bearer $OMNIROUTE_GATEWAY_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"auto","messages":[{"role":"user","content":"Say hello."}]}'
```

### Big Pickle

```json
{
  "model": "big-pickle",
  "messages": [
    {"role": "user", "content": "Say hello in one short sentence."}
  ]
}
```

### Dynamic catalog selection in pseudocode

```text
models = GET /models
available_ids = ids(models)
requested = user_model_or_auto
model = requested if requested == "auto" or requested in available_ids else "auto"
result = POST /chat/completions with model and the user request
```

## Error handling

Handle errors in a direct, useful way:

| Situation | Agent action |
| --- | --- |
| Endpoint is missing | Ask the developer for the deployed OmniRoute domain only. |
| Models request succeeds | Continue immediately. |
| Models request returns an authorization error | Reuse the host’s configured authenticated client and report that its existing gateway authorization is unavailable. Do not request unrelated provider or database credentials. |
| Chat request times out | Retry once with a concise request; then use `auto` if a specific model was selected. |
| Requested model is absent | Use `auto` and report the live selected model. |
| Gateway returns a structured error | Preserve its safe message and give the next task-oriented action. |
| Gateway is unavailable | Report the endpoint availability issue and pause the AI task; do not change deployment configuration automatically. |

Keep troubleshooting proportional. A successful `/models` call and one successful short completion are enough to establish that the endpoint is ready for normal work.

## Agent response style

Begin work immediately after receiving the endpoint. State the selected model when useful, complete the user’s AI request, and give a concise result. Use the deployed gateway as the single AI interface and let its configured routing, provider selection, quotas, and Parad persistence work in the background.

When reporting a test, use a compact format such as:

```text
OmniRoute is ready.
Endpoint: the developer-provided deployment
Model: the live selected model
Result: the completed response
```

Do not reveal secrets or reproduce authorization headers in any response, log, code sample, issue, commit, or public document.
