using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.Linq;
using System.Threading;

using Grasshopper;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;

using Newtonsoft.Json.Linq;

using Rhino;

using Alien.Mcp;

namespace Alien.Ai
{
    /// <summary>
    /// The project agent's building tool: a whole building as the suite's own nodes, placed, wired and lined up on one
    /// footprint (bim_site -> levels, grids -> walls -> doors -> windows; slabs, roofs, the frame, stairs ->
    /// balustrades, and their types), so "a two-storey house" becomes a dozen editable nodes instead of one generated
    /// node that models everything badly. No model calls. The grids' outline and edges are the footprint every element
    /// follows, which is what makes the parts meet; nodes left on their samples never line up.
    /// </summary>
    internal static class BuildingTool
    {
        public const string Description =
            "Place a whole building as Alien's suite nodes, wired and lined up on one footprint: site, levels, grids, wall/slab/roof types, " +
            "walls on every storey, a front door, windows, floor slabs, the roof, a structural frame on the grids, stairs with their " +
            "voids and balustrades. Use it for any building request (house, apartments, office, 'walls + slab + roof'...) instead of " +
            "generating one node. Everything is optional; sizes in metres. Returns each node's id, the group id and a picture. " +
            "To change that building later (storeys, size, roof, types, frame, windows), call it again with edit:<group id> and only " +
            "what changes. Free: no model calls.";

        private static readonly string[] Kinds = { "house", "apartments", "office", "building" };
        private static readonly string[] RoofForms = { "hip", "gable", "dutch gable", "skillion", "flat" };
        private static readonly string[] Frames = { "steel", "timber", "concrete", "none" };

        public static JObject Schema()
        {
            JObject S(string type, string d) => new JObject { ["type"] = type, ["description"] = d };
            var p = new JObject
            {
                ["kind"] = S("string", "house | apartments | office | building: sets sensible defaults for everything below"),
                ["name"] = S("string", "project name"),
                ["storeys"] = S("integer", "storeys above ground (house 2, others 4)"),
                ["floor_height"] = S("number", "floor-to-floor (m)"),
                ["width"] = S("number", "footprint along X (m)"),
                ["depth"] = S("number", "footprint along Y (m)"),
                ["x_bays"] = S("string", "grid bays along X instead of width, e.g. '3x4' or '6, 4.5, 6'"),
                ["y_bays"] = S("string", "grid bays along Y instead of depth"),
                ["roof"] = S("string", "hip | gable | dutch gable | skillion | flat"),
                ["pitch"] = S("number", "roof pitch (degrees)"),
                ["wall"] = S("string", "wall type preset: brick veneer, double brick, timber stud, concrete 200, precast 150, blockwork 190, CLT, rendered AAC"),
                ["slab"] = S("string", "slab type preset: concrete 200, post-tensioned 250, CLT 180 + screed, timber joist floor, slab on ground 100 ..."),
                ["roof_type"] = S("string", "roof type preset: metal deck, concrete tile, terracotta tile, slate, membrane on concrete, green roof"),
                ["frame"] = S("string", "steel | timber | concrete | none"),
                ["stairs"] = S("boolean", "stairs between the storeys (default: when there are two or more)"),
                ["doors"] = S("string", "front door positions on the front wall, e.g. '50%' or '2.5, 8' (m); 'none' for no doors"),
                ["windows"] = S("string", "window positions on every wall of every storey, e.g. '25%, 75%'; 'none' for no windows"),
                ["window_family"] = S("string", "fixed | casement | awning | hopper | sliding | double hung"),
                ["window_width"] = S("number", "window width (m)"),
                ["window_height"] = S("number", "window height (m)"),
                ["edit"] = S("string", "group id of a building placed before: change that building (give only what changes) instead of placing a new one; it is rebuilt in place, keeping other settings"),
                ["origin"] = new JObject { ["type"] = "array", ["items"] = new JObject { ["type"] = "number" }, ["description"] = "[x, y] in metres: where the building's front-left corner goes in Rhino (default: clear of what is already there)" },
                ["site"] = S("string", "id of an existing bim_site node to use instead of placing one"),
                ["levels"] = S("string", "id of an existing levels node to use"),
                ["grids"] = S("string", "id of an existing grids node to use (its outline is the footprint)"),
            };
            return new JObject { ["type"] = "object", ["properties"] = p };
        }

        private sealed class Spec
        {
            public string Kind, Name, XBays, YBays, Roof, Wall, Slab, RoofType, Frame, Doors, Windows, WindowFamily, StairUse, Beam, Column, ColumnsAt;
            public int Storeys;
            public double FloorHeight, Width, Depth, Pitch, StairWidth, Riser, Going;
            public double WindowWidth = 1.2, WindowHeight = 1.2;
            public bool Stairs;
        }

        private static Spec Defaults(string kind) => kind switch
        {
            "apartments" => new Spec
            {
                Kind = kind, Name = "Apartments", Storeys = 4, FloorHeight = 3.1, Width = 24, Depth = 15, Roof = "flat", Pitch = 2,
                Wall = "precast 150", Slab = "post-tensioned 250", RoofType = "membrane on concrete", Frame = "concrete",
                StairUse = "public / commercial", StairWidth = 1.2, Riser = 175, Going = 280, WindowFamily = "sliding", ColumnsAt = "all intersections",
                WindowWidth = 1.8, WindowHeight = 1.5,
            },
            "office" => new Spec
            {
                Kind = kind, Name = "Office", Storeys = 4, FloorHeight = 3.8, Width = 30, Depth = 20, Roof = "flat", Pitch = 2,
                Wall = "precast 150", Slab = "post-tensioned 250", RoofType = "membrane on concrete", Frame = "steel",
                StairUse = "public / commercial", StairWidth = 1.2, Riser = 175, Going = 280, WindowFamily = "fixed", ColumnsAt = "all intersections",
                WindowWidth = 2.4, WindowHeight = 2.0,
            },
            "building" => new Spec
            {
                Kind = kind, Name = "Building", Storeys = 3, FloorHeight = 3.4, Width = 20, Depth = 14, Roof = "flat", Pitch = 2,
                Wall = "blockwork 190", Slab = "concrete 200", RoofType = "membrane on concrete", Frame = "steel",
                StairUse = "public / commercial", StairWidth = 1.2, Riser = 175, Going = 280, WindowFamily = "awning", ColumnsAt = "all intersections",
                WindowWidth = 1.8, WindowHeight = 1.5,
            },
            _ => new Spec
            {
                Kind = "house", Name = "House", Storeys = 2, FloorHeight = 3.0, Width = 12, Depth = 9, Roof = "hip", Pitch = 22.5,
                Wall = "brick veneer", Slab = "concrete 200", RoofType = "metal deck", Frame = "steel",
                StairUse = "private / domestic", StairWidth = 1.0, Riser = 180, Going = 250, WindowFamily = "awning", ColumnsAt = "perimeter",
            },
        };

