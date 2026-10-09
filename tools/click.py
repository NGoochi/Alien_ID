"""Simulate a left click on a node, as Grasshopper would deliver it (tests the node's own mouse handling):

  python3 click.py <node nickname> <x from the node's left> <y: positive = from the bottom, negative = from the top> [double]

Example: the PANEL button of a 225-wide node:  python3 click.py scale_figure 200 -36
"""
import sys

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # works under python -I too
from mcp import call  # noqa: E402

name, dx, dy = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
double = len(sys.argv) > 4 and sys.argv[4] == "double"
y = "b.Bottom - %ff" % dy if dy >= 0 else "b.Y + %ff" % -dy
code = """
using System.Linq; using System.Drawing; using System.Windows.Forms; using Grasshopper.GUI; using Grasshopper.GUI.Canvas;
var canvas = Grasshopper.Instances.ActiveCanvas;
var node = canvas.Document.Objects.First(o => o.NickName == "%s");
var b = node.Attributes.Bounds;
var p = canvas.Viewport.ProjectPoint(new PointF(b.X + %ff, %s));
var e = new GH_CanvasMouseEvent(canvas.Viewport, new MouseEventArgs(MouseButtons.Left, %d, (int)p.X, (int)p.Y, 0));
var r = %s;
if (r.ToString() == "Capture") node.Attributes.RespondToMouseUp(canvas, e);
result = node.NickName + " -> " + r;
""" % (name, dx, y, 2 if double else 1,
       "node.Attributes.RespondToMouseDoubleClick(canvas, e)" if double else "node.Attributes.RespondToMouseDown(canvas, e)")
print(call("run_code", {"language": "csharp", "code": code})["text"])
