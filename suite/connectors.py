# NODE_INPUTS: members:list[object] | "member records (from beams_columns or structure_edit); a sample frame when unwired"
# NODE_INPUTS: type:str | "connector: auto picks one per joint; any other makes only that kind (every beam end as an end plate, fin plate or cleat)" default="auto" options="auto|base plate|cap plate|end plate|fin plate|cleat"
# NODE_INPUTS: thickness:float | "plate thickness (mm); 0 = by joint: base 25, cap 16, end 12, fin 10, cleat 10, splice 12" min=0 max=60 default=0
# NODE_INPUTS: bolt:str | "bolt size (and anchor size at base plates)" default="M20" options="M16|M20|M24"
# NODE_INPUTS: rows:int | "bolt rows (0 = as many as fit)" min=0 max=12 default=0, cols:int | "bolt columns (0 = by joint: 2 across end and base plates, 1 along fin plates and cleats)" min=0 max=6 default=0
# NODE_INPUTS: edge:float | "bolt edge distance (mm)" min=20 max=100 default=40, margin:float | "base plate beyond the column (mm)" min=0 max=300 default=100
# NODE_INPUTS: welds:bool | "preview fillet welds" default=false
# NODE_OUTPUTS: plates:list[Brep] | "plates, cleats and stiffeners", bolts:list[Brep] | "bolts and anchors", welds:list[Brep] | "fillet welds (with welds on)"
# NODE_OUTPUTS: solids:list[Brep] | "plates, bolts and welds together, for one Custom Preview", joints:list[str] | "joint records: id, kind, members, plate, bolts"
# NODE_OUTPUTS: schedule:list[str] | "one line per joint, e.g. BP A/1 GF: PL 505x508x25, 4 M24 anchors", colours:list[Color] | "preview colour per solid", info:str
# CATEGORY: Building elements
# Connectors: base plates with anchors, cap plates, column splices, end plates, fin plates and cleats where the frame's members meet, with bolts, optional welds and a schedule.
#
# Joints are found from the geometry, so they follow structure_edit: a column with no column below gets a base plate
# (anchor bolts at the lowest level), one with nothing above a cap plate, stacked columns a flange-plate splice that
# fits above the beams; a beam end near a column or a crossing beam meets its face (flange, web or flange tips, from the
# support's orientation). Auto puts end plates on column flanges and on hollow beams, fin plates on webs, beam-to-beam
# joints and timber beams (knife plates), and web plates where beams continue in line; at flange tips a fin plate
# reaches back to the web and an end plate or cleat gets a stiffener; on timber or concrete a fin plate stands on a
# T bracket. Concrete members are cast together: no connectors. Plates sit on the face; bolts run through the wall.
import math
import time

import Rhino.Geometry as rg
import System.Drawing

import alien_bim as ab
import alien_sections as S

t_start = time.time()
u = ab.units()
tol = ab.tol()
notes = []

BOLTS = {"M16": (0.016, 0.027, 0.010, 0.015), "M20": (0.020, 0.032, 0.013, 0.018), "M24": (0.024, 0.041, 0.015, 0.022)}
PITCH = {"M16": 0.060, "M20": 0.070, "M24": 0.080}  # bolt spacing, m
DEFAULT_T = {"base plate": 25, "cap plate": 16, "end plate": 12, "fin plate": 10, "cleat": 10, "splice": 12, "web splice": 12}
ABBR = {"base plate": "BP", "cap plate": "CP", "end plate": "EP", "fin plate": "FP", "cleat": "CL", "splice": "SP", "web splice": "WS"}
PLATE_C = System.Drawing.Color.FromArgb(205, 105, 55)
BOLT_C = System.Drawing.Color.FromArgb(45, 45, 52)
WELD_C = System.Drawing.Color.FromArgb(240, 200, 40)
WELD_LEG = 0.006
HOLLOW = S.BOX_SHAPES + S.TUBE_SHAPES
OPEN = S.I_SHAPES + S.C_SHAPES

recs = ab.read(members, "member")
if not recs and any(m is not None for m in (members or [])):
    notes.append("no member records on members: wire beams_columns or structure_edit")
elif not recs:  # unwired (an empty wired list looks the same to a script)
    recs = S.sample_frame()
    notes.append("sample frame")
mode = type if type in ("auto", "base plate", "cap plate", "end plate", "fin plate", "cleat") else "auto"
if type and mode != type:
    notes.append("type '%s' unknown: used auto" % type)
del type  # the input shadows the builtin, which Alien's output handling needs
size = bolt if bolt in BOLTS else "M20"
if bolt and size != bolt:
    notes.append("bolt '%s' unknown: used M20" % bolt)
D_B, S_B, K_B, M_B = BOLTS[size]
PITCH_B = PITCH[size]
RH = S_B * 0.54  # round head and nut, between across-flats and across-corners
E = max((edge if edge else 40.0) / 1000.0, RH + 0.004)
if edge and edge / 1000.0 < E - 1e-9:
    notes.append("edge distance raised to %d mm so %s heads stay on the plate" % (round(E * 1000), size))
