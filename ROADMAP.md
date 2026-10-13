# Roadmap — Alien Cloud (BRIEF.md Phases 2–3)

*Written 12 Oct 2026. Plugin Phase 1 is done (see README). This covers going online: Cloudflare deploy, R2 storage,
accounts (Google + GitHub), the shared library, hosted models with credits, and Stripe (test mode). Mock business:
no real sales; Stripe never leaves test mode.*

## Shape

One Cloudflare Worker serves the API and the website (static assets), the same pattern as `../street-scheme`
(Hono, `@cloudflare/vite-plugin`, React, D1, KV, R2, rate-limit bindings). Copy that skeleton rather than starting
blank; `street-scheme/worker/lib/stripe.ts` (Stripe over plain fetch, webhook signature checks) is reusable as is.

```
alien-cloud/                 new repo or folder next to Alien-main
  worker/index.ts            Hono app: /api/*, /auth/*, /webhooks/stripe
  worker/lib/                auth, tokens, credits, library, openrouter, stripe (from street-scheme), moderation
  migrations/                D1 SQL
  src/                       website: landing + pricing, gallery, node page, account, admin review queue
  wrangler.jsonc
```

| Binding | Use |
|---|---|
| D1 `DB` | users, sessions, tokens, nodes, credits ledger, Stripe state |
| R2 `LIBRARY` | node scripts (every version), thumbnails, later generated assets (textures, objects, drawings) |
| KV `CACHE` | OAuth state, short-lived plugin sign-in codes, gallery cache |
| Rate limits | per user for `/api/ai/*`, per IP for `/auth/*` and anonymous reads |
| Secrets | `OPENROUTER_KEY`, `GOOGLE_CLIENT_ID/SECRET`, `GITHUB_CLIENT_ID/SECRET`, `STRIPE_SECRET_KEY` (sk_test), `STRIPE_WEBHOOK_SECRET`, `SESSION_SECRET` |

## Accounts to register (Nick)

1. **Domain** on Cloudflare Registrar (~US$15), plus Workers (free tier is enough; Paid US$5 if limits bite).
2. **OpenRouter:** top up US$50, create a key *only for the Worker*, and set a monthly spend limit on it.
   Turn on the privacy setting that excludes providers that store or train on prompts (the plugin already does this per request).
3. **Google Cloud:** OAuth client (web), scopes `openid email profile` only. Redirect `https://<domain>/auth/google/callback`
   (plus `http://localhost:5180/...` for dev). In "Testing" mode only listed test users can sign in: add classmates,
   or publish the consent screen (basic scopes shouldn't need Google's review — check when you do it).
4. **GitHub:** OAuth App, callback `https://<domain>/auth/github/callback` (and a second app for localhost).
5. **Stripe:** account in test mode (no activation needed). Product "Alien Pro", price US$20/month. Webhook endpoint
   `https://<domain>/webhooks/stripe`; use the Stripe CLI (`stripe listen --forward-to`) locally.

## D1 schema (first migration)

| Table | Key columns |
|---|---|
| `users` | id, email, name, avatar, plan (`free`/`pro`), plan_until, credits (integer), stripe_customer_id, is_admin |
| `oauth_accounts` | provider (`google`/`github`), provider_user_id, user_id — one user can link both |
| `sessions` | id (random, stored hashed), user_id, expires_at — web sessions, HttpOnly cookie |
| `api_tokens` | id, user_id, hash, label ("Alien on Nick's Mac"), last_used, revoked_at — the plugin's credential |
| `nodes` | id, owner_id, slug, name, description, category, language, visibility (`public`/`private`), status (`pending`/`approved`/`rejected`), latest_version, installs, flags (JSON) |
| `node_versions` | node_id, version, r2_key, sha256, header (JSON pins), created_at |
| `credit_ledger` | id, user_id, delta, reason (`monthly_grant`/`ai_call`/`topup`/`refund`), model, cost_usd, request_id, created_at |
| `stripe_events` | event_id (PK), type, processed_at — makes webhooks idempotent |

