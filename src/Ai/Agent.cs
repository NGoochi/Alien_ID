using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

using Grasshopper;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Rhino;

using Alien.Mcp;

namespace Alien.Ai
{
    /// <summary>One line of the agent chat as people see it: a request, a reply, a tool step, or an error.</summary>
    public sealed class AgentEntry
    {
        public string Role { get; set; }      // user | assistant | tool | error
        public string Text { get; set; }
        public string Tool { get; set; }
        public bool Ok { get; set; } = true;
        /// <summary>A viewport capture the agent looked at (data URI; shown, not saved).</summary>
        [JsonIgnore] public string Image { get; set; }
    }

    /// <summary>
    /// A project's agent conversation and memory, kept per Grasshopper file in &lt;script folder&gt;/.alien/agent/: what
    /// was said (for the panel), a compact history for the model, the notes the agent was asked to remember, and cost.
    /// </summary>
    public sealed class AgentSession
    {
        public string Key { get; set; }
        public string Title { get; set; }
        public List<string> Notes { get; set; } = new List<string>();
        public List<AgentEntry> Entries { get; set; } = new List<AgentEntry>();
        /// <summary>Earlier turns for the model: {user, reply, actions}.</summary>
        public List<JObject> Turns { get; set; } = new List<JObject>();
        public double Cost { get; set; }

        [JsonIgnore] public string File { get; set; }

        public void Save()
        {
            if (File == null) return;
            try
            {
                Directory.CreateDirectory(Path.GetDirectoryName(File));
                if (Entries.Count > 300) Entries.RemoveRange(0, Entries.Count - 300);
                if (Turns.Count > 30) Turns.RemoveRange(0, Turns.Count - 30);
                System.IO.File.WriteAllText(File, JsonConvert.SerializeObject(this, Formatting.Indented));
            }
            catch { /* memory is best-effort */ }
        }
    }

    /// <summary>
    /// The project agent: a chat in the Alien panel that sees the whole canvas and the project's memory, and works
    /// through the same tools agents use over MCP (canvas, add, wire, delete, set_values, generate, capture, …) plus
    /// library search, project notes and canvas selection. Runs one turn at a time on a worker thread; each tool call
    /// goes through the MCP server's own dispatcher, so the agent and outside agents see and change the same things,
    /// and every change can be undone in Grasshopper.
    /// </summary>
    public static class ProjectAgent
    {
        public const string DefaultModel = "anthropic/claude-sonnet-5.5";
        private const int MaxSteps = 24;
        private const int ResultChars = 6000;

        /// <summary>MCP tools the agent may use. Not run_code, rhino, doc or bake: nothing that runs arbitrary code,
        /// opens or saves files, or writes into the Rhino document.</summary>
        private static readonly string[] AllowedTools =
            { "canvas", "inspect", "add", "wire", "delete", "set_values", "script_read", "script_edit", "generate", "capture", "view", "objects", "layers", "search", "docs" };

        private static AgentSession _session;
        private static CancellationTokenSource _cts;

        /// <summary>Raised (any thread) when the chat changed: the panel re-renders it.</summary>
        public static event Action Changed;

        public static bool Busy => _cts != null;
        public static string Status { get; private set; }

        public static string Model
        {
            get
            {
                var m = ScriptRepository.Setting("agentModel");
                return string.IsNullOrWhiteSpace(m) ? DefaultModel : m;
            }
        }

        // ── the project's session ──────────────────────────────────

        /// <summary>The active Grasshopper file's session (UI thread), loaded from disk the first time.</summary>
        public static AgentSession Session
        {
            get
            {
                var (key, title) = ProjectKey();
                if (_session != null && _session.Key == key) return _session;
                if (_session != null && Busy) return _session; // don't switch mid-turn
                _session = Load(key, title);
                return _session;
            }
        }

        private static (string key, string title) ProjectKey()
        {
            var doc = Instances.ActiveCanvas?.Document;
            if (doc == null) return ("no-document", "No Grasshopper file");
            if (!string.IsNullOrEmpty(doc.FilePath)) return (doc.FilePath, Path.GetFileNameWithoutExtension(doc.FilePath));
            return ("doc-" + doc.DocumentID, "Unsaved file");
        }

