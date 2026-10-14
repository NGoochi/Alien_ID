"""
Alien stairs helpers: stair and balustrade types, first-pass NCC checks, flight / landing / spiral solids, and
balustrades built along polylines, for stair_types, stairs, balustrade_types and balustrades. Keep it next to them and
alien_bim.py.

Records (metres; alien_bim lists the other kinds):
  stair_type       name, construction (concrete | precast | steel | timber), use (public | private), riser, going
                   (targets), tread, nosing, waist, stringer {d, b} or null, risers (closed | open), riser_t,
                   materials {tread, riser, stringer, landing}, limits {riser, going, blondel, flight, landing, opening,
                   width, consecutive, headroom}
  stair            id, type (stair_type), shape (straight | L | U | spiral), turn, align, width, level, top_level,
                   base (z of the lower level), height, risers, riser, going, origin, direction, flights [{risers,
                   start, direction, length, z}], landings [{points, z, length, kind}], void (curve record),
                   checks [{rule, value, min, max, ok, unit}], ok, site; spiral: centre, column, radius, turns, and
                   flights [{risers, z}], landings [{z, angle, top}]
  balustrade_type  name, style, infill (glass | bars | rails | wires | sheet), height, stair_height, spacing, post
                   (m, null = frameless), size (infill thickness or bar / rail size), gap, bottom, handrail {name,
                   shape, b, d} or null, materials {post, infill, handrail}, limits {floor, stair, opening, climb,
                   climb_fall}
  balustrade       id, type (balustrade_type), path (curve record of the base line, after the offset), base, closed, height,
                   stair_height, offset, length, bays, posts, max_gap, sloped, checks, ok, site

NCC numbers here are FIRST-PASS CHECKS, NOT CERTIFICATION: NCC 2022 Volume One Part D2 for public / commercial stairs
and barriers, Volume Two Part H5 (ABCB Housing Provisions Part 11) for houses, and the Housing Provisions' spiral stair
row. Check the current NCC (and a building surveyor) before relying on them.
"""
import math
import re

import Rhino.Geometry as rg
import System.Drawing

import alien_bim as bim


# ── NCC limits (metres) ──────────────────────────────────────

LIMITS = {
    "public": {"riser": [0.115, 0.190], "going": [0.250, 0.355], "blondel": [0.550, 0.700], "flight": [2, 18],
               "landing": 0.75, "opening": 0.125, "width": 1.0, "consecutive": 36, "headroom": 2.0},
    "private": {"riser": [0.115, 0.190], "going": [0.240, 0.355], "blondel": [0.550, 0.700], "flight": [2, 18],
                "landing": 0.75, "opening": 0.125, "width": None, "consecutive": None, "headroom": 2.0},
}
SPIRAL = {"riser": [0.140, 0.220], "going": [0.210, 0.370], "blondel": [0.590, 0.680]}
SOURCE = {"public": "NCC Vol 1 Part D2, public / commercial", "private": "NCC Vol 2 Part H5 and Housing Provisions 11.2, houses"}
BARRIER = {"floor": 1.0, "stair": 0.865, "opening": 0.125, "climb": [0.15, 0.76], "climb_fall": 4.0, "handrail": 0.865}

CONSTRUCTIONS = {
    "concrete": {"label": "concrete in-situ", "stringer": None, "riser_t": 0.0,
                 "materials": {"tread": "concrete", "riser": "concrete", "stringer": "concrete", "landing": "concrete"}},
    "precast": {"label": "precast concrete", "stringer": None, "riser_t": 0.0,
                "materials": {"tread": "precast concrete", "riser": "precast concrete", "stringer": "precast concrete", "landing": "precast concrete"}},
    "steel": {"label": "steel stringers", "stringer": [0.25, 0.012], "riser_t": 0.006,
              "materials": {"tread": "steel", "riser": "steel", "stringer": "steel", "landing": "steel"}},
    "timber": {"label": "timber", "stringer": [0.29, 0.045], "riser_t": 0.018,
               "materials": {"tread": "timber", "riser": "timber", "stringer": "timber", "landing": "timber"}},
}


def construction_key(text):
    s = str(text or "").strip().lower()
    for key in ("precast", "steel", "timber", "concrete"):
        if key in s:
            return key
    if "wood" in s:
        return "timber"
    return "concrete"


def use_key(text):
    s = str(text or "").lower()
    return "private" if ("private" in s or "domestic" in s or "house" in s) else "public"


_EXTRA = {"precast": (198, 194, 184), "stainless": (190, 194, 198), "sheet": (120, 128, 140)}


def colour(material, alpha=None):
    m = str(material or "").lower()
    a = alpha if alpha is not None else (90 if "glass" in m else 255)
    for key, rgb in _EXTRA.items():
        if key in m:
            return System.Drawing.Color.FromArgb(a, *rgb)
    return bim.colour(m, a)