R2 keys: `nodes/<nodeId>/<version>/<file>.py|.cs`, `nodes/<nodeId>/thumb.png`, later `assets/<assetId>/...`.

---

## Stage 1 — Deploy a skeleton (½ day)

- Scaffold from street-scheme; `wrangler d1 create`, `wrangler r2 bucket create`, KV, first migration.
- Deploy to the domain: landing page (one-liner, the 30 s demo video slot, pricing table from BRIEF) and `/api/health`.
- Seed the library: upload the `suite/` nodes as approved public nodes owned by an "Alien" account (a script in `tools/`).
- **Done when:** `https://<domain>` loads, and `GET /api/nodes` lists the suite from D1 + R2.

## Stage 2 — Accounts: web sign-in (½–1 day)

- Google and GitHub OAuth with PKCE and a `state` check. Use a small OAuth client library (e.g. Arctic) and our own
  `sessions` table — a few hundred lines we fully understand. (Alternative: Better Auth on D1; more features, more to debug.)
- Same email from both providers → one user (link `oauth_accounts`).
- Account page: plan, credits, tokens (with revoke), sign out.
- **Done when:** sign in with Google, sign out, sign in with GitHub on the same email, land on the same account.

## Stage 3 — Plugin sign-in (½ day)

The plugin already runs an HTTP server on `127.0.0.1:9876`, so use the loopback redirect pattern (RFC 8252):

1. Panel → **Sign in** opens the browser at `https://<domain>/auth/plugin?port=9876&state=<random>&challenge=<pkce>`.
2. The user signs in (Stage 2), approves "Alien on this computer".
3. The site redirects to `http://127.0.0.1:9876/auth/callback?code=<one-time>&state=...`.
4. The plugin checks `state`, swaps the code (+ PKCE verifier) at `POST /api/tokens/exchange` for an API token.
5. The token goes into settings.json (`"alienToken"`, owner-only file, never in `.gh` or scripts); the panel shows the account and credits.

- New MCP-server route `/auth/callback`: GET only, valid only while a sign-in is pending, then closes.
- **Done when:** sign in from the panel, quit and reopen Rhino, still signed in; revoke the token on the website and the panel shows signed out.

## Stage 4 — The library online (1 day)

- **API:** `GET /api/nodes?q=&category=` · `GET /api/nodes/:id` (metadata, code, versions) · `GET /api/nodes/:id/download` ·
  `POST /api/nodes` (publish; token auth) · `POST /api/nodes/:id/versions`.
- **Publish (panel):** "Publish" on the selected node → name, description, category from the header → upload → status `pending`.
- **Review:** an admin page lists pending nodes with code and automatic flags; approve or reject. Flags (regex, not a
  sandbox): `subprocess`, `os.system`, `eval`/`exec`, `socket`, `ctypes`, file deletes, reading Alien's settings path,
  network calls to hosts outside an allow-list. Flags show on the node page and in the install dialog.
- **Install (panel):** Library tab gets an **Online** section; install = show code + flags → confirm → download into
  `<script folder>/library/<slug>.py` → place the node. Record the version in `.alien/library.json` and offer updates.
- **Gallery (website):** browse, search, categories, node page with code and pins, "copy install link"
  (`alien://install/<id>` later; for now the panel searches by id).
- Private nodes (Pro): `visibility = private`, only the owner sees or downloads them.
- **Done when:** publish on one machine → approve → install on another → the node works.

## Stage 5 — Hosted models and credits (1 day)

- **Credit unit:** 1 credit = US$0.001 of model cost, so one describe-a-node build ≈ 1 credit, an image ≈ 34, an
  object_3d build ≈ 10–20. Pro grants 10,000 credits a month (US$10 of model spend on a US$20 plan).
  *This replaces the BRIEF's "1,000 credits" — same money, friendlier numbers.* Free tier: decide between 0 and a small
  monthly grant (e.g. 200); bring-your-own key stays unlimited either way.