        private static string FileFor(string key)
        {
            var root = ScriptRepository.Root;
            if (root == null) return null;
            using var sha = SHA1.Create();
            var hash = BitConverter.ToString(sha.ComputeHash(Encoding.UTF8.GetBytes(key))).Replace("-", "").Substring(0, 12).ToLowerInvariant();
            return Path.Combine(root, ".alien", "agent", hash + ".json");
        }

        private static AgentSession Load(string key, string title)
        {
            var file = FileFor(key);
            AgentSession s = null;
            try { if (file != null && File.Exists(file)) s = JsonConvert.DeserializeObject<AgentSession>(File.ReadAllText(file)); }
            catch { }
            // A file saved since the chat began: carry the unsaved file's chat over.
            var doc = Instances.ActiveCanvas?.Document;
            if (s == null && doc != null && !string.IsNullOrEmpty(doc.FilePath))
            {
                var old = FileFor("doc-" + doc.DocumentID);
                try
                {
                    if (old != null && File.Exists(old))
                    {
                        s = JsonConvert.DeserializeObject<AgentSession>(File.ReadAllText(old));
                        File.Delete(old);
                    }
                }
                catch { }
            }
            s ??= new AgentSession();
            s.Key = key;
            s.Title = title;
            s.File = file;
            return s;
        }

        public static void NewChat()
        {
            if (Busy) return;
            var s = Session;
            s.Entries.Clear();
            s.Turns.Clear();
            s.Save();
            Changed?.Invoke();
        }

        public static void Forget(int index)
        {
            var s = Session;
            if (index < 0 || index >= s.Notes.Count) return;
            s.Notes.RemoveAt(index);
            s.Save();
            Changed?.Invoke();
        }

        public static void Stop() => _cts?.Cancel();

        /// <summary>The chat for the panel (UI thread).</summary>
        public static JObject StateJson()
        {
            var s = Session;
            return new JObject
            {
                ["title"] = s.Title,
                ["busy"] = Busy,
                ["status"] = Status,
                ["model"] = Model,
                ["cost"] = Math.Round(s.Cost, 4),
                ["notes"] = new JArray(s.Notes),
                ["entries"] = Entries(s),
            };
        }

        /// <summary>The last 120 lines; captures only on the last four that have one (they are large).</summary>
        private static JArray Entries(AgentSession s)
        {
            List<AgentEntry> list;
            lock (s) list = s.Entries.Skip(Math.Max(0, s.Entries.Count - 120)).ToList();
            var withImages = new HashSet<AgentEntry>(list.Where(e => e.Image != null).Reverse().Take(4));
            return new JArray(list.Select(e =>
            {
                var o = new JObject { ["role"] = e.Role, ["text"] = e.Text };
                if (e.Tool != null) o["tool"] = e.Tool;
                if (!e.Ok) o["ok"] = false;
                if (withImages.Contains(e)) o["image"] = e.Image;
                return o;
            }));
        }

        /// <summary>Cheap: changes whenever the chat should be re-sent to the panel.</summary>
        public static string Signature()
        {
            var s = Session;
            return $"{s.Key}|{s.Entries.Count}|{Busy}|{Status}|{s.Notes.Count}|{Model}";
        }

        // ── a turn ─────────────────────────────────────────────────

        /// <summary>Start a turn (UI thread): the request goes to the model with the canvas and memory; tools run until it replies.</summary>
        public static void Send(string text)
        {
            text = (text ?? "").Trim();
            if (text.Length == 0 || Busy) return;
            var s = Session;
            s.Entries.Add(new AgentEntry { Role = "user", Text = text });
            var cts = _cts = new CancellationTokenSource();
            Status = "thinking…";
            Changed?.Invoke();
            Task.Run(async () =>
            {
                try { await Turn(s, text, cts.Token).ConfigureAwait(false); }
                catch (OperationCanceledException) { Add(s, new AgentEntry { Role = "error", Text = "Stopped." }); }
                catch (Exception ex) { Add(s, new AgentEntry { Role = "error", Text = ex.Message }); }
                finally
                {
                    _cts = null;
                    Status = null;
                    s.Save();
                    Changed?.Invoke();
                }
            });
        }

        private static void Add(AgentSession s, AgentEntry e)
        {
            lock (s) s.Entries.Add(e);
            Changed?.Invoke();
        }

