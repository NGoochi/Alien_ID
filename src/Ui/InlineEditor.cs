using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Threading;
using System.Threading.Tasks;
using System.Windows.Forms;

using Grasshopper.GUI.Canvas;

namespace Alien.Ui
{
    /// <summary>
    /// A borderless text box over a field on a node, styled like the node (Grasshopper's own editor brings a ✓/✗ strip).
    /// It grows with its text, so long values stay readable while typing. Enter, clicking anywhere else (another field
    /// included) or scrolling the canvas commits; Escape cancels. One at a time. With a suggestion provider, a list
    /// drawn on the canvas under the box follows the typing (arrow keys + Enter, or a click, pick one). It is painted
    /// rather than a ListBox because Rhino for Mac's WinForms ListBox keeps a zero size.
    /// </summary>
    internal static class InlineEditor
    {
        private const float RowH = 13f;   // canvas units, like the node's rows
        private const float ListW = 230f;
        private const int MaxWidth = 460;  // screen px the box may grow to (or the canvas edge)

        private static TextBox _box;
        private static GH_Canvas _canvas;
        private static Action<string> _accept;
        private static bool _closing;
        private static System.Windows.Forms.Timer _debounce;
        private static CancellationTokenSource _pending;
        private static RectangleF _anchor;   // the field, in canvas coordinates
        private static int _fieldWidth;      // the field's width on screen: the box never gets narrower
        private static int _highlight = -1;
        private static GH_Canvas _hooked;
        private static DateTime _pickedAt;
        private static PointF _pickedAtPoint;

        /// <summary>The suggestions on show (also read by tests); empty when none.</summary>
        public static string[] Suggestions { get; private set; } = Array.Empty<string>();

        public static bool IsOpen => _box != null;

        /// <param name="area">The field, in canvas coordinates.</param>
        public static void Show(GH_Canvas canvas, RectangleF area, string text, Action<string> accept, string family, float size = 7.5f,
            Func<string, CancellationToken, Task<string[]>> suggest = null)
        {
            if (canvas == null) return;
            // Another field still open: keep what was typed there. (It used to be dropped when the next field opened.)
            Close(true);
            Hook(canvas);

            var theme = Theme.Current;
            var a = canvas.Viewport.ProjectPoint(new PointF(area.Left + 4, area.Top));
            var b = canvas.Viewport.ProjectPoint(new PointF(area.Right - 4, area.Bottom));
            var box = new TextBox
            {
                BorderStyle = BorderStyle.None,
                BackColor = theme.Field,
                ForeColor = theme.Text,
                Font = new Font(family, Math.Max(6f, size * canvas.Viewport.Zoom)),
                Text = text ?? "",
            };
            int h = Math.Min((int)(b.Y - a.Y), box.PreferredHeight);
            _fieldWidth = Math.Max(20, (int)(b.X - a.X));
            box.SetBounds((int)a.X, (int)(a.Y + (b.Y - a.Y - h) / 2), _fieldWidth, h);
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
            // Deferred, so a click on the suggestion list (handled in the same mouse event) wins over committing the
            // typing. Only for this box: by then another field may have opened, and it must stay open.
            box.LostFocus += (s, e) => canvas.BeginInvoke(new Action(() => { if (_box == box) Close(true); }));
            box.TextChanged += (s, e) => Grow();

            _box = box;
            _canvas = canvas;
            _accept = accept;
            _anchor = area;
            canvas.Controls.Add(box);
            Grow();

            if (suggest != null)
            {
                _debounce = new System.Windows.Forms.Timer { Interval = 220 };
                _debounce.Tick += (s, e) => { _debounce.Stop(); Lookup(suggest, box.Text); };
                box.TextChanged += (s, e) => { _debounce.Stop(); _debounce.Start(); };
            }

            box.Focus();
            box.SelectAll();
        }

        private static void Hook(GH_Canvas canvas)
        {
            if (_hooked == canvas) return;
            canvas.CanvasPostPaintObjects += Paint;
            canvas.MouseDown += OnCanvasMouseDown;
            canvas.MouseWheel += (s, e) => Close(true); // the box doesn't follow pan and zoom: commit instead
            _hooked = canvas;
        }

