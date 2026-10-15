"""
Alien envelope helpers: slab and roof types (layered build-ups), slabs at levels, and roofs over outlines (flat,
skillion, gable, hip, dutch gable). Used by slab_types, slabs, roof_types and roofs; keep it next to alien_bim.py and
alien_walls.py.

Layers run top -> bottom; in the text form thicknesses are mm ("concrete 200*; ..."), in records metres; * marks the
structure (core). Hip, gable and dutch-gable faces come from a weighted straight skeleton: every eave's wavefront moves
inward as the roof rises (cot(pitch) metres per metre of height), a gable edge stays put and becomes a vertical end. A
roof layer is the top surface dropped by the layers above it and thickness / cos(pitch) deep, cut plumb at the edges.

Record kinds added here (lengths in metres, world coordinates; geometry pins in model units):
  slab_type  name, preset, layers [{name, material, thickness, function, core}], thickness, finish, soffit
  roof_type  name, preset, layers, thickness, min_pitch, max_pitch (degrees, what the covering allows), colour
             ([r, g, b] of the top layer or null)
  slab       id, level, level_code, elevation, top, bottom, thickness, setdown, type (slab_type), outline (curve record
             at the top, after the edge offset), openings [curve records that cut it], area (net m2), perimeter,
             edge_offset, site
  roof       id, form, pitch, level, level_code, elevation, outline (wall line), footprint (eaves line), overhang,
             gable_overhang, type (roof_type), thickness, eave (z of the top at the eaves), ridge (highest z), area (on
             slope, m2), plan_area, faces [{points, pitch, aspect (bearing it faces, degrees from true north), area}],
             lines {ridge, hip, valley, eave: [[p, q], ...]}, site
"""
import math
import re
from collections import OrderedDict

import Rhino.Geometry as rg
import System.Drawing

import alien_bim as bim
import alien_walls as aw

Z = rg.Vector3d.ZAxis

# ── layers ───────────────────────────────────────────────────

# Floor build-ups, top -> bottom, mm; * marks the structure.
SLAB_PRESETS = OrderedDict([
    ("concrete 200", "concrete slab 200*"),
    ("post-tensioned 250", "post-tensioned concrete slab 250*"),
    ("hollowcore 200 + topping", "concrete topping 60; hollowcore planks 200*"),
    ("CLT 180 + screed", "screed 50; acoustic underlay 10; CLT panel 180*"),
    ("timber joist floor", "particleboard flooring 19; timber joists 240*; plasterboard ceiling 13"),
    ("waffle 300", "concrete topping 75; waffle ribs and pods 225*"),
    ("steel deck composite 150", "composite concrete slab 149*; steel decking 1"),
    ("slab on ground 100", "concrete slab 100*; vapour barrier 0.2; sand bed 50"),
])

# Finishes go on top of the build-up, soffits replace (or add) the ceiling under it.
FINISHES = OrderedDict([
    ("none", ""), ("polished concrete", ""), ("tiles", "floor tiles and bed 20"), ("timber boards", "timber floorboards 19"),
    ("carpet", "carpet and underlay 12"), ("vinyl", "vinyl 3"), ("screed", "screed 40"),
    ("raised floor", "raised access floor 150"),
])
SOFFITS = OrderedDict([
    ("as built", None), ("exposed", ""), ("plasterboard", "plasterboard ceiling 13"),
    ("suspended ceiling", "ceiling void 300; plasterboard ceiling 13"), ("render", "render 10"),
    ("acoustic panels", "acoustic ceiling panels 25"),
])

# Roof build-ups, top -> bottom, mm, with the pitches the covering allows (degrees).
ROOF_PRESETS = OrderedDict([
    ("metal deck", ("corrugated metal roofing 20; roof battens 40; insulation blanket 55; rafters 140*", 5, 90)),
    ("metal deck low pitch", ("metal deck 41; insulation blanket 55; steel purlins 150*", 1, 90)),
    ("concrete tile", ("concrete roof tiles 50; tile battens 25; sarking 1; rafters 140*", 15, 75)),
    ("terracotta tile", ("terracotta roof tiles 45; tile battens 25; sarking 1; rafters 140*", 15, 75)),
    ("slate", ("slate 10; slate battens 25; sarking 1; rafters 140*", 20, 90)),
    ("membrane on concrete", ("gravel ballast 50; XPS insulation 100; waterproof membrane 5; screed to falls 50; "
                              "concrete roof slab 200*", 0, 5)),
    ("green roof", ("vegetation 50; growing medium 150; filter fabric 2; drainage cell 30; root barrier membrane 5; "
                    "XPS insulation 100; concrete roof slab 200*", 0, 30)),
])

ROOF_COLOURS = OrderedDict([
    ("as built", None), ("charcoal", (62, 64, 68)), ("dark grey", (92, 96, 100)), ("mid grey", (140, 142, 140)),
    ("light grey", (196, 198, 196)), ("off-white", (232, 230, 220)), ("red", (150, 60, 45)),
    ("terracotta", (176, 90, 60)), ("green", (70, 100, 70)), ("blue", (60, 80, 110)),
])

# Layer name -> (material, function): checked before alien_walls' wall words, first match wins.
_WORDS = [
    (r"ceiling void|plenum|\bvoid\b|air gap", "air", "air"),
    (r"ceiling panel|acoustic ceiling|ceiling tile", "ceiling panel", "ceiling"),
    (r"vegetat|sedum|planting|\bgrass|turf", "vegetation", "finish"),
    (r"growing|substrate|\bsoil\b", "soil", "finish"),
    (r"drainage", "drainage", "drainage"),
    (r"filter|geotextile|fabric", "membrane", "membrane"),
    (r"gravel|ballast|pebble", "gravel", "finish"),
    (r"\bsand\b|bedding", "sand", "fill"),
    (r"\bmetal\b|decking|sheeting|corrugated|klip|standing seam|roofing", "metal deck", "finish"),
    (r"\bsteel\b", "steel", "structure"),
    (r"joist|rafter|purlin|truss|batten", "timber", "structure"),
    (r"terracotta|clay til", "terracotta", "finish"),
    (r"\bslate\b", "slate", "finish"),
    (r"roof til|concrete til", "roof tile", "finish"),
    (r"carpet", "carpet", "finish"),
    (r"vinyl|\blino", "vinyl", "finish"),
    (r"underlay|acoustic mat|rubber", "underlay", "acoustic"),
    (r"raised|access floor", "access floor", "finish"),
    (r"floor ?boards?|flooring|parquet|particleboard|chipboard", "timber floor", "finish"),
    (r"floor til|tiles? and bed|ceramic|porcelain", "tile", "finish"),
    (r"screed", "screed", "finish"),
    (r"hollow ?core|waffle|post.?tension|topping", "concrete", "structure"),
]

_COLOURS = OrderedDict([
    ("metal deck", (104, 110, 118)), ("terracotta", (176, 90, 60)), ("roof tile", (112, 110, 106)),
    ("slate", (66, 72, 82)), ("screed", (198, 196, 188)), ("carpet", (122, 110, 120)), ("vinyl", (182, 172, 158)),
    ("underlay", (72, 74, 78)), ("sand", (216, 198, 154)), ("gravel", (152, 150, 142)), ("soil", (112, 86, 60)),
    ("vegetation", (92, 146, 74)), ("drainage", (62, 64, 68)), ("timber floor", (186, 136, 84)),
    ("access floor", (192, 196, 200)), ("ceiling panel", (232, 230, 222)), ("fascia", (226, 224, 216)),
    ("barge", (226, 224, 216)), ("gutter", (206, 208, 206)),
])


def material_of(name):
    s = str(name).lower()
    for pattern, material, function in _WORDS:
        if re.search(pattern, s):
            break
    else:
        material, function = aw.material_of(s)
    if "ceiling" in s and function != "air":
        function = "ceiling"
    return material, function


def colour(material, alpha=None):
    """Preview colour of a layer material (air is see-through)."""
    m = str(material or "").lower()
    for key, rgb in _COLOURS.items():
        if key in m:
            return System.Drawing.Color.FromArgb(255 if alpha is None else alpha, *rgb)
    return aw.colour(m, alpha)


def parse_layers(text):
    """'screed 50; CLT 180*; plasterboard ceiling 13' -> (layers top -> bottom, problems). Thickness in mm unless it
    says m or cm; * marks the core (else the thickest structural layer); ; , | or new lines separate layers."""
    layers, problems = [], []
    for tok in re.split(r"[;,|\n]+", str(text or "")):
        tok = tok.strip()
        if not tok:
            continue
        core = "*" in tok
        tok = tok.replace("*", " ").strip()
        m = re.search(r"(-?\d+(?:\.\d+)?)\s*(mm|cm|m)?\b", tok)
        if not m:
            problems.append("no thickness in '%s'" % tok)
            continue
        mm = float(m.group(1)) * {"mm": 1.0, "cm": 10.0, "m": 1000.0}[m.group(2) or "mm"]
        name = re.sub(r"\s+", " ", (tok[:m.start()] + " " + tok[m.end():]).strip()) or "layer"
        if mm <= 0 or mm > 3000:
            problems.append("'%s': %g mm is not a layer thickness (0-3000 mm)" % (tok, mm))
            continue
        material, function = material_of(name)
        layers.append({"name": name, "material": material, "thickness": round(mm / 1000.0, 6), "function": function, "core": core})
    if layers and not any(l["core"] for l in layers):
        structural = [l for l in layers if l["function"] == "structure"] or [l for l in layers if l["material"] != "air"] or layers
        max(structural, key=lambda l: l["thickness"])["core"] = True
    return layers, problems


