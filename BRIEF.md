# Brief — Alien as a product

*Working name TBC. Uni mini-MBA pitch, one week. A mock business: no real sales, Stripe stays in test mode.*

## The product in one line

A Grasshopper node you describe in plain English, which writes and fixes its own code, plus a shared library of
nodes anyone can install in one click. Built on Alien.

**Pitch order:** the library and blank-slate node first (make your own, share it, fork others'); describe-a-node
second; the preloaded suite (map node first) as proof it works on day one.

## Tiers

| | Free | Pro (US$20/month) |
|---|---|---|
| Alien node + public library | Yes | Yes |
| Preloaded suite (incl. map node) | Yes | Yes |
| Local MCP (Claude Code, Cursor, etc.) | Yes | Yes |
| Describe-a-node, own OpenRouter key or local model (Ollama) | Unlimited | Unlimited |
| Hosted builds via our Worker | TBC: none, or ~20/month on the cheapest model | 1,000 credits/month, all models, weighted by cost |
| Private nodes + version history | No | Yes |
| Hosted library MCP | No | Yes |

Roadmap only (slides, not built): Dynamo version, studio tier (private library for a practice, per seat), student price.

## Stack

- **Plugin:** Alien (C#, net7.0, Rhino 8, Windows + macOS). Script files with `NODE_INPUTS` / `NODE_OUTPUTS` headers; local MCP on `127.0.0.1:9876`.
- **Cloud:** a Cloudflare Worker (auth, credits, model routing) · R2 storage (node registry) · D1 database (users, credits, node metadata) · Cloudflare Pages (gallery + landing page) · AI Gateway (optional: logs, caching, rate limits).
- **Models:** OpenRouter (one OpenAI-compatible API). Default cheap model (e.g. DeepSeek Flash), mid (Gemini Flash), premium (a frontier model, 5–10× credits). Exclude providers that train on prompts.
- **Data (map node):** Vicmap (free) + OpenStreetMap. Reuse `ArchiSkills/skills/project-card/scripts/vic_site_lookup.py` (stdlib only).
- **Accounts/payments:** Google sign-in, Stripe in test mode.

---

## Phase 1 — Upgrade the Alien node (local only, no cloud)

### 1a. Node design clean-up
- Tidy the on-canvas look (`AlienAttributes.cs`): clear title (script name), language badge (Py/C#), run state (ok / error / running), and the error message on the node itself, not only in the log.
- Input dots stay: green = wired, blue = manual value, grey = default.
- Right-click menu: group into sections (Script · Describe · Share · Settings); add **Describe…**.
- **Done when:** a new user can tell from the canvas alone what a node is, which language it runs, and whether it failed and why.

### 1b. Describe-a-node (API calling, in the node)
- A small chat or prompt panel (Eto) opened from the node: describe → generate → run → fix.
- Providers behind one interface: **OpenRouter key** (default) and **Ollama** (local). Keys go in `settings.json`, never in `.gh` files or scripts.
- **Retry loop:** generate the script with its header → write the file → Alien rebuilds and solves → on error, send the code plus the error (Roslyn diagnostics for C#, the traceback for Python) back to the model → retry, max 3–4 times → stop and show the last error.
- Prompt context: `docs/AGENT_GUIDE.md` (header syntax), the current script, the wired input types, and short RhinoCommon notes. Optional: check RhinoCommon member names by reflection before running, to catch invented API calls.
- Follow-ups edit the existing script ("make the grid hexagonal") rather than starting again.
- **Done when:** 8 out of 10 test prompts (list below) produce a working node within 4 attempts on the cheap default model. **Test this first.** If the cheap model fails, change the default model or the pitch before building more.

### 1c. Build the preloaded suite with Claude Code via MCP
- Use Claude Code through Alien's MCP to build nodes. This also stress-tests the MCP tools.
- **Hero: map node.** Address → lot boundary, neighbouring lots, footprints (extruded where height is known), roads with names, zone and overlays as data. Port `vic_site_lookup.py`.
- Two or three utilities: smart tree merge/split, quick slider/input maker, and one "convoluted thing in one click".
- Each node: a clear header, sensible defaults, a one-line description comment (this becomes its library listing).
- **Done when:** the suite works on a fresh machine with a fresh script repository.

**Test prompts (draft):** divide a curve into N points · a voronoi panel facade on a surface · flatten and regroup a tree by branch index · contour a mesh at intervals · offset a closed curve inwards N times · random-colour breps by area · number panels for fabrication · attractor-point scaling of circles · bounding-box dimensions as text · loft between sorted curves.

---

## Phase 2 — Web scaffold (needs accounts: Cloudflare, OpenRouter, domain)

- **Worker API:** `POST /v1/generate` (proxies OpenRouter, deducts credits from the reported cost) · `GET/POST /v1/nodes` (list / publish) · `GET /v1/nodes/:id` (download a script).
- **Registry:** a script file plus a metadata JSON in R2; a D1 table for search (name, description, tags, author, version, language, downloads).
- **Plugin side:** a "hosted" provider option (calls the Worker instead of OpenRouter); right-click **Publish** and **Install from library** (downloads into the script repository).
- **Gallery (Pages):** browse, search, view code, copy an install link.
- **Safety, minimum:** code is always visible before install; a "reviewed" badge set by hand; publishers tied to accounts.
- **Done when:** publish on one machine → install on another → the node works.

## Phase 3 — Deploy, accounts, credits, payments

- Google sign-in (web) → an API token for the plugin (paste once, or a device-code flow).
- Credits ledger in D1 (monthly grant, deduction per call, free-tier allowance if we choose one).
- Stripe test mode: Pro subscription checkout, webhook → set the plan and grant credits.
- Private nodes (Pro): a visibility flag; the Worker checks ownership.
- Landing page: the one-liner, a 30-second demo video, tiers, sign-up.
- **Done when:** sign up → pay with Stripe's test card → hosted builds and private nodes unlock.

---

## Pitch checklist (in parallel with Phase 3)

- 5–10 classmates try it; collect quotes and pain points.
- Competitors: Planaria (US$29/month), Raven (US$49/month), Ant, Smarthopper, Runchat. Our difference: shareable plain-file nodes plus a library.
- Unit economics: about 1c per cheap build; budget US$8–10 model spend per Pro user per month; OpenRouter fee 5.5%.
- Market size: estimate from Food4Rhino download counts, labelled as an estimate.
- Risks: a thin moat (answer: the network and open files), dependence on OpenRouter (the API is standard, so we can switch), running shared code (shown before install, review badge).
- Pre-record the demo; live is a bonus.

## Budget
About US$100 generous: domain $15, Cloudflare $5, OpenRouter $50 + fee, buffer.

## Open questions
- Name and brand.
- Free tier: hosted taste (~20 builds) or bring-your-own-key only?
- macOS build paths in the csproj are unverified. Check on the Mac first.
- MCP over the network for Pro: what exactly is exposed?
