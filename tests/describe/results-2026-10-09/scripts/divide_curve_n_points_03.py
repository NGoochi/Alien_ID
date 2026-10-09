# NODE_INPUTS: curve:Curve | "curve to divide"
# NODE_INPUTS: n:int | "number of divisions" min=1 max=50 default=5
# NODE_OUTPUTS: points:list[Point3d]
# Divide a curve into a chosen number of divisions and return the division points.
import Rhino.Geometry as rg

# Use a sample line when no curve is wired.
if curve is None or (isinstance(curve, (list, tuple)) and len(curve) == 0):
    curve = rg.LineCurve(rg.Point3d(0, 0, 0), rg.Point3d(10, 0, 0))

count = max(1, min(50, int(n or 5)))
points = []

if curve is not None:
    divided = curve.DivideByCount(count, True)
    if divided:
        points = list(divided)