MARGIN = max(0.0, (margin if margin is not None else 100.0) / 1000.0)
NROWS = max(0, int(rows or 0))
NCOLS = max(0, int(cols or 0))
Z = rg.Vector3d(0, 0, 1)


def tp_for(kind):
    return thickness / 1000.0 if thickness and thickness > 0 else DEFAULT_T[kind] / 1000.0


# ── geometry (metres in, model units out) ──

def M(p):
    return rg.Point3d(p.X * u, p.Y * u, p.Z * u)


def unit(v):
    v = rg.Vector3d(v)
    v.Unitize()
    return v


def box(origin, xa, ya, x0, x1, y0, y1, z0, z1):
    return ab.box(rg.Plane(M(origin), xa, ya), x0 * u, x1 * u, y0 * u, y1 * u, min(z0, z1) * u, max(z0, z1) * u)


def prism(pts, vec):
    """A planar polygon (metres) extruded square to its plane by vec (metres, along the plane's normal): welds and
    cleats. Extrusion.Create is ~3x faster than a swept surface + caps."""
    crv = rg.PolylineCurve([M(p) for p in pts] + [M(pts[0])])
    ok, plane = crv.TryGetPlane(tol)
    if not ok:
        return None
    h = vec.Length * u * (1.0 if plane.Normal * vec > 0 else -1.0)
    ext = rg.Extrusion.Create(crv, h, True)
    return ext.ToBrep() if ext else None


def fillet(p0, p1, a, b):
    """A fillet weld from p0 to p1 in the corner between two faces, legs along a and b."""
    return prism([p0, p0 + a * WELD_LEG, p0 + b * WELD_LEG], p1 - p0)


_protos = {}


def _proto(grip, anchor):
    r, so = D_B / 2.0, max(0.3 * D_B, 0.006)
    if anchor:  # nut and washer on the plate, rod down into the footing
        prof = [(0, M_B + so), (r, M_B + so), (r, M_B), (RH, M_B), (RH, 0), (r, 0), (r, -anchor), (0, -anchor)]
    else:  # head on the near face (z < 0), shank through the grip, nut beyond
        prof = [(0, -K_B), (RH, -K_B), (RH, 0), (r, 0), (r, grip), (RH, grip), (RH, grip + M_B), (r, grip + M_B),
                (r, grip + M_B + so), (0, grip + M_B + so)]
    zs = [z for _, z in prof]
    rev = rg.RevSurface.Create(rg.PolylineCurve([rg.Point3d(x * u, 0, z * u) for x, z in prof]),
                               rg.Line(rg.Point3d(0, 0, min(zs) * u), rg.Point3d(0, 0, max(zs) * u)))
    b = rg.Brep.CreateFromRevSurface(rev, False, False) if rev else None
    if b is not None and b.SolidOrientation == rg.BrepSolidOrientation.Inward:
        b.Flip()
    return b


def bolt_at(head, axis, grip, anchor=0.0):
    """A bolt with its head's bearing face at `head` (metres), shank along `axis` through `grip` (m); an anchor stands
    on the plate with its rod `anchor` m down."""
    key = (round(grip, 4), round(anchor, 4))
    if key not in _protos:
        _protos[key] = _proto(max(grip, 0.001), anchor)
    pr = _protos[key]
    if pr is None:
        return None
    b = pr.DuplicateBrep()
    b.Transform(rg.Transform.PlaneToPlane(rg.Plane.WorldXY, rg.Plane(M(head), axis)))
    return b


def spread(a, b, n):
    if n <= 1:
        return [(a + b) / 2.0]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def fit_rows(zone, minimum=1):
    """Bolt rows that fit along a zone (m) at the bolt pitch; NROWS when given."""
    if NROWS:
        return NROWS
    return max(minimum, min(12, int(math.floor(max(zone, 0.0) / PITCH_B + 1e-6)) + 1))


def mm(*xs):
    return "x".join("%d" % round(x * 1000) for x in xs)


# ── members ──

cols = [r for r in recs if r.get("role") == "column"]
beams = [r for r in recs if r.get("role") == "beam"]
others = [r for r in recs if r.get("role") not in ("column", "beam")]
if others:
    notes.append("%d braces/other members: no connectors yet" % len(others))
FR = {id(r): S.frame(r) for r in recs}
LOHI = {}
for c in cols:
    s, e, _, _, _ = FR[id(c)]
    LOHI[id(c)] = (s, e) if s.Z <= e.Z else (e, s)


def index_points(items, pts_of):
    idx = {}
    for it in items:
        for p in pts_of(it):
            idx.setdefault(S.cell(p.X, p.Y), []).append(it)
    return idx


def near(idx, p):
    return S.near(idx, p.X, p.Y)


