using System;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json.Linq;

using Grasshopper.Kernel;

using Alien.Mcp;
using Alien.Scripting;

namespace Alien.Ai
{
    public sealed class Attempt
    {
        public int N { get; set; }
        /// <summary>ok | error | unset | empty | no-outputs | no-code | stuck | model-error</summary>
        public string Status { get; set; }
        /// <summary>What was sent back to the model (null when the attempt worked).</summary>
        public string Problem { get; set; }
        /// <summary>One-line version for people ("NameError: name 'x' is not defined (line 12)").</summary>
        public string Note { get; set; }
        public double Seconds { get; set; }
    }

    public sealed class GenerateResult
    {
        public bool Ok { get; set; }
        public string Model { get; set; }
        public List<Attempt> Attempts { get; } = new List<Attempt>();
        public int PromptTokens { get; set; }
        public int CompletionTokens { get; set; }
        public double Cost { get; set; }
        /// <summary>Why it stopped, when it didn't work.</summary>
        public string Error { get; set; }
        /// <summary>The node's last run report.</summary>
        public JObject Report { get; set; }

        public JObject ToJson()
        {
            var o = new JObject
            {
                ["ok"] = Ok,
                ["model"] = Model,
                ["attempts"] = new JArray(Attempts.Select(a =>
                {
                    var j = new JObject { ["n"] = a.N, ["status"] = a.Status, ["s"] = a.Seconds };
                    if (a.Note != null) j["note"] = a.Note;
                    return j;
                })),
                ["tokens"] = PromptTokens + CompletionTokens,
                ["cost"] = Math.Round(Cost, 5),
            };
            if (!Ok && Error != null) o["error"] = Error;
            if (Report != null) o["report"] = Report;
            return o;
        }
    }

    /// <summary>What the model is told about the node it is writing for (read on the UI thread).</summary>
    internal sealed class NodeContext
    {
        public string Path { get; private set; }
        public string Source { get; private set; }
        public ScriptLanguage Language { get; private set; }
        public List<string> Wired { get; } = new List<string>();
        /// <summary>The nodes selected with the request: what each is and what it outputs now.</summary>
        public List<string> Selected { get; } = new List<string>();
        /// <summary>The Rhino document's units, e.g. "Millimeters (1 m = 1000 units)".</summary>
        public string Units { get; private set; }

        public static NodeContext Read(AlienComponent node)
        {
            var c = new NodeContext { Path = node.ScriptPath, Language = node.Language };
            var rdoc = Rhino.RhinoDoc.ActiveDoc;
            if (rdoc != null)
            {
                double perMetre = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, rdoc.ModelUnitSystem);
                c.Units = $"{rdoc.ModelUnitSystem} (1 m = {perMetre.ToString("0.######", System.Globalization.CultureInfo.InvariantCulture)} units)";
            }
            if (c.Path != null && File.Exists(c.Path)) c.Source = File.ReadAllText(c.Path).Replace("\r\n", "\n");
            if (c.Language == ScriptLanguage.None && c.Path != null) c.Language = ScriptLanguages.FromPath(c.Path);
            foreach (var p in node.Params.Input)
                if (p.SourceCount > 0) c.Wired.Add($"{p.NickName}: {Describe.ParamData(p, 1)}");
            var doc = node.OnPingDocument();
            foreach (var id in node.ContextIds)
            {
                var o = doc?.FindObject(id, true);
                if (o != null && o != node) c.Selected.Add(About(o));
            }
            return c;
        }

