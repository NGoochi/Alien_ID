# NODE_INPUTS: geometry:list[Geometry] | "what the views show and are framed to (breps, meshes, curves...)", layer:str | "or frame the objects on this Rhino layer (when geometry is unwired)" default=""
# NODE_INPUTS: preset:str | "which views" default="plan + elevations" options="plan + elevations|plan + sections|plans|elevations|axonometrics|all|custom"
# NODE_INPUTS: custom:str | "views for 'custom', comma separated: plan, rcp, top, north, south, east, west, section x, section y, iso SW, iso SE, iso NE, iso NW, persp" default="plan, north, east, section x, iso SW"
# NODE_INPUTS: cut:float | "plan cut height (m): above the lowest point, or above each level" min=0 max=20 default=1.2, levels:list[object] | "level records (from levels): a plan and RCP per level, cut above each"
# NODE_INPUTS: cuts:list[object] | "section lines (curves: the view looks to the left of the line) or planes (looks along the normal); replace section x and y"
# NODE_INPUTS: display:str | "display mode saved with each view (a Rhino display mode name)" default="Technical"
# NODE_INPUTS: margin:float | "space around the geometry in each view (m)" min=0 max=50 default=2
# NODE_INPUTS: prefix:str | "view name prefix: a press replaces the views made with this prefix" default="Alien views"
# NODE_INPUTS: make:bool | "create (or replace) the named views and their clipping planes in Rhino" button
# NODE_OUTPUTS: views:list[str] | "named view names (wire into layout)", planes:list[Plane] | "picture plane of each view: frame centre, X right, Y up", frames:list[Curve] | "the extent of each view"
# NODE_OUTPUTS: sections:list[Curve] | "cut lines through the geometry at each plan cut and section", marks:list[Curve] | "section marks for plans: cut line, arrows and letters", info:str
# CATEGORY: Views & drawings
# Views: plans, reflected ceiling plans, elevations, sections, isometrics and a perspective of any geometry as Rhino named views, clipped and framed with a margin.
#
# Plans look down from a cut (above the lowest point, or above each level with level records: storey plans see only
# their storey); RCPs look up from the same cut (a camera can't mirror, so east and west swap compared with the plan).
# Elevations look at each side from outside, clipped just in front so nothing in front hides the geometry. Section x
# cuts along X through the centre looking north, section y along Y looking west; wired section lines or planes replace
# them. Without the button only the frames are previewed; a press replaces the views made with the same prefix.
import math
import re

import Rhino
import Rhino.Geometry as rg

import alien_bim as ab
import alien_views as av

u = ab.units()
tol = ab.tol()
d = Rhino.RhinoDoc.ActiveDoc
notes = []
X, Y, Z = rg.Vector3d.XAxis, rg.Vector3d.YAxis, rg.Vector3d.ZAxis

# ── what to frame ──

geo = [g for g in (av.as_geometry(x) for x in (geometry or [])) if g is not None]
source = "%d wired objects" % len(geo)
if not geo and layer and str(layer).strip() and d is not None:
    path = str(layer).strip()
    hits = [l for l in d.Layers if not l.IsDeleted and (l.FullPath == path or l.Name == path or l.FullPath.startswith(path + "::"))]
    for l in hits:
        for o in d.Objects.FindByLayer(l) or []:
            if o.ObjectType not in (Rhino.DocObjects.ObjectType.ClipPlane, Rhino.DocObjects.ObjectType.Detail) and o.Geometry is not None:
                geo.append(o.Geometry)
    source = "%d objects on layer '%s'" % (len(geo), path) if hits else ""
    if not hits:
        notes.append("no layer '%s': showing the sample block" % path)
    elif not geo:
        notes.append("nothing to frame on layer '%s': showing the sample block" % path)
if not geo:  # sample: an L-shaped two-storey block with a roof slab, at the origin
    source = "sample block (wire geometry or name a layer)"
    geo = [ab.box(rg.Plane.WorldXY, 0, 18 * u, 0, 8 * u, 0, 6.4 * u), ab.box(rg.Plane.WorldXY, 0, 8 * u, 8 * u, 16 * u, 0, 3.2 * u),
           ab.box(rg.Plane.WorldXY, -0.5 * u, 18.5 * u, -0.5 * u, 8.5 * u, 6.4 * u, 6.7 * u)]

box = rg.BoundingBox.Empty
points = []
for g in geo:
    b = g.GetBoundingBox(True)
    if b.IsValid:
        box.Union(b)
        points += list(b.GetCorners())
if len(points) > 4000:  # many small parts: their union is close enough and much cheaper
    points = list(box.GetCorners())
centre = box.Center
zmin, zmax = box.Min.Z, box.Max.Z
m = max(margin if margin is not None else 2.0, 0.0) * u
cut_h = max(cut if cut is not None else 1.2, 0.0) * u

