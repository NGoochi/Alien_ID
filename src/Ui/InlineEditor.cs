using System;
using System.Drawing;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

using Grasshopper.GUI.Canvas;

namespace Alien.Ui
{
    /// <summary>
    /// A borderless text box over a field on a node, styled like the node (Grasshopper's own editor brings a ✓/✗ strip).
    /// Enter or clicking away commits, Escape cancels. One at a time. With a suggestion provider, a list drawn on the
    /// canvas under the box follows the typing (arrow keys + Enter, or a click, pick one). It is painted rather than a
    /// ListBox because Rhino for Mac's WinForms ListBox keeps a zero size.
    /// </summary>
    internal static class InlineEditor
    {
        private static readonly Color Back = Color.FromArgb(46, 46, 46);
        private static readonly Color Fore = Color.FromArgb(230, 230, 230);
        private const float RowH = 13f;   // canvas units, like the node's rows
        private const float ListW = 230f;

        private static TextBox _box;
        private static GH_Canvas _canvas;
        private static Action<string> _accept;
        private static bool _closing;
        private static System.Windows.Forms.Timer _debounce;
        private static CancellationTokenSource _pending;
        private static RectangleF _anchor;   // the field, in canvas coordinates
        private static int _highlight = -1;
        private static GH_Canvas _hooked;

        /// <summary>The suggestions on show (also read by tests); empty when none.</summary>
        public static string[] Suggestions { get; private set; } = Array.Empty<string>();

        /// <param name="area">The field, in canvas coordinates.</param>
        public static void Show(GH_Canvas canvas, RectangleF area, string text, Action<string> accept, string family, float size = 7.5f,
            Func<string, CancellationToken, Task<string[]>> suggest = null)
        {
            if (canvas == null) return;
            Close(false);

            var a = canvas.Viewport.ProjectPoint(new PointF(area.Left + 4, area.Top));
            var b = canvas.Viewport.ProjectPoint(new PointF(area.Right - 4, area.Bottom));
            var box = new TextBox
            {
                BorderStyle = BorderStyle.None,
                BackColor = Back,
                ForeColor = Fore,
                Font = new Font(family, Math.Max(6f, size * canvas.Viewport.Zoom)),
                Text = text ?? "",
            };
            int h = Math.Min((int)(b.Y - a.Y), box.PreferredHeight);
            box.SetBounds((int)a.X, (int)(a.Y + (b.Y - a.Y - h) / 2), Math.Max(20, (int)(b.X - a.X)), h);
            box.KeyDown += (s, e) =>
            {
                if (Suggestions.Length > 0 && (e.KeyCode == Keys.Down || e.KeyCode == Keys.Up))
                {
                    e.SuppressKeyPress = true;
                    _highlight = Math.Max(-1, Math.Min(Suggestions.Length - 1, _highlight + (e.KeyCode == Keys.Down ? 1 : -1)));
                    canvas.Refresh();
                }
                else if (e.KeyCode == Keys.Enter)
                {
                    e.SuppressKeyPress = true;
                    if (_highlight >= 0 && _highlight < Suggestions.Length) box.Text = Suggestions[_highlight];
                    Close(true);
                }
                else if (e.KeyCode == Keys.Escape) { e.SuppressKeyPress = true; Close(false); }
            };
            // Deferred, so a click on the suggestion list (handled in the same mouse event) wins over committing the typing.
            box.LostFocus += (s, e) => canvas.BeginInvoke(new Action(() => Close(true)));

            _box = box;
            _canvas = canvas;
            _accept = accept;
            _anchor = area;
            canvas.Controls.Add(box);

            if (suggest != null)
            {
                if (_hooked != canvas)
                {
                    canvas.CanvasPostPaintObjects += PaintSuggestions;
                    canvas.MouseDown += OnCanvasMouseDown;
                    _hooked = canvas;
                }
                _debounce = new System.Windows.Forms.Timer { Interval = 220 };
                _debounce.Tick += (s, e) => { _debounce.Stop(); Lookup(suggest, box.Text); };
                box.TextChanged += (s, e) => { _debounce.Stop(); _debounce.Start(); };
            }

            box.Focus();
            box.SelectAll();
        }

