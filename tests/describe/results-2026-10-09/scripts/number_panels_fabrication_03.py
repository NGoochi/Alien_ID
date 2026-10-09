# NODE_INPUTS: panels:list[Brep] | "panels to number", prefix:str | "label prefix" default="P", start:int | "first number" min=0 max=9999 default=1, row_tol:float | "grouping tolerance for rows" min=1 max=5000 default=500
# NODE_OUTPUTS: labels, names:list[str], log:str | "status text"
# Numbers panels in fabrication reading order (top row first, left to right) and returns a label dot for each.

import Rhino.Geometry as rg

# Sample geometry when nothing is wired
if not panels:
    panels = [
        rg.Box(rg.Plane(rg.Point3d(x * 1200, 0, 0), rg.Vector3d.ZAxis),
               rg.Interval(0, 1000), rg.Interval(0, 600), rg.Interval(0, 20)).ToBrep()
        for x in range(3)
    ] + [
        rg.Box(rg.Plane(rg.Point3d(x * 1200, 800, 0), rg.Vector3d.ZAxis),
               rg.Interval(0, 1000), rg.Interval(0, 600), rg.Interval(0, 20)).ToBrep()
        for x in range(2)
    ]

# Centre of each panel's bounding box
items = []
for b in panels:
    c = b.GetBoundingBox(True).Center
    items.append((b, c))

# Reading order: group by row (Y), then left to right (X)
items.sort(key=lambda t: (-round(t[1].Y / row_tol), t[1].X))

labels = []
names = []
for i, (b, c) in enumerate(items):
    name = "{}-{:03d}".format(prefix, start + i)
    names.append(name)
    labels.append(rg.TextDot(name, c))

log = "numbered {} panels from {} to {}".format(
    len(names), names[0] if names else "-", names[-1] if names else "-")
