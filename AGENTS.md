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
  below, library categories, the generated-asset library, and four more suite nodes (site_views, object_3d,
  material_texture, vic_site_map v2 with terrain, streets and trees). On 13 Oct 2026: themes (presets + custom colours),
  the Alien mark, a growing text box that keeps typing on click-away, describe-a-node from a selection (auto-wired), and
  a first-pass building suite (bim_site, levels v2, grids, wall_types, walls, doors, windows, curtain_wall,
  beams_columns, structure_edit, connectors). On 14 Oct 2026 (0.02-alpha): one-click installers (Windows Setup.exe,
  macOS .pkg) and an in-panel Help screen that links an OpenRouter key. Phases 2–3 (Cloudflare library, accounts,
  Stripe test mode) are not started.

## Architecture

```
src/
  AlienComponent.cs     the node: script binding, solve, pin rebuild, manual values, persistence, menu, StartDescribe
  AlienAttributes.cs    the node's look and mouse handling: header, status row, controls, error strip, selection glow
  ScriptRepository.cs   settings.json (re-read when changed; saved owner-only) and the user's script folder
  Suite.cs              suite/*.py are embedded in the .gha; on panel open, new files are added to <script folder>/suite
                        and unedited ones updated (hashes in .alien/suite.json)
  Library.cs            the node library: categories (# CATEGORY: line > model pick cached in .alien/library.json >
                        keywords), and the background model call that sorts uncategorised nodes
  Assets.cs             generated assets (.alien/assets/<kind>/<id>/): listing with thumbnails, bake as block, add
                        texture as a PBR material, the recipe to re-place the node that made one
  AlienUi.cs            dialogs and shell helpers (editor, reveal, settings file)
  Scripting/            HeaderParser (header grammar -> ParamDef), ScriptRunner (RhinoCode), TypeMap, Values
  Ai/                   ModelClient (OpenRouter / Ollama, one OpenAI-style client), Generator (the retry loop),
                        Prompts (system prompt = rules + AGENT_GUIDE sections), Transcript (per-node request history),
                        OpenRouterAccount (the Help screen's key check and credit balance)
  Ui/                   AlienPanel (Eto WebView showing pane.html), AlienSidecar (panel window glued to the GH editor),
                        NodeControls (control kind, recommended ranges), InlineEditor (text box on the canvas), Theme
                        (presets + colours mixed from background/text/accent), Logo (the Alien mark as a GDI path),
                        Suggest, ImageDrop, pane.html
  Mcp/                  McpServer (hand-rolled Streamable HTTP, JSON only), Tools*.cs (the tool set), Describe (compact
                        run reports), UiThread (all Rhino/GH calls marshalled to the UI thread, tool calls serialised)
suite/                  the preloaded nodes (Python) + helpers: alien_graphics (figure/tree drawings), alien_ai (settings,
                        OpenRouter chat/vision/images, caches), alien_assets (asset library writer), alien_vicdata
                        (terrain, trees, street sections for vic_site_map), alien_bim (building records, units, levels,
                        grids, text curves, sections, member solids), alien_walls (walls and openings), alien_sections
                        (Australian section catalogue)
docs/AGENT_GUIDE.md     agent reference: served by the MCP `docs` tool AND embedded in describe-a-node's prompt
tests/describe/         the describe-a-node test harness (suite.py) + results
tests/bim/chain.py      places, wires and checks the whole building suite on the open canvas
tools/                  drive a running Rhino from a shell (see tools/README.md)
installer/              windows/Alien.nsi + settings.ps1 (NSIS Setup.exe), mac/ (productbuild .pkg), art/ (icon and
                        wizard images drawn from the mark), INSTALL.md, and the by-hand scripts for the zip
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
- **Theme:** people pick three colours (background, text, accent) or a preset; everything else is mixed from them
  (`Ui/Theme.cs`), so adding a preset is one line and light themes work without special cases. Saved as settings
  `theme`; the panel gets it as CSS variables. The Alien mark (`docs/brand/alien-mark.svg`) is drawn in the accent on
  nodes and the panel, and in black as Grasshopper's icon.
- **Describe from a selection:** a box around several nodes means "build on these": a new node is placed beside them,
  the model sees what each outputs, and its `WIRES:` line is wired before the first judged run. This keeps chains
  continuous (levels and grids -> a slab node) without the user wiring by hand.
- **Describe-a-node:**
  - The model always returns the whole file.
  - Alien writes it (the old version goes to `.alien/history`), re-solves, and judges the run report: an error, unset
    outputs, no outputs, or every output empty sends the report back. Up to 4 attempts.
  - The default model is `anthropic/claude-haiku-5.5`, chosen from tests/describe (10/10 on the first try).
  - The in-node prompt bar was removed on purpose (two prompt places confused people); describing happens in the panel.
- **Building elements travel as JSON records** (`alien_bim.pack` / `read`), one per element, because Grasshopper
  turns Python objects into their repr text on the wire. Records are metres in world coordinates and carry enough to
  rebuild the element (doors rebuild and cut the walls from wall records), so chains stay plain wires and agents and
  models can read them. Each node still outputs ordinary geometry for previews and baking.
- **Suite nodes speak metres and scale to the model's units.** They give sample geometry when unwired, so they show
  something straight away. After the header: a `# CATEGORY:` line, then a one-line description (the library listing).