        /// <summary>Widen the box to fit its text: from the field's width up to MaxWidth, or the canvas edge.</summary>
        private static void Grow()
        {
            if (_box == null || _canvas == null) return;
            float textW;
            using (var bmp = new Bitmap(1, 1))
            using (var g = Graphics.FromImage(bmp))
                textW = g.MeasureString(_box.Text + "MM", _box.Font).Width;
            int limit = Math.Max(_fieldWidth, Math.Min(MaxWidth, _canvas.ClientSize.Width - _box.Left - 8));
            int w = Math.Max(_fieldWidth, Math.Min(limit, (int)Math.Ceiling(textW)));
            if (w == _box.Width) return;
            _box.Width = w;
            _canvas.Refresh();
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

        /// <summary>The box (grown or not) and the field under it, in canvas coordinates.</summary>
        private static RectangleF Frame()
        {
            if (_box == null || _canvas == null) return _anchor;
            var tl = _canvas.Viewport.UnprojectPoint(new PointF(_box.Left, _box.Top));
            var br = _canvas.Viewport.UnprojectPoint(new PointF(_box.Right, _box.Bottom));
            var box = RectangleF.FromLTRB(tl.X - 4, Math.Min(tl.Y, _anchor.Top), br.X + 4, Math.Max(br.Y, _anchor.Bottom));
            return RectangleF.Union(_anchor, box);
        }

        private static RectangleF Row(int i)
        {
            var f = Frame();
            return new RectangleF(f.X, f.Bottom + 2 + i * RowH, Math.Max(f.Width, ListW), RowH);
        }

        private static void Paint(GH_Canvas canvas)
        {
            if (_box == null || canvas != _canvas) return;
            var theme = Theme.Current;
            var g = canvas.Graphics;
            var smoothing = g.SmoothingMode;
            g.SmoothingMode = SmoothingMode.AntiAlias;

            // The field being edited, at the box's grown size, outlined in the accent
            var f = Frame();
            using (var fb = new SolidBrush(theme.Field)) g.FillRectangle(fb, f);
            using (var edge = new Pen(theme.Accent, 1f)) g.DrawRectangle(edge, f.X, f.Y, f.Width, f.Height);

            if (Suggestions.Length > 0)
            {
                var all = RectangleF.Union(Row(0), Row(Suggestions.Length - 1));
                using (var bg = new SolidBrush(Color.FromArgb(245, theme.Header))) g.FillRectangle(bg, RectangleF.Inflate(all, 2, 2));
                using (var edge = new Pen(theme.Accent, 0.8f)) g.DrawRectangle(edge, all.X - 2, all.Y - 2, all.Width + 4, all.Height + 4);
                using var font = new Font(AlienAttributes.Family, 7f);
                using var text = new SolidBrush(theme.Text);
                var fmt = new StringFormat { LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap };
                for (int i = 0; i < Suggestions.Length; i++)
                {
                    var r = Row(i);
                    if (i == _highlight)
                        using (var hb = new SolidBrush(Color.FromArgb(70, theme.Accent))) g.FillRectangle(hb, r);
                    g.DrawString(Suggestions[i], font, text, new RectangleF(r.X + 4, r.Y, r.Width - 6, r.Height), fmt);
                }
            }
            g.SmoothingMode = smoothing;
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
                // Stamped after the commit: it can solve for seconds (a map refetching), and the same click's second
                // handler runs only after that.
                _pickedAt = DateTime.UtcNow;
                _pickedAtPoint = pt;
                return true;
            }
            return false;
        }

        /// <summary>
        /// True when this click already picked a suggestion (the canvas and the node both hear one click, in either
        /// order), so the node must not also treat it as a click on whatever control lies under the list.
        /// </summary>
        internal static bool Consumed(PointF pt) =>
            (DateTime.UtcNow - _pickedAt).TotalMilliseconds < 400 && Math.Abs(pt.X - _pickedAtPoint.X) < 2 && Math.Abs(pt.Y - _pickedAtPoint.Y) < 2;

        // A click on the list picks; a click anywhere else outside the box commits the typing. (The list can stick out
        // past the node, so those clicks reach the canvas, not the node.)
        private static void OnCanvasMouseDown(object sender, MouseEventArgs e)
        {
            if (_box == null || _canvas == null) return;
            var pt = _canvas.Viewport.UnprojectPoint(e.Location);
            if (e.Button == MouseButtons.Left && HandleClick(pt)) return;
            if (!Frame().Contains(pt)) Close(true);
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
