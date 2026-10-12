using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.IO;
using System.Linq;
using System.Windows.Forms;

using Grasshopper.GUI;
using Grasshopper.GUI.Canvas;
using Grasshopper.Kernel;
using Grasshopper.Kernel.Attributes;

using Newtonsoft.Json.Linq;

using Alien.Mcp;
using Alien.Scripting;
using Alien.Ui;

namespace Alien
{
    /// <summary>
    /// Dark card, green accent, glowing when selected (the Alien panel follows the selection). Header = script name +
    /// language badge; status row (ok / error / running); one row per pin, where an unwired input carries its control
    /// (slider, switch, dropdown, text field) built from the script header; and the error itself when the script failed.
    /// Pins never move when the error strip comes and goes.
    /// </summary>
    public class AlienAttributes : GH_ComponentAttributes
    {
        public static readonly Color Accent = Color.FromArgb(0, 200, 83);

        private const float HeaderH = 26f, RowH = 20f, PinH = 18f, CtrlPinH = 22f, MinW = 170f, Radius = 8f, Pad = 9f;
        private const float ErrorMaxH = 52f, ButtonW = 46f, CtrlW = 124f, ValueW = 36f;

        private static readonly Color Bg = Color.FromArgb(30, 30, 30);
        private static readonly Color HeaderBg = Color.FromArgb(22, 22, 22);
        private static readonly Color IoBg = Color.FromArgb(36, 36, 36);
        private static readonly Color FieldBg = Color.FromArgb(46, 46, 46);
        private static readonly Color FieldEdge = Color.FromArgb(62, 62, 62);
        private static readonly Color Text = Color.FromArgb(215, 215, 215);
        private static readonly Color Dim = Color.FromArgb(140, 140, 140);
        private static readonly Color Outline = Color.FromArgb(50, 50, 50);
        private static readonly Color Amber = Color.FromArgb(224, 160, 32);
        private static readonly Color Red = Color.FromArgb(225, 70, 70);
        private static readonly Color Blue = Color.FromArgb(90, 160, 255);
        private static readonly Color Grey = Color.FromArgb(85, 85, 85);
        internal static readonly string Family = OperatingSystem.IsMacOS() ? "Helvetica Neue" : "Segoe UI";

        /// <summary>One input pin and, when it is unwired and has a kind, its control.</summary>
        private sealed class InputRow
        {
            public IGH_Param Param;
            public ParamDef Def;
            public ControlKind Kind;
            public RectangleF Label, Control;
            public bool Active => Kind != ControlKind.None && Param.SourceCount == 0;
        }

        private readonly List<InputRow> _rows = new List<InputRow>();
        private RectangleF _statusRow, _panelButton, _errorStrip;
        private string _error;
        private bool _errorIsWarning;

        // slider being dragged
        private InputRow _drag;
        private NodeControls.SliderRange _dragRange;
        private double _dragValue, _applied;
        private bool _dragLive;

        private new AlienComponent Owner => (AlienComponent)base.Owner;

        public AlienAttributes(AlienComponent owner) : base(owner) { }

        // ── Layout ───────────────────────────────────────────────────

