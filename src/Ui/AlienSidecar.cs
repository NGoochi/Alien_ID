using System;
using System.Runtime.InteropServices;

using Eto.Drawing;
using Eto.Forms;

using Grasshopper;

namespace Alien.Ui
{
    /// <summary>
    /// The Alien panel glued to the side of the Grasshopper window: a borderless window attached to the editor.
    /// macOS: a native child window (addChildWindow:ordered:), so it moves with Grasshopper with no lag. Windows: an owned
    /// window that follows the editor. It sits on Grasshopper's right when the screen has room, else on its left, else
    /// inside its right edge; it matches the editor's height and hides while the editor is hidden or minimised.
    /// </summary>
    internal static class AlienSidecar
    {
        public const int Width = 340;

        private static Form _form;
        private static UITimer _timer;
        private static System.Drawing.Rectangle _last;
        private static bool _wanted;

        public static bool IsOpen => _wanted;

        public static void Show()
        {
            var editor = Instances.DocumentEditor;
            if (editor == null) return;
            if (_form == null)
            {
                _form = new Form
                {
                    WindowStyle = WindowStyle.None,
                    ShowInTaskbar = false,
                    Resizable = false,
                    Minimizable = false,
                    Maximizable = false,
                    Content = new AlienPanel(sidecar: true),
                };
                _timer = new UITimer { Interval = 0.15 };
                _timer.Elapsed += (s, e) => Follow(false);
            }
            _wanted = true;
            Follow(true);
            _timer.Start();
        }

        public static void Hide()
        {
            _wanted = false;
            _timer?.Stop();
            if (_form != null) _form.Visible = false;
        }

        /// <summary>Keep the window beside the editor: same height, right side if it fits, else left, else inside.</summary>
        private static void Follow(bool force)
        {
            var editor = Instances.DocumentEditor;
            bool editorShown = editor != null && !editor.IsDisposed && editor.Visible
                               && editor.WindowState != System.Windows.Forms.FormWindowState.Minimized;
            if (!_wanted || !editorShown)
            {
                if (_form.Visible) _form.Visible = false;
                return;
            }

            var b = editor.Bounds;
            bool show = !_form.Visible;
            if (!force && !show && b == _last) return;
            _last = b;

            var screen = Screen.FromRectangle(new RectangleF(b.X, b.Y, b.Width, b.Height)).WorkingArea;
            int x = b.Right + Width <= screen.Right ? b.Right
                  : b.Left - Width >= screen.Left ? b.Left - Width
                  : b.Right - Width;
            _form.Location = new Point(x, b.Top);
            _form.Size = new Size(Width, b.Height);
            if (show)
            {
                _form.Show();
                Attach(editor);
            }
        }

        private static void Attach(System.Windows.Forms.Form editor)
        {
            try
            {
                if (OperatingSystem.IsMacOS())
                    MacObjC.Send(editor.Handle, MacObjC.Sel("addChildWindow:ordered:"), _form.NativeHandle, 1 /* NSWindowAbove */);
                else if (OperatingSystem.IsWindows())
                    Win32.SetWindowLongPtr(_form.NativeHandle, -8 /* GWLP_HWNDPARENT: owner */, editor.Handle);
            }
            catch (Exception ex) { Rhino.RhinoApp.WriteLine("Alien: could not attach the panel to Grasshopper (" + ex.Message + ")"); }
        }

        private static class MacObjC
        {
            private const string Lib = "/usr/lib/libobjc.A.dylib";
            [DllImport(Lib, EntryPoint = "sel_registerName")] public static extern IntPtr Sel(string name);
            [DllImport(Lib, EntryPoint = "objc_msgSend")] public static extern IntPtr Send(IntPtr receiver, IntPtr selector, IntPtr arg, long ordered);
        }

        private static class Win32
        {
            [DllImport("user32.dll", EntryPoint = "SetWindowLongPtrW")] public static extern IntPtr SetWindowLongPtr(IntPtr hWnd, int index, IntPtr value);
        }
    }
}
