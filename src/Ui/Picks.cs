using System;
using System.Collections.Generic;
using System.Linq;

using Newtonsoft.Json.Linq;

using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using Rhino.Input;
using Rhino.Input.Custom;

using Alien.Scripting;

namespace Alien.Ui
{
    /// <summary>
    /// Geometry inputs picked in Rhino instead of wired: an unwired Curve / Brep / Surface / Mesh / SubD / Geometry /
    /// Point3d input gets a "Select in Rhino" control on the node. Picked objects are stored as references
    /// (<c>{"pick": ["guid", ...]}</c>, the node's manual value, saved with the .gh), so the node follows the objects:
    /// moving, editing, deleting or undeleting one re-solves it. A single Point3d is a clicked location instead.
    /// </summary>
    internal static class Picks
    {
        private static readonly string[] Types = { "Curve", "Line", "Circle", "Arc", "Rectangle3d", "Brep", "Box", "Surface", "Mesh", "SubD", "Geometry", "Point3d" };

        public static bool IsPickable(ParamDef d) => d.Access != ParamAccess.Tree && Types.Contains(d.Type);

        /// <summary>A single Point3d is picked as a location (GetPoint), everything else as objects.</summary>
        private static bool IsLocation(ParamDef d) => d.Type == "Point3d" && d.Access == ParamAccess.Item;

        public static JArray Ids(JToken token) => token is JObject o ? o["pick"] as JArray : null;

        public static JArray Ids(string raw)
        {
            if (string.IsNullOrEmpty(raw) || raw[0] != '{') return null;
            try { return Ids(JToken.Parse(raw)); } catch { return null; }
        }

        /// <summary>What the control says ("3 curves", "1 surface · 1 missing", "12.5, 3, 0"), or null when nothing is picked.
        /// Counts objects without converting their geometry, so it is cheap enough for every repaint.</summary>
        public static string Label(ParamDef d, string raw)
        {
            if (string.IsNullOrEmpty(raw)) return null;
            var ids = Ids(raw);
            if (ids == null)
            {
                var p = Values.Resolve(raw, d);
                if (p is Point3d pt) return $"{pt.X:0.##}, {pt.Y:0.##}, {pt.Z:0.##}";
                // a curve typed as points (set by an agent)
                int curves = p is Curve ? 1 : p is System.Collections.IList l ? l.OfType<Curve>().Count() : 0;
                return curves > 0 ? $"{curves} {Noun(d, curves)}" : null;
            }
            var doc = RhinoDoc.ActiveDoc;
            int found = ids.Count(t => Guid.TryParse(t?.ToString(), out var id) && doc?.Objects.FindId(id) is RhinoObject o && !o.IsDeleted);
            int missing = ids.Count - found;
            string noun = Noun(d, found);
            return found == 0 ? (ids.Count == 1 ? "object missing" : $"{ids.Count} objects missing")
                 : missing > 0 ? $"{found} {noun} · {missing} missing" : $"{found} {noun}";
        }

        private static string Noun(ParamDef d, int n)
        {
            string one = d.Type switch
            {
                "Point3d" => "point", "Brep" or "Box" => "solid", "Surface" => "surface", "Mesh" => "mesh", "SubD" => "SubD",
                "Geometry" => "object", _ => "curve",
            };
            return n == 1 ? one : one == "mesh" ? "meshes" : one + "s";
        }

        // ── resolving references into geometry ─────────────────

        /// <summary>The geometry behind a pick token, typed for the input: an item, or a list for list inputs. Missing objects are skipped.</summary>
        public static object Resolve(JArray ids, ParamDef d)
        {
            var doc = RhinoDoc.ActiveDoc;
            var items = new List<object>();
            if (doc != null)
                foreach (var t in ids)
                {
                    if (!Guid.TryParse(t?.ToString(), out var id)) continue;
                    var obj = doc.Objects.FindId(id);
                    if (obj == null || obj.IsDeleted) continue;
                    var g = Convert(obj.Geometry, d.Type);
                    if (g != null) items.Add(g);
                }
            if (d.Access == ParamAccess.List) return items;
            return items.FirstOrDefault();
        }

        private static object Convert(GeometryBase g, string type)
        {
            if (g == null) return null;
            switch (type)
            {
                case "Point3d":
                    return g is Point pt ? pt.Location : (object)null;
                case "Curve":
                    return (g as Curve)?.DuplicateCurve();
                case "Line":
                    return g is Curve lc && lc.IsLinear() ? new Line(lc.PointAtStart, lc.PointAtEnd) : (object)null;
                case "Circle":
                    return g is Curve cc && cc.TryGetCircle(out var circle) ? circle : (object)null;
                case "Arc":
                    return g is Curve ac && ac.TryGetArc(out var arc) ? arc : (object)null;
                case "Rectangle3d":
                    return g is Curve rc && rc.TryGetPolyline(out var pl) && pl.Count == 5 && pl.IsClosed
                        ? new Rectangle3d(new Plane(pl[0], pl[1] - pl[0], pl[3] - pl[0]), pl[0], pl[2]) : (object)null;
                case "Brep":
                case "Box":
                    var b = ToBrep(g);
                    if (type == "Box" && b != null) return b.GetBoundingBox(true).IsValid ? new Box(b.GetBoundingBox(true)) : (object)null;
                    return b;
                case "Surface":
                    var sb = ToBrep(g);
                    return sb != null && sb.Faces.Count == 1 ? sb.Faces[0].DuplicateSurface() : (object)null;
                case "Mesh":
                    if (g is Mesh m) return m.DuplicateMesh();
                    var mb = ToBrep(g);
                    if (mb == null) return null;
                    var parts = Mesh.CreateFromBrep(mb, MeshingParameters.Default);
                    if (parts == null || parts.Length == 0) return null;
                    var joined = new Mesh();
                    joined.Append(parts);
                    return joined;
                case "SubD":
                    return (g as SubD)?.Duplicate();
                default: // Geometry: anything, as it is
                    return g.Duplicate();
            }
        }

