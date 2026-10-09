# NODE_INPUTS: panels:list[Geometry] | "Panel geometry to number; uses sample rectangles when empty"
# NODE_INPUTS: start:int | "First panel number" min=1 max=9999 default=1
# NODE_INPUTS: prefix:str | "Optional label prefix" default=""
# NODE_OUTPUTS: panels, numbers, label_points, labels
# Assign sequential fabrication numbers and label locations to panel geometry.

import Rhino.Geometry as rg

if panels is None or len(panels) == 0:
    # Sample geometry keeps the node useful before panels are connected.
    panels = [
        rg.Rectangle3d(rg.Plane.WorldXY, 10.0, 6.0).ToNurbsCurve(),
        rg.Rectangle3d(rg.Plane(rg.Point3d(12, 0, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), 10.0, 6.0).ToNurbsCurve(),
        rg.Rectangle3d(rg.Plane(rg.Point3d(24, 0, 0), rg.Vector3d.XAxis, rg.Vector3d.YAxis), 10.0, 6.0).ToNurbsCurve()
    ]

panels = list(panels)
first = int(start) if start is not None else 1
prefix = str(prefix) if prefix is not None else ""

numbers = [first + i for i in range(len(panels))]
labels = [prefix + str(n) for n in numbers]
label_points = []

for panel in panels:
    if panel is None:
        label_points.append(rg.Point3d.Origin)
    else:
        label_points.append(panel.GetBoundingBox(True).Center)
