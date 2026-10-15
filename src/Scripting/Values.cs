using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.Linq;

using Grasshopper.Kernel.Types;

using Newtonsoft.Json.Linq;

using Rhino.Collections;
using Rhino.Geometry;

namespace Alien.Scripting
{
    /// <summary>Value plumbing: GH goo → plain values, manual/default tokens → typed values, script outputs → GH-friendly values.</summary>
    public static class Values
    {
        // ── Incoming ────────────────────────────────────────────────

        /// <summary>
        /// Strip Grasshopper wrappers so scripts see RhinoCommon / primitive values. Plugin goo types that don't
        /// implement ScriptVariable (it returns null) are passed through as the goo itself rather than lost.
        /// </summary>
        public static object Unwrap(object o)
        {
            if (o is IGH_Goo goo)
            {
                try { return goo.ScriptVariable() ?? goo; }
                catch { return goo; }
            }
            return o;
        }

        /// <summary>
        /// Resolve a stored manual value or header default (raw JSON or bare token) for a param.
        /// Returns null for item params / empty list for list params when nothing usable is given.
        /// </summary>
        public static object Resolve(string raw, ParamDef def)
        {
            JToken token = ParseToken(raw);
            // Objects picked in Rhino: {"pick": ["guid", ...]} -> their current geometry
            if (Ui.Picks.Ids(token) is JArray picked && Ui.Picks.IsPickable(def)) return Ui.Picks.Resolve(picked, def);
            if (def.Access == ParamAccess.Item)
                return token == null ? null : ResolveItem(token, def.Type);

            var list = new List<object>();
            if (token == null) return list;
            if (token is JArray arr && !IsVectorLiteral(arr, def.Type))
            {
                if (IsCurveType(def.Type) && IsPointList(arr)) list.Add(ResolveItem(arr, def.Type)); // one curve's points
                else foreach (var t in arr) list.Add(ResolveItem(t, def.Type));
            }
            else if (token.Type == JTokenType.String && def.Type is "float" or "int" or "bool" && token.ToString().Contains(','))
            {
                foreach (var part in token.ToString().Split(','))
                    list.Add(ResolveItem(new JValue(part.Trim()), def.Type));
            }
            else list.Add(ResolveItem(token, def.Type));
            return list;
        }

        /// <summary>Default value for a param with no wire, manual value or header default.</summary>
        public static object Empty(ParamDef def, bool valueTypesNeedDefault)
        {
            if (def.Access != ParamAccess.Item) return new List<object>();
            if (!valueTypesNeedDefault) return null;
            var clr = TypeMap.ClrType(def.Type);
            return clr.IsValueType ? Activator.CreateInstance(clr) : null;
        }

        private static JToken ParseToken(string raw)
        {
            if (raw == null) return null;
            raw = raw.Trim();
            if (raw.Length == 0) return null;
            try { return JToken.Parse(raw); }
            catch { return new JValue(raw); } // bare word, e.g. default=hello
        }

        private static bool IsVectorLiteral(JArray arr, string type) =>
            (type is "Point3d" or "Vector3d" or "Interval" or "Color") && arr.All(t => t.Type is JTokenType.Integer or JTokenType.Float);

        public static object ResolveItem(JToken t, string type)
        {
            if (t == null || t.Type == JTokenType.Null) return null;
            var inv = CultureInfo.InvariantCulture;
            string s = t.Type == JTokenType.String ? (string)t : t.ToString(Newtonsoft.Json.Formatting.None);
            try
            {
                switch (type)
                {
                    case "float": return t.Type is JTokenType.Float or JTokenType.Integer ? (double)t : double.Parse(s, inv);
                    case "int": return t.Type is JTokenType.Float or JTokenType.Integer ? (int)Math.Round((double)t) : (int)Math.Round(double.Parse(s, inv));
                    case "bool": return t.Type == JTokenType.Boolean ? (bool)t : s.Trim() is "1" || s.Trim().Equals("true", StringComparison.OrdinalIgnoreCase);
                    case "str": return s;
                    case "Point3d": { var v = Numbers(t, 3); return v == null ? null : new Point3d(v[0], v[1], v[2]); }
                    case "Vector3d": { var v = Numbers(t, 3); return v == null ? null : new Vector3d(v[0], v[1], v[2]); }
                    case "Interval": { var v = Numbers(t, 2); return v == null ? null : new Interval(v[0], v[1]); }
                    case "Color": return ParseColor(t, s);
                    case "Curve": return (object)CurveFrom(t) ?? ToPlain(t);
                    case "Line": { var c = CurveFrom(t); return c is LineCurve lc ? lc.Line : (object)null; }
                    case "object": return ToPlain(t);
                    default: return ToPlain(t); // geometry cannot be typed in by hand
                }
            }
            catch { return null; }
        }

