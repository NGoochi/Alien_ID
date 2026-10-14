using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Runtime.InteropServices;

using Eto.Forms;

using Grasshopper;
using Grasshopper.Kernel;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Rhino;

using Alien.Ai;
using Alien.Mcp;
using Alien.Scripting;

namespace Alien.Ui
{
    /// <summary>
    /// Alien's docked Rhino panel: an HTML page (embedded pane.html) in a WebView. It follows the canvas selection and
    /// shows the selected node's requests, inputs, code and earlier versions, plus a library to place nodes from.
    /// The page talks to C# with <c>window.eto.postMessage(json)</c>; C# answers by calling <c>window.alien.state(json)</c>.
    /// Registered under Grasshopper's own Rhino plug-in, so Alien needs no .rhp of its own.
    /// </summary>
    [Guid("6C1D6E0A-6F52-4E8B-9C0E-5B7A2D9F4A11")]
    public class AlienPanel : Panel
    {
        private static readonly Guid GrasshopperPlugIn = new Guid("b45a29b1-4343-4035-989e-044e8580d9cf");
        private static readonly List<AlienPanel> Live = new List<AlienPanel>();
        private static bool _registered, _suiteChecked, _hooked;
        private static readonly string[] Models =
        {
            ModelChoice.DefaultModel, "openai/gpt-6-luna", "deepseek/deepseek-v4.1-flash",
            "google/gemini-3.8-flash", "anthropic/claude-sonnet-5.5", "ollama:qwen3.5:9b",
        };

        public static Guid Id => typeof(AlienPanel).GUID;

        private readonly WebView _web;
        private readonly UITimer _timer;
        private bool _ready;
        private string _sent;
        private JArray _library = new JArray();
        private DateTime _libraryAt = DateTime.MinValue;
        private string _assetsSent;

        /// <summary>True when hosted by the sidecar beside Grasshopper; false when docked in Rhino.</summary>
        private readonly bool _sidecar;

        public AlienPanel() : this(false) { }

        internal AlienPanel(bool sidecar)
        {
            _sidecar = sidecar;
            MinimumSize = new Eto.Drawing.Size(300, 460);
            _web = new WebView();
            _web.MessageReceived += (s, e) => OnMessage(e.Message);
            // The page is ours alone: it never navigates away or opens windows (shown text could hold links).
            _web.DocumentLoading += (s, e) => { if (e.Uri != null && e.Uri.Scheme != "about" && e.Uri.Scheme != "data") e.Cancel = true; };
            _web.OpenNewWindow += (s, e) => e.Cancel = true;
            _web.LoadHtml(Page());
            Content = _web;
            _timer = new UITimer { Interval = 0.5 };
            _timer.Elapsed += (s, e) => Push(false);
            _timer.Start();
            Live.Add(this);
            if (!_hooked)
            {
                _hooked = true; // the model sorted more nodes into categories: refresh the listing
                Library.Classified += () => Application.Instance.AsyncInvoke(() => { foreach (var p in Live.ToList()) p.Push(true); });
                Theme.Changed += () => { Instances.RedrawCanvas(); foreach (var p in Live.ToList()) p.Push(true); };
                ProjectAgent.Changed += () => Application.Instance.AsyncInvoke(() => { foreach (var p in Live.ToList()) p.PushAgent(true); });
            }
        }

        protected override void OnUnLoad(EventArgs e)
        {
            _timer.Stop();
            Live.Remove(this);
            base.OnUnLoad(e);
        }

        protected override void OnLoad(EventArgs e)
        {
            base.OnLoad(e);
            if (!Live.Contains(this)) Live.Add(this);
            _timer.Start();
        }

        /// <summary>A node's state changed (describe finished, inputs changed): open panels refresh now.</summary>
        public static void NodeChanged(AlienComponent node)
        {
            foreach (var p in Live.ToList()) p.Push(true);
        }

        // ── Registration ─────────────────────────────────────────────

