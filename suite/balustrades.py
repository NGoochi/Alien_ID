# NODE_INPUTS: path:list[Curve] | "lines to guard: slab edges, stairs' edges (they keep their slope), polylines, arcs, closed outlines (a sample when unwired)"
# NODE_INPUTS: balustrade_type:object | "balustrade_type record from balustrade_types (default: glass and posts, 1.0 m)"
# NODE_INPUTS: base:str | "blank: follow each path's own z (stair edges); else a level name, code or index, or an elevation like 3.2 (m)" default=""
# NODE_INPUTS: levels:list[object] | "level records, for a level named in base (sample levels when unwired)"
# NODE_INPUTS: height:float | "height override (m), floors and flights alike; 0 = the type's" min=0 max=2 default=0
# NODE_INPUTS: offset:float | "move off the path (m): + = to its left (stairs' edges: onto the stair), inside a closed outline" min=-1 max=1 default=0
# NODE_INPUTS: end_posts:bool | "posts at the ends of open paths" default=true
# NODE_INPUTS: fall:float | "drop beside the balustrade (m), for the NCC climbing check on rails and wires; 0 = not checked" min=0 max=100 default=0
# NODE_INPUTS: site:object | "site record (optional): places the sample"
# NODE_OUTPUTS: balustrades:list[str] | "balustrade records", solids:list[Brep] | "posts, infill (glass, bars, rails, wires, sheet), handrails", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: plan:list[Curve] | "plan: posts and the balustrade's faces, at each balustrade's lowest point", elevation:list[Curve] | "front outlines of every part, in place (seen square-on they are the elevation)"
# NODE_OUTPUTS: handrail:list[Curve] | "handrail centrelines", info:str
# CATEGORY: Building elements
# Balustrades: posts, glass, bars, rails, wires or sheet and a handrail along slab edges and stair edges, following a stair's pitch, with plan and elevation linework and first-pass NCC barrier checks.
#
# Each path is one balustrade. Paths keep their own z unless `base` names a level or an elevation, which flattens them
# onto it: a stairs node's `edges` (nosing line on flights, flat on landings, stepping up a riser at a newel) give
# balustrades whose handrail runs parallel to the pitch, `stair_height` above the nosing line, and `height` on the
# landings and floors. Posts go at corners (turns over 15°, a change between flat and sloped, a step in the base) and
# evenly between them at no more than the type's spacing; curves are faceted into bays of about that width. Bars,
# rails and wires are spaced so no clear gap is wider than the type's gap. The offset moves a path sideways in plan
# (mitred). First-pass NCC checks per balustrade (not certification): heights, openings, the gap underneath,
# climbable rails (with `fall`), a handrail on flights.
import time

import Rhino.Geometry as rg

import alien_bim as bim
import alien_stairs as ss

t0 = time.time()
u = bim.units()
tol = bim.tol()
notes = []
st = bim.site_of(site)

bt, tnotes = ss.balustrade_type_of(balustrade_type)
notes += tnotes
h_over = ss.number(height, 0.0, 0.0, 5.0)
h_floor = h_over or bt["height"]
h_stair = h_over or bt["stair_height"]

# base: the path's own z, a level, or an elevation
flat_z, base_label = None, "path z"
b = str(base or "").strip()
if b:
    lv = bim.levels_of(levels)
    try:
        flat_z = float(b)
        base_label = "z %.3f m" % flat_z
    except ValueError:
        L = bim.level_at(lv, b)
        if L is None:
            notes.append("no level '%s': following the path's own z" % b)
        else:
            flat_z = float(L["elevation"])
            base_label = "%s (z %.3f m)" % (L.get("name"), flat_z)

curves = [c for c in (path or []) if isinstance(c, rg.Curve)]
sample = not curves
if path and sample:
    notes.append("path has no curves: showing the sample")
if sample:
    # a stair edge rising 1.75 m over 2.8 m, a 1.2 m landing, then 3 m along a floor edge turning a corner
    sp = bim.site_plane(st, u)

    def q(x, y, z):
        return sp.PointAt(x * u, y * u) + rg.Vector3d(0, 0, z * u)
    curves = [rg.PolylineCurve([q(0, 0, 0.175), q(2.8, 0, 1.75), q(4.0, 0, 1.75), q(4.0, 3.0, 1.75)])]


def points_of(c):
    """A curve as points: polyline vertices as they are, smooth stretches sampled finely (resample makes the bays)."""
    ok, pl = c.TryGetPolyline()
    if ok:
        return list(pl)
    segs = list(c.DuplicateSegments() or []) or [c]
    pts = []
    for sgm in segs:
        if sgm.IsLinear(tol):
            sp_ = [sgm.PointAtStart, sgm.PointAtEnd]
        else:
            n = max(8, int(sgm.GetLength() / (0.1 * u)))
            sp_ = [sgm.PointAt(t) for t in sgm.DivideByCount(n, True)]
        for p in sp_:
            if not pts or p.DistanceTo(pts[-1]) > tol:
                pts.append(p)
    return pts


