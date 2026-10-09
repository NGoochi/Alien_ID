# NODE_INPUTS: mesh:Mesh
# NODE_INPUTS: spacing:float | "distance between contour planes" min=0.01 max=100 step=0.1 default=1
# NODE_INPUTS: direction:Vector3d | "contour plane normal" default=[0,0,1]
# NODE_INPUTS: origin:Point3d | "reference point for contour positions" default=[0,0,0]
# NODE_OUTPUTS: contours:list[Curve], planes:list[Plane], count:int, status:str
# Creates mesh contour curves at regular intervals along a direction.
import Rhino.Geometry as rg
import Rhino.Geometry.Intersect as ri

# Use a small sample cube when no mesh is supplied.
if mesh is None or (isinstance(mesh, list) and len(mesh) == 0):
    mesh = rg.Mesh()
    for p in [
        (0, 0, 0), (2, 0, 0), (2, 2, 0), (0, 2, 0),
        (0, 0, 2), (2, 0, 2), (2, 2, 2), (0, 2, 2)
    ]:
        mesh.Vertices.Add(*p)
    mesh.Faces.AddFace(0, 1, 2, 3)
    mesh.Faces.AddFace(4, 7, 6, 5)
    mesh.Faces.AddFace(0, 4, 5, 1)
    mesh.Faces.AddFace(1, 5, 6, 2)
    mesh.Faces.AddFace(2, 6, 7, 3)
    mesh.Faces.AddFace(3, 7, 4, 0)

contours = []
planes = []
count = 0
status = "No contours created."

try:
    step = float(spacing)
    normal = rg.Vector3d(direction)
    if step <= 0:
        status = "Spacing must be greater than zero."
    elif not normal.Unitize():
        status = "Direction must be non-zero."
    elif mesh is None or mesh.Faces.Count == 0:
        status = "Mesh is empty."
    else:
        base = origin if isinstance(origin, rg.Point3d) else rg.Point3d(0, 0, 0)
        corners = mesh.GetBoundingBox(True).GetCorners()
        offsets = [(p - base) * normal for p in corners]
        low, high = min(offsets), max(offsets)
        first = int(low // step)
        last = int(high // step)

        for i in range(first, last + 1):
            point = base + normal * (i * step)
            plane = rg.Plane(point, normal)
            polylines = ri.Intersection.MeshPlane(mesh, plane)
            if polylines:
                planes.append(plane)
                for polyline in polylines:
                    if polyline.Count >= 2:
                        contours.append(rg.PolylineCurve(polyline))

        count = len(contours)
        status = "Created {} contour curve(s).".format(count)
except Exception as exc:
    contours = []
    planes = []
    count = 0
    status = "Contour failed: {}".format(exc)