def layers_text(layers):
    return aw.layers_text(layers)


def make_type(kind, name, layers, preset=None, **extra):
    t = {"alien": kind, "name": name, "preset": preset, "layers": layers,
         "thickness": round(sum(float(l["thickness"]) for l in layers), 6)}
    t.update(extra)
    return t


def preset_key(presets, text):
    s = str(text or "").strip().lower()
    return next((k for k in presets if k.lower() == s), None)


def default_slab_type():
    layers, _ = parse_layers(SLAB_PRESETS["concrete 200"])
    return make_type("slab_type", "Concrete 200", layers, "concrete 200", finish="none", soffit="as built")


def default_roof_type():
    text, lo, hi = ROOF_PRESETS["metal deck"]
    layers, _ = parse_layers(text)
    return make_type("roof_type", "Metal deck", layers, "metal deck", min_pitch=lo, max_pitch=hi, colour=None)


def type_of(value, kind):
    """The first `kind` record in a value with at least one real layer, else the default."""
    t = value if isinstance(value, dict) and value.get("alien") == kind else bim.first(value, kind)
    if not t or not [l for l in t.get("layers") or [] if float(l.get("thickness") or 0) > 0]:
        return default_slab_type() if kind == "slab_type" else default_roof_type()
    return t


def solid_layers(t, tol_m):
    """(layers thick enough to model, layers too thin for the model's tolerance)."""
    keep, thin = [], []
    for l in t.get("layers") or []:
        th = float(l.get("thickness") or 0)
        if th <= 0:
            continue
        (keep if th > 5 * tol_m else thin).append(l)
    return keep, thin


def swatch(layers, plane, u, tilt=0.0, width=0.6):
    """A stepped sample of a build-up (Breps, colours): the top layer shortest, each lower layer stepping out along X so
    its top shows; membranes drawn 4 mm thick so they show; tilted `tilt` degrees about X (roofs)."""
    breps, cols = [], []
    sizes = [max(float(l["thickness"]), 0.004) for l in layers]
    z = sum(sizes)
    for i, (l, th) in enumerate(zip(layers, sizes)):
        b = bim.box(rg.Plane.WorldXY, 0, (0.3 + 0.12 * i) * u, 0, width * u, (z - th) * u, z * u)
        z -= th
        if b is not None:
            breps.append(b)
            cols.append(colour(l["material"]))
    xf = rg.Transform.PlaneToPlane(rg.Plane.WorldXY, plane if isinstance(plane, rg.Plane) and plane.IsValid else rg.Plane.WorldXY)
    if tilt:
        xf = xf * rg.Transform.Rotation(math.radians(tilt), rg.Vector3d.XAxis, rg.Point3d.Origin)
    for b in breps:
        b.Transform(xf)
    return breps, cols


# ── straight skeleton ────────────────────────────────────────
# The simulation advances every wavefront vertex to the next event (an edge shrinking to nothing, a reflex vertex
# reaching an edge or a vertex), resolves everything that happens at that height at once (so the ties of rectangles,
# squares and orthogonal plans come out clean), and keeps each vertex's path (hips, valleys, verges) and the lines where
# the wavefront closes (ridges). Time is height. Tested on ~4000 random orthogonal and star-shaped polygons.

class _Node(object):
    __slots__ = ("x", "y", "a", "b", "sx", "sy", "st", "vx", "vy", "reflex")