        /// <summary>Register once (UI thread, when Grasshopper loads).</summary>
        public static void Register()
        {
            if (_registered) return;
            try
            {
                var gh = Rhino.PlugIns.PlugIn.Find(GrasshopperPlugIn);
                if (gh == null) return;
                Rhino.UI.Panels.RegisterPanel(gh, typeof(AlienPanel), "Alien", (System.Drawing.Icon)null);
                _registered = true;
            }
            catch (Exception ex) { RhinoApp.WriteLine($"Alien: could not register the Alien panel ({ex.Message})"); }
        }

        /// <summary>Where the panel lives: "grasshopper" (glued beside the Grasshopper window, default) or "rhino" (a Rhino panel).</summary>
        private static bool DockedInRhino => ScriptRepository.Setting("panel") == "rhino";

        public static void Open()
        {
            if (!DockedInRhino && Instances.DocumentEditor != null)
            {
                AlienSidecar.Show();
                foreach (var p in Live.ToList()) p.Push(true);
                return;
            }
            OpenInRhino();
        }

        private static void Close()
        {
            if (AlienSidecar.IsOpen) AlienSidecar.Hide();
            if (_registered && Rhino.UI.Panels.IsPanelVisible(Id)) Rhino.UI.Panels.ClosePanel(Id);
        }

        /// <summary>Move the panel between the Grasshopper sidecar and Rhino's own panels, and remember the choice.</summary>
        private static void Dock(bool inRhino)
        {
            ScriptRepository.SetSetting("panel", inRhino ? "rhino" : null);
            Close();
            Open();
        }

        private static void OpenInRhino()
        {
            Register();
            if (!_registered) return;
            Rhino.UI.Panels.OpenPanel(Id, true);
            foreach (var p in Live.ToList()) p.Push(true);
            // Rhino opens a never-docked panel as a 200 x 200 floating window: give it a usable size, beside Grasshopper.
            Application.Instance.AsyncInvoke(() =>
            {
                foreach (var p in Live.ToList())
                {
                    var w = p.ParentWindow;
                    if (w == null || w.Size.Width >= 280) continue;
                    w.Size = new Eto.Drawing.Size(340, 640);
                    var editor = Instances.DocumentEditor;
                    if (editor == null) continue;
                    var screen = Eto.Forms.Screen.PrimaryScreen.WorkingArea;
                    var e = editor.Bounds;
                    float x = e.Right + 8 + 340 <= screen.Right ? e.Right + 8 : Math.Max(screen.Left, e.Left - 348);
                    w.Location = new Eto.Drawing.Point((int)x, Math.Max((int)screen.Top, e.Top + 40));
                }
            });
        }

        private static string Page()
        {
            using var s = Assembly.GetExecutingAssembly().GetManifestResourceStream("Alien.pane.html");
            if (s == null) return "<html><body>pane.html missing from the build</body></html>";
            using var r = new StreamReader(s);
            return r.ReadToEnd();
        }

        // ── State → page ─────────────────────────────────────────────

        private static AlienComponent Selected() =>
            Instances.ActiveCanvas?.Document?.SelectedObjects().OfType<AlienComponent>().FirstOrDefault();

        /// <summary>The selected nodes: components and parameters (not groups or scribbles).</summary>
        private static List<IGH_DocumentObject> Selection() =>
            Instances.ActiveCanvas?.Document?.SelectedObjects().Where(o => o is IGH_Component || o is IGH_Param).ToList()
            ?? new List<IGH_DocumentObject>();

        private static JArray SelectionJson(List<IGH_DocumentObject> picked) => new JArray(picked.Select(o => new JObject
        {
            ["id"] = Describe.ShortId(o),
            ["kind"] = o is AlienComponent ? "alien" : o is IGH_Param ? "param" : "component",
            ["name"] = o is AlienComponent a ? (a.ScriptPath == null ? "blank Alien node" : Path.GetFileNameWithoutExtension(a.ScriptPath))
                       : string.IsNullOrEmpty(o.NickName) ? o.Name : o.NickName,
        }));

