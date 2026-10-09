# NODE_INPUTS: curves:list[Curve] | "Curves to sort and loft"
# NODE_INPUTS: sort_axis:Vector3d | "Sort direction" default=[0,0,1]
# NODE_OUTPUTS: sorted_curves, loft, log:str | "Loft status"
# Sort curves by their bounding-box centers along an axis, then loft them.
import Rhino.Geometry as rg

# Sample geometry keeps the node useful when no curves are connected.
if curves is None or len(curves) == 0:
    curves = [
        rg.Circle(rg.Plane(rg.Point3d(0, 0, z), rg.Vector3d.ZAxis), radius).ToNurbsCurve()
        for z, radius in [(0, 2.0), (2, 1.7), (4, 1.2)]
    ]

axis = rg.Vector3d(sort_axis)
if not axis.Unitize():
    axis = rg.Vector3d.ZAxis

def sort_value(curve):
    box = curve.GetBoundingBox(True)
    center = box.Center
    return rg.Vector3d(center.X, center.Y, center.Z) * axis

valid_curves = [c for c in curves if c is not None]
sorted_curves = sorted(valid_curves, key=sort_value)
loft = None

if len(sorted_curves) >= 2:
    breps = rg.Brep.CreateFromLoft(
        sorted_curves,
        rg.Point3d.Unset,
        rg.Point3d.Unset,
        rg.LoftType.Normal,
        False
    )
    if breps and len(breps) > 0:
        loft = breps[0]

log = "Loft created from {} sorted curves.".format(len(sorted_curves)) if loft else \
      "Need at least two valid curves; loft was not created."
