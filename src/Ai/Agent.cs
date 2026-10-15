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
        /// <summary>The last building the agent placed or changed: role -> node id.</summary>
        public JObject Building { get; set; }

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
            var limits = new TurnLimits();
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
                    var (resultText, ok, image) = Run(s, name, args, request, limits);
                    if (image != null) images.Add(image);
                    var entry = new AgentEntry { Role = "tool", Tool = name, Text = Summary(name, args, resultText, ok), Ok = ok };
                    if (image != null) entry.Image = "data:image/png;base64," + image;
                    Add(s, entry);
                    if (ok && name == "building")
                    {
                        actionsDone.Add(BuildingTool.Brief(resultText));  // which node is which, for later turns
                        s.Building = BuildingTool.RoleIds(resultText) ?? s.Building;
                    }
                    else if (ok && name is "add" or "wire" or "delete" or "set_values" or "script_edit" or "generate" or "remember")
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

        /// <summary>What one turn has made so far, for the limits below.</summary>
        private sealed class TurnLimits
        {
            public int Generated;
            public int Buildings;
            public int Edits;
        }

        private const int MaxGeneratesPerTurn = 2;
        private const int MaxCodeLines = 150;

        /// <summary>Run one tool: an MCP tool through the server's dispatcher, or one of the agent's own.</summary>
        private static (string text, bool ok, string image) Run(AgentSession s, string name, JObject args, string request, TurnLimits limits)
        {
            try
            {
                switch (name)
                {
                    case "building":
                        bool edit = !string.IsNullOrWhiteSpace(args.Value<string>("edit"));
                        if (!edit && limits.Buildings >= 1) return ("Not placed: one new building per request. Tell the user what you placed; they can ask for another.", false, null);
                        if (edit && limits.Edits >= 3) return ("Not changed: three rebuilds this request already. Tell the user where it stands.", false, null);
                        if (edit) limits.Edits++; else limits.Buildings++;
                        // Some models send every field, false included: no stairs only when the user said so.
                        if (args["stairs"]?.Type == JTokenType.Boolean && !(bool)args["stairs"] && !Regex.IsMatch(request ?? "", @"\bstair", RegexOptions.IgnoreCase))
                            args.Remove("stairs");
                        if (edit) args = EditFields(args, request);
                        var (built, look) = BuildingTool.Run(args);
                        return (built, true, look);
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
                if (name == "generate")
                {
                    var refusal = GenerateGuard(args.Value<string>("prompt"), request, limits);
                    if (refusal != null) return (refusal, false, null);
                    limits.Generated++;
                    AddBuildingContext(s, args);
                }
                if (name == "add" && TooMuchCode(args) is string big) return (big, false, null);
                if (name == "add" && limits.Buildings == 0 && LooseBuilding(args) is string loose) return (loose, false, null);
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

        // ── guardrails: one node does one job ─────────────────────

        /// <summary>Building elements the suite already makes, by the words a prompt uses for them.</summary>
        private static readonly (string kind, string pattern)[] SuiteElements =
        {
            ("walls", @"\bwalls?\b"), ("slabs or floors", @"\b(slabs?|floor ?plates?|floors)\b"), ("roofs", @"\broof(s|ing)?\b"),
            ("stairs", @"\bstair(s|case|cases|way)?\b"), ("doors", @"\bdoors?\b"), ("windows", @"\bwindows?\b"),
            ("a structural frame", @"\b(columns?|beams?|structural frame|framing|frame structure|portal frames?)\b"),
            ("grids", @"\bgrids?\b"), ("levels", @"\b(levels|storeys?|stor(y|ies)|multi-?storey|\w+-storey)\b"),
            ("balustrades", @"\b(balustrades?|railings?|handrails?)\b"), ("curtain walls", @"\bcurtain ?walls?\b"),
        };

        private const string WholeBuilding =
            @"\b(house|home|dwelling|villa|cabin|cottage|bungalow|duplex|townhouses?|apartments?|flats|tower|skyscraper|" +
            @"office building|(whole|entire|complete|full) building|building model|fit-?out|all (the )?fittings)\b";

        /// <summary>
        /// Why a generate call shouldn't run, or null. A generated node is one focused job; a prompt that models a whole
        /// building or several elements the suite already has makes one slow, shoddy node nobody can edit (it happened:
        /// "a two-storey house with all fittings" became a single node). Those go to the building tool and suite nodes.
        /// </summary>
        private static string GenerateGuard(string prompt, string request, TurnLimits limits)
        {
            prompt ??= "";
            if (limits.Generated >= MaxGeneratesPerTurn)
                return $"Not generated: {MaxGeneratesPerTurn} new nodes this request already. Stop here: tell the user what you made and what you'd add next; they can ask for more.";
            // The user asked for exactly this in so many words.
            if (Regex.IsMatch(request ?? "", @"\b(one|single|a single|in one|just one) node\b", RegexOptions.IgnoreCase)) return null;
            var kinds = SuiteElements.Where(e => Regex.IsMatch(prompt, e.pattern, RegexOptions.IgnoreCase)).Select(e => e.kind).ToList();
            // A building is what the node makes when it names one before the first "of / off / to / on ..." ("a two-storey
            // house in Rhino ..."), not when it only says where something goes ("a pergola off the back of the house").
            var subject = Regex.Split(prompt, @"\b(of|off|for|to|beside|behind|around|near|on|onto|against|from|along|at|over|under|in|inside|outside|next|attached|between)\b", RegexOptions.IgnoreCase)[0];
            bool whole = Regex.IsMatch(subject, WholeBuilding, RegexOptions.IgnoreCase) && !Regex.IsMatch(subject, @"\bmass(ing)?\b", RegexOptions.IgnoreCase);
            // A node that reads or reports on elements (a door schedule, area labels) is still one job.
            bool reads = Regex.IsMatch(prompt, @"\b(schedules?|tables?|take-?offs?|quantit(y|ies)|counts?|labels?|tags?|reports?|summar(y|ies|ise|ize))\b", RegexOptions.IgnoreCase);
            if ((kinds.Count >= 3 && !reads) || whole)
                return "Not generated: one node does one job, and this prompt asks for a building" +
                       (kinds.Count > 0 ? $" ({string.Join(", ", kinds)})" : "") + ". Alien's suite already has a node for each building element. " +
                       "Call building(...) to place them wired on one footprint (house, apartments, office ...), then adjust them with set_values. " +
                       "Generate only a piece the suite doesn't have (e.g. a pergola, joinery, a facade screen, a landscape element), " +
                       "as one focused node wired to the suite's outputs with context:[ids]; if that is what this is, describe just that " +
                       "piece without listing the building's elements. Furniture and fittings: object_3d nodes, one per item.";
            if (prompt.Length > 1200)
                return "Not generated: that prompt is too much for one node. Split it into focused nodes (each one job, wired together), " +
                       "and check the library first: most building parts already exist.";
            return null;
        }

        /// <summary>The words a request uses for each building setting: a change is kept only when the request talks
        /// about it, since some models fill in every field of an edit with guesses (a 12 x 9 m house came back 10 x 8).</summary>
        private static readonly (string pattern, string[] fields)[] EditTopics =
        {
            (@"\b(stor(e)?y|stories|storeys|floors?|levels?|tall|taller|shorter|high|higher|lower)\b", new[] { "storeys" }),
            (@"\b(height|ceiling|tall|taller|floor.to.floor)\b", new[] { "floor_height" }),
            (@"\b(wide|wider|width|long|longer|length|deep|deeper|depth|size|bigger|larger|smaller|footprint|bays?|grids?|metres|meters|m)\b|\d\s*(m\s*)?(x|by)\s*\d", new[] { "width", "depth", "x_bays", "y_bays" }),
            (@"\b(roofs?|gable|hip|hipped|skillion|flat|dutch)\b", new[] { "roof" }),
            (@"\b(pitch|pitched|degrees?|steep|steeper|shallow|shallower)\b|°", new[] { "pitch" }),
            (@"\b(walls?|brick|stud|concrete|precast|block(work)?|clt|aac|render(ed)?|cladding|veneer)\b", new[] { "wall" }),
            (@"\b(slabs?|floors?|concrete|timber|clt|joists?|post.tensioned)\b", new[] { "slab" }),
            (@"\b(roofing|tiles?|tiled|metal|deck|membrane|slate|green roof|terracotta|colorbond)\b", new[] { "roof_type" }),
            (@"\b(frame|framed|structure|structural|steel|timber|concrete|columns?|beams?)\b", new[] { "frame" }),
            (@"\bstair", new[] { "stairs" }),
            (@"\bdoors?\b", new[] { "doors" }),
            (@"\b(windows?|glazing|glazed|casement|awning|sliding|hopper|fixed|double.hung)\b", new[] { "windows", "window_family", "window_width", "window_height" }),
            (@"\b(name|call(ed)?|rename)\b", new[] { "name" }),
        };

        private static JObject EditFields(JObject args, string request)
        {
            var keep = new HashSet<string> { "edit" };
            foreach (var (pattern, fields) in EditTopics)
                if (Regex.IsMatch(request ?? "", pattern, RegexOptions.IgnoreCase)) keep.UnionWith(fields);
            return new JObject(args.Properties().Where(p => keep.Contains(p.Name)));
        }

        /// <summary>
        /// A new node that refers to the building ("a pergola off the back of the house") without context gets the
        /// building's site, levels and grids: describe-a-node then reads the real footprint and wires to it, instead of
        /// guessing where the walls are (a pergola once landed a metre inside the house).
        /// </summary>
        private static void AddBuildingContext(AgentSession s, JObject args)
        {
            if (s.Building == null || (args["context"] is JArray given && given.Count > 0) || args["id"] != null) return;
            if (!Regex.IsMatch(args.Value<string>("prompt") ?? "", @"\b(house|home|building|walls?|roof|back|front|rear|sides?|beside|next to|attached|footprint|grids?|levels?|storeys?)\b", RegexOptions.IgnoreCase))
                return;
            var ids = UiThread.Run(() =>
            {
                var doc = Instances.ActiveCanvas?.Document;
                if (doc == null) return new List<string>();
                return new[] { "site", "levels", "grids" }.Select(r => s.Building.Value<string>(r)).Where(id => id != null)
                    .Where(id => { try { Tools.Resolve(doc, id); return true; } catch { return false; } }).ToList();
            });
            if (ids.Count > 0) args["context"] = new JArray(ids);
        }

        /// <summary>A hand-written node (add with code) over the size limit: the same shoddy-node risk as a huge generate.</summary>
        private static string TooMuchCode(JObject args)
        {
            IEnumerable<JToken> specs = args["nodes"] is JArray batch ? batch : new JArray(args);
            foreach (var n in specs.OfType<JObject>())
            {
                var code = n.Value<string>("code");
                if (code != null && code.Split('\n').Length > MaxCodeLines)
                    return $"Not added: {code.Split('\n').Length} lines is too big for a hand-written node (limit {MaxCodeLines}). " +
                           "Use suite nodes (library, building) and keep new nodes to one focused job each.";
            }
            return null;
        }

        private static readonly string[] BuildingScripts =
            { "bim_site", "levels", "grids", "walls", "doors", "windows", "slabs", "roofs", "stairs", "balustrades", "beams_columns", "curtain_wall" };

        /// <summary>A whole building's worth of suite nodes placed by hand: left on their samples they never line up
        /// (each draws its own sample room), which is what the building tool is for.</summary>
        private static string LooseBuilding(JObject args)
        {
            if (args["nodes"] is not JArray batch) return null;
            int n = batch.OfType<JObject>().Select(o => Path.GetFileNameWithoutExtension(o.Value<string>("script") ?? ""))
                         .Count(sc => BuildingScripts.Contains(sc, StringComparer.OrdinalIgnoreCase));
            return n >= 5
                ? $"Not placed: {n} building nodes by hand won't line up. Call building(...) instead: it places and wires them on one footprint. Add extra nodes beside it afterwards."
                : null;
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
            var tip = SuiteElements.Count(e => Regex.IsMatch(query ?? "", e.pattern, RegexOptions.IgnoreCase)) >= 2 || Regex.IsMatch(query ?? "", WholeBuilding, RegexOptions.IgnoreCase)
                ? "Tip: for a building, building(...) places and wires these suite nodes on one footprint.\n" : "";
            if (scored.Count == 0) return tip + "No library node matches. Consider generate(...) to make one.";
            return tip + string.Join("\n", scored.Select(x => $"{x.n.Value<string>("path")} [{x.n.Value<string>("category")}] {x.n.Value<string>("description")}"));
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
            defs.Add(Function("building", BuildingTool.Description, BuildingTool.Schema()));
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
- Build with what exists. Alien's suite has a node for each building element and many other jobs; the user wants those
  nodes placed and wired together, never one big node that models everything (that makes one slow, shoddy node nobody
  can edit). Search the library (library tool) before making anything new, and suggest or place existing nodes.
- Buildings: for any building request (a house, apartments, an office, ""walls, slabs and a roof"", a frame with levels
  and grids ...), call building(...) FIRST. It places the suite nodes (site, levels, grids, types, walls on every storey,
  doors, windows, slabs, roof, frame, stairs, balustrades) wired and lined up on one footprint. Then adjust them with
  set_values (sizes on grids and levels, types, door and window positions, roof form) and look at the result. Don't
  wire new elements into an old, unrelated setup on the canvas; pass site/levels/grids ids to building only when the
  user points you at them.
- Changing a building you placed (storeys, size or bays, roof form or pitch, wall/slab/roof type, frame, windows,
  doors): call building(edit:<its group id>, ...only what changes). It rebuilds that building in place and keeps the
  parts in step (more storeys get windows and stairs too). Use set_values on its nodes only for other inputs.
- Not covered by building: interior walls (another walls node; set its path with points, below), furniture and
  fittings (object_3d, one node per item, e.g. ""kitchen bench""; its generate button spends credit, so place it with
  `thing` set and ask before pressing), site context (vic_site_map). Say plainly what is not modelled yet.
- Place one library node with add(component:""alien"", script:""suite/walls.py""); several in one add call
  (nodes:[{key, component:""alien"", script, x, y, values}], links:[""key.out>key.in""]). Suite nodes chain by matching pin
  names (site>site, levels>levels, grids>grids, wall_type>wall_type, walls>walls, members>members). Leave room: place new
  nodes to the right of what they use (add's near:<id>, or x/y about 450 apart).
- Curve inputs can be set as points with set_values, in model units: [[x,y,z],[x,y,z]] is a line, more points a
  polyline (repeat the first to close it); several curves: [[[...],[...]], [[...],[...]]].
- generate(prompt, context:[ids]) is only for a piece the suite doesn't have, one focused job per node (a pergola, a
  facade screen, a schedule from wall records), wired to the nodes it uses. It refuses whole buildings and prompts that
  list several building elements: use building and suite nodes for those. At most two new nodes per request; write
  code yourself (add with code, script_edit) only for small fixes.
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
            "building" => "placing a building",
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
                case "building":
                    try
                    {
                        var b = JObject.Parse(result);
                        var placed = (b["placed"] as JArray)?.Count ?? 0;
                        var bad = (b["placed"] as JArray)?.Count(n => n.Value<string>("status") != "ok") ?? 0;
                        var what = b["edited"] != null ? "changed the building: " + Clip(b.Value<string>("edited").Replace("rebuilt in place with ", "").Split(';')[0], 90)
                                                       : $"placed a building: {placed} nodes · {b.Value<int>("wires")} wires";
                        return what + (bad > 0 ? $" · {bad} need a look" : "");
                    }
                    catch { return "placed a building"; }
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
