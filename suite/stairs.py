# NODE_INPUTS: levels:list[object] | "level records (sample: 3 storeys at 3.2 m)", from_level:str | "lower level: name, code or index (blank: the lowest)" default=""
# NODE_INPUTS: to_level:str | "upper level: name, code or index (blank: the level above)" default="", height:float | "floor-to-floor height (m); 0 = from the levels" min=0 max=12 default=0
# NODE_INPUTS: base:list[Curve] | "base lines: each one's start is the foot of a stair, its direction the way up (sample: a line along X at the site origin)"
# NODE_INPUTS: align:str | "where the base line runs across the stair (looking up)" default="centre" options="centre|left edge|right edge"
# NODE_INPUTS: shape:str | "stair shape" default="straight" options="straight|L (quarter landing)|U (half landing)|spiral"
# NODE_INPUTS: turn:str | "L, U and spiral: the way the stair turns going up" default="left" options="left|right"
# NODE_INPUTS: width:float | "stair width (m): tread length; stringers sit outside it" min=0.5 max=4 default=1.2
# NODE_INPUTS: stair_type:object | "stair_type record from stair_types (default: concrete in-situ, public, 175/280)"
# NODE_INPUTS: landing:float | "landing depth (m) in the walking direction; 0 = the stair width (at least 0.75)" min=0 max=5 default=0
# NODE_INPUTS: split:int | "L and U: risers in the first flight (0 = half)" min=0 max=40 default=0
# NODE_INPUTS: well:float | "U: gap between the two flights; spiral: centre column radius (m)" min=0 max=2 default=0.1
# NODE_INPUTS: sides:str | "which stair sides get balustrade edges (the upper floor's void edges always do)" default="both" options="both|inner|outer|none"
# NODE_INPUTS: cut:float | "plan cut height above the lower level (m): treads above it are dashed" min=0.2 max=3 default=1.0
# NODE_INPUTS: site:object | "site record (optional): drawing scale for the plan symbols; places the sample base line"
# NODE_OUTPUTS: stairs:list[str] | "stair records", solids:list[Brep] | "flights (waist slab, or treads, risers and stringers), landings", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: plan:list[Curve] | "plan at the lower level: treads, walking line with UP and arrow, cut line with break; above the cut dashed"
# NODE_OUTPUTS: edges:list[Curve] | "balustrade lines: open stair sides on the nosing line (flat on landings), and the upper floor's void edges; the walkable side is on each curve's left"
# NODE_OUTPUTS: voids:list[Curve] | "floor opening at the upper level, closed: for the slabs node's openings"
# NODE_OUTPUTS: risers:list[int] | "risers per stair", riser:list[float] | "riser height per stair (mm)", going:list[float] | "going per stair (mm)", info:str
# CATEGORY: Building elements
# Stairs: straight, L (quarter landing), U (half landing) or spiral stairs between two levels, with risers and going fitted to the floor-to-floor height, first-pass NCC checks, plan symbols, balustrade edges and the floor void.
#
# Risers: the floor-to-floor height divided into the whole number of risers nearest the type's target (kept inside
# the NCC range when the target is); the going is the type's. Flights hold 2–18 risers: a taller run gets landings
# (a 4.5 m storey makes a straight stair two flights), and L and U stairs split at their turn (`split` sets the first
# flight). The base line's start is the centre (or an edge, `align`) of the first riser, at the lower level; its
# direction is the way up. Spiral stairs wind round a centre column (`well` = its radius), going measured on the
# walking line at mid-width, with the Housing Provisions' spiral limits. Edges run along the nosing line on flights
# and flat on landings; where a flight starts from a landing the edge steps up one riser at the first nosing (a newel
# post), so balustrades follow the pitch. The void covers every part of the stair with less than 2.0 m headroom
# under the upper floor (taken as 300 mm deep); its edges, less the arrival edge, are added to `edges`. NCC checks are
# first-pass design checks, not certification.
import math
import time

import Rhino.Geometry as rg

import alien_bim as bim
import alien_stairs as ss

t_start = time.time()
u = bim.units()
tol = bim.tol()
Z = rg.Vector3d.ZAxis
notes = []
st = bim.site_of(site)
FLOOR = 0.3                         # assumed depth of the upper floor, for the void's headroom (m)
PHI_MAX = 2 * math.pi - math.radians(3)   # a spiral's plan shows its first turn: the rest is straight above it
WALK_MAX = 2 * math.pi - math.radians(30)  # and its walking line stops short of the start, so the arrow reads

# ── levels and height ──
lv = bim.levels_of(levels)
lo = bim.level_at(lv, from_level if str(from_level or "").strip() else None)
if lo is None:
    lo = lv[0]
    notes.append("no level '%s': starting from %s" % (from_level, lo.get("name")))
z_from = float(lo["elevation"])
hi = None
if height is not None and height > 0:
    H = float(height)
    hi = next((l for l in lv if abs(float(l["elevation"]) - (z_from + H)) < 0.005), None)   # a level up there, if any
