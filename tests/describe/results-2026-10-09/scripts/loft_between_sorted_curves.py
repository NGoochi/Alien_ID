# NODE_INPUTS: curves:list[Curve] | "curves to loft between"
# NODE_INPUTS: dir:Vector3d | "sort direction" default=[0,0,1]
# NODE_INPUTS: reverse:bool | "reverse the loft order" default=false
# NODE_OUTPUTS: loft:Brep, ordered:list[Curve], count:int | "lofted surface, curves sorted along dir, curve count"
# Lofts between curves sorted along a direction (sample geometry when none is wired).

import Rhino.Geometry as rg

loft = None

src = [c for c in (curves or []) if c is not None]
if not src:
    # no input wired: three stacked sample circles
    for i, r in enumerate((6.0, 3.0, 8.0)):
        pl = rg.Plane(rg.Point3d(0.0, 0.0, i * 5.0), rg.Vector3d.ZAxis)
        src.append(rg.Circle(pl, r).ToNurbsCurve())

d = dir if dir is not None else rg.Vector3d.ZAxis
if not d.IsValid or d.IsZero:
    d = rg.Vector3d.ZAxis
d.Unitize()

def key_of(c):
    p = c.PointAtNormalizedLength(0.5)
    return p.X * d.X + p.Y * d.Y + p.Z * d.Z

ordered = sorted(src, key=key_of)
if reverse:
    ordered = list(reversed(ordered))

count = len(ordered)

if count > 1:
    breps = rg.Brep.CreateFromLoft(ordered, rg.Point3d.Unset, rg.Point3d.Unset,
                                   rg.LoftType.Normal, False)
    if breps and len(breps) > 0:
        loft = breps[0]
