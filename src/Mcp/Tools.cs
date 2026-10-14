using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Text;
using System.Threading;

using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;

using Newtonsoft.Json.Linq;

using Rhino;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.Geometry;

using Alien.Scripting;

namespace Alien.Mcp
{
    /// <summary>
    /// MCP tool set. Design rules: few tools, each doing a whole step; compact JSON; short ids;
    /// edits block until Grasshopper has re-solved and return the run report (no polling loops).
    /// </summary>
    public static partial class Tools
    {
        public const string Instructions =
@"Alien: Grasshopper nodes backed by script files (Python 3 or C#) in the user's script repository.
Pins come from header comments ('//' instead of '#' in C#):
  # NODE_INPUTS: pts:list[Point3d], n:int | ""count"" min=1 max=50 default=5
  # NODE_OUTPUTS: curves, log
Unwired input -> manual value (set_values) -> header default -> None/[] (C#: default(T)). Outputs are top-level variables with those names; nested lists become data trees.
Loop: canvas -> add (python|csharp|any GH component; nodes+links for a whole chain) -> wire -> script_edit (returns ok/error+line/output summary) -> inspect / capture.
Pick Python for quick iteration and libraries; C# for heavy loops (simulations, 100k+ items) or strict typing.
Rhino side: doc (context, save, import), objects (list/select/zoom), layers (tree, visibility, materials), view (camera, display), rhino (commands, find, help). search finds GH components.
Ids are the 8-char ids from canvas/objects (a unique nickname also works for nodes). docs(topic) has syntax, types, gotchas.";

        private const int WaitTimeoutMs = 30000;
        private static PointF? _lastPlaced;

        public static IEnumerable<Tool> All() => new[]
        {
            new Tool("canvas",
                "Compact graph of the active Grasshopper document: nodes (id, type, name, state, pins; sel = selected by the user) and wires 'src.out>dst.in'.",
                Schema(("filter", Str("'alien', 'errors', or a name substring. Default: everything."), false),
                       ("detail", Bool("Include pins of native components too."), false),
                       ("solve", Bool("Recompute the whole definition first and report errors/warnings."), false)),
                Canvas),

            new Tool("add",
                "Place a node. component='python'|'csharp' creates an Alien node with a new script in the repository (or attaches `script`); any other value places that Grasshopper component by name or search id (e.g. 'Number Slider', 'Panel'; 'Category:Name' when plugins share a name). Returns id, pins and (Alien) the first run report. Whole chains: `nodes` (each {key, component, name?, script?, code?, x?, y?, values?}) plus `links` ('key.out>key.in', keys or ids) in one call.",
                Schema(("component", Str("'python', 'csharp', a Grasshopper component name, or an id from search"), false),
                       ("name", Str("Script file name (Alien) or nickname (native)"), false),
                       ("code", Str("Initial script source (Alien). Omit for a starter template."), false),
                       ("script", Str("Attach an existing script instead (repo-relative or absolute path)."), false),
                       ("x", Num("Canvas x"), false), ("y", Num("Canvas y"), false),
                       ("near", Str("Place to the right of this node id"), false),
                       ("nodes", Arr(AnyObj("{key, component, ...same fields as above, values: set_values payload}"), "Batch: several nodes"), false),
                       ("links", Arr(Str("'key.out>key.in'"), "Batch: wires between the new nodes (keys) and existing ids"), false)),
                Add),

            new Tool("wire",
                "Connect (or disconnect) wires. Each link is 'srcId.outputParam>dstId.inputParam'; the param part may be omitted for single-pin nodes and sliders/panels. Links that would create a loop are refused.",
                Schema(("links", Arr(Str("e.g. 'a1b2c3d4.points>e5f6a7b8.pts'"), "Links to create/remove"), true),
                       ("disconnect", Bool("Remove these wires instead"), false),
                       ("replace", Bool("Drop each target input's existing wires first (links in this call still accumulate)"), false)),
                Wire),

            new Tool("delete", "Remove nodes from the canvas (script files are kept).",
                Schema(("ids", Arr(Str("node id"), "Nodes to delete"), true)),
                Delete),

            new Tool("search",
                "Search the Grasshopper component library: every word must appear in a name, nickname, description or category. Returns 'Name [nick] (Category/Sub) id'; pass the id to add when names clash. detail=true (or a single hit) lists the top hit's inputs and outputs.",
                Schema(("query", Str("Words to match, e.g. 'curtain wall cell'"), true),
                       ("category", Str("Restrict to a tab/category, e.g. 'VisualARQ' or 'Curve/Division'"), false),
                       ("limit", Num("Max results (default 15)"), false),
                       ("detail", Bool("Describe the top hit's pins"), false)),
                Search),

            new Tool("script_read", "Read an Alien node's script. With from/to, returns numbered lines only.",
                Schema(("id", Str("Alien node id"), true), ("from", Num("First line (1-based)"), false), ("to", Num("Last line"), false)),
                ScriptRead),

            new Tool("script_edit",
                "Change an Alien node's script and wait for Grasshopper to re-solve. Either `edits` (exact find/replace, each `old` must match once) or full `code`. Previous version goes to .alien/history. Returns the run report.",
                Schema(("id", Str("Alien node id"), true),
                       ("edits", Arr(Obj(("old", Str("exact existing text"), true), ("new", Str("replacement"), true)), "Find/replace edits, applied in order"), false),
                       ("code", Str("Full new source (replaces the file)"), false)),
                ScriptEdit),

            new Tool("generate",
                "Describe-a-node: a model (OpenRouter, or 'ollama:<name>' locally) writes the script from plain English, runs it and fixes its own errors. Omit id for a new node; on an existing id the request edits its script. Returns attempts, cost and the run report.",
                Schema(("prompt", Str("What the node should do"), true),
                       ("id", Str("Alien node to change (omit to create one)"), false),
                       ("language", Str("'python' (default) or 'csharp', for a new node"), false),
                       ("model", Str("Override settings 'model', e.g. 'deepseek/deepseek-v4.1-flash' or 'ollama:qwen3:8b'"), false),
                       ("attempts", Num("Max attempts (default 4)"), false),
                       ("context", Arr(Str("node id"), "Nodes the new node works with: the model sees what they output and wires them in"), false),
                       ("x", Num("Canvas x"), false), ("y", Num("Canvas y"), false),
                       ("near", Str("Place to the right of this node id"), false)),
                Generate),

            new Tool("inspect",
                "Summarise a node's data: Alien run report + input sources, or any component's outputs (counts, types, ranges, bbox, samples).",
                Schema(("id", Str("node id"), true), ("sample", Num("Sample values per param (default 3)"), false)),
                Inspect),

            new Tool("set_values",
                "Set values and re-solve. Alien: manual values for unwired inputs by name (null clears). Slider: {value,min,max,decimals}. Panel: {text}. Toggle: {value}. Value List: {value: item name}. Any other component: values for its unwired inputs by nickname (arrays = several items). `preview` shows/hides viewport preview of any node(s).",
                Schema(("id", Str("node id"), false), ("values", AnyObj("name -> value"), false),
                       ("preview", Bool("Show (true) or hide (false) the node's viewport preview"), false),
                       ("ids", Arr(Str("node id"), "Several nodes at once (preview only)"), false)),
                SetValues),

            new Tool("run_code",
                "Run a throwaway Python 3 or C# snippet inside Rhino (API exploration, document queries). Returns printed output and the value of `result` if assigned.",
                Schema(("language", Str("'python' or 'csharp'"), true), ("code", Str("source"), true)),
                RunCode),

            new Tool("capture",
                "PNG of the Rhino viewport (default) or the Grasshopper canvas. Camera/display options are the same as `view` but apply to this capture only; the user's view is restored unless keep=true.",
                Schema(("target", Str("'viewport' (default) or 'canvas'"), false),
                       ("width", Num("px, default 800"), false), ("height", Num("px, default 500"), false),
                       ("view", Str("'perspective', 'iso', 'top', 'front', 'right', 'left', 'back' or 'bottom'"), false),
                       ("focus", Arr(Str("node id, Rhino object id or 'layer:Path'"), "Frame these"), false),
                       ("camera", Arr(Num("coordinate"), "Camera position [x,y,z]"), false),
                       ("look_at", Arr(Num("coordinate"), "Camera target [x,y,z]"), false),
                       ("zoom", Any("true/'extents', 'selected', or a box [[x,y,z],[x,y,z]] (extents is the default when `view` is given without camera/focus)"), false),
                       ("display", Str("Display mode, e.g. 'Shaded', 'Rendered', 'Arctic', 'Ghosted', 'Wireframe', 'Technical'"), false),
                       ("lens", Num("Perspective lens length (mm)"), false),
                       ("grid", Bool("Draw grid and axes (default false)"), false),
                       ("keep", Bool("Leave the viewport at the new camera/display afterwards"), false)),
                Capture),

            new Tool("view",
                "Set the active Rhino viewport without capturing (persistent): view preset, camera/look_at, lens, projection, display mode, zoom or focus. Returns the resulting camera.",
                Schema(("view", Str("'perspective', 'iso', 'top', 'front', 'right', 'left', 'back' or 'bottom'"), false),
                       ("camera", Arr(Num("coordinate"), "Camera position [x,y,z]"), false),
                       ("look_at", Arr(Num("coordinate"), "Camera target [x,y,z]"), false),
                       ("lens", Num("Perspective lens length (mm)"), false),
                       ("projection", Str("'parallel' or 'perspective'"), false),
                       ("display", Str("Display mode name"), false),
                       ("zoom", Any("true/'extents', 'selected', or a box [[x,y,z],[x,y,z]]"), false),
                       ("focus", Arr(Str("node id, Rhino object id or 'layer:Path'"), "Frame these"), false)),
                View),

            new Tool("objects",
                "Rhino document objects. Filter by ids (8-char or full), layer (includes sublayers), type, name (substring), selected, hidden. action 'list' (default: counts by type/layer, bbox, first `limit` rows 'id type layer name'), 'select' (replaces the selection) or 'zoom' (frames them in the active view).",
                Schema(("action", Str("'list' (default), 'select' or 'zoom'"), false),
                       ("ids", Arr(Str("object id"), "Objects by id"), false),
                       ("layer", Str("Layer full path, e.g. 'Walls' or 'Extension::Timber'"), false),
                       ("type", Str("point, pointcloud, curve, surface, brep, extrusion, mesh, subd, block, annotation, hatch, light"), false),
                       ("name", Str("Object name contains"), false),
                       ("selected", Bool("Only the current selection"), false),
                       ("hidden", Bool("Include hidden objects"), false),
                       ("limit", Num("Rows to list (default 30)"), false)),
                Objects),

            new Tool("layers",
                "Rhino layers. Without `path`: the layer tree with object counts, flags and materials. With `path`: creates the layer if missing, then applies visible, locked, current, color, material (a document material name, a Rhino material-library search like 'Glass brick' or 'Matte Steel', or {color, transparency, gloss}).",
                Schema(("path", Str("Layer full path 'A::B::C'"), false),
                       ("filter", Str("List only layers whose path contains this"), false),
                       ("visible", Bool("Show/hide"), false), ("locked", Bool("Lock/unlock"), false),
                       ("current", Bool("Make it the current layer"), false),
                       ("color", Str("Layer colour '#RRGGBB' or a colour name"), false),
                       ("material", Any("Material name / library search string, or {color, transparency 0-1, gloss 0-1, name?}"), false)),
                Layers),

            new Tool("doc",
                "Rhino + Grasshopper documents. action 'info' (default: file, units, counts, selection, active view, GH document, versions), 'save' (both documents in place), 'save_copy' (`path`: .3dm copy or export by extension, `gh_path`: .gh copy; documents keep their own paths), 'import' (merge a file into the Rhino document), 'open_gh' (open `path`, or the editor with a blank document), 'plugins' (third-party Rhino plug-ins and Grasshopper libraries).",
                Schema(("action", Str("'info', 'save', 'save_copy', 'import', 'open_gh' or 'plugins'"), false),
                       ("path", Str("Absolute file path"), false),
                       ("gh_path", Str("save_copy: absolute .gh path"), false)),
                Doc),

            new Tool("rhino", "Run a Rhino command and return what it printed; without arguments, return recent command-line history. find: command names containing text. help: the command's help page from docs.mcneel.com.",
                Schema(("command", Str("e.g. '_-Export \"C:/out.obj\" _Enter'"), false), ("lines", Num("History lines when no command (default 20)"), false),
                       ("find", Str("List command names containing this, e.g. 'va' or 'array'"), false),
                       ("help", Str("Command name to look up, e.g. 'ArrayCrv'"), false)),
                RhinoCommand),

            new Tool("bake",
                "Bake a node's output data into the Rhino document (anything bakeable: geometry, plugin objects). Objects are tagged; by default the node's previous bake is deleted first, so re-baking replaces instead of duplicating.",
                Schema(("id", Str("node id"), true),
                       ("param", Str("Output name (default: all outputs; for sliders/params: the node itself)"), false),
                       ("layer", Str("Target layer full path, e.g. 'Model::Walls' (created if missing; default current layer)"), false),
                       ("replace", Bool("Delete this node/param's previous bake first (default true)"), false)),
                Bake),

            new Tool("docs", "Alien reference. Call with no topic for the topic list.",
                Schema(("topic", Str("Topic name"), false)),
                Docs),

            new Tool("repo", "Script repository: show root and scripts, or set the root folder. library=true: nodes by category with descriptions, plus generated assets.",
                Schema(("set", Str("New repository folder (created if missing)"), false), ("library", Bool("Nodes by category + generated assets"), false)),
                Repo),
        };

        // ── canvas ──────────────────────────────────────────────────

        private static ToolResult Canvas(JObject a)
        {
            var filter = a.Value<string>("filter");
            bool detail = a.Value<bool?>("detail") ?? false;
            bool solve = a.Value<bool?>("solve") ?? false;
            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                string solved = null;
                if (solve)
                {
                    var sw = System.Diagnostics.Stopwatch.StartNew();
                    doc.NewSolution(true);
                    var active = doc.Objects.OfType<IGH_ActiveObject>().ToList();
                    solved = $"{sw.ElapsedMilliseconds} ms; {active.Count(o => o.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error)} errors, " +
                             $"{active.Count(o => o.RuntimeMessageLevel == GH_RuntimeMessageLevel.Warning)} warnings";
                }
                var objs = doc.Objects.Where(o => o is IGH_Component || o is IGH_Param).ToList();
                var picked = objs.Where(o => Matches(o, filter)).Take(300).ToList();
                var ids = new HashSet<Guid>(picked.Select(o => o.InstanceGuid));

                var nodes = new JArray();
                foreach (var o in picked)
                {
                    var n = new JObject { ["id"] = Describe.ShortId(o) };
                    if (o is AlienComponent al)
                    {
                        n["type"] = "alien";
                        n["lang"] = ScriptLanguages.Tag(al.Language);
                        n["script"] = al.ScriptPath == null ? null : ScriptRepository.ToStored(al.ScriptPath).Replace("repo:", "");
                        n["state"] = al.LastRun.Status;
                        n["in"] = new JArray(al.Params.Input.Select(p => p.NickName));
                        n["out"] = new JArray(al.Params.Output.Select(p => p.NickName));
                    }
                    else
                    {
                        n["type"] = o.Name;
                        if (!string.IsNullOrEmpty(o.NickName) && o.NickName != o.Name) n["name"] = o.NickName;
                        var v = Describe.SpecialValue(o);
                        if (v != null) n["value"] = v;
                        if (o is IGH_Component c && detail)
                        {
                            n["in"] = new JArray(c.Params.Input.Select(p => p.NickName));
                            n["out"] = new JArray(c.Params.Output.Select(p => p.NickName));
                        }
                    }
                    if (o is IGH_PreviewObject pv && pv.IsPreviewCapable && pv.Hidden) n["preview"] = false;
                    if (o is IGH_ActiveObject act && act.RuntimeMessageLevel != GH_RuntimeMessageLevel.Blank)
                    {
                        var lvl = act.RuntimeMessageLevel;
                        var msg = act.RuntimeMessages(lvl).FirstOrDefault();
                        n["msg"] = $"{(lvl == GH_RuntimeMessageLevel.Error ? "error" : lvl.ToString().ToLowerInvariant())}: {Trim(msg, 160)}";
                    }
                    n["at"] = new JArray((int)o.Attributes.Pivot.X, (int)o.Attributes.Pivot.Y);
                    if (o.Attributes.Selected) n["sel"] = true;
                    nodes.Add(n);
                }

                var wires = new JArray();
                foreach (var o in picked)
                    foreach (var input in Inputs(o))
                        foreach (var src in input.Sources)
                        {
                            var srcObj = src.Attributes?.GetTopLevel?.DocObject;
                            if (srcObj == null || !ids.Contains(srcObj.InstanceGuid)) continue;
                            wires.Add($"{Endpoint(srcObj, src)}>{Endpoint(o, input)}");
                        }

                var result = new JObject
                {
                    ["doc"] = doc.DisplayName,
                    ["repo"] = ScriptRepository.Root,
                    ["nodes"] = nodes,
                    ["wires"] = wires
                };
                if (solved != null) result["solved"] = solved;
                if (objs.Count > picked.Count) result["shown"] = $"{picked.Count}/{objs.Count}";
                return result;
            }));
        }

