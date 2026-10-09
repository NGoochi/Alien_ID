# NODE_INPUTS: circles:list[Curve] | "circles to scale", attractors:list[Point3d] | "attractor points", min_scale:float | "scale far from attractors" min=0.01 max=10 default=0.3, max_scale:float | "scale at attractors" min=0.01 max=10 default=2.0, influence:float | "falloff radius" min=0.01 default=25.0
# NODE_OUTPUTS: curves, scales:list[float] | "scale factor per circle"
# Scales circles by distance to attractor points (smooth falloff between min and max scale).

import Rhino.Geometry as rg

# sample geometry when nothing is wired
if not circles:
    circles = [rg.Circle(rg.Plane.WorldXY, rg.Point3d(i * 8, j * 8, 0), 2.0).ToNurbsCurve()
               for i in range(-2, 3) for j in range(-2, 3)]
if not attractors:
    attractors = [rg.Point3d(-10, -10, 0), rg.Point3d(12, 6, 0)]

falloff = max(influence, 1e-6)
curves = []
scales = []

for crv in circles:
    ok, circ = crv.TryGetCircle()
    if not ok:
        curves.append(crv)
        scales.append(1.0)
        continue
    center = circ.Center
    d = min((center - p).Length for p in attractors)
    t = max(0.0, min(1.0, 1.0 - d / falloff))
    s = min_scale + (max_scale - min_scale) * t
    curves.append(rg.Circle(circ.Plane, circ.Radius * s).ToNurbsCurve())
    scales.append(s)
