# NODE_INPUTS: curves:list[Curve], axis:int | "sort axis: 0=X 1=Y 2=Z" min=0 max=2 default=2, closed:bool | "close the loft" default=false
# NODE_OUTPUTS: breps:list[Brep], log:str | "status text"
# Lofts a surface through curves, sorted by their bounding-box centre along an axis.
import Rhino.Geometry as rg

# Sample geometry (unsorted) when nothing is wired in
if not curves:
    curves = [rg.Circle(rg.Point3d(0, 0, z), r).ToNurbsCurve()
              for z, r in [(10, 3), (0, 6), (20, 2), (5, 5)]]
    log = "using sample circles"
else:
    log = "{} curves".format(len(curves))

def key(c):
    p = c.GetBoundingBox(True).Center
    if axis == 0:
        return p.X
    if axis == 1:
        return p.Y
    return p.Z

ordered = sorted([c for c in curves if c is not None], key=key)

breps = []
if len(ordered) < 2:
    log += " | need at least 2 curves"
else:
    result = rg.Brep.CreateFromLoft(ordered, rg.Point3d.Unset, rg.Point3d.Unset,
                                    rg.LoftType.Normal, closed)
    if result:
        breps = list(result)
        log += " | lofted {} brep(s)".format(len(breps))
    else:
        log += " | loft failed"