else:
    if str(to_level or "").strip():
        hi = bim.level_at(lv, to_level)
        if hi is None:
            notes.append("no level '%s': using the level above" % to_level)
        elif float(hi["elevation"]) <= z_from + 1e-6:
            notes.append("%s is not above %s: using the level above" % (hi.get("name"), lo.get("name")))
            hi = None
    if hi is None:
        hi = bim.level_above(lv, lo)
    H = (float(hi["elevation"]) if hi else bim.level_top(lv, lo)) - z_from
    if H < 0.1:
        notes.append("the levels are %.2f m apart: stairs made 3.0 m high" % H)
        H = 3.0

t, tnotes = ss.stair_type_of(stair_type)
notes += tnotes
key = t["construction"]
stringer = key in ("steel", "timber")
sb = float(t["stringer"]["b"]) if stringer and t.get("stringer") else 0.0
nos = float(t.get("nosing") or 0.0)
sh = str(shape or "straight").lower()
kind = "spiral" if "spiral" in sh else "U" if sh.startswith("u") else "L" if sh.startswith("l") else "straight"
if kind == "straight" and not sh.startswith("straight"):
    notes.append("shape '%s' not known: built straight" % shape)
s = -1 if str(turn or "").strip().lower().startswith("r") else 1
W = ss.number(width, 1.2, 0.3, 6.0)
Ld = ss.number(landing, 0.0, 0.0, 10.0)
Ld = Ld if Ld > 0 else max(W, 0.75)
gw = ss.number(well, 0.1, 0.0, 5.0)
lim = ss.limits_for(t, kind == "spiral")
maxf = int(lim["flight"][1])

N = ss.riser_count(H, float(t["riser"]), t, kind == "spiral")
r = H / N
G = float(t["going"])
if kind in ("L", "U") and N < 4:
    notes.append("%d risers are too few to turn: built straight" % N)
    kind = "straight"
if kind in ("L", "U"):
    n1 = int(split or 0)
    n1 = n1 if 0 < n1 < N else int(math.ceil(N / 2.0))
    runs = [n1, N - n1]
else:
    runs = [N]

# ── layout (metres, in the stair's own frame: x up the first flight, y to its left, z above the lower level) ──


def add2(p, v, k=1.0):
    return (p[0] + v[0] * k, p[1] + v[1] * k)


def leftof(d):
    return (-d[1], d[0])


flights, landings = [], []          # flights: start, dir, z, a, from_landing, end; landings: origin, dir, x0..y1, z, length, kind
side_pts = {1: [], -1: []}          # +1 = left side, -1 = right side, in walking order (x, y, z)
walk = []                           # walking line (x, y, z on the nosing line)
spiral = None
if kind != "spiral":
    cur, d, z = (0.0, 0.0), (1.0, 0.0), 0.0
    first = True
    for ri, nr in enumerate(runs):
        fl = ss.split_even(nr, maxf)
        for fi, a in enumerate(fl):
            lf = leftof(d)
            end = add2(cur, d, (a - 1) * G)
            flights.append({"start": cur, "dir": d, "z": z, "a": a, "from_landing": not first, "end": end})
            for sg in (1, -1):
                side_pts[sg].append(add2(cur, lf, sg * W / 2) + (z + r,))
                side_pts[sg].append(add2(end, lf, sg * W / 2) + (z + a * r,))
            walk.append(cur + (z + r,))
            walk.append(end + (z + a * r,))
            first = False
            z += a * r
            cur = end
            if fi < len(fl) - 1:          # a landing within the run
                landings.append({"origin": cur, "dir": d, "x0": 0.0, "x1": Ld, "y0": -W / 2, "y1": W / 2, "z": z, "length": Ld, "kind": "straight"})
                cur = add2(cur, d, Ld)
                for sg in (1, -1):
                    side_pts[sg].append(add2(cur, lf, sg * W / 2) + (z,))
                walk.append(cur + (z,))
        if ri < len(runs) - 1:            # the turn
            lf = leftof(d)

            def P(a_, b_):
                return add2(add2(cur, d, a_), lf, b_)
            if kind == "L":
                S = max(W, Ld)
                ys = sorted([-s * W / 2, -s * W / 2 + s * S])
                landings.append({"origin": cur, "dir": d, "x0": 0.0, "x1": S, "y0": ys[0], "y1": ys[1], "z": z, "length": S, "kind": "quarter"})
                inner = [P(S - W, s * W / 2), P(S - W, s * (S - W / 2))]
                outer = [P(S, -s * W / 2), P(S, s * (S - W / 2))]
                walk.append(P(S - W / 2, 0) + (z,))
                nxt, nd = P(S - W / 2, s * (S - W / 2)), (lf[0] * s, lf[1] * s)
            else:
                ys = sorted([-s * W / 2, s * (1.5 * W + gw)])
                landings.append({"origin": cur, "dir": d, "x0": 0.0, "x1": Ld, "y0": ys[0], "y1": ys[1], "z": z, "length": Ld, "kind": "half"})
                inner = [P(0, s * (W / 2 + gw))]
                outer = [P(Ld, -s * W / 2), P(Ld, s * (1.5 * W + gw)), P(0, s * (1.5 * W + gw))]
                walk += [P(Ld / 2, 0) + (z,), P(Ld / 2, s * (W + gw)) + (z,)]
                nxt, nd = P(0, s * (W + gw)), (-d[0], -d[1])
            for p in inner:
                side_pts[s].append(p + (z,))
            for p in outer:
                side_pts[-s].append(p + (z,))
            walk.append(nxt + (z,))
            cur, d = nxt, nd
    walk.insert(0, (0.0, 0.0, r))
