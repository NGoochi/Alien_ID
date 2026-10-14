using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;

using Newtonsoft.Json.Linq;

using Rhino;
using Rhino.DocObjects;
using Rhino.Geometry;
using Rhino.Render;

namespace Alien
{
    /// <summary>
    /// What suite nodes generate, kept for reuse: &lt;repository&gt;/.alien/assets/&lt;kind&gt;/&lt;id&gt;/asset.json plus a
    /// thumbnail and files (geometry.3dm in metres, texture.png, drawing images). Written by suite/alien_assets.py;
    /// listed in the panel, baked into the document as blocks, or turned into render materials.
    /// </summary>
    public static class Assets
    {
        private static readonly Regex SafeName = new Regex("^[a-z0-9_-]{1,40}$");
        private static readonly Dictionary<string, (DateTime stamp, string uri)> Thumbs = new Dictionary<string, (DateTime, string)>();

        public static string Root => ScriptRepository.Root == null ? null : Path.Combine(ScriptRepository.Root, ".alien", "assets");

        /// <summary>The asset's folder, or null. Kind and id come from the page, so they must be plain names (no paths).</summary>
        public static string Folder(string kind, string id)
        {
            if (Root == null || kind == null || id == null || !SafeName.IsMatch(kind) || !SafeName.IsMatch(id)) return null;
            var dir = Path.Combine(Root, kind, id);
            return File.Exists(Path.Combine(dir, "asset.json")) ? dir : null;
        }

        public static JObject Meta(string folder)
        {
            try { return JObject.Parse(File.ReadAllText(Path.Combine(folder, "asset.json"))); }
            catch { return null; }
        }

        /// <summary>
        /// Newest first (up to 200), with thumbnails as data URIs (cached until the file changes), and a signature that
        /// changes when any asset does, so the panel resends them only then.
        /// </summary>
        public static JArray List(out string signature)
        {
            var arr = new JArray();
            signature = "";
            var root = Root;
            if (root == null || !Directory.Exists(root)) return arr;
            var folders = Directory.GetDirectories(root).SelectMany(Directory.GetDirectories)
                .Where(d => File.Exists(Path.Combine(d, "asset.json")))
                .Select(d => (dir: d, stamp: File.GetLastWriteTimeUtc(Path.Combine(d, "asset.json"))))
                .OrderByDescending(x => x.stamp).Take(200).ToList();
            signature = string.Join("|", folders.Select(f => f.dir + f.stamp.Ticks));
            foreach (var (dir, _) in folders)
            {
                var m = Meta(dir);
                if (m == null) continue;
                var files = m["files"] as JObject;
                arr.Add(new JObject
                {
                    ["id"] = Path.GetFileName(dir),
                    ["kind"] = Path.GetFileName(Path.GetDirectoryName(dir)),
                    ["name"] = m.Value<string>("name"),
                    ["prompt"] = m.Value<string>("prompt"),
                    ["node"] = m.Value<string>("node"),
                    ["created"] = m.Value<string>("created"),
                    ["size"] = m.Value<string>("size") ?? (m["size_m"] != null ? $"{m.Value<double>("size_m"):0.##} m tile" : null),
                    ["materials"] = m["materials"],
                    ["hasGeometry"] = files?["geometry"] != null,
                    ["hasTexture"] = files?["texture"] != null,
                    ["hasRecord"] = files?["record"] != null,
                    ["canPlace"] = m["inputs"] is JObject && m.Value<string>("node") != null,
                    ["thumb"] = Thumb(Path.Combine(dir, "thumb.png")),
                });
            }
            return arr;
        }

        private static string Thumb(string path)
        {
            if (!File.Exists(path)) return null;
            var stamp = File.GetLastWriteTimeUtc(path);
            if (Thumbs.TryGetValue(path, out var t) && t.stamp == stamp) return t.uri;
            var uri = "data:image/png;base64," + Convert.ToBase64String(File.ReadAllBytes(path));
            Thumbs[path] = (stamp, uri);
            return uri;
        }

        /// <summary>
        /// Put an object asset into the document as a block (defined once per asset, scaled from metres to the model's
        /// units) and insert one at the origin, selected, on the "Alien assets" layer. Returns a message for the panel.
        /// </summary>
        public static string Bake(RhinoDoc doc, string kind, string id)
        {
            var dir = Folder(kind, id);
            var path = dir == null ? null : Path.Combine(dir, "geometry.3dm");
            if (path == null || !File.Exists(path)) return "This asset has no geometry.";
            var meta = Meta(dir);
            var name = "Alien · " + Clean(meta?.Value<string>("name") ?? id) + " · " + id.Substring(0, Math.Min(6, id.Length));
            var layer = AssetLayer(doc);

            var def = doc.InstanceDefinitions.Find(name);
            if (def == null)
            {
                var model = Rhino.FileIO.File3dm.Read(path);
                if (model == null) return "Could not read geometry.3dm.";
                double s = RhinoMath.UnitScale(model.Settings.ModelUnitSystem == UnitSystem.None ? UnitSystem.Meters : model.Settings.ModelUnitSystem, doc.ModelUnitSystem);
                var scale = Transform.Scale(Point3d.Origin, s);
                var geometry = new List<GeometryBase>();
                var attributes = new List<ObjectAttributes>();
                foreach (var o in model.Objects)
                {
                    var g = o.Geometry?.Duplicate();
                    if (g == null) continue;
                    g.Transform(scale);
                    var a = o.Attributes.Duplicate();
                    a.LayerIndex = layer;
                    geometry.Add(g);
                    attributes.Add(a);
                }
                if (geometry.Count == 0) return "geometry.3dm is empty.";
                int index = doc.InstanceDefinitions.Add(name, meta?.Value<string>("prompt") ?? "", Point3d.Origin, geometry, attributes);
                if (index < 0) return "Rhino refused the block definition.";
                def = doc.InstanceDefinitions[index];
            }
            var attrs = new ObjectAttributes { LayerIndex = layer, Name = meta?.Value<string>("name") };
            var placed = doc.Objects.AddInstanceObject(def.Index, Transform.Identity, attrs);
            doc.Objects.UnselectAll();
            doc.Objects.Select(placed);
            doc.Views.Redraw();
            return $"Placed block '{name}' at the origin (selected, on the Alien assets layer).";
        }

