# NODE_INPUTS: mesh:Mesh, direction:Vector3d | "contour direction" default=[0,0,1], interval:float | "spacing" min=0.01 default=1.0, tol:float | "tolerance" min=0.0 default=0.01
# NODE_OUTPUTS: curves, count:int
# Contours a mesh with evenly spaced section curves along a direction.
import Rhino.Geometry as rg

# sample mesh when nothing is wired
if mesh is None:
    mesh = rg.Mesh.CreateFromBox(rg.BoundingBox(rg.Point3d(-5, -5, 0), rg.Point3d(5, 5, 10)), 2, 2, 4)

if direction is None or not direction.IsValid or direction.Length < 1e-12:
    direction = rg.Vector3d.ZAxis
direction.Unitize()

if interval is None or interval <= 0:
    interval = 1.0
if tol is None or tol <= 0:
    tol = 0.01

bb = mesh.GetBoundingBox(True)
center = bb.Center
lo = hi = 0.0
for c in bb.GetCorners():
    d = (c - center) * direction
    lo = min(lo, d)
    hi = max(hi, d)

curves = []
n = min(int((hi - lo) / interval) + 1, 500)
for k in range(n):
    plane = rg.Plane(center + direction * (lo + k * interval), direction)
    segs = rg.Mesh.CreateContourCurves(mesh, plane, tol)
    if segs:
        curves.extend(segs)

count = len(curves)
