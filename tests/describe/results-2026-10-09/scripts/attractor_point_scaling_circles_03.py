# NODE_INPUTS: centers:list[Point3d] | "circle centers", attractors:list[Point3d] | "attractor points", base_r:float | "base radius" min=0.01 max=100 default=1.0, max_scale:float | "max scale factor at attractor" min=1 max=20 default=3.0, influence:float | "attractor influence distance" min=0.01 max=500 default=10.0
# NODE_OUTPUTS: circles:list[Curve] | "scaled circles as curves", radii:list[float] | "resulting radii"
# Scales circles by their distance to attractor points: closer circles grow more.
import Rhino.Geometry as rg

# Sample geometry when inputs are unwired
if not centers:  # sample: 5x5 grid of centers
    centers = [rg.Point3d(x * 4.0, y * 4.0, 0) for x in range(5) for y in range(5)]
if not attractors:  # sample: two attractor points
    attractors = [rg.Point3d(4, 4, 0), rg.Point3d(14, 10, 0)]

circles = []
radii = []
for c in centers:
    # distance to nearest attractor
    d = min(c.DistanceTo(a) for a in attractors)
    # linear falloff: full scale at distance 0, base at >= influence
    t = max(0.0, 1.0 - d / influence) if influence > 0 else 0.0
    factor = 1.0 + (max_scale - 1.0) * t
    r = base_r * factor
    plane = rg.Plane(c, rg.Vector3d.ZAxis)
    circle = rg.Circle(plane, r)
    circles.append(circle.ToNurbsCurve())
    radii.append(r)
