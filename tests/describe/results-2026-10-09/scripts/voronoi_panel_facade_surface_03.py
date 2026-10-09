# NODE_INPUTS: srf:Surface | "surface to panel (Brep uses first face)", count:int | "number of voronoi cells" min=3 max=300 default=30, seed:int | "random seed" min=0 max=9999 default=1, inset:float | "cell scale about its centre, 1 = no gap" min=0.3 max=1.0 default=0.9
# NODE_OUTPUTS: panels, centers, log:str | "status text"
import random
import Rhino.Geometry as rg

def clip_poly(poly, nx, ny, c):
    # Keep the part of a 2D polygon where nx*x + ny*y <= c
    out = []
    n = len(poly)
    for k in range(n):
        a = poly[k]
        b = poly[(k + 1) % n]
        fa = nx * a[0] + ny * a[1] - c
        fb = nx * b[0] + ny * b[1] - c
        if fa <= 0:
            out.append(a)
        if (fa < 0 and fb > 0) or (fa > 0 and fb < 0):
            t = fa / (fa - fb)
            out.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    return out

# Use sample surface if nothing is wired
if srf is None:
    srf = rg.PlaneSurface(rg.Plane.WorldXY, rg.Interval(0, 30), rg.Interval(0, 20))  # sample
    note = "sample surface used; "
else:
    note = ""

if isinstance(srf, rg.Brep):
    if srf.Faces.Count == 0:
        srf = None
    else:
        srf = srf.Faces[0]

panels = []
centers = []
log = ""

if srf is None:
    log = "no valid surface"
else:
    count = max(3, int(count))
    inset = max(0.3, min(1.0, float(inset)))
    rnd = random.Random(int(seed))
    seeds = [(rnd.random(), rnd.random()) for _ in range(count)]

    d0 = srf.Domain(0)
    d1 = srf.Domain(1)

    def to_surface(x, y):
        return srf.PointAt(d0.Min + x * d0.Length, d1.Min + y * d1.Length)

    for i, (xi, yi) in enumerate(seeds):
        cell = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for j, (xj, yj) in enumerate(seeds):
            if i == j:
                continue
            nx = xj - xi
            ny = yj - yi
            c = (xj * xj + yj * yj - xi * xi - yi * yi) / 2.0
            cell = clip_poly(cell, nx, ny, c)
            if len(cell) < 3:
                break
        if len(cell) < 3:
            continue

        # Shrink cell toward its centroid to create panel gaps
        cx = sum(p[0] for p in cell) / len(cell)
        cy = sum(p[1] for p in cell) / len(cell)
        shrunk = [(cx + (p[0] - cx) * inset, cy + (p[1] - cy) * inset) for p in cell]

        pts = [to_surface(p[0], p[1]) for p in shrunk]
        pts.append(pts[0])
        panels.append(rg.PolylineCurve(pts))
        centers.append(to_surface(xi, yi))

    log = note + "built {} panels from {} seeds".format(len(panels), count)
