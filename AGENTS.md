# Alien: context for coding agents

Read this first. Then read [README.md](README.md) (what it does), [BRIEF.md](BRIEF.md) (the product plan) and, before
touching C#, [docs/DEV_NOTES.md](docs/DEV_NOTES.md) (hard-won facts).

## What this is

- **The plugin:** a Grasshopper (Rhino 8) plugin, `Alien.gha` (C#, net7.0, Windows and macOS). An **Alien node** runs
  an external Python 3 or C# script; its pins come from the script's `NODE_INPUTS` / `NODE_OUTPUTS` header comments.
- **Agents:** an in-process MCP server (`127.0.0.1:9876/mcp`) lets agents build, wire and edit nodes live.
- **The product:** "Alien" as a mock product for a uni mini-MBA pitch.
  - Describe-a-node: a model writes a node's script, runs it and fixes its own errors.
  - Nodes that adapt their UI to the script.
  - A preloaded suite.
  - Later, a shared online library.
- **Status (Oct 2026):** Phase 1 of BRIEF.md is done (node redesign, describe-a-node, the suite) plus the UI work
  below. Phases 2–3 (Cloudflare library, accounts, Stripe test mode) are not started.

## Architecture

```
src/
  AlienComponent.cs     the node: script binding, solve, pin rebuild, manual values, persistence, menu, StartDescribe
  AlienAttributes.cs    the node's look and mouse handling: header, status row, controls, error strip, selection glow
  ScriptRepository.cs   settings.json (re-read when changed; saved owner-only) and the user's script folder
  Suite.cs              suite/*.py are embedded in the .gha and copied to <script folder>/suite on first panel open
  AlienUi.cs            dialogs and shell helpers (editor, reveal, settings file)
  Scripting/            HeaderParser (header grammar -> ParamDef), ScriptRunner (RhinoCode), TypeMap, Values
  Ai/                   ModelClient (OpenRouter / Ollama, one OpenAI-style client), Generator (the retry loop),
                        Prompts (system prompt = rules + AGENT_GUIDE sections), Transcript (per-node request history)
  Ui/                   AlienPanel (Eto WebView showing pane.html), AlienSidecar (panel window glued to the GH editor),
                        NodeControls (control kind, recommended ranges), InlineEditor (text box on the canvas), pane.html
  Mcp/                  McpServer (hand-rolled Streamable HTTP, JSON only), Tools*.cs (the tool set), Describe (compact
                        run reports), UiThread (all Rhino/GH calls marshalled to the UI thread, tool calls serialised)
suite/                  the preloaded nodes (Python) + alien_graphics.py (shared helper for the two graphics nodes)
docs/AGENT_GUIDE.md     agent reference: served by the MCP `docs` tool AND embedded in describe-a-node's prompt
tests/describe/         the describe-a-node test harness (suite.py) + results
tools/                  drive a running Rhino from a shell (see tools/README.md)
```

### Key decisions (and why)

- **Scripts are plain files in the user's script folder, never in this repo.** That keeps nodes shareable as single
  files (the future library). The `.gh` stores `repo:relative/path`.
- **Controls are drawn by us in `AlienAttributes` (GDI), not HTML.**
  - The Grasshopper canvas is one painted surface: web views per node would fight pan, zoom and selection.
  - The values are the node's manual values, the same store as MCP `set_values`, so the canvas and agents always agree.
- **Rich UI goes in the Alien panel (HTML in an Eto WebView)**, hosted two ways:
  - **Default: `AlienSidecar`**, a borderless window glued to the Grasshopper editor.
    - macOS: `DocumentEditor.Handle` *is* the editor's NSWindow, so `addChildWindow:ordered:` makes the sidecar move
      with it natively.
    - Windows: an owned window (`GWLP_HWNDPARENT`) that follows the editor (untested).
  - **Setting `"panel": "rhino"`:** a Rhino panel, registered under Grasshopper's own Rhino plug-in (no .rhp needed).
  - **Bridge:** page → C# is `window.eto.postMessage(json)` → `MessageReceived`. C# → page is
    `ExecuteScriptAsync("window.alien.state(json)")`.
- **Describe-a-node:**
  - The model always returns the whole file.
  - Alien writes it (the old version goes to `.alien/history`), re-solves, and judges the run report: an error, unset
    outputs, no outputs, or every output empty sends the report back. Up to 4 attempts.
  - The default model is `anthropic/claude-haiku-5.5`, chosen from tests/describe (10/10 on the first try).
  - The in-node prompt bar was removed on purpose (two prompt places confused people); describing happens in the panel.
- **Suite nodes speak metres and scale to the model's units.** They give sample geometry when unwired, so they show
  something straight away. Line 3 of each file is a one-line description, which becomes the library listing.

## Build and verify (do this for every change)

1. **Rhino must be fully closed to deploy.** On macOS Cmd+Q; closing the windows is not quitting. Wait for
   `pgrep Rhinoceros` to be empty, then run `dotnet build -c Release` in `src/`. Building while Rhino is still quitting
   overwrites a loaded assembly.
2. **While Rhino is open,** `dotnet build -c Release -p:GH_Libraries=` checks the code compiles without deploying.
3. **Check it in Rhino:** launch it (see tools/README.md) and drive it with `tools/` (`mcp.py`, `capture.py`,
   `click.py`, `panel.py`).
   - Look at the result: `capture.py` for the canvas or viewport; `screencapture` for the panel and dialogs.
   - Don't claim a UI change works without seeing it.
4. **Describe-a-node prompts or model changes:** rerun `tests/describe/suite.py` and update its README table.
5. **Never quit someone else's Rhino session without asking.** `quit saving no` discards their work.

## Gotchas (each cost real time)

- **Settings path on macOS (inside Rhino):** `~/Library/Application Support/Alien/settings.json`, not `~/.config`.
- **Never `g.SetClip(...)` in a node's Render and then restore it.** On macOS the restored clip leaks into the next
  node's render (other nodes vanish). Shape bands with rounded paths instead.
- **`run_code` (MCP)** runs in its own Python scope, without the nodes' `sys.modules`. To debug a helper, insert the
  script folder into `sys.path` and import it there.
- **Rhino C# scripts:** a top-level `return;` skips writing `result`. Use if/else.
- **`CreateInterpolatedCurve(..., ChordPeriodic)`** only closes if the point list repeats its first point at the end.
- **Ollama thinking models** (qwen3.x) need `reasoning_effort: "none"`, or they think for minutes.
- **MCP `add` name lookup** prefers an exact name over a nickname, and a component over a same-named parameter.
  `"Params:Circle"` gets the parameter.
- **The public Overpass (OSM) mirrors are unreliable**, which is why `vic_site_map` reads Overture's PMTiles from their
  CDN and only falls back to Overpass.
- **Cost:** image generation is about US$0.034 per drawing. Graphics are cached in `<script folder>/.alien/cache/graphics`
  by request; `variant` asks for a new drawing.

## Conventions

- **Match the surrounding code:** comments explain why, not what; names are plain; C# 9, net7.0.
- **MCP tool output stays compact JSON** (every byte is paid for on every call); keep tool descriptions short.
- **Script header grammar** (docs/AGENT_GUIDE.md):
  `name:type [| "description"] [min= max= step= decimals= default= options="a|b"]`.
- **Header ranges and defaults are UI:** give them in every node.
- **Keep docs in step with behaviour:** README.md (users), docs/DEV_NOTES.md (developers), docs/AGENT_GUIDE.md (agents
  and the model), and this file.
- **Never put API keys in `.gh` files, scripts or this repo.** They live in settings.json only; `.gitignore` covers
  `settings.json` and `.alien/`.

## Security notes

- **MCP server:** loopback only, and it refuses requests carrying a non-loopback Origin (DNS-rebinding guard). Any
  local process can call it, including `run_code`. Never bind it to other interfaces without adding auth (the brief's
  "hosted MCP for Pro" needs that).
- **Model-written and installed scripts run with the user's rights** and can read settings.json, including the key.
  Before the public library:
  - show the code and add a review step before anything runs
  - route model and image calls through the plugin (`alien_graphics` currently reads the key via
    `ScriptRepository.Setting`)
- **The panel page escapes everything it shows**, and its WebView refuses navigation and new windows.

## Next up

- Test on Windows: the build, the sidecar (an owned window), and the controls.
- **Library (Phase 2):** publish and install from the panel; a Cloudflare Worker with R2 and D1; review before install.
- Save generated graphics (the drawing plus traced geometry) to the library (backburner).
- Open questions: the name and brand; whether the free tier is bring-your-own-key only or includes some hosted builds.