        private static void Lookup(Func<string, CancellationToken, Task<string[]>> suggest, string text)
        {
            _pending?.Cancel();
            var cts = _pending = new CancellationTokenSource();
            var canvas = _canvas;
            Task.Run(async () =>
            {
                string[] found;
                try { found = await suggest(text, cts.Token).ConfigureAwait(false); }
                catch { found = Array.Empty<string>(); }
                if (cts.IsCancellationRequested || canvas == null) return;
                canvas.BeginInvoke(new Action(() =>
                {
                    if (_box == null || _box.Text != text) return; // typed on since: a newer lookup follows
                    Suggestions = found ?? Array.Empty<string>();
                    _highlight = -1;
                    canvas.Refresh();
                }));
            });
        }

        private static RectangleF Row(int i) => new RectangleF(_anchor.X, _anchor.Bottom + 2 + i * RowH, Math.Max(_anchor.Width, ListW), RowH);

        private static void PaintSuggestions(GH_Canvas canvas)
        {
            if (_box == null || Suggestions.Length == 0) return;
            var g = canvas.Graphics;
            var all = RectangleF.Union(Row(0), Row(Suggestions.Length - 1));
            using (var bg = new SolidBrush(Color.FromArgb(245, 30, 30, 30))) g.FillRectangle(bg, RectangleF.Inflate(all, 2, 2));
            using (var edge = new Pen(Color.FromArgb(0, 200, 83), 0.8f)) g.DrawRectangle(edge, all.X - 2, all.Y - 2, all.Width + 4, all.Height + 4);
            using var font = new Font(AlienAttributes.Family, 7f);
            using var text = new SolidBrush(Fore);
            var fmt = new StringFormat { LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap };
            for (int i = 0; i < Suggestions.Length; i++)
            {
                var r = Row(i);
                if (i == _highlight)
                    using (var hb = new SolidBrush(Color.FromArgb(70, 0, 200, 83))) g.FillRectangle(hb, r);
                g.DrawString(Suggestions[i], font, text, new RectangleF(r.X + 4, r.Y, r.Width - 6, r.Height), fmt);
            }
        }

        /// <summary>A click (canvas coordinates) on the suggestion list picks that suggestion. True when it did.</summary>
        internal static bool HandleClick(PointF pt)
        {
            if (_box == null) return false;
            for (int i = 0; i < Suggestions.Length; i++)
            {
                if (!Row(i).Contains(pt)) continue;
                _box.Text = Suggestions[i];
                Close(true);
                return true;
            }
            return false;
        }

        // The list can stick out past the node; clicks there reach the canvas, not the node.
        private static void OnCanvasMouseDown(object sender, MouseEventArgs e)
        {
            if (_box == null || _canvas == null || e.Button != MouseButtons.Left) return;
            HandleClick(_canvas.Viewport.UnprojectPoint(e.Location));
        }

        /// <summary>Remove the box; with commit, hand its text to the caller.</summary>
        public static void Close(bool commit)
        {
            if (_box == null || _closing) return;
            _closing = true;
            try
            {
                var box = _box;
                var accept = _accept;
                var text = box.Text;
                _pending?.Cancel();
                _debounce?.Stop();
                _debounce?.Dispose();
                _debounce = null;
                _box = null;
                _accept = null;
                Suggestions = Array.Empty<string>();
                _highlight = -1;
                _canvas?.Controls.Remove(box);
                box.Dispose();
                _canvas?.Focus();
                _canvas?.Refresh();
                if (commit) accept?.Invoke(text);
            }
            finally { _closing = false; }
        }
    }
}