def skeleton(pts, speeds):
    """
    Weighted straight skeleton of a simple counter-clockwise polygon (no holes): pts [(x, y)], speeds per edge i
    (pts[i] -> pts[i+1]) in horizontal run per unit of height (0 = a gable that stays put).
    Returns (faces, lines): faces {edge index: [(x, y, h), ...]} each edge's roof face (h above the eaves; a gable's face
    is vertical), lines [((x, y, h), (x, y, h), kind, face a, face b)], kind 'ridge' | 'hip' | 'valley' | 'verge'.
    Raises ValueError when it can't resolve the outline.
    """
    n = len(pts)
    ox, oy = min(p[0] for p in pts), min(p[1] for p in pts)   # work near the origin: small numbers, clean ties
    P = [(x - ox, y - oy) for x, y in pts]
    size = max(max(p[0] for p in P), max(p[1] for p in P), 1e-9)
    eps = size * 1e-7
    L = []  # per edge: inward unit normal, line constant, speed (the wavefront at height t: n . x = c + s t)
    for i in range(n):
        (x0, y0), (x1, y1) = P[i], P[(i + 1) % n]
        d = math.hypot(x1 - x0, y1 - y0)
        nx, ny = -(y1 - y0) / d, (x1 - x0) / d
        L.append((nx, ny, nx * x0 + ny * y0, max(0.0, float(speeds[i]))))
    arcs, segs = [], []

    def det(a, b):
        return L[a][0] * L[b][1] - L[a][1] * L[b][0]

    def node(x, y, a, b, t):
        nd = _Node()
        nd.x, nd.y, nd.a, nd.b, nd.sx, nd.sy, nd.st = x, y, a, b, x, y, t
        nd.vx = nd.vy = None
        nd.reflex = det(a, b) < -1e-9
        return nd

    def kill(nd, x, y, t):
        arcs.append(((nd.sx, nd.sy, nd.st), (x, y, t), nd.a, nd.b, nd.reflex))

    def velocity(nd):
        na, nb = L[nd.a], L[nd.b]
        dd = det(nd.a, nd.b)
        if abs(dd) < 1e-9:
            if na[0] * nb[0] + na[1] * nb[1] > 0:  # collinear, same way
                s = (na[3] + nb[3]) / 2.0
                return na[0] * s, na[1] * s
            return None
        return (na[3] * nb[1] - na[1] * nb[3]) / dd, (na[0] * nb[3] - na[3] * nb[0]) / dd

    def place(nd, t):
        na, nb = L[nd.a], L[nd.b]
        dd = det(nd.a, nd.b)
        if abs(dd) < 1e-6:
            return False
        ca, cb = na[2] + na[3] * t, nb[2] + nb[3] * t
        nd.x, nd.y = (ca * nb[1] - na[1] * cb) / dd, (na[0] * cb - ca * nb[0]) / dd
        return True

    def dist(p, q):
        return math.hypot(p.x - q.x, p.y - q.y)

    def seg_dist(p, a, b):
        ex, ey = b.x - a.x, b.y - a.y
        l2 = ex * ex + ey * ey
        if l2 < 1e-30:
            return dist(p, a)
        u = max(0.0, min(1.0, ((p.x - a.x) * ex + (p.y - a.y) * ey) / l2))
        return math.hypot(p.x - a.x - u * ex, p.y - a.y - u * ey)

    def area(loop):
        return sum(loop[i].x * loop[(i + 1) % len(loop)].y - loop[(i + 1) % len(loop)].x * loop[i].y for i in range(len(loop))) / 2.0

    def finish(loop, t):
        for i in range(len(loop)):
            p, q = loop[i], loop[(i + 1) % len(loop)]
            if dist(p, q) > eps:
                segs.append(((p.x, p.y, t), (q.x, q.y, t), p.b, None))
        for nd in loop:
            kill(nd, nd.x, nd.y, t)

    def clean(loop, t):
        """Resolve everything happening at height t in one loop; returns the loops it becomes."""
        for _ in range(4 * len(loop) + 8):
            m = len(loop)
            if m < 3 or abs(area(loop)) < eps * sum(dist(loop[i], loop[(i + 1) % m]) for i in range(m)):
                finish(loop, t)
                return []
            changed = False
            for k in range(m):  # an edge shrunk to nothing: merge its ends
                A, B = loop[k], loop[(k + 1) % m]
                if dist(A, B) < eps:
                    N = node((A.x + B.x) / 2, (A.y + B.y) / 2, A.a, B.b, t)
                    kill(A, N.x, N.y, t)
                    kill(B, N.x, N.y, t)
                    loop = loop[1:k] + [N] if k == m - 1 else loop[:k] + [N] + loop[k + 2:]
                    changed = True
                    break
            if changed:
                continue
            for k in range(m):  # a node between two parts of one edge is just a point on it
                if loop[k].a == loop[k].b:
                    kill(loop[k], loop[k].x, loop[k].y, t)
                    loop = loop[:k] + loop[k + 1:]
                    changed = True
                    break
            if changed:
                continue
            for k in range(m):  # a spike (its two edges on one line, facing): zip it shut to the nearer neighbour
                N = loop[k]
                na, nb = L[N.a], L[N.b]
                if abs(det(N.a, N.b)) < 1e-9 and na[0] * nb[0] + na[1] * nb[1] < 0:
                    Pn, Qn = loop[k - 1], loop[(k + 1) % m]
                    if dist(Pn, N) <= dist(Qn, N):
                        segs.append(((N.x, N.y, t), (Pn.x, Pn.y, t), N.a, N.b))
                        R = node(Pn.x, Pn.y, Pn.a, N.b, t)
                        kill(N, N.x, N.y, t)
                        kill(Pn, Pn.x, Pn.y, t)
                        ip = (k - 1) % m
                        loop = [R if i == ip else x for i, x in enumerate(loop) if i != k]
                    else:
                        segs.append(((N.x, N.y, t), (Qn.x, Qn.y, t), N.a, N.b))
                        R = node(Qn.x, Qn.y, N.a, Qn.b, t)
                        kill(N, N.x, N.y, t)
                        kill(Qn, Qn.x, Qn.y, t)
                        iq = (k + 1) % m
                        loop = [R if i == k else x for i, x in enumerate(loop) if i != iq]
                    changed = True
                    break
            if changed:
                continue
            for i in range(m):  # a reflex node touching a vertex or an edge of its loop: split the loop there
                r = loop[i]
                if not r.reflex:
                    continue
                hit = None
                for j in range(m):
                    if j not in (i, (i + 1) % m, (i - 1) % m) and dist(r, loop[j]) < eps:
                        hit = ("vertex", j)
                        break
                if hit is None:
                    for k in range(m):
                        if k not in (i, (i - 1) % m) and seg_dist(r, loop[k], loop[(k + 1) % m]) < eps:
                            hit = ("edge", k)
                            break
                if hit is None:
                    continue
                kind, j = hit
                rot = loop[i:] + loop[:i]
                j = (j - i) % m
                if kind == "vertex":
                    q = rot[j]
                    hx, hy = (r.x + q.x) / 2, (r.y + q.y) / 2
                    n1, n2 = node(hx, hy, q.a, r.b, t), node(hx, hy, r.a, q.b, t)
                    kill(r, hx, hy, t)
                    kill(q, hx, hy, t)
                    one, two = [n1] + rot[1:j], [n2] + rot[j + 1:]
                else:
                    e = rot[j].b
                    n1, n2 = node(r.x, r.y, e, r.b, t), node(r.x, r.y, r.a, e, t)
                    kill(r, r.x, r.y, t)
                    one, two = [n1] + rot[1:j + 1], [n2] + rot[j + 1:]
                return clean(one, t) + clean(two, t)
            return [loop]
        raise ValueError("the roof outline could not be resolved")

    T = 0.0
    loops = clean([node(x, y, (i - 1) % n, i, 0.0) for i, (x, y) in enumerate(P)], T)
    for _ in range(20 * n + 50):
        if not loops:
            break
        for loop in loops:
            for nd in loop:
                v = velocity(nd)
                if v is None:
                    raise ValueError("the roof outline could not be resolved (a spike)")
                nd.vx, nd.vy = v
        best = None
        for loop in loops:
            m = len(loop)
            for k in range(m):
                A, B = loop[k], loop[(k + 1) % m]
                dx, dy = B.x - A.x, B.y - A.y
                d = math.hypot(dx, dy)
                rate = ((B.vx - A.vx) * dx + (B.vy - A.vy) * dy) / d if d > 0 else -1.0
                if rate < -1e-12:
                    te = T + d / -rate
                    best = te if best is None or te < best else best
            for r in loop:
                if not r.reflex:
                    continue
                for k in range(m):
                    A, B = loop[k], loop[(k + 1) % m]
                    if A is r or B is r:
                        continue
                    nx, ny, c, s = L[A.b]
                    rel = s - (nx * r.vx + ny * r.vy)
                    if rel <= 1e-12:
                        continue
                    tau = (nx * r.x + ny * r.y - c - s * T) / rel
                    if tau < -eps:
                        continue
                    tau = max(tau, 0.0)
                    hx, hy = r.x + r.vx * tau, r.y + r.vy * tau
                    ax, ay = A.x + A.vx * tau, A.y + A.vy * tau
                    ex, ey = B.x + B.vx * tau - ax, B.y + B.vy * tau - ay
                    l2 = ex * ex + ey * ey
                    if l2 < eps * eps:
                        continue
                    u = ((hx - ax) * ex + (hy - ay) * ey) / l2
                    slack = eps / math.sqrt(l2)
                    if -slack <= u <= 1 + slack:
                        best = T + tau if best is None or T + tau < best else best
        if best is None:  # nothing moves any more (only gables left)
            for loop in loops:
                finish(loop, T)
            loops = []
            break
        dt, T = best - T, best
        for loop in loops:
            for nd in loop:
                if not place(nd, T):
                    nd.x += nd.vx * dt
                    nd.y += nd.vy * dt
        loops = [l2 for l in loops for l2 in clean(l, T)]
    if loops:
        raise ValueError("the roof outline could not be resolved (too many events)")

    reps = []

    def key(p):
        for i, q in enumerate(reps):
            if abs(p[0] - q[0]) < eps * 10 and abs(p[1] - q[1]) < eps * 10 and abs(p[2] - q[2]) < eps * 10:
                return i
        reps.append(p)
        return len(reps) - 1

    border = {i: [] for i in range(n)}
    found = OrderedDict()  # (key, key) -> [start, end, faces, reflex, closing line]
    for s, e, a, b, reflex in arcs:
        ks, ke = key(s), key(e)
        if ks == ke:
            continue
        border[a].append((ks, ke))
        border[b].append((ks, ke))
        if abs(det(a, b)) < 1e-9 and L[a][0] * L[b][0] + L[a][1] * L[b][1] > 0 and L[a][3] == L[b][3]:
            continue  # between two faces of one plane: not a line on the roof
        found.setdefault((min(ks, ke), max(ks, ke)), [s, e, set(), reflex, False])[2].update((a, b))
    for p, q, a, b in segs:
        kp, kq = key(p), key(q)
        if kp == kq:
            continue
        for f in (a, b):
            if f is not None:
                border[f].append((kp, kq))
        rec = found.setdefault((min(kp, kq), max(kp, kq)), [p, q, set(), False, True])
        rec[2].update(f for f in (a, b) if f is not None)
        rec[4] = True
    lines = []
    for s, e, fs, reflex, closing in found.values():
        fl = sorted(fs)
        if any(L[f][3] == 0 for f in fl):
            kind = "verge"
        elif closing:
            kind = "ridge"
        else:
            kind = "valley" if reflex else "hip"
        lines.append(((s[0] + ox, s[1] + oy, s[2]), (e[0] + ox, e[1] + oy, e[2]), kind, fl[0] if fl else None, fl[-1] if len(fl) > 1 else None))
    faces = {}
    for i in range(n):
        k0 = key((P[i][0], P[i][1], 0.0))
        k1 = key((P[(i + 1) % n][0], P[(i + 1) % n][1], 0.0))
        adj = {}
        for j, (p, q) in enumerate(border[i]):
            adj.setdefault(p, []).append((q, j))
            adj.setdefault(q, []).append((p, j))
        chain, cur, used = [k0, k1], k1, set()
        while cur != k0 and len(chain) < 4 * len(border[i]) + 4:
            nxt = [(q, j) for q, j in adj.get(cur, []) if j not in used]
            if not nxt:
                break
            q, j = nxt[0]
            used.add(j)
            if q != k0:
                chain.append(q)
            cur = q
        if cur == k0 and len(chain) >= 3:
            faces[i] = [(reps[k][0] + ox, reps[k][1] + oy, reps[k][2]) for k in chain]
    return faces, lines


# ── plan polygons (metres, in the site's frame) ─────────────

class Frame(object):
    """The site's frame: plan points in metres along the project grid from the site origin; z stays world metres."""

    def __init__(self, site, u):
        s = site if isinstance(site, dict) else bim.site_of(site)
        o = s.get("origin") or [0.0, 0.0, 0.0]
        a = math.radians(float(s.get("rotation") or 0.0))
        self.ox, self.oy, self.c, self.s, self.u = float(o[0]), float(o[1]), math.cos(a), math.sin(a), u
        self.north = float(s.get("north") or 0.0)

    def local(self, p):
        x, y = p.X / self.u - self.ox, p.Y / self.u - self.oy
        return (x * self.c + y * self.s, -x * self.s + y * self.c)

    def world(self, x, y, z):
        return rg.Point3d((self.ox + x * self.c - y * self.s) * self.u, (self.oy + x * self.s + y * self.c) * self.u, z * self.u)

    def vector(self, x, y):
        return rg.Vector3d(x * self.c - y * self.s, x * self.s + y * self.c, 0)

    def bearing(self, x, y):
        """Compass bearing (degrees from true north, clockwise) of a frame direction."""
        v = self.vector(x, y)
        return (math.degrees(math.atan2(v.X, v.Y)) + self.north) % 360.0


def _cross(o, a, b):
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def area2d(pts):
    return sum(pts[i][0] * pts[(i + 1) % len(pts)][1] - pts[(i + 1) % len(pts)][0] * pts[i][1] for i in range(len(pts))) / 2.0