def beam_cells(b):
    s, e = FR[id(b)][:2]
    n = max(1, int(s.DistanceTo(e) / (S.CELL / 2.0)) + 1)
    return [s + (e - s) * (t / float(n)) for t in range(n + 1)]


cidx = index_points(cols, lambda c: LOHI[id(c)])
bidx = index_points(beams, beam_cells)


def label(c):
    return "%s %s" % (c.get("grid") or c.get("id"), c.get("level") or "")


def short(cid):
    return cid[2:] if str(cid).startswith("C-") else str(cid)


def mat(r):
    return str(r.get("material") or (r.get("section") or {}).get("material") or "").lower()


plates, bolts, weld_list, joints, schedule = [], [], [], [], []
counts, made = {}, {"bolts": 0, "anchors": 0}
skipped_concrete = [0]


def add(kind, jid, mem_ids, at, plate_txt, pl, bl, wl, line, extra=None):
    pl = [p for p in pl if p is not None]
    bl = [b for b in bl if b is not None]
    wl = [w for w in wl if w is not None] if welds else []
    plates.extend(pl)
    bolts.extend(bl)
    weld_list.extend(wl)
    counts[kind] = counts.get(kind, 0) + 1
    anchors = bool(extra and extra.get("anchors"))
    made["anchors" if anchors else "bolts"] += len(bl)
    rec = {"alien": "joint", "id": jid, "kind": kind, "members": mem_ids, "at": S.coords(at), "plate": plate_txt, "plates": len(pl),
           "bolts": len(bl), "bolt": size if bl else None}
    if extra:
        rec.update(extra)
    joints.append(ab.dump(rec))  # not pack(): "kind" is one of the fields
    schedule.append(line)


# ── column ends: base plates, cap plates, splices ──

def outline_welds(c, P0, x, y, zdir):
    """Fillets round a column's foot or head on a plate (zdir: from the plate into the column)."""
    if not welds:
        return []
    spec = c.get("section") or {}
    shape = S.shape_of(spec)
    hb, hd = S.halves(spec)
    t, tf, tw = S.thick(spec)
    out = []
    if shape in OPEN:
        for sy in (1.0, -1.0):
            out.append(fillet(P0 + x * (-hb) + y * (sy * hd), P0 + x * hb + y * (sy * hd), zdir, y * sy))
        for sx in (1.0, -1.0):
            if shape in S.C_SHAPES and sx > 0:
                continue
            w = tw / 2.0 if shape in S.I_SHAPES else hb
            out.append(fillet(P0 + x * (sx * w) + y * (-(hd - tf)), P0 + x * (sx * w) + y * (hd - tf), zdir, x * (sx if shape in S.I_SHAPES else -1.0)))
    elif shape not in S.TUBE_SHAPES + S.ROUND_SHAPES:
        for sy in (1.0, -1.0):
            out.append(fillet(P0 + x * (-hb) + y * (sy * hd), P0 + x * hb + y * (sy * hd), zdir, y * sy))
            out.append(fillet(P0 + x * (sy * hb) + y * (-hd), P0 + x * (sy * hb) + y * hd, zdir, x * sy))
    else:  # round: eight chords
        for i in range(8):
            a0, a1 = 2 * math.pi * i / 8, 2 * math.pi * (i + 1) / 8
            p0 = P0 + x * (hd * math.cos(a0)) + y * (hd * math.sin(a0))
            p1 = P0 + x * (hd * math.cos(a1)) + y * (hd * math.sin(a1))
            out.append(fillet(p0, p1, zdir, unit((p0 + (p1 - p0) * 0.5) - P0)))
    return out


def base_plate(c, lowest):
    lo, _ = LOHI[id(c)]
    _, _, x, y, _ = FR[id(c)]
    spec = c.get("section") or {}
    hb, hd = S.halves(spec)
    tp = tp_for("base plate")
    m = MARGIN
    if m < E + RH + 0.005:
        m = E + RH + 0.005
        if "margin" not in str(notes):
            notes.append("base plate margin raised to %d mm to clear the anchors" % round(m * 1000))
    X, Y = hb + m, hd + m
    pl = [box(lo, x, y, -X, X, -Y, Y, -tp, 0.0)]
    bl = []
    for xi in spread(-(X - E), X - E, NCOLS or 2):
        for yj in spread(-(Y - E), Y - E, NROWS or 2):
            if abs(xi) < hb + RH + 0.003 and abs(yj) < hd + RH + 0.003:
                continue  # under the column
            p = lo + x * xi + y * yj
            if lowest:
                bl.append(bolt_at(p, Z, 0.0, anchor=tp + 0.05 + 12 * D_B))
            else:
                bl.append(bolt_at(p, Z * -1.0, tp + 0.012))
    wl = outline_welds(c, lo, x, y, Z)
    what = "%s anchors" % size if lowest else size
    add("base plate", "BP-" + short(c["id"]), [c["id"]], lo, mm(2 * X, 2 * Y, tp), pl, bl, wl,
        "BP %s: PL %s, %d %s" % (label(c), mm(2 * X, 2 * Y, tp), len([b for b in bl if b]), what), {"anchors": lowest})