        private static async Task Turn(AgentSession s, string request, CancellationToken ct)
        {
            var choice = ModelChoice.Resolve(Model);
            var messages = new JArray { new JObject { ["role"] = "system", ["content"] = SystemPrompt() } };
            foreach (var t in s.Turns.Skip(Math.Max(0, s.Turns.Count - 8)))
            {
                messages.Add(new JObject { ["role"] = "user", ["content"] = t.Value<string>("user") });
                var reply = t.Value<string>("reply") ?? "";
                var actions = t.Value<string>("actions");
                if (!string.IsNullOrEmpty(actions)) reply += "\n[what I did: " + actions + "]";
                messages.Add(new JObject { ["role"] = "assistant", ["content"] = reply });
            }
            messages.Add(new JObject { ["role"] = "user", ["content"] = Context(s) + "\n\nRequest: " + request });

            var tools = ToolDefinitions();
            var actionsDone = new List<string>();
            for (int step = 1; step <= MaxSteps; step++)
            {
                ct.ThrowIfCancellationRequested();
                Status = step == 1 ? "thinking…" : $"step {step} · thinking…";
                Changed?.Invoke();
                var (message, usage) = await ModelClient.CompleteWithTools(choice, messages, tools, ct).ConfigureAwait(false);
                s.Cost += usage.Cost;
                var calls = message["tool_calls"] as JArray;
                var text = (message.Value<string>("content") ?? "").Trim();
                if (calls == null || calls.Count == 0)
                {
                    if (text.Length == 0) text = "Done.";
                    Add(s, new AgentEntry { Role = "assistant", Text = text });
                    s.Turns.Add(new JObject { ["user"] = request, ["reply"] = text, ["actions"] = string.Join("; ", actionsDone.Take(20)) });
                    return;
                }

                // The assistant's message goes back as it came (content may be null with tool calls).
                var echo = new JObject { ["role"] = "assistant", ["content"] = text.Length > 0 ? text : null, ["tool_calls"] = calls };
                messages.Add(echo);
                if (text.Length > 0) Add(s, new AgentEntry { Role = "assistant", Text = text });

                var images = new List<string>();
                foreach (var call in calls)
                {
                    ct.ThrowIfCancellationRequested();
                    var id = call.Value<string>("id");
                    var name = call.SelectToken("function.name")?.ToString();
                    JObject args;
                    try { args = JObject.Parse(call.SelectToken("function.arguments")?.ToString() ?? "{}"); }
                    catch { args = new JObject(); }
                    Status = $"{Verb(name)}…";
                    Changed?.Invoke();
                    var (resultText, ok, image) = Run(s, name, args, request);
                    if (image != null) images.Add(image);
                    var entry = new AgentEntry { Role = "tool", Tool = name, Text = Summary(name, args, resultText, ok), Ok = ok };
                    if (image != null) entry.Image = "data:image/png;base64," + image;
                    Add(s, entry);
                    if (ok && name is "add" or "wire" or "delete" or "set_values" or "script_edit" or "generate" or "remember")
                        actionsDone.Add(entry.Text);
                    messages.Add(new JObject { ["role"] = "tool", ["tool_call_id"] = id, ["content"] = Clip(resultText, ResultChars) });
                }
                if (images.Count > 0)
                {
                    // Tool results carry text only: the captures follow as a user message the model can see.
                    var parts = new JArray { new JObject { ["type"] = "text", ["text"] = "The viewport capture(s) from the capture call:" } };
                    foreach (var img in images)
                        parts.Add(new JObject { ["type"] = "image_url", ["image_url"] = new JObject { ["url"] = "data:image/png;base64," + img } });
                    messages.Add(new JObject { ["role"] = "user", ["content"] = parts });
                }
            }
            Add(s, new AgentEntry { Role = "error", Text = $"Stopped after {MaxSteps} steps. Say \"continue\" to carry on." });
            s.Turns.Add(new JObject { ["user"] = request, ["reply"] = "(stopped after many steps)", ["actions"] = string.Join("; ", actionsDone.Take(20)) });
        }