        // ── the tool ───────────────────────────────────────────────

        /// <summary>Place a building, or (edit) rebuild one in place with some settings changed. Returns the report for
        /// the model and a capture framed on the building (base64 PNG, or null).</summary>
        public static (string text, string image) Run(JObject a)
        {
            // Blank and zero fields mean "not given" (some models send every field): a width of 0 is no width.
            a = new JObject(a.Properties().Where(p => !(p.Value.Type == JTokenType.Null
                || (p.Value.Type == JTokenType.String && string.IsNullOrWhiteSpace((string)p.Value))
                || (p.Value.Type is JTokenType.Integer or JTokenType.Float && Math.Abs((double)p.Value) < 1e-9))));
            JObject result;
            if (Str(a, "edit") is string group) result = Edit(group, a);
            else
            {
                var (s, w, d, notes) = SpecFrom(a);
                var reuse = new Dictionary<string, string>();
                foreach (var k in new[] { "site", "levels", "grids" })
                    if (Str(a, k) is string id) reuse[k] = id;
                result = Place(s, w, d, a["origin"] as JArray, reuse, notes, null, null);
            }
            return (result.ToString(Newtonsoft.Json.Formatting.None), Look(result.Value<string>("preview") ?? result.Value<string>("group")));
        }

        private static (Spec s, double w, double d, List<string> notes) SpecFrom(JObject a)
        {
            var kind = (a.Value<string>("kind") ?? "house").Trim().ToLowerInvariant();
            if (kind is "apartment" or "flats" or "residential") kind = "apartments";
            if (!Kinds.Contains(kind)) kind = kind.Contains("office") ? "office" : kind.Contains("house") || kind.Contains("home") ? "house" : "building";
            var s = Defaults(kind);
            var notes = new List<string>();

            s.Name = Str(a, "name") ?? s.Name;
            s.Storeys = Math.Max(1, Math.Min(40, a.Value<int?>("storeys") ?? s.Storeys));
            s.FloorHeight = Clamp(a.Value<double?>("floor_height") ?? s.FloorHeight, 2.4, 8);
            s.Width = Clamp(a.Value<double?>("width") ?? s.Width, 3, 300);
            s.Depth = Clamp(a.Value<double?>("depth") ?? s.Depth, 3, 300);
            var roof = (Str(a, "roof") ?? s.Roof).ToLowerInvariant();
            s.Roof = RoofForms.Contains(roof) ? roof : s.Roof;
            if (!RoofForms.Contains(roof)) notes.Add($"roof '{roof}' is not a form the roofs node has: used {s.Roof}");
            s.Pitch = Clamp(a.Value<double?>("pitch") ?? (s.Roof == "flat" ? 2 : s.Roof == "skillion" ? 10 : s.Pitch), 0, 75);
            s.Wall = Str(a, "wall") ?? s.Wall;
            s.Slab = Str(a, "slab") ?? s.Slab;
            s.RoofType = Str(a, "roof_type") ?? (s.Roof == "flat" && a["roof"] != null && kind == "house" ? "membrane on concrete" : s.RoofType);
            var frame = (Str(a, "frame") ?? s.Frame).ToLowerInvariant();
            s.Frame = Frames.Contains(frame) ? frame : s.Frame;
            s.Stairs = (a.Value<bool?>("stairs") ?? true) && s.Storeys > 1;
            s.Doors = Str(a, "doors");      // null: the bay nearest the middle of the front wall
            s.Windows = Str(a, "windows");  // null: the middle of each grid bay
            s.WindowFamily = Str(a, "window_family") ?? s.WindowFamily;
            s.WindowWidth = Clamp(a.Value<double?>("window_width") ?? s.WindowWidth, 0.3, 6);
            s.WindowHeight = Clamp(a.Value<double?>("window_height") ?? s.WindowHeight, 0.3, 4);

            // The grid is the footprint: bays from the request, or the size split into even bays (a new width or
            // depth replaces the bays it came with).
            double bayTarget = kind == "house" ? 4.5 : 7.5;
            s.XBays = (a["width"] != null && a["x_bays"] == null ? null : Str(a, "x_bays")) ?? Even(s.Width, bayTarget);
            s.YBays = (a["depth"] != null && a["y_bays"] == null ? null : Str(a, "y_bays")) ?? Even(s.Depth, bayTarget);
            var (w, d) = (Total(s.XBays) ?? s.Width, Total(s.YBays) ?? s.Depth);
            double maxBay = Math.Max(MaxBay(s.XBays) ?? w, MaxBay(s.YBays) ?? d);

            // Small frames get small sections, columns slim enough to hide in a 120 mm stud wall on the same grid
            // line; the node's defaults (310UC, 410UB ...) suit bigger frames.
            bool small = s.Storeys <= 3 && maxBay <= 6.5;
            (s.Column, s.Beam) = s.Frame switch
            {
                "steel" when small => ("SHS 89x5", "200UB25.4"),
                "timber" when small => ("F17 90x90", "GL18 315x85"),
                "concrete" when small => ("concrete 300x300", "concrete 450x300"),
                _ => ("", ""),
            };
            return (s, w, d, notes);
        }