        private void Push(bool force)
        {
            if (!_ready) return;
            try
            {
                if (!_suiteChecked && ScriptRepository.IsConfigured)
                {
                    _suiteChecked = true;
                    Suite.InstallIfMissing();
                }
                bool relist = force || DateTime.UtcNow - _libraryAt > TimeSpan.FromSeconds(3);
                if (relist)
                {
                    _libraryAt = DateTime.UtcNow;
                    _library = Library.Nodes();
                    PushAssets();
                }

                var node = Selected();
                var state = new JObject
                {
                    ["node"] = node == null ? null : NodeJson(node),
                    ["selection"] = SelectionJson(Selection()),
                    ["library"] = _library,
                    ["categories"] = Library.CategoryList(),
                    ["essentials"] = Library.EssentialsList(),
                    ["theme"] = Theme.Current.ToJson(),
                    ["presets"] = Theme.PresetsJson(),
                    ["host"] = _sidecar ? "grasshopper" : "rhino",
                    ["settings"] = new JObject
                    {
                        ["model"] = ScriptRepository.Setting("model") ?? ModelChoice.DefaultModel,
                        ["models"] = new JArray(Models),
                        ["hasKey"] = !string.IsNullOrWhiteSpace(ScriptRepository.Setting("openrouterKey")),
                        ["keyHint"] = OpenRouterAccount.Hint(ScriptRepository.Setting("openrouterKey")),
                        ["settingsFile"] = ScriptRepository.SettingsFile,
                    },
                };
                PushAgent(force);
                var json = state.ToString(Formatting.None);
                if (!force && json == _sent) return;
                _sent = json;
                _web.ExecuteScriptAsync("window.alien && window.alien.state(" + json + ")");
            }
            catch (Exception ex) { RhinoApp.WriteLine("Alien panel: " + ex.Message); }
        }

        private static JObject NodeJson(AlienComponent n)
        {
            var (status, level) = AlienAttributes.StatusOf(n);
            string source = null;
            try { if (n.ScriptPath != null && File.Exists(n.ScriptPath)) source = File.ReadAllText(n.ScriptPath); } catch { }
            return new JObject
            {
                ["id"] = Describe.ShortId(n),
                ["name"] = n.ScriptPath == null ? "New node" : Path.GetFileNameWithoutExtension(n.ScriptPath),
                ["lang"] = n.Language == ScriptLanguage.None ? null : ScriptLanguages.Tag(n.Language),
                ["script"] = n.ScriptPath == null ? null : ScriptRepository.ToStored(n.ScriptPath).Replace("repo:", ""),
                ["status"] = status,
                ["level"] = level,
                ["describing"] = n.IsDescribing,
                ["activity"] = n.Activity,
                ["description"] = ScriptInfo.Description(source),
                ["error"] = AlienAttributes.ProblemOf(n),
                ["transcript"] = new JArray(n.Transcript.Select(t => new JObject
                {
                    ["request"] = t.Request, ["state"] = t.State, ["summary"] = t.Summary, ["notes"] = new JArray(t.Notes),
                })),
                ["inputs"] = new JArray(n.Header.Inputs.Select(d =>
                {
                    var p = n.Params.Input.FirstOrDefault(x => x.Name == d.Name);
                    n.ManualValues.TryGetValue(d.Name, out var raw);
                    var value = p != null && p.SourceCount > 0 ? "wired" : d.Button ? "button"
                        : Picks.IsPickable(d) ? (Picks.Label(d, raw) ?? "not picked") : NodeControls.AsText(NodeControls.Current(n, d));
                    return new JObject { ["name"] = d.Name, ["value"] = value.Length > 0 ? value : "—" };
                })),
                ["code"] = source == null ? null : source.Length > 60000 ? source.Substring(0, 60000) + "\n…" : source,
                ["history"] = History(n),
            };
        }

        /// <summary>Earlier versions of the node's script, newest first (from .alien/history).</summary>
        private static JArray History(AlienComponent n)
        {
            var arr = new JArray();
            var root = ScriptRepository.Root;
            if (n.ScriptPath == null || root == null) return arr;
            var dir = Path.Combine(root, ".alien", "history");
            if (!Directory.Exists(dir)) return arr;
            var stem = Path.GetFileNameWithoutExtension(n.ScriptPath);
            var ext = Path.GetExtension(n.ScriptPath);
            foreach (var f in Directory.GetFiles(dir, stem + ".*" + ext).OrderByDescending(f => f, StringComparer.Ordinal).Take(20))
            {
                var name = Path.GetFileName(f);
                var stamp = name.Substring(stem.Length + 1, name.Length - stem.Length - 1 - ext.Length);
                var when = DateTime.TryParseExact(stamp, "yyyyMMdd-HHmmss-fff", CultureInfo.InvariantCulture, DateTimeStyles.None, out var t)
                    ? t.ToString("d MMM HH:mm:ss", CultureInfo.InvariantCulture) : stamp;
                arr.Add(new JObject { ["file"] = name, ["when"] = when });
            }
            return arr;
        }

