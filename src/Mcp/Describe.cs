using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

using Grasshopper.Kernel;
using Grasshopper.Kernel.Special;
using Grasshopper.Kernel.Types;

using Newtonsoft.Json.Linq;

using Rhino.Geometry;

using Alien.Scripting;

namespace Alien.Mcp
{
    /// <summary>
    /// Token-lean summaries of Grasshopper data. Agents get "list[24] Point3d; bbox 0,0,0->10,5,0; e.g. ..."
    /// instead of hundreds of ToString() lines.
    /// </summary>
    public static class Describe
    {
        private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        public static string ShortId(IGH_DocumentObject o) => o.InstanceGuid.ToString("N").Substring(0, 8);

        /// <summary>Summarise a parameter's current (volatile) data.</summary>
        public static string ParamData(IGH_Param p, int sample)
        {
            var data = p.VolatileData;
            if (data == null || data.DataCount == 0) return "empty";
            var items = new List<object>();
            foreach (var goo in data.AllData(true)) items.Add(Scripting.Values.Unwrap(goo));
            var s = Summarise(items, sample);
            return data.PathCount > 1 ? $"tree[{data.PathCount} branches] " + s : s;
        }

        /// <summary>Summarise a script output value (item, list or nested list).</summary>
        public static string Value(object v, int sample)
        {
            if (v == null) return "None";
            if (!Scripting.Values.IsSequence(v)) return Summarise(new List<object> { v }, sample);
            var items = Scripting.Values.ToList(v);
            if (items.Any(Scripting.Values.IsSequence))
            {
                var flat = items.SelectMany(i => Scripting.Values.IsSequence(i) ? Scripting.Values.ToList(i) : new List<object> { i }).ToList();
                return $"tree[{items.Count} branches] " + Summarise(flat, sample);
            }
            return Summarise(items, sample, forceList: true);
        }

        private static string Summarise(List<object> items, int sample, bool forceList = false)
        {
            if (items.Count == 1 && !forceList) return Short(items[0], 200);

            var parts = new List<string>();
            var types = items.GroupBy(i => TypeName(i)).Select(g => (g.Key, g.Count())).OrderByDescending(t => t.Item2).ToList();
            parts.Add($"list[{items.Count}] " + (types.Count == 1 ? types[0].Key : string.Join(", ", types.Take(4).Select(t => $"{t.Key}x{t.Item2}"))));

            var nums = items.Where(i => i is double || i is int || i is float).Select(Convert.ToDouble).ToList();
            if (nums.Count > 0) parts.Add($"{Num(nums.Min())}..{Num(nums.Max())}");

            var box = BoundingBox.Empty;
            foreach (var i in items) box.Union(Bounds(i));
            if (box.IsValid) parts.Add($"bbox {Pt(box.Min)}->{Pt(box.Max)}");

            var nulls = items.Count(i => i == null);
            if (nulls > 0) parts.Add($"{nulls} null");

            if (sample > 0) parts.Add("e.g. " + string.Join(" | ", items.Where(i => i != null).Take(sample).Select(i => Short(i, 60))));
            return string.Join("; ", parts);
        }

        public static string TypeName(object o) => o switch
        {
            null => "null",
            double or float => "float",
            int or long => "int",
            string => "str",
            bool => "bool",
            _ => o.GetType().Name
        };

        public static string Short(object o, int max = 80)
        {
            string s = o switch
            {
                null => "None",
                string str => "\"" + str + "\"",
                double d => Num(d),
                float f => Num(f),
                Point3d p => Pt(p),
                Vector3d v => $"<{Num(v.X)},{Num(v.Y)},{Num(v.Z)}>",
                Plane pl => $"Plane(o {Pt(pl.Origin)}, z <{Num(pl.ZAxis.X)},{Num(pl.ZAxis.Y)},{Num(pl.ZAxis.Z)}>)",
                Line l => $"Line({Pt(l.From)}->{Pt(l.To)})",
                Interval iv => $"[{Num(iv.T0)},{Num(iv.T1)}]",
                Curve c => $"{c.GetType().Name}(len {Num(c.GetLength())}{(c.IsClosed ? ", closed" : "")})",
                Brep b => $"Brep({b.Faces.Count} faces{(b.IsSolid ? ", solid" : "")}{(b.IsValid ? "" : ", INVALID")})",
                Mesh m => $"Mesh({m.Vertices.Count} v, {m.Faces.Count} f{(m.IsValid ? "" : ", INVALID")})",
                Box bx => $"Box({Pt(bx.BoundingBox.Min)}->{Pt(bx.BoundingBox.Max)})",
                System.Drawing.Color col => $"#{col.R:X2}{col.G:X2}{col.B:X2}",
                IGH_Goo goo => goo.ToString(),
                IFormattable fmt => fmt.ToString(null, Inv),
                _ => o.ToString()
            };
            s = s.Replace("\r", "").Replace("\n", "\\n");
            return s.Length > max ? s.Substring(0, max) + "..." : s;
        }

        /// <summary>Bounds of geometry in a node's outputs (or inputs when it has no outputs, e.g. Custom Preview).</summary>
        public static BoundingBox DataBounds(IGH_DocumentObject o)
        {
            IEnumerable<IGH_Param> ps = o switch
            {
                IGH_Component c => c.Params.Output.Count > 0 ? c.Params.Output : c.Params.Input,
                IGH_Param p => new[] { p },
                _ => Enumerable.Empty<IGH_Param>()
            };
            var box = BoundingBox.Empty;
            foreach (var p in ps)
            {
                if (p.VolatileData == null) continue;
                foreach (var goo in p.VolatileData.AllData(true))
                {
                    var b = Bounds(Scripting.Values.Unwrap(goo));
                    if (b.IsValid) box.Union(b);
                }
            }
            return box;
        }