balustrades, solids, colours, plan, elevation, handrail = [], [], [], [], [], []
rows, all_ok, total = [], True, 0.0
for i, c in enumerate(curves):
    pts = points_of(c)
    closed = c.IsClosed
    if closed and len(pts) > 2 and pts[0].DistanceTo(pts[-1]) <= tol:
        pts = pts[:-1]
    if flat_z is not None:
        pts = [rg.Point3d(p.X, p.Y, flat_z * u) for p in pts]
    vs = ss.vertices_of(pts, tol)
    if closed and len(vs) > 2 and abs(vs[0][0] - vs[-1][0]) < tol and abs(vs[0][1] - vs[-1][1]) < tol:
        vs = vs[:-1]
    if len(vs) < 2:
        notes.append("path %d is a point in plan: skipped" % i)
        continue
    if offset:
        d = offset * u
        if closed:  # + = inside, whichever way the outline runs
            area = 0.5 * sum(vs[k][0] * vs[(k + 1) % len(vs)][1] - vs[(k + 1) % len(vs)][0] * vs[k][1] for k in range(len(vs)))
            d = d if area > 0 else -d
        vs = ss.offset_vertices(vs, d, closed)
    bv = ss.resample(vs, bt["spacing"] * u, closed, u)
    if len(bv) < 2:
        notes.append("path %d is too short: skipped" % i)
        continue
    res = ss.build_balustrade(bv, bt, u, end_posts=bool(end_posts), closed=closed, h_floor=h_floor, h_stair=h_stair)
    if not res["solids"]:
        notes.append("path %d made no parts (too short for the posts?)" % i)
    solids += res["solids"]
    colours += res["colours"]
    elevation += res["elevation"]
    handrail += res["rail"]
    zmin = min(min(v[2], v[3]) for v in bv)
    for crv in res["plan"]:   # plan at the balustrade's lowest point
        crv = crv.DuplicateCurve()
        crv.Translate(rg.Vector3d(0, 0, zmin))
        plan.append(crv)
    gap = max(ss.infill_gap(bt), res["max_gap"])
    checks = ss.barrier_checks(bt, h_floor, h_stair, res["flat"], res["sloped"], gap, bt["bottom"], fall or 0.0)
    ok = all(ch["ok"] for ch in checks)
    all_ok = all_ok and ok
    base_pts = []
    for v in bv:
        base_pts.append(rg.Point3d(v[0], v[1], v[2]))
        if abs(v[3] - v[2]) > tol:
            base_pts.append(rg.Point3d(v[0], v[1], v[3]))
    rec = {"id": "BL%02d" % (len(balustrades) + 1), "type": bt, "path": bim.curve_record(rg.PolylineCurve(base_pts), u),
           "base": base_label, "height": h_floor, "stair_height": h_stair, "offset": offset or 0.0, "closed": closed,
           "length": round(res["length"], 4), "bays": res["bays"], "posts": res["posts"], "max_gap": round(gap, 4),
           "sloped": res["sloped"], "checks": checks, "ok": ok, "site": st.get("name")}
    balustrades.append(bim.pack("balustrade", **rec))
    total += res["length"]
    rows.append((rec, checks))

info_lines = ["%d balustrade%s%s, %.2f m in all: %s (%s), %s, base %s%s" % (
    len(balustrades), "" if len(balustrades) == 1 else "s", " (sample)" if sample else "", total, bt["name"], bt["style"],
    ("%.0f mm (override)" % (h_over * 1000)) if h_over else ("%.0f mm, %.0f mm on flights" % (h_floor * 1000, h_stair * 1000)),
    base_label, (", offset %+.3f m" % offset) if offset else "")]
for rec, checks in rows[:6]:
    info_lines.append("%s: %.2f m, %d bays, %d posts%s; NCC first-pass checks, not certification: %s" % (
        rec["id"], rec["length"], rec["bays"], rec["posts"], ", follows a stair pitch" if rec["sloped"] else "",
        "all pass" if rec["ok"] else "FAILS"))
    info_lines += ["  " + ss.rule_text(ch) for ch in checks]
if len(rows) > 6:
    info_lines.append("... %d more (%s)" % (len(rows) - 6, "all pass" if all(r[0]["ok"] for r in rows[6:]) else "some fail: see the records"))
info_lines.append("%d solids in %.0f ms" % (len(solids), (time.time() - t0) * 1000))
info = "\n".join(info_lines + notes)
