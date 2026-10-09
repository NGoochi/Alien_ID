# Alien

A Grasshopper (Rhino 8) node whose logic lives in a plain script file, Python 3 or C#. Its pins and controls come from
a two-line header. You describe what you want in plain English and a model writes the script, runs it, and fixes its
own errors. Nodes live as plain files, so they can be shared, and AI agents can drive everything through a built-in
MCP server.

*Status: working prototype for a uni mini-MBA pitch (mock business: no real sales). Phase 1 of [BRIEF.md](BRIEF.md) is
done; the online library, accounts and payments (Phases 2–3) are not built yet.*

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

- **The Alien node:** the script name, a PY / C# badge, its state (ok / error / running), and the error itself on the
  node when a run fails. The selected node glows.
- **Controls built from the header:** an unwired number input gets a slider. The header's `min`/`max` set its range;
  without them a range is recommended from the default. A `bool` gets a switch, `str` a text field, and
  `str ... options="a|b|c"` a dropdown. Wired inputs collapse to their pin. Values save with the `.gh`, can be undone, and
  are the same values agents set over MCP.
- **The Alien panel:** it sits glued to the side of the Grasshopper window and moves with it, or docks in Rhino. It
  follows the selected node:
  - describe a new node or a change to it
  - see every request with its attempts and cost
  - see the node's inputs and code
  - restore an earlier version
  - pick the model
  - place nodes from the library: your nodes and the preloaded suite
- **Describe-a-node:** the request, the current script and the wired inputs go to a model, which writes the whole
  script. Alien runs it, and any error (Python traceback, C# compiler message) goes back to the model to fix, up to 4
  attempts. The default is Claude Haiku 5.5 through OpenRouter; any OpenRouter model, or a local Ollama model, also
  works. A test of 10 prompts went 10/10 on the first try, about US$0.001 per node ([tests/describe](tests/describe/README.md)).
- **Preloaded suite** ([suite/](suite/README.md)), shipped inside the plugin and copied into your script folder:

  | Node | What it does |
  |---|---|
  | `vic_site_map` | A Victorian address becomes the lot, neighbouring lots, named roads, building massing, zone and overlays (Vicmap + Overture Maps) |
  | `levels` | Building levels from z0, each with its own height |
  | `branch_picker` | Passes chosen branches of a data tree through |
  | `scale_figure`, `scale_tree` | An image model draws a person or tree, traced into true-size outlines, fills and hatches for elevations and plans |

- **MCP server** (`http://127.0.0.1:9876/mcp`): agents such as Claude Code or Cursor can place and wire nodes, edit
  scripts, inspect data, capture the viewport and drive Rhino, while you watch.

## Install

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
4. Place an **Alien** node (tab *Alien › Script*). The first time, pick a **script repository** folder: your scripts
   live there, not in this repo, and `.gh` files refer to scripts relative to it.
5. Add your key: right-click a node, then *Open settings file*, and fill in `"openrouterKey"`. The file is
   `%APPDATA%\Alien\settings.json` on Windows and `~/Library/Application Support/Alien/settings.json` on macOS. It is
   re-read when it changes; no restart is needed.

### Settings (`settings.json`)

| Key | Meaning |
|---|---|
| `repository` | Script folder |
| `port` | MCP port (default 9876) |
| `editor` | Command for *Edit script*, e.g. `code` |
| `openrouterKey` | OpenRouter API key, used by describe-a-node and the graphics nodes. Never stored in `.gh` files or scripts |
| `model` | Describe-a-node model (default `anthropic/claude-haiku-5.5`; `ollama:<name>` for local) |
| `imageModel` | Graphics nodes' image model (default `google/gemini-3.1-flash-lite-image`) |
| `ollamaUrl`, `reasoning` | Local model server; reasoning effort (default `low`, and `none` for local models) |
| `panel` | `"rhino"` docks the panel in Rhino instead of beside Grasshopper |

## Using it

- **Describe a node:** select a node (or none, to make a new one), open the panel (PANEL on the node, or double-click
  the node) and type what it should do. Follow-ups edit the current script: "make the grid hexagonal".
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
- Reference: `docs`, `repo`

## Repository

| Path | What |
|---|---|
| `src/` | The plugin (C#, net7.0) |
| `src/Ai/` | Describe-a-node: model client, retry loop, prompts, request history |
| `src/Ui/` | The Alien panel (`pane.html` in a WebView), the sidecar, node controls, the text editor |
| `src/Mcp/` | The MCP server and its tools |
| `src/Scripting/` | Header parser, script runner (RhinoCode), value conversion |
| `suite/` | The preloaded nodes; embedded in the plugin at build time |
| `docs/AGENT_GUIDE.md` | The reference agents read (served by the `docs` MCP tool, and part of describe-a-node's prompt) |
| `docs/DEV_NOTES.md` | Hard-won facts: build, threading, pin rebuilds, RhinoCode, UI, describe-a-node |
| `tests/describe/` | The describe-a-node test (10 prompts, several models) and its results |
| `tools/` | Shell tools to drive and check a running Rhino (MCP calls, captures, simulated clicks) |
| `BRIEF.md` | The product brief: tiers, stack, phases |
| `AGENTS.md` | Project context for coding agents (also loaded by Claude Code via `CLAUDE.md`) |

## Development

See [AGENTS.md](AGENTS.md) for architecture, conventions and the verify loop, and [docs/DEV_NOTES.md](docs/DEV_NOTES.md)
for the details.

- **Build:** build with Rhino fully closed (Grasshopper locks the `.gha`). To check that code compiles while Rhino is
  open, run `dotnet build -c Release -p:GH_Libraries=`, which builds without deploying.
- **Suite:** edit nodes in `suite/`, and they are embedded at the next build. A script folder's `suite/` copy is only
  written when missing, so delete it to take the new version.

## Data, privacy and security

- **Describe-a-node:** sends the request, the current script and summaries of wired inputs to the chosen model
  through OpenRouter. Requests are only routed to providers that don't store or train on prompts.
- **Graphics nodes:** send only the drawing prompt.
- **`vic_site_map`:** sends the address to Vicmap and, as a fallback, OpenStreetMap Nominatim.
- **The MCP server:** listens on loopback only and refuses browser requests from other origins. Any local program can
  still use it, including `run_code`, as with any local MCP server. Don't expose the port.
- **Code runs as you:** scripts written by the model, and scripts you install, run inside Rhino with your user's rights,
  and can read your settings file (including the API key). Read shared scripts before running them. The planned public
  library needs review before install, and model calls routed through the plugin so scripts never see the key.

## Roadmap ([BRIEF.md](BRIEF.md))

- **Phase 2:** a Cloudflare Worker, R2 and D1 for a shared node library (publish and install from the panel), a hosted
  model provider, and a gallery site.
- **Phase 3:** Google sign-in, credits and Stripe (test mode), and private nodes for Pro.
- **Planned, not built:** saving generated graphics (drawings and traced geometry) to the library.
