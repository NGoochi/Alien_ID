# NODE_INPUTS: mesh:Mesh, interval:float | "contour spacing along Z" min=0.1 max=50 default=1.0
# NODE_OUTPUTS: contours:list[Curve], levels:list[float] | "Z-level contours of a mesh at regular intervals"
# Contour a mesh with horizontal planes spaced at a fixed Z interval.
import Rhino.Geometry as rg

# Sample geometry if no mesh is wired
if not mesh:
    sphere = rg.Sphere(rg.Point3d(0, 0, 0), 5.0).ToBrep()
    meshes = list(rg.Mesh.CreateFromBrep(sphere, rg.MeshingParameters.Default))
else:
    meshes = mesh if isinstance(mesh, list) else [mesh]

contours = []
levels = []

step = interval if interval and interval > 0 else 1.0

for m in meshes:
    if m is None:
        continue
    bbox = m.GetBoundingBox(True)
    z = bbox.Min.Z
    while z <= bbox.Max.Z + 1e-9:
        plane = rg.Plane(rg.Point3d(0, 0, z), rg.Vector3d.ZAxis)
        polylines = rg.Intersect.Intersection.MeshPlane(m, plane)
        if polylines:
            for pl in polylines:
                contours.append(rg.PolylineCurve(pl))
                levels.append(z)
        z += step
