using System;
using System.Drawing;
using System.Windows.Forms;

using Grasshopper.GUI.Canvas;

namespace Alien.Ui
{
    /// <summary>
    /// A borderless text box over a field on a node, styled like the node (Grasshopper's own editor brings a ✓/✗ strip).
    /// Enter or clicking away commits, Escape cancels. One at a time.
    /// </summary>
    internal static class InlineEditor
    {
        private static readonly Color Back = Color.FromArgb(46, 46, 46);
        private static readonly Color Fore = Color.FromArgb(230, 230, 230);

        private static TextBox _box;
        private static GH_Canvas _canvas;
        private static Action<string> _accept;
        private static bool _closing;

        /// <param name="area">The field, in canvas coordinates.</param>
        public static void Show(GH_Canvas canvas, RectangleF area, string text, Action<string> accept, string family, float size = 7.5f)
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
                if (e.KeyCode == Keys.Enter) { e.SuppressKeyPress = true; Close(true); }
                else if (e.KeyCode == Keys.Escape) { e.SuppressKeyPress = true; Close(false); }
            };
            box.LostFocus += (s, e) => Close(true);

            _box = box;
            _canvas = canvas;
            _accept = accept;
            canvas.Controls.Add(box);
            box.Focus();
            box.SelectAll();
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
                _box = null;
                _accept = null;
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
