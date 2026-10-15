# Alien

A Grasshopper (Rhino 8) node whose logic lives in a plain script file, Python 3 or C#. Its pins and controls come from
a two-line header. You describe what you want in plain English and a model writes the script, runs it, and fixes its
own errors. Nodes live as plain files, so they can be shared, and AI agents can drive everything through a built-in
MCP server.

*Status: [alpha 0.02](https://github.com/NGoochi/Alien_ID/releases/tag/v0.02-alpha) (14 Oct 2026; [what's new](CHANGELOG.md)), a working prototype for a uni
mini-MBA pitch (mock business: no real sales). Phase 1 of [BRIEF.md](BRIEF.md) is done; the online library, accounts
and payments (Phases 2–3, planned in [ROADMAP.md](ROADMAP.md)) are not built yet.*

## Download

| | |
|---|---|
| **Windows** | [Alien-0.02-alpha-Setup.exe](https://github.com/NGoochi/Alien_ID/releases/download/v0.02-alpha/Alien-0.02-alpha-Setup.exe): run it with Rhino closed. No admin rights; it can link your OpenRouter key and start Rhino for you |
| **macOS** | [Alien-0.02-alpha.pkg](https://github.com/NGoochi/Alien_ID/releases/download/v0.02-alpha/Alien-0.02-alpha.pkg): open it with Rhino closed. Installs for your user only |
| By hand | [Alien-0.02-alpha.zip](https://github.com/NGoochi/Alien_ID/releases/download/v0.02-alpha/Alien-0.02-alpha.zip) (Alien.gha and install scripts) · [checksums](https://github.com/NGoochi/Alien_ID/releases/download/v0.02-alpha/Alien-0.02-alpha.sha256) |

Then open Grasshopper, place a node from the **Alien** tab, click **PANEL**, and click **?**: Help walks you through
connecting OpenRouter. The installers aren't signed yet, so Windows asks *More info > Run anyway* and macOS *System
Settings > Privacy & Security > Open Anyway* the first time. Full guide: [installer/INSTALL.md](installer/INSTALL.md).
Windows is untested in this alpha.

```python
# NODE_INPUTS: base:Curve, count:int | "divisions" min=2 max=200 default=20
# NODE_OUTPUTS: points, log
# Divides a curve into N points.
import Rhino.Geometry as rg
params = base.DivideByCount(count, True) if base else []
points = [base.PointAt(t) for t in params]
log = f"{len(points)} points"
```

Save the file, or let the model or an agent edit it, and the node rebuilds its pins (wires kept) and re-solves.

## What's in it

- **The Alien node:** the Alien mark and the script name, a PY / C# badge, its state (ok / error / running), and the
  error itself on the node when a run fails. The selected node glows.
- **Project agent:** a chat in the panel that knows the whole canvas and remembers the project (per Grasshopper file).
  Ask "what could I build next?", "set up levels and grids for three storeys", "look at the viewport": it searches the
  library first and suggests or places existing nodes, has describe-a-node write new ones, wires, sets inputs, tests,
  fixes, deletes and captures the viewport to check its work. It never spends your credit on Generate buttons unless
  you ask, and everything it changes can be undone (Cmd/Ctrl+Z).
  - **Buildings:** "build a two-storey house with a steel frame" places the building suite as one wired group, all on
    one footprint (site, levels, grids, walls on every storey, doors, windows, slabs, roof, frame, stairs,
    balustrades), never one big generated node. "Make it three storeys with a hip roof" changes that group in place.
    Interior walls and fittings aren't included yet; it says so and asks before generating fittings.
- **Pick geometry in Rhino:** an unwired geometry input (curve, surface, Brep, mesh, point…) shows **Select in Rhino**:
  click it and pick objects in the viewport (or select them first). The node keeps the references and re-solves when
  you move or edit them.
- **Suite nodes on the toolbar:** every suite node has its own button on Grasshopper's Alien tab, in panels Create,
  Site, Building, Views, Entourage and Data, and the panel's library opens with **Start here**: blank node, 3D object,
  2D object, symbol, material, display mode, views, layout, exploded axo.
- **Themes:** pick a preset (Alien, Cursor Dark, One Dark, Dracula, Monokai, Nord, Tokyo Night, Solarized Dark/Light,
  High Contrast, Light, GitHub Light) or your own background, text and accent colours in the panel's settings (⚙).
  Nodes, the canvas text box and the panel all follow it.
- **Controls built from the header:** an unwired number input gets a slider. The header's `min`/`max` set its range;
  without them a range is recommended from the default. A `bool` gets a switch, `str` a text field, and
  `str ... options="a|b|c"` a dropdown (a text field grows while you type, and clicking anywhere else keeps what you
  typed), and `bool ... button` a momentary button (true for one solve: for model calls
  and other slow or paid steps, so re-solving never repeats them). `str ... image` takes an image dropped on the node
  and shows a thumbnail; `str ... suggest="vic-address"` suggests addresses while you type. A script can retune its sliders after a run
  (`NODE_CONTROLS`): `object_3d`'s size sliders show and span the size of what it built. Wired inputs collapse to their pin. Values save with the `.gh`, can be undone, and
  are the same values agents set over MCP.
- **The Alien panel:** it sits glued to the side of the Grasshopper window and moves with it, or docks in Rhino. It
  follows the selected node:
  - describe a new node or a change to it; with several nodes selected (drag a box around them), describe a new node
    that works with them: the model sees what each one outputs, and the new node is wired to them
  - see every request with its attempts and cost
  - see the node's inputs and code
  - restore an earlier version
  - pick the model, and set the theme (⚙)
  - **Help (?)**: step by step, create an OpenRouter account, add credit, make a key and link it (Alien checks the key
    with OpenRouter and shows your credit), plus getting started, costs and troubleshooting. It opens by itself the
    first time if no key is linked
  - place nodes from the library, sorted into categories (Create, Site & context, Building elements, Massing & objects, Entourage, Materials,
    Views & drawings, Data & lists, Geometry, Analysis, Utilities). A node's category comes from its `# CATEGORY:`
    line; nodes without one are sorted by the model once (cached in `.alien/library.json`), with a keyword guess until
    then
  - browse the **Objects** library, with thumbnails: what the generators made (3D and 2D objects, symbols, materials,
    display modes, drawings) and the building types saved by type nodes (wall, slab, roof, stair and balustrade types,
    doors, windows, section profiles). Bake an object or symbol as a block, add a texture as a real-size material,
    place the node that made it (with the same inputs), or copy a type's record
- **Describe-a-node:** the request, the current script and the wired inputs go to a model, which writes the whole
  script. Alien runs it, and any error (Python traceback, C# compiler message) goes back to the model to fix, up to 4
  attempts. The default is Claude Haiku 5.5 through OpenRouter; any OpenRouter model, or a local Ollama model, also
  works. The 10-prompt test works 10/10 (8–10 on the first try, the rest fixed by the loop) at about US$0.001–0.002 per node
  ([tests/describe](tests/describe/README.md)).
- **Preloaded suite** ([suite/](suite/README.md)), shipped inside the plugin and copied into your script folder:

  | Node | What it does |
  |---|---|
  | `vic_site_map` | A Victorian address (suggested as you type) becomes the lot, neighbouring lots, roads, terrain and contours, 3D streets (carriageway, kerbs, footpaths, nature strips), trees, building massing on the ground, zone and overlays (Vicmap, Geoscience Australia, Overture Maps) |
  | `site_views` | Plan, site plan, X/Y centreline sections and four elevations around the site as Rhino named views (sections clipped), and an optional layout sheet at standard scales |
  | `object_3d` | Describe a thing ("a dining chair"), or give a picture, and a model writes the code that builds it from solids at real size |
  | `object_2d`, `symbol` | A model draws flat things at real size (a car or bed in plan, a person in elevation) or annotation symbols at paper size (north arrows, section and level markers, scale bars) with text slots and Rhino blocks |
  | `display_mode` | Describe a look ("white clay with soft shadows", "blueprint") or show a picture, and get a Rhino display mode |
  | `material_texture` | A finish ("board-formed concrete") becomes a seamless texture and a Rhino material at real-world scale |
  | `bim_site`, `levels`, `grids` | The building suite's base: a site record (origin, grid rotation, north, AHD datum, drawing scale), named levels with RLs, level lines and heads, and BIM-style grids with bubbles, intersections and dimensions |
  | `wall_types`, `walls`, `doors`, `windows` | Layered walls from Australian build-ups along any curve, joined at corners, with hosted doors and windows cut through them, plan and elevation symbols (swings, opening leaves), and generated designs fitted into leaves |
  | `curtain_wall` | Mullions, transoms and glass along straight, curved or closed paths between levels, in box, I, T, round, fin or flat bar profiles, with optional trusses |
  | `slab_types`, `slabs`, `roof_types`, `roofs` | Layered slabs at levels with openings (stair voids), and hip, gable, dutch gable, skillion or flat roofs over any simple outline (straight skeleton) with fascias, gutters and a roof plan |
  | `stair_types`, `stairs`, `balustrade_types`, `balustrades` | Straight, L, U or spiral stairs fitted between levels with first-pass Australian NCC checks, plans, floor voids, and balustrades (glass, bars, rails, wires, timber) along stair and slab edges |
  | `section_profile` | A section from the Australian catalogue or a custom shape, with computed properties and a dimensioned drawing, usable as a beam or column section |
  | `views`, `layout`, `exploded_axo` | Named plans, RCPs, sections, elevations and axonometrics of any geometry; layout sheets with views at standard scales and a title block; exploded axonometrics with explosion lines and labels |
  | `beams_columns`, `structure_edit`, `connectors` | A steel, timber or concrete frame on the grids from real Australian sections, edited by pattern ("B-L02-*: section=460UB67.1"), with base plates, splices, end and fin plates, bolts and a schedule |
  | `branch_picker` | Passes chosen branches of a data tree through |
  | `scale_figure`, `scale_tree` | An image model draws a person or tree, traced into true-size outlines, fills and hatches for elevations and plans |

  Every node that calls a model has a **Generate** button: only that spends credits. Sliders rework the cached result.
  The building nodes pass elements as JSON records (levels, grids, walls, slabs, members) and chain by pin name; type
  nodes save their types to the Objects library. See [suite/README.md](suite/README.md).

- **MCP server** (`http://127.0.0.1:9876/mcp`): agents such as Claude Code or Cursor can place and wire nodes, edit
  scripts, inspect data, capture the viewport and drive Rhino, while you watch.
- **Demo definitions** ([demos/](demos/README.md)): eight ready-made workflows: a greyscale site map, 3D object
  generation, 2D entourage, a frame with connection details, an attractor facade, a generated texture as a material, an
  exploded axo and a framed tower with curtain walls. Copy the folder into your script folder as `demos/`.

## Install

**From a release (recommended):** see [Download](#download) above. The installers put `Alien.gha` into Grasshopper's
Libraries folder (`%APPDATA%\Grasshopper\Libraries` on Windows; `~/Library/Application Support/McNeel/Rhinoceros/8.0/
Plug-ins/Grasshopper (b45a29b1-…)/Libraries` on macOS); one `Alien.gha` serves both platforms.
[installer/INSTALL.md](installer/INSTALL.md) covers first run, costs, updating, uninstalling and installing by hand.

**From source:**

Requirements:
- Rhino 8 (Windows or macOS)
- the .NET SDK 7 or later
- for describe-a-node and the graphics nodes, an [OpenRouter](https://openrouter.ai) API key; or Ollama for local models

1. Close Rhino completely (on macOS, Cmd+Q: closing the windows is not quitting).
2. Run `dotnet build -c Release` in `src/`. It builds `Alien.gha` and copies it into Grasshopper's Libraries folder:
   - Windows: `%APPDATA%\Grasshopper\Libraries`
   - macOS: `~/Library/Application Support/McNeel/Rhinoceros/8.0/Plug-ins/Grasshopper (b45a29b1-…)/Libraries`

   The Rhino install paths for the references are in `src/Alien.csproj` (override with `-p:RhinoSystemDir=… -p:RhinoGhDir=…`).
3. Start Rhino and open Grasshopper. The MCP server starts when Grasshopper loads.
4. Place an **Alien** node (tab *Alien › Script*). Your scripts live in the **script repository**, `Documents/Alien
   scripts` unless you choose another folder (right-click a node > *Script repository*), never in this repo; `.gh` files
   refer to scripts relative to it.
5. Link your key: open the Alien panel, click **?** and follow *Connect OpenRouter* (or put `"openrouterKey"` in
   `%APPDATA%\Alien\settings.json` on Windows, `~/Library/Application Support/Alien/settings.json` on macOS; the file
   is re-read when it changes).

### Settings (`settings.json`)

| Key | Meaning |
|---|---|
| `repository` | Script folder |
| `port` | MCP port (default 9876) |
| `editor` | Command for *Edit script*, e.g. `code` |
| `openrouterKey` | OpenRouter API key, used by describe-a-node and the graphics nodes. Never stored in `.gh` files or scripts |
| `model` | Describe-a-node model (default `anthropic/claude-haiku-5.5`; `ollama:<name>` for local) |
| `imageModel` | Graphics and texture nodes' image model (default `google/gemini-3.1-flash-lite-image`) |
| `model3d` | `object_3d`'s model (default `anthropic/claude-sonnet-5.5`) |
| `classifyModel`, `classify` | Model that sorts uncategorised library nodes (default: `model`); `"classify": "off"` uses keywords only |
| `ollamaUrl`, `reasoning` | Local model server; reasoning effort (default `low`, and `none` for local models) |
| `panel` | `"rhino"` docks the panel in Rhino instead of beside Grasshopper |
| `agentModel` | The project agent's model (default `anthropic/claude-sonnet-5.5`; it needs tool calling) |
| `model2d`, `modelDisplay` | The models for `object_2d`/`symbol` and `display_mode` (default Claude Sonnet 5.5) |
| `theme` | Written by the panel's settings: `{"preset", "background", "text", "accent"}` (absent = the Alien preset) |

## Using it

- **Describe a node:** select a node (or none, to make a new one), open the panel (PANEL on the node, or double-click
  the node) and type what it should do. Follow-ups edit the current script: "make the grid hexagonal".
- **Build on what's there:** drag a box around two or more nodes (say `levels` and `grids`), then describe a node that
  uses them ("a floor slab at every level inside the grid"). The new node appears beside them, already wired. With
  one Alien node selected, tick *Build a new node from this one* to do the same instead of changing it.
- **Ask the project agent:** switch the panel to *Project agent* and ask in plain words. It sees the canvas, remembers
  the project (ask it to "remember" facts), and shows each step it takes (placing, wiring, checking, looking at the
  viewport). About US$0.03–0.15 a request with Claude Sonnet 5.5; Stop cancels; Cmd/Ctrl+Z undoes its changes.
- **Pick geometry:** click *Select in Rhino* on a node and pick objects in the viewport; × clears.
- **Try values:** drag the sliders, flip the switches, type in the fields. A fast script follows a slider live; a slow
  one (network, heavy geometry) runs when you let go.
- **Right-click a node** for these sections:
  - Script: edit, new, open, reveal, reload
  - Describe: open the panel, stop
  - Inputs: show controls, reset
  - Share: copy the node id
  - Settings: script repository, settings file
- **Input dots:** green = wired, blue = a value set on the node (or by an agent), grey = the header default.

## Connect an agent

- Claude Code: `claude mcp add --transport http alien http://127.0.0.1:9876/mcp`
- Cursor / others: `{ "mcpServers": { "alien": { "url": "http://127.0.0.1:9876/mcp" } } }`

The server tells the agent how to work (its `initialize` instructions and the `docs` tool). Tools:
- Grasshopper: `canvas`, `add`, `wire`, `delete`, `search`, `script_read`, `script_edit`, `generate`, `inspect`,
  `set_values`, `bake`
- Rhino: `doc`, `objects`, `layers`, `view`, `capture`, `rhino`, `run_code`
- Reference: `docs`, `repo` (`library: true` lists nodes by category and the generated assets)

## Repository

| Path | What |
|---|---|
| `src/` | The plugin (C#, net7.0); `Library.cs` (node categories), `Assets.cs` (generated assets), `Suite.cs` |
| `src/Ai/` | Describe-a-node: model client, retry loop, prompts, request history; the Help screen's OpenRouter key check |
| `src/Ui/` | The Alien panel (`pane.html` in a WebView: describe, library, Generated, Help, settings), the sidecar, node controls, the text editor, themes, the Alien mark |
| `src/Mcp/` | The MCP server and its tools |
| `src/Scripting/` | Header parser, script runner (RhinoCode), value conversion |
| `suite/` | The preloaded nodes; embedded in the plugin at build time |
| `docs/AGENT_GUIDE.md` | The reference agents read (served by the `docs` MCP tool, and part of describe-a-node's prompt) |
| `docs/DEV_NOTES.md` | Hard-won facts: build, threading, pin rebuilds, RhinoCode, UI, describe-a-node |
| `docs/docs.json` | The full reference as JSON: every node and its pins, the panel, library, assets, settings, MCP tools (`python3 tools/docs_json.py` rewrites it) |
| `tests/describe/` | The describe-a-node test (10 prompts, several models) and its results |
| `tests/bim/` | `chain.py`: places, wires and checks the whole building suite on the open canvas |
| `tests/agent/` | `run.py` drives the panel's project agent; `building.py` checks its building tool (free) |
| `demos/`, `tests/demos/` | The demo definitions, their four helper nodes and previews; `build.py` rebuilds and checks them |
| `tools/` | Shell tools to drive and check a running Rhino (MCP calls, captures, simulated clicks, off-screen panel renders), and `package.sh` (builds the release downloads) |
| `installer/` | `windows/` (the NSIS script for Setup.exe and its settings helper), `mac/` (the .pkg definition and pages), `art/` (icon and wizard images drawn from the mark), INSTALL.md, and the by-hand scripts for the zip |
| `docs/brand/` | The Alien mark (SVG) |
| `CHANGELOG.md` | What changed in each release |
| `BRIEF.md`, `ROADMAP.md` | The product brief (tiers, stack, phases) and the plan for Phases 2–3 (Alien Cloud) |
| `AGENTS.md` | Project context for coding agents (also loaded by Claude Code via `CLAUDE.md`) |

## Development

See [AGENTS.md](AGENTS.md) for architecture, conventions and the verify loop, and [docs/DEV_NOTES.md](docs/DEV_NOTES.md)
for the details.

- **Build:** build with Rhino fully closed (Grasshopper locks the `.gha`). To check that code compiles while Rhino is
  open, run `dotnet build -c Release -p:GH_Libraries=`, which builds without deploying.
- **Suite:** edit nodes in `suite/`, and they are embedded at the next build. When the panel opens, new suite files are
  added to the script folder's `suite/` and unedited ones updated (by hash, `.alien/suite.json`); files you edited or
  deleted are left alone.

## Data, privacy and security

- **Describe-a-node:** sends the request, the current script and summaries of wired inputs to the chosen model
  through OpenRouter. Requests are only routed to providers that don't store or train on prompts.
- **Graphics, texture and 3D object nodes:** send only the prompt (and, for `object_3d`, the reference image you give).
- **`vic_site_map`:** sends the address to Vicmap and, as a fallback, OpenStreetMap Nominatim; the terrain, tree and
  street requests send only the map box.
- **Library sorting:** sends the header comments of uncategorised scripts (not their code) to the describe model.
- **The MCP server:** listens on loopback only and refuses browser requests from other origins. Any local program can
  still use it, including `run_code`, as with any local MCP server. Don't expose the port.
- **Code runs as you:** scripts written by the model, and scripts you install, run inside Rhino with your user's rights,
  and can read your settings file (including the API key). Read shared scripts before running them. The planned public
  library needs review before install, and model calls routed through the plugin so scripts never see the key.

## Roadmap ([BRIEF.md](BRIEF.md))

- **Phase 2:** a Cloudflare Worker, R2 and D1 for a shared node library (publish and install from the panel), a hosted
  model provider, and a gallery site.
- **Phase 3:** Google sign-in, credits and Stripe (test mode), and private nodes for Pro.
- **Next for the library:** publish generated assets (objects, materials, drawings) alongside nodes in the shared
  library.