        private static bool Matches(IGH_DocumentObject o, string filter)
        {
            if (string.IsNullOrWhiteSpace(filter)) return true;
            if (filter == "alien") return o is AlienComponent;
            if (filter == "errors") return o is IGH_ActiveObject a && a.RuntimeMessageLevel != GH_RuntimeMessageLevel.Blank;
            return (o.NickName ?? "").Contains(filter, StringComparison.OrdinalIgnoreCase)
                || (o.Name ?? "").Contains(filter, StringComparison.OrdinalIgnoreCase);
        }

        private static IEnumerable<IGH_Param> Inputs(IGH_DocumentObject o) => o switch
        {
            IGH_Component c => c.Params.Input,
            IGH_Param p => new[] { p },
            _ => Enumerable.Empty<IGH_Param>()
        };

        private static string Endpoint(IGH_DocumentObject owner, IGH_Param p) =>
            owner is IGH_Param ? Describe.ShortId(owner) : $"{Describe.ShortId(owner)}.{p.NickName}";

        // ── add ─────────────────────────────────────────────────────

        private static ToolResult Add(JObject a) =>
            ToolResult.Json(a["nodes"] is JArray nodes ? AddBatch(nodes, a["links"] as JArray) : AddOne(a));

        /// <summary>Place several nodes (each like a single add, plus optional `values`), then wire them by key.</summary>
        private static JObject AddBatch(JArray nodes, JArray links)
        {
            var keys = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            var placed = new JObject();
            var aliens = new List<string>();
            foreach (var n in nodes.OfType<JObject>())
            {
                var key = n.Value<string>("key") ?? n.Value<string>("name") ?? $"n{placed.Count + 1}";
                try
                {
                    var r = AddOne(n);
                    var id = r.Value<string>("id");
                    keys[key] = id;
                    if (r["status"] != null) aliens.Add(id);
                    string note = "";
                    if (n["values"] is JObject vals && vals.Count > 0)
                    {
                        try { SetValues(new JObject { ["id"] = id, ["values"] = vals }); }
                        catch (Exception ex) { note = $" (values: {ex.Message})"; }
                    }
                    placed[key] = id + note;
                }
                catch (Exception ex) { placed[key] = "error: " + ex.Message; }
            }

            var result = new JObject { ["nodes"] = placed };
            if (links != null && links.Count > 0)
            {
                string Map(string end)
                {
                    int dot = end.IndexOf('.');
                    var head = dot > 0 ? end.Substring(0, dot) : end;
                    return keys.TryGetValue(head.Trim(), out var id) ? id + (dot > 0 ? end.Substring(dot) : "") : end;
                }
                var mapped = links.Select(l => l.ToString().Split('>')).Select(p => p.Length == 2 ? $"{Map(p[0])}>{Map(p[1])}" : string.Join(">", p)).ToList();
                result["links"] = WireCore(mapped, disconnect: false, replace: false)["results"];
            }
            // Final state of the new Alien nodes (after values and wiring), not their first, unwired run.
            if (aliens.Count > 0)
                result["alien"] = UiThread.Run(() =>
                {
                    var doc = RequireDoc(false);
                    return new JArray(aliens.Select(id => Resolve(doc, id)).OfType<AlienComponent>().Select(al => Describe.Run(al, 1)));
                });
            return result;
        }

