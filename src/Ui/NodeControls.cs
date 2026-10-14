using System;
using System.Collections;
using System.Globalization;
using System.Linq;

using Newtonsoft.Json.Linq;

using Alien.Scripting;

namespace Alien.Ui
{
    public enum ControlKind { None, Slider, Toggle, Button, Choice, Text, Pick }

    /// <summary>
    /// A script's run-time tuning for one input's control (its NODE_CONTROLS entry): a slider range that suits the
    /// result, the value to show while the input has no manual value, and a key naming what the value belongs to.
    /// </summary>
    public sealed class ControlHint
    {
        public double? Min, Max, Step;
        public int? Decimals;
        public JToken Value;
        public string Key;

        public static ControlHint From(JToken t)
        {
            if (!(t is JObject o)) return null;
            double? Num(string k) => o[k] != null && (o[k].Type == JTokenType.Float || o[k].Type == JTokenType.Integer) ? o.Value<double>(k) : (double?)null;
            return new ControlHint
            {
                Min = Num("min"), Max = Num("max"), Step = Num("step"),
                Decimals = o["decimals"]?.Type == JTokenType.Integer ? o.Value<int>("decimals") : (int?)null,
                Value = o["value"], Key = o["key"]?.ToString(),
            };
        }
    }

    /// <summary>
    /// Which control an unwired input gets on the node, and the value plumbing behind it. Values are the node's
    /// manual values (the same ones agents set with set_values), so canvas and MCP always agree.
    /// </summary>
    public static class NodeControls
    {
        private static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        public static ControlKind KindOf(ParamDef d)
        {
            if (d.Access == ParamAccess.Item)
                return d.Type switch
                {
                    "float" or "int" => ControlKind.Slider,
                    "bool" => d.Button ? ControlKind.Button : ControlKind.Toggle,
                    "str" => d.Choices.Length > 0 ? ControlKind.Choice : ControlKind.Text,
                    _ => Picks.IsPickable(d) ? ControlKind.Pick : ControlKind.None,
                };
            // Lists of plain values are typed as "a, b, c"; lists of geometry are picked in Rhino.
            if (d.Access == ParamAccess.List && d.Type is "str" or "float" or "int") return ControlKind.Text;
            return Picks.IsPickable(d) ? ControlKind.Pick : ControlKind.None;
        }

        public readonly struct SliderRange
        {
            public readonly double Min, Max, Step;
            public readonly int Decimals;
            public readonly bool Integer;

            public SliderRange(double min, double max, double step, int decimals, bool integer)
            {
                Min = min; Max = max; Step = step; Decimals = decimals; Integer = integer;
            }

            public double Snap(double v)
            {
                v = Math.Max(Min, Math.Min(Max, v));
                if (Step > 0) v = Min + Math.Round((v - Min) / Step) * Step;
                return Integer ? Math.Round(v) : Math.Round(v, Decimals);
            }

            public string Format(double v) => Integer ? ((long)Math.Round(v)).ToString(Inv) : v.ToString("F" + Decimals, Inv);
        }

        /// <summary>
        /// The script's run-time range (NODE_CONTROLS), else the header's min/max, else a recommended range around the
        /// default.
        /// </summary>
        public static SliderRange Range(ParamDef d, double? current, ControlHint hint = null)
        {
            bool integer = d.Type == "int";
            double? dv = double.TryParse(d.Default, NumberStyles.Float, Inv, out var parsed) ? parsed : (double?)null;
            double anchor = dv ?? current ?? 0;
            double unit = integer || anchor == 0 ? 10 : 1; // a 0 default says nothing about scale: offer 0-10
            double min = hint?.Min ?? d.Min ?? (anchor < 0 ? -Nice(2 * -anchor) : 0);
            double max = hint?.Max ?? d.Max ?? Nice(Math.Max(anchor > 0 ? 2 * anchor : unit, min + unit));
            if (current.HasValue) { min = Math.Min(min, current.Value); max = Math.Max(max, current.Value); }
            if (max <= min) max = min + unit;
            double span = max - min;
            int decimals = hint?.Decimals ?? d.Decimals ?? (integer ? 0 : Math.Max(span <= 2 ? 2 : span <= 100 ? 1 : 0, DecimalsOf(d.Default)));
            double step = hint?.Step ?? d.Step ?? (integer ? 1 : Math.Pow(10, -decimals));
            return new SliderRange(min, max, step, decimals, integer);
        }

        /// <summary>Decimals written in a default ("1.75" -> 2), so the slider can show and keep it.</summary>
        private static int DecimalsOf(string number)
        {
            int dot = (number ?? "").IndexOf('.');
            return dot < 0 ? 0 : Math.Min(4, number.Length - dot - 1);
        }

        /// <summary>1, 2 or 5 times a power of ten, at or above v.</summary>
        private static double Nice(double v)
        {
            if (v <= 0) return 1;
            double e = Math.Pow(10, Math.Floor(Math.Log10(v)));
            double m = v / e;
            return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * e;
        }

        /// <summary>
        /// What the control shows: the manual value, else the value the script reported for it (NODE_CONTROLS, e.g. the
        /// size it built), else the header default, else null.
        /// </summary>
        public static object Current(AlienComponent node, ParamDef d)
        {
            if (node.ManualValues.TryGetValue(d.Name, out var raw))
            {
                var v = Values.Resolve(raw, d);
                if (v != null) return v;
            }
            if (node.Hint(d.Name)?.Value is JToken shown && shown.Type != JTokenType.Null)
            {
                var v = Values.Resolve(shown.ToString(Newtonsoft.Json.Formatting.None), d);
                if (v != null) return v;
            }
            return d.Default != null ? Values.Resolve(d.Default, d) : null;
        }

        public static double? AsNumber(object v) => v switch
        {
            double d => d,
            int i => i,
            long l => l,
            float f => f,
            _ => null,
        };

        /// <summary>Text shown in (and edited from) a text control.</summary>
        public static string AsText(object v) => v switch
        {
            null => "",
            string s => s,
            IEnumerable e => string.Join(", ", e.Cast<object>().Select(o => Convert.ToString(o, Inv))),
            _ => Convert.ToString(v, Inv),
        };

        /// <summary>Typed text -> the manual value to store (lists stay "a, b, c" and are split when resolved).</summary>
        public static JToken FromText(ParamDef d, string text)
        {
            text = (text ?? "").Trim();
            if (d.Access == ParamAccess.Item && d.Type is "float" or "int")
                return double.TryParse(text, NumberStyles.Float, Inv, out var v) ? (d.Type == "int" ? new JValue((long)Math.Round(v)) : new JValue(v)) : null;
            return new JValue(text);
        }
    }
}