        private string _agentSent;

        /// <summary>The agent chat goes in its own message (window.alien.agent), sent when it changed.</summary>
        private void PushAgent(bool force)
        {
            if (!_ready) return;
            try
            {
                var sig = ProjectAgent.Signature();
                if (!force && sig == _agentSent) return;
                _agentSent = sig;
                _web.ExecuteScriptAsync("window.alien && window.alien.agent && window.alien.agent(" + ProjectAgent.StateJson().ToString(Formatting.None) + ")");
            }
            catch (Exception ex) { RhinoApp.WriteLine("Alien agent: " + ex.Message); }
        }

        /// <summary>Generated assets go in their own message, sent only when one changes (thumbnails make it large).</summary>
        private void PushAssets()
        {
            var list = Assets.List(out var signature);
            if (signature == _assetsSent) return;
            _assetsSent = signature;
            _web.ExecuteScriptAsync("window.alien && window.alien.assets(" + list.ToString(Formatting.None) + ")");
        }

        private void Toast(string text)
        {
            if (!string.IsNullOrEmpty(text)) _web.ExecuteScriptAsync("window.alien && window.alien.toast(" + JsonConvert.ToString(text) + ")");
        }

        // ── Page → actions ───────────────────────────────────────────

        private void OnMessage(string raw)
        {
            JObject m;
            try { m = JObject.Parse(raw); }
            catch { return; }
            try
            {
                var node = Selected();
                switch (m.Value<string>("type"))
                {
                    case "ready":
                        _ready = true;
                        _assetsSent = null;
                        Push(true);
                        return;
                    case "describe":
                    {
                        var text = m.Value<string>("text");
                        var lang = ScriptLanguages.Parse(m.Value<string>("lang") ?? "python");
                        // Several nodes selected, or one that isn't an Alien node, or asked for: a new node beside them
                        // that sees what they output and is wired to them.
                        var picked = Selection();
                        bool fromSelection = picked.Count > 1 || (picked.Count == 1 && picked[0] is not AlienComponent)
                                             || (picked.Count > 0 && (m.Value<bool?>("fromSelection") ?? false));
                        if (fromSelection) Place(null, picked)?.StartDescribe(text, lang, picked.Select(o => o.InstanceGuid));
                        else
                        {
                            node ??= Place(null);
                            node?.StartDescribe(text, lang);
                        }
                        break;
                    }
                    case "agentSend":
                        ProjectAgent.Send(m.Value<string>("text"));
                        return;
                    case "agentStop":
                        ProjectAgent.Stop();
                        return;
                    case "agentNew":
                        ProjectAgent.NewChat();
                        return;
                    case "agentForget":
                        ProjectAgent.Forget(m.Value<int?>("index") ?? -1);
                        return;
                    case "agentModel":
                        ScriptRepository.SetSetting("agentModel", m.Value<string>("value"));
                        PushAgent(true);
                        return;
                    case "openUrl":
                        if (!AlienUi.OpenUrl(m.Value<string>("url"))) Toast("That link can't be opened from here.");
                        return;
                    case "setKey":
                        LinkKey(m.Value<string>("key"));
                        return;
                    case "testKey":
                        LinkKey(null);
                        return;
                    case "removeKey":
                        ScriptRepository.SetSetting("openrouterKey", "");
                        KeyStatus(new JObject { ["ok"] = false, ["reachable"] = true, ["message"] = "Key removed from this computer. (It still works on OpenRouter until you delete it there.)" });
                        break;
                    case "theme":
                        Theme.Set(m.Value<string>("preset"), m.Value<string>("background"), m.Value<string>("text"), m.Value<string>("accent"));
                        break;
                    case "new":
                        Place(null);
                        break;
                    case "place":
                        Place(m.Value<string>("path"));
                        break;
                    case "stop":
                        node?.StopDescribing();
                        break;
                    case "model":
                        ScriptRepository.SetSetting("model", m.Value<string>("value"));
                        break;
                    case "dock":
                        Dock(m.Value<string>("where") == "rhino");
                        return;
                    case "close":
                        Close();
                        return;
                    case "settings":
                        AlienUi.OpenSettings();
                        break;
                    case "openCode":
                        if (node?.ScriptPath != null) AlienUi.OpenInEditor(node.ScriptPath);
                        break;
                    case "reveal":
                        if (node?.ScriptPath != null) AlienUi.Reveal(node.ScriptPath);
                        break;
                    case "restore":
                        Restore(node, m.Value<string>("file"));
                        break;
                    case "bakeAsset":
                        if (RhinoDoc.ActiveDoc != null) Toast(Assets.Bake(RhinoDoc.ActiveDoc, m.Value<string>("kind"), m.Value<string>("id")));
                        break;
                    case "materialAsset":
                        if (RhinoDoc.ActiveDoc != null) Toast(Assets.AddMaterial(RhinoDoc.ActiveDoc, m.Value<string>("kind"), m.Value<string>("id")));
                        break;
                    case "placeAsset":
                        Toast(PlaceRecipe(m.Value<string>("kind"), m.Value<string>("id")));
                        break;
                    case "removeAsset":
                        Toast(Assets.Remove(m.Value<string>("kind"), m.Value<string>("id")));
                        PushAssets();
                        break;
                    case "copyRecord":
                    {
                        var dir = Assets.Folder(m.Value<string>("kind"), m.Value<string>("id"));
                        var rec = dir == null ? null : Path.Combine(dir, "record.json");
                        if (rec != null && File.Exists(rec)) { AlienUi.CopyText(File.ReadAllText(rec)); Toast("Record copied: paste it into a Panel wired to a type input."); }
                        break;
                    }
                    case "revealAsset":
                        var folder = Assets.Folder(m.Value<string>("kind"), m.Value<string>("id"));
                        if (folder != null) AlienUi.Reveal(Path.Combine(folder, "asset.json"));
                        break;
                }
                Push(true);
            }
            catch (Exception ex) { RhinoApp.WriteLine("Alien panel: " + ex.Message); }
        }

