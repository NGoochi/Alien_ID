using System;
using System.Collections.Generic;
using System.Drawing;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text.RegularExpressions;

using Grasshopper;
using Grasshopper.Kernel;

using Newtonsoft.Json.Linq;

using Rhino;
using Rhino.Display;
using Rhino.DocObjects;
using Rhino.FileIO;
using Rhino.Geometry;
using Rhino.PlugIns;
using Rhino.Render;

namespace Alien.Mcp
{
    /// <summary>
    /// Rhino-document tools (objects, layers, view, doc), component search and command help.
    /// The tool set follows McNeel's RhinoAI MCP server (MIT), folded into fewer, token-lean tools.
    /// </summary>
    public static partial class Tools
    {
        private static RhinoDoc RequireRhino() => RhinoDoc.ActiveDoc ?? throw new ToolException("No Rhino document is open.");

        private static string RId(RhinoObject o) => o.Id.ToString("N").Substring(0, 8);

        private static ObjectEnumeratorSettings AllObjects(bool hidden) => new ObjectEnumeratorSettings
        {
            NormalObjects = true,
            LockedObjects = true,
            HiddenObjects = hidden,
            DeletedObjects = false,
            IncludeLights = true,
            IncludeGrips = false,
            IncludePhantoms = false,
            ReferenceObjects = true
        };

        /// <summary>Object by full Guid or a 6+ character id prefix; null when none.</summary>
        internal static RhinoObject FindRhinoObject(RhinoDoc doc, string id)
        {
            id = (id ?? "").Trim();
            if (Guid.TryParse(id, out var g)) return doc.Objects.FindId(g);
            var key = id.Replace("-", "").ToLowerInvariant();
            if (key.Length < 6 || !key.All(Uri.IsHexDigit)) return null;
            RhinoObject hit = null;
            foreach (var o in doc.Objects.GetObjectList(AllObjects(true)))
            {
                if (!o.Id.ToString("N").StartsWith(key, StringComparison.Ordinal)) continue;
                if (hit != null) throw new ToolException($"Object id '{id}' is ambiguous — use more characters.");
                hit = o;
            }
            return hit;
        }

        /// <summary>Indices of a layer and all its sublayers.</summary>
        private static HashSet<int> LayerAndChildren(RhinoDoc doc, string path)
        {
            path = path.Trim();
            var set = new HashSet<int>();
            foreach (var l in doc.Layers)
                if (!l.IsDeleted && (l.FullPath.Equals(path, StringComparison.OrdinalIgnoreCase)
                                     || l.FullPath.StartsWith(path + "::", StringComparison.OrdinalIgnoreCase)))
                    set.Add(l.Index);
            if (set.Count == 0) throw new ToolException($"No layer '{path}'. Call layers for the tree.");
            return set;
        }

        private static IEnumerable<RhinoObject> ObjectsOnLayer(RhinoDoc doc, string path)
        {
            var set = LayerAndChildren(doc, path);
            return doc.Objects.GetObjectList(AllObjects(false)).Where(o => set.Contains(o.Attributes.LayerIndex));
        }

        private static ObjectType ParseObjectType(string s) => s.Trim().ToLowerInvariant() switch
        {
            "point" => ObjectType.Point,
            "pointcloud" or "pointset" => ObjectType.PointSet,
            "curve" => ObjectType.Curve,
            "surface" => ObjectType.Surface,
            "brep" or "polysurface" => ObjectType.Brep,
            "extrusion" => ObjectType.Extrusion,
            "mesh" => ObjectType.Mesh,
            "subd" => ObjectType.SubD,
            "block" or "instance" => ObjectType.InstanceReference,
            "annotation" or "text" or "dimension" => ObjectType.Annotation,
            "hatch" => ObjectType.Hatch,
            "light" => ObjectType.Light,
            "clippingplane" => ObjectType.ClipPlane,
            _ => throw new ToolException("type must be one of: point, pointcloud, curve, surface, brep, extrusion, mesh, subd, block, annotation, hatch, light, clippingplane.")
        };