def clean_polygon(pts, eps=1e-6):
    """Drop repeated and collinear points; counter-clockwise."""
    out = []
    for p in pts:
        if not out or math.hypot(p[0] - out[-1][0], p[1] - out[-1][1]) > eps:
            out.append((p[0], p[1]))
    while len(out) > 1 and math.hypot(out[0][0] - out[-1][0], out[0][1] - out[-1][1]) <= eps:
        out.pop()
    changed = True
    while changed and len(out) > 3:
        changed = False
        for i in range(len(out)):
            a, b, c = out[i - 1], out[i], out[(i + 1) % len(out)]
            la, lb = math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(c[0] - b[0], c[1] - b[1])
            if abs(_cross(a, b, c)) <= 1e-7 * la * lb:
                out.pop(i)
                changed = True
                break
    if len(out) >= 3 and area2d(out) < 0:
        out.reverse()
    return out


def is_simple(pts):
    n = len(pts)

    def crosses(a, b, c, d):
        o1, o2, o3, o4 = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
        return o1 * o2 < -1e-12 and o3 * o4 < -1e-12
    for i in range(n):
        for j in range(i + 2, n):
            if i == 0 and j == n - 1:
                continue
            if crosses(pts[i], pts[(i + 1) % n], pts[j], pts[(j + 1) % n]):
                return False
    return True


def plan_polygon(curve, frame, tol):
    """A closed curve's plan as a clean CCW polygon in the frame (metres), and whether it was a polyline (exact)."""
    ok, pl = curve.TryGetPolyline()
    exact = ok
    okc, circ = curve.TryGetCircle(tol)
    if not ok and okc:
        return clean_polygon([frame.local(circ.PointAt(2 * math.pi * k / 36.0)) for k in range(36)]), False
    if not ok:
        pc = curve.ToPolyline(0.02 * frame.u, math.radians(10), 0, 0)
        ok, pl = pc.TryGetPolyline() if pc is not None else (False, None)
        if not ok:
            return None, False
    return clean_polygon([frame.local(p) for p in pl]), exact


def _unit(dx, dy):
    d = math.hypot(dx, dy)
    return (dx / d, dy / d) if d > 0 else (0.0, 0.0)


def _meet(p, d, q, e):
    den = d[0] * e[1] - d[1] * e[0]
    if abs(den) < 1e-9:
        return None
    t = ((q[0] - p[0]) * e[1] - (q[1] - p[1]) * e[0]) / den
    return (p[0] + d[0] * t, p[1] + d[1] * t)


def offset_polygon(pts, dists):
    """Each edge moved outward by its own distance (m), corners re-met; None if the result folds over itself."""
    n = len(pts)
    lines = []
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        d = _unit(b[0] - a[0], b[1] - a[1])
        lines.append(((a[0] + d[1] * dists[i], a[1] - d[0] * dists[i]), d))
    out = []
    reach = 6.0 * max(max(dists), 1e-9)   # a mitre limit: very sharp corners would shoot the overhang miles out
    for i in range(n):
        p = _meet(lines[i - 1][0], lines[i - 1][1], lines[i][0], lines[i][1])
        p = p if p is not None else (pts[i][0] + lines[i][1][1] * dists[i], pts[i][1] - lines[i][1][0] * dists[i])
        if math.hypot(p[0] - pts[i][0], p[1] - pts[i][1]) > reach:
            return None
        out.append(p)
    for i in range(n):  # every edge must keep its direction
        a, b, c, d = pts[i], pts[(i + 1) % n], out[i], out[(i + 1) % n]
        if (b[0] - a[0]) * (d[0] - c[0]) + (b[1] - a[1]) * (d[1] - c[1]) <= 0:
            return None
    return out if area2d(out) > 0 and is_simple(out) else None


def clip_convex(poly, clip):
    """A planar 3D polygon [(x, y, z)] clipped to a convex CCW plan polygon (Sutherland-Hodgman, z interpolated)."""
    out = list(poly)
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        src, out = out, []
        if not src:
            break

        def side(p):
            return (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        for j in range(len(src)):
            p, q = src[j], src[(j + 1) % len(src)]
            sp, sq = side(p), side(q)
            if sp >= -1e-9:
                out.append(p)
            if (sp >= -1e-9) != (sq >= -1e-9):
                t = sp / (sp - sq)
                out.append(tuple(p[k] + (q[k] - p[k]) * t for k in range(3)))
    return out


def clip_segment(p, q, clip):
    """The part of a 3D segment inside a convex CCW plan polygon, or None."""
    t0, t1 = 0.0, 1.0
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        sp = (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
        sq = (b[0] - a[0]) * (q[1] - a[1]) - (b[1] - a[1]) * (q[0] - a[0])
        if sp < -1e-9 and sq < -1e-9:
            return None
        if (sp < -1e-9) != (sq < -1e-9):
            t = sp / (sp - sq)
            if sp < 0:
                t0 = max(t0, t)
            else:
                t1 = min(t1, t)
    if t1 - t0 < 1e-9:
        return None
    return tuple(p[k] + (q[k] - p[k]) * t0 for k in range(3)), tuple(p[k] + (q[k] - p[k]) * t1 for k in range(3))


# ── solids ───────────────────────────────────────────────────

def _outward(b):
    if b is not None and b.IsSolid and b.SolidOrientation == rg.BrepSolidOrientation.Inward:
        b.Flip()
    return b


def polygon_brep(points, tol):
    """A planar face from a closed list of Point3d (no repeat of the first)."""
    pts = [p for i, p in enumerate(points) if i == 0 or p.DistanceTo(points[i - 1]) > tol]
    if len(pts) > 1 and pts[0].DistanceTo(pts[-1]) <= tol:
        pts.pop()
    if len(pts) < 3:
        return None
    b = rg.Brep.CreatePlanarBreps(rg.PolylineCurve(pts + [pts[0]]), tol)
    return b[0] if b else None


def heightfield_solid(faces, drop, depth, frame, tol):
    """A layer under a surface of planar faces [(x, y, z)] (frame metres) that tile a region: the faces dropped by
    `drop`, `depth` deep, with vertical sides along the region's edge. One closed Brep, or one prism per face when they
    won't join."""
    reps = []

    def key(p):
        for i, q in enumerate(reps):
            if abs(p[0] - q[0]) < 1e-6 and abs(p[1] - q[1]) < 1e-6 and abs(p[2] - q[2]) < 1e-6:
                return i
        reps.append(p)
        return len(reps) - 1
    kf = []
    for f in faces:
        ks = []
        for p in f:
            k = key(p)
            if not ks or ks[-1] != k:
                ks.append(k)
        while len(ks) > 1 and ks[0] == ks[-1]:
            ks.pop()
        if len(ks) >= 3:
            kf.append(ks)
    # a vertex lying on another face's edge (where a ridge meets another ridge mid-way) goes into that edge too, or the
    # faces won't join
    for fi, ks in enumerate(kf):
        out = []
        for i in range(len(ks)):
            a, b = reps[ks[i]], reps[ks[(i + 1) % len(ks)]]
            d = [b[j] - a[j] for j in range(3)]
            l2 = sum(x * x for x in d)
            mids = []
            for k, p in enumerate(reps):
                if k in (ks[i], ks[(i + 1) % len(ks)]) or l2 < 1e-12:
                    continue
                t = sum((p[j] - a[j]) * d[j] for j in range(3)) / l2
                if 1e-9 < t < 1 - 1e-9 and sum((a[j] + d[j] * t - p[j]) ** 2 for j in range(3)) < 1e-12:
                    mids.append((t, k))
            out.append(ks[i])
            out += [k for _, k in sorted(mids)]
        kf[fi] = out
    count = {}
    for ks in kf:
        for i in range(len(ks)):
            e = (min(ks[i], ks[(i + 1) % len(ks)]), max(ks[i], ks[(i + 1) % len(ks)]))
            count[e] = count.get(e, 0) + 1
    top = [frame.world(p[0], p[1], p[2] - drop) for p in reps]
    bot = [frame.world(p[0], p[1], p[2] - drop - depth) for p in reps]

    def build(groups):
        polys = []
        for ks, edges in groups:
            polys.append([top[k] for k in ks])
            polys.append([bot[k] for k in reversed(ks)])
            for a, b in edges:
                polys.append([top[a], top[b], bot[b], bot[a]])
        parts = [polygon_brep(p, tol) for p in polys]
        if any(p is None for p in parts):
            return None
        joined = rg.Brep.JoinBreps(parts, tol)
        if joined is None or len(joined) != 1 or not joined[0].IsSolid:
            return None
        b = joined[0]
        b.MergeCoplanarFaces(tol)
        return _outward(b)
    whole = build([(ks, [(ks[i], ks[(i + 1) % len(ks)]) for i in range(len(ks))
                         if count[(min(ks[i], ks[(i + 1) % len(ks)]), max(ks[i], ks[(i + 1) % len(ks)]))] == 1]) for ks in kf])
    if whole is not None:
        return [whole]
    out = []
    for ks in kf:
        b = build([(ks, [(ks[i], ks[(i + 1) % len(ks)]) for i in range(len(ks))])])
        if b is not None:
            out.append(b)
    return out


def drop_prism(face_brep, drop, depth, u):
    """A planar region (Brep) moved down `drop` and extruded `depth` down (metres): a closed solid."""
    b = face_brep.DuplicateBrep()
    b.Translate(rg.Vector3d(0, 0, -drop * u))
    s = b.Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d(0, 0, -depth * u)), True)
    return _outward(s) if s is not None and s.IsValid and s.IsSolid else None


