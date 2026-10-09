using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

using GH_IO.Serialization;

using Grasshopper.Kernel;
using Grasshopper.Kernel.Data;
using Grasshopper.Kernel.Types;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Rhino;

using Alien.Ai;
using Alien.Mcp;
using Alien.Scripting;
using Alien.Ui;

namespace Alien
{
    /// <summary>
    /// Alien: runs an external Python 3 / C# script. Pins come from the script's NODE_INPUTS / NODE_OUTPUTS header
    /// and are rebuilt (wires preserved) whenever the header changes. Unwired inputs fall back to a manual value
    /// (set by an agent over MCP) and then to the header default.
    /// </summary>
    public class AlienComponent : GH_Component, IGH_VariableParameterComponent
    {
        private string _path;                     // resolved full path
        private string _stored = "";              // what we persist ("repo:rel/path.py" or absolute)
        private string _source;
        private DateTime _sourceStamp = DateTime.MinValue;
        private ScriptLanguage _language = ScriptLanguage.None;
        private ScriptHeader _header = ScriptHeader.Empty;   // layout the pins currently reflect
        private ScriptHeader _pendingHeader;
        private bool _rebuildScheduled;
        private bool _loadedFromFile;
        private string _snapshot;                 // header lines read from a .gh file, until the script is re-read
        private ScriptFileWatcher _watcher;
        private readonly Dictionary<string, string> _manual = new Dictionary<string, string>(StringComparer.Ordinal);

        /// <summary>Set by MCP before adding to the document so we don't pop the repository dialog.</summary>
        internal bool SuppressRepositoryPrompt { get; set; }

        public string ScriptPath => _path;
        public ScriptLanguage Language => _language;
        public ScriptHeader Header => _header;
        public IReadOnlyDictionary<string, string> ManualValues => _manual;
        public bool IsRebuilding => _rebuildScheduled;
        public RunRecord LastRun { get; private set; } = new RunRecord();
        /// <summary>Increments after every solution this component takes part in (MCP waits on it).</summary>
        public int SolveStamp { get; private set; }

        /// <summary>What describe-a-node is doing right now ("Attempt 2/4: running…"), shown on the node; null when idle.</summary>
        public string Activity { get; private set; }

        public void SetActivity(string text)
        {
            Activity = text;
            OnDisplayExpired(false);
            Grasshopper.Instances.RedrawCanvas();
        }

        /// <summary>Sliders, toggles and text fields for unwired inputs, drawn on the node (saved with the .gh).</summary>
        public bool ShowControls { get; set; } = true;

        // ── Describe-a-node from the node's prompt bar ───────────────

        private CancellationTokenSource _describe;
        public bool IsDescribing => _describe != null;
        /// <summary>Every describe request on this node and how it went (saved with the .gh, shown in the Alien panel).</summary>
        public List<TranscriptEntry> Transcript { get; } = new List<TranscriptEntry>();
        /// <summary>Why the last request failed, shown on the node until the next one; null when it worked.</summary>
        public string DescribeError { get; private set; }

        /// <summary>Hand the request to the model in the background; progress shows on the node.</summary>
        public void StartDescribe(string request, ScriptLanguage language = ScriptLanguage.Python3)
        {
            request = (request ?? "").Trim();
            if (request.Length == 0 || _describe != null) return;
            var cts = _describe = new CancellationTokenSource();
            var entry = new TranscriptEntry { Request = request };
            Transcript.Add(entry);
            if (Transcript.Count > 50) Transcript.RemoveAt(0);
            DescribeError = null;
            SetActivity("starting…");
            var lang = _language != ScriptLanguage.None ? _language : language;
            Task.Run(async () =>
            {
                try { entry.Complete(await Generator.Run(this, request, lang, null, Generator.DefaultAttempts, null, cts.Token)); }
                catch (OperationCanceledException) { entry.Fail("stopped", null); }
                catch (Exception ex) { entry.Fail("failed", ex.Message); }
                RhinoApp.InvokeOnUiThread(new Action(() =>
                {
                    _describe = null;
                    DescribeError = entry.State == "failed" ? entry.Error : null;
                    SetActivity(null);
                    Attributes?.ExpireLayout();
                    AlienPanel.NodeChanged(this);
                }));
            });
        }

        public void StopDescribing() => _describe?.Cancel();

        /// <summary>Set (or with null, clear) one input's manual value from a control on the node; undoable.</summary>
        public void SetInput(string name, JToken value)
        {
            OnPingDocument()?.UndoUtil.RecordGenericObjectEvent("Alien input", this);
            SetManualValues(new Dictionary<string, JToken> { [name] = value });
        }