def number(v, default, lo=None, hi=None):
    """A float from an input (None, junk or NaN -> default), clamped to [lo, hi]."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def sizes_mm(text):
    """'250x12', '250 x 12 mm', '290*45' -> [0.25, 0.012] in metres; None when it has no two positive numbers."""
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", str(text or ""))]
    nums = [x for x in nums if x > 0]
    if len(nums) < 2:
        return None
    return [nums[0] / 1000.0, nums[1] / 1000.0]


# ── stair types ──────────────────────────────────────────────

def stair_type(construction="concrete", use="public", name="", riser=0.175, going=0.28, tread=0.04, nosing=0.02,
               waist=0.175, stringer=None, risers="closed"):
    """A stair_type record (dict) and notes. Sizes in metres; stringer is text in mm ('250x12') or None."""
    notes = []
    key = construction_key(construction)
    c = CONSTRUCTIONS[key]
    use = use_key(use)
    riser = number(riser, 0.175, 0.05, 0.4)
    going = number(going, 0.28, 0.1, 0.6)
    tread = number(tread, 0.04, 0.003, 0.2)
    nosing = number(nosing, 0.02, 0.0, 0.08)
    waist = number(waist, 0.175, 0.05, 0.6)
    st = None
    if c["stringer"]:
        st = c["stringer"]
        if str(stringer or "").strip():
            s = sizes_mm(stringer)
            if s is None:
                notes.append("stringer '%s' not read (give depth x thickness in mm, e.g. 250x12): using %.0fx%.0f" % (stringer, st[0] * 1000, st[1] * 1000))
            else:
                st = [max(s[0], 0.05), max(s[1], 0.005)]
        if tread >= riser:
            notes.append("tread %.0f mm is as thick as a riser: made %.0f mm" % (tread * 1000, riser * 500))
            tread = riser * 0.5
    open_r = str(risers or "").strip().lower() == "open" and key in ("steel", "timber")
    if str(risers or "").strip().lower() == "open" and key not in ("steel", "timber"):
        notes.append("open risers need steel or timber: %s steps are closed" % c["label"])
    if not str(name or "").strip():
        name = "%s %s %.0f/%.0f" % (c["label"].capitalize(), "public" if use == "public" else "private", riser * 1000, going * 1000)
    rec = {"alien": "stair_type", "name": str(name).strip(), "construction": key, "use": use, "riser": riser, "going": going,
           "tread": tread if key in ("steel", "timber") else 0.0, "nosing": nosing if key in ("steel", "timber") else 0.0,
           "waist": waist if key in ("concrete", "precast") else 0.0,
           "stringer": {"d": st[0], "b": st[1]} if st else None, "risers": "open" if open_r else "closed",
           "riser_t": c["riser_t"], "materials": dict(c["materials"]), "limits": dict(LIMITS[use])}
    return rec, notes


def default_stair_type():
    return stair_type()[0]


def stair_type_of(value):
    """The first stair_type record on an input, completed with defaults (a hand-made or older record still works);
    the default type when there is none. Returns (record, notes)."""
    t = bim.first(value, "stair_type")
    if not t:
        return default_stair_type(), (["stair_type has no stair_type record: using the default"] if value else [])
    st = t.get("stringer") or {}
    rec, notes = stair_type(t.get("construction"), t.get("use"), t.get("name"), t.get("riser"), t.get("going"),
                            t.get("tread"), t.get("nosing"), t.get("waist"),
                            "%gx%g" % (float(st.get("d", 0)) * 1000, float(st.get("b", 0)) * 1000) if st else None,
                            t.get("risers"))
    for k, v in t.items():  # keep fields we don't know
        rec.setdefault(k, v)
    return rec, notes


# ── checks ───────────────────────────────────────────────────

def rule(name, value, lo=None, hi=None, unit="mm", scale=1000.0, decimals=0):
    ok = (lo is None or value >= lo - 1e-9) and (hi is None or value <= hi + 1e-9)
    return {"rule": name, "value": round(value, 6), "min": lo, "max": hi, "ok": bool(ok), "unit": unit, "scale": scale, "decimals": decimals}


def rule_text(c):
    """'riser 177.8 mm: pass (115–190)'."""
    sc, dec, unit = c.get("scale", 1000.0), c.get("decimals", 0), c.get("unit", "mm")
    v = c["value"]
    if isinstance(v, list):
        val = ", ".join(str(x) for x in v)
    else:
        val = ("%." + str(dec) + "f") % (v * sc)
    lo, hi = c.get("min"), c.get("max")
    f = "%." + str(dec) + "f"
    if lo is not None and hi is not None:
        lim = (f + "–" + f) % (lo * sc, hi * sc)
    elif lo is not None:
        lim = ("at least " + f) % (lo * sc)
    elif hi is not None:
        lim = ("at most " + f) % (hi * sc)
    else:
        lim = ""
    note = c.get("note")
    return "%s %s%s: %s%s%s" % (c["rule"], val, (" " + unit) if unit else "", "pass" if c["ok"] else "FAIL",
                                " (%s)" % lim if lim else "", ("; " + note) if note else "")


def limits_for(t, spiral=False):
    lim = dict(LIMITS[use_key(t.get("use"))])
    if spiral:
        lim.update(SPIRAL)
    return lim


def stair_checks(t, r, going, flights, landings, width, spiral=False, runs=None, headroom=None):
    """First-pass NCC checks of a stair: a list of rule dicts."""
    lim = limits_for(t, spiral)
    out = [rule("riser", r, *lim["riser"], decimals=1), rule("going", going, *lim["going"]),
           rule("2R+G", 2 * r + going, *lim["blondel"])]
    lo, hi = lim["flight"]
    out.append({"rule": "risers per flight", "value": list(flights), "min": lo, "max": hi, "unit": "",
                "ok": bool(flights) and all(lo <= a <= hi for a in flights), "scale": 1, "decimals": 0})
    if landings:
        out.append(rule("landing length", min(landings), lim["landing"], None))
    if t.get("risers") == "open":
        out.append(rule("open riser gap", r - float(t.get("tread") or 0), None, lim["opening"]))
    if lim.get("width"):
        out.append(rule("clear width", width, lim["width"], None))
    if lim.get("consecutive") and runs:
        out.append({"rule": "risers without a 30° turn", "value": max(runs), "min": None, "max": lim["consecutive"], "unit": "",
                    "ok": max(runs) <= lim["consecutive"], "scale": 1, "decimals": 0})
    if headroom is not None:
        out.append(rule("headroom under the turn above", headroom, lim["headroom"], None))
    return out


def type_checks(t):
    """Checks a stair type's own targets can be judged on (before a stair rounds the riser)."""
    lim = limits_for(t)
    r, g = float(t["riser"]), float(t["going"])
    out = [rule("riser", r, *lim["riser"], decimals=1), rule("going", g, *lim["going"]), rule("2R+G", 2 * r + g, *lim["blondel"])]
    if t.get("risers") == "open":
        out.append(rule("open riser gap", r - float(t.get("tread") or 0), None, lim["opening"]))
    return out


