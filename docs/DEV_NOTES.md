# Alien — developer notes

Hard-won facts about building this plugin. Read before changing C# code.

## Build & deploy

- `dotnet build -c Release` in `src/`. Post-build copies `Alien.gha` to `%APPDATA%\Grasshopper\Libraries`.
- **Close Rhino before building.** Grasshopper locks the `.gha`; the copy then fails and the build prints
  `Alien.gha was NOT deployed` — you would be testing the old build.
- References come from the Rhino install (`C:\Program Files\Rhino 8\System`, `Plug-ins\Grasshopper`), not NuGet:
  `Rhino.Runtime.Code.dll` (RhinoCode — runs CPython 3 / C# / IronPython 2) is not published on NuGet.
  Override with `-p:RhinoSystemDir=... -p:RhinoGhDir=...`. The macOS paths in the csproj are unverified.
- Targets `net7.0` (loads on Rhino 8's .NET 7 and .NET 8 runtimes; Rhino 8.32 runs .NET 8).

## Threading

- Every Rhino/Grasshopper API call must run on the UI thread. MCP requests arrive on pool threads →
  `UiThread.Run(...)` (`RhinoApp.InvokeAndWait`). Tool calls are serialised by `UiThread.Lock()`.
- `FileSystemWatcher` fires on a background thread → debounced → `RhinoApp.InvokeOnUiThread`.
- Tools that change a script wait for the re-solve **off** the UI thread (`WaitForSolve` polls `SolveStamp`),
  never by blocking inside a UI-thread call.

## Pin rebuild without losing wires ("Hops pattern")

Never change parameters inside `SolveInstance`. When the header changes:

1. `SolveInstance` notices and calls `doc.ScheduleSolution(1, callback)`, then returns.
2. The callback runs between solutions: snapshot `new List<IGH_Param>(p.Sources)` / `p.Recipients` keyed by
   nickname → unregister all → register new params → `OnParametersChanged()` → restore:
   inputs `newParam.AddSource(upstream)`, outputs `downstream.AddSource(newParam)` (always from the receiver's side).
3. `ExpireSolution(false)` → normal solve.

Renaming a pin in the header drops its wires (by design — names are the identity).

## Persistence

`.gh` stores `ScriptRef` (`repo:relative/path.py` when inside the repository, else absolute), a header snapshot
(so opening a file doesn't trigger a rebuild) and manual values. Grasshopper itself persists the dynamic params and wires.

## Script execution (RhinoCode) — `Scripting/ScriptRunner.cs`

Verified against Rhino 8.32 (decompiled + smoke-tested 2026-09-30):

- `RhinoCode.Languages.QueryLatest(LanguageSpec.Python3|CSharp|IronPython2)` returns null until loaders are registered
  and run: `RhinoCodePlatform.Rhino3D.Registrar.StartScriptingLanguages(new ProgressStatusResponder(), spec)` (public
  but undocumented → version-sensitive). Call on the UI thread; first Python use unpacks the runtime.
- Create code with `language.CreateCode(new Uri(fullPath))` (file storage → real file name in tracebacks, script folder
  on `sys.path`, relative `#load`/`#r`). One cached `Code` per file; `code.Text.Set(...)` on change; compile cache is
  keyed on text + params.
- Only params declared on `code.Inputs/Outputs` are injected/read. Python inputs are declared `object` / `List<object>`
  (so `None` stays `None`; any `List<T>` marshals to a Python list). C# inputs are strict types (`TypeMap.ClrType`).
- Fresh `RunContext` per run (`# flag:` lines are `Dictionary.Add`-ed into Options). stdout via `RunContextStream`.
- Errors: `ExecuteException.Position.LineNumber` + Python-only `StackTrace`; compile errors via `TryBuild(out Diagnosis)`.
- Text we hand to RhinoCode (never the user's file), always appended so line numbers don't move:
  - **Python epilogue**: converts tuple/set/generator/numpy outputs to lists (otherwise they come back as raw
    `PyObject`) and reports never-assigned outputs via a hidden `__alien_unset__` output.
  - **C#**: RhinoCode pre-declares each output as `object name = default;` → a script's own `var name = …` would be
    CS0128. We parse with Roslyn; outputs the script declares itself (or pass-through pins) are *not* declared as
    params and we append `__context__.Outputs["name"] = name;` after the last top-level statement.
- `sys.modules` is shared across runs, so edited helper modules wouldn't reload: before a Python run we drop modules
  whose `__file__` is in the script folder / repository, but only when a sibling `.py` changed since the last purge.
- C# recompiles `Assembly.Load` new bytes each time; they are never unloaded (small leak per edit, same as McNeel's).

## Settings

`SpecialFolder.ApplicationData/Alien/settings.json`: `%APPDATA%` on Windows; inside Rhino on macOS that resolves to
`~/Library/Application Support` (not `~/.config` as plain .NET would). Keys: `repository`, `port` (default 9876),
`editor` (e.g. `"code"`, `"cursor"`), `openrouterKey`, `model`, `ollamaUrl`, `reasoning`, `imageModel`, `model3d`,
`classifyModel`, `classify` (`"off"`: keywords only), `panel`, `theme` (`{preset, background, text, accent}`, written
by the panel's settings; absent = the Alien preset). The file is re-read when it changes (no Rhino restart) and saved
owner-only (it holds an API key). `ScriptRepository.SettingToken` / `SetSettingToken` read and write non-string keys.

## Node UI and the Alien panel (`src/AlienAttributes.cs`, `src/Ui/`)

- The node is drawn entirely by `AlienAttributes`, controls included. Each input pin's attribute bounds cover only its label,
  so clicks on the control area reach the node's mouse handlers.
  - Slider: press = Capture, move, release = Release. A script whose last solve took under 80 ms follows the slider live;
    a slower one solves once, on release.
  - Values go through `AlienComponent.SetManualValues`, the same store as MCP `set_values`. Undo comes from
    `UndoUtil.RecordGenericObjectEvent`, which serialises the node.
- Text entry (text fields, typed slider values) is `InlineEditor`: a borderless WinForms TextBox added to the canvas
  over the field (Grasshopper's own `GH_TextBoxInputBase` works on Mac too, but brings a ✓/✗ strip). Enter, losing
  focus, any canvas click outside the box or a mouse-wheel scroll commits; Escape cancels.
  - The box grows with its text (measured with `Graphics.MeasureString`) from the field's width up to 460 px or the
    canvas edge; a frame painted in `CanvasPostPaintObjects` outlines it in the accent.
  - Typing used to be lost when another field was clicked: `Show` for the new field called `Close(false)` on the open
    one, and the old box's deferred LostFocus commit could then close the new box. `Show` now commits the open box
    first, and a deferred LostFocus only closes the box it belongs to.
  - The canvas MouseDown and the node's RespondToMouseDown both hear one click, in an order we don't control. A click
    that picked a suggestion is remembered for 400 ms (`InlineEditor.Consumed`), so the node doesn't also treat it as
    a click on the control under the list.
- Theme (`Ui/Theme.cs`): three chosen colours (background, text, accent; a preset or custom, saved as settings
  `theme`), everything else mixed from them (header, IO band, fields, edges, dim text, muted grey; light themes get
  darker status colours and stronger outlines). `Theme.Current` is read on every node repaint, so it re-checks the
  settings at most once a second. The panel gets the theme as CSS variables (`theme.css`) in each state push;
  `Theme.Set` (page message `theme`) saves it, raises `Theme.Changed`, and the canvas redraws.
- The Alien mark (`Ui/Logo.cs`, source `docs/brand/alien-mark.svg`): the SVG path (absolute M, C, Z only) parsed once
  into a `GraphicsPath` and fitted into a box; drawn in the accent before the title in each node's header, in black
  as Grasshopper's component and library icon (`Logo.Icon(24, Color.Black)`). The panel header has the same path as
  inline SVG filled with `var(--accent)`.
- The selected node draws a soft accent glow (rings fading outwards) instead of its shadow. The panel follows the
  selection, so the glow marks the node the panel is showing.
- `NodeControls`: which control an input gets, recommended ranges (header `min`/`max`, else 0..2x the default rounded
  to 1/2/5x10^n; a 0 default gets 0-10), and decimals (at least as many as the default has).
- Header grammar has `options="a|b|c"` → `ParamDef.Options` / `Choices` → a dropdown (a WinForms ContextMenuStrip).
- `button` on a bool → `ParamDef.Button` → `ControlKind.Button`. A click calls `AlienComponent.Pulse(name)`: the name
  goes into `_pulse`, the node repaints ("working…") and `ScheduleSolution(15)` solves; `ReadInput` returns true for a
  pulsed, unwired input; `AfterSolveInstance` clears the pulses once the script has run. Pulses are never stored in
  manual values, saved or undone. `set_values` with `true` pulses too.
- `image` on a str → `ParamDef.Image`. `ImageDrop.Attach` (called from `AlienAttributes.Render`, once per canvas)
  adds DragEnter/DragOver/DragDrop handlers to the GH canvas; a drag over an Alien node with an unwired image input
  (the row under the cursor, else the first) is accepted when it carries an image file (FileDrop) or an http(s) link,
  and the drop sets the input through `SetInput` (undoable). Drops elsewhere are left to Grasshopper. Thumbnails
  (64 px, `Image.FromFile`) are cached per path and file time, failures included. A real OS drag couldn't be simulated
  from a shell (no Accessibility permission): the handler was tested by calling it with a DataObject.
- `suggest="vic-address"` → `ParamDef.Suggest` → `Ui/Suggest.cs` providers. `InlineEditor` debounces typing (220 ms),
  runs the provider on a worker thread and paints the list on the canvas under the field (`CanvasPostPaintObjects`);
  arrow keys + Enter go through the TextBox, clicks through `InlineEditor.HandleClick` (from the node's
  RespondToMouseDown, or the canvas MouseDown where the list sticks out past the node). The box's LostFocus defers
  its commit so a click on the list wins.
- Rhino for Mac's WinForms is partial: a ListBox keeps a zero size whatever SetBounds says, and creating a ListView
  crashed Rhino. TextBox and ContextMenuStrip work. Paint anything else on the canvas.
- While `IsDescribing`, `RespondToMouseDown` ignores control clicks and the controls draw dimmed: the Generator judges
  the first solve after it writes the script, and a user's slider value causing an error there would go back to the model.
- `NODE_CONTROLS` (Python): the epilogue `json.dumps` it into `__alien_controls__`, read back as `RunResult.Controls`,
  parsed into `ControlHint`s on iteration 0 of a successful run (no dict clears them). `NodeControls.Range` takes the
  hint's min/max/step/decimals over the header's; `NodeControls.Current` shows the hint's `value` while there is no
  manual value. `_manualKeys` records the hint key each manual value was set under (saved in the .gh as
  `Manual_i_Key`); `DropStaleValues` (after each solve) clears values whose key differs from the current hint's and
  schedules one more solve. A value set before any key adopts the current one. A manual value equal to the header
  default is dropped, so "0 = as built" never pins a slider at 0.
- Describe from the node: `AlienComponent.StartDescribe` runs `Generator.Run` on a pool thread. Each request is a
  `TranscriptEntry` saved in the .gh ("Transcript", JSON, last 50).
- Describe from a selection: with several nodes selected (or one that isn't an Alien node, or "Build a new node from
  this one" ticked), the panel's describe places a blank node to the right of the selection and passes the selected
  ids as `ContextIds` (in memory, kept for follow-ups). `NodeContext.Read` turns each into a block for the model (id,
  Alien header and description or the component's name and category, every output's data summary, and one building
  record in full); the request asks for a `WIRES: input <- id.output; ...` line after the code. `Prompts.Wires` parses
  it, and `Generator.Apply` adds the wires (matched by short id prefix or nickname, then output nickname or name) after
  the first solve has built the pins, then solves again before judging the run. The MCP `generate` tool takes the
  same context as `context: [ids]`.
- `AlienPanel`: an Eto `Panel` with a `WebView` showing the embedded `Ui/pane.html`.
  - It is registered with `Rhino.UI.Panels.RegisterPanel` under Grasshopper's own Rhino plug-in (`PlugIn.Find`), so no .rhp
    is needed.
  - Page → C#: `window.eto.postMessage(json)` → `MessageReceived`. C# → page: `ExecuteScriptAsync("window.alien.state(json)")`.
  - A `UITimer` pushes state every 0.5 s, only when the JSON changed. It follows `doc.SelectedObjects()`.
  - Default host: `AlienSidecar`, a borderless Eto window glued to the Grasshopper editor. `DocumentEditor.Handle` is the
    editor's NSWindow on macOS, so `addChildWindow:ordered:` makes the sidecar move with it natively. On Windows the
    sidecar is an owned window (`GWLP_HWNDPARENT`), untested so far. A 0.15 s `UITimer` matches the editor's height and
    side (right if the screen has room, else left, else inside its right edge) and hides the sidecar with the editor.
  - Setting `"panel": "rhino"` (⇲ in the page header) docks it as a Rhino panel instead. Rhino opens a never-docked panel
    as a 200 x 200 floating window, so `Open()` resizes it once.
- `Suite`: `suite/*.py` are embedded in the .gha. The first panel push with a repository set calls
  `Suite.InstallIfMissing`: a file never installed is added; a file whose hash still matches the one recorded in
  `.alien/suite.json` (unedited) is updated through `WriteScript` (old copy to history); an edited, unknown or deleted
  file is left alone.
- Library (`Library.cs`): `Library.Nodes()` reads the first 40 lines of every script with a header (every 3 s, or on a
  forced push). Category: `# CATEGORY:` line → `.alien/library.json` entry whose `hash` matches the header's sha1 →
  keyword score (name ×3, description ×2, header ×1). Uncategorised nodes go to the describe model in one background
  request (up to 40 per call, at most one running, retried no sooner than 10 minutes); the `Classified` event makes
  open panels push again. Header comments only are sent, never code.
- Generated assets (`Assets.cs`): `List` scans `.alien/assets/*/*/asset.json` (newest 200) and caches thumbnails as data
  URIs by file time. The panel sends them in a separate `window.alien.assets(list)` call, only when the signature
  (folders + times) changes, because thumbnails make the payload large. Kind and id from the page must match
  `^[a-z0-9_-]{1,40}$` (no paths). Bake: a block definition per asset ("Alien · name · id6"), geometry scaled from the
  3dm's units, one instance at the origin on "Alien assets". Material: the same PBR recipe as material_texture's `add`.
  Place node: `Place(script)` then `PresetManualValues(inputs)`.
- Page-only state (tabs, chips, the picked asset) lives in the page; `tools/panel.py --js "<script>"` runs JavaScript
  in the panel to click through it when testing.
- Help view (`?`): the OpenRouter steps with buttons that post `openUrl` (allow-listed in `AlienUi.OpenUrl`), a key
  box that posts `setKey`, and Check credit / Remove (`testKey`, `removeKey`). `AlienPanel.LinkKey` runs
  `OpenRouterAccount.Check` off the UI thread: GET `/api/v1/key` (401 = refused; limit_remaining, expires_at) and
  `/api/v1/credits` (total_credits − total_usage), then saves the key if OpenRouter accepted it, or if OpenRouter
  couldn't be reached (saved with a note). The result goes to the page as `window.alien.keyStatus(...)`. A pasted key
  is trimmed of spaces, quotes and newlines first. State carries `hasKey` and `keyHint` only. With no key, the page
  shows a banner, and opens Help once by itself (`localStorage alien.helpSeen`).
- Theme colour fields send a change only when the value differs from the current theme: a blur on an unchanged field
  used to resend the colours it last showed (that is how a stale "GitHub Light" came back).
- Panel captures need Rhino in front (a WKWebView in an occluded window captures black). `tools/pane_snapshot.swift`
  renders pane.html off screen with a state saved by `panel.py --state`, so the page can be checked without taking
  focus from the user.
- Project agent (`Ai/Agent.cs`, panel mode "Project agent"): a tool-using chat per Grasshopper file. Each request goes
  to the agent model (settings `agentModel`, default anthropic/claude-sonnet-5.5) with a system prompt (rules + the
  AGENT_GUIDE Workflow, Header and Building records sections + the suite list), the last 8 turns (user text, reply, a
  one-line list of what was done), and fresh context (project memory notes, the `canvas` tool's output with `sel`, the
  document units). Tools are the MCP tools minus run_code, rhino, doc and bake, run through
  `McpServer.Invoke` (the same dispatcher and lock as MCP calls), plus `library` (search), `remember` (project notes)
  and `select` (canvas selection). Captures come back as a user message with image parts (tool results carry text
  only). At most 24 steps a request; Stop cancels. Pressing a `generate*` button needs the user's request to say so
  ("generate", "go ahead", "yes"…). Sessions are saved in `<repo>/.alien/agent/<sha1(key)>.json` (key = the .gh path,
  or the document id until it is saved; an unsaved file's chat moves to its path on the first request after saving).
  `ModelClient.CompleteWithTools` sends OpenAI-style tools through OpenRouter.
- The agent's `building` tool (`Ai/AgentBuilding.cs`) is deterministic C#: one `add` batch (nodes with values + links)
  through `McpServer.Invoke`, then it lays the nodes out in columns by their real sizes, groups them (unique name:
  "House 2"), turns off the element nodes' red previews (walls, doors and windows all output the walls; only the last
  has every opening cut) in favour of one Custom Preview fed geometry/colour pairs in wire order, sets the slabs'
  offset to half the wall type's `total`, and returns a report plus a capture focused on that preview (the image goes
  to the model like a capture's). Footprint: grids `outline`/`edges` (clockwise from the front, exterior on the left,
  as walls reads them). Walls: base `all`, so ids run four a storey (front, left, back, right); windows are two nodes
  (front/back walls by index, then sides) at grid-bay middles, a door in the bay nearest the centre (windows skip
  overlaps with it). Stairs: straight along the back wall when it fits in 18 risers, else U; one stairs node per
  storey pair, stacked (identical straight flights work: the void is only where headroom is short). Type swatches get
  the site's plane, so a group's data stays at the building (capture's group focus). Origin: given, or 10 m past the
  existing previews' bounding box. `edit` reads the group's nodes' manual values back into the tool's arguments,
  lays the change over them, deletes the group and places it again at the same canvas spot and origin, each role
  keeping its other values (a new type drops the old layers; a new size, frame or storey count lets sections and
  door/window positions be recomputed). Blank and zero arguments are dropped first (gpt-6-luna sends every field:
  width 0 made a 3 x 3 m house), and `stairs: false` only counts when the request mentions stairs.
- Agent guardrails (`Agent.cs`): `GenerateGuard` refuses a generate prompt naming a whole building (house, apartments,
  tower …, unless it is massing) or three or more building elements (walls, slabs, roof, stairs, doors, windows,
  frame, grids, levels, balustrades, curtain walls; reading nodes like schedules and labels pass), prompts over 1,200
  characters, and a third generate in one request; the user's own "one node" overrides. `TooMuchCode` refuses
  hand-written nodes over 150 lines; `LooseBuilding` refuses a batch `add` of five or more building suite nodes before
  any building was placed. One new building and three rebuilds a request. The chat history keeps the building's
  role -> id list (`BuildingTool.Brief`), and `canvas` lists named groups before the nodes (the agent's context clips
  long canvases), so "make that roof a gable" finds the right building. Edits keep only the fields the request talks
  about (`EditFields`: luna filled in a guessed 10 x 8 m footprint while asked for a hip roof). A generate prompt that
  refers to the building ("off the back of the house") with no context gets the last building's site, levels and
  grids (`AddBuildingContext`; the session keeps that building's role -> id map).
- Manual values for Curve / Line inputs can be point lists (`Values.CurveFrom`): two points a line, more a polyline,
  model units; a list input takes one curve's points or a list of them. The pick control labels them ("1 curve").
- MCP `add`, `wire`, `delete` and `set_values` record Grasshopper undo events (RecordAddObjectEvent, RecordWireEvent,
  RecordRemoveObjectEvent, RecordGenericObjectEvent), so what the agent (or any MCP client) changes can be undone.
- Picking geometry (`Ui/Picks.cs`): `ControlKind.Pick` for unwired geometry inputs. A click runs Rhino's `GetObject`
  (preselected objects count; `GetMultiple` for lists) or `GetPoint` for a single Point3d, after the mouse handler has
  returned (`InvokeOnUiThread`). The manual value is `{"pick": [guid...]}`; `Values.Resolve` turns it into the objects'
  current geometry, converted to the input's type (extrusions and SubDs to Breps, single-face Breps to Surfaces, Breps to
  meshes…; missing objects skipped). Replace/Delete/Undelete events on picked objects collect into one re-solve on
  Idle. The control label counts objects without converting geometry (it runs on every repaint).
- Suite nodes on the toolbar (`SuiteComponents.cs`): one `SuiteComponent` subclass per suite node (Grasshopper registers
  components by type), panels Create / Site / Building / Views / Entourage / Data on the Alien tab. Each binds to
  `<repo>/suite/<script>.py` in AddedToDocument when it has no script yet (a file or paste brings its own); the
  ComponentGuid is an MD5 of "alien.suite.<script>", so it stays stable; the description is the embedded suite file's
  library line; icons are coloured tiles with a glyph. The plain Alien node stays under Script.
- Library: a Create category (generators) leads; `Library.Essentials` is the "Start here" strip (blank node, 3D object,
  2D object, symbol, material, display mode, views, layout, exploded axo; missing scripts are left out). The second tab
  is the object library: generated kinds plus building types (`alien_assets.TYPE_KINDS`, saved by type nodes' Save
  button through `alien_assets.save_type`: record.json + thumbnail + inputs for Place node; Copy record copies the JSON).
- First run: `ScriptRepository.EnsureDefaultRoot` (on Grasshopper load) sets the script folder to Documents/Alien
  scripts when none was ever chosen, so the first node no longer asks; a chosen folder that's missing is left alone.

## Describe-a-node (`src/Ai/`)

- `ModelClient`: one OpenAI-compatible client. `vendor/model` → OpenRouter (`provider.data_collection = deny`,
  `usage.include` for the real cost, `reasoning.effort` low); `ollama:name` → `{ollamaUrl}/v1/chat/completions` with
  `reasoning_effort: none` (qwen3.x otherwise thinks for minutes and hits the 4-minute timeout).
- `Generator`: the retry loop. The model always returns the whole file; we write it (history snapshot), `Reload()`,
  `Tools.WaitForSolve`, and judge the `Describe.Run` report: error / unset outputs / no outputs / every output empty
  → the report goes back to the model. Progress is shown on the node (`AlienComponent.Activity`).
- `Prompts`: system prompt = rules + AGENT_GUIDE sections (Header, Types, Inputs and outputs, Gotchas, Python|CSharp).
  Follow-ups send the current file plus earlier successful requests for that node (kept in memory only).
- Test from a shell: the MCP `generate` tool runs the same loop (`{"prompt": ..., "model": ...}`).
- Building while Rhino runs: `dotnet build -c Release -p:GH_Libraries=` compiles without trying to deploy.

## MCP

Hand-rolled Streamable HTTP (JSON responses, no SSE) on loopback. `tools/call` failures return `isError: true`
with a message (not JSON-RPC errors) so agents can self-correct. Tool output is compact JSON; keep it that way —
every byte is paid for on every call.

Rhino-side tools (`Mcp/Tools.Rhino.cs`: doc, objects, layers, view, search, rhino find/help) follow the tool set
of McNeel's RhinoAI MCP server (github.com/mcneel/RhinoAI, MIT), folded into fewer tools: list_objects /
get_selection / set_selection / zoom_to_* -> `objects`; set_layer_material -> `layers`; set_camera -> `view`;
get_context / save_doc / open_doc / g1_start + the host and plug-in resources -> `doc`; g1_search_components +
g1_describe_component -> `search`; g1_apply_graph -> batch `add`; g1_connect replace + cycle check -> `wire`;
g1_solve_graph -> `canvas(solve)`; get_commands + the command-help resource -> `rhino(find, help)`.
Not ported: ask_user (their panel), manage_plugin_commands (Rhino 9), close_doc and g1_clear_canvas (destructive).

Viewport gotchas: `RhinoViewport.IsVisible` tests the frustum of the last redraw, so after moving the camera in the
same call use `InView` (camera-plane + screen-rect test). Textures for a newly shown display mode load
asynchronously; `capture` renders once, pumps `RhinoApp.Wait()`, then takes the real shot.

## Rhino API facts (learned building site_views, material_texture, object_3d)

Views and layouts:
- `ViewInfo.Viewport` is read-only, but its getter returns a live, editable viewport. `ViewInfo.ClippingPlanesIds` is
  read-only: `NamedViews.Add(name, viewportId)` records the clipping planes active in that viewport, so give the plane
  to the viewport (`ClippingPlaneGeometry.AddClipViewportId`, `CommitChanges`), save the view, then remove it again.
- A clipping plane keeps the geometry on the side its normal points to. Planes on a hidden layer still clip, so
  site_views hides its planes on an "Alien views" layer.
- Restoring a named view renames the viewport to the view's name: find viewports by object, not by name.
- Layout details: `page.SetActiveDetail(detail.Id)` must come before changing a detail's camera (otherwise every detail
  gets the page's last camera); then `CommitViewportChanges`, `DetailGeometry.SetScale(n, mm, 1, mm)`,
  `IsProjectionLocked`, `CommitChanges`, and `page.SetPageAsActive()` at the end.
- `doc.Views.ActiveView` can be a page or null right after a page closes: fall back to a standard view.
- The display modes available on Mac Rhino 8 include Pen (clean hidden-line drawings: used for the sheet), Technical,
  Arctic, Monochrome, Raytraced. Rhino on Mac won't switch the visible layout tab through the API.

Materials and previews:
- Custom Preview's material input needs `Grasshopper.Kernel.Types.GH_Material(DisplayMaterial)`; a raw DisplayMaterial
  fails with "Data conversion failed from Goo to Shader".
- `DisplayMaterial(Material)` with `Material.SetBitmapTexture(path)` gives a textured preview material.
- PBR render material: `mat.ToPhysicallyBased()`, `mat.PhysicallyBased.SetTexture(tex, TextureType.PBR_BaseColor /
  Bump)`, `RenderMaterial.FromMaterial(mat, doc)`, add it, then for each child `RenderTexture` (walk
  `rm.FirstChild` / `NextSibling`): `BeginChange(Program)`, `SetProjectionMode(WcsBox)`, `SetRepeat(1/tile)` in model
  units, `EndChange()`. Check with `RenderTexture.SimulatedTexture(...)` (`Filename`, `Repeat`).
- Captures of a Rhino that is not the front app don't show display textures or freshly baked objects' shading.

Images inside a solve (thumbnails, texture processing): decode and resize with Eto (`Eto.Drawing.Bitmap(stream)`,
`new Bitmap(src, w, h, ImageInterpolation.High)`), read pixels with `bmp.Lock()` + `Marshal.Copy`, and move byte
arrays between .NET and Python through base64 (much faster than element by element). `Graphics.FillPolygon` needs a
.NET `PointF[]` (`System.Array[ed.PointF](list)`); a same-colour `DrawPolygon` after each triangle hides the hairline
seams anti-aliasing leaves.

### More Rhino API facts (views, layout, display_mode, symbol: 2026-10-14)

- **Layout details clip only after their planes are committed once all pages exist:** after `NamedViews.Restore` into
  details, add each detail to its planes (`AddClipViewportId`) and `CommitChanges` at the end. Committing inside the
  detail loop wasn't enough (site_views commits per detail: unverified whether its details clip).
- **A clipping plane's own depth (`PlaneDepth`) works in model views, not in layout details:** views.py limits a plan's
  depth with a second, back-facing plane.
- **`ChangeToParallelReflectedProjection()` returns False** (RhinoViewport and ViewportInfo), so an RCP is a plain view
  looking up (east and west swap).
- **A named view's frustum must be set on the live viewport** (`ViewportInfo.SetFrustum` -> `vp.SetViewProjection(vpi,
  False)`, then `NamedViews.Add(name, vp.Id)`); changing a copy's frustum and adding it keeps the old frustum. A copy's
  DisplayModeId does stick. `ViewInfo(vp).Name = ...` renames the live viewport.
- **Hidden clipping planes are skipped by `Objects.GetObjectList(ObjectType.ClipPlane)`:** use ObjectEnumeratorSettings
  with HiddenObjects; `FindByUserString` finds hidden objects.
- **Re-making views deletes the planes layout details use:** views.py's info says to press the layout again.
- **Captures of a backgrounded Rhino draw no document objects in Technical or Pen modes** (even stock ones), and
  Grasshopper previews ignore most display-mode settings (only background and lighting apply). Check display modes on
  real document objects with render meshes (`CreateMeshes`), and Technical looks through their .ini.
- **Display modes:** `DisplayModeDescription.CopyDisplayMode` for the first install; updating in place = export the base
  .ini, rewrite id, name and order, `ImportFromFile`. The material colour (Front Material Diffuse,
  OverrideObjectColor) and technical line colours/thickness aren't in RhinoCommon: patch the .ini and re-import.
  `RhinoView.CaptureToBitmap(size, mode)` captures in a mode without changing the viewport's.
- **A non-uniform scale opens closed PolyCurves that contain arcs** (and their planar fills are lost): convert to NURBS
  first (alien_codegen.transform).
- **Font.FromQuartetProperties** gives bold faces for TextEntity text curves (symbol labels).

### Grasshopper facts from the demo definitions (2026-10-15)

- **Textured previews:** `GH_Material(DisplayMaterial)` keeps only the colour (a `DisplayMaterial` made from a
  `Material` does have the bitmap, but the GH wrapper and its duplicates drop it). `GH_Material(RenderMaterial)` is an
  `XmlMaterial` holding the material's RDK XML, texture included, so it survives wires and saved files, and Custom
  Preview shows it (also in captures of a backgrounded Rhino). A WCS-box projection repeat (1/tile in model units)
  that is right on document objects tiles thousands of times over in a GH preview: preview materials use
  MappingChannel with repeat 1 and geometry carries texture coordinates in tiles (`demos/texture_mapping.py`: unweld,
  box-project each face from its normal, position / tile size).
- **Rhino 8's Custom Curve Preview** (Curve, Linetype, Colour; no thickness input) drew nothing in viewport captures;
  the demos draw curves with Custom Preview and a Colour Swatch.
- **vic_site_map's lot and site curves lie at z 0** under a terrain that follows the ground, so they vanish under the
  ground mesh: drape them (`demos/drape_curves.py`, MeshRay down every metre and at each corner).
- **Building definitions from a script:** MCP `add` batches (natives by name or search id, e.g. Line is `4c4e56eb`);
  Colour Swatches are set through `GH_ColourSwatch.SwatchColour` (run_code); scribbles are `GH_Scribble` with a Font;
  `GH_DocumentIO(doc).SaveQuiet(path)` saves. Grasshopper has no unit-aware points, so demo layouts come from a node
  that works in metres (`demos/spots.py`).

## Victorian open data (vic_site_map, alien_vicdata)

Measured on 2026-10-12 from Melbourne:
- **Terrain:** GA `DEM_LiDAR_5m_2025` WCS 1.0.0 (EPSG:4283 only; snap the bbox to the native grid, or the data shifts
  up to ~2.5 m; GA's SRTM services return 403). Vicmap Elevation 10 m DEM: LERC v1 tiles from
  `tiles-ap1.arcgis.com/.../Vicmap_10m_DEM/ImageServer/tile/{z}/{row}/{col}` (z ≤ 15), 0.08–0.29 m RMS against 1 m
  contours in metro Melbourne. Terrarium and Open-Meteo read rooftops and canopy (+2 to +17 m in built-up areas).
- **Trees:** WFS `open-data-platform:tree_urban` (0.2 s; the FeatureServer takes 3–10 s). Points only; no species.
  Rooftop "trees" read roof height; trees planted after the 2017–18 LiDAR read ~0 m.
- **Roads:** Vicmap `tr_road.width_m` is empty for roads; `road_casement_polygon` gives the reserve (property line to
  property line) and reproduces survey widths (20.1 m one-chain streets, 30.2 m Hoddle grid). `parcel_road = 'Y'`
  only returns titled road lots.
- **Street regions:** `Curve.CreateBooleanRegions(curves, WorldXY, false, tol)` on the unioned reserve, carriageway
  and nature-strip bands plus the context box gives every face; classify each face with one interior point. Test
  points against the small original bands through a 10 m grid: `Curve.Contains` on the big unioned curves made a
  400 m site take 7.5 s instead of 3.7 s.