        public AlienComponent()
            : base("Alien", "Alien",
                "Live Python 3 / C# script node. Pins come from the script header; AI agents edit it over MCP.\n" +
                "Right-click for script options. Double-click to open the script.",
                "Alien", "Script")
        { }

        public override Guid ComponentGuid => new Guid("F082B5BE-EC5F-4BC3-9927-7147C53A41F5");
        public override GH_Exposure Exposure => GH_Exposure.primary;

        protected override void RegisterInputParams(GH_InputParamManager pManager) { }
        protected override void RegisterOutputParams(GH_OutputParamManager pManager) { }

        // ── Script binding ───────────────────────────────────────────

        public void SetScript(string fullPath)
        {
            _path = string.IsNullOrEmpty(fullPath) ? null : Path.GetFullPath(fullPath);
            _stored = ScriptRepository.ToStored(_path);
            _source = null;
            _sourceStamp = DateTime.MinValue;
            SetupWatcher();
            NickName = _path == null ? "Alien" : Path.GetFileNameWithoutExtension(_path);
            ExpireSolution(true);
        }

        /// <summary>Force a re-read of the script file on the next solve.</summary>
        public void Reload()
        {
            _sourceStamp = DateTime.MinValue;
            ExpireSolution(true);
        }

        /// <summary>Re-read the file if it changed. Returns true when the pin layout must be rebuilt first.</summary>
        private bool RefreshSource(out string error)
        {
            error = null;
            if (_path == null || !File.Exists(_path))
            {
                error = _path == null ? "No script. Right-click > New or Open script." : $"Script not found: {_path}";
                return false;
            }

            var stamp = File.GetLastWriteTimeUtc(_path);
            if (_source != null && stamp == _sourceStamp) return false;

            try { _source = ReadShared(_path); }
            catch (IOException) { error = "Script is locked (being saved?) — will retry."; return false; }
            _sourceStamp = stamp;
            _language = ScriptLanguages.Detect(_path, _source);

            var parsed = HeaderParser.Parse(_source);
            if (parsed.SameAs(_header))
            {
                _header = parsed; // warnings may differ
                return false;
            }
            _pendingHeader = parsed;
            return true;
        }

        private static string ReadShared(string path)
        {
            using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete);
            using var sr = new StreamReader(fs);
            return sr.ReadToEnd();
        }

        // ── Solve ────────────────────────────────────────────────────

        protected override void BeforeSolveInstance()
        {
            LastRun = new RunRecord();
            base.BeforeSolveInstance();
        }

        protected override void SolveInstance(IGH_DataAccess DA)
        {
            if (DA.Iteration == 0)
            {
                bool needsRebuild = RefreshSource(out var error);
                if (error != null)
                {
                    LastRun.Status = _path == null ? "no-script" : "error";
                    LastRun.Note = error;
                    AddRuntimeMessage(_path == null ? GH_RuntimeMessageLevel.Remark : GH_RuntimeMessageLevel.Warning, error);
                    return;
                }
                if (needsRebuild)
                {
                    ScheduleRebuild();
                    LastRun.Status = "rebuilding";
                    return;
                }
                foreach (var w in _header.Warnings)
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Warning, "Header: " + w);
            }
            if (_source == null || _rebuildScheduled) return;

            bool csharp = _language == ScriptLanguage.CSharp;
            var inputs = new Dictionary<string, object>();
            RunResult result;
            try
            {
                for (int i = 0; i < Params.Input.Count && i < _header.Inputs.Count; i++)
                {
                    var def = _header.Inputs[i];
                    inputs[def.Name] = ReadInput(DA, i, def, csharp);
                }
                result = ScriptRunner.Run(_language, _path, _source, _header, inputs);
            }
            catch (Exception ex)
            {
                // Plugin-side failure (input conversion etc.) — report it like a script error instead of a bare
                // "Solution exception", so agents see what happened.
                result = RunResult.Fail($"Alien: {ex.GetType().Name}: {ex.Message}");
            }
            LastRun.Add(result);

            if (!result.Success)
            {
                var where = result.Line.HasValue ? $"line {result.Line}: " : "";
                AddRuntimeMessage(GH_RuntimeMessageLevel.Error, where + result.Error);
                return;
            }

