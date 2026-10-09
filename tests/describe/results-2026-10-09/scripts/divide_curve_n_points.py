# NODE_INPUTS: curve:Curve | "curve to divide", n:int | "point count" min=2 max=500 default=10
# NODE_OUTPUTS: points, length:float | "curve length"
# Divides a curve into N points spaced evenly along its arc length.

import Rhino.Geometry as rg

if curve is None or (isinstance(curve, list) and not curve):
    # sample geometry when nothing is wired
    curve = rg.Curve.CreateInterpolatedCurve(
        [rg.Point3d(0, 0, 0), rg.Point3d(3, 4, 0), rg.Point3d(7, -2, 0), rg.Point3d(10, 3, 0)], 3)
elif isinstance(curve, list):
    curve = curve[0]

n = max(2, int(n))
length = curve.GetLength()
points = [curve.PointAtLength(length * i / (n - 1)) for i in range(n)]