def riser_count(height, target, t, spiral=False):
    """Risers for a floor-to-floor height: the nearest whole number to the target riser, kept inside the limits when
    the target itself is inside them (an out-of-range target is honoured, and fails the check)."""
    lo, hi = limits_for(t, spiral)["riser"]
    n = max(1, int(round(height / target)))
    if lo - 1e-9 <= target <= hi + 1e-9:
        nmin, nmax = int(math.ceil(height / hi - 1e-9)), int(math.floor(height / lo + 1e-9))
        if nmin <= nmax:
            n = max(nmin, min(nmax, n))
    return max(1, n)


def suggestion(height, t, spiral=False):
    """A compliant riser count, riser and going for a height: (n, r, g) or None."""
    lim = limits_for(t, spiral)
    lo, hi = lim["riser"]
    nmin, nmax = int(math.ceil(height / hi - 1e-9)), int(math.floor(height / lo + 1e-9))
    if nmin > nmax or height <= 0:
        return None
    n = min(range(max(1, nmin), nmax + 1), key=lambda k: abs(height / k - 0.175))
    r = height / n
    glo, ghi = lim["going"]
    blo, bhi = lim["blondel"]
    g = max(glo, min(ghi, round((0.63 - 2 * r) / 0.005) * 0.005))
    g = max(g, blo - 2 * r)
    g = min(g, bhi - 2 * r)
    if not (glo - 1e-9 <= g <= ghi + 1e-9):
        return None
    return n, r, g


def split_even(n, most):
    """n risers into the fewest flights of at most `most`, as even as possible (first flights get the extra)."""
    if n <= 0:
        return []
    k = max(1, int(math.ceil(n / float(most))))
    base, extra = divmod(n, k)
    return [base + (1 if i < extra else 0) for i in range(k)]


# ── geometry ─────────────────────────────────────────────────

def at(plane, x, y, z, u):
    """A point on a plane from metres (x, y along its axes, z along its normal) -> model units."""
    return plane.PointAt(x * u, y * u, z * u)


def _dedupe(points, closed=True):
    out = []
    for p in points:
        if not out or p.DistanceTo(out[-1]) > bim.tol():
            out.append(p)
    if closed and len(out) > 1 and out[0].DistanceTo(out[-1]) <= bim.tol():
        out.pop()
    return out


def prism(points, vector):
    """A closed solid: the closed planar polygon `points` (model units) swept along `vector` (normal to it)."""
    pts = _dedupe(points)
    if len(pts) < 3 or vector.Length <= bim.tol():
        return None
    crv = rg.PolylineCurve(pts + [pts[0]])
    ok, pl = crv.TryGetPlane(bim.tol())
    if not ok:
        return None
    h = vector.Length if pl.Normal * vector > 0 else -vector.Length
    ext = rg.Extrusion.Create(crv, h, True)
    if ext is None:
        return None
    b = ext.ToBrep(True)
    if b is None or not b.IsValid:
        return None
    if b.SolidOrientation == rg.BrepSolidOrientation.Inward:
        b.Flip()
    return b


def _box(plane, x0, x1, y0, y1, z0, z1, u):
    return bim.box(plane, x0 * u, x1 * u, y0 * u, y1 * u, z0 * u, z1 * u)