        /// <summary>Run one tool: an MCP tool through the server's dispatcher, or one of the agent's own.</summary>
        private static (string text, bool ok, string image) Run(AgentSession s, string name, JObject args, string request)
        {
            try
            {
                switch (name)
                {
                    case "library": return (Library(args.Value<string>("query")), true, null);
                    case "remember":
                        var note = (args.Value<string>("note") ?? "").Trim();
                        if (note.Length == 0) return ("Nothing to remember.", false, null);
                        lock (s) s.Notes.Add(note.Length > 400 ? note.Substring(0, 400) : note);
                        s.Save();
                        return ("Remembered.", true, null);
                    case "select": return (Select(args["ids"] as JArray), true, null);
                }
                if (!AllowedTools.Contains(name)) return ($"Unknown tool {name}.", false, null);
                if (name == "set_values" && args["values"] is JObject vals && !SpendAllowed(request)
                    && vals.Properties().Any(p => p.Value.Type == JTokenType.Boolean && (bool)p.Value && p.Name.StartsWith("generate", StringComparison.OrdinalIgnoreCase)))
                    return ("Not pressed: Generate buttons spend the user's credit. Ask the user first (they can say \"go ahead\").", false, null);
                var result = McpServer.Instance.Invoke(name, args);
                bool ok = !result.Value<bool>("isError");
                var text = string.Join("\n", (result["content"] as JArray ?? new JArray()).Where(c => c.Value<string>("type") == "text").Select(c => c.Value<string>("text")));
                var image = (result["content"] as JArray)?.FirstOrDefault(c => c.Value<string>("type") == "image")?.Value<string>("data");
                return (text, ok, image);
            }
            catch (Exception ex) { return (ex.Message, false, null); }
        }

        /// <summary>The user's own words allow pressing a paid Generate button ("generate it", "go ahead", "yes").</summary>
        private static bool SpendAllowed(string request) =>
            Regex.IsMatch(request ?? "", @"\b(generat\w*|press|go ahead|yes|do it|build it|make it|create it)\b", RegexOptions.IgnoreCase);

        // ── agent-only tools ───────────────────────────────────────

        private static string Library(string query)
        {
            var nodes = UiThread.Run(() => Alien.Library.Nodes());
            var words = Regex.Matches((query ?? "").ToLowerInvariant(), "[a-z0-9]+").Select(m => m.Value).Where(w => w.Length > 1).ToList();
            var scored = nodes.OfType<JObject>().Select(n =>
            {
                string name = n.Value<string>("name") ?? "", desc = n.Value<string>("description") ?? "", cat = n.Value<string>("category") ?? "";
                var hay = (name.Replace('_', ' ') + " " + desc + " " + cat).ToLowerInvariant();
                int score = words.Count == 0 ? 1 : words.Sum(w => (name.Contains(w) ? 3 : 0) + (hay.Contains(w) ? 1 : 0));
                if (n.Value<string>("group") == "suite") score += 1;
                return (n, score);
            }).Where(x => x.score > 0).OrderByDescending(x => x.score).Take(words.Count == 0 ? 40 : 12).ToList();
            if (scored.Count == 0) return "No library node matches. Consider generate(...) to make one.";
            return string.Join("\n", scored.Select(x => $"{x.n.Value<string>("path")} [{x.n.Value<string>("category")}] {x.n.Value<string>("description")}"));
        }

        private static string Select(JArray ids)
        {
            return UiThread.Run(() =>
            {
                var canvas = Instances.ActiveCanvas;
                var doc = canvas?.Document;
                if (doc == null) return "No Grasshopper document.";
                doc.DeselectAll();
                var picked = new List<Grasshopper.Kernel.IGH_DocumentObject>();
                foreach (var t in ids ?? new JArray())
                {
                    try { var o = Tools.Resolve(doc, t.ToString()); o.Attributes.Selected = true; picked.Add(o); }
                    catch { }
                }
                if (picked.Count > 0)
                {
                    var b = picked.Select(o => o.Attributes.Bounds).Aggregate(System.Drawing.RectangleF.Union);
                    canvas.Viewport.MidPoint = new System.Drawing.PointF(b.X + b.Width / 2, b.Y + b.Height / 2);
                }
                canvas.Refresh();
                return $"Selected {picked.Count} node(s) on the canvas.";
            });
        }

        // ── what the model is told ─────────────────────────────────

