using System;
using System.Collections.Generic;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Text.RegularExpressions;
using System.Windows.Forms;

using Grasshopper.GUI.Canvas;

using Newtonsoft.Json.Linq;

using Alien.Scripting;

namespace Alien.Ui
{
    /// <summary>
    /// Image inputs (header flag <c>image</c> on a str): drop an image file or a link onto the node and the field takes
    /// its path or URL; the field shows a thumbnail. Drops anywhere else on the canvas are left to Grasshopper.
    /// </summary>
    internal static class ImageDrop
    {
        private static readonly string[] Extensions = { ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic" };
        private static readonly Dictionary<string, (DateTime stamp, Bitmap thumb)> Thumbs = new Dictionary<string, (DateTime, Bitmap)>();
        private static GH_Canvas _hooked;

        /// <summary>Listen for drops on the canvas (once per canvas).</summary>
        public static void Attach(GH_Canvas canvas)
        {
            if (canvas == null || canvas == _hooked) return;
            _hooked = canvas;
            canvas.AllowDrop = true;
            canvas.DragEnter += OnDrag;
            canvas.DragOver += OnDrag;
            canvas.DragDrop += OnDrop;
        }

        private static void OnDrag(object sender, DragEventArgs e)
        {
            var canvas = sender as GH_Canvas;
            if (Payload(e.Data) != null && Target(canvas, e.X, e.Y).node != null) e.Effect = DragDropEffects.Copy;
        }

        private static void OnDrop(object sender, DragEventArgs e)
        {
            var canvas = sender as GH_Canvas;
            var value = Payload(e.Data);
            var (node, def) = Target(canvas, e.X, e.Y);
            if (value == null || node == null) return;
            Drop(node, def, value);
            e.Effect = DragDropEffects.Copy;
        }

        /// <summary>Put a dropped image (path or URL) into an image input; undoable like typing it.</summary>
        internal static void Drop(AlienComponent node, ParamDef def, string value)
        {
            node.SetInput(def.Name, new JValue(value));
            Grasshopper.Instances.RedrawCanvas();
        }

        /// <summary>The Alien node (topmost) and image input under a screen point.</summary>
        internal static (AlienComponent node, ParamDef def) Target(GH_Canvas canvas, int screenX, int screenY)
        {
            if (canvas?.Document == null) return (null, null);
            var pt = canvas.Viewport.UnprojectPoint(canvas.PointToClient(new Point(screenX, screenY)));
            foreach (var node in canvas.Document.Objects.OfType<AlienComponent>().Reverse())
            {
                if (node.Attributes == null || !node.Attributes.Bounds.Contains(pt)) continue;
                var def = (node.Attributes as AlienAttributes)?.ImageInputAt(pt)
                          ?? node.Header.Inputs.FirstOrDefault(d => d.Image && node.Params.Input.Any(p => p.Name == d.Name && p.SourceCount == 0));
                return def != null ? (node, def) : (null, null);
            }
            return (null, null);
        }

        /// <summary>An image file path, or an http(s) link, from what is being dragged; null otherwise.</summary>
        internal static string Payload(IDataObject data)
        {
            if (data == null) return null;
            if (data.GetDataPresent(DataFormats.FileDrop) && data.GetData(DataFormats.FileDrop) is string[] files)
                return files.FirstOrDefault(IsImageFile);
            foreach (var format in new[] { DataFormats.UnicodeText, DataFormats.Text, "UniformResourceLocatorW", "UniformResourceLocator" })
            {
                if (!data.GetDataPresent(format)) continue;
                var text = (data.GetData(format) as string ?? "").Trim().Split('\n')[0].Trim();
                if (Regex.IsMatch(text, @"^https?://\S+$", RegexOptions.IgnoreCase)) return text;
                if (text.StartsWith("file://")) text = Uri.UnescapeDataString(new Uri(text).LocalPath);
                if (IsImageFile(text)) return text;
            }
            return null;
        }

        private static bool IsImageFile(string path) =>
            !string.IsNullOrWhiteSpace(path) && Extensions.Contains(Path.GetExtension(path).ToLowerInvariant()) && File.Exists(path);

        /// <summary>A small cached thumbnail of a local image file, or null (URLs, missing files, unreadable images).</summary>
        public static Bitmap Thumbnail(string path)
        {
            if (string.IsNullOrWhiteSpace(path) || path.StartsWith("http", StringComparison.OrdinalIgnoreCase)) return null;
            path = path.Trim().Trim('"');
            if (!File.Exists(path)) return null;
            var stamp = File.GetLastWriteTimeUtc(path);
            if (Thumbs.TryGetValue(path, out var t) && t.stamp == stamp) return t.thumb;
            Bitmap thumb = null;
            try
            {
                using var src = Image.FromFile(path);
                float k = 64f / Math.Max(src.Width, src.Height);
                thumb = new Bitmap(src, Math.Max(1, (int)(src.Width * k)), Math.Max(1, (int)(src.Height * k)));
            }
            catch { /* unreadable here (e.g. HEIC): remembered as no thumbnail, so it isn't retried every repaint */ }
            if (Thumbs.TryGetValue(path, out var old)) old.thumb?.Dispose();
            Thumbs[path] = (stamp, thumb);
            return thumb;
        }
    }
}