def cap_plate(c):
    _, hi = LOHI[id(c)]
    _, _, x, y, _ = FR[id(c)]
    hb, hd = S.halves(c.get("section"))
    tp = tp_for("cap plate")
    pl = [box(hi, x, y, -hb, hb, -hd, hd, 0.0, tp)]
    wl = outline_welds(c, hi, x, y, Z * -1.0)
    lv = c.get("level_top") or c.get("level") or ""
    add("cap plate", "CP-%s-%s" % (c.get("grid") or c["id"], lv), [c["id"]], hi, mm(2 * hb, 2 * hd, tp), pl, [], wl,
        "CP %s %s: PL %s" % (c.get("grid") or c["id"], lv, mm(2 * hb, 2 * hd, tp)))


def splice(c, c2):
    _, hi = LOHI[id(c)]
    _, _, x, y, _ = FR[id(c)]
    spec = c.get("section") or {}
    shape = S.shape_of(spec)
    jid = "SP-" + short(c2["id"])
    if shape not in OPEN or S.shape_of(c2.get("section")) not in OPEN:
        add("splice", jid, [c["id"], c2["id"]], hi, "", [], [], [],
            "SP %s: site butt weld (%s)" % (label(c2), spec.get("name", shape)), {"welded": True})
        return
    hb, hd = S.halves(spec)
    t, tf, tw = S.thick(spec)
    tp = tp_for("splice")
    # the plates straddle the joint, so they must fit between it and the tops of beams framing in just below
    tops = [q.Z + S.vertical_half(b) for b in near(bidx, hi) for q in FR[id(b)][:2]
            if math.hypot(q.X - hi.X, q.Y - hi.Y) <= S.radius(spec) + 0.35 and hi.Z - 1.5 <= q.Z <= hi.Z + 0.5]
    nr = NROWS or 2
    if tops:
        room = hi.Z - max(tops) - 0.01
        nr = min(nr, int(math.floor((room - 0.005 - 2 * E) / PITCH_B + 1e-6)) + 1 if room >= 0.005 + 2 * E else 0)
        if nr < 1:
            made["no room"] = made.get("no room", 0) + 1
            add("splice", jid, [c["id"], c2["id"]], hi, "", [], [], [],
                "SP %s: no room for flange plates above the beams (%d mm): site butt weld" % (label(c2), max(0, round(room * 1000))),
                {"welded": True})
            return
    half = 0.005 + E + (nr - 1) * PITCH_B + E
    pl, bl = [], []
    for sy in (1.0, -1.0):
        nrm = y * sy
        pl.append(box(hi, x, Z, -hb, hb, -half, half, -sy * hd, -sy * (hd + tp)))  # plane x,Z: its normal is -y
        for xi in spread(-(hb - E), hb - E, NCOLS or 2):
            for side in (1.0, -1.0):
                for j in range(nr):
                    zz = side * (0.005 + E + j * PITCH_B)
                    bl.append(bolt_at(hi + x * xi + Z * zz + nrm * (hd + tp), nrm * -1.0, tp + tf))
    add("splice", jid, [c["id"], c2["id"]], hi, mm(2 * hb, 2 * half, tp), pl, bl, [],
        "SP %s: 2 PL %s flange plates, %d %s" % (label(c2), mm(2 * hb, 2 * half, tp), len(bl), size))


lowest_z = min((LOHI[id(c)][0].Z for c in cols), default=0.0)
for c in cols:
    if mat(c) == "concrete":
        skipped_concrete[0] += 1
        continue
    lo, hi = LOHI[id(c)]
    below = [o for o in near(cidx, lo) if o is not c and LOHI[id(o)][1].DistanceTo(lo) < 0.03]
    above = [o for o in near(cidx, hi) if o is not c and LOHI[id(o)][0].DistanceTo(hi) < 0.03]
    if not below and mode in ("auto", "base plate"):
        base_plate(c, abs(lo.Z - lowest_z) < 0.05)
    if not above and mode in ("auto", "cap plate"):
        cap_plate(c)
    if above and mode == "auto" and mat(above[0]) != "concrete":
        if mat(c) == "timber" or mat(above[0]) == "timber":
            made["timber splices"] = made.get("timber splices", 0) + 1
        else:
            splice(c, above[0])


# ── beam ends ──