        private static JObject AddOne(JObject a)
        {
            var component = Req(a, "component");
            var lang = ScriptLanguages.Parse(component);
            if (component.Equals("alien", StringComparison.OrdinalIgnoreCase)) lang = ScriptLanguage.Python3;

            if (lang != ScriptLanguage.None)
            {
                var (node, stamp) = UiThread.Run(() =>
                {
                    var doc = RequireDoc(true);
                    string path;
                    var script = a.Value<string>("script");
                    if (!string.IsNullOrWhiteSpace(script))
                    {
                        path = ScriptRepository.Resolve(script);
                        if (!File.Exists(path)) throw new ToolException($"Script not found: {path}");
                    }
                    else
                    {
                        if (!ScriptRepository.IsConfigured)
                            throw new ToolException("Script repository not set. Ask the user for a folder, then call repo(set=...).");
                        path = ScriptRepository.CreateScript(a.Value<string>("name"), lang, a.Value<string>("code"));
                    }

                    var n = new AlienComponent { SuppressRepositoryPrompt = true };
                    n.CreateAttributes();
                    n.Attributes.Pivot = Placement(doc, a, false);
                    doc.AddObject(n, false);
                    int s = n.SolveStamp;
                    n.SetScript(path);
                    return (n, s);
                });
                WaitForSolve(node, stamp);
                return UiThread.Run(() =>
                {
                    var r = Describe.Run(node);
                    r["script"] = ScriptRepository.ToStored(node.ScriptPath).Replace("repo:", "");
                    r["in"] = Describe.Pins(node);
                    return r;
                });
            }

            return UiThread.Run(() =>
            {
                var doc = RequireDoc(true);
                var proxy = FindProxy(component);
                var obj = proxy.CreateInstance();
                obj.CreateAttributes();
                obj.Attributes.Pivot = Placement(doc, a, obj is IGH_Param);
                var nick = a.Value<string>("name");
                if (!string.IsNullOrWhiteSpace(nick)) obj.NickName = nick;
                doc.AddObject(obj, false);
                (obj as IGH_ActiveObject)?.ExpireSolution(true);

                var r = new JObject { ["id"] = Describe.ShortId(obj), ["type"] = obj.Name };
                if (obj is IGH_Component c)
                {
                    r["in"] = new JArray(c.Params.Input.Select(p => p.NickName));
                    r["out"] = new JArray(c.Params.Output.Select(p => p.NickName));
                }
                var v = Describe.SpecialValue(obj);
                if (v != null) r["value"] = v;
                return r;
            });
        }

        /// <summary>Component by name, proxy Guid or 8+ char proxy id (from search); "Category:Name" (or "Category/Subcategory:Name") restricts to one plugin/tab.</summary>
        private static IGH_ObjectProxy FindProxy(string name)
        {
            var key = name.Trim().Replace("-", "").ToLowerInvariant();
            if (Guid.TryParse(name.Trim(), out var pg) || (key.Length >= 8 && key.Length <= 32 && key.All(Uri.IsHexDigit)))
            {
                var byId = Instances.ComponentServer.ObjectProxies.Where(p => pg != Guid.Empty ? p.Guid == pg
                    : p.Guid.ToString("N").StartsWith(key, StringComparison.Ordinal)).ToList();
                if (byId.Count == 1) return byId[0];
                if (byId.Count > 1) throw new ToolException($"Component id '{name}' is ambiguous — use more characters.");
            }

            var proxies = Instances.ComponentServer.ObjectProxies
                .Where(p => !p.Obsolete && p.Exposure != GH_Exposure.hidden).ToList();
            int colon = name.LastIndexOf(':');
            if (colon > 0)
            {
                var scope = name.Substring(0, colon).Trim();
                name = name.Substring(colon + 1).Trim();
                proxies = proxies.Where(p =>
                    (p.Desc.Category ?? "").Contains(scope, StringComparison.OrdinalIgnoreCase) ||
                    ($"{p.Desc.Category}/{p.Desc.SubCategory}").Contains(scope, StringComparison.OrdinalIgnoreCase)).ToList();
                if (proxies.Count == 0) throw new ToolException($"No component category matches '{scope}'.");
            }
            // Rank exact matches: a name beats a nickname ("Circle" is not "Circle 3Pt", whose nickname is Circle), a
            // component beats a same-named parameter (the Circle component, not the empty Circle param), core beats plugins.
            bool ByName(IGH_ObjectProxy p) => p.Desc.Name.Equals(name, StringComparison.OrdinalIgnoreCase);
            bool IsParam(IGH_ObjectProxy p) => p.Type != null && typeof(IGH_Param).IsAssignableFrom(p.Type);
            bool IsCore(IGH_ObjectProxy p) => p.Location?.EndsWith("Grasshopper.dll", StringComparison.OrdinalIgnoreCase) == true;
            var exact = proxies.Where(p => ByName(p) || p.Desc.NickName.Equals(name, StringComparison.OrdinalIgnoreCase))
                .OrderBy(p => ByName(p) ? 0 : 1).ThenBy(p => IsParam(p) ? 1 : 0).ThenBy(p => IsCore(p) ? 0 : 1).ToList();
            if (exact.Count > 0)
            {
                var best = exact[0];
                if (IsCore(best)) return best;
                var ties = exact.Where(p => ByName(p) == ByName(best) && IsParam(p) == IsParam(best)).ToList();
                if (ties.Select(p => p.Desc.Category).Distinct().Count() > 1)
                    throw new ToolException($"'{name}' exists in several plugins — use 'Category:Name': " +
                        string.Join(", ", ties.Select(p => $"{p.Desc.Category}:{p.Desc.Name}").Distinct()));
                return best;
            }

            var fuzzy = proxies.Where(p => p.Desc.Name.Contains(name, StringComparison.OrdinalIgnoreCase)).Take(8).ToList();
            if (fuzzy.Count == 1) return fuzzy[0];
            throw new ToolException(fuzzy.Count == 0
                ? $"No Grasshopper component named '{name}'. Try search."
                : $"'{name}' is ambiguous: {string.Join(", ", fuzzy.Select(p => $"{p.Desc.Name} ({p.Desc.Category}/{p.Desc.SubCategory})"))}");
        }