# ── which views ──

PRESETS = {
    "plan + elevations": "plan, north, south, east, west",
    "plan + sections": "plan, section x, section y",
    "plans": "plan, rcp",
    "elevations": "north, south, east, west",
    "axonometrics": "iso SW, iso SE, iso NE, iso NW",
    "all": "plan, rcp, north, south, east, west, section x, section y, iso SW, iso SE, persp",
}
ALIASES = {"plan": "plan", "plans": "plan", "floor plan": "plan", "rcp": "rcp", "reflected": "rcp", "ceiling": "rcp",
           "reflected ceiling plan": "rcp", "top": "top", "roof": "top", "site": "top",
           "north": "north", "n": "north", "south": "south", "s": "south", "east": "east", "e": "east", "west": "west", "w": "west",
           "section x": "section x", "x": "section x", "section y": "section y", "y": "section y",
           "iso": "iso sw", "axo": "iso sw", "persp": "persp", "perspective": "persp"}
GROUPS = {"elevations": ["north", "south", "east", "west"], "sections": ["section x", "section y"],
          "isos": ["iso sw", "iso se", "iso ne", "iso nw"], "axonometrics": ["iso sw", "iso se", "iso ne", "iso nw"],
          "all": re.split(r",\s*", PRESETS["all"].lower())}
text = PRESETS.get(preset, None) if preset != "custom" else (custom or "")
if text is None:
    notes.append("unknown preset %r: used plan + elevations" % preset)
    text = PRESETS["plan + elevations"]
wanted, ignored = [], []
for tok in re.split(r"[,;\n]+", str(text)):
    t = " ".join(tok.lower().replace("-", " ").split())
    if t not in GROUPS:  # 'north elevation', 'elevation north' -> 'north'
        t = " ".join(re.sub(r"\belevation\b", " ", t).split())
    if not t:
        continue
    if t in GROUPS:
        keys = GROUPS[t]
    elif t in ALIASES:
        keys = [ALIASES[t]]
    elif re.fullmatch(r"(iso|axo) ?(sw|se|ne|nw)", t):
        keys = ["iso " + t[-2:]]
    else:
        ignored.append(tok.strip())
        keys = []
    for k in keys:
        if k not in wanted:
            wanted.append(k)
if ignored:
    notes.append("not a view: %s (try plan, rcp, top, north, section x, iso SW, persp)" % ", ".join(repr(t) for t in ignored))

# section lines or planes wired: they replace section x / y
custom_cuts = []
for i, c in enumerate(cuts or []):
    if isinstance(c, rg.Plane):
        custom_cuts.append(("plane", c))
        continue
    g = av.as_geometry(c)
    if isinstance(g, rg.Curve) and g.PointAtStart.DistanceTo(g.PointAtEnd) > tol:
        custom_cuts.append(("line", g))
    elif c is not None:
        notes.append("section %d is not a curve or plane (or is closed): skipped" % (i + 1))
if custom_cuts:  # in place of the first section x/y asked for, else at the end
    at = next((i for i, w in enumerate(wanted) if w.startswith("section")), len(wanted))
    wanted = [w for w in wanted[:at] if not w.startswith("section")] + ["cuts"] + [w for w in wanted[at:] if not w.startswith("section")]

levels_in = ab.read(levels, "level")
levels_in = sorted(levels_in, key=lambda r: float(r.get("elevation", 0)))

# ── the views ──
# spec: name, kind, d (camera direction), up, centre (target), hw, hh (half frame), clip (plane, normal away from the
#       camera), extra (the far plane of a limited view depth), label (section letter), persp


def spec(name, kind, dv, up, at_depth=None, clip_at=None, depth=None, label=None, persp=False):
    """Frame the geometry seen along dv: the target sits at the clip plane (or the geometry's middle)."""
    dv = rg.Vector3d(dv)
    dv.Unitize()
    up = rg.Vector3d(up)
    up.Unitize()
    right = rg.Vector3d.CrossProduct(dv, up)
    right.Unitize()
    x0, x1, y0, y1, z0, z1 = av.extents(points, centre, right, up, dv)
    depth_at = at_depth if at_depth is not None else (z0 + z1) / 2
    c = centre + right * ((x0 + x1) / 2) + up * ((y0 + y1) / 2) + dv * depth_at
    clip, extra = None, []
    if clip_at is not None:
        clip = rg.Plane(centre + dv * clip_at, right, rg.Vector3d.CrossProduct(dv, right))  # normal = dv
        if depth:  # the far end of the view depth: a plane facing back towards the camera
            extra.append(rg.Plane(centre + dv * (clip_at + depth), right, rg.Vector3d.CrossProduct(right, dv)))
    return {"name": name, "kind": kind, "d": dv, "up": up, "right": right, "centre": c, "hw": max((x1 - x0) / 2 + m, 0.25 * u),
            "hh": max((y1 - y0) / 2 + m, 0.25 * u), "clip": clip, "extra": extra, "depth": depth, "label": label, "persp": persp, "deep": z1 - z0,
            "box": rg.BoundingBox(box.Min - rg.Vector3d(m, m, m), box.Max + rg.Vector3d(m, m, m))}