else:
    rc = max(gw, 0.0)
    R = rc + W
    rw = rc + W / 2
    alpha = G / rw                     # radians per going on the walking line
    beta = Ld / rw
    centre = (0.0, s * rw)
    th0 = math.atan2(-s, 0.0)          # angle of the first riser line, from the centre

    def polar(rad, phi):
        th = th0 + s * phi
        return (centre[0] + rad * math.cos(th), centre[1] + rad * math.sin(th))
    spiral = {"rc": rc, "R": R, "rw": rw, "alpha": alpha, "flights": [], "landings": []}
    phi, z = 0.0, 0.0
    fl = ss.split_even(N, maxf)
    for fi, a in enumerate(fl):
        spiral["flights"].append({"phi": phi, "z": z, "a": a})
        flights.append({"a": a, "z": z, "from_landing": fi > 0})
        steps = max(2, int(math.ceil((a - 1) * alpha / math.radians(5))))
        for q in range(steps + 1):
            f = (a - 1) * alpha * q / steps
            side_pts[1].append(polar(R, phi + f) + (z + r + r * f / alpha,))
            if phi + f <= WALK_MAX:
                walk.append(polar(rw, phi + f) + (z + r + r * f / alpha,))
        phi += (a - 1) * alpha
        z += a * r
        span = beta if fi < len(fl) - 1 else math.pi / 2
        spiral["landings"].append({"phi": phi, "span": span, "z": z, "top": fi == len(fl) - 1})
        if fi < len(fl) - 1:
            landings.append({"z": z, "length": Ld, "kind": "spiral"})
            steps = max(2, int(math.ceil(beta / math.radians(5))))
            for q in range(1, steps + 1):
                side_pts[1].append(polar(R, phi + beta * q / steps) + (z,))
                if phi + beta * q / steps <= WALK_MAX:
                    walk.append(polar(rw, phi + beta * q / steps) + (z,))
            phi += beta
    spiral["phi_end"] = phi
    spiral["polar"] = polar
    spiral["centre"] = centre

# ── checks ──
flight_risers = [f["a"] for f in flights]
headroom = None
if spiral and spiral["phi_end"] > 2 * math.pi:
    under = float(t.get("tread") or 0.04) if stringer else r + float(t.get("waist") or 0.175)
    headroom = 2 * math.pi / spiral["alpha"] * r - under
consecutive = None if kind == "spiral" else [sum(flight_risers)] if kind == "straight" else runs   # a spiral turns all the way
checks = ss.stair_checks(t, r, G, flight_risers, [l["length"] for l in landings], W, kind == "spiral", consecutive, headroom)
ok_all = all(c["ok"] for c in checks)


def signed_area(pts):
    return 0.5 * sum(pts[i].X * pts[(i + 1) % len(pts)].Y - pts[(i + 1) % len(pts)].X * pts[i].Y for i in range(len(pts)))


def simplify(pts):
    """A closed polygon without repeated or collinear vertices."""
    pts = ss._dedupe(pts)
    changed = True
    while changed and len(pts) > 3:
        changed = False
        for i in range(len(pts)):
            a, b, c = pts[i - 1], pts[i], pts[(i + 1) % len(pts)]
            v1, v2 = b - a, c - b
            if rg.Vector3d.CrossProduct(v1, v2).Length <= 1e-6 * v1.Length * v2.Length + tol * tol:
                pts.pop(i)
                changed = True
                break
    return pts


def open_at(pts, a, b):
    """The closed polygon opened where it runs along the segment a-b (the arrival edge): the path from b's end of
    that stretch round to a's, keeping the polygon's direction. None if no side lies along a-b."""
    n = len(pts)
    ab = b - a
    L = ab.Length
    if L < tol:
        return None
    ab.Unitize()
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]

        def off(x):
            v = x - a
            return (v - ab * (v * ab)).Length
        if off(p) > 2 * tol + 0.001 * u or off(q) > 2 * tol + 0.001 * u:
            continue
        pq = q - p
        Lpq = pq.Length
        if Lpq < tol:
            continue
        ta, tb = ((a - p) * pq) / (Lpq * Lpq), ((b - p) * pq) / (Lpq * Lpq)
        t0, t1 = max(0.0, min(ta, tb)), min(1.0, max(ta, tb))
        if t1 - t0 < 1e-6:
            continue
        A, B = p + pq * t0, p + pq * t1
        path = [B] + [pts[(i + 1 + k) % n] for k in range(n)] + [A]
        return ss._dedupe(path, closed=False)
    return None


# ── world mapping, per base line ──
curves = [c for c in (base or []) if isinstance(c, rg.Curve)]
sample = not curves
if base and sample:
    notes.append("base has no curves: using the sample base line")
if sample:
    sp = bim.site_plane(st, u)
    curves = [rg.LineCurve(sp.Origin, sp.Origin + sp.XAxis * u)]