- **Library categories are fixed** (Library.Categories: Site & context, Massing & objects, Entourage, Materials, Views &
  drawings, Data & lists, Geometry, Analysis, Utilities) so the panel's chips stay stable. The model never rewrites a
  user's file to add a category; its pick lives in `.alien/library.json`, keyed by a hash of the header, so editing the
  header re-sorts the node.
- **Generated assets are files, not a database:** `asset.json` + `thumb.png` + files per asset, written by the Python
  helper (`alien_assets.save`) and read by C# (`Assets.cs`). The id is a hash of (kind, prompt, params), so regenerating
  overwrites. `asset.json` keeps the node's `inputs`, so "Place node" recreates the node (free, from the cache).
- **Paid or slow steps sit behind a `button` input** (object_3d `generate`, vic_site_map `refresh`, site_views `run`,
  material_texture `add`): a momentary control, true for one solve, so re-solving, dragging other sliders or reopening
  the file never repeats them. object_3d once put sizes into the prompt, so each slider release called the model and
  saved a new asset; sizes now only rescale the cached design.
- **Scripts can retune their controls after a run** with `NODE_CONTROLS` (range, shown value, a key per result). It is
  a first step towards dynamic inputs (pins that change with the result), which the user asked to leave for later:
  no pins appear or disappear.
- **object_3d runs model-written code:** only `rg` and `math`, a restricted builtins dict, no imports. It is not a
  sandbox (Python introspection can escape one); the code comes from the user's own model call, and is cached and shown
  in the `code` output.

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
- **RhinoCode sets every Python output to None before the script runs**, so "never assigned" can't be read from the
  scope: an output still None whose name the code never mentions is reported unset (`ScriptRunner`). The epilogue
  calls builtins through `import builtins`, so scripts may name inputs `type`, `list` or `set`.
- **A wired empty list reaches a script exactly like an unwired input**, so a suite node shows its sample geometry when
  the node upstream outputs nothing. Say so in `info` when it matters.
- **`CreateInterpolatedCurve(..., ChordPeriodic)`** only closes if the point list repeats its first point at the end.
- **Ollama thinking models** (qwen3.x) need `reasoning_effort: "none"`, or they think for minutes.
- **MCP `add` name lookup** prefers an exact name over a nickname, and a component over a same-named parameter.
  `"Params:Circle"` gets the parameter.
- **The public Overpass (OSM) mirrors are unreliable**, which is why `vic_site_map` reads Overture's PMTiles from their
  CDN and only falls back to Overpass.
- **Cost:** image generation is about US$0.034 per drawing or texture; an object_3d build US$0.01–0.02. Everything is
  cached under `<script folder>/.alien/cache` (graphics, images, textures, objects) by request; `variant` asks for a new
  one.