        private static Brep ToBrep(GeometryBase g) => g switch
        {
            Brep b => b.DuplicateBrep(),
            Extrusion e => e.ToBrep(),
            Surface s => s.ToBrep(),
            SubD sd => sd.ToBrep(SubDToBrepOptions.Default),
            _ => null,
        };

        // ── picking ─────────────────────────────────────────────

        private static ObjectType Filter(string type) => type switch
        {
            "Point3d" => ObjectType.Point,
            "Curve" or "Line" or "Circle" or "Arc" or "Rectangle3d" => ObjectType.Curve,
            "Brep" or "Box" => ObjectType.Brep | ObjectType.Extrusion | ObjectType.SubD,
            "Surface" => ObjectType.Surface,
            "Mesh" => ObjectType.Mesh | ObjectType.Brep | ObjectType.Extrusion | ObjectType.SubD,
            "SubD" => ObjectType.SubD,
            _ => ObjectType.AnyObject,
        };

        /// <summary>
        /// Ask Rhino for the input's geometry (objects already selected in Rhino count, so selecting first and then
        /// clicking works too), then store it on the node, undoably. Runs after the click has finished.
        /// </summary>
        public static void Pick(AlienComponent node, ParamDef d)
        {
            RhinoApp.InvokeOnUiThread(new Action(() =>
            {
                var doc = RhinoDoc.ActiveDoc;
                if (doc == null) return;
                string what = d.Access == ParamAccess.List ? Noun(d, 2) : "a " + Noun(d, 1);
                JToken value = null;
                if (IsLocation(d))
                {
                    var gp = new GetPoint();
                    gp.SetCommandPrompt($"Click a point for {node.NickName} › {d.Name}");
                    if (gp.Get() != GetResult.Point) return;
                    var p = gp.Point();
                    value = new JArray(p.X, p.Y, p.Z);
                }
                else
                {
                    var go = new GetObject();
                    go.SetCommandPrompt($"Select {what} for {node.NickName} › {d.Name}" + (d.Access == ParamAccess.List ? ", then press Enter" : ""));
                    go.GeometryFilter = Filter(d.Type);
                    go.EnablePreSelect(true, true);
                    go.SubObjectSelect = false;
                    go.GroupSelect = true;
                    var res = d.Access == ParamAccess.List ? go.GetMultiple(1, 0) : go.Get();
                    if (res != GetResult.Object || go.ObjectCount == 0) return;
                    value = new JObject { ["pick"] = new JArray(go.Objects().Select(o => o.ObjectId.ToString())) };
                    doc.Objects.UnselectAll();
                    doc.Views.Redraw();
                }
                node.SetInput(d.Name, value);
                Hook();
            }));
        }

        // ── following the picked objects ────────────────────────

        private static bool _hooked;
        private static readonly HashSet<Guid> Changed = new HashSet<Guid>();

        /// <summary>Re-solve nodes whose picked objects change. Hooked on the first pick, and when a file with picks opens.</summary>
        public static void Hook()
        {
            if (_hooked) return;
            _hooked = true;
            RhinoDoc.ReplaceRhinoObject += (s, e) => Touch(e.ObjectId);
            RhinoDoc.DeleteRhinoObject += (s, e) => Touch(e.ObjectId);
            RhinoDoc.UndeleteRhinoObject += (s, e) => Touch(e.ObjectId);
        }

        private static void Touch(Guid id)
        {
            lock (Changed)
            {
                if (Changed.Count == 0) RhinoApp.Idle += Flush;  // one re-solve per burst (a move replaces each object)
                Changed.Add(id);
            }
        }

        private static void Flush(object sender, EventArgs e)
        {
            RhinoApp.Idle -= Flush;
            HashSet<string> ids;
            lock (Changed)
            {
                ids = new HashSet<string>(Changed.Select(g => g.ToString()), StringComparer.OrdinalIgnoreCase);
                Changed.Clear();
            }
            foreach (Grasshopper.Kernel.GH_Document doc in Grasshopper.Instances.DocumentServer)
                foreach (var node in doc.Objects.OfType<AlienComponent>())
                    if (node.ManualValues.Values.Any(raw => Ids(raw)?.Any(t => ids.Contains(t.ToString())) == true))
                        node.ExpireSolution(true);
        }
    }
}