        protected override void Layout()
        {
            var c = Owner;
            (_error, _errorIsWarning) = Problem(c);

            _rows.Clear();
            for (int i = 0; i < c.Params.Input.Count; i++)
            {
                var p = c.Params.Input[i];
                var def = c.Header.FindInput(p.Name) ?? (i < c.Header.Inputs.Count ? c.Header.Inputs[i] : null);
                _rows.Add(new InputRow { Param = p, Def = def, Kind = c.ShowControls && def != null ? NodeControls.KindOf(def) : ControlKind.None });
            }
            bool controls = _rows.Any(r => r.Kind != ControlKind.None);
            float pinH = controls ? CtrlPinH : PinH;
            int rows = Math.Max(c.Params.Input.Count, c.Params.Output.Count);

            float w = MinW, errorH = 0, labelW, outW;
            using (var bmp = new Bitmap(1, 1))
            using (var g = Graphics.FromImage(bmp))
            using (var f = new Font(Family, 7.5f))
            {
                labelW = c.Params.Input.Select(p => g.MeasureString(p.NickName, f).Width).DefaultIfEmpty(0).Max();
                outW = c.Params.Output.Select(p => g.MeasureString(p.NickName, f).Width).DefaultIfEmpty(0).Max();
                float left = 15 + labelW + (controls ? 8 + CtrlW : 0) + 8;
                w = Math.Max(w, left + outW + 22);
                using var hf = new Font(Family, 9f, FontStyle.Bold);
                w = Math.Max(w, g.MeasureString(Title(c), hf).Width + 70);
                if (_error != null)
                {
                    w = Math.Max(w, 220f);
                    errorH = Math.Min(ErrorMaxH, g.MeasureString(_error, f, (int)(w - Pad * 2)).Height) + 10f;
                }
            }

            float io = rows * pinH + (rows > 0 ? 4 : 0);
            float h = HeaderH + RowH + io + errorH;
            var x = Pivot.X - w / 2f;
            var y = Pivot.Y - h / 2f;
            Bounds = new RectangleF(x, y, w, h);
            _statusRow = new RectangleF(x, y + HeaderH, w, RowH);
            _panelButton = new RectangleF(x + w - ButtonW - 6, y + HeaderH + 3, ButtonW, RowH - 6);
            float ioBottom = y + HeaderH + RowH + io;
            _errorStrip = errorH > 0 ? new RectangleF(x, ioBottom, w, errorH) : RectangleF.Empty;

            float top = y + HeaderH + RowH + 2;
            for (int i = 0; i < _rows.Count; i++)
            {
                var r = _rows[i];
                float ry = top + i * pinH;
                if (r.Param.Attributes == null) r.Param.CreateAttributes();
                // The pin owns only its label, so clicks on the control reach the node.
                r.Param.Attributes.Bounds = new RectangleF(x, ry, 15 + labelW + 4, pinH);
                r.Param.Attributes.Pivot = new PointF(x + 4f, ry + pinH / 2f);
                r.Label = new RectangleF(x + 15, ry, labelW + 4, pinH);
                r.Control = new RectangleF(x + 15 + labelW + 8, ry + 3, CtrlW, pinH - 6);
            }
            for (int i = 0; i < c.Params.Output.Count; i++)
            {
                var p = c.Params.Output[i];
                if (p.Attributes == null) p.CreateAttributes();
                float ry = top + i * pinH;
                p.Attributes.Bounds = new RectangleF(x + w - outW - 18, ry, outW + 18, pinH);
                p.Attributes.Pivot = new PointF(x + w - 4f, ry + pinH / 2f);
            }
        }

        private static string Title(AlienComponent c) =>
            c.ScriptPath == null ? "ALIEN" : Path.GetFileNameWithoutExtension(c.ScriptPath);

        // ── Render ───────────────────────────────────────────────────

        protected override void Render(GH_Canvas canvas, Graphics g, GH_CanvasChannel channel)
        {
            if (channel == GH_CanvasChannel.Wires) { base.Render(canvas, g, channel); return; }
            if (channel != GH_CanvasChannel.Objects) return;

            var c = Owner;
            var b = Bounds;
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using var f = new Font(Family, 7.5f);
            using var dimBrush = new SolidBrush(Dim);
            using var textBrush = new SolidBrush(Text);
            var left = new StringFormat { LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap };
            var right = new StringFormat { Alignment = StringAlignment.Far, LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap };
            var centre = new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center };

            if (Selected) Glow(g, b);
            else
                using (var shadow = RoundRect(new RectangleF(b.X + 2, b.Y + 3, b.Width, b.Height), Radius))
                using (var sb = new SolidBrush(Color.FromArgb(50, 0, 0, 0))) g.FillPath(sb, shadow);
            using var body = RoundRect(b, Radius);
            using (var bb = new SolidBrush(Bg)) g.FillPath(bb, body);