def flight_solids(plane, t, a, r, going, width, from_landing, u, nib=True):
    """One flight's solids as [(brep, part)] (model units). plane: origin at the centre of the first riser line, at the
    floor or landing the flight starts from; X up the flight, Y to its left, normal up. a risers of r, going `going`,
    width (metres). The last riser ends at x = (a-1)·going, where the landing or upper floor begins.
    Concrete and precast: a stepped waist slab (the soffit parallel to the pitch, `waist` thick), with a short nib past
    the last riser so the flight meets its landing or floor. Steel and timber: treads (with nosings), riser boards when
    closed, and a stringer each side, outside the width, plumb cut at both ends."""
    out = []
    W, G = width, going
    key = t.get("construction", "concrete")
    if a < 1 or G <= 0 or r <= 0:
        return out
    k = r / G
    cos = G / math.hypot(r, G)
    x_end = (a - 1) * G
    if key in ("concrete", "precast"):
        waist = float(t.get("waist") or 0.175)
        off = waist / cos                                     # the waist measured vertically
        zb = -max(waist, r) if from_landing else 0.0          # the flight springs from the landing's soffit
        e = min(0.1, G * 0.5) if nib else 0.0

        def bottom(x):
            return max(x * k - off, zb)
        if a < 2:
            b = _box(plane, 0, max(e, 0.05), -W / 2, W / 2, zb, r, u)
            return [(b, "tread")] if b else out
        pts = [(0.0, bottom(0.0))]
        for i in range(a - 1):
            pts += [(i * G, (i + 1) * r), ((i + 1) * G, (i + 1) * r)]
        x2 = x_end + e
        pts += [(x_end, a * r), (x2, a * r), (x2, bottom(x2))]
        xb = (zb + off) / k
        if 0 < xb < x2 and bottom(x2) > zb + 1e-9:
            pts.append((xb, zb))
        b = prism([at(plane, x, -W / 2, z, u) for x, z in pts], plane.YAxis * (W * u))
        if b:
            out.append((b, "tread"))
        return out
    # steel / timber
    tt = float(t.get("tread") or 0.04)
    nos = float(t.get("nosing") or 0.0)
    rt = float(t.get("riser_t") or 0.006)
    st = t.get("stringer") or {"d": 0.25, "b": 0.012}
    d, sb = float(st.get("d", 0.25)), float(st.get("b", 0.012))
    for i in range(1, a):
        b = _box(plane, (i - 1) * G - nos, i * G, -W / 2, W / 2, i * r - tt, i * r, u)
        if b:
            out.append((b, "tread"))
    if t.get("risers") != "open":
        for i in range(1, a + 1):
            z0, z1 = (i - 1) * r, i * r - tt
            if z1 - z0 > 0.002:
                b = _box(plane, (i - 1) * G - rt, (i - 1) * G, -W / 2, W / 2, z0, z1, u)
                if b:
                    out.append((b, "riser"))
    if a >= 2:
        lift = min(0.05, 0.3 * d)
        depth = d / cos
        zb = -(tt + d) if from_landing else 0.0
        x_f = -nos - 0.02

        def top(x):
            return r + x * k + lift

        def bottom(x):
            return max(top(x) - depth, zb)
        pts = [(x_f, bottom(x_f)), (x_f, top(x_f)), (x_end, top(x_end)), (x_end, bottom(x_end))]
        xb = (zb - r - lift + depth) / k
        if x_f < xb < x_end and bottom(x_end) > zb + 1e-9:
            pts.append((xb, zb))
        for y0 in (W / 2, -W / 2 - sb):
            b = prism([at(plane, x, y0, z, u) for x, z in pts], plane.YAxis * (sb * u))
            if b:
                out.append((b, "stringer"))
    return out


def landing_solids(plane, t, x0, x1, y0, y1, u, r=0.0):
    """A landing on `plane` (origin and axes at the landing's top surface), extents in metres: a slab for concrete and
    precast (the waist thick, at least a riser), a tray of tread thickness on edge beams of the stringer size for steel
    and timber. [(brep, part)]."""
    key = t.get("construction", "concrete")
    if key in ("concrete", "precast"):
        th = max(float(t.get("waist") or 0.175), r)
        b = _box(plane, x0, x1, y0, y1, -th, 0, u)
        return [(b, "landing")] if b else []
    tt = float(t.get("tread") or 0.04)
    st = t.get("stringer") or {"d": 0.25, "b": 0.012}
    d, sb = float(st.get("d", 0.25)), max(float(st.get("b", 0.012)), 0.01)
    out = []
    b = _box(plane, x0, x1, y0, y1, -tt, 0, u)
    if b:
        out.append((b, "landing"))
    z0, z1 = -tt - d, -tt
    for bx in ((x0, x1, y0, y0 + sb), (x0, x1, y1 - sb, y1), (x0, x0 + sb, y0 + sb, y1 - sb), (x1 - sb, x1, y0 + sb, y1 - sb)):
        b = _box(plane, bx[0], bx[1], bx[2], bx[3], z0, z1, u)
        if b:
            out.append((b, "stringer"))
    return out


def sector(centre, r0, r1, a0, a1, z, u, steps=None):
    """A closed annular sector polygon (model units) about `centre` (Point3d, model units, its z ignored), radii and z in
    metres, angles in radians (CCW from world X)."""
    n = steps or max(2, int(math.ceil(abs(a1 - a0) / math.radians(6))))
    outer = [rg.Point3d(centre.X + r1 * u * math.cos(a0 + (a1 - a0) * i / n), centre.Y + r1 * u * math.sin(a0 + (a1 - a0) * i / n), z * u) for i in range(n + 1)]
    if r0 <= 1e-4:
        return outer + [rg.Point3d(centre.X, centre.Y, z * u)]
    inner = [rg.Point3d(centre.X + r0 * u * math.cos(a0 + (a1 - a0) * i / n), centre.Y + r0 * u * math.sin(a0 + (a1 - a0) * i / n), z * u) for i in range(n, -1, -1)]
    return outer + inner


# ── balustrade types ─────────────────────────────────────────

STYLES = {
    "frameless glass": {"infill": "glass", "size": 0.015, "post": None, "handrail": "none", "spacing": 1.2,
                        "materials": {"post": "stainless steel", "infill": "glass", "handrail": "stainless steel"}},
    "glass and posts": {"infill": "glass", "size": 0.010, "post": 0.05, "handrail": "round 50", "spacing": 1.2,
                        "materials": {"post": "stainless steel", "infill": "glass", "handrail": "stainless steel"}},
    "vertical bars": {"infill": "bars", "size": 0.016, "post": 0.05, "handrail": "round 50", "spacing": 1.2,
                      "materials": {"post": "steel", "infill": "steel", "handrail": "steel"}},
    "horizontal rails": {"infill": "rails", "size": 0.025, "post": 0.05, "handrail": "round 50", "spacing": 1.2,
                         "materials": {"post": "steel", "infill": "steel", "handrail": "steel"}},
    "wires": {"infill": "wires", "size": 0.004, "post": 0.05, "handrail": "round 50", "spacing": 1.2,
              "materials": {"post": "stainless steel", "infill": "stainless steel", "handrail": "stainless steel"}},
    "solid sheet": {"infill": "sheet", "size": 0.006, "post": 0.05, "handrail": "rect 65x20", "spacing": 1.2,
                    "materials": {"post": "aluminium", "infill": "sheet aluminium", "handrail": "aluminium"}},
    "timber": {"infill": "bars", "size": 0.042, "post": 0.09, "handrail": "timber 70x42", "spacing": 1.2,
               "materials": {"post": "timber", "infill": "timber", "handrail": "timber"}},
}
HANDRAILS = {
    "round 50": {"shape": "circle", "b": 0.05, "d": 0.05}, "round 42": {"shape": "circle", "b": 0.042, "d": 0.042},
    "square 50": {"shape": "rect", "b": 0.05, "d": 0.05}, "rect 65x20": {"shape": "rect", "b": 0.065, "d": 0.02},
    "timber 70x42": {"shape": "rect", "b": 0.07, "d": 0.042},
}