            for (int i = 0; i < Params.Output.Count && i < _header.Outputs.Count; i++)
            {
                if (!result.Outputs.TryGetValue(_header.Outputs[i].Name, out var value)) continue;
                try { WriteOutput(DA, i, value); }
                catch (Exception ex)
                {
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Error, $"output '{_header.Outputs[i].Name}': {ex.Message}");
                    LastRun.Note = $"could not write output '{_header.Outputs[i].Name}': {ex.Message}";
                }
            }
        }

        protected override void AfterSolveInstance()
        {
            base.AfterSolveInstance();
            if (LastRun.Iterations > 0)
            {
                LastRun.Status = LastRun.Failures > 0 ? "error" : "ok";
                var so = LastRun.Stdout.Trim();
                if (so.Length > 0)
                    AddRuntimeMessage(GH_RuntimeMessageLevel.Remark, so.Length > 1500 ? so.Substring(0, 1500) + "…" : so);
            }
            Message = _path == null ? "no script"
                : LastRun.Iterations > 0 ? $"{ScriptLanguages.Tag(_language)} · {LastRun.Ms:0} ms"
                : ScriptLanguages.Tag(_language);
            Attributes?.ExpireLayout(); // the error strip on the node changes height
            SolveStamp++;
        }

        private object ReadInput(IGH_DataAccess DA, int index, ParamDef def, bool csharp)
        {
            var param = Params.Input[index];
            if (param.VolatileDataCount > 0)
            {
                switch (def.Access)
                {
                    case ParamAccess.Item:
                        object o = null;
                        DA.GetData(index, ref o);
                        return Values.Unwrap(o);
                    case ParamAccess.List:
                        var list = new List<object>();
                        DA.GetDataList(index, list);
                        return list.Select(Values.Unwrap).ToList();
                    default:
                        // DA.GetDataTree<IGH_Goo> fails (null tree) for typed params such as Param_Geometry,
                        // so read the param's own structure — for tree access it holds the whole input tree.
                        var data = param.VolatileData;
                        var branches = new List<object>();
                        foreach (var path in data.Paths)
                        {
                            var branch = new List<object>();
                            foreach (var item in data.get_Branch(path)) branch.Add(Values.Unwrap(item));
                            branches.Add(branch);
                        }
                        return branches;
                }
            }

            if (_manual.TryGetValue(def.Name, out var manual))
            {
                var v = Values.Resolve(manual, def);
                if (v != null) return v;
            }
            if (def.Default != null)
            {
                var v = Values.Resolve(def.Default, def);
                if (v != null) return v;
            }
            return Values.Empty(def, valueTypesNeedDefault: csharp);
        }

        private static void WriteOutput(IGH_DataAccess DA, int index, object value)
        {
            value = Values.Normalise(value);
            if (value == null) return;
            if (!Values.IsSequence(value))
            {
                DA.SetData(index, value);
                return;
            }

            var items = Values.ToList(value);
            if (!items.Any(Values.IsSequence))
            {
                DA.SetDataList(index, items.Select(Values.Normalise));
                return;
            }

            // Nested lists → one branch per sub-list, under this iteration's target path.
            var basePath = DA.ParameterTargetPath(index);
            var tree = new Grasshopper.DataTree<object>();
            for (int b = 0; b < items.Count; b++)
            {
                var path = basePath.AppendElement(b);
                if (Values.IsSequence(items[b]))
                    tree.AddRange(Values.ToList(items[b]).Select(Values.Normalise), path);
                else
                    tree.Add(Values.Normalise(items[b]), path);
            }
            DA.SetDataTree(index, tree);
        }

        // ── Pin rebuild (Hops pattern: snapshot wires by name → rebuild → restore) ──

        private void ScheduleRebuild()
        {
            if (_rebuildScheduled) return;
            _rebuildScheduled = true;
            OnPingDocument()?.ScheduleSolution(1, _ =>
            {
                _rebuildScheduled = false;
                if (_pendingHeader == null) return;
                RebuildParameters(_pendingHeader);
                _pendingHeader = null;
                ExpireSolution(false);
            });
        }

        private void RebuildParameters(ScriptHeader header)
        {
            var sources = new Dictionary<string, List<IGH_Param>>();
            foreach (var p in Params.Input) sources.TryAdd(p.NickName, p.Sources.ToList());
            var recipients = new Dictionary<string, List<IGH_Param>>();
            foreach (var p in Params.Output) recipients.TryAdd(p.NickName, p.Recipients.ToList());

            foreach (var p in Params.Input.ToList()) Params.UnregisterInputParameter(p, true);
            foreach (var p in Params.Output.ToList()) Params.UnregisterOutputParameter(p, true);

            foreach (var def in header.Inputs)
            {
                var p = TypeMap.CreateInputParam(def);
                Params.RegisterInputParam(p);
            }
            foreach (var def in header.Outputs)
                Params.RegisterOutputParam(TypeMap.CreateOutputParam(def));

            VariableParameterMaintenance();
            Params.OnParametersChanged();

            foreach (var p in Params.Input)
                if (sources.TryGetValue(p.NickName, out var src))
                    foreach (var s in src) p.AddSource(s);
            foreach (var p in Params.Output)
                if (recipients.TryGetValue(p.NickName, out var rec))
                    foreach (var r in rec) r.AddSource(p);

            _header = header;
            OnDisplayExpired(true);
        }

        public bool CanInsertParameter(GH_ParameterSide side, int index) => false;
        public bool CanRemoveParameter(GH_ParameterSide side, int index) => false;
        public IGH_Param CreateParameter(GH_ParameterSide side, int index) => new Grasshopper.Kernel.Parameters.Param_GenericObject();
        public bool DestroyParameter(GH_ParameterSide side, int index) => true;

        public void VariableParameterMaintenance()
        {
            foreach (var p in Params.Input.Concat(Params.Output))
                if (p.Attributes == null) p.CreateAttributes();
        }

        // ── Manual values (agent-set values for unwired inputs) ──────

        /// <summary>Set manual values (raw JSON tokens) by input name. Null/empty removes. Returns unknown names.</summary>
        public List<string> SetManualValues(IDictionary<string, JToken> values)
        {
            var unknown = new List<string>();
            foreach (var kv in values)
            {
                if (_header.FindInput(kv.Key) == null) { unknown.Add(kv.Key); continue; }
                if (kv.Value == null || kv.Value.Type == JTokenType.Null) _manual.Remove(kv.Key);
                else _manual[kv.Key] = kv.Value.ToString(Formatting.None);
            }
            ExpireSolution(true);
            return unknown;
        }

        // ── Persistence ──────────────────────────────────────────────

        public override bool Write(GH_IWriter writer)
        {
            writer.SetString("ScriptRef", _stored ?? "");
            // Header lines let us restore _header without re-reading the file, so opening a .gh doesn't rebuild pins.
            writer.SetString("HeaderSnapshot", _source != null ? string.Join("\n", HeaderLines(_source)) : _snapshot ?? "");
            writer.SetBoolean("ShowControls", ShowControls);
            if (Transcript.Count > 0) writer.SetString("Transcript", JsonConvert.SerializeObject(Transcript));
            writer.SetInt32("ManualCount", _manual.Count);
            int i = 0;
            foreach (var kv in _manual)
            {
                writer.SetString($"Manual_{i}_Name", kv.Key);
                writer.SetString($"Manual_{i}_Value", kv.Value);
                i++;
            }
            return base.Write(writer);
        }

        public override bool Read(GH_IReader reader)
        {
            _loadedFromFile = true;
            _stored = reader.ItemExists("ScriptRef") ? reader.GetString("ScriptRef") : "";
            _path = ScriptRepository.FromStored(_stored);
            if (reader.ItemExists("HeaderSnapshot"))
            {
                _snapshot = reader.GetString("HeaderSnapshot");
                _header = HeaderParser.Parse(_snapshot);
            }
            ShowControls = !reader.ItemExists("ShowControls") || reader.GetBoolean("ShowControls");
            Transcript.Clear();
            if (reader.ItemExists("Transcript"))
            {
                try { Transcript.AddRange(JsonConvert.DeserializeObject<List<TranscriptEntry>>(reader.GetString("Transcript")) ?? new List<TranscriptEntry>()); }
                catch { /* an unreadable history is not worth failing the file over */ }
            }
            _manual.Clear();
            int n = reader.ItemExists("ManualCount") ? reader.GetInt32("ManualCount") : 0;
            for (int i = 0; i < n; i++)
                _manual[reader.GetString($"Manual_{i}_Name")] = reader.GetString($"Manual_{i}_Value");
            return base.Read(reader);
        }

        private static IEnumerable<string> HeaderLines(string source) =>
            (source ?? "").Split('\n').Take(HeaderParser.MaxHeaderLines)
                .Where(l => l.Contains("NODE_INPUTS") || l.Contains("NODE_OUTPUTS"))
                .Select(l => l.TrimEnd('\r'));

        // ── Document lifecycle ───────────────────────────────────────

        public override void AddedToDocument(GH_Document document)
        {
            base.AddedToDocument(document);
            McpServer.Instance.Register(this);
            SetupWatcher();

            if (!ScriptRepository.IsConfigured && !_loadedFromFile && !SuppressRepositoryPrompt)
                RhinoApp.InvokeOnUiThread(new Action(() => AlienUi.PromptForRepository()));
        }

        public override void RemovedFromDocument(GH_Document document)
        {
            McpServer.Instance.Unregister(this);
            _watcher?.Dispose();
            _watcher = null;
            StopDescribing();
            base.RemovedFromDocument(document);
        }

        public override void DocumentContextChanged(GH_Document document, GH_DocumentContext context)
        {
            base.DocumentContextChanged(document, context);
            if (context == GH_DocumentContext.Close || context == GH_DocumentContext.Unloaded)
                McpServer.Instance.Unregister(this);
            else if (context == GH_DocumentContext.Loaded || context == GH_DocumentContext.Open)
                McpServer.Instance.Register(this);
        }

        private void SetupWatcher()
        {
            if (_path == null) { _watcher?.Stop(); return; }
            _watcher ??= new ScriptFileWatcher(OnFileChanged);
            _watcher.Watch(_path);
        }

        private void OnFileChanged()
        {
            if (_path == null || !File.Exists(_path)) return;
            if (File.GetLastWriteTimeUtc(_path) == _sourceStamp) return; // already picked up (e.g. MCP reload)
            ExpireSolution(true);
        }

        // ── Canvas UI ────────────────────────────────────────────────

        public override void CreateAttributes() => m_attributes = new AlienAttributes(this);

        public override void AppendAdditionalMenuItems(System.Windows.Forms.ToolStripDropDown menu)
        {
            base.AppendAdditionalMenuItems(menu);
            bool hasFile = _path != null && File.Exists(_path);

            MenuSection(menu, "Script");
            Menu_AppendItem(menu, "Edit script", (s, e) => AlienUi.OpenInEditor(_path), hasFile);
            Menu_AppendItem(menu, "New Python script…", (s, e) => AlienUi.NewScript(this, ScriptLanguage.Python3));
            Menu_AppendItem(menu, "New C# script…", (s, e) => AlienUi.NewScript(this, ScriptLanguage.CSharp));
            Menu_AppendItem(menu, "Open script…", (s, e) => AlienUi.OpenScript(this));
            Menu_AppendItem(menu, "Reveal in folder", (s, e) => AlienUi.Reveal(_path), hasFile);
            Menu_AppendItem(menu, "Reload", (s, e) => Reload(), hasFile);

            MenuSection(menu, "Describe");
            Menu_AppendItem(menu, "Open the Alien panel", (s, e) => AlienPanel.Open());
            if (IsDescribing) Menu_AppendItem(menu, "Stop describing", (s, e) => StopDescribing());

            MenuSection(menu, "Inputs");
            Menu_AppendItem(menu, "Show controls on the node", (s, e) => { ShowControls = !ShowControls; Attributes?.ExpireLayout(); Grasshopper.Instances.RedrawCanvas(); }, true, ShowControls);
            Menu_AppendItem(menu, "Reset inputs to their defaults", (s, e) =>
            {
                OnPingDocument()?.UndoUtil.RecordGenericObjectEvent("Alien inputs", this);
                SetManualValues(_manual.Keys.ToDictionary(k => k, k => (JToken)null));
            }, _manual.Count > 0);

            MenuSection(menu, "Share");
            Menu_AppendItem(menu, "Copy node id (for agents)", (s, e) => AlienUi.CopyText(InstanceGuid.ToString()));

            MenuSection(menu, "Settings");
            var repo = ScriptRepository.Root ?? "(not set)";
            Menu_AppendItem(menu, $"Script repository: {repo}…", (s, e) => AlienUi.PromptForRepository());
            Menu_AppendItem(menu, "Open settings file (model, API key)", (s, e) => AlienUi.OpenSettings());
        }

        /// <summary>Separator + a greyed caption, so the menu reads as Script · Describe · Share · Settings.</summary>
        private static void MenuSection(System.Windows.Forms.ToolStripDropDown menu, string title)
        {
            Menu_AppendSeparator(menu);
            var item = Menu_AppendItem(menu, title.ToUpperInvariant());
            item.Enabled = false;
        }

        protected override Bitmap Icon
        {
            get
            {
                var bmp = new Bitmap(24, 24);
                using var g = Graphics.FromImage(bmp);
                g.SmoothingMode = SmoothingMode.AntiAlias;
                g.Clear(Color.Transparent);
                using var bg = new SolidBrush(Color.FromArgb(26, 26, 26));
                g.FillEllipse(bg, 1, 1, 22, 22);
                using var pen = new Pen(AlienAttributes.Accent, 2f);
                g.DrawEllipse(pen, 3, 3, 18, 18);
                using var dot = new SolidBrush(AlienAttributes.Accent);
                g.FillEllipse(dot, 9, 9, 6, 6);
                return bmp;
            }
        }
    }
}