            // Header: name + language badge + MCP dot
            using (var hp = RoundTop(new RectangleF(b.X, b.Y, b.Width, HeaderH), Radius))
            using (var hb = new SolidBrush(HeaderBg)) g.FillPath(hb, hp);
            using (var hf = new Font(Family, 9f, FontStyle.Bold))
            using (var tb = new SolidBrush(c.Locked ? Dim : Text))
                g.DrawString(Title(c), hf, tb, new RectangleF(b.X + Pad, b.Y, b.Width - Pad - 58, HeaderH), left);
            if (c.Language != ScriptLanguage.None)
            {
                var pill = new RectangleF(b.Right - 52, b.Y + 7, 30, HeaderH - 14);
                using (var pp = RoundRect(pill, pill.Height / 2f))
                using (var pen = new Pen(Color.FromArgb(160, Accent), 1f)) g.DrawPath(pen, pp);
                using (var bf = new Font(Family, 6.5f, FontStyle.Bold))
                using (var ab = new SolidBrush(Accent))
                    g.DrawString(ScriptLanguages.Tag(c.Language), bf, ab, pill, centre);
            }
            Dot(g, b.Right - 12, b.Y + HeaderH / 2f, 3f, McpServer.Instance.IsRunning ? Accent : Grey);

            // Status row
            using (var sep = new Pen(Color.FromArgb(40, 255, 255, 255), 0.75f))
                g.DrawLine(sep, b.X + 4, _statusRow.Y, b.Right - 4, _statusRow.Y);
            var (statusText, statusColor) = Status(c);
            Dot(g, b.X + Pad + 3, _statusRow.Y + RowH / 2f, 3.5f, statusColor);
            using (var sbr = new SolidBrush(statusColor == Grey || statusColor == Accent ? Dim : statusColor))
                g.DrawString(statusText, f, sbr, new RectangleF(b.X + Pad + 12, _statusRow.Y, _panelButton.X - b.X - Pad - 16, RowH), left);
            Pill(g, _panelButton, "PANEL", Accent, false);

            // Pins and controls
            int rows = Math.Max(c.Params.Input.Count, c.Params.Output.Count);
            if (rows > 0)
            {
                float ioTop = _statusRow.Bottom, ioBottom = !_errorStrip.IsEmpty ? _errorStrip.Y : b.Bottom;
                // No clipping here: on macOS Grasshopper a restored clip leaks into the next node's render. The band
                // that ends the card gets the card's rounded bottom corners itself.
                var io = new RectangleF(b.X, ioTop, b.Width, ioBottom - ioTop);
                using (var ip = _errorStrip.IsEmpty ? RoundBottom(io, Radius) : Rect(io))
                using (var ib = new SolidBrush(IoBg)) g.FillPath(ib, ip);

                foreach (var r in _rows)
                {
                    float cy = r.Label.Y + r.Label.Height / 2f;
                    var p = r.Param;
                    // green = wired, blue = value set on the node or by an agent, grey = header default / none
                    var col = p.SourceCount > 0 ? Accent : c.ManualValues.ContainsKey(p.Name) ? Blue : Grey;
                    Dot(g, b.X + 9, cy, 2.5f, col);
                    g.DrawString(p.NickName, f, textBrush, r.Label, left);
                    if (r.Kind == ControlKind.None) continue;
                    if (!r.Active) { g.DrawString("wired", f, dimBrush, r.Control, left); continue; }
                    DrawControl(g, f, r);
                }
                for (int i = 0; i < c.Params.Output.Count; i++)
                {
                    var p = c.Params.Output[i];
                    var ob = p.Attributes.Bounds;
                    g.DrawString(p.NickName, f, textBrush, new RectangleF(ob.X, ob.Y, ob.Width - 9, ob.Height), right);
                }
            }