def band(chain, depth, thick, inward, frame, tol):
    """A board hanging `depth` (m) under a chain of points (frame metres), `thick` deep along the plan unit vector
    `inward` (fascias, barge boards, gablets)."""
    pts = [frame.world(*p) for p in chain] + [frame.world(p[0], p[1], p[2] - depth) for p in reversed(chain)]
    f = polygon_brep(pts, tol)
    if f is None:
        return None
    v = frame.vector(inward[0], inward[1]) * (thick * frame.u)
    s = f.Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d.Origin + v), True)
    return _outward(s) if s is not None and s.IsValid and s.IsSolid else None


# ── slabs ────────────────────────────────────────────────────

def _flat(c):
    c = c.DuplicateCurve()
    c.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
    return c


def _ccw(c):
    if c.ClosedCurveOrientation(Z) == rg.CurveOrientation.Clockwise:
        c.Reverse()
    return c


def closed_flat(curve, tol):
    """A curve flattened onto z=0, closed and counter-clockwise, or None."""
    if not isinstance(curve, rg.Curve):
        return None
    c = _flat(curve)
    if not c.IsClosed:  # a small gap (up to 1% of the length) is closed with a straight segment
        gap = c.PointAtStart.DistanceTo(c.PointAtEnd)
        if gap > max(tol * 100, c.GetLength() * 0.01) or not c.MakeClosed(gap * 1.01 + tol):
            return None
    if not c.IsPlanar(tol) or c.GetLength() < tol * 100:
        return None
    return _ccw(c)


def offset_closed(c, d, tol, corners=None):
    """A closed CCW curve offset outward by d (model units; negative = inward), sharp corners (or chamfered where the
    sharp mitres would run far out). None if it vanishes."""
    if abs(d) <= tol:
        return c
    if corners is None:
        r = offset_closed(c, d, tol, rg.CurveOffsetCornerStyle.Sharp)
        if r is None or d < 0:
            return r
        a0, a1 = rg.AreaMassProperties.Compute(c), rg.AreaMassProperties.Compute(r)
        if a0 and a1 and a1.Area > a0.Area + c.GetLength() * d * 1.5 + 4 * d * d:
            return offset_closed(c, d, tol, rg.CurveOffsetCornerStyle.Chamfer)
        return r
    r = c.Offset(rg.Plane.WorldXY, d, tol, corners)
    if not r:
        return None
    r = [x for x in rg.Curve.JoinCurves(r, tol * 10) if x.IsClosed] if len(r) > 1 else list(r)
    if not r:
        return None
    best = max(r, key=lambda x: abs(rg.AreaMassProperties.Compute(x).Area) if rg.AreaMassProperties.Compute(x) else 0)
    return _ccw(best)


def slab_region(outline, openings, tol):
    """Planar faces (Breps at z=0) of an outline minus openings (flat closed curves). Returns (faces, used openings,
    holes (the merged openings wholly inside), notes)."""
    notes, used = [], []
    holes, cuts = [], []
    if openings:
        merged = rg.Curve.CreateBooleanUnion(openings, tol) if len(openings) > 1 else openings
        merged = list(merged) if merged else list(openings)
        for o in merged:
            rel = rg.Curve.PlanarClosedCurveRelationship(o, outline, rg.Plane.WorldXY, tol)
            if rel == rg.RegionContainment.AInsideB:
                holes.append(o)
            elif rel == rg.RegionContainment.MutualIntersection:
                cuts.append(o)
            elif rel == rg.RegionContainment.BInsideA:
                notes.append("an opening covers the whole slab")
                return [], list(openings), [], notes
        used = [o for o in openings
                if rg.Curve.PlanarClosedCurveRelationship(o, outline, rg.Plane.WorldXY, tol) != rg.RegionContainment.Disjoint]
    outers = [outline]
    if cuts:
        d = rg.Curve.CreateBooleanDifference(outline, cuts, tol)
        if d:
            outers = list(d)
        else:
            notes.append("an opening across the slab edge could not be cut")
    faces = rg.Brep.CreatePlanarBreps(outers + holes, tol) or []
    return [f for f in faces if f.IsValid], used, holes, notes


def void_cross(c, tol):
    """The diagonal cross architects draw in a void: corner to corner of its box, kept inside the curve."""
    bb = c.GetBoundingBox(True)
    out = []
    for a, b in ((bb.Min, rg.Point3d(bb.Max.X, bb.Max.Y, bb.Min.Z)), (rg.Point3d(bb.Min.X, bb.Max.Y, bb.Min.Z), rg.Point3d(bb.Max.X, bb.Min.Y, bb.Min.Z))):
        ln = rg.LineCurve(a, b)
        x = rg.Intersect.Intersection.CurveCurve(ln, c, tol, tol)
        ts = sorted(e.ParameterA for e in x) if x else []
        if len(ts) >= 2 and ln.PointAt(ts[-1]).DistanceTo(ln.PointAt(ts[0])) > tol * 10:
            out.append(rg.LineCurve(ln.PointAt(ts[0]), ln.PointAt(ts[-1])))
    return out


def pick_levels(levels, which):
    """The levels a text names, lowest first: floors (all but the top), all, suspended (not the lowest or the top),
    indices from 0 = the lowest ('1-3', '-1'), names or codes ('L01, Level 2'), name ranges ('GF..L02')."""
    s = str(which or "").strip()
    key = s.lower()
    if key in ("", "floors", "floor"):
        return (levels[:-1] if len(levels) > 1 else list(levels)), []
    if key in ("all", "*"):
        return list(levels), []
    if key == "suspended":
        return levels[1:-1], ([] if len(levels) > 2 else ["suspended: there are no levels between the lowest and the top"])
    picked, notes = [], []
    for tok in re.split(r"[,;\n]+", s):
        tok = tok.strip()
        if not tok:
            continue
        m = re.fullmatch(r"(-?\d+)\s*(?:-|to|\.\.)\s*(-?\d+)", tok)
        r = re.fullmatch(r"(.+?)\s*(?:\.\.|\bto\b)\s*(.+)", tok)
        if m:
            i0, i1 = [int(g) + (len(levels) if int(g) < 0 else 0) for g in m.groups()]
            got = [levels[i] for i in range(min(i0, i1), max(i0, i1) + 1) if 0 <= i < len(levels)]
        elif r and bim.level_at(levels, r.group(1)) is not None and bim.level_at(levels, r.group(2)) is not None:
            a, b = levels.index(bim.level_at(levels, r.group(1))), levels.index(bim.level_at(levels, r.group(2)))
            got = levels[min(a, b):max(a, b) + 1]
        else:
            lv = bim.level_at(levels, tok)
            got = [lv] if lv is not None and (not re.fullmatch(r"-?\d+(\.\d+)?", tok) or "." not in tok) else []
        if not got:
            notes.append("no level '%s'" % tok)
        picked += got
    return [l for l in levels if any(l is p for p in picked)], notes


def nearest_level(levels, z, within=1.0):
    """The level whose elevation is nearest z (m), if within `within` metres."""
    if not levels:
        return None
    lv = min(levels, key=lambda l: abs(float(l["elevation"]) - z))
    return lv if abs(float(lv["elevation"]) - z) <= within else None


# ── roofs ────────────────────────────────────────────────────

def gable_edges(pts, ridge):
    """Edges that become gables: between two convex corners, across the ridge (x / y), or with ridge 'auto' shorter
    than both neighbours (the ends of a rectangle or of an L's wings). Never two side by side."""
    n = len(pts)

    def turn(i):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        d1, d2 = _unit(b[0] - a[0], b[1] - a[1]), _unit(c[0] - b[0], c[1] - b[1])
        return math.degrees(math.atan2(d1[0] * d2[1] - d1[1] * d2[0], d1[0] * d2[0] + d1[1] * d2[1]))
    convex = [turn(i) >= 45 for i in range(n)]
    lens = [math.hypot(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1]) for i in range(n)]
    out = []
    for i in range(n):
        if not (convex[i] and convex[(i + 1) % n]):
            continue
        d = _unit(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1])
        if ridge == "x":
            ok = abs(d[0]) < 0.26
        elif ridge == "y":
            ok = abs(d[1]) < 0.26
        else:
            ok = lens[i] <= min(lens[i - 1], lens[(i + 1) % n]) * (1 + 1e-6)
        if ok:
            out.append(i)
    keep = []
    for i in out:  # side by side (a square): keep the ones across project X, so the ridge runs along X
        j = keep[-1] if keep else None
        if j is not None and (j + 1) % n == i:
            dj = abs(_unit(pts[(j + 1) % n][0] - pts[j][0], pts[(j + 1) % n][1] - pts[j][1])[0])
            di = abs(_unit(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1])[0])
            if di < dj:
                keep[-1] = i
            continue
        keep.append(i)
    if len(keep) > 1 and (keep[-1] + 1) % n == keep[0]:
        keep.pop()
    return keep


def ridge_direction(pts, ridge):
    """Unit plan vector of the ridge: x, y, or the longest edge (pointing +x, or +y when across)."""
    if ridge == "x":
        return (1.0, 0.0)
    if ridge == "y":
        return (0.0, 1.0)
    n = len(pts)
    i = max(range(n), key=lambda k: math.hypot(pts[(k + 1) % n][0] - pts[k][0], pts[(k + 1) % n][1] - pts[k][1]))
    d = _unit(pts[(i + 1) % n][0] - pts[i][0], pts[(i + 1) % n][1] - pts[i][1])
    return d if d[0] > 1e-9 or (abs(d[0]) <= 1e-9 and d[1] > 0) else (-d[0], -d[1])


