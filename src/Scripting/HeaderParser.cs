using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

namespace Alien.Scripting
{
    public enum ParamAccess { Item, List, Tree }

    /// <summary>One declared input or output. Records give value equality for change detection.</summary>
    public sealed record ParamDef(
        string Name,
        string Type,          // normalised type key, see TypeMap ("float", "Point3d", "object", ...)
        ParamAccess Access,
        string Description,
        double? Min,
        double? Max,
        double? Step,
        int? Decimals,
        string Default,       // raw default token (number, true/false, string, JSON), resolved per type
        string Options = null, // options="a|b|c": the choices for a dropdown on the node
        bool Button = false)   // `button` on a bool: a momentary button (true for one solve) instead of a switch
    {
        public string[] Choices => string.IsNullOrWhiteSpace(Options)
            ? Array.Empty<string>()
            : Options.Split('|').Select(o => o.Trim()).Where(o => o.Length > 0).ToArray();
    }

    public sealed class ScriptHeader
    {
        public IReadOnlyList<ParamDef> Inputs { get; }
        public IReadOnlyList<ParamDef> Outputs { get; }
        public IReadOnlyList<string> Warnings { get; }

        public ScriptHeader(IReadOnlyList<ParamDef> inputs, IReadOnlyList<ParamDef> outputs, IReadOnlyList<string> warnings)
        {
            Inputs = inputs ?? Array.Empty<ParamDef>();
            Outputs = outputs ?? Array.Empty<ParamDef>();
            Warnings = warnings ?? Array.Empty<string>();
        }

        public static ScriptHeader Empty { get; } = new ScriptHeader(null, null, null);

        /// <summary>True when the pin layout (names, types, access, metadata) is identical.</summary>
        public bool SameAs(ScriptHeader other) =>
            other != null && Inputs.SequenceEqual(other.Inputs) && Outputs.SequenceEqual(other.Outputs);

        public ParamDef FindInput(string name) => Inputs.FirstOrDefault(p => p.Name == name);
    }

    /// <summary>
    /// Parses `NODE_INPUTS:` / `NODE_OUTPUTS:` comment lines (`#` for Python, `//` for C#).
    /// Token grammar: name[:type] [| "description"] [min= max= step= decimals= default= options="a|b"] [button]
    /// Tolerated variants: list[T], List[T], list&lt;T&gt;, T[], T:list, tree[T], T:tree, metadata without `|`.
    /// </summary>
    public static class HeaderParser
    {
        public const int MaxHeaderLines = 200;