        private static PointF Placement(GH_Document doc, JObject a, bool isSource)
        {
            var x = a.Value<float?>("x");
            var y = a.Value<float?>("y");
            if (x.HasValue && y.HasValue) return new PointF(x.Value, y.Value); // explicit: trust the agent

            PointF pt;
            if (a.Value<string>("near") is string near)
            {
                var o = Resolve(doc, near);
                // Value sources (sliders, panels, toggles) go left of the node they feed; everything else goes right.
                pt = isSource
                    ? new PointF(o.Attributes.Bounds.Left - 130, o.Attributes.Bounds.Top + 20)
                    : new PointF(o.Attributes.Bounds.Right + 130, o.Attributes.Pivot.Y);
            }
            else if (_lastPlaced.HasValue && doc.Objects.Count > 0) pt = new PointF(_lastPlaced.Value.X + 220, _lastPlaced.Value.Y);
            else if (Instances.ActiveCanvas != null) pt = Instances.ActiveCanvas.Viewport.MidPoint;
            else pt = new PointF(100, 100);

            pt = FreeSpot(doc, pt);
            if (!isSource) _lastPlaced = pt;
            return pt;
        }

        /// <summary>Nudge down until a ~180x70 footprint around the pivot overlaps nothing.</summary>
        private static PointF FreeSpot(GH_Document doc, PointF pt)
        {
            for (int i = 0; i < 60; i++)
            {
                var probe = new RectangleF(pt.X - 90, pt.Y - 35, 180, 70);
                if (!doc.Objects.Any(o => o.Attributes != null && o.Attributes.Bounds.IntersectsWith(probe))) return pt;
                pt = new PointF(pt.X, pt.Y + 50);
            }
            return pt;
        }

        // ── wire / delete ───────────────────────────────────────────

        private static ToolResult Wire(JObject a) =>
            ToolResult.Json(WireCore(ReqArray(a, "links"), a.Value<bool?>("disconnect") ?? false, a.Value<bool?>("replace") ?? false));