def style_key(text):
    s = str(text or "").strip().lower()
    for k in STYLES:
        if s == k:
            return k
    for k, words in (("frameless glass", ("frameless",)), ("glass and posts", ("glass",)), ("vertical bars", ("bar", "baluster", "picket")),
                     ("horizontal rails", ("rail", "horizontal")), ("wires", ("wire", "cable")), ("solid sheet", ("solid", "sheet", "perforated")),
                     ("timber", ("timber", "wood"))):
        if any(w in s for w in words):
            return k
    return "glass and posts"


def balustrade_type(style="glass and posts", name="", height=1.0, stair_height=0.0, spacing=1.2, post=0.0, gap=0.1,
                    bottom=0.05, handrail="by style", size=0.0):
    """A balustrade_type record (dict) and notes. Metres; post and size 0 = by style; stair_height 0 = as height."""
    notes = []
    key = style_key(style)
    s = STYLES[key]
    height = number(height, 1.0, 0.3, 3.0)
    sh = number(stair_height, 0.0, 0.0, 3.0)
    spacing = number(spacing, s["spacing"], 0.2, 6.0)
    gap = number(gap, 0.1, 0.005, 1.0)
    bottom = number(bottom, 0.05, 0.0, 1.0)
    size = number(size, 0.0, 0.0, 0.2) or s["size"]
    p = None
    if s["post"]:
        p = number(post, 0.0, 0.0, 0.4) or s["post"]
    hr_name = str(handrail or "").strip().lower()
    if hr_name in ("", "by style"):
        hr_name = s["handrail"]
    hr = None
    if hr_name != "none":
        hr = HANDRAILS.get(hr_name)
        if hr is None:
            dims = sizes_mm(hr_name)
            nums = re.findall(r"\d+(?:\.\d+)?", hr_name)
            if dims:
                hr = {"shape": "rect", "b": dims[0], "d": dims[1]}
            elif nums:
                hr = {"shape": "circle", "b": float(nums[0]) / 1000.0, "d": float(nums[0]) / 1000.0}
            else:
                notes.append("handrail '%s' not read: using round 50" % handrail)
                hr_name, hr = "round 50", HANDRAILS["round 50"]
        hr = dict(hr, name=hr_name)
    if hr and hr["d"] >= height * 0.5:
        notes.append("handrail deeper than half the height: dropped")
        hr = None
    if not str(name or "").strip():
        name = "%s %.0f" % (key.capitalize(), height * 1000)
    rec = {"alien": "balustrade_type", "name": str(name).strip(), "style": key, "infill": s["infill"], "height": height,
           "stair_height": sh if sh > 0 else height, "spacing": spacing, "post": p, "size": size, "gap": gap,
           "bottom": bottom, "handrail": hr, "materials": dict(s["materials"]), "limits": dict(BARRIER)}
    return rec, notes


def balustrade_type_of(value):
    t = bim.first(value, "balustrade_type")
    if not t:
        return balustrade_type()[0], (["balustrade_type has no balustrade_type record: using the default"] if value else [])
    hr = t.get("handrail")
    rec, notes = balustrade_type(t.get("style"), t.get("name"), t.get("height"), t.get("stair_height"), t.get("spacing"),
                                 t.get("post") or 0.0, t.get("gap"), t.get("bottom"),
                                 (hr.get("name") or ("%gx%g" % (hr.get("b", 0.05) * 1000, hr.get("d", 0.05) * 1000))) if isinstance(hr, dict) else "none",
                                 t.get("size"))
    for k, v in t.items():
        rec.setdefault(k, v)
    return rec, notes


def infill_gap(bt):
    """The clear opening the infill leaves (m): bars, rails and wires at their gap; glass and sheet only at joints."""
    if bt["infill"] in ("bars", "rails", "wires"):
        return float(bt["gap"])
    return 0.02