        private static readonly Regex HeaderLine =
            new Regex(@"^\s*(?:#|//)\s*NODE_(INPUTS|OUTPUTS)\s*:\s*(.*)$", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        private static readonly Regex Identifier = new Regex(@"^[A-Za-z_][A-Za-z0-9_]*$", RegexOptions.Compiled);
        private static readonly Regex WrappedType = new Regex(@"^(list|tree)\s*[\[<]\s*(.+?)\s*[\]>]$", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex ButtonFlag = new Regex(@"(^|\s)button(\s|$)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex KeyValue = new Regex(@"\b(min|max|step|decimals|default|options)\s*=\s*(""[^""]*""|\[[^\]]*\]|[^\s,]+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);
        private static readonly Regex FreeRange = new Regex(@"Range\s*:\s*([-+]?\d*\.?\d+)\s*[-–—]\s*([-+]?\d*\.?\d+)", RegexOptions.Compiled | RegexOptions.IgnoreCase);

        public static ScriptHeader Parse(string source)
        {
            var inputs = new List<ParamDef>();
            var outputs = new List<ParamDef>();
            var warnings = new List<string>();
            if (string.IsNullOrEmpty(source)) return new ScriptHeader(inputs, outputs, warnings);

            using var reader = new StringReader(source);
            string line;
            int n = 0;
            while ((line = reader.ReadLine()) != null && ++n <= MaxHeaderLines)
            {
                var m = HeaderLine.Match(line);
                if (!m.Success) continue;
                bool isInput = m.Groups[1].Value.Equals("INPUTS", StringComparison.OrdinalIgnoreCase);
                var target = isInput ? inputs : outputs;
                foreach (var token in SplitTopLevel(m.Groups[2].Value))
                {
                    var def = ParseToken(token, warnings);
                    if (def == null) continue;
                    if (target.Any(p => p.Name == def.Name))
                    {
                        warnings.Add($"duplicate {(isInput ? "input" : "output")} '{def.Name}' ignored");
                        continue;
                    }
                    target.Add(def);
                }
            }

            // Same name as input and output is a deliberate pass-through pattern — allowed.
            return new ScriptHeader(inputs, outputs, warnings);
        }

        /// <summary>Split on commas outside quotes and brackets.</summary>
        internal static IEnumerable<string> SplitTopLevel(string raw)
        {
            var sb = new StringBuilder();
            bool inQuotes = false;
            int depth = 0;
            foreach (char c in raw)
            {
                if (c == '"') inQuotes = !inQuotes;
                else if (!inQuotes && (c == '[' || c == '<' || c == '(')) depth++;
                else if (!inQuotes && (c == ']' || c == '>' || c == ')')) depth = Math.Max(0, depth - 1);
                else if (c == ',' && !inQuotes && depth == 0)
                {
                    if (sb.Length > 0) yield return sb.ToString().Trim();
                    sb.Clear();
                    continue;
                }
                sb.Append(c);
            }
            if (sb.ToString().Trim().Length > 0) yield return sb.ToString().Trim();
        }

        private static ParamDef ParseToken(string token, List<string> warnings)
        {
            token = token.Trim();
            if (token.Length == 0) return null;

            // Split declaration from metadata: explicit `|`, else first whitespace (e.g. `seed:int default=42`).
            string decl, meta;
            int pipe = IndexOutsideQuotes(token, '|');
            if (pipe >= 0)
            {
                decl = token.Substring(0, pipe).Trim();
                meta = token.Substring(pipe + 1).Trim();
            }
            else
            {
                int ws = IndexOfWhitespaceOutsideBrackets(token);
                decl = ws >= 0 ? token.Substring(0, ws).Trim() : token;
                meta = ws >= 0 ? token.Substring(ws + 1).Trim() : "";
            }

            // name[:type[:list|tree]]
            var parts = decl.Split(new[] { ':' }, 3);
            var name = parts[0].Trim();
            if (!Identifier.IsMatch(name))
            {
                warnings.Add($"invalid parameter name '{name}' skipped");
                return null;
            }

            string typeRaw = parts.Length > 1 ? parts[1].Trim() : "";
            var access = ParamAccess.Item;
            if (parts.Length > 2)
            {
                var suffix = parts[2].Trim().ToLowerInvariant();
                if (suffix == "list") access = ParamAccess.List;
                else if (suffix == "tree") access = ParamAccess.Tree;
                else warnings.Add($"'{name}': unknown access '{parts[2]}'");
            }

            var wrapped = WrappedType.Match(typeRaw);
            if (wrapped.Success)
            {
                access = wrapped.Groups[1].Value.Equals("tree", StringComparison.OrdinalIgnoreCase) ? ParamAccess.Tree : ParamAccess.List;
                typeRaw = wrapped.Groups[2].Value;
            }
            else if (typeRaw.EndsWith("[]", StringComparison.Ordinal))
            {
                access = ParamAccess.List;
                typeRaw = typeRaw.Substring(0, typeRaw.Length - 2);
            }
            else if (typeRaw.Equals("list", StringComparison.OrdinalIgnoreCase))
            {
                access = ParamAccess.List;
                typeRaw = "";
            }

            var type = TypeMap.Normalize(typeRaw, out bool known);
            if (!known) warnings.Add($"'{name}': unknown type '{typeRaw}', treated as object");

            return ParseMeta(name, type, access, meta);
        }

        private static ParamDef ParseMeta(string name, string type, ParamAccess access, string meta)
        {
            string description = "";
            var rest = meta;
            if (rest.StartsWith("\"", StringComparison.Ordinal))
            {
                int close = rest.IndexOf('"', 1);
                if (close > 0)
                {
                    description = rest.Substring(1, close - 1);
                    rest = rest.Substring(close + 1);
                }
            }

            double? min = null, max = null, step = null;
            int? decimals = null;
            string def = null, options = null;
            foreach (Match kv in KeyValue.Matches(rest))
            {
                var key = kv.Groups[1].Value.ToLowerInvariant();
                var val = kv.Groups[2].Value;
                switch (key)
                {
                    case "min": min = ParseDouble(val); break;
                    case "max": max = ParseDouble(val); break;
                    case "step": step = ParseDouble(val); break;
                    case "decimals": decimals = int.TryParse(val, out var d) ? d : null; break;
                    case "default": def = val.Trim('"'); break;
                    case "options": options = val.Trim('"'); break;
                }
            }

            if (min == null || max == null)
            {
                var fr = FreeRange.Match(description + " " + rest);
                if (fr.Success)
                {
                    var a = ParseDouble(fr.Groups[1].Value);
                    var b = ParseDouble(fr.Groups[2].Value);
                    if (a.HasValue && b.HasValue)
                    {
                        min ??= Math.Min(a.Value, b.Value);
                        max ??= Math.Max(a.Value, b.Value);
                    }
                }
            }

            bool button = type == "bool" && ButtonFlag.IsMatch(Regex.Replace(rest, "\"[^\"]*\"", ""));
            return new ParamDef(name, type, access, description, min, max, step, decimals, def, options, button);
        }

        private static double? ParseDouble(string s) =>
            double.TryParse(s, NumberStyles.Float, CultureInfo.InvariantCulture, out var v) ? v : null;

        private static int IndexOutsideQuotes(string s, char target)
        {
            bool inQ = false;
            for (int i = 0; i < s.Length; i++)
            {
                if (s[i] == '"') inQ = !inQ;
                else if (s[i] == target && !inQ) return i;
            }
            return -1;
        }

        private static int IndexOfWhitespaceOutsideBrackets(string s)
        {
            int depth = 0;
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c == '[' || c == '<') depth++;
                else if (c == ']' || c == '>') depth = Math.Max(0, depth - 1);
                else if (char.IsWhiteSpace(c) && depth == 0)
                {
                    // `name : type` style spacing is not a metadata boundary
                    var before = s.Substring(0, i).TrimEnd();
                    var after = s.Substring(i).TrimStart();
                    if (before.EndsWith(":") || after.StartsWith(":")) continue;
                    return i;
                }
            }
            return -1;
        }
    }
}
