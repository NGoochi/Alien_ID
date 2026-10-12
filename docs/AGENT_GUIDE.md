# Alien — agent guide

Served section-by-section by the MCP `docs` tool. Keep sections short and factual.

## Workflow
1. `canvas` — see what exists (ids are 8 chars; a unique nickname also works).
2. `add` — `component: "python" | "csharp"` makes an Alien node + a new script in the user's repository
   (pass `code`, or omit for a starter template). Any other name places a native component
   (`"Number Slider"`, `"Panel"`, `"Custom Preview"`, `"Boolean Toggle"`, …).
3. `wire` — `["a1b2c3d4.points>e5f6a7b8.pts"]`. Sliders/panels need no `.param`.
4. `script_edit` — small `edits` (exact find/replace) beat rewriting the file. The call waits for the re-solve and
   returns `status`, `error` + `line` + `code`, `print`, and a one-line summary per output.
5. `inspect` for detail, `capture` to see geometry, `set_values` to try inputs.
Don't poll: every tool that changes something already returns the new state.

## Header
First 200 lines. Python uses `#`, C# uses `//`. Several `NODE_INPUTS` lines are allowed.
```
# NODE_INPUTS: base:Curve, pts:list[Point3d], grid:tree[float], n:int | "count" min=1 max=50 default=5
# NODE_OUTPUTS: result, log:str | "status text"
```
- Token: `name:type [| "description"] [min= max= step= decimals= default= options="a|b|c"]`. Commas inside quotes are fine.
- Unwired inputs get a control on the node: numbers a slider (give `min`/`max`, else a range is guessed from the default),
  `bool` a switch, `str` a text field, `str` with `options=` a dropdown. Give sensible ranges and defaults: they are the UI.
- `bool` with the word `button` (`go:bool | "build it" button`) is a momentary button: true for one solve when clicked,
  false otherwise, never saved. Use it for anything slow, paid or with side effects (model calls, fetching again,
  writing to the Rhino document), so re-solving never repeats it. Agents press it with `set_values {"go": true}`.
- Access: `list[T]` / `T[]` / `T:list` → list; `tree[T]` → tree (arrives as a list of branch lists); default item.
- `default=` accepts numbers, `true/false`, `"text"`, `[x,y,z]`.
- Names are pin identities: renaming a pin drops its wires. Keep names stable.
- Outputs are generic; a type after `:` is documentation only.
- After the header: `# CATEGORY: Geometry` (library group: Site & context, Massing & objects, Entourage, Materials,
  Views & drawings, Data & lists, Geometry, Analysis, Utilities), then one comment line saying what the node does (the
  library listing). Without a CATEGORY line the library sorts the node itself (model, else keywords).

## Types
`float int bool str Color Interval Point3d Vector3d Plane Line Circle Arc Box Rectangle3d Curve Surface Brep Mesh SubD Geometry Transform object`
Aliases: double/number→float, string/text→str, point→Point3d, vector→Vector3d, domain→Interval, geometrybase→Geometry, colour→Color.
Unknown types become `object` (header warning). Values arrive as RhinoCommon/.NET types, never GH_Goo wrappers.

## Inputs and outputs
- Every declared input is defined when the script runs. Unwired → manual value (`set_values`) → header `default=` →
  Python `None` / `[]`; C# `default(T)` for value types (0, false, Point3d(0,0,0)), `null` otherwise, empty list for lists.
- Outputs are top-level variables named in `NODE_OUTPUTS`. Unassigned outputs are reported as `unset_outputs`.
- A list becomes a GH list; a list of lists becomes a data tree (one branch per sub-list).
- `Polyline` outputs are converted to `PolylineCurve` automatically.
- Item-access inputs fed with lists make Grasshopper run the script once per item (`runs` in the report).
- Python only: set `NODE_CONTROLS = {"width": {"min": 0.35, "max": 1.45, "value": 0.72, "decimals": 2, "key": "..."}}`
  to retune a slider after a run: its range, the value it shows while unset (e.g. "0 = as built" showing the built
  size), and a `key` naming what the value belongs to. When a run reports a different key, values set under the old
  one are cleared and the node solves again. An input set to its header default counts as unset.

## Python
- Real CPython 3.9 (Rhino 8). f-strings, dataclasses, type hints all fine. `#! python 2` opts into legacy IronPython.
- `import Rhino.Geometry as rg`. Prefer RhinoCommon geometry over `rhinoscriptsyntax` (rs adds objects to the document).
- Inputs arrive as Python lists / plain values. Tuples, sets, generators and numpy arrays assigned to outputs are converted to lists.
- Shared helpers: put `helpers.py` in the repository and `import helpers`; edits to it are picked up on the next run.
- Packages: add `# r: numpy` (one per line, first 32 lines); Rhino installs them on first run.
- Globals do not persist between runs (fresh scope each solve).
- `print()` output is returned as `print` in the run report.

## CSharp
- C# 9 script mode: `using` lines, then top-level statements (no class needed). Inputs are typed variables from the
  header (`list[Point3d]` → `List<Point3d>`, `tree[float]` → `List<List<double>>`).
- Outputs: either declare them (`var points = new List<Point3d>();`) or assign them (`log = "done";`) — both work.
- An early top-level `return;` skips writing outputs — use if/else instead.
- C# 9 only: no raw string literals, no `required`, no file-scoped namespaces. Arithmetic is `checked`.
- Extra references go on the first lines: `// r "C:/full/path/to/lib.dll"` (note: `// r`, not `// #r`; an assembly
  *name* alone may not resolve — use the full path) or `// r "nuget: Package, 1.2.3"`.