def barrier_checks(bt, h_floor, h_stair, flat, sloped, gap, bottom, fall=0.0):
    """First-pass NCC barrier checks: heights, openings, climbable elements, handrail on flights."""
    L = BARRIER
    out = []
    if flat:
        out.append(rule("height on floors and landings", h_floor, L["floor"], None))
    if sloped:
        out.append(rule("height above the nosing line", h_stair, L["stair"], None))
    out.append(rule("infill opening", gap, None, L["opening"]))
    out.append(rule("gap under the infill", bottom, None, L["opening"]))
    if bt["infill"] in ("rails", "wires"):
        c = {"rule": "climbable rails (150–760 mm)", "value": round(fall or 0.0, 3), "min": None, "max": L["climb_fall"],
             "unit": "m fall", "scale": 1.0, "decimals": 1, "ok": not fall or fall <= L["climb_fall"]}
        c["note"] = ("horizontal rails can be climbed: not allowed where the floor is more than 4 m above the surface below"
                     if fall and fall > L["climb_fall"] else ("set fall to check" if not fall else "fall within 4 m"))
        out.append(c)
    if sloped:
        hr = bt.get("handrail")
        c = {"rule": "handrail along the flight", "value": round((h_stair if hr else 0.0), 4), "min": L["handrail"], "max": None,
             "unit": "mm", "scale": 1000.0, "decimals": 0, "ok": bool(hr) and h_stair >= L["handrail"] - 1e-9}
        if not hr:
            c["note"] = "no handrail: stair flights need one"
        out.append(c)
    if bt["infill"] == "wires":
        out.append({"rule": "wire tension", "value": round(float(bt["gap"]), 4), "min": None, "max": None, "unit": "mm",
                    "scale": 1000.0, "decimals": 0, "ok": True, "note": "check the NCC wire barrier tables (spacing, post spacing, tension)"})
    return out


# ── balustrade geometry ──────────────────────────────────────

def section_of(shape, b, d):
    return {"shape": shape, "b": b, "d": d}


def vertices_of(points, tol):
    """Points along a path -> vertices [x, y, z_in, z_out]: a point straight above or below the one before (a stair
    flight starting from a landing) becomes one vertex whose base steps from z_in to z_out."""
    out = []
    for p in points:
        if out and math.hypot(p.X - out[-1][0], p.Y - out[-1][1]) <= tol:
            out[-1][3] = p.Z
            continue
        out.append([p.X, p.Y, p.Z, p.Z])
    return out


def offset_vertices(vs, dist, closed):
    """Move vertices sideways in plan by dist (model units, + = left of the direction), mitred at corners."""
    if abs(dist) < 1e-12 or len(vs) < 2:
        return vs
    n = len(vs)

    def seg_dir(i, j):
        dx, dy = vs[j][0] - vs[i][0], vs[j][1] - vs[i][1]
        L = math.hypot(dx, dy) or 1.0
        return dx / L, dy / L
    out = []
    for i in range(n):
        if closed:
            d_in = seg_dir((i - 1) % n, i)
            d_out = seg_dir(i, (i + 1) % n)
        else:
            d_in = seg_dir(i - 1, i) if i > 0 else seg_dir(0, 1)
            d_out = seg_dir(i, i + 1) if i < n - 1 else d_in
        n_in = (-d_in[1], d_in[0])
        n_out = (-d_out[1], d_out[0])
        mx, my = n_in[0] + n_out[0], n_in[1] + n_out[1]
        L = math.hypot(mx, my)
        if L < 1e-9:
            mx, my, k = n_out[0], n_out[1], 1.0
        else:
            mx, my = mx / L, my / L
            k = 1.0 / max(0.25, mx * n_out[0] + my * n_out[1])  # mitre length, capped at 4x for very sharp corners
        out.append([vs[i][0] + mx * dist * k, vs[i][1] + my * dist * k, vs[i][2], vs[i][3]])
    return out


def offset_points(points, dist, closed=False, tol=1e-6):
    """Points (model units) moved sideways in plan by dist (+ = left), mitred; steps straight up stay steps."""
    vs = offset_vertices(vertices_of(points, tol), dist, closed)
    out = []
    for v in vs:
        out.append(rg.Point3d(v[0], v[1], v[2]))
        if abs(v[3] - v[2]) > tol:
            out.append(rg.Point3d(v[0], v[1], v[3]))
    return out


def resample(vs, spacing, closed, u, corner_deg=15.0, max_turn=20.0):
    """Bay vertices: posts at corners (turns over corner_deg, a change between flat and sloped, a step in the base)
    and evenly between them at no more than `spacing` (plan length, model units). Smooth curves come in as many short
    segments and leave as chords no longer than the spacing, each turning at most max_turn degrees (a 1 m radius
    gets 0.35 m chords). Returns [x, y, z_in, z_out, corner]."""
    if closed and len(vs) > 2:
        vs = vs + [list(vs[0])]
    if len(vs) < 2:
        return []

    def slope(i):
        a, b = vs[i], vs[i + 1]
        L = math.hypot(b[0] - a[0], b[1] - a[1])
        return (b[2] - a[3]) / L if L > 1e-12 else 0.0

    def heading(i):
        a, b = vs[i], vs[i + 1]
        return math.atan2(b[1] - a[1], b[0] - a[0])
    corner = [True] + [False] * (len(vs) - 2) + [True]
    for i in range(1, len(vs) - 1):
        turn = abs((heading(i) - heading(i - 1) + math.pi) % (2 * math.pi) - math.pi)
        s0, s1 = slope(i - 1), slope(i)
        if (turn > math.radians(corner_deg) or abs(vs[i][2] - vs[i][3]) > 1e-6 * u
                or (abs(s0) > 0.05) != (abs(s1) > 0.05) or abs(s0 - s1) > 0.05):
            corner[i] = True
    out = []
    i = 0
    while i < len(vs) - 1:
        j = i + 1
        while not corner[j]:
            j += 1
        run = vs[i:j + 1]
        lens = [math.hypot(run[m + 1][0] - run[m][0], run[m + 1][1] - run[m][1]) for m in range(len(run) - 1)]
        total = sum(lens)
        turn = sum(abs((heading(m) - heading(m - 1) + math.pi) % (2 * math.pi) - math.pi) for m in range(i + 1, j))
        # bays at most `spacing` long, and turning at most max_turn on curves (facets stay close to the curve)
        nb = max(1, int(math.ceil(total / spacing - 1e-6)) if spacing > 0 else 1, int(math.ceil(turn / math.radians(max_turn) - 1e-6)))
        if not out:
            out.append([run[0][0], run[0][1], run[0][2], run[0][3], True])
        else:
            out[-1][3] = run[0][3]
        for q in range(1, nb + 1):
            s = total * q / nb
            m, acc = 0, 0.0
            while m < len(lens) - 1 and acc + lens[m] < s - 1e-9:
                acc += lens[m]
                m += 1
            f = (s - acc) / lens[m] if lens[m] > 1e-12 else 1.0
            f = max(0.0, min(1.0, f))
            a, b = run[m], run[m + 1]
            z = a[3] + (b[2] - a[3]) * f
            if q == nb:
                out.append([b[0], b[1], b[2], b[3], True])
            else:
                out.append([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, z, z, False])
        i = j
    return out