        private static JArray _toolDefs;

        private static JArray ToolDefinitions()
        {
            if (_toolDefs != null) return _toolDefs;
            var defs = new JArray();
            foreach (var name in AllowedTools)
            {
                var t = McpServer.Instance.Find(name);
                if (t == null) continue;
                defs.Add(Function(t.Name, t.Description, t.Schema));
            }
            defs.Add(Function("library", "Search Alien's node library (suite nodes and the user's own scripts) by words; empty query lists them all. Returns path [category] description; place one with add(component:'alien', script:path).",
                Schema(("query", "Words, e.g. 'walls doors' or 'site map'", false))));
            defs.Add(Function("remember", "Keep a lasting note about this project (site, brief, decisions, what a group of nodes is for). Notes are shown to you at the start of every request.",
                Schema(("note", "One short fact", true))));
            defs.Add(Function("select", "Select nodes on the Grasshopper canvas (and centre the view on them) to show the user what you mean.",
                new JObject { ["type"] = "object", ["properties"] = new JObject { ["ids"] = new JObject { ["type"] = "array", ["items"] = new JObject { ["type"] = "string" } } }, ["required"] = new JArray("ids") }));
            return _toolDefs = defs;
        }

        private static JObject Function(string name, string description, JObject schema) => new JObject
        {
            ["type"] = "function",
            ["function"] = new JObject { ["name"] = name, ["description"] = description, ["parameters"] = schema },
        };

        private static JObject Schema(params (string name, string description, bool required)[] props)
        {
            var p = new JObject();
            foreach (var (n, d, _) in props) p[n] = new JObject { ["type"] = "string", ["description"] = d };
            return new JObject { ["type"] = "object", ["properties"] = p, ["required"] = new JArray(props.Where(x => x.required).Select(x => x.name)) };
        }

        private static string _system;

