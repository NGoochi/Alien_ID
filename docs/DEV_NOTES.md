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
`editor` (e.g. `"code"`, `"cursor"`), `openrouterKey`, `model`, `ollamaUrl`, `reasoning`. The file is re-read when it
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
- `Suite`: `suite/*.py` are embedded in the .gha and copied into `<repository>/suite` the first time the panel opens with
  a repository set. Existing files are never overwritten (they may have been edited).

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

