using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

using Newtonsoft.Json;
using Newtonsoft.Json.Linq;

using Rhino;

using Alien.Ai;
using Alien.Scripting;

namespace Alien
{
    /// <summary>
    /// The node library: every script in the repository with a header, sorted into categories. A node's category is,
    /// in order: its <c># CATEGORY:</c> header line, a model's pick (asked once per header in the background, cached in
    /// .alien/library.json), or a keyword guess, so the listing is grouped straight away and gets better on its own.
    /// </summary>
    public static class Library
    {
        public sealed class Category
        {
            public string Key { get; }
            public string Label { get; }
            public string Hint { get; }
            public string[] Words { get; }
            public Category(string key, string label, string hint, string words)
            {
                Key = key; Label = label; Hint = hint;
                Words = words.Split(' ', StringSplitOptions.RemoveEmptyEntries);
            }
        }

        public static readonly Category[] Categories =
        {
            new Category("site", "Site & context", "maps, parcels, terrain, roads, planning data around a site",
                "site map address parcel lot cadastre cadastral zone zoning overlay terrain topography topo contour gis street road osm vicmap survey context planning setback"),
            new Category("massing", "Massing & objects", "building masses, floors and levels, furniture and 3D objects",
                "massing mass building floor floors level levels storey storeys tower envelope furniture chair object volume extrude extrusion slab facade roof"),
            new Category("entourage", "Entourage", "people, trees, plants, cars: scale and life in drawings",
                "figure figures person people human tree trees plant plants vegetation car cars entourage silhouette scale"),
            new Category("materials", "Materials", "material finishes, textures, colours",
                "material materials texture textures finish finishes pbr swatch render shader colour color"),
            new Category("views", "Views & drawings", "cameras, plans, sections, elevations, sheets, annotation",
                "view views camera section sections elevation elevations plan plans layout sheet drawing drawings annotation label labels dimension print clipping"),
            new Category("data", "Data & lists", "lists, trees, sorting, filtering, files and spreadsheets",
                "list lists tree branch branches data sort filter csv json excel path cull item items index flatten graft"),
            new Category("geometry", "Geometry", "curves, surfaces, meshes, points, patterns and transforms",
                "curve curves surface surfaces mesh meshes brep point points divide offset loft grid pattern polygon voronoi subd panel panels transform twist"),
            new Category("analysis", "Analysis", "measuring and checking: areas, sun, shadow, distances, schedules",
                "analysis area areas solar sun shadow shadows daylight wind distance measure measures schedule gfa compliance radiation"),
            new Category("utilities", "Utilities", "anything else: helpers, debugging, files, documents",
                "utility utilities helper debug log bake export import file files document"),
        };

        private static readonly Regex HeaderLine = new Regex(@"^\s*(?:#|//)\s*CATEGORY\s*:\s*(.+?)\s*$", RegexOptions.IgnoreCase | RegexOptions.Multiline);

        /// <summary>The category key a <c># CATEGORY:</c> line names (by label or key, any case), or null.</summary>
        public static string FromHeader(string head)
        {
            var m = HeaderLine.Match(head ?? "");
            return m.Success ? KeyOf(m.Groups[1].Value) : null;
        }

        /// <summary>A category key from free text ("Site & context", "site", "Materials"), or null.</summary>
        public static string KeyOf(string text)
        {
            var t = (text ?? "").Trim().ToLowerInvariant();
            if (t.Length == 0) return null;
            var c = Categories.FirstOrDefault(x => x.Key == t || x.Label.ToLowerInvariant() == t)
                    ?? Categories.FirstOrDefault(x => x.Label.ToLowerInvariant().StartsWith(t) || t.StartsWith(x.Key));
            return c?.Key;
        }

        /// <summary>Keyword guess: words in the name count three times, the description twice, the rest of the header once.</summary>
        public static string Guess(string name, string description, string head)
        {
            var scores = new Dictionary<string, int>();
            void Add(string text, int weight)
            {
                foreach (Match w in Regex.Matches((text ?? "").ToLowerInvariant(), "[a-z]+"))
                    foreach (var c in Categories)
                        if (c.Words.Contains(w.Value)) scores[c.Key] = scores.TryGetValue(c.Key, out var s) ? s + weight : weight;
            }
            Add(name?.Replace('_', ' '), 3);
            Add(description, 2);
            Add(head, 1);
            return scores.Count == 0 ? "utilities" : scores.OrderByDescending(kv => kv.Value).First().Key;
        }

        // ── The listing ──────────────────────────────────────────────

        private sealed class Entry
        {
            public string Path, Name, Head, Description, Hash;
        }

