using System;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using System.Text.RegularExpressions;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Alien.Mcp;
using Alien.Scripting;

namespace Alien.Ai
{
    /// <summary>Everything the model reads, and how its reply is turned back into a script.</summary>
    internal static class Prompts
    {
        private static readonly string[] GuideSections = { "Header", "Types", "Inputs and outputs", "Gotchas" };

        public static string System(ScriptLanguage lang)
        {
            bool cs = lang == ScriptLanguage.CSharp;
            string c = cs ? "//" : "#", fence = cs ? "csharp" : "python";
            var sb = new StringBuilder();
            sb.Append($@"You write the script for an Alien node: a Grasshopper (Rhino 8) component whose logic lives in one {(cs ? "C#" : "Python 3")} file.
Comment lines at the top of the file (the header) define the node's input and output pins.

Reply with exactly one ```{fence} code block holding the complete file, then at most one short sentence. Never more than one code block.

File layout:
{c} NODE_INPUTS: ...
{c} NODE_OUTPUTS: ...
{c} <one line saying what the node does; it becomes the node's library listing>
<code>

Rules:
- Give number inputs a sensible default= (and min/max where natural) so the node works before anything is wired.
- When a geometry input is not wired (None, or an empty list), use small sample geometry instead so the node shows a result straight away. Mark it with a short comment.
- Assign every output on every path.
- Geometry: RhinoCommon only ({(cs ? "using Rhino.Geometry;" : "import Rhino.Geometry as rg")}). Don't use rhinoscriptsyntax or scriptcontext, and don't add objects to the Rhino document.
- Use RhinoCommon's built-in methods (Curve.Offset, Mesh.CreateContourCurves, Brep.CreateFromLoft, Intersection.*, …) instead of
  hand-writing geometry algorithms. If a member doesn't exist, the error comes back to you to fix.
- Keep it short and readable.{(cs ? " Top-level statements; local functions are fine; no top-level `return`." : " No print() unless asked.")}

Reference:
");
            var guide = Tools.LoadGuide();
            foreach (var title in GuideSections.Append(cs ? "CSharp" : "Python"))
            {
                var s = Section(guide, title);
                if (s != null) sb.Append(s).Append("\n\n");
            }
            return sb.ToString().TrimEnd();
        }

        public static string Request(string request, IReadOnlyList<string> earlier, NodeContext ctx, ScriptLanguage lang)
        {
            var sb = new StringBuilder();
            sb.Append("Request: ").Append(request.Trim()).Append("\n\n");
            if (earlier.Count > 0)
            {
                sb.Append("Earlier requests for this node, oldest first:\n");
                foreach (var e in earlier) sb.Append("- ").Append(e).Append('\n');
                sb.Append('\n');
            }
            if (ctx.Source != null)
            {
                sb.Append("Current file (change it to meet the request; keep pin names that still apply, because renaming a pin drops its wires):\n")
                  .Append("```").Append(lang == ScriptLanguage.CSharp ? "csharp" : "python").Append('\n')
                  .Append(ctx.Source.TrimEnd()).Append("\n```\n\n");
            }
            else sb.Append("The node has no script yet.\n\n");

            if (ctx.Wired.Count > 0)
            {
                sb.Append("Wired inputs (what arrives on each pin right now):\n");
                foreach (var w in ctx.Wired) sb.Append("- ").Append(w).Append('\n');
            }
            else sb.Append("Nothing is wired into the node yet.\n");
            return sb.ToString();
        }

        public static string Retry(string problem) =>
            "The node failed:\n" + problem + "\n\nFix the file. Reply with the complete corrected file in one code block.";

        /// <summary>A run report trimmed to what helps the model fix the script.</summary>
        public static string Failure(JObject report)
        {
            var r = (JObject)report.DeepClone();
            r.Remove("id");
            r.Remove("ms");
            return r.ToString(Formatting.None);
        }

        private static readonly Regex Think = new Regex(@"<think>.*?</think>", RegexOptions.Singleline | RegexOptions.Compiled);
        private static readonly Regex Fence = new Regex(@"```[^\n]*\n(.*?)```", RegexOptions.Singleline | RegexOptions.Compiled);
        private static readonly Regex OpenFence = new Regex(@"```[^\n]*\n(.*)$", RegexOptions.Singleline | RegexOptions.Compiled);

        /// <summary>The script inside the reply: the fenced block with a header, else the longest block. Null if none.</summary>
        public static string ExtractCode(string reply)
        {
            reply = Think.Replace(reply ?? "", "");
            var blocks = Fence.Matches(reply).Select(m => m.Groups[1].Value).ToList();
            var code = blocks.FirstOrDefault(IsScript) ?? blocks.OrderByDescending(b => b.Length).FirstOrDefault();
            if (code == null)
            {
                // Truncated reply (no closing fence) or a bare file.
                var open = OpenFence.Match(reply);
                code = open.Success ? open.Groups[1].Value : IsScript(reply) ? reply : null;
            }
            return string.IsNullOrWhiteSpace(code) ? null : code.Trim('\n', '\r') + "\n";
        }

        private static bool IsScript(string s) => s.Contains("NODE_OUTPUTS") || s.Contains("NODE_INPUTS");

        private static readonly HashSet<string> Stop = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
            { "a", "an", "the", "into", "in", "of", "on", "by", "to", "at", "and", "with", "for", "from", "make", "node", "that", "each", "me" };

        /// <summary>"divide a curve into N points" → "divide_curve_n_points".</summary>
        public static string FileName(string request)
        {
            var words = Regex.Matches(request.ToLowerInvariant(), "[a-z0-9]+").Select(m => m.Value).Where(w => !Stop.Contains(w)).Take(4).ToList();
            return words.Count == 0 ? "described" : string.Join("_", words);
        }

        private static string Section(string guide, string title)
        {
            foreach (var s in guide.Split(new[] { "\n## " }, StringSplitOptions.None).Skip(1))
                if (s.Split('\n')[0].Trim().Equals(title, StringComparison.OrdinalIgnoreCase))
                    return "## " + s.Trim();
            return null;
        }
    }
}