specs = []
P = av.with_separator(prefix)
dz = centre.Z  # extents are measured from the centre: a world height z sits at depth (centre.Z - z) looking down
lowered = set()


def nm_of(lv, i):
    return str(lv.get("name") or lv.get("code") or "Level %d" % i)


def plan_like(key):
    looking_down = key in ("plan", "top")
    dv = -Z if looking_down else Z
    if key == "top":
        return [spec(P + "Top", "top", dv, Y)]
    kind = "plan" if key == "plan" else "rcp"
    rows = []
    if not levels_in:
        z = zmin + cut_h
        a = (dz - z) if looking_down else (z - dz)
        rows.append(spec(P + ("Plan" if looking_down else "RCP"), kind, dv, Y, at_depth=a, clip_at=a))
        return rows
    for i, lv in enumerate(levels_in):
        e = float(lv.get("elevation", 0)) * u
        nxt = levels_in[i + 1] if i + 1 < len(levels_in) else None
        if not looking_down and (lv.get("roof") or nxt is None):
            continue  # nothing above the roof to look up at
        c_h = cut_h
        if nxt is not None and e + c_h > float(nxt["elevation"]) * u - 0.05 * u:  # keep the cut inside the storey
            c_h = max(float(nxt["elevation"]) * u - e - 0.05 * u, 0.0)
            lowered.add(nm_of(lv, i))
        z = e + c_h
        if looking_down and nxt is None and i > 0:  # the top level (a roof): look down on all of it
            z = max(z, zmax + 0.1 * u)
        a = (dz - z) if looking_down else (z - dz)
        if looking_down:  # storey plans see down to just below their own floor (the lowest sees everything)
            depth = (z - e) + 0.3 * u if i > 0 else None
        else:             # RCPs see up to just above the next floor
            depth = (float(nxt["elevation"]) * u - z) + 0.3 * u
        nm = nm_of(lv, i)
        rows.append(spec(P + nm + (" plan" if looking_down else " RCP"), kind, dv, Y, at_depth=a, clip_at=a, depth=depth))
    return rows


gap = 0.5 * u  # elevation clip planes sit this far in front of the geometry
ELEV = {"north": (-Y, "North elevation"), "south": (Y, "South elevation"), "east": (-X, "East elevation"), "west": (X, "West elevation")}
for key in wanted:
    if key in ("plan", "rcp", "top"):
        specs += plan_like(key)
    elif key in ELEV:
        dv, nm = ELEV[key]
        front = av.extents(points, centre, rg.Vector3d.CrossProduct(dv, Z), Z, dv)[4] - gap  # just before the nearest point
        specs.append(spec(P + nm, "elevation", dv, Z, at_depth=front, clip_at=front))
    elif key == "section x":
        specs.append(spec(P + "Section X", "section", Y, Z, at_depth=0.0, clip_at=0.0, label="X"))
    elif key == "section y":
        specs.append(spec(P + "Section Y", "section", -X, Z, at_depth=0.0, clip_at=0.0, label="Y"))
    elif key.startswith("iso "):
        a = key[4:].upper()
        dv = av.iso_direction(a)
        specs.append(spec(P + "Iso " + a, "iso", dv, av.up_for(dv)))
    elif key == "persp":
        dv = rg.Vector3d(1, 1, -math.tan(math.radians(25)) * math.sqrt(2))
        dv.Unitize()
        specs.append(spec(P + "Perspective", "persp", dv, av.up_for(dv), persp=True))
    elif key == "cuts":
        letters = "ABCDEFGHJKLMNPQRSTUVW"
        for i, (kind, c) in enumerate(custom_cuts):
            lab = letters[i] if i < len(letters) else str(i + 1)
            if kind == "line":
                a, b = c.PointAtStart, c.PointAtEnd
                along = rg.Vector3d(b.X - a.X, b.Y - a.Y, 0)
                if along.Length < tol:
                    notes.append("section %s is vertical: skipped" % lab)
                    continue
                along.Unitize()
                dv = rg.Vector3d.CrossProduct(Z, along)  # look to the left of the line's direction
                origin = rg.Point3d(a.X, a.Y, centre.Z)
                up = Z
            else:
                dv = rg.Vector3d(c.ZAxis)
                dv.Unitize()
                origin = c.Origin
                up = rg.Vector3d(c.YAxis) if abs(dv.Z) > 0.9 else av.up_for(dv)
            off = (origin - centre) * dv
            specs.append(spec(P + "Section " + lab, "section", dv, up, at_depth=off, clip_at=off, label=lab))