            // Error strip: the actual message, on the node
            if (!_errorStrip.IsEmpty)
            {
                var col = _errorIsWarning ? Amber : Red;
                using (var ep = RoundBottom(_errorStrip, Radius))
                using (var eb = new SolidBrush(Color.FromArgb(38, col))) g.FillPath(eb, ep);
                using (var sep = new Pen(Color.FromArgb(90, col), 0.75f))
                    g.DrawLine(sep, b.X + 4, _errorStrip.Y, b.Right - 4, _errorStrip.Y);
                using var etb = new SolidBrush(Color.FromArgb(235, col));
                g.DrawString(_error, f, etb, RectangleF.Inflate(_errorStrip, -Pad, -5), new StringFormat { Trimming = StringTrimming.EllipsisWord });
            }


            // Edge: selection / error / outline
            var edge = Selected ? Color.FromArgb(200, Accent)
                : c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error ? Color.FromArgb(200, Red)
                : Outline;
            using (var ep = new Pen(edge, Selected || c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error ? 2f : 1f))
                g.DrawPath(ep, body);
            using (var ap = new Pen(Accent, 2f))
                g.DrawLine(ap, b.X + 1.5f, b.Y + Radius, b.X + 1.5f, b.Bottom - Radius);

            g.SmoothingMode = SmoothingMode.Default;
        }

        /// <summary>Make this node the selection, so the Alien panel follows it.</summary>
        private void SelectOnly(GH_Canvas canvas)
        {
            Owner.OnPingDocument()?.DeselectAll();
            Selected = true;
            canvas.Refresh();
        }

        /// <summary>A soft accent glow around the selected node: rings fading outwards.</summary>
        private static void Glow(Graphics g, RectangleF b)
        {
            for (int i = 10; i >= 1; i--)
            {
                float t = i / 10f;
                using var ring = RoundRect(RectangleF.Inflate(b, i * 1.6f, i * 1.6f), Radius + i * 1.6f);
                using var pen = new Pen(Color.FromArgb((int)(60 * (1 - t) * (1 - t)) + 6, Accent), 3.2f);
                g.DrawPath(pen, ring);
            }
        }

