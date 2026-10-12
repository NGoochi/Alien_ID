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
`classifyModel`, `classify` (`"off"`: keywords only), `panel`. The file is re-read when it
changes (no Rhino restart) and saved owner-only (it holds an API key).

## Node UI and the Alien panel (`src/AlienAttributes.cs`, `src/Ui/`)

- The node is drawn entirely by `AlienAttributes`, controls included. Each input pin's attribute bounds cover only its label,
  so clicks on the control area reach the node's mouse handlers.
  - Slider: press = Capture, move, release = Release. A script whose last solve took under 80 ms follows the slider live;
    a slower one solves once, on release.
  - Values go through `AlienComponent.SetManualValues`, the same store as MCP `set_values`. Undo comes from
    `UndoUtil.RecordGenericObjectEvent`, which serialises the node.
- Text entry (text fields, typed slider values) is `InlineEditor`: a borderless WinForms TextBox added to the canvas
  over the field (Grasshopper's own `GH_TextBoxInputBase` works on Mac too, but brings a ✓/✗ strip). Enter or losing
  focus commits, Escape cancels.
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