        private static JObject WireCore(List<string> links, bool disconnect, bool replace)
        {
            var touched = new List<AlienComponent>();
            var stamps = new Dictionary<AlienComponent, int>();

            var report = UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                var results = new JArray();
                var targets = new HashSet<IGH_DocumentObject>();
                var cleared = new HashSet<Guid>();
                foreach (var link in links)
                {
                    try
                    {
                        var parts = link.Split('>');
                        if (parts.Length != 2) throw new ToolException("expected 'src.param>dst.param'");
                        var (srcObj, src) = ResolveParam(doc, parts[0].Trim(), output: true);
                        var (dstObj, dst) = ResolveParam(doc, parts[1].Trim(), output: false);
                        if (disconnect) dst.RemoveSource(src);
                        else
                        {
                            if (WouldCycle(srcObj, dstObj)) throw new ToolException("would create a loop (the source already depends on the target)");
                            if (replace && cleared.Add(dst.InstanceGuid) && dst.SourceCount > 0) dst.RemoveAllSources();
                            if (!dst.Sources.Contains(src)) dst.AddSource(src);
                        }
                        targets.Add(dstObj);
                        results.Add("ok");
                    }
                    catch (Exception ex) { results.Add($"{link}: {ex.Message}"); }
                }
                foreach (var t in targets)
                {
                    if (t is AlienComponent al) { touched.Add(al); stamps[al] = al.SolveStamp; }
                    (t as IGH_ActiveObject)?.ExpireSolution(false);
                }
                doc.NewSolution(false);
                return results;
            });

            foreach (var al in touched) WaitForSolve(al, stamps[al]);
            var r = new JObject { ["results"] = report };
            if (touched.Count > 0)
                r["alien"] = UiThread.Run(() => new JArray(touched.Select(n => Describe.Run(n, 1))));
            return r;
        }

        /// <summary>Wiring src into dst closes a loop when src already depends on dst: walk upstream from src.</summary>
        private static bool WouldCycle(IGH_DocumentObject src, IGH_DocumentObject dst)
        {
            var seen = new HashSet<Guid>();
            var pending = new Queue<IGH_DocumentObject>();
            pending.Enqueue(src);
            while (pending.Count > 0)
            {
                var current = pending.Dequeue();
                if (current.InstanceGuid == dst.InstanceGuid) return true;
                if (!seen.Add(current.InstanceGuid)) continue;
                foreach (var input in Inputs(current))
                    foreach (var source in input.Sources)
                        pending.Enqueue(source.Attributes?.GetTopLevel?.DocObject ?? source);
            }
            return false;
        }

        private static ToolResult Delete(JObject a)
        {
            var ids = ReqArray(a, "ids");
            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                var objs = ids.Select(id => Resolve(doc, id)).ToList();
                doc.RemoveObjects(objs, false);
                doc.NewSolution(false);
                return new JObject { ["deleted"] = objs.Count };
            }));
        }

        // ── scripts ─────────────────────────────────────────────────

        private static ToolResult ScriptRead(JObject a)
        {
            var node = UiThread.Run(() => RequireAlien(Req(a, "id")));
            var path = node.ScriptPath ?? throw new ToolException("Node has no script.");
            var lines = File.ReadAllText(path).Replace("\r\n", "\n").Split('\n');
            int? from = a.Value<int?>("from"), to = a.Value<int?>("to");
            if (from == null && to == null)
                return ToolResult.Json(new JObject
                {
                    ["path"] = path,
                    ["lang"] = ScriptLanguages.Tag(node.Language),
                    ["lines"] = lines.Length,
                    ["text"] = string.Join("\n", lines)
                });

            int f = Math.Max(1, from ?? 1), t = Math.Min(lines.Length, to ?? lines.Length);
            var sb = new StringBuilder();
            for (int i = f; i <= t; i++) sb.Append(i).Append("| ").Append(lines[i - 1]).Append('\n');
            return ToolResult.Text(sb.ToString());
        }

        private static ToolResult ScriptEdit(JObject a)
        {
            var node = UiThread.Run(() => RequireAlien(Req(a, "id")));
            var path = node.ScriptPath ?? throw new ToolException("Node has no script — use add(script=...) or add a new node.");

            string code = a.Value<string>("code");
            var edits = a["edits"] as JArray;
            if (code == null && (edits == null || edits.Count == 0)) throw new ToolException("Give `edits` or `code`.");

            if (code == null)
            {
                code = File.ReadAllText(path).Replace("\r\n", "\n");
                int i = 0;
                foreach (var e in edits)
                {
                    i++;
                    var oldText = (e.Value<string>("old") ?? "").Replace("\r\n", "\n");
                    var newText = (e.Value<string>("new") ?? "").Replace("\r\n", "\n");
                    if (oldText.Length == 0) throw new ToolException($"edit {i}: `old` is empty");
                    int count = CountOccurrences(code, oldText);
                    if (count != 1)
                        throw new ToolException($"edit {i}: `old` matched {count} times (must be exactly 1). Nothing was written.");
                    int at = code.IndexOf(oldText, StringComparison.Ordinal);
                    code = code.Substring(0, at) + newText + code.Substring(at + oldText.Length);
                }
            }

            int stamp = UiThread.Run(() =>
            {
                ScriptRepository.WriteScript(path, code);
                int s = node.SolveStamp;
                node.Reload();
                return s;
            });
            WaitForSolve(node, stamp);
            return ToolResult.Json(UiThread.Run(() =>
            {
                var r = Describe.Run(node);
                r["in"] = Describe.Pins(node);
                return r;
            }));
        }

        private static int CountOccurrences(string text, string find)
        {
            int n = 0, i = 0;
            while ((i = text.IndexOf(find, i, StringComparison.Ordinal)) >= 0) { n++; i += find.Length; }
            return n;
        }

        // ── inspect / set_values ────────────────────────────────────

        private static ToolResult Inspect(JObject a)
        {
            int sample = a.Value<int?>("sample") ?? 3;
            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                var obj = Resolve(doc, Req(a, "id"));
                if (obj is AlienComponent al)
                {
                    var r = Describe.Run(al, sample);
                    r["script"] = al.ScriptPath;
                    r["in"] = Describe.Pins(al);
                    if (al.LastRun.Status != "ok")
                    {
                        var outs = new JObject();
                        foreach (var p in al.Params.Output) outs[p.NickName] = Describe.ParamData(p, sample);
                        r["out"] = outs;
                    }
                    return r;
                }

                var res = new JObject { ["id"] = Describe.ShortId(obj), ["type"] = obj.Name };
                if (obj is IGH_ActiveObject act && act.RuntimeMessageLevel != GH_RuntimeMessageLevel.Blank)
                    res["messages"] = new JArray(act.RuntimeMessages(act.RuntimeMessageLevel).Take(5).Select(m => Trim(m, 300)));
                if (obj is IGH_Component c)
                {
                    var ins = new JObject();
                    foreach (var p in c.Params.Input) ins[p.NickName] = p.SourceCount > 0 || p.VolatileDataCount > 0 ? Describe.ParamData(p, sample) : "empty";
                    res["in"] = ins;
                    var outs = new JObject();
                    foreach (var p in c.Params.Output) outs[p.NickName] = Describe.ParamData(p, sample);
                    res["out"] = outs;
                }
                else if (obj is IGH_Param p)
                {
                    var v = Describe.SpecialValue(obj);
                    if (v != null) res["value"] = v;
                    res["data"] = Describe.ParamData(p, sample);
                }
                return res;
            }));
        }

        private static ToolResult SetValues(JObject a)
        {
            var ids = new List<string>();
            if (!string.IsNullOrWhiteSpace(a.Value<string>("id"))) ids.Add(a.Value<string>("id"));
            if (a["ids"] is JArray more) ids.AddRange(more.Select(t => t.ToString()));
            if (ids.Count == 0) throw new ToolException("Give `id` (or `ids` for preview).");

            var values = a["values"] as JObject;
            bool? preview = a.Value<bool?>("preview");
            if (values == null && preview == null) throw new ToolException("Give `values` and/or `preview`.");
            if (values != null && ids.Count > 1) throw new ToolException("`values` applies to a single `id`.");

            string previewNote = preview.HasValue ? UiThread.Run(() => SetPreview(ids, preview.Value)) : null;
            if (values == null) return ToolResult.Json(new JObject { ["ok"] = true, ["note"] = previewNote });

            AlienComponent alien = null;
            int stamp = 0;

            var msg = UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                var obj = Resolve(doc, ids[0]);
                switch (obj)
                {
                    case AlienComponent al:
                        alien = al;
                        stamp = al.SolveStamp;
                        var unknown = al.SetManualValues(values.Properties().ToDictionary(p => p.Name, p => p.Value));
                        return unknown.Count > 0 ? $"unknown inputs ignored: {string.Join(", ", unknown)}" : null;

                    case GH_NumberSlider s:
                        if (values["decimals"] != null) s.Slider.DecimalPlaces = (int)values["decimals"];
                        if (values["min"] != null) s.Slider.Minimum = (decimal)(double)values["min"];
                        if (values["max"] != null) s.Slider.Maximum = (decimal)(double)values["max"];
                        if (values["value"] != null) s.SetSliderValue((decimal)(double)values["value"]);
                        s.ExpireSolution(true);
                        return $"slider = {Describe.SpecialValue(s)}";

                    case GH_Panel pn:
                        pn.UserText = values.Value<string>("text") ?? values.Value<string>("value") ?? "";
                        pn.ExpireSolution(true);
                        return "panel set";

                    case GH_BooleanToggle t:
                        t.Value = values.Value<bool>("value");
                        t.ExpireSolution(true);
                        return $"toggle = {t.Value}";

                    case GH_ValueList vl:
                        var want = values.Value<string>("value");
                        var idx = vl.ListItems.FindIndex(i => i.Name == want || i.Expression == want);
                        if (idx < 0) throw new ToolException($"Value list has no item '{want}'. Items: {string.Join(", ", vl.ListItems.Select(i => i.Name))}");
                        vl.SelectItem(idx);
                        vl.ExpireSolution(true);
                        return $"value list = {want}";

                    case IGH_Component comp:
                        // Any other component: store values on unwired inputs (like right-click > Set, internalised).
                        var notes = new List<string>();
                        foreach (var prop in values.Properties())
                        {
                            var p = comp.Params.Input.FirstOrDefault(x => x.NickName.Equals(prop.Name, StringComparison.OrdinalIgnoreCase))
                                 ?? comp.Params.Input.FirstOrDefault(x => x.Name.Equals(prop.Name, StringComparison.OrdinalIgnoreCase));
                            if (p == null) { notes.Add($"no input '{prop.Name}' (has: {string.Join(", ", comp.Params.Input.Select(x => x.NickName))})"); continue; }
                            if (p.SourceCount > 0) { notes.Add($"'{p.NickName}' is wired, skipped"); continue; }
                            var setter = p.GetType().GetMethod("SetPersistentData", new[] { typeof(object[]) });
                            if (setter == null) { notes.Add($"'{p.NickName}' cannot store values"); continue; }
                            var items = prop.Value is JArray arr ? arr.Select(ToClr).ToArray() : new[] { ToClr(prop.Value) };
                            // SetPersistentData appends to whatever is stored (incl. the component's defaults): clear first.
                            var stored = p.GetType().GetProperty("PersistentData")?.GetValue(p);
                            stored?.GetType().GetMethod("Clear", Type.EmptyTypes)?.Invoke(stored, null);
                            setter.Invoke(p, new object[] { items });
                            notes.Add($"{p.NickName}={prop.Value.ToString(Newtonsoft.Json.Formatting.None)}");
                        }
                        comp.ExpireSolution(true);
                        return string.Join("; ", notes);

                    default:
                        throw new ToolException($"set_values supports components, sliders, panels, toggles and value lists — not {obj.Name}.");
                }
            });

            var note = string.Join("; ", new[] { msg, previewNote }.Where(s => s != null));
            if (alien == null) return ToolResult.Json(new JObject { ["ok"] = true, ["note"] = note });
            WaitForSolve(alien, stamp);
            return ToolResult.Json(UiThread.Run(() =>
            {
                var r = Describe.Run(alien);
                if (note.Length > 0) r["note"] = note;
                r["in"] = Describe.Pins(alien);
                return r;
            }));
        }

        private static object ToClr(JToken t) => t.Type switch
        {
            JTokenType.Integer => (object)(int)t,
            JTokenType.Float => (double)t,
            JTokenType.Boolean => (bool)t,
            JTokenType.String => (string)t,
            JTokenType.Null => null,
            _ => t.ToString(Newtonsoft.Json.Formatting.None)
        };

        private static string SetPreview(List<string> ids, bool on)
        {
            var doc = RequireDoc(false);
            var changed = new List<string>();
            var skipped = new List<string>();
            foreach (var id in ids)
            {
                var obj = Resolve(doc, id);
                if (obj is not IGH_PreviewObject po || !po.IsPreviewCapable) { skipped.Add($"{id} ({obj.Name})"); continue; }
                po.Hidden = !on;
                changed.Add(Describe.ShortId(obj));
            }
            if (changed.Count == 0) throw new ToolException($"None of these nodes has a viewport preview: {string.Join(", ", skipped)}");
            doc.ExpirePreview(true);
            Instances.RedrawCanvas();
            RhinoDoc.ActiveDoc?.Views.Redraw();
            return $"preview {(on ? "on" : "off")}: {string.Join(", ", changed)}"
                + (skipped.Count > 0 ? $" (no preview, skipped: {string.Join(", ", skipped)})" : "");
        }

        // ── run_code / capture / rhino ──────────────────────────────

        private static ToolResult RunCode(JObject a)
        {
            var lang = ScriptLanguages.Parse(Req(a, "language"));
            if (lang == ScriptLanguage.None) throw new ToolException("language must be 'python' or 'csharp'");
            var code = Req(a, "code");
            return ToolResult.Json(UiThread.Run(() =>
            {
                var r = ScriptRunner.RunSnippet(lang, code);
                var o = new JObject { ["ok"] = r.Success, ["ms"] = Math.Round(r.Ms, 1) };
                if (!string.IsNullOrWhiteSpace(r.Stdout)) o["print"] = Trim(r.Stdout.Trim(), 4000);
                if (r.Outputs.TryGetValue("result", out var res) && res != null) o["result"] = Describe.Value(res, 5);
                if (!r.Success)
                {
                    o["error"] = r.Error;
                    if (r.Line.HasValue) o["line"] = r.Line;
                    if (!string.IsNullOrEmpty(r.Trace)) o["trace"] = Trim(r.Trace, 1500);
                }
                return o;
            }));
        }

        private static ToolResult Capture(JObject a)
        {
            var target = a.Value<string>("target") ?? "viewport";
            int w = Math.Clamp(a.Value<int?>("width") ?? 800, 100, 2000);
            int h = Math.Clamp(a.Value<int?>("height") ?? 500, 100, 2000);

            string label = $"{target} {w}x{h}";
            var png = UiThread.Run(() =>
            {
                Bitmap bmp;
                if (target == "canvas")
                {
                    var canvas = Instances.ActiveCanvas ?? throw new ToolException("Grasshopper canvas is not open.");
                    var full = new Bitmap(canvas.Width, canvas.Height);
                    canvas.DrawToBitmap(full, new Rectangle(0, 0, canvas.Width, canvas.Height));
                    bmp = Fit(full, w, h);
                }
                else
                {
                    bmp = CaptureViewport(a, w, h, out var info);
                    label += info;
                }
                using (bmp)
                using (var ms = new MemoryStream())
                {
                    bmp.Save(ms, ImageFormat.Png);
                    return Convert.ToBase64String(ms.ToArray());
                }
            });
            return ToolResult.Text(label).AddImage(png);
        }

        /// <summary>Apply view/camera/display options, capture, then restore the user's viewport unless keep=true.</summary>
        private static Bitmap CaptureViewport(JObject a, int w, int h, out string info)
        {
            var view = RhinoDoc.ActiveDoc?.Views.ActiveView ?? throw new ToolException("No active Rhino view.");
            var vp = view.ActiveViewport;
            var savedProjection = new ViewportInfo(vp);
            var savedTarget = vp.CameraTarget;
            var savedMode = vp.DisplayMode;
            bool keep = a.Value<bool?>("keep") ?? false;

            try
            {
                bool changed = ApplyViewOptions(vp, a);
                view.Redraw();

                bool grid = a.Value<bool?>("grid") ?? false;
                var vc = new ViewCapture
                {
                    Width = w,
                    Height = h,
                    ScaleScreenItems = false,
                    DrawAxes = grid,
                    DrawGrid = grid,
                    DrawGridAxes = grid,
                    TransparentBackground = false
                };
                if (changed)
                {
                    // Textures for a newly shown display mode / camera load asynchronously: render once to request
                    // them, let Rhino process the loads, then take the real shot.
                    vc.CaptureToBitmap(view)?.Dispose();
                    for (int i = 0; i < 8; i++) { RhinoApp.Wait(); Thread.Sleep(25); }
                }
                var bmp = vc.CaptureToBitmap(view) ?? view.CaptureToBitmap(new Size(w, h))
                          ?? throw new ToolException("Viewport capture failed.");

                var c = vp.CameraLocation;
                var t = vp.CameraTarget;
                info = $" {vp.DisplayMode?.EnglishName} camera {Num3(c)} -> {Num3(t)}{(keep ? " (kept)" : "")}{EmptyViewNote(view)}";
                return bmp;
            }
            finally
            {
                if (!keep)
                {
                    vp.SetViewProjection(savedProjection, true);
                    vp.SetCameraTarget(savedTarget, false);   // orbit pivot, not just direction
                    if (savedMode != null) vp.DisplayMode = savedMode;
                    view.Redraw();
                }
            }
        }

        private static void ApplyProjection(RhinoViewport vp, string name)
        {
            DefinedViewportProjection? p = name.Trim().ToLowerInvariant() switch
            {
                "top" or "plan" => DefinedViewportProjection.Top,
                "bottom" => DefinedViewportProjection.Bottom,
                "front" => DefinedViewportProjection.Front,
                "back" => DefinedViewportProjection.Back,
                "left" => DefinedViewportProjection.Left,
                "right" => DefinedViewportProjection.Right,
                "perspective" or "persp" or "iso" or "isometric" or "axo" => DefinedViewportProjection.Perspective,
                _ => null
            };
            if (p == null) throw new ToolException("view must be one of: perspective, iso, top, front, right, left, back, bottom.");
            vp.SetProjection(p.Value, vp.Name, false);
            if (name.StartsWith("iso", StringComparison.OrdinalIgnoreCase) || name.StartsWith("axo", StringComparison.OrdinalIgnoreCase))
            {
                var tgt = vp.CameraTarget;
                vp.SetCameraLocations(tgt, tgt + new Vector3d(1, -1, 0.8) * Math.Max(1, vp.CameraLocation.DistanceTo(tgt)));
            }
        }

        /// <summary>
        /// Camera/display options shared by capture and view: view preset, display, lens, projection, focus,
        /// camera/look_at, zoom. Returns true when the look changed enough that textures may still be loading.
        /// </summary>
        private static bool ApplyViewOptions(RhinoViewport vp, JObject a)
        {
            string preset = a.Value<string>("view");
            var camera = ParsePoint(a["camera"], "camera");
            var lookAt = ParsePoint(a["look_at"], "look_at");
            var focus = a["focus"] is JArray fa ? fa.Select(t => t.ToString()).ToList()
                      : a.Value<string>("focus") is string fs ? new List<string> { fs } : null;
            bool changed = false;

            if (!string.IsNullOrWhiteSpace(preset)) { ApplyProjection(vp, preset); changed = true; }

            if (a.Value<string>("display") is string display)
            {
                var mode = DisplayModeDescription.FindByName(display)
                    ?? DisplayModeDescription.GetDisplayModes().FirstOrDefault(m =>
                           m.EnglishName.Equals(display, StringComparison.OrdinalIgnoreCase) ||
                           m.LocalName.Equals(display, StringComparison.OrdinalIgnoreCase))
                    ?? throw new ToolException($"Unknown display mode '{display}'. Available: {string.Join(", ", DisplayModeDescription.GetDisplayModes().Select(m => m.EnglishName))}");
                if (vp.DisplayMode?.Id != mode.Id) changed = true;
                vp.DisplayMode = mode;
            }

            switch (a.Value<string>("projection")?.Trim().ToLowerInvariant())
            {
                case null: case "": break;
                case "parallel": vp.ChangeToParallelProjection(true); break;
                case "perspective": vp.ChangeToPerspectiveProjection(true, vp.Camera35mmLensLength > 0 ? vp.Camera35mmLensLength : 50); break;
                default: throw new ToolException("projection must be 'parallel' or 'perspective'.");
            }

            if (a.Value<double?>("lens") is double lens && vp.IsPerspectiveProjection)
                vp.Camera35mmLensLength = Math.Clamp(lens, 1, 10000);

            var frame = focus != null ? FocusBox(focus) : BoundingBox.Unset;
            if (camera.HasValue || lookAt.HasValue)
            {
                var tgt = lookAt ?? (frame.IsValid ? frame.Center : vp.CameraTarget);
                var loc = camera ?? vp.CameraLocation;
                if (loc.DistanceTo(tgt) < 1e-9) throw new ToolException("camera and look_at are the same point.");
                vp.SetCameraLocations(tgt, loc);
                changed = true;
            }

            var zoom = a["zoom"];
            if (frame.IsValid) { vp.ZoomBoundingBox(frame); changed = true; }
            else if (zoom is JArray box) { vp.ZoomBoundingBox(new BoundingBox(ParsePoint(box[0], "zoom[0]").Value, ParsePoint(box[1], "zoom[1]").Value)); changed = true; }
            else if (zoom != null && zoom.Type == JTokenType.String && zoom.ToString().Equals("selected", StringComparison.OrdinalIgnoreCase))
            {
                if (!vp.ZoomExtentsSelected()) throw new ToolException("Nothing is selected.");
                changed = true;
            }
            else if ((zoom != null && (zoom.Type == JTokenType.Boolean ? (bool)zoom : zoom.ToString().Equals("extents", StringComparison.OrdinalIgnoreCase)))
                     || (preset != null && !camera.HasValue && !lookAt.HasValue)) { vp.ZoomExtents(); changed = true; }
            return changed;
        }

        /// <summary>" | nothing in view ..." when no document object or GH preview is on screen, so a blank capture explains itself.</summary>
        private static string EmptyViewNote(RhinoView view)
        {
            var rdoc = view.Document;
            var vp = view.ActiveViewport;
            if (rdoc == null) return "";
            var scene = BoundingBox.Empty;
            int total = 0;
            foreach (var o in rdoc.Objects.GetObjectList(new ObjectEnumeratorSettings { VisibleFilter = true, HiddenObjects = false }))
            {
                var bb = o.Geometry?.GetBoundingBox(false) ?? BoundingBox.Unset;
                if (!bb.IsValid) continue;
                if (InView(vp, bb)) return "";
                scene.Union(bb);
                total++;
            }
            var gh = UiThread.Document;
            if (gh != null)
                foreach (var p in gh.Objects.OfType<IGH_PreviewObject>())
                    if (p.IsPreviewCapable && !p.Hidden && p.ClippingBox is var cb && cb.IsValid)
                    {
                        if (InView(vp, cb)) return "";
                        scene.Union(cb);
                        total++;
                    }
            return total == 0 ? " | nothing visible in the document"
                : $" | nothing in view ({total} visible objects/previews); scene bbox {Num3(scene.Min)} -> {Num3(scene.Max)}";
        }

        /// <summary>
        /// Box overlaps the viewport's current camera (RhinoViewport.IsVisible uses the frustum of the last redraw,
        /// so it misses camera changes made in the same call).
        /// </summary>
        private static bool InView(RhinoViewport vp, BoundingBox bb)
        {
            var corners = bb.GetCorners();
            if (vp.IsPerspectiveProjection)
            {
                var dir = vp.CameraDirection;
                dir.Unitize();
                int front = corners.Count(c => (c - vp.CameraLocation) * dir > 0);
                if (front == 0) return false;            // entirely behind the camera
                if (front < corners.Length) return true;  // straddles the camera plane
            }
            var xf = vp.GetTransform(CoordinateSystem.World, CoordinateSystem.Screen);
            double x0 = double.MaxValue, y0 = double.MaxValue, x1 = double.MinValue, y1 = double.MinValue;
            foreach (var c in corners)
            {
                var p = c;
                p.Transform(xf);
                x0 = Math.Min(x0, p.X); y0 = Math.Min(y0, p.Y); x1 = Math.Max(x1, p.X); y1 = Math.Max(y1, p.Y);
            }
            var size = vp.Size;
            return x1 >= 0 && x0 <= size.Width && y1 >= 0 && y0 <= size.Height;
        }

        /// <summary>
        /// Box around: GH node ids (geometry flowing through them), Rhino object ids (8+ hex or full), or 'layer:Path'.
        /// </summary>
        private static BoundingBox FocusBox(List<string> ids)
        {
            var box = BoundingBox.Empty;
            var gh = UiThread.Document;
            var rdoc = RhinoDoc.ActiveDoc;
            foreach (var raw in ids)
            {
                var id = raw.Trim();
                BoundingBox b = BoundingBox.Unset;
                if (id.StartsWith("layer:", StringComparison.OrdinalIgnoreCase))
                {
                    if (rdoc == null) throw new ToolException("No Rhino document.");
                    b = BoundingBox.Empty;
                    foreach (var o in ObjectsOnLayer(rdoc, id.Substring(6).Trim()))
                        if (o.Geometry != null) b.Union(o.Geometry.GetBoundingBox(true));
                    if (!b.IsValid) throw new ToolException($"Nothing on layer '{id.Substring(6).Trim()}'.");
                }
                else
                {
                    IGH_DocumentObject node = null;
                    if (gh != null) { try { node = Resolve(gh, id); } catch (ToolException) { } }
                    if (node != null)
                    {
                        b = Describe.DataBounds(node);
                        if (!b.IsValid && node is IGH_PreviewObject po) b = po.ClippingBox;
                    }
                    else if (rdoc != null && FindRhinoObject(rdoc, id) is RhinoObject ro && ro.Geometry != null) b = ro.Geometry.GetBoundingBox(true);
                    else throw new ToolException($"'{id}' is not a node, object id or 'layer:Path'.");
                }
                if (b.IsValid) box.Union(b);
            }
            if (!box.IsValid) throw new ToolException("Nothing to frame: those carry no geometry.");
            return box;
        }

        private static Point3d? ParsePoint(JToken t, string name)
        {
            if (t == null || t.Type == JTokenType.Null) return null;
            if (t is JArray arr && arr.Count >= 3) return new Point3d((double)arr[0], (double)arr[1], (double)arr[2]);
            throw new ToolException($"`{name}` must be [x, y, z].");
        }

        private static string Num3(Point3d p) => FormattableString.Invariant($"{p.X:0.#},{p.Y:0.#},{p.Z:0.#}");

        private static Bitmap Fit(Bitmap src, int maxW, int maxH)
        {
            double s = Math.Min(1.0, Math.Min((double)maxW / src.Width, (double)maxH / src.Height));
            if (s >= 1.0) return src;
            var dst = new Bitmap((int)(src.Width * s), (int)(src.Height * s));
            using (var g = Graphics.FromImage(dst))
            {
                g.InterpolationMode = System.Drawing.Drawing2D.InterpolationMode.HighQualityBicubic;
                g.DrawImage(src, 0, 0, dst.Width, dst.Height);
            }
            src.Dispose();
            return dst;
        }

        private static ToolResult RhinoCommand(JObject a)
        {
            var command = a.Value<string>("command");
            int lines = a.Value<int?>("lines") ?? 20;
            if (a.Value<string>("find") is string find && find.Trim().Length > 0)
            {
                var term = find.Trim().TrimStart('_', '-', '!');
                var names = UiThread.Run(() => global::Rhino.Commands.Command.GetCommandNames(true, false))
                    .Where(n => n.Contains(term, StringComparison.OrdinalIgnoreCase))
                    .OrderBy(n => n.StartsWith(term, StringComparison.OrdinalIgnoreCase) ? 0 : 1).ThenBy(n => n, StringComparer.OrdinalIgnoreCase).ToList();
                var r = new JObject { ["count"] = names.Count, ["commands"] = string.Join(", ", names.Take(120)) };
                if (names.Count > 120) r["more"] = names.Count - 120;
                return ToolResult.Json(r);
            }
            if (a.Value<string>("help") is string help && help.Trim().Length > 0)
                return ToolResult.Text(CommandHelp(help.Trim().TrimStart('_', '-', '!')));

            return ToolResult.Json(UiThread.Run(() =>
            {
                var before = RhinoApp.CommandHistoryWindowText ?? "";
                if (string.IsNullOrWhiteSpace(command))
                {
                    var tail = before.Replace("\r\n", "\n").Split('\n').Where(l => l.Length > 0).TakeLast(lines);
                    return new JObject { ["history"] = string.Join("\n", tail) };
                }
                // RunScript during an active command feeds the text to that command's prompt instead of running it.
                if (global::Rhino.Commands.Command.InCommand())
                    throw new ToolException("Rhino is busy with another command. Ask the user to finish or press Esc, then retry.");
                bool ran = RhinoApp.RunScript(command, true);
                var after = RhinoApp.CommandHistoryWindowText ?? "";
                var delta = after.StartsWith(before, StringComparison.Ordinal) ? after.Substring(before.Length) : after;
                return new JObject { ["ran"] = ran, ["output"] = Trim(delta.Trim(), 3000) };
            }));
        }

        // ── docs / repo ─────────────────────────────────────────────

        private const string BakeKey = "alien.bake";

        private static ToolResult Bake(JObject a)
        {
            var paramName = a.Value<string>("param");
            var layer = a.Value<string>("layer");
            bool replace = a.Value<bool?>("replace") ?? true;

            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireDoc(false);
                var rdoc = RhinoDoc.ActiveDoc ?? throw new ToolException("No Rhino document.");
                var obj = Resolve(doc, Req(a, "id"));

                List<IGH_Param> sources = obj switch
                {
                    IGH_Component c when paramName != null => new List<IGH_Param> { ResolveParam(doc, $"{Req(a, "id")}.{paramName}", output: true).param },
                    IGH_Component c => c.Params.Output.ToList(),
                    IGH_Param p => new List<IGH_Param> { p },
                    _ => throw new ToolException($"{obj.Name} has no data to bake.")
                };

                int layerIndex = string.IsNullOrWhiteSpace(layer) ? rdoc.Layers.CurrentLayerIndex : EnsureLayer(rdoc, layer);
                var result = new JObject();
                int total = 0, skipped = 0, removed = 0, invalid = 0;
                var perParam = new JObject();
                foreach (var p in sources)
                {
                    var tag = $"{Describe.ShortId(obj)}/{p.NickName}";
                    if (replace)
                        foreach (var old in rdoc.Objects.FindByUserString(BakeKey, tag, true) ?? Array.Empty<RhinoObject>())
                            if (rdoc.Objects.Delete(old, true)) removed++;

                    int n = 0, data = 0;
                    if (p.VolatileData != null)
                        foreach (var goo in p.VolatileData.AllData(true))
                        {
                            var att = rdoc.CreateDefaultAttributes();
                            att.LayerIndex = layerIndex;
                            att.SetUserString(BakeKey, tag);
                            // Geometry is added here, not through Grasshopper's bake: that stops on invalid meshes with a
                            // modal "Invalid geometry" dialog, which freezes Rhino (and this server) until someone clicks.
                            if ((goo as Grasshopper.Kernel.Types.IGH_GeometricGoo)?.ScriptVariable() is GeometryBase geo)
                            {
                                if (!geo.IsValid && geo is Mesh mesh)
                                {
                                    mesh = mesh.DuplicateMesh();
                                    mesh.Faces.CullDegenerateFaces();
                                    mesh.Vertices.CombineIdentical(true, true);
                                    mesh.Compact();
                                    geo = mesh;
                                }
                                if (!geo.IsValid) { invalid++; continue; }
                                if (rdoc.Objects.Add(geo, att) != Guid.Empty) n++;
                                else skipped++;
                                continue;
                            }
                            if (goo is IGH_BakeAwareData bakeable && bakeable.BakeGeometry(rdoc, att, out Guid gid))
                            {
                                if (gid != Guid.Empty) n++;
                                else data++;   // baked into document data (e.g. a plugin's building/level table), not an object
                            }
                            else skipped++;
                        }
                    total += n;
                    perParam[p.NickName] = data > 0 ? $"{n} objects, {data} non-object (document data, not tagged/replaceable)" : (JToken)n;
                }
                rdoc.Views.Redraw();
                result["baked"] = total;
                result["per_output"] = perParam;
                if (skipped > 0) result["not_bakeable"] = skipped;
                if (invalid > 0) result["invalid_skipped"] = invalid;
                if (removed > 0) result["replaced"] = removed;
                result["layer"] = rdoc.Layers[layerIndex].FullPath;
                return result;
            }));
        }

        /// <summary>Find or create a layer by full path ("A::B::C"). Returns its index.</summary>
        private static int EnsureLayer(RhinoDoc rdoc, string fullPath)
        {
            int idx = rdoc.Layers.FindByFullPath(fullPath, -1);
            if (idx >= 0) return idx;
            Guid parent = Guid.Empty;
            string path = "";
            foreach (var part in fullPath.Split(new[] { "::" }, StringSplitOptions.RemoveEmptyEntries))
            {
                path = path.Length == 0 ? part : path + "::" + part;
                idx = rdoc.Layers.FindByFullPath(path, -1);
                if (idx < 0)
                {
                    var l = new Layer { Name = part, ParentLayerId = parent };
                    idx = rdoc.Layers.Add(l);
                    if (idx < 0) throw new ToolException($"Could not create layer '{path}'.");
                }
                parent = rdoc.Layers[idx].Id;
            }
            return idx;
        }

        private static ToolResult Docs(JObject a)
        {
            var guide = LoadGuide();
            var sections = guide.Split(new[] { "\n## " }, StringSplitOptions.None).Skip(1)
                .Select(s => (title: s.Split('\n')[0].Trim(), body: "## " + s.Trim())).ToList();
            var topic = a.Value<string>("topic");
            if (string.IsNullOrWhiteSpace(topic))
                return ToolResult.Text("Topics: " + string.Join(", ", sections.Select(s => s.title.ToLowerInvariant())));
            var hit = sections.FirstOrDefault(s => s.title.Contains(topic, StringComparison.OrdinalIgnoreCase));
            return hit.body != null ? ToolResult.Text(hit.body)
                : ToolResult.Error("Unknown topic. Topics: " + string.Join(", ", sections.Select(s => s.title.ToLowerInvariant())));
        }

        private static string _guide;
        internal static string LoadGuide()
        {
            if (_guide != null) return _guide;
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("Alien.AGENT_GUIDE.md");
            if (s == null) return _guide = "";
            using var r = new StreamReader(s);
            return _guide = r.ReadToEnd().Replace("\r\n", "\n");
        }

        private static ToolResult Repo(JObject a)
        {
            var set = a.Value<string>("set");
            if (!string.IsNullOrWhiteSpace(set)) ScriptRepository.SetRoot(set);
            var r = new JObject { ["root"] = ScriptRepository.Root };
            if (a.Value<bool?>("library") == true)
            {
                var groups = new JObject();
                foreach (var n in Library.Nodes().OfType<JObject>().OrderBy(n => Array.FindIndex(Library.Categories, c => c.Key == n.Value<string>("category"))))
                {
                    var label = Library.Categories.First(c => c.Key == n.Value<string>("category")).Label;
                    if (!(groups[label] is JArray list)) groups[label] = list = new JArray();
                    list.Add(n.Value<string>("path") + (n.Value<string>("description") is string d ? " — " + d : ""));
                }
                r["nodes"] = groups;
                r["assets"] = new JArray(Assets.List(out _).OfType<JObject>().Take(100)
                    .Select(x => $"{x.Value<string>("kind")}/{x.Value<string>("id")}: {x.Value<string>("name")}" + (x.Value<string>("size") is string sz ? $" ({sz})" : "")));
                return ToolResult.Json(r);
            }
            var scripts = ScriptRepository.ListScripts().ToList();
            r["scripts"] = new JArray(scripts.Take(300));
            if (scripts.Count > 300) r["more"] = scripts.Count - 300;
            return ToolResult.Json(r);
        }

        // ── shared helpers ──────────────────────────────────────────

        /// <summary>Block (off the UI thread) until the node has finished a solution after `stamp`, including any pin rebuild.</summary>
        internal static void WaitForSolve(AlienComponent node, int stamp)
        {
            var until = DateTime.UtcNow.AddMilliseconds(WaitTimeoutMs);
            while (DateTime.UtcNow < until)
            {
                bool done = UiThread.Run(() =>
                {
                    var doc = node.OnPingDocument();
                    if (doc == null) return true;
                    if (!doc.Enabled || node.Locked) return true;  // solver or node disabled — report as-is
                    return node.SolveStamp > stamp && !node.IsRebuilding && node.LastRun.Status != "rebuilding"
                        && doc.SolutionState != GH_ProcessStep.Process;
                });
                if (done) return;
                Thread.Sleep(40);
            }
        }

        private static GH_Document RequireDoc(bool create)
        {
            var canvas = Instances.ActiveCanvas;
            if (canvas == null && create)
            {
                (RhinoApp.GetPlugInObject("Grasshopper") as Grasshopper.Plugin.GH_RhinoScriptInterface)?.ShowEditor();
                canvas = Instances.ActiveCanvas;
            }
            if (canvas == null) throw new ToolException("The Grasshopper editor is not open (any `add` call opens it).");
            if (canvas.Document == null)
            {
                if (!create) throw new ToolException("No Grasshopper document is open.");
                var doc = new GH_Document();
                Instances.DocumentServer.AddDocument(doc);
                canvas.Document = doc;
            }
            return canvas.Document;
        }

        private static AlienComponent RequireAlien(string id)
        {
            var obj = Resolve(RequireDoc(false), id);
            return obj as AlienComponent ?? throw new ToolException($"{id} is a {obj.Name}, not an Alien node.");
        }

        /// <summary>Full GUID, 8+ char id prefix, or a unique nickname.</summary>
        internal static IGH_DocumentObject Resolve(GH_Document doc, string id)
        {
            id = (id ?? "").Trim();
            if (Guid.TryParse(id, out var g))
                return doc.FindObject(g, true) ?? throw new ToolException($"No node {id}.");

            var key = id.Replace("-", "").ToLowerInvariant();
            var byId = key.Length >= 4 && key.All(Uri.IsHexDigit)
                ? doc.Objects.Where(o => o.InstanceGuid.ToString("N").StartsWith(key, StringComparison.Ordinal)).ToList()
                : new List<IGH_DocumentObject>();
            if (byId.Count == 1) return byId[0];
            if (byId.Count > 1) throw new ToolException($"Id '{id}' is ambiguous.");

            var byName = doc.Objects.Where(o => string.Equals(o.NickName, id, StringComparison.OrdinalIgnoreCase)).ToList();
            if (byName.Count == 1) return byName[0];
            throw new ToolException(byName.Count > 1 ? $"Nickname '{id}' is ambiguous — use the id." : $"No node '{id}'. Call canvas for ids.");
        }

        private static (IGH_DocumentObject owner, IGH_Param param) ResolveParam(GH_Document doc, string spec, bool output)
        {
            string id = spec, pname = null;
            int dot = spec.IndexOf('.');
            if (dot > 0) { id = spec.Substring(0, dot); pname = spec.Substring(dot + 1); }

            var obj = Resolve(doc, id);
            if (obj is IGH_Param p) return (obj, p);
            if (obj is not IGH_Component c) throw new ToolException($"{id} has no parameters.");

            var list = output ? c.Params.Output : c.Params.Input;
            if (pname == null)
            {
                if (list.Count == 1) return (obj, list[0]);
                throw new ToolException($"{id} has several {(output ? "outputs" : "inputs")}: {string.Join(", ", list.Select(x => x.NickName))}");
            }
            var match = list.FirstOrDefault(x => x.NickName.Equals(pname, StringComparison.OrdinalIgnoreCase))
                     ?? list.FirstOrDefault(x => x.Name.Equals(pname, StringComparison.OrdinalIgnoreCase))
                     ?? (int.TryParse(pname, out var idx) && idx >= 0 && idx < list.Count ? list[idx] : null);
            return match != null ? (obj, match)
                : throw new ToolException($"{id} has no {(output ? "output" : "input")} '{pname}'. Has: {string.Join(", ", list.Select(x => x.NickName))}");
        }

        private static string Req(JObject a, string key)
        {
            var v = a.Value<string>(key);
            if (string.IsNullOrWhiteSpace(v)) throw new ToolException($"`{key}` is required.");
            return v;
        }

        private static List<string> ReqArray(JObject a, string key)
        {
            var t = a[key];
            if (t is JArray arr && arr.Count > 0) return arr.Select(x => x.ToString()).ToList();
            if (t != null && t.Type == JTokenType.String) return new List<string> { t.ToString() };
            throw new ToolException($"`{key}` must be a non-empty array.");
        }

        private static string Trim(string s, int max) =>
            string.IsNullOrEmpty(s) ? s : s.Length > max ? s.Substring(0, max) + "..." : s;

        // ── JSON schema helpers ─────────────────────────────────────

        private static JObject Schema(params (string name, JObject schema, bool required)[] props) => Obj(props);

        private static JObject Obj(params (string name, JObject schema, bool required)[] props)
        {
            var o = new JObject { ["type"] = "object", ["properties"] = new JObject(props.Select(p => new JProperty(p.name, p.schema))) };
            var req = props.Where(p => p.required).Select(p => p.name).ToArray();
            if (req.Length > 0) o["required"] = new JArray(req);
            return o;
        }

        private static JObject Str(string d) => new JObject { ["type"] = "string", ["description"] = d };
        private static JObject Num(string d) => new JObject { ["type"] = "number", ["description"] = d };
        private static JObject Bool(string d) => new JObject { ["type"] = "boolean", ["description"] = d };
        private static JObject Arr(JObject items, string d) => new JObject { ["type"] = "array", ["items"] = items, ["description"] = d };
        private static JObject AnyObj(string d) => new JObject { ["type"] = "object", ["description"] = d };
        private static JObject Any(string d) => new JObject { ["description"] = d };
    }
}