        private void DrawControl(Graphics g, Font f, InputRow r)
        {
            var rc = r.Control;
            float cy = rc.Y + rc.Height / 2f;
            var value = NodeControls.Current(Owner, r.Def);
            switch (r.Kind)
            {
                case ControlKind.Slider:
                {
                    var range = _drag == r ? _dragRange : NodeControls.Range(r.Def, NodeControls.AsNumber(value), Owner.Hint(r.Def.Name));
                    double v = _drag == r ? _dragValue : NodeControls.AsNumber(value) ?? range.Min;
                    var track = Track(rc);
                    float t = (float)((v - range.Min) / (range.Max - range.Min));
                    float kx = track.X + Math.Max(0, Math.Min(1, t)) * track.Width;
                    using (var tb = new SolidBrush(Grey)) g.FillRectangle(tb, track.X, cy - 1.5f, track.Width, 3);
                    using (var ab = new SolidBrush(Accent)) g.FillRectangle(ab, track.X, cy - 1.5f, kx - track.X, 3);
                    using (var kb = new SolidBrush(_drag == r ? Color.White : Color.FromArgb(230, 230, 230))) g.FillEllipse(kb, kx - 5, cy - 5, 10, 10);
                    using (var vb = new SolidBrush(Text))
                        g.DrawString(range.Format(v), f, vb, new RectangleF(rc.Right - ValueW, rc.Y - 2, ValueW, rc.Height + 4),
                            new StringFormat { Alignment = StringAlignment.Far, LineAlignment = StringAlignment.Center });
                    break;
                }
                case ControlKind.Toggle:
                {
                    bool on = value is bool bv && bv;
                    var sw = new RectangleF(rc.X, cy - 6, 24, 12);
                    using (var sp = RoundRect(sw, 6))
                    using (var sb = new SolidBrush(on ? Accent : Grey)) g.FillPath(sb, sp);
                    using (var kb = new SolidBrush(Color.White)) g.FillEllipse(kb, on ? sw.Right - 11 : sw.X + 1, cy - 5, 10, 10);
                    using (var tb = new SolidBrush(Dim)) g.DrawString(on ? "on" : "off", f, tb, new RectangleF(sw.Right + 6, rc.Y, 40, rc.Height),
                        new StringFormat { LineAlignment = StringAlignment.Center });
                    break;
                }
                case ControlKind.Button:
                {
                    // Momentary: lit while its press waits for or runs its solve
                    bool busy = Owner.IsPulsing(r.Def.Name);
                    var label = busy ? "working…" : "▶ " + ButtonLabel(r.Def.Name);
                    var bb = new RectangleF(rc.X, rc.Y + 1, Math.Min(rc.Width, 96), rc.Height - 2);
                    using (var bp = RoundRect(bb, 4f))
                    {
                        using (var fb = new SolidBrush(busy ? Accent : FieldBg)) g.FillPath(fb, bp);
                        using (var fe = new Pen(Accent, 0.9f)) g.DrawPath(fe, bp);
                    }
                    using (var tb = new SolidBrush(busy ? Color.Black : Accent))
                        g.DrawString(label, f, tb, bb, new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center });
                    break;
                }
                case ControlKind.Choice:
                case ControlKind.Text:
                {
                    using (var fp = RoundRect(rc, 4f))
                    {
                        using (var fb = new SolidBrush(FieldBg)) g.FillPath(fb, fp);
                        using (var fe = new Pen(FieldEdge, 0.8f)) g.DrawPath(fe, fp);
                    }
                    var text = NodeControls.AsText(value);
                    var inner = new RectangleF(rc.X + 5, rc.Y, rc.Width - (r.Kind == ControlKind.Choice ? 18 : 8), rc.Height);
                    using (var tb = new SolidBrush(text.Length > 0 ? Text : Dim))
                        g.DrawString(text.Length > 0 ? text : "type…", f, tb, inner,
                            new StringFormat { LineAlignment = StringAlignment.Center, Trimming = StringTrimming.EllipsisCharacter, FormatFlags = StringFormatFlags.NoWrap });
                    if (r.Kind == ControlKind.Choice)
                        using (var ab = new SolidBrush(Dim)) g.DrawString("▾", f, ab, new RectangleF(rc.Right - 14, rc.Y, 12, rc.Height),
                            new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center });
                    break;
                }
            }
        }

        private static string ButtonLabel(string name)
        {
            var words = name.Replace('_', ' ');
            return words.Length == 0 ? name : char.ToUpperInvariant(words[0]) + words.Substring(1);
        }

        private static RectangleF Track(RectangleF control) =>
            new RectangleF(control.X + 5, control.Y, control.Width - ValueW - 12, control.Height);

        // ── Mouse ────────────────────────────────────────────────────

        public override GH_ObjectResponse RespondToMouseDown(GH_Canvas sender, GH_CanvasMouseEvent e)
        {
            if (e.Button != MouseButtons.Left) return base.RespondToMouseDown(sender, e);
            var pt = e.CanvasLocation;
            if (_panelButton.Contains(pt)) { SelectOnly(sender); AlienPanel.Open(); return GH_ObjectResponse.Handled; }

            var r = _rows.FirstOrDefault(x => x.Active && x.Control.Contains(pt));
            if (r == null) return base.RespondToMouseDown(sender, e);
            var value = NodeControls.Current(Owner, r.Def);
            switch (r.Kind)
            {
                case ControlKind.Slider:
                    Owner.OnPingDocument()?.UndoUtil.RecordGenericObjectEvent("Alien slider", Owner);
                    _drag = r;
                    _dragRange = NodeControls.Range(r.Def, NodeControls.AsNumber(value), Owner.Hint(r.Def.Name));
                    _applied = NodeControls.AsNumber(value) ?? double.NaN;
                    // Fast scripts follow the slider live; slow ones (network, heavy geometry) solve on release.
                    _dragLive = Owner.LastRun.Iterations > 0 && Owner.LastRun.Ms < 80 && !Owner.IsDescribing;
                    DragTo(pt.X);
                    sender.Refresh();
                    return GH_ObjectResponse.Capture;
                case ControlKind.Toggle:
                    Owner.SetInput(r.Def.Name, new JValue(!(value is bool on && on)));
                    return GH_ObjectResponse.Handled;
                case ControlKind.Button:
                    if (!Owner.IsPulsing(r.Def.Name)) Owner.Pulse(r.Def.Name);
                    return GH_ObjectResponse.Handled;
                case ControlKind.Choice:
                    var menu = new ContextMenuStrip();
                    foreach (var option in r.Def.Choices)
                    {
                        var o = option;
                        var item = new ToolStripMenuItem(o) { Checked = o == NodeControls.AsText(value) };
                        item.Click += (s, a) => Owner.SetInput(r.Def.Name, new JValue(o));
                        menu.Items.Add(item);
                    }
                    menu.Show(sender, e.ControlLocation);
                    return GH_ObjectResponse.Handled;
                case ControlKind.Text:
                    EditValue(sender, r, NodeControls.AsText(value));
                    return GH_ObjectResponse.Handled;
            }
            return base.RespondToMouseDown(sender, e);
        }

        public override GH_ObjectResponse RespondToMouseMove(GH_Canvas sender, GH_CanvasMouseEvent e)
        {
            if (_drag == null) return base.RespondToMouseMove(sender, e);
            DragTo(e.CanvasLocation.X);
            sender.Refresh();
            return GH_ObjectResponse.Handled;
        }

        public override GH_ObjectResponse RespondToMouseUp(GH_Canvas sender, GH_CanvasMouseEvent e)
        {
            if (_drag == null) return base.RespondToMouseUp(sender, e);
            var r = _drag;
            _drag = null;
            if (_dragValue != _applied) Apply(r, _dragValue);
            sender.Refresh();
            return GH_ObjectResponse.Release;
        }

        private void DragTo(float x)
        {
            var track = Track(_drag.Control);
            double t = Math.Max(0, Math.Min(1, (x - track.X) / track.Width));
            _dragValue = _dragRange.Snap(_dragRange.Min + t * (_dragRange.Max - _dragRange.Min));
            if (_dragLive && _dragValue != _applied) Apply(_drag, _dragValue);
        }

        private void Apply(InputRow r, double v)
        {
            _applied = v;
            Owner.SetManualValues(new Dictionary<string, JToken>
            {
                [r.Def.Name] = r.Def.Type == "int" ? new JValue((long)Math.Round(v)) : new JValue(v)
            });
        }

        private void EditValue(GH_Canvas canvas, InputRow r, string text) =>
            InlineEditor.Show(canvas, r.Control, text, typed =>
            {
                var token = NodeControls.FromText(r.Def, typed);
                if (token != null) Owner.SetInput(r.Def.Name, token);
            }, Family);

        /// <summary>Double-click a slider to type its value; anywhere else, select the node and open the Alien panel.</summary>
        public override GH_ObjectResponse RespondToMouseDoubleClick(GH_Canvas sender, GH_CanvasMouseEvent e)
        {
            var r = _rows.FirstOrDefault(x => x.Active && x.Control.Contains(e.CanvasLocation));
            if (r != null)
            {
                if (r.Kind == ControlKind.Slider)
                {
                    var v = NodeControls.AsNumber(NodeControls.Current(Owner, r.Def));
                    EditValue(sender, r, v.HasValue ? NodeControls.Range(r.Def, v, Owner.Hint(r.Def.Name)).Format(v.Value) : "");
                }
                return GH_ObjectResponse.Handled;
            }
            SelectOnly(sender);
            AlienPanel.Open();
            return GH_ObjectResponse.Handled;
        }

        // ── State text ───────────────────────────────────────────────

        /// <summary>Status line and its level (ok | error | warning | busy | idle), for the Alien panel.</summary>
        internal static (string text, string level) StatusOf(AlienComponent c)
        {
            var (text, col) = Status(c);
            return (text, col == Accent ? "ok" : col == Red ? "error" : col == Amber ? (c.Activity != null ? "busy" : "warning") : "idle");
        }

        /// <summary>The error strip's message, or null.</summary>
        internal static string ProblemOf(AlienComponent c) => Problem(c).Item1;

        private static (string, Color) Status(AlienComponent c)
        {
            if (c.Activity != null) return (c.Activity, Amber);
            if (c.ScriptPath == null) return ("blank · describe it in the panel", Grey);
            if (c.IsRebuilding) return ("rebuilding pins…", Amber);
            var r = c.LastRun;
            if (c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error)
                return (r.FirstError?.Line is int line ? $"error · line {line}" : "error", Red);
            if (c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Warning) return ("warning", Amber);
            if (r.Iterations == 0) return ("idle", Grey);
            var runs = r.Iterations > 1 ? $" · {r.Iterations} runs" : "";
            return ($"ok · {r.Ms:0} ms{runs}", Accent);
        }

        /// <summary>The message for the error strip, and whether it's only a warning. (null, _) when all is well.</summary>
        private static (string, bool) Problem(AlienComponent c)
        {
            if (c.Activity != null) return (null, false);
            if (c.ScriptPath != null)
            {
                var e = c.LastRun.FirstError;
                if (e != null) return ((e.Line.HasValue ? $"line {e.Line}: " : "") + e.Error, false);
                if (c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Error)
                    return (c.RuntimeMessages(GH_RuntimeMessageLevel.Error).FirstOrDefault(), false);
            }
            if (c.DescribeError != null) return ("Describe: " + c.DescribeError, true);
            if (c.ScriptPath == null) return (null, false);
            if (c.RuntimeMessageLevel == GH_RuntimeMessageLevel.Warning)
                return (c.RuntimeMessages(GH_RuntimeMessageLevel.Warning).FirstOrDefault(), true);
            if (c.LastRun.Unset.Count > 0) return ("never assigned: " + string.Join(", ", c.LastRun.Unset), true);
            return (null, false);
        }

        // ── Drawing helpers ──────────────────────────────────────────

        private static void Pill(Graphics g, RectangleF r, string text, Color col, bool filled)
        {
            using (var bp = RoundRect(r, r.Height / 2f))
            {
                using (var fill = new SolidBrush(Color.FromArgb(filled ? 90 : 40, col))) g.FillPath(fill, bp);
                using (var pen = new Pen(col, 0.8f)) g.DrawPath(pen, bp);
            }
            using var bf = new Font(Family, 6.5f, FontStyle.Bold);
            using var tb = new SolidBrush(col);
            g.DrawString(text, bf, tb, r, new StringFormat { Alignment = StringAlignment.Center, LineAlignment = StringAlignment.Center });
        }

        private static void Dot(Graphics g, float cx, float cy, float r, Color col)
        {
            using var br = new SolidBrush(col);
            g.FillEllipse(br, cx - r, cy - r, r * 2, r * 2);
        }

        private static GraphicsPath RoundRect(RectangleF r, float rad)
        {
            float d = rad * 2;
            var p = new GraphicsPath();
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        private static GraphicsPath Rect(RectangleF r)
        {
            var p = new GraphicsPath();
            p.AddRectangle(r);
            return p;
        }

        private static GraphicsPath RoundTop(RectangleF r, float rad)
        {
            float d = rad * 2;
            var p = new GraphicsPath();
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddLine(r.Right, r.Bottom, r.X, r.Bottom);
            p.CloseFigure();
            return p;
        }

        private static GraphicsPath RoundBottom(RectangleF r, float rad)
        {
            float d = rad * 2;
            var p = new GraphicsPath();
            p.AddLine(r.X, r.Y, r.Right, r.Y);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }
    }
}