def web(b, lateral):
    """(centre, half, back) of what plates and cleats bolt to, measured along `lateral` from the beam's axis: an I's web
    is central; a channel's or angle's is at its back, where a single plate goes (back = that side, +1 or -1); boxes,
    timber and solid sections take plates on their outer faces (half = half their width)."""
    spec = b.get("section") or {}
    shape = S.shape_of(spec)
    t, tf, tw = S.thick(spec)
    hb, _ = S.halves(spec)
    if shape in S.C_SHAPES + S.L_SHAPES:
        sg = 1.0 if FR[id(b)][2] * lateral >= 0 else -1.0  # the section's local x: its toes point that way
        w = tw if shape in S.C_SHAPES else t
        return sg * (-hb + w / 2.0), w / 2.0, -sg
    if shape in S.I_SHAPES + ("t", "tee"):
        return 0.0, tw / 2.0, 1.0
    return 0.0, hb, 1.0


def web_zone(b):
    """Clear height of the beam's web for bolts and plates (m)."""
    spec = b.get("section") or {}
    hv = S.vertical_half(b)
    t, tf, tw = S.thick(spec)
    shape = S.shape_of(spec)
    if shape in OPEN:
        return 2 * (hv - tf) - 0.02
    if shape in HOLLOW:
        return 2 * (hv - t) - 0.02
    return 2 * hv - 0.05


def support_grip(f, tp):
    """What a bolt through the support passes: its wall, a stiffener across flange tips, or (solid) an anchor's depth."""
    if f["name"] == "flange tips":
        return tp
    return f["wall"] if f["wall"] else 0.1


def stiffener(f, tp, w, h):
    """A plate across an I's flange tips (in the face plane, behind it) for an end plate or cleat to bolt to."""
    sup = f["support"]
    hb, hd = S.halves(sup.get("section"))
    t, tf, tw = S.thick(sup.get("section"))
    ys = f["ys"] - f["n"] * (f["ys"] * f["n"])
    ys.Unitize()
    other = unit(rg.Vector3d.CrossProduct(f["n"], ys))
    along = h / 2.0 if abs(other * Z) > 0.9 else w / 2.0
    return box(f["origin"], ys, other, -(hd - tf), hd - tf, -along, along, -tp, 0.0)


def end_plate(b, k, Ept, v, f, jid, where):
    n = f["n"]
    across = unit(rg.Vector3d.CrossProduct(Z, n))
    O = S.hit(Ept, v, f["origin"], n) or f["origin"]
    spec = b.get("section") or {}
    hb, _ = S.halves(spec)
    hv = S.vertical_half(b)
    t, tf, tw = S.thick(spec)
    tp = tp_for("end plate")
    hollow = S.shape_of(spec) in HOLLOW
    if hollow:  # bolts can't go inside a tube: the plate reaches past its walls
        w = 2 * hb + 4 * E
        xs = [-(hb + E), hb + E]
        zone = hv - E
    else:
        w = 2 * hb + 0.02
        if f["width"]:
            w = min(w, f["width"] - 0.01)
        xs = spread(-(w / 2 - E), w / 2 - E, NCOLS or 2)
        zone = hv - (tf if S.shape_of(spec) in OPEN else 0.0) - RH - 0.005
    h = 2 * hv
    pl = [box(O, across, Z, -w / 2, w / 2, -h / 2, h / 2, 0.0, tp)]
    if f["name"] == "flange tips":
        pl.append(stiffener(f, tp, w, h))
    nr = fit_rows(2 * zone, 2)
    bl = []
    for a in xs:
        for zz in spread(-zone, zone, nr):
            bl.append(bolt_at(O + across * a + Z * zz + n * tp, n * -1.0, tp + support_grip(f, tp)))
    wl = []
    if welds:
        front = O + n * tp
        if S.shape_of(spec) in OPEN:
            for sz in (1.0, -1.0):
                zf = sz * (hv - tf)
                wl.append(fillet(front + across * (-hb) + Z * zf, front + across * hb + Z * zf, n, Z * (-sz)))
            c, hw, _ = web(b, across)
            for sa in (1.0, -1.0):
                wl.append(fillet(front + across * (c + sa * hw) + Z * (-(hv - tf)), front + across * (c + sa * hw) + Z * (hv - tf), n, across * sa))
        else:
            for sz in (1.0, -1.0):
                wl.append(fillet(front + across * (-hb) + Z * (sz * hv), front + across * hb + Z * (sz * hv), n, Z * sz))
                wl.append(fillet(front + across * (sz * hb) + Z * (-hv), front + across * (sz * hb) + Z * hv, n, across * sz))
    plate_txt = mm(w, h, tp)
    add("end plate", jid, [b["id"], f["support"]["id"]], O, plate_txt, pl, bl, wl,
        "EP %s: PL %s%s, %d %s" % (where, plate_txt, " + stiffener" if len(pl) > 1 else "", len(bl), size), {"face": f["name"]})