- **A node placed from C# reads its header on the first solve:** set inputs on a just-placed node with
  `PresetManualValues` (stored by name), not `SetManualValues` (which drops names the header doesn't have yet).
- **Rhino views and layouts** (site_views): see docs/DEV_NOTES.md "Rhino API facts". The ones that cost most:
  `page.SetActiveDetail(id)` before changing a detail's camera; clipping planes keep the side their normal points to;
  a named view records the clipping planes active in the viewport it was saved from.
- **Vicmap services change under us:** on 12 Oct 2026 a republish broke spatial queries on the ArcGIS property and
  parcel layers (attribute queries still worked); vic_site_map now falls back to the open-data WFS twins. WFS CQL
  geometry literals there are lat/lon (`POINT(lat lon)`), output coordinates lon/lat.
- **Rhino for Mac's WinForms is partial:** ListBox stays 0 x 0, ListView crashes Rhino. TextBox and ContextMenuStrip
  work; draw other UI on the canvas (the address suggestion list is painted).
- **Don't open old .gh files from a script in the user's Rhino:** an AutoSave holding the old AlienNode (v0.2.0) raised
  a modal "Unrecognized Objects" dialog that blocked Rhino (and the MCP) until the user closed it.
- **Captures of a backgrounded Rhino** don't show display textures or freshly baked objects' shading. Verify materials
  through the API (or ask the user to look), not from a capture.

## Releases

- **Version:** `src/AlienInfo.cs` (`Version`), `src/Mcp/McpServer.cs` (`Version`) and `src/Alien.csproj`
  (`InformationalVersion`; `Version` must stay numeric, e.g. `0.0.2`) say the same thing, e.g. `0.02-alpha`.
- **Package:** `bash tools/package.sh` builds without deploying and writes to `dist/` (git-ignored):
  - `Alien-<version>-Setup.exe`: NSIS installer (needs `brew install makensis`; package.sh sets `LANG`, because
    makensis dies with `std::bad_alloc` in a shell with no locale). Per user, no admin: Alien.gha into
    `%APPDATA%\Grasshopper\Libraries`, settings merged by `installer/windows/settings.ps1` (script folder, optional key),
    uninstaller + Settings > Apps entry. Waits for Rhino to close; can start Rhino with Grasshopper.
  - `Alien-<version>.pkg`: pkgbuild + productbuild, current-user domain only (~/Library/...); its installation check
    refuses while Rhino runs (`system.applications.fromIdentifier('com.mcneel.rhinoceros.8')`).
  - `Alien-<version>.zip` (by hand) and `Alien-<version>.sha256` (all three).
  - Artwork (icon, wizard bitmaps, pkg background) is drawn by `installer/art/render.swift` from the mark. NSIS needs
    bottom-up 24-bit BMPs; `sips` writes top-down ones, which crash makensis.
- **Check before publishing:** quit Rhino, `installer -pkg dist/Alien-<version>.pkg -target CurrentUserHomeDirectory`,
  start Rhino, and confirm `/health` reports the new version and nodes solve. List the exe's contents with
  `7zz l dist/Alien-<version>-Setup.exe` (`brew install sevenzip`). Both installers are unsigned (SmartScreen / Gatekeeper
  ask once); the exe has not been run on Windows yet.
- **Publish:** merge to main, then `gh release create v<version> dist/Alien-<version>-Setup.exe dist/Alien-<version>.pkg
  dist/Alien-<version>.zip dist/Alien-<version>.sha256 --prerelease --title "Alien <version>"`.
- **Release notes:** add the release to [CHANGELOG.md](CHANGELOG.md) before tagging, and point the README's
  Download table and installer/INSTALL.md's links at the new version.
- **Published:** 0.01-alpha (13 Oct 2026, the zip with install scripts) and 0.02-alpha (14 Oct 2026, `v0.02-alpha`:
  Setup.exe, .pkg, zip, sha256; checksums verified after download). The Setup.exe has not been run on Windows yet.

## Conventions

- **Match the surrounding code:** comments explain why, not what; names are plain; C# 9, net7.0.
- **MCP tool output stays compact JSON** (every byte is paid for on every call); keep tool descriptions short.
- **Script header grammar** (docs/AGENT_GUIDE.md):
  `name:type [| "description"] [min= max= step= decimals= default= options="a|b"]`.
- **Header ranges and defaults are UI:** give them in every node.
- **Keep docs in step with behaviour:** README.md (users), docs/DEV_NOTES.md (developers), docs/AGENT_GUIDE.md (agents
  and the model), this file, and docs/docs.json: after changing a suite node, a setting or an MCP tool, run
  `python3 tools/docs_json.py --check` with Rhino open (it re-reads pins, categories and tools, and fails on gaps).
- **Never put API keys in `.gh` files, scripts or this repo.** They live in settings.json only; `.gitignore` covers
  `settings.json` and `.alien/`. The panel never receives the key, only `OpenRouterAccount.Hint` ("sk-or-v1-…9f3a").

## Security notes

- **MCP server:** loopback only, and it refuses requests carrying a non-loopback Origin (DNS-rebinding guard). Any
  local process can call it, including `run_code`. Never bind it to other interfaces without adding auth (the brief's
  "hosted MCP for Pro" needs that).
- **Model-written and installed scripts run with the user's rights** and can read settings.json, including the key.
  Before the public library:
  - show the code and add a review step before anything runs
  - route model and image calls through the plugin (`alien_graphics` currently reads the key via
    `ScriptRepository.Setting`)
- **The panel page escapes everything it shows**, and its WebView refuses navigation and new windows. Help links open
  in the browser through `AlienUi.OpenUrl`, which allows only https openrouter.ai and the Alien GitHub repo.

## Next up

- Test 0.02-alpha on Windows: run Setup.exe (the key page, settings.ps1, the uninstaller, Start Rhino), then the
  plugin itself (the sidecar as an owned window, the controls, the Help screen's links).
- Sign the installers (an Authenticode certificate for Setup.exe; an Apple Developer ID and notarisation for the .pkg)
  so SmartScreen and Gatekeeper stop asking.
- **Library (Phase 2):** publish and install from the panel; a Cloudflare Worker with R2 and D1; review before install.
- Publish generated assets (objects, materials, drawings) to the shared library alongside nodes.
- vic_site_map: optional draped copies of the site and lot curves; road markings; trees outside Tree Urban coverage
  from `tree_density` polygons.
- Open questions: the name and brand; whether the free tier is bring-your-own-key only or includes some hosted builds.