den = 100
try:
    den = int(st.get("scale") or 100)
except (TypeError, ValueError):
    den = 100
paper = den / 1000.0                   # metres per paper mm
z_cut = ss.number(cut, 1.0, 0.0, 50.0)
z_t = H - lim["headroom"] - FLOOR      # parts above this need the void
y_shift = {"left edge": -W / 2, "right edge": W / 2}.get(str(align or "").strip().lower(), 0.0)

stairs, solids, colours, plan, edges, voids = [], [], [], [], [], []
risers, riser, going = [], [], []
summaries = []
for si, crv in enumerate(curves):
    p0 = crv.PointAtStart
    tan = crv.TangentAtStart
    tan = rg.Vector3d(tan.X, tan.Y, 0)
    if tan.Length < 1e-9:
        tan = rg.Vector3d(crv.PointAtEnd.X - p0.X, crv.PointAtEnd.Y - p0.Y, 0)
    if tan.Length < 1e-9:
        tan = rg.Vector3d.XAxis
        notes.append("base line %d is vertical: the stair runs along X" % si)
    tan.Unitize()
    lf_w = rg.Vector3d.CrossProduct(Z, tan)
    bp = rg.Plane(rg.Point3d(p0.X, p0.Y, z_from * u), tan, lf_w)
    bp.Origin = bp.PointAt(0, y_shift * u)

    def W3(x, y, z=0.0):
        return ss.at(bp, x, y, z, u)

    def V3(d):
        v = bp.XAxis * d[0] + bp.YAxis * d[1]
        v.Unitize()
        return v

    parts = []
    # solids
    if not spiral:
        for f in flights:
            X = V3(f["dir"])
            fp = rg.Plane(W3(f["start"][0], f["start"][1], f["z"]), X, rg.Vector3d.CrossProduct(Z, X))
            parts += ss.flight_solids(fp, t, f["a"], r, G, W, f["from_landing"], u, nib=True)
        for l in landings:
            X = V3(l["dir"])
            lp = rg.Plane(W3(l["origin"][0], l["origin"][1], l["z"]), X, rg.Vector3d.CrossProduct(Z, X))
            parts += ss.landing_solids(lp, t, l["x0"], l["x1"], l["y0"], l["y1"], u, r)
    else:
        sp_ = spiral
        c3 = W3(sp_["centre"][0], sp_["centre"][1], 0)
        bth = math.atan2(bp.XAxis.Y, bp.XAxis.X)   # the base plane's turn in the world

        def wang(phi):                              # world angle of a stair angle phi
            return bth + th0 + s * phi
        tt = float(t.get("tread") or 0.04)
        rt = float(t.get("riser_t") or 0.006)
        block = r + float(t.get("waist") or 0.175)
        for f in sp_["flights"]:
            for k in range(1, f["a"]):
                a0, a1 = wang(f["phi"] + (k - 1) * alpha - nos / rw), wang(f["phi"] + k * alpha)
                ztop = z_from + f["z"] + k * r
                th = tt if stringer else block
                b = ss.prism(ss.sector(c3, sp_["rc"], sp_["R"], min(a0, a1), max(a0, a1), ztop - th, u), Z * (th * u))
                if b:
                    parts.append((b, "tread"))
            if stringer and t.get("risers") != "open":
                for k in range(1, f["a"] + 1):
                    a_ = wang(f["phi"] + (k - 1) * alpha)
                    radial = rg.Vector3d(math.cos(a_), math.sin(a_), 0)
                    tang = rg.Vector3d.CrossProduct(Z, radial) * s
                    rp = rg.Plane(rg.Point3d(c3.X, c3.Y, (z_from + f["z"]) * u), radial, tang)
                    zb0 = (k - 1) * r
                    if k * r - tt - zb0 > 0.002:
                        b = ss._box(rp, max(sp_["rc"], 0.02), sp_["R"], -rt, 0, zb0, k * r - tt, u)
                        if b:
                            parts.append((b, "riser"))
        for l in sp_["landings"]:
            a0, a1 = wang(l["phi"]), wang(l["phi"] + l["span"])
            th = tt if stringer else max(float(t.get("waist") or 0.175), r)
            b = ss.prism(ss.sector(c3, sp_["rc"], sp_["R"], min(a0, a1), max(a0, a1), z_from + l["z"] - th, u), Z * (th * u))
            if b:
                parts.append((b, "landing"))
        if sp_["rc"] >= 0.02:
            col = rg.Cylinder(rg.Circle(rg.Plane(rg.Point3d(c3.X, c3.Y, z_from * u), Z), sp_["rc"] * u), (H + 0.9) * u).ToBrep(True, True)
            parts.append((col, "stringer"))
    for b, part in parts:
        if b is not None:
            solids.append(b)
            colours.append(ss.colour(t["materials"].get(part, "concrete")))

    # ── plan (flat at the lower level) ──
    dash, gap = 1.5 * paper, 1.0 * paper

    def line(pa, pb, za, zb):
        """Plan line between two local points, solid below the cut and dashed above it (split where it crosses)."""
        a3, b3 = W3(pa[0], pa[1]), W3(pb[0], pb[1])
        if (za <= z_cut) == (zb <= z_cut) or abs(zb - za) < 1e-9:
            return _pieces(a3, b3, za > z_cut and zb > z_cut)
        f = (z_cut - za) / (zb - za)
        m3 = a3 + (b3 - a3) * f
        return _pieces(a3, m3, za > z_cut) + _pieces(m3, b3, zb > z_cut)

    def _pieces(a3, b3, dashed):
        L = a3.DistanceTo(b3)
        if L < tol:
            return []
        if not dashed:
            return [rg.LineCurve(a3, b3)]
        out, pos, v = [], 0.0, (b3 - a3) / L
        while pos < L - 1e-9:
            e = min(L, pos + dash * u)
            out.append(rg.LineCurve(a3 + v * pos, a3 + v * e))
            pos = e + gap * u
        return out

    def path(pts):
        """A plan polyline through local (x, y, z) points: solid at or below the cut, dashed above it, with one dash
        pattern running along the whole line (5° arc segments would each be a solid dash otherwise)."""
        runs = []
        for i in range(len(pts) - 1):
            (xa, ya, za), (xb, yb, zb) = pts[i], pts[i + 1]
            a3, b3 = W3(xa, ya), W3(xb, yb)
            cuts = [(a3, za)]
            if (za <= z_cut) != (zb <= z_cut) and abs(zb - za) > 1e-9:
                f = (z_cut - za) / (zb - za)
                cuts.append((a3 + (b3 - a3) * f, z_cut))
            cuts.append((b3, zb))
            for (p, zp), (q, zq) in zip(cuts[:-1], cuts[1:]):
                dashed = (zp + zq) / 2 > z_cut
                if runs and runs[-1][0] == dashed:
                    runs[-1][1].append(q)
                else:
                    runs.append([dashed, [p, q]])
        out = []
        for dashed, ps in runs:
            ps = ss._dedupe(ps, closed=False)
            if len(ps) < 2:
                continue
            if not dashed:
                out.append(rg.PolylineCurve(ps))
                continue
            pattern, k, left, cur = (dash * u, gap * u), 0, dash * u, [ps[0]]
            for a3, b3 in zip(ps[:-1], ps[1:]):
                L = a3.DistanceTo(b3)
                v, pos = (b3 - a3) / L, 0.0
                while L - pos > left:
                    pos += left
                    pt_ = a3 + v * pos
                    if k % 2 == 0:
                        cur = ss._dedupe(cur + [pt_], closed=False)
                        if len(cur) > 1:
                            out.append(rg.PolylineCurve(cur))
                    else:
                        cur = [pt_]
                    k += 1
                    left = pattern[k % 2]
                left -= L - pos
                if k % 2 == 0:
                    cur.append(b3)
            cur = ss._dedupe(cur, closed=False)
            if k % 2 == 0 and len(cur) > 1:
                out.append(rg.PolylineCurve(cur))
        return out

    def flocal(f, x, y):
        return add2(add2(f["start"], f["dir"], x), leftof(f["dir"]), y)

    if not spiral:
        for f in flights:
            a, z0 = f["a"], f["z"]
            x_end = (a - 1) * G
            for k in range(2 if f["from_landing"] else 1, a):   # a flight off a landing: the landing edge is its first step
                x = (k - 1) * G - nos
                plan += line(flocal(f, x, -W / 2), flocal(f, x, W / 2), z0 + k * r, z0 + k * r)
            plan += line(flocal(f, x_end, -W / 2), flocal(f, x_end, W / 2), z0 + a * r, z0 + a * r)
            ys = [-W / 2, W / 2] + ([-W / 2 - sb, W / 2 + sb] if stringer else [])
            for y in ys:
                plan += line(flocal(f, -nos, y), flocal(f, x_end, y), z0 + r - nos * r / G, z0 + a * r)
            if z0 + r <= z_cut < z0 + a * r:  # the cut line, with a break, across this flight
                xc = (z_cut - z0 - r) * G / r
                e_in = (gw * 0.5 if kind == "U" else 0.15 * W)   # stay out of a U stair's other flight
                e0, e1 = (0.15 * W, e_in) if s > 0 else (e_in, 0.15 * W)
                pa, pb = flocal(f, xc - 0.35 * G, -(W / 2 + sb + e0)), flocal(f, xc + 0.35 * G, W / 2 + sb + e1)
                a3, b3 = W3(pa[0], pa[1]), W3(pb[0], pb[1])
                v = b3 - a3
                L = v.Length
                v.Unitize()
                nrm = rg.Vector3d.CrossProduct(Z, v)
                m = a3 + v * (L / 2)
                zz = min(1.2 * paper * u, L / 6)
                plan.append(rg.PolylineCurve([a3, m - v * zz, m - v * (zz * 0.3) + nrm * zz, m + v * (zz * 0.3) - nrm * zz, m + v * zz, b3]))
        for l in landings:
            o, d, lf = l["origin"], l["dir"], leftof(l["dir"])

            def lp(x, y):
                return add2(add2(o, d, x), lf, y)
            x0, x1, y0, y1, zl = l["x0"], l["x1"], l["y0"], l["y1"], l["z"]
            plan += line(lp(x0, y0), lp(x1, y0), zl, zl) + line(lp(x1, y0), lp(x1, y1), zl, zl) + line(lp(x1, y1), lp(x0, y1), zl, zl)
            for ya, yb in ((y0, -W / 2), (W / 2, y1)):  # the x0 edge, less where the flight arrives
                if yb - ya > 1e-6:
                    plan += line(lp(x0, ya), lp(x0, yb), zl, zl)
    else:
        sp_ = spiral
        pol = sp_["polar"]
        c2 = sp_["centre"]
        plan.append(rg.Circle(rg.Plane(W3(c2[0], c2[1]), Z), max(sp_["rc"], 0.02) * u).ToNurbsCurve())
        for f in sp_["flights"]:
            for k in range(1, f["a"] + 1):
                ph = f["phi"] + (k - 1) * alpha
                if ph <= PHI_MAX:
                    plan += line(pol(sp_["rc"], ph), pol(sp_["R"], ph), f["z"] + k * r, f["z"] + k * r)
            steps = max(2, int(math.ceil((f["a"] - 1) * alpha / math.radians(5))))
            arc = []
            for q in range(steps + 1):
                ph = f["phi"] + (f["a"] - 1) * alpha * q / steps
                if ph > PHI_MAX:
                    break
                arc.append(pol(sp_["R"], ph) + (f["z"] + r + r * (ph - f["phi"]) / alpha,))
            plan += path(arc)
            zs = f["z"] + r
            if zs <= z_cut < f["z"] + f["a"] * r:   # break line along a radius at the cut
                ph = f["phi"] + (z_cut - zs) / r * alpha
                a3, b3 = W3(*pol(sp_["rc"], ph)), W3(*pol(sp_["R"] + 0.15 * W, ph))
                v = b3 - a3
                L = v.Length
                v.Unitize()
                nrm = rg.Vector3d.CrossProduct(Z, v)
                m = a3 + v * (L / 2)
                zz = min(1.2 * paper * u, L / 6)
                plan.append(rg.PolylineCurve([a3, m - v * zz, m - v * (zz * 0.3) + nrm * zz, m + v * (zz * 0.3) - nrm * zz, m + v * zz, b3]))
        for l in sp_["landings"]:
            steps = max(2, int(math.ceil(l["span"] / math.radians(5))))
            plan += path([pol(sp_["R"], l["phi"] + l["span"] * q / steps) + (l["z"],) for q in range(steps + 1)
                          if l["phi"] + l["span"] * q / steps <= PHI_MAX])
            if l["phi"] + l["span"] <= PHI_MAX:
                plan += line(pol(sp_["rc"], l["phi"] + l["span"]), pol(sp_["R"], l["phi"] + l["span"]), l["z"], l["z"])

    # walking line: a dot at the foot, UP, solid to the cut then dashed, an arrow at the top
    wl = [walk[0]]
    for p in walk[1:]:
        if math.hypot(p[0] - wl[-1][0], p[1] - wl[-1][1]) > 1e-6 or abs(p[2] - wl[-1][2]) > 1e-9:
            wl.append(p)
    plan += path(wl)
    start3 = W3(wl[0][0], wl[0][1])
    plan.append(rg.Circle(rg.Plane(start3, Z), 0.6 * paper * u).ToNurbsCurve())
    tip = W3(wl[-1][0], wl[-1][1])
    k = len(wl) - 2
    while k > 0 and W3(wl[k][0], wl[k][1]).DistanceTo(tip) < tol:
        k -= 1
    back = W3(wl[k][0], wl[k][1]) - tip
    if back.Length > tol:
        back.Unitize()
        for ang in (0.35, -0.35):
            v = rg.Vector3d(back)
            v.Rotate(ang, Z)
            plan.append(rg.LineCurve(tip, tip + v * (2.5 * paper * u)))
    up = rg.Vector3d(bp.XAxis)
    if up.X < -1e-9 or (abs(up.X) <= 1e-9 and up.Y < 0):
        up.Reverse()
    th_ = 2.5 * paper * u
    if spiral:   # beyond the outer edge, by the first riser
        c0 = W3(spiral["centre"][0], spiral["centre"][1])
        rad = start3 - c0
        rad.Unitize()
        at_ = c0 + rad * (spiral["R"] * u + 0.05 * u + th_ * 0.6)
    else:
        at_ = start3 - bp.XAxis * (0.15 * u + th_ * 0.8)
    plan += bim.text_curves("UP", rg.Plane(at_, up, rg.Vector3d.CrossProduct(Z, up)), th_)

    # ── edges: stair sides (walkable side on the left: the right side runs up, the left side down) ──
    stair_edges = []
    if not spiral:
        inner_side = s
        want = {"both": (1, -1), "inner": (inner_side,), "outer": (-inner_side,), "none": ()}.get(str(sides or "both").strip().lower(), (1, -1))
        for sg in want:
            pts = []
            for p in side_pts[sg]:
                q = W3(p[0], p[1], p[2])
                if not pts or q.DistanceTo(pts[-1]) > tol:
                    pts.append(q)
            if sg > 0:
                pts.reverse()
            if sb and len(pts) > 1:      # steel and timber: on the stringers
                pts = ss.offset_points(pts, -sb / 2 * u, tol=tol)
            if len(pts) > 1:
                stair_edges.append(rg.PolylineCurve(pts))
    elif str(sides or "both").strip().lower() in ("both", "outer"):
        pts = []
        for p in side_pts[1]:
            q = W3(p[0], p[1], p[2])
            if not pts or q.DistanceTo(pts[-1]) > tol:
                pts.append(q)
        c3 = W3(spiral["centre"][0], spiral["centre"][1])
        if len(pts) > 1:
            dv = pts[1] - pts[0]
            if dv.X * (c3.Y - pts[0].Y) - dv.Y * (c3.X - pts[0].X) < 0:  # keep the stair (the centre) on the left
                pts.reverse()
            stair_edges.append(rg.PolylineCurve(pts))

    # ── void at the upper level, and its guard edges ──
    ztop = (z_from + H) * u
    eps = 0.0                    # rectangles sharing an edge union cleanly
    rects = []

    def rect(pts_local):
        return rg.PolylineCurve([rg.Point3d(W3(x, y).X, W3(x, y).Y, ztop) for x, y in pts_local + [pts_local[0]]])
    guard = []
    if not spiral:
        fvoid = []
        for f in flights:
            a, z0 = f["a"], f["z"]
            x_end = (a - 1) * G
            xc = max(0.0, (z_t - z0 - r) * G / r)
            if z0 + a * r <= z_t or xc >= x_end - 1e-6:
                fvoid.append(None)
                continue
            fvoid.append((xc, x_end))
            e = W / 2 + sb + eps
            rects.append(rect([flocal(f, xc - eps, -e), flocal(f, x_end + eps, -e), flocal(f, x_end + eps, e), flocal(f, xc - eps, e)]))
        for li, l in enumerate(landings):
            if l["z"] <= z_t:
                continue
            o, d, lf = l["origin"], l["dir"], leftof(l["dir"])

            def lp(x, y):
                return add2(add2(o, d, x), lf, y)
            e = sb + eps                 # out to the stringers' faces, like the flights
            rects.append(rect([lp(l["x0"] - e, l["y0"] - e), lp(l["x1"] + e, l["y0"] - e), lp(l["x1"] + e, l["y1"] + e), lp(l["x0"] - e, l["y1"] + e)]))
            if l["kind"] == "half" and gw > 0:   # the well between the flights either side of a half landing
                fi = next((i for i, f in enumerate(flights) if math.hypot(f["end"][0] - o[0], f["end"][1] - o[1]) < 1e-9), None)
                if fi is not None and fi + 1 < len(flights) and fvoid[fi] and fvoid[fi + 1]:
                    f1, f2 = flights[fi], flights[fi + 1]
                    lo_ = max(fvoid[fi][0] - (f1["a"] - 1) * G, -fvoid[fi + 1][1])
                    hi_ = min(0.0, -fvoid[fi + 1][0])
                    if hi_ - lo_ > 1e-6:
                        yw = sorted([s * (W / 2 + sb), s * (W / 2 + gw - sb)])
                        if yw[1] - yw[0] > 1e-6:
                            rects.append(rect([lp(lo_ - eps, yw[0] - eps), lp(hi_ + eps, yw[0] - eps), lp(hi_ + eps, yw[1] + eps), lp(lo_ - eps, yw[1] + eps)]))
        loops = list(rg.Curve.CreateBooleanUnion(rects, tol) or []) if len(rects) > 1 else rects
        if rects and not loops:
            loops = rects
            notes.append("the void parts could not be joined: given separately")
        fl_last = flights[-1]
        e = W / 2 + sb
        xe = (fl_last["a"] - 1) * G
        A3, B3 = W3(*flocal(fl_last, xe, -e)), W3(*flocal(fl_last, xe, e))
        A3.Z = B3.Z = ztop
        for lp_ in loops:
            ok, pl = lp_.TryGetPolyline()
            pts = simplify(list(pl) if ok else [lp_.PointAt(x) for x in lp_.DivideByCount(64, True)])
            if len(pts) < 3:
                continue
            if signed_area(pts) > 0:     # clockwise: the floor round the void is on the left
                pts.reverse()
            voids.append(rg.PolylineCurve(pts + [pts[0]]))
            g = open_at(pts, A3, B3)
            guard.append(rg.PolylineCurve(g) if g else rg.PolylineCurve(pts + [pts[0]]))
    else:
        sp_ = spiral
        c3 = W3(sp_["centre"][0], sp_["centre"][1])
        circ = rg.Circle(rg.Plane(rg.Point3d(c3.X, c3.Y, ztop), Z), sp_["R"] * u)
        voids.append(circ.ToNurbsCurve())
        bth = math.atan2(bp.XAxis.Y, bp.XAxis.X)
        pend = sp_["phi_end"]
        pts = []
        steps = 54
        for q in range(steps + 1):   # the circle from the platform's far edge round to the arrival
            ph = pend + math.pi / 2 + (1.5 * math.pi) * q / steps
            p2 = sp_["polar"](sp_["R"], ph)
            p = W3(p2[0], p2[1])
            pts.append(rg.Point3d(p.X, p.Y, ztop))
        p2 = sp_["polar"](max(sp_["rc"], 0.0), pend + math.pi / 2)
        pin = W3(p2[0], p2[1])
        pts.insert(0, rg.Point3d(pin.X, pin.Y, ztop))
        out_test = pts[1] + (pts[1] - rg.Point3d(c3.X, c3.Y, ztop))   # a point on the floor, outside the circle
        dv = pts[2] - pts[1]
        if dv.X * (out_test.Y - pts[1].Y) - dv.Y * (out_test.X - pts[1].X) < 0:
            pts.reverse()
        guard.append(rg.PolylineCurve(pts))
    edges += stair_edges + guard

    # ── record ──
    def m3(p):
        return bim.pt(p, u)
    rec = {"id": "ST%02d" % (si + 1), "type": t, "shape": kind, "turn": "left" if s > 0 else "right", "align": align or "centre",
           "width": W, "level": lo.get("name"), "top_level": hi.get("name") if hi else None, "base": z_from, "height": H,
           "risers": N, "riser": r, "going": G, "origin": m3(bp.Origin), "direction": [round(bp.XAxis.X, 6), round(bp.XAxis.Y, 6), 0.0],
           "checks": checks, "ok": ok_all, "site": st.get("name")}
    if not spiral:
        rec["flights"] = [{"risers": f["a"], "start": m3(W3(f["start"][0], f["start"][1], f["z"])), "direction": [round(V3(f["dir"]).X, 6), round(V3(f["dir"]).Y, 6), 0.0],
                           "length": (f["a"] - 1) * G, "z": z_from + f["z"]} for f in flights]
        rec["landings"] = []
        for l in landings:
            o, d, lf = l["origin"], l["dir"], leftof(l["dir"])
            cs = [add2(add2(o, d, x), lf, y) for x, y in ((l["x0"], l["y0"]), (l["x1"], l["y0"]), (l["x1"], l["y1"]), (l["x0"], l["y1"]))]
            rec["landings"].append({"points": [m3(W3(x, y, l["z"])) for x, y in cs], "z": z_from + l["z"], "length": l["length"], "kind": l["kind"]})
    else:
        rec["flights"] = [{"risers": f["a"], "z": z_from + f["z"]} for f in spiral["flights"]]
        rec["landings"] = [{"z": z_from + l["z"], "angle": math.degrees(l["span"]), "top": l["top"]} for l in spiral["landings"]]
        rec["centre"] = m3(W3(spiral["centre"][0], spiral["centre"][1], 0))
        rec["column"] = spiral["rc"]
        rec["radius"] = spiral["R"]
        rec["turns"] = spiral["phi_end"] / (2 * math.pi)
    rec["void"] = bim.curve_record(voids[-1], u) if voids else None
    stairs.append(bim.pack("stair", **rec))
    risers.append(N)
    riser.append(round(r * 1000, 2))
    going.append(round(G * 1000, 2))
    summaries.append((stair_edges, guard))

