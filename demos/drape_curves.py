# NODE_INPUTS: curves:list[Curve] | "curves to lay on the ground (lot lines, a site boundary, paths)", ground:Mesh | "terrain mesh (e.g. vic_site_map's ground)"
# NODE_INPUTS: step:float | "sample spacing along each curve (m): smaller follows the ground more closely" min=0.2 max=20 default=1
# NODE_INPUTS: lift:float | "height above the ground (m), so the lines draw over it" min=0 max=2 step=0.01 default=0.05
# NODE_OUTPUTS: draped:list[Curve] | "the curves on the ground, as polylines", info:str
# CATEGORY: Site & context
# Drape curves: lays flat curves onto a terrain mesh (sampled every step metres, kept at each corner), lifted a little so they draw over it: lot lines and site boundaries on vic_site_map's ground.
#
# Parts of a curve beyond the mesh keep the height of the last point that hit it, so a lot that runs off the terrain
# stays continuous. Distances are metres whatever the model's units.
import Rhino
import Rhino.Geometry as rg

doc = Rhino.RhinoDoc.ActiveDoc
u = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0
draped = []
info = "Wire curves and a ground mesh."
if ground is not None and curves:
    bb = ground.GetBoundingBox(False)
    top = bb.Max.Z + 10 * u
    gap = max(0.2, step or 1.0) * u
    up = (lift or 0) * u
    hits = misses = 0
    for c in curves:
        if c is None:
            continue
        ts = set(c.DivideByLength(gap, True) or [c.Domain.Min])
        ts.add(c.Domain.Max)
        ok, pl = c.TryGetPolyline()
        if ok:  # keep every corner of a polyline
            for p in pl:
                ok_t, t = c.ClosestPoint(p)
                if ok_t:
                    ts.add(t)
        pts, last = [], None
        for t in sorted(ts):
            p = c.PointAt(t)
            d = rg.Intersect.Intersection.MeshRay(ground, rg.Ray3d(rg.Point3d(p.X, p.Y, top), -rg.Vector3d.ZAxis))
            if d >= 0:
                z = top - d
                last = z
                hits += 1
            else:
                z = last if last is not None else p.Z
                misses += 1
            pts.append(rg.Point3d(p.X, p.Y, z + up))
        if c.IsClosed and pts and pts[0].DistanceTo(pts[-1]) > 1e-6:
            pts.append(pts[0])
        if len(pts) >= 2:
            draped.append(rg.PolylineCurve(pts))
    info = "%d curves draped (%d samples on the ground, %d beyond it), %.2f m above" % (len(draped), hits, misses, (lift or 0))