        /// <summary>
        /// Make a texture asset a PBR render material (base colour + bump), mapped in world coordinates at its real
        /// size, and apply it to the selected objects if any. Returns a message for the panel.
        /// </summary>
        public static string AddMaterial(RhinoDoc doc, string kind, string id)
        {
            var dir = Folder(kind, id);
            var texture = dir == null ? null : Path.Combine(dir, "texture.png");
            if (texture == null || !File.Exists(texture)) return "This asset has no texture.";
            var meta = Meta(dir);
            var name = Clean(meta?.Value<string>("name") ?? id);
            name = name.Length > 0 ? char.ToUpperInvariant(name[0]) + name.Substring(1) : id;
            double sizeM = meta?.Value<double?>("size_m") ?? 1.0;

            var tex = new Texture { FileReference = Rhino.FileIO.FileReference.CreateFromFullPath(texture) };
            var mat = new Material { Name = name };
            mat.ToPhysicallyBased();
            mat.PhysicallyBased.SetTexture(tex, TextureType.PBR_BaseColor);
            mat.PhysicallyBased.SetTexture(tex, TextureType.Bump);
            var rm = RenderMaterial.FromMaterial(mat, doc);
            rm.Name = name;
            foreach (var old in doc.RenderMaterials.Where(m => m.Name == name).ToList()) doc.RenderMaterials.Remove(old);
            doc.RenderMaterials.Add(rm);
            double tile = sizeM * RhinoMath.UnitScale(UnitSystem.Meters, doc.ModelUnitSystem);
            for (var child = rm.FirstChild; child != null; child = child.NextSibling)
            {
                if (!(child is RenderTexture rt)) continue;
                rt.BeginChange(RenderContent.ChangeContexts.Program);
                rt.SetProjectionMode(TextureProjectionMode.WcsBox, RenderContent.ChangeContexts.Program);
                rt.SetRepeat(new Vector3d(1 / tile, 1 / tile, 1 / tile), RenderContent.ChangeContexts.Program);
                rt.EndChange();
            }

            var selected = doc.Objects.GetSelectedObjects(false, false).ToList();
            foreach (var o in selected)
            {
                o.RenderMaterial = rm;
                o.CommitChanges();
            }
            doc.Views.Redraw();
            return $"Added material '{name}' (one tile = {sizeM:0.##} m)" +
                   (selected.Count > 0 ? $" and applied it to {selected.Count} selected object{(selected.Count == 1 ? "" : "s")}." : ". Select objects first to apply it.");
        }

        /// <summary>Take an asset out of the library: its folder moves to .alien/trash (recoverable, never deleted).</summary>
        public static string Remove(string kind, string id)
        {
            var dir = Folder(kind, id);
            if (dir == null) return "That asset is already gone.";
            var name = Meta(dir)?.Value<string>("name") ?? id;
            var trash = Path.Combine(ScriptRepository.Root, ".alien", "trash", "assets");
            Directory.CreateDirectory(trash);
            Directory.Move(dir, Path.Combine(trash, $"{kind}-{id}-{DateTime.Now:yyyyMMdd-HHmmss}"));
            return $"Removed '{name}' from the library (moved to .alien/trash/assets).";
        }

        /// <summary>The inputs that made an asset (asset.json "inputs") and the suite node that makes it, for placing that node.</summary>
        public static (string script, Dictionary<string, JToken> inputs) Recipe(string kind, string id)
        {
            var dir = Folder(kind, id);
            var meta = dir == null ? null : Meta(dir);
            var node = meta?.Value<string>("node");
            if (!(meta?["inputs"] is JObject inputs) || node == null || !SafeName.IsMatch(node)) return (null, null);
            return ("suite/" + node + ".py", inputs.Properties().ToDictionary(p => p.Name, p => p.Value));
        }

        private static int AssetLayer(RhinoDoc doc)
        {
            int i = doc.Layers.FindByFullPath("Alien assets", -1);
            return i >= 0 ? i : doc.Layers.Add(new Layer { Name = "Alien assets" });
        }

        private static string Clean(string s) => Regex.Replace(s ?? "", @"[^\w\s\-\.,()&']", "").Trim();
    }
}