- Python equivalent: `import clr; clr.AddReference("AssemblyName")` (names of already-loaded plugin assemblies work).
- Plugin APIs that call native code (e.g. BIM plugins) can crash Rhino outright — no exception, no traceback.
  Try them in a scratch document first and save before experimenting.
- `Console.WriteLine` output is returned as `print`. Warm runs are ~1 ms; first compile ~1 s.

## Choosing a language
Python: fastest to iterate, packages (numpy, networkx…), short glue logic. C#: tight numeric loops, large data,
parallelism (`System.Threading.Tasks.Parallel`), strict typing. Nodes can mix freely; wires carry RhinoCommon types either way.

## Gotchas
- Booleans and many RhinoCommon calls return arrays or null — check before indexing.
- `Line` and `Polyline` are not `Curve`; use `LineCurve` / `PolylineCurve` when a Curve is required downstream.
- Tolerance: `Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance`, don't hardcode.
- `RTree.Point3dKNeighbors` returns one `int[]` per needle.
- Big outputs recompute everything downstream; keep counts low while iterating.
- Pass structured data between nodes as lists/trees where possible; JSON strings through `str` pins work but hide structure.
- Plugins that keep named definitions in the document (styles, blocks) usually reuse the stored definition when a
  same-named one is baked again: after changing a style's recipe, give it a new name (or purge the old one).
- Wire by index (`id.2`) when a component has two inputs with the same nickname.
- Scripts that add objects to the Rhino document run on every solve: gate them with a `run:bool` input and make them
  idempotent — tag what you create (e.g. `attributes.SetUserString("alien.owner", key)`) and delete the previous run's
  tagged objects before re-creating, or update in place by name.

## Tools
Grasshopper: canvas · add · wire · delete · search · script_read · script_edit · generate · inspect · set_values · bake.
Rhino: doc · objects · layers · view · capture · rhino · run_code. Reference: docs · repo.
- `search(query)` finds components (all words must match; `category` narrows, e.g. 'VisualARQ'); `detail` lists pins.
  Pass a result's id to `add` when two plugins share a name.
- `add(nodes=[{key, component, values?, x?, y?, ...}], links=['key.out>key.in'])` builds a whole chain in one call;
  links may mix new keys and existing ids. The reply maps keys to ids.
- `wire(replace=true)` swaps an input's sources; links that would make a loop are refused.
- `canvas(solve=true)` recomputes everything and reports error/warning counts.
- `bake(id, param?, layer?)` writes a node's output data into the Rhino document (geometry or plugin objects), on a layer
  that is created if missing. Baked objects are tagged, and re-baking the same node/output replaces the previous bake.
  Materials: give the target layer a render material and baked objects pick it up (by-layer).
- `generate(prompt, id?)` hands a node to the user's configured model (describe-a-node): it writes, runs and fixes the
  script itself. Cheaper than writing it yourself for routine nodes; you still get the run report back.
- `run_code` runs a throwaway snippet (assign `result` to get a value back) — use it to explore APIs instead of guessing.
- `set_values(ids=[...], preview=false)` hides raw previews (e.g. of generator nodes feeding coloured Custom Previews);
  `canvas` marks hidden nodes with `"preview": false`.

## Rhino
- `doc()` first: file, units, tolerance, counts, selection, active view, Grasshopper state, versions.
  `doc(action='save')` saves both documents in place; `save_copy` writes copies (`path` .3dm or any export extension,
  `gh_path` .gh) without switching documents; `import` merges a file; `open_gh` opens a definition; `plugins` lists
  third-party Rhino plug-ins and Grasshopper libraries (what domain tools the user has).
- `objects(layer=..., type=..., name=..., ids=..., selected=true)` lists counts, bbox and short rows
  (`id type layer 'name'`); plugin objects show as `Block:*Wall` etc. `action='select'` / `'zoom'` act on the same filter.
  Object ids are 8-char prefixes, accepted anywhere an object id is.
- `layers()` shows the tree with object counts, flags, colours and materials; `layers(path, visible|locked|current|color|material)`
  creates the layer if needed. `material` is a document material name, a Rhino library search ('Glass brick',
  'Matte Steel', 'Wood floor light' — ambiguous searches list the candidates) or `{color, transparency, gloss}`.
- `view(...)` changes the user's viewport (same options as capture, plus `projection`) without an image.
- `rhino(find='array')` lists command names; `rhino(help='ArrayCrv')` returns the command's help page text
  (McNeel commands only). Scripted commands: dash form + `_Enter`, e.g. `_-Export "C:/x.obj" _Enter`.

## Capture
`capture` costs image tokens: use small sizes and only when geometry must be seen. Options (viewport target):
- `view`: `perspective | iso | top | front | right | left | back | bottom` (zooms extents unless a camera/focus is given)
- `focus: [...]` frames GH nodes' geometry, Rhino object ids or `'layer:Path'`; `camera: [x,y,z]` + `look_at: [x,y,z]` for an exact shot
- `display`: display mode name (`Shaded`, `Rendered`, `Arctic`, `Ghosted`, `Technical`, …; a bad name lists the options)
- `lens` (mm), `grid` (default off), `keep` (default false: the user's view and display mode are restored after the shot)
- `zoom`: `true`/`'extents'`, `'selected'`, or a box `[[x,y,z],[x,y,z]]`
The reply text reports the display mode and camera used, so a good shot can be repeated or kept, and says so when
nothing is in view (with the scene bounding box). Textures are loaded before the shot.