        /// <summary>
        /// Check a pasted key (or, with null, the saved one) with OpenRouter, off the UI thread, then save it if
        /// OpenRouter accepts it, or if OpenRouter can't be reached right now (saved, with a note). A key OpenRouter
        /// refuses is not saved.
        /// </summary>
        private void LinkKey(string pasted)
        {
            var key = pasted != null ? OpenRouterAccount.Clean(pasted) : ScriptRepository.Setting("openrouterKey");
            KeyStatus(new JObject { ["busy"] = true, ["message"] = "Checking with OpenRouter…" });
            System.Threading.Tasks.Task.Run(async () =>
            {
                JObject r;
                try { r = await OpenRouterAccount.Check(key).ConfigureAwait(false); }
                catch (Exception ex) { r = new JObject { ["ok"] = false, ["reachable"] = false, ["message"] = ex.Message }; }
                Application.Instance.AsyncInvoke(() =>
                {
                    bool save = pasted != null && OpenRouterAccount.LooksLikeKey(key) && (r.Value<bool>("ok") || !r.Value<bool>("reachable"));
                    if (save)
                    {
                        ScriptRepository.SetSetting("openrouterKey", key);
                        if (!r.Value<bool>("ok")) r["message"] = "Saved, but not checked: " + r.Value<string>("message");
                        r["saved"] = true;
                    }
                    KeyStatus(r);
                    Push(true);
                });
            });
        }

        private void KeyStatus(JObject status) =>
            _web.ExecuteScriptAsync("window.alien && window.alien.keyStatus && window.alien.keyStatus(" + status.ToString(Formatting.None) + ")");

