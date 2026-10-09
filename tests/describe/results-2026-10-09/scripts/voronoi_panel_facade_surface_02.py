# NODE_INPUTS: surface:Surface | "Facade surface; uses a sample surface when empty"
# NODE_INPUTS: u_count:int | "Voronoi seed columns" min=1 max=20 default=5
# NODE_INPUTS: v_count:int | "Voronoi seed rows" min=1 max=20 default=4
# NODE_OUTPUTS: panels, seeds, used_surface
# Creates Voronoi panel boundary curves mapped onto a surface.
import Rhino.Geometry as rg

# Sample geometry for an unwired surface.
if surface is None or (isinstance(surface, list) and len(surface) == 0):
    used_surface = rg.PlaneSurface(
        rg.Plane.WorldXY,
        rg.Interval(0.0, 10.0),
        rg.Interval(0.0, 6.0)
    )
else:
    used_surface = surface[0] if isinstance(surface, list) else surface

panels = []
seeds = []

try:
    ud = used_surface.Domain(0)
    vd = used_surface.Domain(1)
    nu = max(1, int(u_count or 5))
    nv = max(1, int(v_count or 4))

    # Slight deterministic offsets keep the layout from looking like a grid.
    uv_seeds = []
    for j in range(nv):
        for i in range(nu):
            fu = (i + 0.5 + (0.18 if j % 2 else 0.0)) / nu
            fv = (j + 0.5) / nv
            fu = min(0.98, fu)
            uv_seeds.append((ud.T0 + fu * (ud.T1 - ud.T0),
                             vd.T0 + fv * (vd.T1 - vd.T0)))

    def clip_half_plane(poly, a, b, c):
        """Keep points satisfying a*x + b*y <= c."""
        if not poly:
            return []
        result = []
        prev = poly[-1]
        dp = a * prev[0] + b * prev[1] - c
        for cur in poly:
            dc = a * cur[0] + b * cur[1] - c
            if (dp <= 1e-10) != (dc <= 1e-10):
                t = dp / (dp - dc)
                result.append((prev[0] + t * (cur[0] - prev[0]),
                              prev[1] + t * (cur[1] - prev[1])))
            if dc <= 1e-10:
                result.append(cur)
            prev, dp = cur, dc
        return result

    for idx, (sx, sy) in enumerate(uv_seeds):
        poly = [(ud.T0, vd.T0), (ud.T1, vd.T0),
                (ud.T1, vd.T1), (ud.T0, vd.T1)]

        # Clip the domain against each seed's nearer-than bisector.
        for other_idx, (ox, oy) in enumerate(uv_seeds):
            if idx == other_idx:
                continue
            a, b = ox - sx, oy - sy
            c = (ox * ox + oy * oy - sx * sx - sy * sy) * 0.5
            poly = clip_half_plane(poly, a, b, c)
            if not poly:
                break

        if len(poly) < 3:
            continue

        # Densify UV edges so mapped outlines follow curved surfaces better.
        pts = []
        for k in range(len(poly)):
            p0, p1 = poly[k], poly[(k + 1) % len(poly)]
            for step in range(5):
                t = step / 5.0
                u = p0[0] + t * (p1[0] - p0[0])
                v = p0[1] + t * (p1[1] - p0[1])
                pts.append(used_surface.PointAt(u, v))
        pts.append(pts[0])
        if len(pts) >= 4:
            panels.append(rg.PolylineCurve(pts))
        seeds.append(used_surface.PointAt(sx, sy))

except Exception:
    panels = []
    seeds = []