        /// <summary>"Brep", or "Block:*Curtain wall" for instances (plugin objects such as VisualARQ's are blocks).</summary>
        private static string TypeLabel(RhinoObject o)
        {
            if (o is InstanceObject io)
            {
                var name = io.InstanceDefinition?.Name ?? "?";
                int brace = name.IndexOf('{');
                return "Block:" + Trim(brace > 0 ? name.Substring(0, brace) : name, 30);
            }
            return o.ObjectType.ToString();
        }

        private static string ObjectRow(RhinoDoc doc, RhinoObject o)
        {
            var layer = o.Attributes.LayerIndex >= 0 && o.Attributes.LayerIndex < doc.Layers.Count ? doc.Layers[o.Attributes.LayerIndex].FullPath : "?";
            var row = $"{RId(o)} {TypeLabel(o)} {layer}";
            if (!string.IsNullOrEmpty(o.Name)) row += $" '{Trim(o.Name, 40)}'";
            if (o.IsHidden) row += " hidden";
            if (o.IsLocked) row += " locked";
            return row;
        }

        // ── objects ─────────────────────────────────────────────────

        private static ToolResult Objects(JObject a)
        {
            var action = (a.Value<string>("action") ?? "list").Trim().ToLowerInvariant();
            int limit = Math.Clamp(a.Value<int?>("limit") ?? 30, 0, 500);
            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireRhino();
                var found = QueryObjects(doc, a, out var notes);
                var r = new JObject { ["count"] = found.Count };
                switch (action)
                {
                    case "list":
                        if (found.Count == 0) break;
                        r["types"] = new JObject(found.GroupBy(TypeLabel).OrderByDescending(g => g.Count()).Take(12)
                            .Select(g => new JProperty(g.Key, g.Count())));
                        r["layers"] = new JObject(found.GroupBy(o => o.Attributes.LayerIndex).OrderByDescending(g => g.Count()).Take(12)
                            .Select(g => new JProperty(g.Key >= 0 && g.Key < doc.Layers.Count ? doc.Layers[g.Key].FullPath : "?", g.Count())));
                        var bb = Bounds(found);
                        if (bb.IsValid) r["bbox"] = $"{Num3(bb.Min)} -> {Num3(bb.Max)}";
                        if (limit > 0) r["rows"] = new JArray(found.Take(limit).Select(o => ObjectRow(doc, o)));
                        if (found.Count > limit) r["more"] = found.Count - limit;
                        break;

                    case "select":
                        doc.Objects.UnselectAll();
                        r["selected"] = found.Count(o => o.Select(true) > 0);
                        doc.Views.Redraw();
                        break;

                    case "zoom":
                        var box = Bounds(found);
                        if (!box.IsValid) throw new ToolException("Nothing to zoom to.");
                        var view = doc.Views.ActiveView ?? throw new ToolException("No active Rhino view.");
                        view.ActiveViewport.ZoomBoundingBox(box);
                        view.Redraw();
                        r["bbox"] = $"{Num3(box.Min)} -> {Num3(box.Max)}";
                        break;

                    default:
                        throw new ToolException("action must be 'list', 'select' or 'zoom'.");
                }
                if (notes.Count > 0) r["note"] = string.Join("; ", notes);
                return r;
            }));
        }

        private static List<RhinoObject> QueryObjects(RhinoDoc doc, JObject a, out List<string> notes)
        {
            notes = new List<string>();
            bool hidden = a.Value<bool?>("hidden") ?? false;
            bool selected = a.Value<bool?>("selected") ?? false;
            var typeName = a.Value<string>("type");
            ObjectType? type = string.IsNullOrWhiteSpace(typeName) ? null : ParseObjectType(typeName);
            var ids = a["ids"] is JArray ja ? ja.Select(t => t.ToString()).ToList()
                    : a.Value<string>("ids") is string one ? new List<string> { one } : null;

            IEnumerable<RhinoObject> objs;
            if (ids != null)
            {
                var list = new List<RhinoObject>();
                foreach (var id in ids)
                {
                    var o = FindRhinoObject(doc, id);
                    if (o == null) notes.Add($"no object '{id}'"); else list.Add(o);
                }
                objs = list.Where(o => (type == null || (o.ObjectType & type.Value) != 0) && (!selected || o.IsSelected(false) > 0));
            }
            else
            {
                var settings = AllObjects(hidden);
                if (selected) settings.SelectedObjectsFilter = true;
                if (type != null) settings.ObjectTypeFilter = type.Value;
                objs = doc.Objects.GetObjectList(settings);
            }

            if (a.Value<string>("layer") is string layer && layer.Trim().Length > 0)
            {
                var set = LayerAndChildren(doc, layer);
                objs = objs.Where(o => set.Contains(o.Attributes.LayerIndex));
            }
            if (a.Value<string>("name") is string name && name.Length > 0)
                objs = objs.Where(o => (o.Name ?? "").Contains(name, StringComparison.OrdinalIgnoreCase));
            return objs.ToList();
        }

        private static BoundingBox Bounds(IEnumerable<RhinoObject> objs)
        {
            var box = BoundingBox.Empty;
            foreach (var o in objs)
                if (o.Geometry != null) box.Union(o.Geometry.GetBoundingBox(true));
            return box;
        }

        // ── layers ──────────────────────────────────────────────────

        private static ToolResult Layers(JObject a)
        {
            var path = a.Value<string>("path");
            return ToolResult.Json(UiThread.Run(() =>
            {
                var doc = RequireRhino();
                return string.IsNullOrWhiteSpace(path) ? LayerTree(doc, a.Value<string>("filter")) : ChangeLayer(doc, path.Trim(), a);
            }));
        }

        private static JObject LayerTree(RhinoDoc doc, string filter)
        {
            var counts = new Dictionary<int, int>();
            foreach (var o in doc.Objects.GetObjectList(AllObjects(true)))
                counts[o.Attributes.LayerIndex] = counts.TryGetValue(o.Attributes.LayerIndex, out var c) ? c + 1 : 1;

            int current = doc.Layers.CurrentLayerIndex, total = 0, matched = 0;
            var rows = new JArray();
            foreach (var l in doc.Layers)
            {
                if (l.IsDeleted) continue;
                total++;
                if (!string.IsNullOrEmpty(filter) && !l.FullPath.Contains(filter, StringComparison.OrdinalIgnoreCase)) continue;
                if (++matched > 300) continue;
                counts.TryGetValue(l.Index, out int n);
                var row = $"{l.FullPath} [{n}]";
                if (!l.IsVisible) row += " off";
                if (l.IsLocked) row += " locked";
                if (l.Index == current) row += " current";
                row += " " + Hex(l.Color);
                if (l.RenderMaterial is RenderMaterial m) row += $" mat='{m.Name}'";
                rows.Add(row);
            }
            var r = new JObject { ["layers"] = total, ["rows"] = rows };
            if (matched > 300) r["more"] = matched - 300;
            return r;
        }

        private static JObject ChangeLayer(RhinoDoc doc, string path, JObject a)
        {
            // Resolve the material first: a bad name should fail before anything is created.
            var leaf = path.Split(new[] { "::" }, StringSplitOptions.RemoveEmptyEntries).LastOrDefault() ?? path;
            var rm = a["material"] is JToken spec && spec.Type != JTokenType.Null ? ResolveMaterial(doc, spec, leaf) : null;
            var colour = a.Value<string>("color") is string cs && cs.Trim().Length > 0
                ? ParseColor(cs) ?? throw new ToolException($"Unknown colour '{cs}' (use '#RRGGBB', 'r,g,b' or a colour name).")
                : (Color?)null;

            bool created = doc.Layers.FindByFullPath(path, -1) < 0;
            int idx = EnsureLayer(doc, path);
            var layer = doc.Layers[idx];
            var changes = new List<string>();
            var notes = new List<string>();

            if (a.Value<bool?>("visible") is bool visible) { layer.IsVisible = visible; changes.Add(visible ? "shown" : "hidden"); }
            if (a.Value<bool?>("locked") is bool locked) { layer.IsLocked = locked; changes.Add(locked ? "locked" : "unlocked"); }
            if (colour.HasValue) { layer.Color = colour.Value; changes.Add("color " + Hex(layer.Color)); }
            if (rm != null) { layer.RenderMaterial = rm; changes.Add($"material '{rm.Name}'"); }
            doc.Layers.Modify(layer, idx, true);

            if (a.Value<bool?>("current") == true)
            {
                if (doc.Layers.SetCurrentLayerIndex(idx, true)) changes.Add("current");
                else notes.Add("could not make it current (hidden or locked?)");
            }
            doc.Views.Redraw();

            var r = new JObject { ["layer"] = doc.Layers[idx].FullPath, ["objects"] = ObjectsOnLayer(doc, path).Count() };
            if (created) r["created"] = true;
            if (changes.Count > 0) r["changed"] = string.Join(", ", changes);
            if (notes.Count > 0) r["note"] = string.Join("; ", notes);
            return r;
        }

        /// <summary>A document material by name, a Rhino library material by search, or a simple {color, transparency, gloss} material.</summary>
        private static RenderMaterial ResolveMaterial(RhinoDoc doc, JToken spec, string layerName)
        {
            if (spec is JObject o)
            {
                var name = o.Value<string>("name") ?? $"{layerName} material";
                var m = new Material { Name = name };
                if (o.Value<string>("color") is string c) m.DiffuseColor = ParseColor(c) ?? throw new ToolException($"Unknown colour '{c}'.");
                if (o.Value<double?>("transparency") is double t) m.Transparency = Math.Clamp(t, 0, 1);
                if (o.Value<double?>("gloss") is double g) m.Shine = Math.Clamp(g, 0, 1) * Material.MaxShine;
                var basic = RenderMaterial.CreateBasicMaterial(m, doc);
                basic.Name = name;
                doc.RenderMaterials.Add(basic);
                return doc.RenderMaterials.LastOrDefault(x => x.Name == name) ?? basic;
            }

            var query = spec.ToString().Trim();
            var existing = doc.RenderMaterials.FirstOrDefault(x => string.Equals(x.Name, query, StringComparison.OrdinalIgnoreCase));
            if (existing != null) return existing;

            var file = FindLibraryMaterial(query);
            var stem = Path.GetFileNameWithoutExtension(file);
            existing = doc.RenderMaterials.FirstOrDefault(x => string.Equals(x.Name, stem, StringComparison.OrdinalIgnoreCase));
            if (existing != null) return existing;
            var loaded = RenderContent.LoadFromFile(file) as RenderMaterial ?? throw new ToolException($"'{file}' is not a material.");
            loaded.Name = stem;
            doc.RenderMaterials.Add(loaded);
            return doc.RenderMaterials.LastOrDefault(x => x.Name == stem) ?? loaded;
        }

        /// <summary>Rhino's material library: %APPDATA%\McNeel\Rhinoceros\N.0\Localization\*\Render Content (or the macOS equivalent).</summary>
        private static List<string> MaterialLibraryRoots()
        {
            var roots = new List<string>();
            var bases = new[]
            {
                System.Environment.GetFolderPath(System.Environment.SpecialFolder.ApplicationData),
                Path.Combine(System.Environment.GetFolderPath(System.Environment.SpecialFolder.UserProfile), "Library", "Application Support")
            };
            foreach (var b in bases)
            {
                var loc = Path.Combine(b, "McNeel", "Rhinoceros", $"{RhinoApp.ExeVersion}.0", "Localization");
                if (!Directory.Exists(loc)) continue;
                foreach (var lang in Directory.GetDirectories(loc))
                {
                    var rc = Path.Combine(lang, "Render Content");
                    if (Directory.Exists(rc)) roots.Add(rc);
                }
            }
            return roots;
        }

        private static string FindLibraryMaterial(string query)
        {
            var roots = MaterialLibraryRoots();
            var files = roots.SelectMany(r => Directory.EnumerateFiles(r, "*.rmtl", SearchOption.AllDirectories)
                                             .Select(f => (file: f, rel: Path.GetRelativePath(r, f)))).ToList();
            if (files.Count == 0) throw new ToolException($"No document material named '{query}', and no Rhino material library was found.");

            var exact = files.FirstOrDefault(f => Path.GetFileNameWithoutExtension(f.file).Equals(query, StringComparison.OrdinalIgnoreCase));
            if (exact.file != null) return exact.file;

            var words = query.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
            var hits = files.Where(f => words.All(w => f.rel.Contains(w, StringComparison.OrdinalIgnoreCase))).ToList();
            if (hits.Count == 1) return hits[0].file;
            if (hits.Count == 0) throw new ToolException($"No document or library material matches '{query}'.");
            throw new ToolException($"'{query}' matches {hits.Count} library materials: " +
                string.Join(", ", hits.Take(15).Select(h => Path.GetFileNameWithoutExtension(h.file))) + (hits.Count > 15 ? ", ..." : "") + ". Use an exact name.");
        }

        private static Color? ParseColor(string s)
        {
            s = s.Trim();
            if (s.StartsWith("#"))
            {
                var hex = s.Substring(1);
                if (hex.Length == 6 && int.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var rgb))
                    return Color.FromArgb((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF);
                if (hex.Length == 8 && uint.TryParse(hex, NumberStyles.HexNumber, CultureInfo.InvariantCulture, out var argb))
                    return Color.FromArgb((int)(argb >> 24), (int)((argb >> 16) & 0xFF), (int)((argb >> 8) & 0xFF), (int)(argb & 0xFF));
                return null;
            }
            var parts = s.Split(',');
            if (parts.Length == 3 && parts.All(p => int.TryParse(p.Trim(), out var v) && v >= 0 && v <= 255))
                return Color.FromArgb(int.Parse(parts[0].Trim()), int.Parse(parts[1].Trim()), int.Parse(parts[2].Trim()));
            var named = Color.FromName(s);
            return named.IsKnownColor ? named : null;
        }

        private static string Hex(Color c) => $"#{c.R:X2}{c.G:X2}{c.B:X2}";

        // ── view ────────────────────────────────────────────────────

        private static ToolResult View(JObject a) => ToolResult.Json(UiThread.Run(() =>
        {
            var view = RhinoDoc.ActiveDoc?.Views.ActiveView ?? throw new ToolException("No active Rhino view.");
            ApplyViewOptions(view.ActiveViewport, a);
            view.Redraw();
            return ViewSummary(view.ActiveViewport);
        }));

        private static JObject ViewSummary(RhinoViewport vp)
        {
            var r = new JObject
            {
                ["view"] = vp.Name,
                ["display"] = vp.DisplayMode?.EnglishName,
                ["projection"] = vp.IsPerspectiveProjection ? "perspective" : vp.IsParallelProjection ? "parallel" : "two-point",
                ["camera"] = Num3(vp.CameraLocation),
                ["target"] = Num3(vp.CameraTarget)
            };
            if (vp.IsPerspectiveProjection) r["lens"] = Math.Round(vp.Camera35mmLensLength, 1);
            return r;
        }

        // ── doc ─────────────────────────────────────────────────────

        private static ToolResult Doc(JObject a)
        {
            var action = (a.Value<string>("action") ?? "info").Trim().ToLowerInvariant();
            var path = a.Value<string>("path");
            return ToolResult.Json(action switch
            {
                "info" => UiThread.Run(DocInfo),
                "save" => UiThread.Run(SaveDocs),
                "save_copy" => UiThread.Run(() => SaveCopy(path, a.Value<string>("gh_path"))),
                "import" => UiThread.Run(() => ImportFile(path)),
                "open_gh" => UiThread.Run(() => OpenGh(path)),
                "plugins" => UiThread.Run(Plugins),
                _ => throw new ToolException("action must be info, save, save_copy, import, open_gh or plugins.")
            });
        }

        private static JObject DocInfo()
        {
            var r = new JObject
            {
                ["rhino"] = $"{RhinoApp.Version} | {RuntimeInformation.OSDescription.Trim()} | {RuntimeInformation.FrameworkDescription}"
            };
            var doc = RhinoDoc.ActiveDoc;
            if (doc != null)
            {
                r["doc"] = new JObject
                {
                    ["file"] = string.IsNullOrEmpty(doc.Path) ? "(never saved)" : doc.Path,
                    ["modified"] = doc.Modified,
                    ["units"] = doc.ModelUnitSystem.ToString(),
                    ["tolerance"] = doc.ModelAbsoluteTolerance,
                    ["objects"] = doc.Objects.GetObjectList(AllObjects(true)).Count(),
                    ["layers"] = doc.Layers.Count(l => !l.IsDeleted),
                    ["current_layer"] = doc.Layers.CurrentLayer?.FullPath
                };
                var sel = doc.Objects.GetSelectedObjects(false, false).ToList();
                if (sel.Count > 0)
                    r["selection"] = new JObject { ["count"] = sel.Count, ["first"] = new JArray(sel.Take(5).Select(o => ObjectRow(doc, o))) };
                if (doc.Views.ActiveView is RhinoView v) r["view"] = ViewSummary(v.ActiveViewport);
            }

            var gh = UiThread.Document;
            if (gh != null)
            {
                var active = gh.Objects.OfType<IGH_ActiveObject>().ToList();
                r["grasshopper"] = new JObject
                {
                    ["doc"] = gh.DisplayName,
                    ["file"] = gh.FilePath,
                    ["objects"] = gh.ObjectCount,
                    ["alien"] = gh.Objects.OfType<AlienComponent>().Count(),
                    ["errors"] = active.Count(o => o.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error),
                    ["warnings"] = active.Count(o => o.RuntimeMessageLevel == GH_RuntimeMessageLevel.Warning),
                    ["solver"] = gh.Enabled ? "on" : "off"
                };
            }
            else r["grasshopper"] = Instances.ActiveCanvas == null ? "editor not open" : "no document";
            r["repo"] = ScriptRepository.Root;
            return r;
        }

        private static JObject SaveDocs()
        {
            var r = new JObject();
            var doc = RequireRhino();
            r["rhino"] = string.IsNullOrEmpty(doc.Path) ? "not saved: no file yet (use save_copy with a path)"
                       : doc.Save() ? doc.Path : "save failed";
            var gh = UiThread.Document;
            r["grasshopper"] = gh == null ? "no document"
                             : string.IsNullOrEmpty(gh.FilePath) ? "not saved: no file yet (use save_copy with gh_path)"
                             : new GH_DocumentIO(gh).SaveQuiet(gh.FilePath) ? gh.FilePath : "save failed";
            return r;
        }

        private static string FullPath(string path, string defaultExt)
        {
            path = (path ?? "").Trim().Trim('"');
            if (!Path.IsPathRooted(path)) throw new ToolException($"Path must be absolute: '{path}'.");
            if (string.IsNullOrEmpty(Path.GetExtension(path))) path += defaultExt;
            var dir = Path.GetDirectoryName(path);
            if (!string.IsNullOrEmpty(dir)) Directory.CreateDirectory(dir);
            return path;
        }

        /// <summary>Write copies; the open documents keep their own file paths.</summary>
        private static JObject SaveCopy(string path, string ghPath)
        {
            if (string.IsNullOrWhiteSpace(path) && string.IsNullOrWhiteSpace(ghPath))
                throw new ToolException("Give `path` (.3dm, or another extension to export) and/or `gh_path`.");
            var r = new JObject();
            if (!string.IsNullOrWhiteSpace(path))
            {
                path = FullPath(path, ".3dm");
                var doc = RequireRhino();
                bool ok = path.EndsWith(".3dm", StringComparison.OrdinalIgnoreCase)
                    ? doc.WriteFile(path, new FileWriteOptions { SuppressDialogBoxes = true, WriteUserData = true, UpdateDocumentPath = false, SuppressAllInput = true })
                    : doc.Export(path);
                r["rhino"] = ok ? path : "write failed";
            }
            if (!string.IsNullOrWhiteSpace(ghPath))
            {
                ghPath = FullPath(ghPath, ".gh");
                var gh = UiThread.Document ?? throw new ToolException("No Grasshopper document is open.");
                var archive = new GH_IO.Serialization.GH_Archive();
                if (!archive.AppendObject(gh, "Definition")) throw new ToolException("Could not serialise the Grasshopper document.");
                r["grasshopper"] = archive.WriteToFile(ghPath, true, false) ? ghPath : "write failed";
            }
            return r;
        }

        private static JObject ImportFile(string path)
        {
            if (string.IsNullOrWhiteSpace(path)) throw new ToolException("`path` is required.");
            path = path.Trim().Trim('"');
            if (!File.Exists(path)) throw new ToolException($"File not found: {path}");
            var doc = RequireRhino();
            int before = doc.Objects.GetObjectList(AllObjects(true)).Count();
            bool ok;
            if (path.EndsWith(".3dm", StringComparison.OrdinalIgnoreCase)) ok = doc.Import(path);
            else
            {
                if (global::Rhino.Commands.Command.InCommand())
                    throw new ToolException("Rhino is busy with another command. Ask the user to finish or press Esc, then retry.");
                ok = RhinoApp.RunScript($"_-Import \"{path}\" _Enter", false);
            }
            doc.Views.Redraw();
            return new JObject { ["ok"] = ok, ["imported"] = doc.Objects.GetObjectList(AllObjects(true)).Count() - before, ["file"] = path };
        }

        private static JObject OpenGh(string path)
        {
            var gh = RhinoApp.GetPlugInObject("Grasshopper") as Grasshopper.Plugin.GH_RhinoScriptInterface
                     ?? throw new ToolException("Grasshopper is not available.");
            gh.ShowEditor();
            if (!string.IsNullOrWhiteSpace(path))
            {
                path = path.Trim().Trim('"');
                if (!File.Exists(path)) throw new ToolException($"File not found: {path}");
                if (!gh.OpenDocument(path)) throw new ToolException($"Grasshopper could not open {path}.");
            }
            else RequireDoc(true);
            var doc = UiThread.Document;
            return new JObject { ["doc"] = doc?.DisplayName, ["file"] = doc?.FilePath, ["objects"] = doc?.ObjectCount ?? 0 };
        }

        /// <summary>Third-party Rhino plug-ins (not shipped with Rhino, not McNeel) and Grasshopper add-on libraries.</summary>
        private static JObject Plugins()
        {
            var rhino = new List<string>();
            foreach (var kv in PlugIn.GetInstalledPlugIns())
            {
                PlugInInfo info;
                try { info = PlugIn.GetPlugInInfo(kv.Key); } catch { continue; }
                if (info == null || info.ShipsWithRhino || (info.Organization ?? "").Contains("McNeel", StringComparison.OrdinalIgnoreCase)) continue;
                var name = string.IsNullOrWhiteSpace(info.Name) ? kv.Value : info.Name;
                rhino.Add($"{name} {info.Version}{(info.IsLoaded ? "" : " (not loaded)")}");
            }

            var counts = Instances.ComponentServer.ObjectProxies.GroupBy(p => p.LibraryGuid).ToDictionary(g => g.Key, g => g.Count());
            var ghLibs = new List<string>();
            foreach (var lib in Instances.ComponentServer.Libraries)
            {
                if (lib.IsCoreLibrary) continue;
                counts.TryGetValue(lib.Id, out int n);
                var name = string.IsNullOrWhiteSpace(lib.Name) ? Path.GetFileNameWithoutExtension(lib.Location ?? "") : lib.Name.Trim();
                var parts = new[] { name, lib.Version?.Trim(), n > 0 ? $"({n} components)" : null };
                ghLibs.Add(string.Join(" ", parts.Where(p => !string.IsNullOrEmpty(p))));
            }
            return new JObject
            {
                ["rhino_plugins"] = new JArray(rhino.OrderBy(s => s, StringComparer.OrdinalIgnoreCase)),
                ["grasshopper_libraries"] = new JArray(ghLibs.OrderBy(s => s, StringComparer.OrdinalIgnoreCase))
            };
        }

        // ── search (component library) ──────────────────────────────

        private static ToolResult Search(JObject a)
        {
            var query = Req(a, "query").Trim();
            var category = a.Value<string>("category");
            int limit = Math.Clamp(a.Value<int?>("limit") ?? 15, 1, 100);
            bool detail = a.Value<bool?>("detail") ?? false;
            var words = query.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);

            return ToolResult.Json(UiThread.Run(() =>
            {
                static bool Has(string s, string w) => s != null && s.Contains(w, StringComparison.OrdinalIgnoreCase);
                var hits = Instances.ComponentServer.ObjectProxies
                    .Where(p => !p.Obsolete && p.Exposure != GH_Exposure.hidden)
                    .Where(p => string.IsNullOrWhiteSpace(category) || Has(p.Desc.Category, category) || Has($"{p.Desc.Category}/{p.Desc.SubCategory}", category))
                    .Where(p => words.All(w => Has(p.Desc.Name, w) || Has(p.Desc.NickName, w) || Has(p.Desc.Description, w)
                                               || Has(p.Desc.Category, w) || Has(p.Desc.SubCategory, w)))
                    .Select(p => (p, rank: p.Desc.Name.Equals(query, StringComparison.OrdinalIgnoreCase) ? 0 : words.All(w => Has(p.Desc.Name, w)) ? 1 : 2))
                    .OrderBy(h => h.rank)
                    .ThenBy(h => (int)h.p.Exposure & ~(int)GH_Exposure.obscure)
                    .ThenBy(h => h.p.Desc.Name, StringComparer.OrdinalIgnoreCase)
                    .Select(h => h.p).ToList();

                var r = new JObject
                {
                    ["count"] = hits.Count,
                    ["results"] = new JArray(hits.Take(limit).Select(p =>
                        $"{p.Desc.Name} [{p.Desc.NickName}] ({p.Desc.Category}/{p.Desc.SubCategory}) {p.Guid.ToString("N").Substring(0, 8)}"))
                };
                if (hits.Count > 0 && (detail || hits.Count == 1)) r["detail"] = DescribeProxy(hits[0]);
                return r;
            }));
        }

        private static JObject DescribeProxy(IGH_ObjectProxy p)
        {
            static string Pin(IGH_Param q) =>
                $"{q.NickName} {q.Name}: {q.TypeName}{(q.Access == GH_ParamAccess.item ? "" : " " + q.Access)}{(q.Optional ? " opt" : "")}";
            var r = new JObject { ["name"] = p.Desc.Name, ["description"] = Trim(p.Desc.Description, 200) };
            var obj = p.CreateInstance();
            if (obj is IGH_Component c)
            {
                r["in"] = new JArray(c.Params.Input.Select(Pin));
                r["out"] = new JArray(c.Params.Output.Select(Pin));
            }
            else if (obj is IGH_Param q) r["param"] = Pin(q);
            return r;
        }

        // ── command help (docs.mcneel.com) ──────────────────────────

        private static readonly HttpClient Http = new HttpClient { Timeout = TimeSpan.FromSeconds(15) };

        private static string CommandHelp(string name)
        {
            if (!UiThread.Run(() => global::Rhino.Commands.Command.IsCommand(name)))
                throw new ToolException($"'{name}' is not a Rhino command. Try rhino(find=...).");
            var url = $"https://docs.mcneel.com/rhino/{RhinoApp.ExeVersion}/help/en-us/commands/{WebUtility.UrlEncode(name.ToLowerInvariant())}.htm";
            HttpResponseMessage resp;
            try { resp = Http.GetAsync(url).GetAwaiter().GetResult(); }
            catch (Exception ex) { throw new ToolException($"Could not reach {url}: {ex.Message}"); }
            using (resp)
            {
                if (resp.StatusCode == HttpStatusCode.NotFound || resp.StatusCode == HttpStatusCode.Forbidden)
                    throw new ToolException($"No help page for '{name}' on docs.mcneel.com (plug-in commands are documented by their vendor).");
                if (!resp.IsSuccessStatusCode) throw new ToolException($"{url} returned {(int)resp.StatusCode}.");
                var html = resp.Content.ReadAsStringAsync().GetAwaiter().GetResult();
                return $"{name} — {url}\n\n{Trim(HtmlToText(html), 4000)}";
            }
        }

        private static string HtmlToText(string html)
        {
            const RegexOptions o = RegexOptions.Singleline | RegexOptions.IgnoreCase;
            var m = Regex.Match(html, "<div\\b[^>]*\\bid=\"mc-main-content\"[^>]*>(?<body>.*)", o);
            var body = m.Success ? m.Groups["body"].Value : html;
            body = Regex.Replace(body, @"<(script|style)\b[^>]*>.*?</\1>", " ", o);
            body = Regex.Replace(body, @"</(p|div|li|tr|h[1-6])\s*>|<br\s*/?>", "\n", RegexOptions.IgnoreCase);
            body = Regex.Replace(body, @"<[^>]+>", " ", o);
            body = WebUtility.HtmlDecode(body);
            body = Regex.Replace(body, @"[ \t\r]+", " ");
            body = Regex.Replace(body, @" *\n *", "\n");
            return Regex.Replace(body.Trim(), @"\n{3,}", "\n\n");
        }
    }
}