def _arrow(frame, c, f, length, z, label, text_h):
    """A fall arrow (plan curves) centred on c pointing down the slope f, with its label behind the tail."""
    a = (c[0] - f[0] * length / 2, c[1] - f[1] * length / 2)
    b = (c[0] + f[0] * length / 2, c[1] + f[1] * length / 2)
    out = [rg.LineCurve(frame.world(a[0], a[1], z), frame.world(b[0], b[1], z))]
    h = length * 0.22
    for s in (1, -1):
        ang = math.radians(150 * s)
        dx, dy = f[0] * math.cos(ang) - f[1] * math.sin(ang), f[0] * math.sin(ang) + f[1] * math.cos(ang)
        out.append(rg.LineCurve(frame.world(b[0], b[1], z), frame.world(b[0] + dx * h, b[1] + dy * h, z)))
    if label and text_h > 0:
        p = (a[0] - f[0] * text_h * 1.6, a[1] - f[1] * text_h * 1.6)
        pl = rg.Plane(frame.world(p[0], p[1], z), frame.vector(1, 0), frame.vector(0, 1))
        out += bim.text_curves(label, pl, text_h * frame.u)
    return out


def _face_info(poly, frame, slope, fall):
    """Record data for a roof face: world points, pitch, aspect, area on slope."""
    plan = abs(area2d([(p[0], p[1]) for p in poly]))
    return {"points": [bim.pt(frame.world(*p)) for p in poly], "pitch": round(slope, 3),
            "aspect": round(frame.bearing(fall[0], fall[1]), 1) if fall else None,
            "area": round(plan / math.cos(math.radians(slope)), 4), "plan_area": round(plan, 4)}