        /// <summary>Place the suite node that made an asset, with the inputs that made it (cached, so it costs nothing).</summary>
        private static string PlaceRecipe(string kind, string id)
        {
            var (script, inputs) = Assets.Recipe(kind, id);
            if (script == null) return "This asset doesn't record how it was made.";
            if (!File.Exists(ScriptRepository.Resolve(script))) return $"{script} isn't in your script folder.";
            var n = Place(script);
            if (n == null) return null;
            n.PresetManualValues(inputs);
            return $"Placed {Path.GetFileNameWithoutExtension(script)} with the inputs that made it.";
        }

        /// <summary>
        /// A new Alien node (blank, or attached to a repository script), selected: in the middle of the canvas view, or
        /// to the right of the nodes it is built from.
        /// </summary>
        private static AlienComponent Place(string script, IReadOnlyList<IGH_DocumentObject> beside = null)
        {
            var canvas = Instances.ActiveCanvas;
            if (canvas == null)
            {
                (RhinoApp.GetPlugInObject("Grasshopper") as Grasshopper.Plugin.GH_RhinoScriptInterface)?.ShowEditor();
                canvas = Instances.ActiveCanvas;
                if (canvas == null) return null;
            }
            var doc = canvas.Document;
            if (doc == null)
            {
                doc = new GH_Document();
                Instances.DocumentServer.AddDocument(doc);
                canvas.Document = doc;
            }
            if (!ScriptRepository.IsConfigured && !AlienUi.PromptForRepository()) return null;

            var n = new AlienComponent { SuppressRepositoryPrompt = true };
            n.CreateAttributes();
            var at = canvas.Viewport.MidPoint;
            if (beside != null && beside.Count > 0)
            {
                var b = beside.Select(o => o.Attributes.Bounds).Aggregate(RectangleF.Union);
                at = new PointF(b.Right + 160, b.Y + b.Height / 2);
            }
            n.Attributes.Pivot = FreeSpot(doc, at);
            doc.AddObject(n, false);
            if (!string.IsNullOrWhiteSpace(script)) n.SetScript(ScriptRepository.Resolve(script));
            doc.DeselectAll();
            n.Attributes.Selected = true;
            canvas.Refresh();
            return n;
        }

        /// <summary>Nearest point (moving right, then down) where a node-sized area is clear.</summary>
        private static PointF FreeSpot(GH_Document doc, PointF pt)
        {
            for (int row = 0; row < 8; row++)
                for (int col = 0; col < 6; col++)
                {
                    var c = new PointF(pt.X + col * 340, pt.Y + row * 140);
                    var probe = new RectangleF(c.X - 170, c.Y - 70, 340, 160);
                    if (!doc.Objects.Any(o => o.Attributes != null && o.Attributes.Bounds.IntersectsWith(probe))) return c;
                }
            return pt;
        }

        private static void Restore(AlienComponent node, string file)
        {
            if (node?.ScriptPath == null || string.IsNullOrWhiteSpace(file) || ScriptRepository.Root == null) return;
            var path = Path.Combine(ScriptRepository.Root, ".alien", "history", Path.GetFileName(file)); // name only: no paths from the page
            if (!File.Exists(path)) return;
            ScriptRepository.WriteScript(node.ScriptPath, File.ReadAllText(path));
            node.Reload();
        }
    }

    /// <summary>Facts read from a script's text.</summary>
    public static class ScriptInfo
    {
        /// <summary>The one-line description: the first comment after the header lines ("# Divides a curve into N points.").</summary>
        public static string Description(string source)
        {
            if (string.IsNullOrEmpty(source)) return null;
            foreach (var raw in source.Split('\n').Take(40))
            {
                var line = raw.Trim();
                if (line.Length == 0) continue;
                string body = line.StartsWith("#") ? line.TrimStart('#').Trim() : line.StartsWith("//") ? line.Substring(2).Trim() : null;
                if (body == null) return null; // code started: no description
                if (body.Length == 0 || body.StartsWith("NODE_", StringComparison.OrdinalIgnoreCase) || body.StartsWith("CATEGORY:", StringComparison.OrdinalIgnoreCase) || body.StartsWith("!")
                    || body.StartsWith("r:") || body.StartsWith("r \"") || body.StartsWith("flag:")) continue;
                return body;
            }
            return null;
        }
    }
}
