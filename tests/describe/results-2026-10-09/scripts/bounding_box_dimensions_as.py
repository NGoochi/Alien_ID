# NODE_INPUTS: geo:list[Geometry] | "geometry to measure"
# NODE_INPUTS: decimals:int | "decimal places" min=0 max=6 default=2
# NODE_OUTPUTS: text:str | "X x Y x Z"
# NODE_OUTPUTS: box:Box | "bounding box"
# Reports the bounding-box dimensions of the input geometry as a formatted text string.

import Rhino.Geometry as rg

# sample geometry when nothing is wired: a 10 x 5 x 3 box
if not geo:
    geo = [rg.Box(rg.Plane.WorldXY,
                  rg.Interval(0, 10), rg.Interval(0, 5), rg.Interval(0, 3)).ToBrep()]

bb = rg.BoundingBox.Empty
for g in geo:
    if g is None:
        continue
    if isinstance(g, rg.Point3d):
        bb.Union(g)
    else:
        gbb = g.GetBoundingBox(True)
        if gbb.IsValid:
            bb.Union(gbb)

if not bb.IsValid:
    text = "no geometry"
    box = None
else:
    d = int(decimals)
    dx, dy, dz = bb.Max.X - bb.Min.X, bb.Max.Y - bb.Min.Y, bb.Max.Z - bb.Min.Z
    text = "{0:.{3}f} x {1:.{3}f} x {2:.{3}f}".format(dx, dy, dz, d)
    box = rg.Box(bb)