        /// <summary>Nodes for the panel: name, path, language, suite or yours, description, category and where it came from.</summary>
        public static JArray Nodes()
        {
            var arr = new JArray();
            var root = ScriptRepository.Root;
            if (root == null) return arr;
            var cache = LoadCache();
            var pending = new List<Entry>();
            foreach (var rel in ScriptRepository.ListScripts().Take(400))
            {
                string head;
                try { head = string.Join("\n", File.ReadLines(System.IO.Path.Combine(root, rel)).Take(40)); }
                catch { continue; }
                if (!head.Contains("NODE_OUTPUTS") && !head.Contains("NODE_INPUTS")) continue;
                var e = new Entry
                {
                    Path = rel, Name = System.IO.Path.GetFileNameWithoutExtension(rel), Head = head,
                    Description = Ui.ScriptInfo.Description(head), Hash = Sha1(head),
                };
                string category = FromHeader(head), source = "header";
                if (category == null && cache[rel] is JObject c && c.Value<string>("hash") == e.Hash && KeyOf(c.Value<string>("category")) is string picked)
                {
                    category = picked;
                    source = "model";
                }
                if (category == null)
                {
                    category = Guess(e.Name, e.Description, head);
                    source = "keywords";
                    pending.Add(e);
                }
                arr.Add(new JObject
                {
                    ["name"] = e.Name,
                    ["path"] = rel,
                    ["lang"] = ScriptLanguages.Tag(ScriptLanguages.FromPath(rel)),
                    ["group"] = rel.StartsWith("suite/", StringComparison.OrdinalIgnoreCase) ? "suite" : "yours",
                    ["description"] = e.Description,
                    ["category"] = category,
                    ["categorySource"] = source,
                });
            }
            if (pending.Count > 0) ClassifyLater(pending);
            return arr;
        }

        public static JArray CategoryList() =>
            new JArray(Categories.Select(c => new JObject { ["key"] = c.Key, ["label"] = c.Label, ["hint"] = c.Hint }));

        // ── Model classification (background, cached) ────────────────

        private static Task _running;
        private static DateTime _lastTry = DateTime.MinValue;

        /// <summary>Raised (on a worker thread) when new categories were cached.</summary>
        public static event Action Classified;

        private static string CacheFile => ScriptRepository.Root == null ? null : System.IO.Path.Combine(ScriptRepository.Root, ".alien", "library.json");

        private static JObject LoadCache()
        {
            try
            {
                var f = CacheFile;
                if (f != null && File.Exists(f)) return JObject.Parse(File.ReadAllText(f));
            }
            catch { }
            return new JObject();
        }

        /// <summary>
        /// Ask the describe model to sort uncategorised nodes (one request for up to 40 at a time). Runs at most one at a
        /// time and, after a failure or with nothing new, no more than every 10 minutes. Needs a model (key or Ollama).
        /// </summary>
        private static void ClassifyLater(List<Entry> pending)
        {
            if (_running != null && !_running.IsCompleted) return;
            if (DateTime.UtcNow - _lastTry < TimeSpan.FromMinutes(10)) return;
            if (ScriptRepository.Setting("classify") == "off") return;
            ModelChoice model;
            try { model = ModelChoice.Resolve(ScriptRepository.Setting("classifyModel")); }
            catch { return; } // no key and no local model: keywords only
            _lastTry = DateTime.UtcNow;
            var batch = pending.Take(40).ToList();
            _running = Task.Run(async () =>
            {
                try
                {
                    var picks = await Ask(model, batch).ConfigureAwait(false);
                    if (picks.Count == 0) return;
                    var cache = LoadCache();
                    foreach (var e in batch)
                        if (picks.TryGetValue(e.Path, out var key))
                            cache[e.Path] = new JObject { ["hash"] = e.Hash, ["category"] = key };
                    Directory.CreateDirectory(System.IO.Path.GetDirectoryName(CacheFile));
                    File.WriteAllText(CacheFile, cache.ToString(Formatting.Indented));
                    if (pending.Count > batch.Count) _lastTry = DateTime.MinValue; // more to do: next listing continues
                    Classified?.Invoke();
                }
                catch (Exception ex) { RhinoApp.WriteLine("Alien library: could not sort nodes into categories (" + ex.Message + ")"); }
            });
        }

        private static async Task<Dictionary<string, string>> Ask(ModelChoice model, List<Entry> batch)
        {
            var sb = new StringBuilder();
            sb.Append("Sort these Grasshopper script nodes into categories for a node library used by architects.\n\nCategories:\n");
            foreach (var c in Categories) sb.Append("- ").Append(c.Key).Append(": ").Append(c.Hint).Append('\n');
            sb.Append("\nReply with only a JSON object mapping each node's path to one category key, nothing else.\n\nNodes:\n");
            foreach (var e in batch)
            {
                var head = string.Join("\n", e.Head.Split('\n').Where(l => l.TrimStart().StartsWith("#") || l.TrimStart().StartsWith("//")).Take(12));
                if (head.Length > 900) head = head.Substring(0, 900);
                sb.Append("\n### ").Append(e.Path).Append('\n').Append(head).Append('\n');
            }
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(90));
            var reply = await ModelClient.Complete(model, new[] { new ChatMessage("user", sb.ToString()) }, cts.Token).ConfigureAwait(false);
            var text = reply.Text ?? "";
            int a = text.IndexOf('{'), b = text.LastIndexOf('}');
            var picks = new Dictionary<string, string>();
            if (a < 0 || b <= a) return picks;
            foreach (var p in JObject.Parse(text.Substring(a, b - a + 1)).Properties())
                if (KeyOf(p.Value.ToString()) is string key) picks[p.Name] = key;
            return picks;
        }

        private static string Sha1(string s)
        {
            using var h = SHA1.Create();
            return BitConverter.ToString(h.ComputeHash(Encoding.UTF8.GetBytes(s))).Replace("-", "").Substring(0, 16).ToLowerInvariant();
        }
    }
}
