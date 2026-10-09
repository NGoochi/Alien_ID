# NODE_INPUTS: crv:Curve, n:int | "number of divisions" min=1 max=200 default=10
# NODE_OUTPUTS: points:list[Point3d], tangents:list[Vector3d] | "points and tangents"
# Divides a curve into N equal-length segments and returns the points and unit tangents.
import Rhino.Geometry as rg

# Sample geometry when nothing is wired
if crv is None:
    crv = rg.Curve.CreateInterpolatedCurve(
        [rg.Point3d(0, 0, 0), rg.Point3d(5, 4, 0), rg.Point3d(10, 0, 0), rg.Point3d(15, -3, 0)], 3)

count = max(1, int(n)) if n is not None else 10

points = []
tangents = []
pts = crv.DivideByCount(count, True)
if pts is not None:
    for t in pts:
        points.append(crv.PointAt(t))
        tangents.append(crv.TangentAt(t))