def fin_plate(b, k, Ept, v, f, jid, where):
    n = f["n"]
    along = v * -1.0  # into the beam
    lateral = unit(rg.Vector3d.CrossProduct(along, Z))
    O = S.hit(Ept, v, f["axis"] + n * f["weld"], n) or (f["axis"] + n * f["weld"])
    spec = b.get("section") or {}
    timber = mat(b) == "timber" or S.shape_of(spec) not in OPEN + HOLLOW
    t, tf, tw = S.thick(spec)
    tp = tp_for("fin plate")
    c, hw, back = web(b, lateral)
    l0, l1 = (-tp / 2.0, tp / 2.0) if timber else (c + back * hw, c + back * (hw + tp))  # timber: a knife plate in a slot
    g = max(0.0, (Ept - O) * along)
    nc = NCOLS or 1
    L = g + E + (nc - 1) * PITCH_B + E
    hmax = web_zone(b)
    if f["clear"] and abs(f["ys"] * Z) > 0.9:  # between a supporting beam's flanges
        hmax = min(hmax, f["clear"])
    nr = fit_rows(hmax - 2 * E, 2 if hmax - 2 * E >= PITCH_B else 1)
    h = (nr - 1) * PITCH_B + 2 * E
    if NROWS and h > hmax + 1e-6 and "rows" not in str(notes):
        notes.append("%d bolt rows make fin plates taller than some beam webs" % NROWS)
    round_ = S.shape_of(f["support"].get("section")) in S.TUBE_SHAPES + S.ROUND_SHAPES

    def behind(l):  # a round support's wall lies behind the tangent plane at a plate's offset
        return f["dist"] - math.sqrt(max(f["dist"] ** 2 - l * l, 0.0)) if round_ else 0.0

    pl = [box(O, along, Z, -behind(max(abs(l0), abs(l1))), L, -h / 2, h / 2, l0, l1)]  # plane (along, Z): normal `lateral`
    bl = []
    solid_sup = f["wall"] is None and f["name"] != "flange tips"
    if solid_sup:  # timber or concrete: a T bracket, its flange screwed or anchored to the face, the fin welded to it
        mid = (l0 + l1) / 2.0
        pl.append(box(O, along, Z, 0.0, tp, -h / 2, h / 2, mid - 2 * E, mid + 2 * E))
        for side in (1.0, -1.0):
            for zz in spread(-(h / 2 - E), h / 2 - E, nr):
                bl.append(bolt_at(O + along * tp + lateral * (mid + side * (tp / 2 + E)) + Z * zz, along * -1.0, tp + 0.1))
    hb, _ = S.halves(spec)
    for i in range(nc):
        for zz in spread(-(h / 2 - E), h / 2 - E, nr):
            a = g + E + i * PITCH_B
            if timber:
                bl.append(bolt_at(O + along * a + Z * zz + lateral * hb, lateral * -1.0, 2 * hb))
            else:
                bl.append(bolt_at(O + along * a + Z * zz + lateral * l1, lateral * -back, tp + 2 * hw))
    wl = []
    if welds:
        for sl, side in ((l1, back), (l0, -back)) if not timber else ((l1, 1.0), (l0, -1.0)):
            p0 = O + lateral * sl + Z * (-h / 2) - along * behind(sl)
            wl.append(fillet(p0, p0 + Z * h, along, lateral * side))
    plate_txt = mm(L, h, tp)
    add("fin plate", jid, [b["id"], f["support"]["id"]], O, plate_txt, pl, bl, wl,
        "FP %s: PL %s%s%s, %d %s" % (where, plate_txt, " knife plate" if timber else "",
                                     " on a T bracket" if solid_sup else "", len(bl), size), {"face": f["name"]})


def cleat(b, k, Ept, v, f, jid, where):
    n = f["n"]
    along = v * -1.0
    lateral = unit(rg.Vector3d.CrossProduct(along, Z))
    O = S.hit(Ept, v, f["origin"], n) or f["origin"]
    spec = b.get("section") or {}
    tp = tp_for("cleat")
    c, hw, back = web(b, lateral)
    sides = (back,) if S.shape_of(spec) in S.C_SHAPES + S.L_SHAPES else (1.0, -1.0)  # a channel takes one, behind its web
    g = max(0.0, (Ept - O) * along)
    nc = NCOLS or 1
    Lc = max(g + E + (nc - 1) * PITCH_B + E, tp + 0.03)
    ws = max(2 * E + 0.01, tp + 2 * RH + 0.01)  # the leg on the support
    if f["name"] in ("web", "flange tips") and f["width"]:  # between the support's flanges
        ws = max(tp + 0.02, min(ws, f["width"] / 2.0 - abs(c) - hw - 0.005))
    leg_bolts = ws >= tp + 2 * RH + 0.005
    hmax = web_zone(b)
    if f["clear"] and abs(f["ys"] * Z) > 0.9:
        hmax = min(hmax, f["clear"])
    nr = fit_rows(hmax - 2 * E, 2 if hmax - 2 * E >= PITCH_B else 1)
    h = (nr - 1) * PITCH_B + 2 * E
    pl = []
    for sg in sides:
        def P(a, d):
            return O + along * a + lateral * (c + sg * (hw + d)) + Z * (-h / 2)
        pts = [P(0, 0), P(Lc, 0), P(Lc, tp), P(tp, tp), P(tp, ws), P(0, ws)]
        pl.append(prism(pts, Z * h))
    if f["name"] == "flange tips":
        pl.append(stiffener(f, tp, 2 * (abs(c) + hw + ws), h))
    bl = []
    zs = spread(-(h / 2 - E), h / 2 - E, nr)
    head = sides[0]
    for i in range(nc):
        for zz in zs:
            a = g + E + i * PITCH_B
            bl.append(bolt_at(O + along * a + Z * zz + lateral * (c + head * (hw + tp)), lateral * -head,
                              tp * len(sides) + 2 * hw))
    for sg in sides if leg_bolts else ():
        for zz in zs:
            bl.append(bolt_at(O + n * tp + lateral * (c + sg * (hw + tp + (ws - tp) / 2)) + Z * zz, n * -1.0, tp + support_grip(f, tp)))
    if not leg_bolts and "cleat legs" not in str(notes):
        notes.append("some cleat legs are too narrow between the support's flanges for bolts: welded")
    plate_txt = "%dL %s" % (len(sides), mm(ws, Lc, tp))
    add("cleat", jid, [b["id"], f["support"]["id"]], O, plate_txt, pl, bl, [],
        "CL %s: %d L %s x %d long, %d %s" % (where, len(sides), mm(ws, Lc, tp), round(h * 1000), len(bl), size), {"face": f["name"]})