        private static bool IsCurveType(string type) => type is "Curve" or "Line";

        /// <summary>[[x,y,z], [x,y,z], ...]: two or more points (z optional), in model units.</summary>
        private static bool IsPointList(JArray a) =>
            a.Count >= 2 && a.All(p => p is JArray q && q.Count is 2 or 3 && q.All(n => n.Type is JTokenType.Integer or JTokenType.Float));

        /// <summary>A curve typed as its points (agents and the project agent set curve inputs this way): two points
        /// make a line, more a polyline (repeat the first point to close it).</summary>
        private static Curve CurveFrom(JToken t)
        {
            if (t is not JArray a || !IsPointList(a)) return null;
            var pts = a.Select(p => (JArray)p).Select(q => new Point3d((double)q[0], (double)q[1], q.Count > 2 ? (double)q[2] : 0)).ToList();
            return pts.Count == 2 ? new LineCurve(pts[0], pts[1]) : new PolylineCurve(pts);
        }

        private static double[] Numbers(JToken t, int count)
        {
            IEnumerable<double> nums;
            if (t is JArray a) nums = a.Select(x => (double)x);
            else if (t is JObject o) nums = new[] { "x", "y", "z" }.Take(count).Select(k => (double?)o[k] ?? 0);
            else nums = t.ToString().Split(new[] { ',', ';', ' ' }, StringSplitOptions.RemoveEmptyEntries)
                    .Select(p => double.Parse(p.Trim('{', '}', '(', ')', '[', ']'), CultureInfo.InvariantCulture));
            var arr = nums.ToArray();
            if (arr.Length < count) return null;
            return arr;
        }

        private static object ParseColor(JToken t, string s)
        {
            if (t is JArray a && a.Count >= 3)
            {
                var c = a.Select(x => (int)x).ToArray();
                return c.Length >= 4 ? System.Drawing.Color.FromArgb(c[3], c[0], c[1], c[2]) : System.Drawing.Color.FromArgb(c[0], c[1], c[2]);
            }
            s = s.Trim();
            if (s.StartsWith("#"))
            {
                var hex = s.Substring(1);
                var v = uint.Parse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture);
                return hex.Length == 8 ? System.Drawing.Color.FromArgb((int)v) : System.Drawing.Color.FromArgb(255, (int)(v >> 16) & 255, (int)(v >> 8) & 255, (int)v & 255);
            }
            var named = System.Drawing.Color.FromName(s);
            return named.IsKnownColor ? named : null;
        }

        private static object ToPlain(JToken t) => t.Type switch
        {
            JTokenType.Integer => (double)t,
            JTokenType.Float => (double)t,
            JTokenType.Boolean => (bool)t,
            JTokenType.String => (string)t,
            JTokenType.Array => t.Select(ToPlain).ToList(),
            _ => t.ToString(Newtonsoft.Json.Formatting.None)
        };

        // ── Outgoing ────────────────────────────────────────────────

        /// <summary>Make script output items Grasshopper-friendly (Polyline → PolylineCurve, Box → Brep etc. stay as-is otherwise).</summary>
        public static object Normalise(object o) => o switch
        {
            Polyline pl => new PolylineCurve(pl),
            Point3dList pts => pts.ToList(),
            _ => o
        };

        /// <summary>Sequence (list/tuple/array) but not a string or a geometry type that happens to be enumerable.</summary>
        public static bool IsSequence(object o) =>
            o is IEnumerable && o is not string && o is not Polyline && o is not IGH_Goo && o is not GeometryBase;

        public static List<object> ToList(object o)
        {
            var list = new List<object>();
            foreach (var item in (IEnumerable)o) list.Add(item);
            return list;
        }

        public static string ToInvariant(object o) => o switch
        {
            null => "null",
            double d => d.ToString("0.####", CultureInfo.InvariantCulture),
            float f => f.ToString("0.####", CultureInfo.InvariantCulture),
            IFormattable fmt => fmt.ToString(null, CultureInfo.InvariantCulture),
            _ => o.ToString()
        };
    }
}