        private static BoundingBox Bounds(object o)
        {
            try
            {
                return o switch
                {
                    Point3d p => new BoundingBox(p, p),
                    GeometryBase g => g.GetBoundingBox(false),
                    Line l => l.BoundingBox,
                    Box b => b.BoundingBox,
                    Circle c => c.BoundingBox,
                    Arc a => a.BoundingBox(),
                    Rectangle3d r => r.BoundingBox,
                    Plane pl => new BoundingBox(pl.Origin, pl.Origin),
                    _ => BoundingBox.Empty
                };
            }
            catch { return BoundingBox.Empty; }
        }

        private static string Num(double d) =>
            Math.Abs(d) >= 1e6 || (Math.Abs(d) < 1e-3 && d != 0) ? d.ToString("0.###e0", Inv) : d.ToString("0.###", Inv);

        private static string Pt(Point3d p) => $"{Num(p.X)},{Num(p.Y)},{Num(p.Z)}";

        /// <summary>One-line value of a special param object (slider, panel, toggle, value list).</summary>
        public static string SpecialValue(IGH_DocumentObject o) => o switch
        {
            GH_NumberSlider s => $"{Num((double)s.CurrentValue)} [{Num((double)s.Slider.Minimum)}..{Num((double)s.Slider.Maximum)}]",
            GH_Panel p => Short(p.UserText ?? "", 80),
            GH_BooleanToggle t => t.Value ? "true" : "false",
            GH_ValueList v => v.FirstSelectedItem?.Name ?? "",
            _ => null
        };

        /// <summary>Compact run report for an Alien node — what an agent needs after an edit.</summary>
        public static JObject Run(AlienComponent n, int sample = 2)
        {
            var r = n.LastRun;
            var o = new JObject { ["id"] = ShortId(n), ["status"] = r.Status };
            // The script never ran but Grasshopper flagged the node (e.g. an exception outside the script):
            // surface that instead of a misleading "idle".
            if (r.Iterations == 0 && n.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error)
            {
                o["status"] = "error";
                o["error"] = string.Join(" | ", n.RuntimeMessages(GH_RuntimeMessageLevel.Error).Take(3));
            }
            if (r.Iterations > 0) o["ms"] = Math.Round(r.Ms, 1);
            if (r.Iterations > 1) o["runs"] = r.Iterations;
            if (r.Note != null) o["note"] = r.Note;
            if (r.FirstError != null)
            {
                var e = r.FirstError;
                o["error"] = e.Error;
                if (e.Line.HasValue)
                {
                    o["line"] = e.Line.Value;
                    var src = LineOf(n, e.Line.Value);
                    if (src != null) o["code"] = src;
                }
                if (!string.IsNullOrEmpty(e.Trace)) o["trace"] = e.Trace.Length > 1200 ? e.Trace.Substring(e.Trace.Length - 1200) : e.Trace;
                if (r.Failures < r.Iterations) o["failed"] = $"{r.Failures}/{r.Iterations}";
            }
            if (n.Header.Warnings.Count > 0) o["header_warnings"] = new JArray(n.Header.Warnings);
            if (r.Unset.Count > 0) o["unset_outputs"] = new JArray(r.Unset);
            var print = r.Stdout?.Trim();
            if (!string.IsNullOrEmpty(print)) o["print"] = print.Length > 1500 ? print.Substring(0, 1500) + "..." : print;
            if (r.Status == "ok")
            {
                var outs = new JObject();
                foreach (var p in n.Params.Output) outs[p.NickName] = ParamData(p, sample);
                if (outs.Count > 0) o["out"] = outs;
            }
            return o;
        }

        /// <summary>Pin list with type + where the input value comes from.</summary>
        public static JArray Pins(AlienComponent n)
        {
            var arr = new JArray();
            foreach (var def in n.Header.Inputs)
            {
                var p = n.Params.Input.FirstOrDefault(x => x.Name == def.Name);
                string src = p != null && p.SourceCount > 0 ? "wired"
                    : def.Button ? "button: set_values true presses it"
                    : n.ManualValues.TryGetValue(def.Name, out var mv) ? "manual=" + mv
                    : def.Default != null ? "default=" + def.Default : "none";
                var hint = n.Hint(def.Name);
                if (hint != null && p?.SourceCount == 0)  // the script's run-time slider range and shown value
                    src += hint.Min.HasValue && hint.Max.HasValue ? $", range {hint.Min}-{hint.Max}" : "";
                if (hint?.Value != null && p?.SourceCount == 0 && !n.ManualValues.ContainsKey(def.Name))
                    src += ", shows " + hint.Value.ToString(Newtonsoft.Json.Formatting.None);
                arr.Add($"{def.Name}:{TypeMap.Describe(def)} ({src})");
            }
            return arr;
        }

        private static string LineOf(AlienComponent n, int line)
        {
            try
            {
                var lines = System.IO.File.ReadAllLines(n.ScriptPath);
                return line >= 1 && line <= lines.Length ? lines[line - 1].Trim() : null;
            }
            catch { return null; }
        }
    }
}
