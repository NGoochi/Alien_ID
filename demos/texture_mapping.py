# NODE_INPUTS: geometry:list[Geometry] | "Breps, extrusions or meshes to map (a sample wall, plinth and bench when unwired)"
# NODE_INPUTS: size:float | "real-world size of one texture tile (m): wire the material node's size" min=0.05 max=20 default=1.2
# NODE_INPUTS: mapping:str | "how the texture is laid on: box projects each face from its main direction, planar from above" default="box" options="box|planar"
# NODE_INPUTS: rotate:float | "turn the texture on each face (degrees), e.g. 90 to run boards vertically" min=-180 max=180 default=0
# NODE_OUTPUTS: meshes:list[Mesh] | "the geometry as meshes with texture coordinates at real size: wire into Custom Preview with the material"
# NODE_OUTPUTS: info:str
# CATEGORY: Materials
# Texture mapping: gives geometry real-size texture coordinates (box or planar, in metres), so a material's texture tiles at its true scale in a Custom Preview instead of stretching once over each face.
#
# Grasshopper previews a Brep with each face's own 0-1 parameters, so a 1.2 m board texture would cover a 10 m wall
# once. Here every face gets its own copy of its vertices and coordinates = position / size along the two world axes
# across the face's normal (box), or X and Y (planar); the display repeats the texture past 1.
import math

import Rhino
import Rhino.Geometry as rg

doc = Rhino.RhinoDoc.ActiveDoc
u = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
tile = max(0.05, size or 1.2) * u

items = [g for g in (geometry or []) if isinstance(g, (rg.Brep, rg.Extrusion, rg.Mesh, rg.Surface))]
sample = not items
if sample:
    # a curved wall, a plinth and a bench: enough faces at different angles to see the mapping hold
    arc = rg.Arc(rg.Point3d(0, 0, 0), rg.Point3d(4 * u, 1.2 * u, 0), rg.Point3d(8 * u, 0, 0)).ToNurbsCurve()
    face = rg.Surface.CreateExtrusion(arc, rg.Vector3d(0, 0, 3 * u)).ToBrep()
    wall = face.Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d(0, -0.25 * u, 0)), True)
    plinth = rg.Box(rg.Plane.WorldXY, rg.Interval(-1 * u, 9 * u), rg.Interval(-3.5 * u, 1.6 * u), rg.Interval(-0.4 * u, 0)).ToBrep()
    bench = rg.Box(rg.Plane.WorldXY, rg.Interval(1.5 * u, 5.5 * u), rg.Interval(-2.2 * u, -1.6 * u), rg.Interval(0, 0.45 * u)).ToBrep()
    items = [b for b in (wall, plinth, bench) if b is not None]

mp = rg.MeshingParameters.QualityRenderMesh
cos_r, sin_r = math.cos(math.radians(rotate or 0)), math.sin(math.radians(rotate or 0))


def to_mesh(g):
    if isinstance(g, rg.Mesh):
        return g.DuplicateMesh()
    b = g.ToBrep() if isinstance(g, (rg.Extrusion, rg.Surface)) else g
    parts = rg.Mesh.CreateFromBrep(b, mp) or []
    m = rg.Mesh()
    for p in parts:
        m.Append(p)
    return m if m.Vertices.Count else None


meshes = []
for g in items:
    m = to_mesh(g)
    if m is None:
        continue
    m.Unweld(0, True)            # every face gets its own vertices, so each can take its own projection
    m.FaceNormals.ComputeFaceNormals()
    uv = [None] * m.Vertices.Count
    for fi in range(m.Faces.Count):
        f = m.Faces[fi]
        n = m.FaceNormals[fi]
        ax, ay, az = abs(n.X), abs(n.Y), abs(n.Z)
        for vi in ([f.A, f.B, f.C] + ([f.D] if f.IsQuad else [])):
            p = m.Vertices[vi]
            if mapping == "planar" or (az >= ax and az >= ay):
                s, t = p.X, p.Y
            elif ax >= ay:
                s, t = p.Y, p.Z
            else:
                s, t = p.X, p.Z
            s, t = s / tile, t / tile
            uv[vi] = rg.Point2f(s * cos_r - t * sin_r, s * sin_r + t * cos_r)
    m.TextureCoordinates.Clear()
    for vi in range(m.Vertices.Count):
        q = uv[vi] or rg.Point2f(0, 0)
        m.TextureCoordinates.Add(q.X, q.Y)
    m.Normals.ComputeNormals()
    meshes.append(m)

info = "%d mesh%s, %s mapping, one tile = %.2f m%s%s" % (
    len(meshes), "" if len(meshes) == 1 else "es", mapping or "box", tile / u,
    ", turned %g°" % rotate if rotate else "", " (sample wall, plinth and bench)" if sample else "")