# names must be unique (levels can repeat names)
seen = {}
for s in specs:
    n = s["name"]
    if n in seen:
        seen[n] += 1
        s["name"] = "%s (%d)" % (n, seen[n])
    else:
        seen[n] = 1

# ── outputs for the canvas ──

views = [s["name"] for s in specs]
planes, frames = [], []
for s in specs:
    pl = rg.Plane(s["centre"], s["right"], s["up"])
    planes.append(pl)
    frames.append(rg.Rectangle3d(pl, rg.Interval(-s["hw"], s["hw"]), rg.Interval(-s["hh"], s["hh"])).ToNurbsCurve())

sections = []
for s in specs:
    if s["clip"] is not None and s["kind"] in ("plan", "rcp", "section"):
        sections += av.cut(geo, s["clip"], tol)

# section marks: in plan at the lowest point, across the geometry plus half the margin, letters in bubbles
marks = []
diag = max(box.Diagonal.X, box.Diagonal.Y, 1e-6)
r = min(max(diag * 0.025, 0.3 * u), 3 * u)  # bubble radius: about 5 mm on paper at the usual scales
for s in specs:
    if s["kind"] != "section" or abs(s["d"].Z) > 0.9:
        continue
    along = rg.Vector3d(s["right"].X, s["right"].Y, 0)
    if along.Length < 1e-9:
        continue
    along.Unitize()
    look = rg.Vector3d(s["d"].X, s["d"].Y, 0)
    look.Unitize()
    o = rg.Point3d(s["clip"].Origin.X, s["clip"].Origin.Y, zmin)
    xs = [(rg.Point3d(p.X, p.Y, zmin) - o) * along for p in points]
    a, b = o + along * (min(xs) - m / 2 - r), o + along * (max(xs) + m / 2 + r)
    marks.append(rg.LineCurve(a + along * r, b - along * r))
    for end, sgn in ((a, 1), (b, -1)):
        marks.append(rg.Circle(rg.Plane(end, Z), r).ToNurbsCurve())
        tip = end + look * (1.9 * r)
        marks.append(rg.PolylineCurve([end + along * r * 0.999, tip, end - along * r * 0.999]))
        marks += av.letters(s["label"], rg.Plane(end, X, Y), r * 0.9)

# ── info ──

lines = ["%d views of %s: %.1f x %.1f x %.1f m (z %.2f to %.2f m), margin %.1f m" % (
    len(specs), source, box.Diagonal.X / u, box.Diagonal.Y / u, box.Diagonal.Z / u, zmin / u, zmax / u, m / u)]
for s in specs:
    extra = ""
    if s["kind"] in ("plan", "rcp") and s["clip"] is not None:
        extra = ", cut at z %.2f m%s" % (s["clip"].Origin.Z / u, ", sees %.2f m" % (s["depth"] / u) if s["depth"] else "")
    elif s["kind"] == "section":
        extra = ", cut through %s" % ", ".join("%.2f" % (v / u) for v in (s["clip"].Origin.X, s["clip"].Origin.Y, s["clip"].Origin.Z))
    lines.append("  %s: %s, %.1f x %.1f m%s" % (av.title_of(s["name"]), "perspective" if s["persp"] else s["kind"], 2 * s["hw"] / u, 2 * s["hh"] / u, extra))
if lowered:
    notes.append("cut lowered to just under the next level for: %s" % ", ".join(sorted(lowered)))
if levels_in and not any(s["kind"] in ("plan", "rcp") for s in specs):
    notes.append("levels are wired but no plan or rcp is chosen")
if sections:
    lines.append("%d cut lines through the geometry" % len(sections))
mode = av.find_mode(display)
if display and str(display).strip() and mode is None:
    notes.append("no display mode %r (have: %s): views saved without one" % (display, ", ".join(av.mode_names())))

# ── create in Rhino (button only) ──

if make and d is not None:  # (with no views chosen, a press just removes the set)
    stale = []
    gone = av.clear_views(d, "views", P, stale)
    made, skipped = av.save_views(d, specs, "views", P, mode)
    lines.append("Made %d named views%s%s; replaced %d from the last press" % (
        len(made), " in %s" % mode.EnglishName if mode else "", ", %d with clipping planes" % sum(1 for s in specs if s["clip"] is not None and s["name"] in made), len(gone)))
    for n, why in skipped:
        notes.append("skipped %s: %s" % (n, why))
    if stale:
        notes.append("%d layout details showed the old views: press make on the layout again" % len(stale))
    views = made
elif specs:
    lines.append("Press make to create them as named views (prefix '%s')." % P)

info = "\n".join(lines + notes)