def build_roof(curve, form, pitch, overhang, gable_overhang, ridge, flip, fascia, rtype, level_z, frame, tol, scale=100,
               infill=0.0):
    """
    One roof over a closed outline (model units). Returns a dict: solids [(Brep, material)], lines {kind: [Curve]},
    plan [Curve], record fields, notes. Heights: the roof's underside sits on `level_z` (m) at the outline (the wall
    line), so the eaves drop overhang * tan(pitch) below it. `infill` (m) closes the walls up to the roof's underside
    where it rises above the level at the wall line (gable ends, skillion sides and high walls), centred on the line.
    """
    u = frame.u
    tol_m = tol / u
    notes = []
    form = str(form or "hip").strip().lower()
    if form not in ("flat", "skillion", "gable", "hip", "dutch gable"):
        notes.append("no form '%s': hip" % form)
        form = "hip"
    layers, thin = solid_layers(rtype, tol_m)
    if thin:
        notes.append("too thin to model at this tolerance: " + ", ".join(l["name"] for l in thin))
    T = sum(float(l["thickness"]) for l in rtype["layers"] if float(l.get("thickness") or 0) > 0)
    pts, exact = plan_polygon(curve, frame, tol)
    if not pts or len(pts) < 3:
        raise ValueError("the outline is not a closed curve with an area")
    if not is_simple(pts):
        raise ValueError("the outline crosses itself")
    pitch = max(0.0, min(80.0, float(pitch or 0.0)))
    if form != "flat" and pitch < 1.0:
        notes.append("pitch under 1 degree: built flat")
        form = "flat"
    theta = 0.0 if form == "flat" else math.radians(pitch)
    tn, k = math.tan(theta), 1.0 / math.cos(theta)
    run = 1.0 / tn if tn > 0 else 0.0
    overhang, gable_overhang = max(0.0, overhang or 0.0), max(0.0, gable_overhang or 0.0)
    text_h = 2.5 * scale / 1000.0
    arrow_len = 10.0 * scale / 1000.0
    lines = {"ridge": [], "hip": [], "valley": [], "eave": []}
    solids, plan, faces_rec, sloped = [], [], [], []   # sloped: (poly, fall unit vector)
    exact_area = None
    gablets, trims = [], []
    above_core = 0.0   # how far the structure's top sits below the roof's top (fascias line up with it)
    for l in layers:
        if l.get("core"):
            break
        above_core += float(l["thickness"])
    else:
        above_core = 0.0

    def add_layers(make):
        a = 0.0
        for idx, l in enumerate(layers):
            th = float(l["thickness"]) * k
            for b in make(a, th) or []:
                if b is not None:
                    solids.append((b, l["material"], idx))
            a += th

    def seg(p, q):
        return rg.LineCurve(frame.world(*p), frame.world(*q))

    def poly_curve(poly, z=None):
        ps = [frame.world(p[0], p[1], p[2] if z is None else z) for p in poly]
        return rg.PolylineCurve(ps + [ps[0]])

    rd = ridge_direction(pts, ridge)
    fall = (rd[1], -rd[0])  # skillion and flat fall across the ridge direction
    if flip:
        fall = (-fall[0], -fall[1])

    if form in ("flat", "skillion"):
        # footprint: the exact curve offset (round outlines stay round), or the polygon with eaves/verge overhangs
        flatc = closed_flat(curve, tol)
        if exact:
            dists = [overhang if form == "flat" or abs((pts[(i + 1) % len(pts)][0] - pts[i][0]) * fall[0] +
                                                     (pts[(i + 1) % len(pts)][1] - pts[i][1]) * fall[1]) <
                     0.5 * math.hypot(pts[(i + 1) % len(pts)][0] - pts[i][0], pts[(i + 1) % len(pts)][1] - pts[i][1])
                     else gable_overhang for i in range(len(pts))]
            fp = offset_polygon(pts, dists)
            if fp is None:  # a notch narrower than the overhangs: let Rhino's offset close it
                fpc0 = offset_closed(flatc, overhang * u, tol) if flatc is not None else None
                fp = plan_polygon(fpc0, frame, tol)[0] if fpc0 is not None else None
                if fp and len(fp) >= 3:
                    notes.append("a narrow notch or sharp corner: overhangs %.2f m all round" % overhang)
                else:
                    notes.append("the outline is too tight for the overhangs: no overhang")
                    fp = pts
            fpc = poly_curve([(p[0], p[1], 0.0) for p in fp], 0.0)
            fpc.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
        else:
            fpc = offset_closed(flatc, overhang * u, tol) if flatc is not None else None
            if fpc is None:
                notes.append("could not offset the outline: no overhang")
                fpc = flatc
            fp = plan_polygon(fpc, frame, tol)[0] if fpc is not None else None
            if not fp or len(fp) < 3:
                raise ValueError("the outline is not a closed planar curve")
        m_o = max(p[0] * fall[0] + p[1] * fall[1] for p in pts)   # the low wall line
        z0 = level_z + T * k

        def ztop(x, y):
            return z0 + (m_o - (x * fall[0] + y * fall[1])) * tn
        o3 = frame.world(0, 0, ztop(0, 0))
        top_plane = rg.Plane(o3, frame.world(1, 0, ztop(1, 0)) - o3, frame.world(0, 1, ztop(0, 1)) - o3)
        tc = fpc.DuplicateCurve()
        tc.Transform(rg.Transform.ProjectAlong(top_plane, Z))
        region = rg.Brep.CreatePlanarBreps(tc, tol)
        if not region:
            raise ValueError("could not make the roof surface")
        add_layers(lambda a, th: [drop_prism(region[0], a, th, u)])
        poly3 = [(p[0], p[1], ztop(*p)) for p in fp]
        sloped.append((poly3, fall if form == "skillion" else None))
        amp = rg.AreaMassProperties.Compute(fpc)
        exact_area = amp.Area / (u * u) if amp else None
        z_eave = min(p[2] for p in poly3)
        if form == "flat":
            lines["eave"].append(tc)
        else:
            # low edges are eaves, the high edge is the skillion's ridge (apex flashing), the sloping sides verges
            hi = max(p[2] for p in poly3)
            for i in range(len(poly3)):
                p, q = poly3[i], poly3[(i + 1) % len(poly3)]
                if abs(p[2] - q[2]) < 1e-6 and abs(p[2] - hi) < 1e-3 * max(1.0, hi):
                    lines["ridge"].append(seg(p, q))
                else:
                    lines["eave"].append(seg(p, q))
        # fascia: a flat roof's hangs under its edge all round; a skillion's covers the rafter ends (barges up the
        # sloping sides), with a gutter along the low eave
        if fascia > 0:
            if form == "flat" and not exact and fpc is not None:
                inner = offset_closed(fpc, -0.025 * u, tol)
                ring = rg.Brep.CreatePlanarBreps([fpc, inner], tol) if inner is not None else None
                if ring:
                    ring[0].Translate(rg.Vector3d(0, 0, (level_z) * u))
                    b = drop_prism(ring[0], 0.0, fascia, u)
                    if b is not None:
                        trims.append((b, "fascia", "fascia"))
            else:
                drop = (T * k) if form == "flat" else above_core * k
                low = min(ztop(*p) for p in fp)
                gutters = set(i for i in range(len(fp)) if form == "skillion" and
                              abs(ztop(*fp[i]) - low) < 1e-6 and abs(ztop(*fp[(i + 1) % len(fp)]) - low) < 1e-6)
                for i in range(len(fp)):
                    p, q = fp[i], fp[(i + 1) % len(fp)]
                    d = _unit(q[0] - p[0], q[1] - p[1])
                    chain = [(p[0], p[1], ztop(*p) - drop), (q[0], q[1], ztop(*q) - drop)]
                    if form == "flat":
                        trims.append((band(chain, fascia, 0.025, (-d[1], d[0]), frame, tol), "fascia", "fascia"))
                    else:
                        trims.append((band(chain, fascia, 0.025, (d[1], -d[0]), frame, tol), "fascia" if i in gutters else "barge", "fascia"))
                    if i in gutters:
                        trims.append((_gutter(fp, i, low - drop, frame, tol, gutters), "gutter", "gutter"))
        label_z = z_eave
        c = label_point(fp)
        if form == "flat":
            g = 1.0 / math.tan(math.radians(pitch)) if 0 < pitch <= 5 else 100
            plan += _arrow(frame, c, fall, arrow_len, label_z, "FALL 1:%d" % round(g), text_h)
        else:
            plan += _arrow(frame, c, fall, arrow_len, label_z, "%g°" % round(pitch, 1), text_h)
        pc = fpc.DuplicateCurve()
        pc.Translate(rg.Vector3d(0, 0, label_z * u))
        plan.append(pc)
    else:
        n = len(pts)
        ends = gable_edges(pts, ridge) if form in ("gable", "dutch gable") else []
        if form != "hip" and not ends:
            notes.append("no gable ends found on this outline: hipped")
        gables = ends if form == "gable" else []
        fp = offset_polygon(pts, [gable_overhang if i in gables else overhang for i in range(n)])
        if fp is None:
            fpc = offset_closed(closed_flat(curve, tol), overhang * u, tol)
            fp = plan_polygon(fpc, frame, tol)[0] if fpc is not None else None
            if fp and len(fp) >= 3:
                notes.append("a narrow notch or sharp corner: overhangs %.2f m all round" % overhang)
                ends = gable_edges(fp, ridge) if form in ("gable", "dutch gable") else []
                gables = ends if form == "gable" else []
            else:
                notes.append("the outline is too tight for the overhang: none")
                fp = pts
        n = len(fp)
        z_eave = level_z + T * k - overhang * tn

        def lift(poly):
            return [(p[0], p[1], z_eave + p[2]) for p in poly]

        def inward(i):
            d = _unit(fp[(i + 1) % n][0] - fp[i][0], fp[(i + 1) % n][1] - fp[i][1])
            return (-d[1], d[0])

        def top_chain(face, i):
            """The roof's top along footprint edge i, from fp[i] to fp[i+1] (a gable face minus its base)."""
            return [face[0]] + list(reversed(face[2:])) + [face[1]]

        def keep_lines(lns, clip=None, hmin=None):
            for p, q, kind, a, b in lns:
                if hmin is not None and kind == "verge":
                    if max(p[2], q[2]) <= hmin + 1e-9:
                        continue
                    if min(p[2], q[2]) < hmin:  # the part of a gablet verge above the hip end
                        lo, hi = (p, q) if p[2] < q[2] else (q, p)
                        t = (hmin - lo[2]) / (hi[2] - lo[2])
                        p, q = tuple(lo[j] + (hi[j] - lo[j]) * t for j in range(3)), hi
                if clip is not None:
                    s = clip_segment(p, q, clip)
                    if s is None:
                        continue
                    p, q = s
                lines["eave" if kind == "verge" else kind].append(seg(lift([p])[0], lift([q])[0]))
                hi_lines.append((p, q))

        hi_lines = []
        if form == "dutch gable" and ends:
            hip_faces, hip_lines = skeleton(fp, [run] * n)
            A, strips = list(fp), []
            lens = [math.hypot(fp[(i + 1) % n][0] - fp[i][0], fp[(i + 1) % n][1] - fp[i][1]) for i in range(n)]
            for i in ends:
                d = min(lens[i] / 4.0, 0.45 * min(lens[i - 1], lens[(i + 1) % n]))
                nv = inward(i)
                base = (fp[i][0] + nv[0] * d, fp[i][1] + nv[1] * d)
                de = _unit(fp[(i + 1) % n][0] - fp[i][0], fp[(i + 1) % n][1] - fp[i][1])
                dp = _unit(fp[i][0] - fp[i - 1][0], fp[i][1] - fp[i - 1][1])
                dn = _unit(fp[(i + 2) % n][0] - fp[(i + 1) % n][0], fp[(i + 2) % n][1] - fp[(i + 1) % n][1])
                qa, qb = _meet(base, de, fp[i - 1], dp), _meet(base, de, fp[(i + 1) % n], dn)
                if qa is None or qb is None or math.hypot(qa[0] - fp[i][0], qa[1] - fp[i][1]) > 0.9 * lens[i - 1] or \
                        math.hypot(qb[0] - fp[(i + 1) % n][0], qb[1] - fp[(i + 1) % n][1]) > 0.9 * lens[(i + 1) % n]:
                    continue
                strips.append((i, [fp[i], fp[(i + 1) % n], qb, qa], d * tn))
                A[i], A[(i + 1) % n] = qa, qb
            a_faces, a_lines = skeleton(A, [0.0 if i in [s[0] for s in strips] else run for i in range(n)])
            regions = [[lift(a_faces[i]) for i in sorted(a_faces) if i not in [s[0] for s in strips]]]
            hg = min(s[2] for s in strips) if strips else 0.0
            keep_lines(a_lines, hmin=hg)
            for i, strip, h_g in strips:
                clipped = []
                for j in sorted(hip_faces):
                    cpoly = clip_convex(hip_faces[j], strip)
                    if len(cpoly) >= 3 and abs(area2d([(p[0], p[1]) for p in cpoly])) > 1e-8:
                        clipped.append(cpoly)
                        sloped.append((cpoly, _neg(inward(j))))
                regions.append([lift(c) for c in clipped])
                keep_lines([l for l in hip_lines if l[2] == "hip"], clip=strip)
                lines["eave"].append(seg(lift([(strip[3][0], strip[3][1], h_g)])[0], lift([(strip[2][0], strip[2][1], h_g)])[0]))
                # the gablet: a vertical panel from the hip end up to the underside of the main roof
                ch = [(p[0], p[1], p[2] - T * k) for p in top_chain(a_faces[i], i)]
                panel = _above(ch, h_g)
                if len(panel) >= 3:
                    gablets.append((_panel(lift(panel), 0.025, inward(i), frame, tol), "cladding", "gablet"))
            for i in sorted(a_faces):
                if i not in [s[0] for s in strips]:
                    sloped.append((a_faces[i], _neg(inward(i))))
            gable_faces = {}
        else:
            speeds = [0.0 if i in gables else run for i in range(n)]
            faces, sk_lines = skeleton(fp, speeds)
            if len([i for i in range(n) if speeds[i] > 0 and i not in faces]):
                raise ValueError("the roof faces could not be traced")
            regions = [[lift(faces[i]) for i in sorted(faces) if speeds[i] > 0]]
            keep_lines(sk_lines)
            for i in sorted(faces):
                if speeds[i] > 0:
                    sloped.append((faces[i], _neg(inward(i))))
            gable_faces = {i: faces[i] for i in gables if i in faces}
        for faces_r in regions:
            if faces_r:
                add_layers(lambda a, th, f=faces_r: heightfield_solid(f, a, th, frame, tol))
        # eaves along every non-gable footprint edge, verges along the gables (already in lines from the skeleton)
        for i in range(n):
            if i not in gable_faces:
                lines["eave"].append(seg((fp[i][0], fp[i][1], z_eave), (fp[(i + 1) % n][0], fp[(i + 1) % n][1], z_eave)))
        if fascia > 0:
            # fascias cover the rafter ends (their top level with the top of the structure), barge boards follow the
            # verges, gutters hang outside the fascias
            drop = above_core * k
            gutters = set(i for i in range(n) if i not in gable_faces)
            for i in range(n):
                if i in gable_faces:
                    chain = [(p[0], p[1], z_eave + p[2] - drop) for p in top_chain(gable_faces[i], i)]
                else:
                    chain = [(fp[i][0], fp[i][1], z_eave - drop), (fp[(i + 1) % n][0], fp[(i + 1) % n][1], z_eave - drop)]
                trims.append((band(chain, fascia, 0.025, _neg(inward(i)), frame, tol), "barge" if i in gable_faces else "fascia", "fascia"))
                if i in gutters:
                    trims.append((_gutter(fp, i, z_eave - drop, frame, tol, gutters), "gutter", "gutter"))
        sloped = [(lift(p), f) for p, f in sloped]
        label_z = z_eave
        plan.append(poly_curve(fp, label_z))
        for p, q in hi_lines:
            plan.append(rg.LineCurve(frame.world(p[0], p[1], label_z), frame.world(q[0], q[1], label_z)))
        for poly, f in sloped:
            if f is None or abs(area2d([(p[0], p[1]) for p in poly])) < max(1.0, (arrow_len * 1.5) ** 2):
                continue
            plan += _arrow(frame, label_point([(p[0], p[1]) for p in poly]), f, arrow_len, label_z, "%g°" % round(pitch, 1), text_h)

    # the wall line, dashed, under the roof plan
    dash, gap = 2.0 * scale / 1000.0, 1.0 * scale / 1000.0
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        plan += aw.dashed(frame.world(a[0], a[1], label_z), frame.world(b[0], b[1], label_z), dash * u, gap * u)

    slope = 0.0 if form == "flat" else pitch
    for poly, f in sloped:
        faces_rec.append(_face_info(poly, frame, slope, f))
    plan_area = abs(area2d(fp))
    if form in ("flat", "skillion") and exact_area:  # curved outlines: the exact area, not the polygon's
        plan_area = exact_area
        faces_rec[0]["plan_area"] = round(exact_area, 4)
        faces_rec[0]["area"] = round(exact_area / math.cos(math.radians(slope)), 4)
    infills = []
    if infill and infill > 0 and form != "flat":
        for poly in wall_infill(pts, sloped, T * k, level_z):
            a, b = poly[0], poly[-1]
            d = _unit(b[0] - a[0], b[1] - a[1])
            inw = (-d[1], d[0]) if area2d(pts) > 0 else (d[1], -d[0])
            half = infill / 2.0
            moved = [(p[0] - inw[0] * half, p[1] - inw[1] * half, p[2]) for p in poly]
            infills.append((_panel(moved, infill, inw, frame, tol), "cladding", "infill"))
    zs = [p[2] for poly, _ in sloped for p in poly]
    return {
        "form": form, "solids": [(b, m, fn) for b, m, fn in solids + trims + gablets + infills if b is not None], "lines": lines, "plan": plan,
        "faces": faces_rec, "footprint": fp, "outline": pts, "notes": notes, "thickness": T,
        "eave": min(zs) if zs else level_z, "ridge": max(zs) if zs else level_z,
        "area": sum(f["area"] for f in faces_rec), "plan_area": plan_area,
    }