- **Proxy:** `POST /api/ai/chat` and `POST /api/ai/images`, OpenAI-compatible, forwarded to OpenRouter with the
  Worker's key. Allow-list of models; reject anything else.
  1. Check `credits > 0` (and the model's minimum).
  2. Forward, with OpenRouter usage accounting on so the response carries the real cost.
  3. Deduct `ceil(cost_usd / 0.001)` in one statement (`UPDATE users SET credits = credits - ? WHERE id = ?`) and write a
     ledger row. A call may take the balance slightly negative; the next one is refused.
- **Plugin:** a provider setting `"provider": "alien" | "openrouter" | "ollama"`. With `alien`, `ModelClient` calls the
  Worker with the token; the panel shows the balance and the cost per request (it already records cost).
- **Keep tokens away from scripts:** add `POST 127.0.0.1:9876/ai/chat|images` on the plugin's local server. Suite helpers
  (`alien_ai.py`, `alien_graphics`) call that instead of reading `openrouterKey`/`alienToken` from settings. Scripts can
  still read settings.json from disk, so the honest position for the pitch: tokens are revocable, rate-limited and
  capped by credits; OS keychain storage is the next step.
- **Done when:** with no OpenRouter key on the machine, describe-a-node and `material_texture` work through the
  account, and the ledger matches OpenRouter's activity page.

## Stage 6 — Stripe, test mode (½–1 day)

- **Upgrade:** pricing page or panel → `POST /api/billing/checkout` → Stripe Checkout (`mode=subscription`, the Pro
  price, `client_reference_id = user id`, existing customer if any) → redirect back to the account page.
- **Webhooks** (`/webhooks/stripe`, signature checked on the raw body first, event id stored for idempotency):
  - `checkout.session.completed` → save `stripe_customer_id`
  - `invoice.paid` → `plan = pro`, `plan_until = period end`, grant the monthly credits (ledger `monthly_grant`)
  - `customer.subscription.updated` / `.deleted` → follow the status; downgrade at period end
- **Manage:** "Manage billing" → Stripe Customer Portal (cancel, card).
- Optional: one-off credit top-ups (`mode=payment`) for users who run out.
- **Done when:** sign up → pay with test card 4242 4242 4242 4242 → plan shows Pro and credits arrive within seconds →
  cancel in the portal → downgrade at period end (check with Stripe's test clocks).

## Stage 7 — Pitch polish (½ day)

- Landing page: demo video, the comparison (Planaria, Raven, Ant, Smarthopper, Runchat), tiers, sign-up.
- "Research prototype — test mode, no real charges" banner across the site and on the Stripe page.
- Terms and privacy pages (short, plain: what goes to models, what's stored, code runs as you).
- Admin numbers for the deck: users, nodes published, installs, credits spent.

---

## Order, and what to cut if time runs short

Assuming about four working days before the pitch:

| Day | Stages |
|---|---|
| 1 | 1 (skeleton + deploy) and 2 (web sign-in, Google first) |
| 2 | 3 (plugin sign-in) and 4 (library) |
| 3 | 5 (hosted models + credits) |
| 4 | 6 (Stripe), 7 (polish), record the demo |

Cut in this order: GitHub login (Google alone is enough) → Customer Portal (cancel by test clock instead) → private nodes →
update notifications → asset publishing → hosted MCP (stays a roadmap slide). Don't cut: review before install,
signature-checked webhooks, the spend limit on the OpenRouter key.

## Risks

- **Model spend:** a bug in the ledger could spend real OpenRouter credit. The key's spend limit is the backstop; set it low.
- **OAuth redirect mismatches** eat hours: register every redirect URI (prod and localhost) up front.
- **Webhooks arriving out of order or twice:** idempotency table, and always re-read the subscription from Stripe rather than trusting event order.
- **Shared code is a security problem:** review + flags + code shown before install is the minimum; say so in the pitch.
- **Windows** is still untested (build, sidecar, controls). Test it before claiming cross-platform.

## Open decisions

- Name and brand (the domain depends on it).
- Free tier: no hosted credits, or a small monthly grant?
- Credit unit change (10,000 = US$10) — update BRIEF.md if agreed.
