# NODE_INPUTS: curve:Curve | "curve to divide", n:int | "point count" min=2 max=500 default=10, include_ends:bool | "include curve ends" default=true
# NODE_OUTPUTS: points, params, tangents
# Divides a curve into N evenly spaced points, returning points, curve parameters and tangents.

import Rhino.Geometry as rg

if curve is None:
    # sample geometry: a circle so the node shows a result before anything is wired
    curve = rg.Circle(rg.Plane.WorldXY, 10.0).ToNurbsCurve()

n = max(2, int(n))
segments = n - 1 if include_ends else n

params = curve.DivideByCount(segments, include_ends)

points = []
tangents = []
if params:
    for t in params:
        points.append(curve.PointAt(t))
        tangents.append(curve.TangentAt(t))
else:
    params = []