def wall_infill(pts, sloped, thick, level_z, gap=0.02):
    """Vertical infill polygons (frame metres) along the wall line pts, from level_z up to the roof's underside wherever
    the underside rises more than `gap` above it: gable triangles, skillion sides and high walls. The underside is the
    roof faces' top (sloped, absolute z) less the roof's vertical thickness; each edge's profile breaks where it
    crosses a face boundary (a ridge, a hip), so it follows the faces exactly."""
    faces = [poly for poly, _ in sloped if len(poly) >= 3]

    def plane_z(poly, x, y):
        nx = ny = nz = 0.0  # Newell normal
        for i in range(len(poly)):
            p, q = poly[i], poly[(i + 1) % len(poly)]
            nx += (p[1] - q[1]) * (p[2] + q[2])
            ny += (p[2] - q[2]) * (p[0] + q[0])
            nz += (p[0] - q[0]) * (p[1] + q[1])
        if abs(nz) < 1e-12:
            return None
        p0 = poly[0]
        return p0[2] - (nx * (x - p0[0]) + ny * (y - p0[1])) / nz

    def near(poly, x, y, eps=1e-6):
        if inside(poly, x, y):
            return True
        for i in range(len(poly)):
            (ax, ay), (bx, by) = poly[i - 1][:2], poly[i][:2]
            ex, ey = bx - ax, by - ay
            l2 = ex * ex + ey * ey or 1e-18
            t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / l2))
            if math.hypot(x - ax - t * ex, y - ay - t * ey) < eps:
                return True
        return False

    def under(x, y):
        zs = [z for z in (plane_z(f, x, y) for f in faces if near(f, x, y)) if z is not None]
        return (min(zs) - thick) if zs else level_z

    out = []
    n = len(pts)
    for i in range(n):
        a, b = pts[i], pts[(i + 1) % n]
        dx, dy = b[0] - a[0], b[1] - a[1]
        if math.hypot(dx, dy) < 1e-6:
            continue
        ts = {0.0, 1.0}
        for f in faces:  # where the wall line crosses a face boundary
            for j in range(len(f)):
                (px, py), (qx, qy) = f[j - 1][:2], f[j][:2]
                ex, ey = qx - px, qy - py
                den = dx * ey - dy * ex
                if abs(den) < 1e-12:
                    continue
                t = ((px - a[0]) * ey - (py - a[1]) * ex) / den
                v = ((px - a[0]) * dy - (py - a[1]) * dx) / den
                if 1e-9 < t < 1 - 1e-9 and -1e-9 <= v <= 1 + 1e-9:
                    ts.add(round(t, 9))
        prof = [(a[0] + dx * t, a[1] + dy * t, max(level_z, under(a[0] + dx * t, a[1] + dy * t))) for t in sorted(ts)]
        if max(p[2] for p in prof) - level_z <= gap:
            continue
        poly = [(a[0], a[1], level_z)] + [p for p in prof] + [(b[0], b[1], level_z)]
        clean = []
        for p in poly:  # drop repeats (profile ends on the level)
            if not clean or max(abs(p[k] - clean[-1][k]) for k in range(3)) > 1e-9:
                clean.append(p)
        if len(clean) >= 3:
            out.append(clean)
    return out


def _gutter(fp, i, z_top, frame, tol, gutters, width=0.115, depth=0.1):
    """A gutter outside footprint edge i, past the fascia, its top at z_top (m); it runs on past an outside corner
    when the next edge has a gutter too."""
    n = len(fp)
    p, q = fp[i], fp[(i + 1) % n]
    d = _unit(q[0] - p[0], q[1] - p[1])
    a0 = width + 0.025 if (i - 1) % n in gutters and _cross(fp[i - 1], p, q) > 0 else 0.0
    a1 = width + 0.025 if (i + 1) % n in gutters and _cross(p, q, fp[(i + 2) % n]) > 0 else 0.0
    o = (d[1] * 0.025, -d[0] * 0.025)   # outside the fascia
    chain = [(p[0] - d[0] * a0 + o[0], p[1] - d[1] * a0 + o[1], z_top), (q[0] + d[0] * a1 + o[0], q[1] + d[1] * a1 + o[1], z_top)]
    return band(chain, depth, width, (d[1], -d[0]), frame, tol)


def _neg(v):
    return (-v[0], -v[1])


def _centroid(pts):
    a = area2d(pts)
    if abs(a) < 1e-12:
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    cx = cy = 0.0
    for i in range(len(pts)):
        p, q = pts[i], pts[(i + 1) % len(pts)]
        w = p[0] * q[1] - q[0] * p[1]
        cx += (p[0] + q[0]) * w
        cy += (p[1] + q[1]) * w
    return (cx / (6 * a), cy / (6 * a))


def inside(pts, x, y):
    """Point in a plan polygon (even-odd)."""
    c = False
    for i in range(len(pts)):
        (x0, y0), (x1, y1) = pts[i - 1][:2], pts[i][:2]
        if (y0 > y) != (y1 > y) and x < x0 + (y - y0) * (x1 - x0) / (y1 - y0):
            c = not c
    return c


def label_point(pts):
    """A point well inside a plan polygon for an arrow or label: the centroid when it is inside and clear of the edges,
    else the grid point furthest from them."""
    def clearance(x, y):
        best = 1e18
        for i in range(len(pts)):
            (ax, ay), (bx, by) = pts[i - 1][:2], pts[i][:2]
            ex, ey = bx - ax, by - ay
            l2 = ex * ex + ey * ey or 1e-18
            t = max(0.0, min(1.0, ((x - ax) * ex + (y - ay) * ey) / l2))
            best = min(best, math.hypot(x - ax - t * ex, y - ay - t * ey))
        return best
    c = _centroid([p[:2] for p in pts])
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    span = max(max(xs) - min(xs), max(ys) - min(ys))
    if inside(pts, *c) and clearance(*c) > 0.15 * span:
        return c
    best, at = -1.0, c
    for i in range(1, 16):
        for j in range(1, 16):
            x = min(xs) + (max(xs) - min(xs)) * i / 16.0
            y = min(ys) + (max(ys) - min(ys)) * j / 16.0
            if inside(pts, x, y):
                d = clearance(x, y)
                if d > best:
                    best, at = d, (x, y)
    return at


def _above(chain, h):
    """The part of a vertical profile (points along a line, z = height) above height h, closed along h."""
    out = []
    for i in range(len(chain) - 1):
        p, q = chain[i], chain[i + 1]
        if p[2] >= h:
            out.append(p)
        if (p[2] >= h) != (q[2] >= h):
            t = (h - p[2]) / (q[2] - p[2])
            out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t, h))
    if chain and chain[-1][2] >= h:
        out.append(chain[-1])
    return out


def _panel(poly, thick, inward, frame, tol):
    """A vertical panel from a polygon (frame metres), `thick` deep along the plan unit vector `inward`."""
    f = polygon_brep([frame.world(*p) for p in poly], tol)
    if f is None:
        return None
    v = frame.vector(inward[0], inward[1]) * (thick * frame.u)
    s = f.Faces[0].CreateExtrusion(rg.LineCurve(rg.Point3d.Origin, rg.Point3d.Origin + v), True)
    return _outward(s) if s is not None and s.IsValid and s.IsSolid else None