        private static string SystemPrompt()
        {
            if (_system != null) return _system;
            var guide = Tools.LoadGuide();
            var sb = new StringBuilder();
            sb.Append(@"You are the Alien project agent, working inside Rhino 8 and Grasshopper with an architect on their project.
You can see the whole Grasshopper canvas (summarised in each request), read and change Alien nodes, place nodes from the Alien
library, wire them, test them, delete them, and look at the Rhino viewport.

How to work
- Start from what exists: read the canvas summary; search the library (library tool) before making anything new, and suggest or
  place existing nodes when they fit. When the user isn't sure what to build, look at the canvas and the project memory and
  suggest two or three concrete next steps with the nodes that do them.
- Place a library node with add(component:""alien"", script:""suite/walls.py""); place several and wire them in one add call
  (nodes:[{key, component:""alien"", script, x, y}], links:[""key.out>key.in""]). Suite nodes chain by matching pin names
  (site>site, levels>levels, grids>grids, wall_type>wall_type, walls>walls, members>members). Leave room: place new nodes to
  the right of what they use (add's near:<id>, or x/y about 450 apart).
- When nothing in the library fits, use generate(prompt, context:[ids of the nodes it should use]): describe-a-node writes,
  runs and fixes a new node and wires it. Write code yourself (add with code, script_edit) only for small fixes.
- Check your work: tool replies include run status and errors; inspect for detail; capture(view:""iso"" or ""top"",
  focus:[ids], width:640, height:400) when you need to see geometry. Fix what breaks before you report.
- Never press Generate or other buttons that spend the user's credit, and never delete nodes the user made, unless the user
  asked for it. Nodes you made in this conversation you may tidy up. Every change can be undone with Ctrl/Cmd+Z.
- Keep lasting facts with remember(note): the site, the brief, decisions, what each group of nodes is for.
- Reply briefly, in plain language: what you did, what you see, and one or two next steps (name the nodes). Ask a short
  question when the request is unclear. Don't paste JSON or code unless asked.

");
            foreach (var title in new[] { "Workflow", "Header", "Building records" })
            {
                var section = Section(guide, title);
                if (section != null) sb.Append(section).Append("\n\n");
            }
            sb.Append("## Suite nodes (Alien's own library; the user's scripts are found with the library tool)\n");
            foreach (var n in UiThread.Run(() => Alien.Library.Nodes()).OfType<JObject>().Where(n => n.Value<string>("group") == "suite").OrderBy(n => n.Value<string>("category")))
                sb.Append($"- {n.Value<string>("path")} [{n.Value<string>("category")}]: {n.Value<string>("description")}\n");
            return _system = sb.ToString();
        }

        private static string Section(string guide, string title)
        {
            foreach (var s in guide.Split(new[] { "\n## " }, StringSplitOptions.None).Skip(1))
                if (s.Split('\n')[0].Trim().Equals(title, StringComparison.OrdinalIgnoreCase))
                    return "## " + s.Trim();
            return null;
        }

        /// <summary>What changes from request to request: the project, its memory, the canvas now, the selection, the units.</summary>
        private static string Context(AgentSession s)
        {
            var sb = new StringBuilder();
            sb.Append($"Project: {s.Title}.");
            var units = UiThread.Run(() => RhinoDoc.ActiveDoc?.ModelUnitSystem.ToString());
            if (units != null) sb.Append($" Rhino document units: {units}.");
            sb.Append('\n');
            sb.Append(s.Notes.Count == 0 ? "Project memory: (empty)\n"
                : "Project memory:\n" + string.Join("\n", s.Notes.Select((n, i) => $"{i + 1}. {n}")) + "\n");
            try
            {
                var canvas = McpServer.Instance.Invoke("canvas", new JObject());
                var text = string.Join("\n", (canvas["content"] as JArray ?? new JArray()).Select(c => c.Value<string>("text")));
                sb.Append("Canvas now (sel = selected by the user): ").Append(Clip(text, 9000)).Append('\n');
            }
            catch (Exception ex) { sb.Append("Canvas: unavailable (" + ex.Message + ")\n"); }
            return sb.ToString();
        }

        // ── for people ──────────────────────────────────────────────

        private static string Verb(string tool) => tool switch
        {
            "canvas" => "reading the canvas", "inspect" => "inspecting", "add" => "placing nodes", "wire" => "wiring",
            "delete" => "deleting", "set_values" => "setting inputs", "script_read" => "reading a script",
            "script_edit" => "editing a script", "generate" => "writing a node", "capture" => "looking at the viewport",
            "view" => "changing the view", "library" => "searching the library", "remember" => "remembering",
            "select" => "selecting", "search" => "searching components", "objects" => "reading Rhino objects",
            "layers" => "reading layers", "docs" => "reading the docs", _ => tool,
        };

        /// <summary>A one-line chip for the chat: what the tool did.</summary>
        private static string Summary(string tool, JObject args, string result, bool ok)
        {
            if (!ok) return Verb(tool) + ": " + Clip(result, 160);
            switch (tool)
            {
                case "add":
                    var nodes = args["nodes"] as JArray;
                    var names = nodes != null ? nodes.Select(n => Short(n.Value<string>("script") ?? n.Value<string>("component"))) : new[] { Short(args.Value<string>("script") ?? args.Value<string>("component")) };
                    var links = (args["links"] as JArray)?.Count ?? 0;
                    return "placed " + string.Join(", ", names) + (links > 0 ? $" · {links} wire{(links == 1 ? "" : "s")}" : "");
                case "wire": return $"{(args.Value<bool?>("disconnect") == true ? "unwired" : "wired")} {(args["links"] as JArray)?.Count ?? 0} link(s)";
                case "delete": return $"deleted {(args["ids"] as JArray)?.Count ?? 0} node(s)";
                case "set_values": return "set " + string.Join(", ", (args["values"] as JObject)?.Properties().Select(p => p.Name) ?? Array.Empty<string>());
                case "generate": return "wrote a node: " + Clip(args.Value<string>("prompt"), 80);
                case "capture": return "looked at the viewport";
                case "script_edit": return "edited a script";
                case "library": return "searched the library" + (string.IsNullOrEmpty(args.Value<string>("query")) ? "" : $": {args.Value<string>("query")}");
                case "remember": return "remembered: " + Clip(args.Value<string>("note"), 80);
                default: return Verb(tool);
            }
        }

        private static string Short(string s) => string.IsNullOrEmpty(s) ? "node" : Path.GetFileNameWithoutExtension(s);

        private static string Clip(string s, int max) => s == null ? "" : s.Length > max ? s.Substring(0, max) + "…" : s;
    }
}