        // ── changing a building ────────────────────────────────────

        private static readonly Dictionary<string, string> Roles = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["bim_site"] = "site", ["wall_types"] = "wall_type", ["slab_types"] = "slab_type", ["roof_types"] = "roof_type",
            ["stair_types"] = "stair_type", ["balustrade_types"] = "balustrade_type", ["grids"] = "grids", ["levels"] = "levels",
            ["walls"] = "walls", ["slabs"] = "slabs", ["roofs"] = "roofs", ["beams_columns"] = "frame", ["stairs"] = "stairs",
            ["balustrades"] = "balustrades", ["doors"] = "doors", ["windows"] = "windows",
        };

        /// <summary>
        /// Rebuild a building the tool placed, with some settings changed: its current settings are read back from its
        /// own nodes, the changes laid over them, the old nodes deleted and the building placed again where it was, each
        /// node keeping its other values (a tweaked overhang survives a new roof form). Rebuilding, rather than setting a
        /// few values, keeps the parts in step: more storeys get their windows and stairs too.
        /// </summary>
        private static JObject Edit(string groupId, JObject change)
        {
            double u = UiThread.Run(() => RhinoMath.UnitScale(Rhino.UnitSystem.Meters, RhinoDoc.ActiveDoc?.ModelUnitSystem ?? Rhino.UnitSystem.Meters));
            var (vals, members, at) = UiThread.Run(() =>
            {
                var doc = EnsureDocument();
                var group = Tools.Resolve(doc, groupId) as GH_Group
                            ?? throw new ToolException($"{groupId} is not a group: give the group id the building tool returned (canvas lists groups).");
                var objs = group.ObjectIDs.Select(g => doc.FindObject(g, true)).Where(o => o != null).ToList();
                var byRole = new Dictionary<string, JObject>();
                var stairs = new List<(int from, JObject v)>();
                var windows = new List<JObject>();
                foreach (var al in objs.OfType<AlienComponent>())
                {
                    var script = System.IO.Path.GetFileNameWithoutExtension(al.ScriptPath ?? "");
                    if (!Roles.TryGetValue(script, out var role)) continue;
                    var v = new JObject();
                    foreach (var kv in al.ManualValues)
                    {
                        try { v[kv.Key] = JToken.Parse(kv.Value); } catch { v[kv.Key] = kv.Value; }
                    }
                    if (role == "stairs") stairs.Add((int.TryParse(v.Value<string>("from_level"), out var f) ? f : stairs.Count, v));
                    else if (role == "windows") windows.Add(v);
                    else byRole[role] = v;
                }
                if (!byRole.ContainsKey("levels") || !byRole.ContainsKey("grids") || !byRole.ContainsKey("site"))
                    throw new ToolException("That group has no site, levels or grids of its own (placed with outside ones?): change those nodes with set_values instead.");
                stairs.Sort((x, y) => x.from.CompareTo(y.from));
                for (int i = 0; i < stairs.Count; i++) byRole[stairs.Count == 1 ? "stairs" : $"stairs_{i + 1}"] = stairs[i].v;
                // bay-placed windows come in two (front/back walls first, then the sides); one node means 'all' walls
                foreach (var v in windows)
                    byRole[windows.Count > 1 && (v.Value<string>("wall") ?? "").TrimStart().StartsWith("1", StringComparison.Ordinal) ? "side_windows" : "windows"] = v;
                var b = objs.Where(o => o.Attributes != null).Select(o => o.Attributes.Bounds).Aggregate(RectangleF.Union);
                return (byRole, objs.Select(Describe.ShortId).Concat(new[] { Describe.ShortId(group) }).ToList(), new PointF(b.Left, b.Top));
            });

            JToken V(string role, string key) => vals.TryGetValue(role, out var o) ? o[key] : null;
            var cur = new JObject
            {
                ["name"] = V("site", "name"), ["storeys"] = V("levels", "count"), ["floor_height"] = V("levels", "height"),
                ["x_bays"] = V("grids", "x_spacing"), ["y_bays"] = V("grids", "y_spacing"),
                ["roof"] = V("roofs", "form"), ["pitch"] = V("roofs", "pitch"),
                ["wall"] = V("wall_type", "preset"), ["slab"] = V("slab_type", "preset"), ["roof_type"] = V("roof_type", "preset"),
                ["frame"] = vals.ContainsKey("frame") ? V("frame", "material") ?? "steel" : "none",
                ["stairs"] = vals.Keys.Any(k => k.StartsWith("stairs", StringComparison.Ordinal)),
                ["doors"] = vals.ContainsKey("doors") ? V("doors", "positions") : "none",
                ["windows"] = vals.ContainsKey("side_windows") ? null : vals.ContainsKey("windows") ? V("windows", "positions") : "none",
                ["window_family"] = V("windows", "family"), ["window_width"] = V("windows", "width"), ["window_height"] = V("windows", "height"),
            };
            // a house keeps a house's defaults (private stairs, perimeter columns) when it grows a storey
            int storeys = cur.Value<int?>("storeys") ?? 2;
            bool house = (V("stair_type", "use")?.ToString() ?? "").StartsWith("private", StringComparison.Ordinal)
                         || V("frame", "columns_at")?.ToString() == "perimeter" || (storeys <= 2 && cur.Value<string>("roof") != "flat");
            cur["kind"] = house ? "house" : "building";
            var changed = new List<string>();
            foreach (var p in change.Properties())
            {
                if (p.Name is "edit" or "kind" or "site" or "levels" or "grids" or "origin") continue;
                if (!JToken.DeepEquals(cur[p.Name], p.Value)) changed.Add($"{p.Name} {p.Value.ToString(Newtonsoft.Json.Formatting.None)}");
                cur[p.Name] = p.Value;
            }
            if (change["width"] != null && change["x_bays"] == null) cur.Remove("x_bays");
            if (change["depth"] != null && change["y_bays"] == null) cur.Remove("y_bays");

            // What each node keeps: its other values. A new type drops the old one's layers; a new frame, size or
            // height lets the sections be picked again; doors and windows follow a new footprint.
            var carry = vals.ToDictionary(kv => kv.Key, kv => (JObject)kv.Value.DeepClone());
            void Drop(string role, params string[] keys) { if (carry.TryGetValue(role, out var o)) foreach (var k in keys) o.Remove(k); }
            if (change["wall"] != null) carry.Remove("wall_type");
            if (change["slab"] != null) carry.Remove("slab_type");
            if (change["roof_type"] != null) carry.Remove("roof_type");
            bool resized = new[] { "width", "depth", "x_bays", "y_bays", "storeys" }.Any(k => change[k] != null);
            if (resized || change["frame"] != null) Drop("frame", "column", "beam");
            if (resized) { cur.Remove("doors"); Drop("doors", "positions"); if (cur.Value<string>("windows") != "none") cur["windows"] = null; }
            if (change["pitch"] == null && change["roof"] != null) { cur.Remove("pitch"); Drop("roofs", "pitch"); }

            var origin = V("site", "origin") is JArray o3 && o3.Count >= 2 ? new JArray((double)o3[0] / u, (double)o3[1] / u) : null;
            Call("delete", new JObject { ["ids"] = new JArray(members) });
            var (s, w, d, notes) = SpecFrom(cur);  // the site's name: the old group is gone, so it gets its name back
            var result = Place(s, w, d, origin, new Dictionary<string, string>(), notes, carry, at);
            result["edited"] = (changed.Count > 0 ? "rebuilt in place with " + string.Join(", ", changed) : "rebuilt in place (nothing changed)") +
                               $"; the old group {groupId} and its nodes were removed";
            return result;
        }

