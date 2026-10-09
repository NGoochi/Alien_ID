# NODE_INPUTS: srf:Geometry | "surface to panel"
# NODE_INPUTS: n:int | "cell count" min=4 max=300 default=30
# NODE_INPUTS: gap:float | "gap between panels (model units)" min=0.0 max=5.0 default=0.08
# NODE_INPUTS: seed:int | "random seed" min=0 max=9999 default=1
# NODE_OUTPUTS: panels, centers:list[Point3d], count:int
# Voronoi panel facade: clips a surface's UV domain into Voronoi cells and returns panel outlines.

import random
import Rhino.Geometry as rg

# sample geometry when nothing is wired
if srf is None:
    srf = rg.PlaneSurface(rg.Plane.WorldXY, rg.Interval(0, 12), rg.Interval(0, 8))
elif hasattr(srf, "Faces"):  # Brep -> first face's surface
    srf = srf.Faces[0].UnderlyingSurface() if srf.Faces.Count else None
    if srf is None:
        srf = rg.PlaneSurface(rg.Plane.WorldXY, rg.Interval(0, 12), rg.Interval(0, 8))

du, dv = srf.Domain(0), srf.Domain(1)
u0, u1, v0, v1 = du.T0, du.T1, dv.T0, dv.T1

rnd = random.Random(seed)
seeds = [(rnd.uniform(u0, u1), rnd.uniform(v0, v1)) for _ in range(max(1, n))]


def clip(poly, nx, ny, c):
    """Keep the part of poly where nx*x + ny*y <= c."""
    out = []
    m = len(poly)
    for i in range(m):
        a, b = poly[i], poly[(i + 1) % m]
        da = nx * a[0] + ny * a[1] - c
        db = nx * b[0] + ny * b[1] - c
        if da <= 0.0:
            out.append(a)
        if (da < 0.0 < db) or (db < 0.0 < da):
            t = da / (da - db)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out


panels, centers = [], []
for i, (px, py) in enumerate(seeds):
    poly = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    for j, (qx, qy) in enumerate(seeds):
        if i == j:
            continue
        nx, ny = qx - px, qy - py
        c = 0.5 * (qx * qx + qy * qy - px * px - py * py)
        poly = clip(poly, nx, ny, c)
        if len(poly) < 3:
            break
    if len(poly) < 3:
        continue

    p3 = [srf.PointAt(u, v) for u, v in poly]
    cen = rg.Point3d(sum(p.X for p in p3) / len(p3),
                     sum(p.Y for p in p3) / len(p3),
                     sum(p.Z for p in p3) / len(p3))

    shrunk = []
    for p in p3:
        d = cen - p
        L = d.Length
        q = p + d * (max(0.0, L - gap) / L) if L > 1e-9 else p
        ok, u, v = srf.ClosestPoint(q)
        shrunk.append(srf.PointAt(u, v) if ok else q)

    panels.append(rg.PolylineCurve(shrunk + [shrunk[0]]))
    centers.append(cen)

count = len(panels)