def web_splice(b, Eb, o, Eo):
    """Two beams meeting in line: web plates both sides (I, channel) or a knife plate (timber)."""
    s, e, _, _, z = FR[id(b)]
    along = unit(Eo - Eb) if Eo.DistanceTo(Eb) > 1e-6 else z
    lateral = unit(rg.Vector3d.CrossProduct(along, Z))
    C = Eb + (Eo - Eb) * 0.5
    spec = b.get("section") or {}
    timber = mat(b) == "timber"
    if not timber and S.shape_of(spec) not in OPEN:
        add("web splice", "WS-%s+%s" % (b["id"], o["id"]), [b["id"], o["id"]], C, "", [], [], [],
            "WS %s + %s: site butt weld" % (b["id"], o["id"]), {"welded": True})
        return
    tp = tp_for("web splice")
    nc = NCOLS or 2
    gap = Eb.DistanceTo(Eo) / 2.0
    half = gap + E + (nc - 1) * PITCH_B + E
    hmax = min(web_zone(b), web_zone(o))
    nr = fit_rows(hmax - 2 * E, 2 if hmax - 2 * E >= PITCH_B else 1)
    h = (nr - 1) * PITCH_B + 2 * E
    c, hw, _ = web(b, lateral)
    hb = S.halves(spec)[0]
    if not timber:  # beams framing in here bear on the splice plates, not the bare web
        for m in (b, o):
            covered.setdefault(m["id"], []).append((C, half, tp))
    pl, bl = [], []
    sides = (0.0,) if timber else (1.0, -1.0)
    for sg in sides:
        a0, a1 = (-tp / 2, tp / 2) if timber else (c + sg * hw, c + sg * (hw + tp))
        pl.append(box(C, along, Z, -half, half, -h / 2, h / 2, a0, a1))
    for side in (1.0, -1.0):
        for i in range(nc):
            a = side * (gap + E + i * PITCH_B)
            for zz in spread(-(h / 2 - E), h / 2 - E, nr):
                if timber:
                    bl.append(bolt_at(C + along * a + Z * zz + lateral * hb, lateral * -1.0, 2 * hb))
                else:
                    bl.append(bolt_at(C + along * a + Z * zz + lateral * (c + hw + tp), lateral * -1.0, 2 * tp + 2 * hw))
    add("web splice", "WS-%s+%s" % (b["id"], o["id"]), [b["id"], o["id"]], C, mm(2 * half, h, tp), pl, bl, [],
        "WS %s + %s: %d PL %s, %d %s" % (b["id"], o["id"], len(pl), mm(2 * half, h, tp), len(bl), size))