        /// <summary>A selected node for the model: id, what it is (an Alien node's header), and its outputs right now.</summary>
        internal static string About(IGH_DocumentObject o)
        {
            var sb = new StringBuilder();
            string id = Describe.ShortId(o), nick = string.IsNullOrEmpty(o.NickName) || o.NickName == o.Name ? "" : $" (nickname \"{o.NickName}\")";
            IEnumerable<IGH_Param> outs = Array.Empty<IGH_Param>();
            if (o is AlienComponent a)
            {
                string src = a.ScriptPath != null && File.Exists(a.ScriptPath) ? File.ReadAllText(a.ScriptPath).Replace("\r\n", "\n") : null;
                sb.Append($"[{id}] ").Append(a.ScriptPath == null ? "a blank Alien node" : $"Alien node \"{System.IO.Path.GetFileNameWithoutExtension(a.ScriptPath)}\"");
                var what = Ui.ScriptInfo.Description(src);
                if (what != null) sb.Append(": ").Append(what);
                sb.Append('\n');
                foreach (var line in (src ?? "").Split('\n').Take(40).Where(l => l.Contains("NODE_INPUTS") || l.Contains("NODE_OUTPUTS")))
                    sb.Append("  ").Append(line.Trim()).Append('\n');
                outs = a.Params.Output;
            }
            else if (o is IGH_Component comp)
            {
                sb.Append($"[{id}] Grasshopper component \"{comp.Name}\"{nick} ({comp.Category} > {comp.SubCategory})\n");
                outs = comp.Params.Output;
            }
            else if (o is IGH_Param p)
            {
                sb.Append($"[{id}] Grasshopper {p.Name}{nick}: {Describe.ParamData(p, 2)}\n");
                return sb.ToString().TrimEnd();
            }
            else return $"[{id}] {o.Name}{nick}";
            var list = outs.ToList();
            if (list.Count > 0)
            {
                sb.Append("  outputs now:\n");
                foreach (var p in list)
                {
                    sb.Append($"  - {p.NickName}: {Describe.ParamData(p, 2)}\n");
                    // Building-suite records are JSON text: one in full, so the model knows the fields.
                    var rec = p.VolatileData?.AllData(true).Select(g => Scripting.Values.Unwrap(g) as string)
                        .FirstOrDefault(x => x != null && x.TrimStart().StartsWith("{\"alien\"", StringComparison.Ordinal));
                    if (rec != null) sb.Append("    one item in full: ").Append(rec.Length > 700 ? rec.Substring(0, 700) + "…" : rec).Append('\n');
                }
            }
            return sb.ToString().TrimEnd();
        }
    }

    /// <summary>
    /// Describe-a-node: request → the model writes the whole script → Alien rebuilds and solves → on failure the run
    /// report goes back to the model → retry, up to maxAttempts → stop with the last problem.
    /// Runs off the UI thread; every Rhino/GH call goes through <see cref="UiThread"/>.
    /// </summary>
    public static class Generator
    {
        public const int DefaultAttempts = 4;

        /// <summary>Requests that worked, per node, so follow-ups ("make it hexagonal") have context.</summary>
        private static readonly ConcurrentDictionary<Guid, List<string>> Requests = new ConcurrentDictionary<Guid, List<string>>();

        public static async Task<GenerateResult> Run(AlienComponent node, string request,
            ScriptLanguage language = ScriptLanguage.Python3, string model = null, int maxAttempts = DefaultAttempts,
            Action<string> progress = null, CancellationToken ct = default)
        {
            var choice = ModelChoice.Resolve(model);
            var ctx = UiThread.Run(() => NodeContext.Read(node));
            if (ctx.Path == null && !ScriptRepository.IsConfigured)
                throw new InvalidOperationException("Script repository not set. Right-click the node > Script repository.");

            var lang = ctx.Language != ScriptLanguage.None ? ctx.Language : language;
            var earlier = Requests.TryGetValue(node.InstanceGuid, out var done) ? done.ToList() : new List<string>();
            var messages = new List<ChatMessage>
            {
                new ChatMessage("system", Prompts.System(lang)),
                new ChatMessage("user", Prompts.Request(request, earlier, ctx, lang)),
            };

            // Progress goes to the caller and onto the node itself, so the canvas shows what is happening.
            void Say(string text)
            {
                progress?.Invoke(text);
                UiThread.Run(() => node.SetActivity(text));
            }

            try { return await Attempts(node, request, lang, choice, messages, maxAttempts, Say, ct).ConfigureAwait(false); }
            finally { UiThread.Run(() => node.SetActivity(null)); }
        }

        private static async Task<GenerateResult> Attempts(AlienComponent node, string request, ScriptLanguage lang,
            ModelChoice choice, List<ChatMessage> messages, int maxAttempts, Action<string> say, CancellationToken ct)
        {
            var result = new GenerateResult { Model = choice.Label };
            for (int n = 1; n <= maxAttempts; n++)
            {
                ct.ThrowIfCancellationRequested();
                var clock = Stopwatch.StartNew();
                var attempt = new Attempt { N = n };
                result.Attempts.Add(attempt);

                say($"{n}/{maxAttempts} · asking {ShortName(choice.Label)}…");
                ModelReply reply;
                try { reply = await ModelClient.Complete(choice, messages, ct).ConfigureAwait(false); }
                catch (Exception ex) when (!ct.IsCancellationRequested) // an HttpClient timeout is not a user cancel
                {
                    attempt.Status = "model-error";
                    attempt.Seconds = Math.Round(clock.Elapsed.TotalSeconds, 1);
                    result.Error = ex is OperationCanceledException ? $"{choice.Label} did not answer within 4 minutes." : ex.Message;
                    attempt.Note = result.Error;
                    return result;
                }
                result.PromptTokens += reply.PromptTokens;
                result.CompletionTokens += reply.CompletionTokens;
                result.Cost += reply.Cost;
                messages.Add(new ChatMessage("assistant", reply.Text));

                string problem;
                var code = Prompts.ExtractCode(reply.Text);
                if (code == null)
                {
                    attempt.Status = "no-code";
                    attempt.Note = "the reply had no code";
                    problem = "Your reply had no code block.";
                }
                else
                {
                    say($"{n}/{maxAttempts} · running…");
                    result.Report = Apply(node, code, lang, request, Prompts.Wires(reply.Text));
                    string note;
                    (attempt.Status, problem, note) = Judge(node, result.Report);
                    attempt.Note = note;
                }
                attempt.Seconds = Math.Round(clock.Elapsed.TotalSeconds, 1);

                if (problem == null)
                {
                    Requests.AddOrUpdate(node.InstanceGuid, _ => new List<string> { request }, (_, l) => { l.Add(request); return l; });
                    result.Ok = true;
                    result.Error = null;
                    return result;
                }
                attempt.Problem = problem;
                result.Error = attempt.Note;
                if (n < maxAttempts) say($"{n}/{maxAttempts} · {attempt.Status}, fixing…");
                messages.Add(new ChatMessage("user", Prompts.Retry(problem)));
            }
            return result;
        }

        /// <summary>"deepseek/deepseek-v4.1-flash" → "deepseek-v4.1-flash".</summary>
        private static string ShortName(string model)
        {
            int slash = model.LastIndexOf('/');
            return slash >= 0 ? model.Substring(slash + 1) : model;
        }

        /// <summary>
        /// Write the script (creating it on the first run), wait for the re-solve, make the wires the model asked for
        /// (from selected nodes to the new pins) and solve again, then return the run report.
        /// </summary>
        private static JObject Apply(AlienComponent node, string code, ScriptLanguage lang, string request, IReadOnlyList<(string input, string source, string output)> wires = null)
        {
            int stamp = UiThread.Run(() =>
            {
                int s = node.SolveStamp;
                if (node.ScriptPath == null)
                    node.SetScript(ScriptRepository.CreateScript(Prompts.FileName(request), lang, code));
                else
                {
                    ScriptRepository.WriteScript(node.ScriptPath, code);
                    node.Reload();
                }
                return s;
            });
            Tools.WaitForSolve(node, stamp);
            if (wires != null && wires.Count > 0)
            {
                int again = UiThread.Run(() =>
                {
                    int s = node.SolveStamp;
                    if (Connect(node, wires) == 0) return -1;
                    node.ExpireSolution(true);
                    return s;
                });
                if (again >= 0) Tools.WaitForSolve(node, again);
            }
            return UiThread.Run(() => Describe.Run(node, 1));
        }

        /// <summary>Wire selected nodes' outputs into the node's inputs (UI thread). Returns how many wires were added.</summary>
        internal static int Connect(AlienComponent node, IReadOnlyList<(string input, string source, string output)> wires)
        {
            var doc = node.OnPingDocument();
            if (doc == null) return 0;
            var context = node.ContextIds.Select(g => doc.FindObject(g, true)).Where(o => o != null).ToList();
            int added = 0;
            foreach (var (input, source, output) in wires)
            {
                var target = node.Params.Input.FirstOrDefault(p => string.Equals(p.Name, input, StringComparison.OrdinalIgnoreCase));
                var owner = context.FirstOrDefault(o => Describe.ShortId(o).StartsWith(source.ToLowerInvariant(), StringComparison.Ordinal))
                            ?? context.FirstOrDefault(o => string.Equals(o.NickName, source, StringComparison.OrdinalIgnoreCase));
                IGH_Param from = owner switch
                {
                    IGH_Param p => p,
                    IGH_Component c => c.Params.Output.FirstOrDefault(p => string.Equals(p.NickName, output, StringComparison.OrdinalIgnoreCase))
                                       ?? c.Params.Output.FirstOrDefault(p => string.Equals(p.Name, output, StringComparison.OrdinalIgnoreCase)),
                    _ => null,
                };
                if (target == null || from == null || target.Sources.Contains(from)) continue;
                target.AddSource(from);
                added++;
            }
            return added;
        }

        /// <summary>(status, problem for the model, note for people) — problem is null when the node works.</summary>
        private static (string, string, string) Judge(AlienComponent node, JObject report)
        {
            var status = report.Value<string>("status");
            if (status == "error")
            {
                var line = report.Value<int?>("line");
                return ("error", Prompts.Failure(report), report.Value<string>("error") + (line.HasValue ? $" (line {line})" : ""));
            }
            if (status != "ok")
                return ("stuck", $"The node did not finish solving (status: {status}). {report.Value<string>("note")}".Trim(), $"did not finish ({status})");

            if (UiThread.Run(() => node.Header.Outputs.Count) == 0)
                return ("no-outputs", "The header declares no outputs, so the node shows nothing. Add a NODE_OUTPUTS line.", "no outputs declared");
            if (report["unset_outputs"] is JArray unset && unset.Count > 0)
                return ("unset", $"These outputs were never assigned: {string.Join(", ", unset)}. Assign every output on every path.",
                        "never assigned: " + string.Join(", ", unset));
            if (report["out"] is JObject outs && outs.Properties().All(p => p.Value.ToString() == "empty"))
                return ("empty", "The script ran but every output is empty. When an input is unwired or empty, fall back to sample " +
                                 "geometry or defaults so the node shows a result. Run report: " + Prompts.Failure(report), "every output was empty");
            return ("ok", null, null);
        }
    }
}