def _quad_outline(pts):
    return rg.PolylineCurve(list(pts) + [pts[0]])


def build_balustrade(bv, bt, u, end_posts=True, closed=False, h_floor=None, h_stair=None):
    """Solids, colours and linework of one balustrade along bay vertices `bv` (from resample, model units; a closed
    path repeats its first vertex at the end). h_floor / h_stair (m) override the type's heights. Returns a dict."""
    Z = rg.Vector3d.ZAxis
    hf = (h_floor or bt["height"]) * u
    hs = (h_stair or bt["stair_height"]) * u
    hr = bt.get("handrail")
    rd = hr["d"] * u if hr else 0.0
    rb = hr["b"] * u if hr else 0.0
    p = (bt.get("post") or 0.0) * u
    infill = bt["infill"]
    size = float(bt["size"]) * u
    gap = float(bt["gap"]) * u
    bottom = float(bt["bottom"]) * u
    mats = bt["materials"]
    res = {"solids": [], "colours": [], "parts": [], "plan": [], "elevation": [], "rail": [], "posts": 0, "bays": 0,
           "max_gap": 0.0, "flat": False, "sloped": False, "length": 0.0}
    if len(bv) < 2:
        return res
    nb = len(bv) - 1
    last = len(bv) - 1

    def add(b, part):
        if b is not None:
            res["solids"].append(b)
            res["parts"].append(part)
            res["colours"].append(colour(mats.get(part, "steel")))

    # bays: plan direction and length, base z at each end, height (flights get the stair height)
    bays = []
    for i in range(nb):
        A, B = bv[i], bv[i + 1]
        dx, dy = B[0] - A[0], B[1] - A[1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            bays.append(None)
            continue
        za, zb = A[3], B[2]
        sloped = abs(zb - za) / L > 0.05
        res["sloped" if sloped else "flat"] = True
        bays.append({"d": rg.Vector3d(dx / L, dy / L, 0), "L": L, "za": za, "zb": zb, "h": hs if sloped else hf,
                     "sloped": sloped, "pa": rg.Point3d(A[0], A[1], 0), "pb": rg.Point3d(B[0], B[1], 0)})
        res["length"] += math.hypot(L, zb - za) / u
    res["bays"] = sum(1 for b in bays if b)

    def base_at(bay, s):
        return bay["za"] + (bay["zb"] - bay["za"]) * s / bay["L"]

    # posts at every bay vertex (none for frameless glass); the path ends only with end_posts
    has_post = [False] * len(bv)
    if p > 0:
        for i in range(len(bv)):
            if closed and i == last:
                continue
            if not closed and i in (0, last) and not end_posts:
                continue
            has_post[i] = True

    def post_at(j):
        return has_post[j] or (closed and j == last and has_post[0])

    for i, v in enumerate(bv):
        if not has_post[i]:
            continue
        b_in = bays[i - 1] if i > 0 else (bays[-1] if closed else None)
        b_out = bays[i] if i < nb else None
        tops, dirs = [], rg.Vector3d(0, 0, 0)
        for b, z in ((b_in, "zb"), (b_out, "za")):
            if b:
                tops.append(b[z] + b["h"])
                dirs += b["d"]
        if not tops:
            continue
        if dirs.Length < 1e-9:
            dirs = rg.Vector3d((b_out or b_in)["d"])
        dirs.Unitize()
        z0 = min(v[2], v[3], bv[0][2] if closed and i == 0 else v[2])
        z1 = max(tops) - rd
        if z1 - z0 < 0.05 * u:
            continue
        add(bim.member(rg.Point3d(v[0], v[1], z0), rg.Point3d(v[0], v[1], z1), section_of("rect", p / u, p / u), up=dirs), "post")
        res["posts"] += 1
        q = rg.Point3d(v[0], v[1], 0)
        side = rg.Vector3d.CrossProduct(Z, dirs)
        res["plan"].append(rg.Rectangle3d(rg.Plane(q, dirs, side), rg.Interval(-p / 2, p / 2), rg.Interval(-p / 2, p / 2)).ToNurbsCurve())
        res["elevation"].append(_quad_outline([q - dirs * (p / 2) + Z * z0, q + dirs * (p / 2) + Z * z0,
                                               q + dirs * (p / 2) + Z * z1, q - dirs * (p / 2) + Z * z1]))

    panel = infill in ("glass", "sheet")
    clear = 0.01 * u              # glass or sheet to a post face
    joint = 0.01 * u              # half the joint between frameless panes
    max_gap = 0.0
    under = rd                    # the infill stops under the handrail
    for i, bay in enumerate(bays):
        if not bay:
            continue
        d, L = bay["d"], bay["L"]
        n = rg.Vector3d.CrossProduct(Z, d)
        ends = []
        for j in (i, i + 1):
            if post_at(j):
                ends.append(p / 2 + (clear if panel else 0.0))
            else:
                ends.append(joint if (infill == "glass" and p == 0 and not (j in (0, last) and not closed)) else 0.0)
        sa, sb = ends[0], L - ends[1]
        if sb - sa < 0.02 * u:
            continue
        if panel and p > 0:
            max_gap = max(max_gap, clear)

        def pt(s, z):
            return bay["pa"] + d * s + Z * z
        if hr:
            a = pt(0, bay["za"] + bay["h"] - rd / 2)
            b = pt(L, bay["zb"] + bay["h"] - rd / 2)
            add(bim.member(a, b, section_of(hr["shape"], hr["b"], hr["d"])), "handrail")
            res["rail"].append(rg.LineCurve(a, b))
            res["elevation"].append(_quad_outline([pt(0, bay["za"] + bay["h"] - rd), pt(L, bay["zb"] + bay["h"] - rd),
                                                   pt(L, bay["zb"] + bay["h"]), pt(0, bay["za"] + bay["h"])]))
        if panel:
            top_gap = 0.03 * u if hr else 0.0
            q = [pt(sa, base_at(bay, sa) + bottom), pt(sb, base_at(bay, sb) + bottom),
                 pt(sb, base_at(bay, sb) + bay["h"] - under - top_gap), pt(sa, base_at(bay, sa) + bay["h"] - under - top_gap)]
            if q[3].Z - q[0].Z > 0.05 * u:
                add(prism([x - n * (size / 2) for x in q], n * size), "infill")
                res["elevation"].append(_quad_outline(q))
            if infill == "glass" and p == 0:   # frameless: two spigots under each pane
                for f in (0.25, 0.75):
                    s = sa + (sb - sa) * f
                    z0 = base_at(bay, s)
                    add(bim.member(pt(s, z0), pt(s, z0 + bottom + 0.1 * u), section_of("circle", 0.05, 0.05), up=d), "post")
                max_gap = max(max_gap, 2 * joint)
        elif infill == "bars":
            rail_d, rail_b = max(size, 0.03 * u), max(size, 0.02 * u)
            a = pt(sa, base_at(bay, sa) + bottom + rail_d / 2)
            b = pt(sb, base_at(bay, sb) + bottom + rail_d / 2)
            add(bim.member(a, b, section_of("rect", rail_b / u, rail_d / u)), "infill")
            res["elevation"].append(_quad_outline([a - Z * (rail_d / 2), b - Z * (rail_d / 2), b + Z * (rail_d / 2), a + Z * (rail_d / 2)]))
            Lc = sb - sa
            nbars = max(0, int(math.ceil((Lc - gap) / (size + gap) - 1e-9)))
            g_act = (Lc - nbars * size) / (nbars + 1)
            max_gap = max(max_gap, g_act)
            for k in range(nbars):
                s = sa + g_act * (k + 1) + size * (k + 0.5)
                z0 = base_at(bay, s) + bottom + rail_d
                z1 = base_at(bay, s) + bay["h"] - under
                if z1 - z0 > 0.05 * u:
                    add(bim.member(pt(s, z0), pt(s, z1), section_of("rect", size / u, size / u), up=d), "infill")
                    res["elevation"].append(_quad_outline([pt(s - size / 2, z0), pt(s + size / 2, z0), pt(s + size / 2, z1), pt(s - size / 2, z1)]))
        elif infill in ("rails", "wires"):
            h_clear = bay["h"] - under - bottom
            m = max(1, int(math.ceil(h_clear / (size + gap) - 1e-9)))
            g_act = (h_clear - m * size) / m
            max_gap = max(max_gap, g_act)
            for k in range(m):
                off = bottom + size / 2 + k * (size + g_act)
                a, b = pt(sa, base_at(bay, sa) + off), pt(sb, base_at(bay, sb) + off)
                add(bim.member(a, b, section_of("circle", size / u, size / u)), "infill")
                res["elevation"].append(rg.LineCurve(a, b))
        # plan: the balustrade's two faces along the bay (the handrail's, else the infill's)
        w = max(rb, size, 0.01 * u)
        for sgn in (1, -1):
            res["plan"].append(rg.LineCurve(bay["pa"] + n * (sgn * w / 2), bay["pb"] + n * (sgn * w / 2)))

    # handrail joints: a vertical piece where the rail steps at a vertex with no post, round knuckles at kinks
    if hr:
        joints = [(i, bays[i - 1], bays[i]) for i in range(1, nb)]
        if closed:
            joints.append((0, bays[nb - 1], bays[0]))
        for i, b0, b1 in joints:
            if not b0 or not b1:
                continue
            v = bv[i]
            t0 = b0["zb"] + b0["h"] - rd / 2
            t1 = b1["za"] + b1["h"] - rd / 2
            if abs(t1 - t0) > 0.005 * u:
                if not post_at(i):
                    add(bim.member(rg.Point3d(v[0], v[1], min(t0, t1) - rd / 2), rg.Point3d(v[0], v[1], max(t0, t1) + rd / 2),
                                   section_of(hr["shape"], hr["b"], hr["b"]), up=b1["d"]), "handrail")
            elif hr["shape"] == "circle" and (b0["d"] * b1["d"] < 0.9999 or b0["sloped"] != b1["sloped"]):
                add(rg.Sphere(rg.Point3d(v[0], v[1], t0), rd / 2).ToBrep(), "handrail")
    res["max_gap"] = max_gap / u
    return res