        /// <summary>A capture framed on the building, for the model to see (and the chat to show).</summary>
        private static string Look(string id)
        {
            if (string.IsNullOrEmpty(id)) return null;
            try
            {
                var r = McpServer.Instance.Invoke("capture", new JObject
                {
                    ["view"] = "iso", ["focus"] = new JArray(id), ["width"] = 640, ["height"] = 400,
                });
                return (r["content"] as JArray)?.FirstOrDefault(c => c.Value<string>("type") == "image")?.Value<string>("data");
            }
            catch { return null; }
        }

        private static JObject Place(Spec s, double w, double d, JArray originArg, Dictionary<string, string> reuse, List<string> notes,
                                     Dictionary<string, JObject> carry, PointF? atOverride)
        {
            double u = UiThread.Run(() => RhinoMath.UnitScale(Rhino.UnitSystem.Meters, RhinoDoc.ActiveDoc?.ModelUnitSystem ?? Rhino.UnitSystem.Meters));

            // Reused nodes must exist (and be what they say) before anything is placed.
            var reused = UiThread.Run(() =>
            {
                var doc = EnsureDocument();
                return reuse.ToDictionary(kv => kv.Key, kv =>
                {
                    var o = Tools.Resolve(doc, kv.Value) as AlienComponent ?? throw new ToolException($"{kv.Value} is not an Alien node.");
                    var script = System.IO.Path.GetFileNameWithoutExtension(o.ScriptPath ?? "");
                    var want = kv.Key == "site" ? "bim_site" : kv.Key;
                    if (!script.Equals(want, StringComparison.OrdinalIgnoreCase)) throw new ToolException($"{kv.Value} runs {script}, not {want}.");
                    if (kv.Key == "grids" && !o.Params.Output.Any(p => p.NickName == "edges"))
                        throw new ToolException("That grids node has no outline/edges outputs yet: open the Alien panel once to update the suite, or leave grids out.");
                    return Describe.ShortId(o);
                });
            });

            // Where in Rhino: given, or clear of the previews already there (several buildings never overlap).
            double ox = 0, oy = 0;
            if (originArg != null && originArg.Count >= 2)
            {
                ox = (double)originArg[0] * u;
                oy = (double)originArg[1] * u;
            }
            else if (!reused.ContainsKey("site"))
            {
                var box = UiThread.Run(() =>
                {
                    var bb = Rhino.Geometry.BoundingBox.Empty;
                    foreach (var o in Instances.ActiveCanvas.Document.Objects.OfType<IGH_PreviewObject>())
                        if (!o.Hidden && o.IsPreviewCapable && o.ClippingBox.IsValid) bb.Union(o.ClippingBox);
                    return bb;
                });
                if (box.IsValid && box.Diagonal.Length > 0) { ox = Math.Round(box.Max.X + 10 * u); oy = Math.Round(box.Min.Y); }
            }

            // ── the nodes ──
            var nodes = new List<(string key, string script, int col, JObject values)>();
            void N(string key, string script, int col, JObject values = null)
            {
                // a rebuilt node keeps its other values; what this placement sets wins
                var v = carry != null && carry.TryGetValue(key, out var kept) ? (JObject)kept.DeepClone() : new JObject();
                foreach (var p in (values ?? new JObject()).Properties()) v[p.Name] = p.Value;
                nodes.Add((key, script, col, v));
            }

            if (!reused.ContainsKey("site"))
                N("site", "bim_site", 0, new JObject { ["name"] = s.Name, ["origin"] = new JArray(ox, oy, 0) });
            N("wall_type", "wall_types", 0, new JObject { ["preset"] = s.Wall });
            N("slab_type", "slab_types", 0, new JObject { ["preset"] = s.Slab });
            N("roof_type", "roof_types", 0, new JObject { ["preset"] = s.RoofType });
            if (s.Stairs) N("stair_type", "stair_types", 0, new JObject { ["use"] = s.StairUse, ["riser"] = s.Riser, ["going"] = s.Going });
            if (s.Stairs) N("balustrade_type", "balustrade_types", 0);
            if (!reused.ContainsKey("grids"))
                N("grids", "grids", 1, new JObject { ["x_spacing"] = s.XBays, ["y_spacing"] = s.YBays });
            if (!reused.ContainsKey("levels"))
                N("levels", "levels", 1, new JObject { ["count"] = s.Storeys, ["height"] = s.FloorHeight, ["roof_level"] = true });
            N("walls", "walls", 2, new JObject { ["base"] = "all" });
            N("slabs", "slabs", 2);
            N("roofs", "roofs", 2, new JObject { ["form"] = s.Roof, ["pitch"] = s.Pitch });
            if (s.Frame != "none")
            {
                var bc = new JObject { ["material"] = s.Frame, ["columns_at"] = s.ColumnsAt, ["top_offset"] = 0.2 };
                if (s.Column.Length > 0) { bc["column"] = s.Column; bc["beam"] = s.Beam; }
                N("frame", "beams_columns", 2, bc);
            }
            var stairKeys = new List<string>();
            if (s.Stairs)
            {
                int flights = Math.Min(s.Storeys - 1, 6);
                if (s.Storeys - 1 > flights) notes.Add($"stairs placed for the lowest {flights + 1} levels only");
                var (line, shape, turn) = StairLine(w, d, s);
                var baseLine = new JArray(line.Select(p => new JArray(ox + p.x * u, oy + p.y * u, 0)));
                for (int i = 0; i < flights; i++)
                {
                    var key = flights == 1 ? "stairs" : $"stairs_{i + 1}";
                    stairKeys.Add(key);
                    N(key, "stairs", 3, new JObject
                    {
                        ["from_level"] = i.ToString(CultureInfo.InvariantCulture), ["to_level"] = (i + 1).ToString(CultureInfo.InvariantCulture),
                        ["base"] = baseLine, ["shape"] = shape, ["turn"] = turn, ["width"] = s.StairWidth,
                    });
                }
                N("balustrades", "balustrades", 4);
            }
            // Openings sit in the middle of the grid bays, clear of the columns on the grid lines. Walls come four a
            // storey in the grids' edge order (front, left, back, right), so the front and back walls take the X bays
            // and the sides the Y bays; a window that would overlap the door is skipped by the windows node.
            var xb = Bays(s.XBays) ?? new List<double> { w };
            var yb = Bays(s.YBays) ?? new List<double> { d };
            bool doors = s.Doors == null || !IsNone(s.Doors), windows = s.Windows == null || !IsNone(s.Windows);
            if (doors)
            {
                var mids = Mids(xb, 1);
                var door = s.Doors ?? Fmt(w - mids.OrderBy(m => Math.Abs(m - w / 2)).First()); // the front wall runs from x = width back to 0
                N("doors", "doors", 3, new JObject { ["wall"] = "0", ["positions"] = door });
            }
            var windowKeys = new List<string>();
            if (windows)
            {
                var size = new JObject { ["family"] = s.WindowFamily, ["width"] = s.WindowWidth, ["height"] = s.WindowHeight };
                if (s.Windows != null)
                {
                    windowKeys.Add("windows");
                    N("windows", "windows", 4, new JObject(size) { ["wall"] = "all", ["positions"] = s.Windows });
                }
                else
                {
                    string Walls(int first) => string.Join(", ", Enumerable.Range(0, s.Storeys).SelectMany(k => new[] { 4 * k + first, 4 * k + first + 2 }));
                    string Places(List<double> bays) => string.Join(", ", Mids(bays, bays.Max() >= 6 && s.Kind != "house" ? 2 : 1).Select(Fmt));
                    windowKeys.Add("windows");
                    N("windows", "windows", 4, new JObject(size) { ["wall"] = Walls(0), ["positions"] = Places(xb) });
                    windowKeys.Add("side_windows");
                    N("side_windows", "windows", 4, new JObject(size) { ["wall"] = Walls(1), ["positions"] = Places(yb) });
                }
            }

            // ── the wires ──
            string Src(string key) => reused.TryGetValue(key, out var id) ? id : key;
            var links = new List<string>();
            void L(string from, string to) => links.Add($"{from}>{to}");
            foreach (var k in new[] { "levels", "grids", "walls", "slabs", "roofs", "frame", "balustrades" }.Concat(stairKeys))
                if (nodes.Any(n => n.key == k) && !(k == "levels" && reused.ContainsKey("levels")) && !(k == "grids" && reused.ContainsKey("grids")))
                    L(Src("site") + ".site", k + ".site");
            if (!reused.ContainsKey("levels")) L(Src("grids") + ".outline", "levels.outline");
            foreach (var k in new[] { "walls", "slabs", "roofs", "frame", "balustrades" }.Concat(stairKeys))
                if (nodes.Any(n => n.key == k)) L(Src("levels") + ".levels", k + ".levels");
            if (nodes.Any(n => n.key == "frame")) L(Src("grids") + ".grids", "frame.grids");
            L(Src("grids") + ".edges", "walls.path");
            L(Src("grids") + ".outline", "slabs.outline");
            L(Src("grids") + ".outline", "roofs.outline");
            // the type swatches sit at the building, not the world origin (framing the group frames the building)
            foreach (var k in new[] { "wall_type", "slab_type", "roof_type", "stair_type", "balustrade_type" })
                if (nodes.Any(n => n.key == k)) L(Src("site") + ".plane", k + ".plane");
            L("wall_type.wall_type", "walls.wall_type");
            L("slab_type.slab_type", "slabs.slab_type");
            L("roof_type.roof_type", "roofs.roof_type");
            foreach (var k in stairKeys)
            {
                L("stair_type.stair_type", k + ".stair_type");
                L(k + ".voids", "slabs.openings");
                L(k + ".edges", "balustrades.path");
            }
            if (s.Stairs) L("balustrade_type.balustrade_type", "balustrades.balustrade_type");
            if (doors) L("walls.walls", "doors.walls");
            var host = doors ? "doors" : "walls";
            foreach (var k in windowKeys)
            {
                L(host + ".walls", k + ".walls");  // each opens the walls the last one returned
                host = k;
            }

            // One preview in the elements' own colours. Walls, doors and windows each output the walls (only the last has
            // every opening cut), so the element nodes' red previews are turned off and this draws each part once.
            var shown = new List<(string geo, string col)> { (host + ".solids", host + ".colours") };
            if (doors) shown.Add(("doors.parts", "doors.part_colours"));
            foreach (var k in windowKeys) shown.Add((k + ".parts", k + ".part_colours"));
            shown.Add(("slabs.solids", "slabs.colours"));
            shown.Add(("roofs.solids", "roofs.colours"));
            if (nodes.Any(n => n.key == "frame")) shown.Add(("frame.solids", "frame.colours"));
            foreach (var k in stairKeys) shown.Add((k + ".solids", k + ".colours"));
            if (s.Stairs) shown.Add(("balustrades.solids", "balustrades.colours"));
            N("preview", "@Custom Preview", 5);
            foreach (var (geo, col) in shown)
            {
                L(geo, "preview.Geometry");
                L(col, "preview.Material");  // same order: the nth colour list pairs with the nth geometry list
            }

            // ── place, set and wire in one undoable batch, below everything on the canvas ──
            var at = atOverride ?? UiThread.Run(() =>
            {
                var objs = Instances.ActiveCanvas.Document.Objects.Where(o => o.Attributes != null).ToList();
                if (objs.Count == 0) return new PointF(100, 100);
                var b = objs.Select(o => o.Attributes.Bounds).Aggregate(RectangleF.Union);
                return new PointF(b.Left, b.Bottom + 160);
            });
            var spec = new JArray(nodes.Select((n, i) =>
            {
                var o = new JObject { ["key"] = n.key, ["x"] = at.X + n.col * 420, ["y"] = at.Y + i * 30 };
                if (n.script.StartsWith("@", StringComparison.Ordinal)) o["component"] = n.script.Substring(1); // a Grasshopper component
                else { o["component"] = "alien"; o["script"] = $"suite/{n.script}.py"; o["values"] = n.values; }
                return o;
            }));
            var added = Call("add", new JObject { ["nodes"] = spec, ["links"] = new JArray(links) });

            var ids = new Dictionary<string, string>();
            var problems = new List<string>();
            foreach (var p in (added["nodes"] as JObject ?? new JObject()).Properties())
            {
                var v = p.Value.ToString();
                if (v.StartsWith("error", StringComparison.Ordinal)) { problems.Add($"{p.Name}: {v}"); continue; }
                ids[p.Name] = v.Split(' ')[0];
                if (v.Contains("(values:")) problems.Add($"{p.Name}: {v.Substring(v.IndexOf("(values:", StringComparison.Ordinal))}");
            }
            int wired = 0;
            foreach (var r in added["links"] as JArray ?? new JArray())
            {
                var t = r.ToString();
                if (t == "ok") wired++;
                else problems.Add("wire " + t);
            }
            foreach (var kv in reused) ids[kv.Key] = kv.Value;

            var elements = new[] { "walls", "doors", "slabs", "roofs", "frame", "balustrades", "wall_type", "slab_type", "roof_type", "stair_type", "balustrade_type" }
                .Concat(windowKeys).Concat(stairKeys)
                .Where(k => ids.ContainsKey(k)).Select(k => ids[k]).ToList();
            if (ids.ContainsKey("preview") && elements.Count > 0)
                Call("set_values", new JObject { ["ids"] = new JArray(elements), ["preview"] = false });

            var placed = nodes.Where(n => ids.ContainsKey(n.key)).ToList();
            var groupId = UiThread.Run(() => Arrange(placed.Select(n => (ids[n.key], n.col)).ToList(), at, s.Name));
            Settle(ids.Values);

            // Slab edges out to the walls' outer face: half the wall type's thickness.
            if (ids.TryGetValue("wall_type", out var wtId) && ids.TryGetValue("slabs", out var slId))
            {
                var total = UiThread.Run(() => Output(wtId, "total"));
                if (double.TryParse(total, NumberStyles.Float, CultureInfo.InvariantCulture, out var t) && t > 0)
                {
                    Call("set_values", new JObject { ["id"] = slId, ["values"] = new JObject { ["offset"] = Math.Round(t / 2, 3) } });
                    Settle(ids.Values);
                }
            }

            // ── the report ──
            var report = UiThread.Run(() =>
            {
                var doc = Instances.ActiveCanvas.Document;
                var arr = new JArray();
                foreach (var (key, script, _, _) in nodes)
                {
                    if (!ids.TryGetValue(key, out var id)) continue;
                    var al = Tools.Resolve(doc, id) as AlienComponent;
                    if (al == null) continue;
                    var r = Describe.Run(al, 0);
                    var o = new JObject { ["role"] = key, ["id"] = id, ["status"] = r.Value<string>("status") };
                    if (r["error"] != null) o["error"] = r["error"];
                    if (r["unset_outputs"] != null) o["unset"] = r["unset_outputs"];
                    var info = Output(id, "info");
                    if (!string.IsNullOrEmpty(info)) o["info"] = info.Split('\n')[0];
                    arr.Add(o);
                }
                return arr;
            });

            var result = new JObject
            {
                ["placed"] = report,
                ["wires"] = wired,
                ["group"] = groupId,
                ["preview"] = ids.TryGetValue("preview", out var pv) ? pv : null,
                ["footprint_m"] = $"{w:0.##} x {d:0.##}, grids {s.XBays} by {s.YBays}",
                ["origin"] = new JArray(Math.Round(ox / u, 2), Math.Round(oy / u, 2)),
                ["storeys"] = s.Storeys,
            };
            result["look"] = $"a capture framed on the building follows (it draws in its materials' colours; red linework and solids are levels, " +
                             $"grids or other nodes' previews); capture(view:\"iso\", focus:[\"{(ids.TryGetValue("preview", out var pvId) ? pvId : groupId)}\"]) frames it again";
            result["change"] = $"building(edit:\"{groupId}\", ...only what changes) for storeys, size, roof, types, frame or windows";
            if (problems.Count > 0) result["problems"] = new JArray(problems.Take(12));
            if (notes.Count > 0) result["notes"] = new JArray(notes);
            result["next"] = "Every part is its own node: change sizes on grids (x_spacing, y_spacing) and levels (count, height); " +
                             "types on wall_types / slab_types / roof_types; doors' and windows' positions and family; the roofs' form. " +
                             "Not included: interior walls (another walls node with its own path), furniture and fittings (object_3d, one per item; " +
                             "its generate button spends credit, so ask first), site context (vic_site_map into the site's boundary).";
            return result;
        }

        /// <summary>One line for the chat history: the building's group and each role's node id, so a later request
        /// ("make that roof a gable") changes this building's nodes, not another setup's.</summary>
        public static string Brief(string result)
        {
            try
            {
                var r = JObject.Parse(result);
                var roles = (r["placed"] as JArray ?? new JArray()).Select(n => $"{n.Value<string>("role")} {n.Value<string>("id")}");
                return $"placed a building (group {r.Value<string>("group")}, {r.Value<int>("storeys")} storeys, {r.Value<string>("footprint_m")}): " + string.Join(", ", roles);
            }
            catch { return "placed a building"; }
        }

        /// <summary>Role -> node id from a report, or null.</summary>
        public static JObject RoleIds(string result)
        {
            try
            {
                var r = JObject.Parse(result);
                var o = new JObject();
                foreach (var n in r["placed"] as JArray ?? new JArray()) o[n.Value<string>("role")] = n.Value<string>("id");
                if (r.Value<string>("group") is string g) o["group"] = g;
                return o.Count > 0 ? o : null;
            }
            catch { return null; }
        }

        // ── helpers ────────────────────────────────────────────────

        /// <summary>A straight flight along the back wall when it fits, else a U (half landing) that doubles back inside.
        /// Points in metres from the front-left corner; walls are about 0.35 m deep to the inside face.</summary>
        private static (List<(double x, double y)> line, string shape, string turn) StairLine(double w, double d, Spec s)
        {
            const double c = 0.35;
            double riser = s.Riser / 1000.0, going = s.Going / 1000.0;
            int risers = (int)Math.Ceiling(s.FloorHeight / riser);
            double run = (risers - 1) * going;
            double half = s.StairWidth / 2;
            if (risers <= 18 && w >= d && c + 0.1 + run + 1.0 <= w - c)
                return (new List<(double, double)> { (c + 0.1, d - c - half), (c + 1.1, d - c - half) }, "straight", "left");
            if (risers <= 18 && d > w && c + 0.1 + run + 1.0 <= d - c)
                return (new List<(double, double)> { (w - c - half, c + 0.1), (w - c - half, c + 1.1) }, "straight", "left");
            // U: along the longer side, the second flight on the inside
            return w >= d
                ? (new List<(double, double)> { (c + 0.1, d - c - half), (c + 1.1, d - c - half) }, "U (half landing)", "right")
                : (new List<(double, double)> { (w - c - half, c + 0.1), (w - c - half, c + 1.1) }, "U (half landing)", "left");
        }

        /// <summary>Columns by role, stacked by each node's real size, in a named group; the canvas centres on it.</summary>
        private static string Arrange(List<(string id, int col)> placed, PointF at, string name)
        {
            var canvas = Instances.ActiveCanvas;
            var doc = canvas.Document;
            var objs = placed.Select(p => (obj: Tools.Resolve(doc, p.id), p.col)).Where(p => p.obj?.Attributes != null).ToList();
            foreach (var (obj, _) in objs) obj.Attributes.PerformLayout();
            var widths = objs.GroupBy(p => p.col).ToDictionary(g => g.Key, g => g.Max(p => p.obj.Attributes.Bounds.Width));
            float x = at.X;
            var colX = new Dictionary<int, float>();
            foreach (var col in widths.Keys.OrderBy(k => k))
            {
                colX[col] = x;
                x += widths[col] + 90;
            }
            var colY = new Dictionary<int, float>();
            foreach (var (obj, col) in objs)
            {
                float y = colY.TryGetValue(col, out var yy) ? yy : at.Y;
                var b = obj.Attributes.Bounds;
                obj.Attributes.Pivot = new PointF(obj.Attributes.Pivot.X + colX[col] - b.Left, obj.Attributes.Pivot.Y + y - b.Top);
                obj.Attributes.ExpireLayout();
                obj.Attributes.PerformLayout();
                colY[col] = y + obj.Attributes.Bounds.Height + 40;
            }

            var taken = new HashSet<string>(doc.Objects.OfType<GH_Group>().Select(g => g.NickName), StringComparer.OrdinalIgnoreCase);
            var unique = name;
            for (int k = 2; taken.Contains(unique); k++) unique = $"{name} {k}";  // "House 2": buildings stay told apart
            var group = new GH_Group { NickName = unique, Colour = Color.FromArgb(40, 124, 77, 255) };
            group.CreateAttributes();
            foreach (var (obj, _) in objs) group.AddObject(obj.InstanceGuid);
            doc.AddObject(group, false);
            doc.UndoUtil.RecordAddObjectEvent("Alien: group " + name, group);
            group.ExpireCaches();
            if (objs.Count > 0)
            {
                // centred and zoomed to fit (not past 100%), so the user sees the whole chain at once
                var all = objs.Select(p => p.obj.Attributes.Bounds).Aggregate(RectangleF.Union);
                var size = canvas.ClientSize;
                if (size.Width > 0 && size.Height > 0)
                    canvas.Viewport.Zoom = Math.Max(0.15f, Math.Min(1f, 0.92f * Math.Min(size.Width / (all.Width + 80), size.Height / (all.Height + 80))));
                canvas.Viewport.MidPoint = new PointF(all.X + all.Width / 2, all.Y + all.Height / 2);
            }
            canvas.Refresh();
            return Describe.ShortId(group);
        }

        /// <summary>The canvas's document, or a new one on an empty editor (UI thread).</summary>
        private static GH_Document EnsureDocument()
        {
            var canvas = Instances.ActiveCanvas;
            if (canvas == null)
            {
                (RhinoApp.GetPlugInObject("Grasshopper") as Grasshopper.Plugin.GH_RhinoScriptInterface)?.ShowEditor();
                canvas = Instances.ActiveCanvas ?? throw new ToolException("The Grasshopper editor is not open.");
            }
            if (canvas.Document == null)
            {
                var doc = new GH_Document();
                Instances.DocumentServer.AddDocument(doc);
                canvas.Document = doc;
            }
            return canvas.Document;
        }

        /// <summary>Wait (off the UI thread) until the new nodes have run and the solution is done.</summary>
        private static void Settle(IEnumerable<string> ids)
        {
            var list = ids.ToList();
            var until = DateTime.UtcNow.AddSeconds(90);
            Thread.Sleep(150);
            while (DateTime.UtcNow < until)
            {
                bool done = UiThread.Run(() =>
                {
                    var doc = Instances.ActiveCanvas?.Document;
                    if (doc == null || !doc.Enabled) return true;
                    if (doc.SolutionState == GH_ProcessStep.Process) return false;
                    return list.Select(id => { try { return Tools.Resolve(doc, id) as AlienComponent; } catch { return null; } })
                               .All(n => n == null || (!n.IsRebuilding && n.LastRun.Status != "rebuilding" && n.LastRun.Status != "running"));
                });
                if (done) return;
                Thread.Sleep(100);
            }
        }

        /// <summary>The first item of a node's output, as text (UI thread).</summary>
        private static string Output(string id, string name)
        {
            var doc = Instances.ActiveCanvas?.Document;
            if (doc == null) return null;
            var n = Tools.Resolve(doc, id) as AlienComponent;
            var p = n?.Params.Output.FirstOrDefault(x => x.NickName == name);
            var goo = p?.VolatileData.AllData(true).FirstOrDefault();
            return goo == null ? null : Convert.ToString(Scripting.Values.Unwrap(goo), CultureInfo.InvariantCulture);
        }

        private static JObject Call(string tool, JObject args)
        {
            var r = McpServer.Instance.Invoke(tool, args);
            var text = string.Join("\n", (r["content"] as JArray ?? new JArray()).Where(c => c.Value<string>("type") == "text").Select(c => c.Value<string>("text")));
            if (r.Value<bool>("isError")) throw new ToolException($"{tool}: {text}");
            try { return JObject.Parse(text); } catch { return new JObject { ["text"] = text }; }
        }

        /// <summary>The size in even bays near the target, preferring a round bay (20 m: 4 x 5, not 3 x 6.667).</summary>
        private static string Even(double size, double target)
        {
            int guess = Math.Max(1, (int)Math.Round(size / target));
            var candidates = Enumerable.Range(Math.Max(1, guess - 1), 3).ToList();
            bool Round(int k) => Math.Abs(size / k * 20 - Math.Round(size / k * 20)) < 1e-6;  // a multiple of 50 mm
            int n = candidates.Where(Round).OrderBy(k => Math.Abs(size / k - target)).ThenByDescending(k => k).Cast<int?>().FirstOrDefault() ?? guess;
            double bay = Math.Round(size / n, 3);
            return n == 1 ? Fmt(bay) : $"{n}x{Fmt(bay)}";
        }

        /// <summary>Opening centres (m from the start): each bay's middle, or its quarter points for two per bay.</summary>
        private static List<double> Mids(List<double> bays, int perBay)
        {
            var out_ = new List<double>();
            double x = 0;
            foreach (var b in bays)
            {
                if (perBay == 1) out_.Add(x + b / 2);
                else { out_.Add(x + b / 4); out_.Add(x + 3 * b / 4); }
                x += b;
            }
            return out_;
        }

        private static string Fmt(double v) => Math.Round(v, 3).ToString(CultureInfo.InvariantCulture);

        /// <summary>Bays as grids reads them ('3x4.2', '6, 4.5'); null when unreadable.</summary>
        private static List<double> Bays(string text)
        {
            var out_ = new List<double>();
            foreach (var tok in (text ?? "").Split(new[] { ',', ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                var t = tok.Trim().Replace(" ", "");
                var m = System.Text.RegularExpressions.Regex.Match(t, @"^(\d+)[xX*×]([\d.]+)$");
                if (m.Success && double.TryParse(m.Groups[2].Value, NumberStyles.Float, CultureInfo.InvariantCulture, out var v))
                    out_.AddRange(Enumerable.Repeat(v > 100 ? v / 1000 : v, int.Parse(m.Groups[1].Value)));
                else if (double.TryParse(t, NumberStyles.Float, CultureInfo.InvariantCulture, out var one) && one > 0)
                    out_.Add(one > 100 ? one / 1000 : one);
                else return null;
            }
            return out_.Count > 0 ? out_ : null;
        }

        private static double? Total(string bays) => Bays(bays)?.Sum();
        private static double? MaxBay(string bays) => Bays(bays)?.Max();

        private static bool IsNone(string s) => string.IsNullOrWhiteSpace(s) || s.Trim().Equals("none", StringComparison.OrdinalIgnoreCase) || s.Trim() == "0";
        private static string Str(JObject a, string k) { var v = a.Value<string>(k); return string.IsNullOrWhiteSpace(v) ? null : v.Trim(); }
        private static double Clamp(double v, double lo, double hi) => Math.Max(lo, Math.Min(hi, v));
    }
}
