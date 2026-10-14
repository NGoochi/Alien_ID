# Changelog

Releases are GitHub pre-releases at https://github.com/NGoochi/Alien_ID/releases. Alpha: built and tested on Rhino 8
for macOS; Windows builds from the same code but hasn't been tested on a Windows PC yet.

## Unreleased

### The Alien panel
- **Project agent:** a chat (panel mode *Project agent*) that sees the whole canvas and keeps a memory per Grasshopper
  file. It searches the library before building and suggests or places existing nodes, has describe-a-node write new
  ones, wires, sets inputs, edits scripts, deletes, inspects results and captures the viewport to check its work. Tool
  steps show as chips; Stop cancels; it won't press Generate buttons unless asked; its changes can be undone. Model:
  setting `agentModel` (default Claude Sonnet 5.5).
- **Start here:** the library opens with the base nodes (blank node, 3D object, 2D object, symbol, material, display
  mode, views, layout, exploded axo), and a new **Create** category holds the generators.
- **Objects library** (was *Generated*): adds 2D objects, symbols, display modes and **building types** (wall, slab,
  roof, stair and balustrade types, doors, windows, section profiles) saved by type nodes' Save buttons; Place node
  rebuilds a type, Copy record copies it.

### Nodes and canvas
- **Pick geometry in Rhino:** unwired geometry inputs show *Select in Rhino*; the node keeps the references and
  re-solves when the objects move, change or are deleted.
- **Suite nodes on the toolbar:** a button per suite node on the Alien tab (Create, Site, Building, Views, Entourage,
  Data).
- **New suite nodes:** `slab_types`, `slabs`, `roof_types`, `roofs` (straight-skeleton hip and gable roofs),
  `stair_types`, `stairs`, `balustrade_types`, `balustrades` (first-pass NCC checks), `section_profile`, `object_2d`,
  `symbol`, `display_mode`, `views`, `layout`, `exploded_axo`. `wall_types`, `doors` and `windows` gain Save buttons.
- MCP `add`, `wire`, `delete` and `set_values` record Grasshopper undo.

## 0.02-alpha (14 Oct 2026)

Downloads: `Alien-0.02-alpha-Setup.exe` (Windows), `Alien-0.02-alpha.pkg` (macOS), `Alien-0.02-alpha.zip` (by hand),
`Alien-0.02-alpha.sha256`.

### Install and first run
- **Windows installer (`Setup.exe`):** per user, no administrator rights. It waits for Rhino to close, installs Alien
  into Grasshopper, creates `Documents\Alien scripts`, can save your OpenRouter key on an optional page, adds an
  uninstaller to *Settings > Apps*, and can start Rhino with Grasshopper. Not yet run on a Windows PC.
- **macOS installer (`.pkg`):** installs for the current user only, no password; refuses while Rhino is open.
- Both are unsigned: Windows SmartScreen (*More info > Run anyway*) and macOS Gatekeeper (*System Settings > Privacy &
  Security > Open Anyway*) ask once.
- No first-run prompt for a script folder: Alien uses `Documents/Alien scripts` until you choose another.

### The Alien panel
- **Help (?):** connect OpenRouter step by step (account, credit, API key with a limit), paste the key and **Link key**.
  Alien checks it with OpenRouter and shows the credit left, the key's own limit and its expiry; *Check credit* and
  *Remove* later. Opens by itself the first time when no key is linked, with getting started, costs and
  troubleshooting.
- **Themes (⚙):** 12 presets (Alien, Cursor Dark, One Dark, Dracula, Monokai, Nord, Tokyo Night, Solarized Dark and
  Light, High Contrast, Light, GitHub Light) or your own background, text and accent colours. Nodes, the canvas text
  box and the panel follow it.
- **Build on a selection:** select two or more nodes (or one native component) and describe a node that uses them: it
  is placed beside them, the model sees what each outputs, and the new node is wired to them. Agents get the same
  through MCP `generate` with `context`, and `canvas` marks the user's selection.
- The Alien mark on node headers and the panel (in the accent), and as Grasshopper's icon (black).

### Nodes
- **Building suite (new category *Building elements*):** `bim_site` (project base point, grid rotation, north, AHD
  datum, drawing scale), `levels` (rewritten: naming schemes, RLs, level lines, heads, annotations), `grids` (BIM-style,
  bubbles, intersections, dimensions), `wall_types`, `walls`, `doors`, `windows` (hosted openings cut through the
  walls, with plan, reflected and elevation symbols, and generated designs fitted into leaves), `curtain_wall`
  (profiles and trusses), `beams_columns`, `structure_edit` and `connectors` (Australian steel, timber and concrete
  sections; base plates, splices, end and fin plates, bolts, a schedule). Elements pass between nodes as JSON records.
- Text fields grow while you type, and clicking another field or the canvas keeps what you typed (it used to be lost).
  Picking an address suggestion no longer also clicks the control under the list.

### Fixes
- Scripts with an input named `type`, `list` or `set` broke Alien's output handling.
- Outputs a script never assigned were no longer reported (Rhino now sets outputs to None before a run).
- Describe-a-node tells the model the document's units ("5 m" in a millimetre model had come out as 5 mm).
- A theme colour field losing focus could bring back old colours.

### For developers
- `tools/package.sh` builds all the downloads (NSIS via `brew install makensis`, pkgbuild/productbuild, artwork from
  the mark). `tools/pane_snapshot.swift` renders the panel page off screen; `panel.py --state` saves its state.
- `tests/bim/chain.py` places, wires and checks the whole building suite.

## 0.01-alpha (13 Oct 2026)

First release: a zip with `Alien.gha`, `install-mac.command`, `install-windows.bat` and INSTALL.md.

- The Alien node: script name, PY / C# badge, run state and the error on the node; controls built from the script
  header (sliders, switches, dropdowns, text fields, momentary Generate buttons, image drop, address suggestions);
  `NODE_CONTROLS` to retune sliders after a run.
- Describe-a-node in the Alien panel (beside Grasshopper): a model writes the script, runs it and fixes its own errors
  (Claude Haiku 5.5 by default through OpenRouter, or a local Ollama model).
- The library, sorted into categories, and the **Generated** tab for 3D objects, materials and drawings.
- The suite: `vic_site_map` (lot, neighbours, roads, terrain, 3D streets, trees, massing, zone and overlays),
  `site_views`, `object_3d`, `material_texture`, `scale_figure`, `scale_tree`, `levels`, `branch_picker`.
- Only Generate buttons spend credit: sliders rework cached results.
- An MCP server (`127.0.0.1:9876/mcp`) so agents such as Claude Code can build and wire nodes live.