BEAM_MODES = ("auto", "end plate", "fin plate", "cleat")
free = 0
covered = {}      # beam id -> [(centre, half length, thickness)] of web splice plates on it
found, in_line = [], []
for b in beams if mode in BEAM_MODES else []:
    s, e, _, _, bz = FR[id(b)]
    if s.DistanceTo(e) < 1e-6:
        continue
    hv = S.vertical_half(b)
    for k, (Ept, Opt) in enumerate(((s, e), (e, s))):
        v = unit(Ept - Opt)
        best = None
        for c in near(cidx, Ept):
            lo, hi = LOHI[id(c)]
            if not (lo.Z - hv - 0.01 <= Ept.Z <= hi.Z + hv + 0.01):
                continue
            ap = S.axis_point(c, Ept)
            if math.hypot(Ept.X - ap.X, Ept.Y - ap.Y) > S.radius(c.get("section")) + 0.3 or not S.covers(c, Ept, v):
                continue
            f = S.face(c, v, b, Ept)
            if f:
                gap = (Ept - f["origin"]) * f["n"]
                if -0.05 <= gap <= 0.3 and (best is None or abs(gap) < best[0]):
                    best = (abs(gap), c, f)
        if best is None:
            for o in near(bidx, Ept):
                if o is b:
                    continue
                os_, oe, _, _, oz = FR[id(o)]
                if abs(oz * v) > 0.5:
                    continue
                ap = S.axis_point(o, Ept)
                tpar = (ap - os_) * oz
                reach = max(0.01, 0.5 * S.extent_along(b, oz))  # the support must carry at least half the beam's width
                if tpar < -reach or tpar > os_.DistanceTo(oe) + reach or abs(Ept.Z - ap.Z) > S.vertical_half(o) + hv:
                    continue
                f = S.face(o, v, b, Ept)
                if f:
                    gap = (Ept - f["origin"]) * f["n"]
                    if -0.05 <= gap <= 0.3 and (best is None or abs(gap) < best[0]):
                        best = (abs(gap), o, f)
        names = b.get("node_names") or ["start", "end"]
        node = names[k] if k < len(names) else ("start", "end")[k]
        if best is None:
            partner = None
            for o in near(bidx, Ept):
                if o is b or abs(FR[id(o)][4] * v) < 0.95:
                    continue
                for q in FR[id(o)][:2]:
                    if q.DistanceTo(Ept) <= 0.1:
                        partner = (o, q)
            if partner and mode == "auto":
                o, q = partner
                if str(b["id"]) < str(o["id"]) and not (mat(b) == "concrete" or mat(o) == "concrete"):
                    in_line.append((b, Ept, o, q))
            elif not partner:
                free += 1
            continue
        found.append((b, k, Ept, v, best[1], best[2], node))

for args in in_line:
    web_splice(*args)

for b, k, Ept, v, sup, f, node in found:
    if mat(b) == "concrete":  # cast with what it frames into
        skipped_concrete[0] += 1
        continue
    kind = mode
    if mode == "auto":
        if mat(b) == "timber":
            kind = "fin plate"
        elif S.shape_of(b.get("section")) in HOLLOW:  # an end plate past the walls, if the face is wide enough
            need = 2 * S.halves(b.get("section"))[0] + 4 * E
            kind = "end plate" if not f["width"] or f["width"] >= need - 1e-6 else "fin plate"
        elif sup.get("role") == "column" and f["name"] == "flange":
            kind = "end plate"
        else:
            kind = "fin plate"
    for C, half, tsp in covered.get(sup["id"], []):  # a splice plate over the supporting web: bear on it
        if f["name"] in ("web", "flange tips") and abs((Ept - C) * S.frame(sup)[4]) <= half + 0.05:
            f = dict(f)
            f["weld"] += tsp
            if f["name"] == "web":
                f["dist"] += tsp
                f["origin"] = f["axis"] + f["n"] * f["dist"]
            break
    jid = "%s-%s@%s" % (ABBR[kind], b["id"], node)
    where = "%s @ %s -> %s %s" % (b["id"], node, sup["id"], f["name"])
    {"end plate": end_plate, "fin plate": fin_plate, "cleat": cleat}[kind](b, k, Ept, v, f, jid, where)

solids = plates + bolts + weld_list
colours = [PLATE_C] * len(plates) + [BOLT_C] * len(bolts) + [WELD_C] * len(weld_list)
welds = weld_list

order = ["base plate", "cap plate", "splice", "end plate", "fin plate", "cleat", "web splice"]
parts = ["%d %s%s" % (counts[kd], kd, "s" if counts[kd] != 1 else "") for kd in order if counts.get(kd)]
info = "%d joints: %s; %d plates, %d bolts%s%s; %d ms" % (
    len(joints), ", ".join(parts) or "none", len(plates), made["bolts"],
    " + %d %s anchors" % (made["anchors"], size) if made["anchors"] else "",
    ", %d welds" % len(weld_list) if weld_list else "", (time.time() - t_start) * 1000)
if free:
    notes.append("%d beam ends meet nothing (free ends, or primaries carrying a beam past a node)" % free)
if made.get("no room"):
    notes.append("%d column splices have no room for flange plates above the beams: site welded (a smaller edge distance "
                 "or bolt, or a deeper top offset, makes room)" % made["no room"])
if made.get("timber splices"):
    notes.append("%d timber column joints stacked without splice hardware" % made["timber splices"])
if skipped_concrete[0]:
    notes.append("%d concrete joints left monolithic (no connectors)" % skipped_concrete[0])
if mode != "auto":
    notes.append("only %s joints (auto makes every kind)" % mode)
if notes:
    info += "\n" + "\n".join(notes)