# ── info ──
lab = {"straight": "straight", "L": "L (quarter landing)", "U": "U (half landing)", "spiral": "spiral"}[kind]
lines = [
    "%d %s stair%s%s: %d risers of %.1f mm, going %.0f mm, %s (%s)" % (
        len(curves), lab, "" if len(curves) == 1 else "s", " (sample base line)" if sample else "", N, r * 1000, G * 1000,
        "flights " + " + ".join(str(a) for a in flight_risers), "landings " + ", ".join("%.2f m" % l["length"] for l in landings) if landings else "no landings"),
    "%s (z %.3f) to %s: %.3f m; %s, width %.2f m%s" % (
        lo.get("name"), z_from, ("%s (z %.3f)" % (hi.get("name"), float(hi["elevation"]))) if hi else "+%.3f m" % H,
        H, t["name"], W, (", column r %.2f m, %.2f turns" % (spiral["rc"], spiral["phi_end"] / (2 * math.pi))) if spiral else ""),
    "NCC first-pass checks, not certification (%s):" % ss.SOURCE[t["use"]] + (" spiral limits" if spiral else ""),
] + ["  " + ss.rule_text(c) for c in checks]
if not all(c["ok"] for c in checks if c["rule"] in ("riser", "going", "2R+G")):
    sug = ss.suggestion(H, t, kind == "spiral")
    if sug:
        lines.append("  a compliant option for %.3f m: %d risers of %.1f mm with a %.0f mm going" % (H, sug[0], sug[1] * 1000, sug[2] * 1000))
if spiral and t["use"] == "public":
    lines.append("  note: spiral stairs are generally not accepted as required exit stairs in public buildings (check NCC Vol 1)")
if summaries:
    se, gd = summaries[0]
    lines.append("Edges per stair: %d stair side%s, %d upper-floor void edge%s; void at %s for 2.0 m headroom under a %.0f mm floor" % (
        len(se), "" if len(se) == 1 else "s", len(gd), "" if len(gd) == 1 else "s", hi.get("name") if hi else "the top", FLOOR * 1000))
lines.append("%d solids, %d plan curves in %.0f ms" % (len(solids), len(plan), (time.time() - t_start) * 1000))
info = "\n".join(lines + notes)
