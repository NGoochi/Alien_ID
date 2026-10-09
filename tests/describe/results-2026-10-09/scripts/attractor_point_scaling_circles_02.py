# NODE_INPUTS: circles:list[Circle] | "Circles to scale"
# NODE_INPUTS: attractor:Point3d | "Attractor point" default=[0,0,0]
# NODE_INPUTS: max_distance:float | "Distance at which minimum scale is reached" min=0.01 max=1000 default=10
# NODE_INPUTS: min_scale:float | "Scale at maximum distance" min=0 max=100 default=0.5
# NODE_INPUTS: max_scale:float | "Scale at the attractor" min=0 max=100 default=2
# NODE_OUTPUTS: result, scales:list[float]
# Scales circles linearly by distance from an attractor point.
import Rhino.Geometry as rg

# Sample circle used when no circles are wired.
if not circles:
    circles = [rg.Circle(rg.Plane(rg.Point3d(3, 0, 0), rg.Vector3d.ZAxis), 1.0)]

result = []
scales = []
distance_limit = max(float(max_distance), 0.01)

for circle in circles:
    distance = circle.Center.DistanceTo(attractor)
    t = min(distance / distance_limit, 1.0)
    scale = float(max_scale) + (float(min_scale) - float(max_scale)) * t
    result.append(rg.Circle(circle.Plane, circle.Radius * scale))
    scales.append(scale)
