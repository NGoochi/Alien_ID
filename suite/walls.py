# NODE_INPUTS: path:list[Curve] | "wall location lines: lines, polylines, arcs, splines, closed outlines (a sample room when unwired)"
# NODE_INPUTS: wall_type:list[object] | "wall_types record(s), one per path (the last repeats; default: generic 200 mm)"
# NODE_INPUTS: levels:list[object] | "level records (sample levels when unwired)"
# NODE_INPUTS: base:str | "base level: name, code or index (blank: the first); 'all' = every storey, or several with commas" default="", top:str | "top level: name, code or index (blank: the level above the base)" default=""
# NODE_INPUTS: height:float | "height (m); 0 = up to the top level" min=0 max=30 default=0, offset:float | "base offset from the base level (m)" min=-5 max=5 default=0
# NODE_INPUTS: location:str | "which line of the wall the path is" default="centre" options="centre|exterior|interior|core centre"
# NODE_INPUTS: flip:bool | "swap the exterior and interior sides" default=false, join:bool | "clean joins: mitre L corners, trim walls that butt into others" default=true
# NODE_INPUTS: cut:float | "plan cut height above the base level (m)" min=0 max=3 default=1.0, site:object | "site record (optional): places the sample room"
# NODE_OUTPUTS: walls:list[str] | "wall records (for doors and windows)", solids:list[Brep] | "one solid per layer per wall", materials:list[str] | "material per solid", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: plan:list[Curve] | "plan linework at the cut height: every layer boundary and the wall ends", centrelines:list[Curve] | "centre of each wall's thickness, at its base", info:str
# CATEGORY: Building elements
# Walls: layered walls along lines, polylines, arcs or closed outlines, from a base level to a top level, with plan linework.
#
# Each path is one wall: polylines and closed outlines get mitred corners, curves stay curved. The exterior is on the
# left of the path's direction (seen from above); closed outlines put it outside whichever way they were drawn; flip
# swaps. With join on, two walls ending at the same point are mitred (L), and a wall ending inside another is
# trimmed back to that wall's face (T); walls that cross (X) are left overlapping. Height runs from the base level +
# offset up to the top level (the next level up by default) unless `height` is given. base 'all' (or several levels)
# repeats the paths on every storey, each from its level to the next (roof levels get none), joined storey by storey;
# doors and windows then find each storey's walls (windows' wall 'all' puts them on every floor).
import time

import alien_bim as bim
import alien_walls as aw

import Rhino.Geometry as rg

t0 = time.time()
u = bim.units()
notes = []

lv = bim.levels_of(levels)


def bases_named(text):
    """The base levels: blank = the first; 'all' = every storey but roof levels; or several, with commas."""
    key = (text or "").strip()
    if key.lower() in ("all", "*", "storeys", "every storey"):
        if any("roof" in l for l in lv):
            got = [l for l in lv if not l.get("roof")]
        else:
            got = lv[:-1]  # sample levels: the top one is the roof
        return got or lv[:1]
    out = []
    for tok in (key.split(",") if "," in key else [key]):
        tok = tok.strip()
        l = bim.level_at(lv, tok if tok else None)
        if l is None:
            l = lv[0]
            notes.append("no level '%s': using %s" % (tok, l.get("name")))
        if not any(l is o for o in out):
            out.append(l)
    return out


bases = bases_named(base)


def storey(base_lv):
    """(bottom z, height, top level) of the walls on one base level."""
    z0 = float(base_lv["elevation"]) + (offset or 0.0)
    if height and height > 0:
        return z0, float(height), None
    top_lv = None
    if (top or "").strip() and len(bases) == 1:
        top_lv = bim.level_at(lv, top)
        if top_lv is None:
            notes.append("no level '%s': using the level above" % top)
        elif float(top_lv["elevation"]) <= z0 + 1e-6:
            notes.append("top level %s is not above the base: using the level above" % top_lv.get("name"))
            top_lv = None
    elif (top or "").strip():
        notes.append("top is ignored with several base levels: each storey runs to the level above")
    if top_lv is None:
        top_lv = bim.level_above(lv, base_lv)
    h = (float(top_lv["elevation"]) if top_lv else bim.level_top(lv, base_lv)) - z0
    if h <= 0.01:
        notes.append("the top is not above %s (%.2f m): walls made 3.0 m high" % (base_lv.get("name"), h))
        h = 3.0
    return z0, h, top_lv


curves = [c for c in (path or []) if isinstance(c, rg.Curve)]
sample = not curves
if path and sample:
    notes.append("path has no curves: showing the sample room")
if sample:
    # a 10 x 7 m room and an internal wall that stops short, on the site plane
    pl = bim.site_plane(site, u)
    def sp(x, y):
        return pl.PointAt(x * u, y * u)
    curves = [rg.PolylineCurve([sp(0, 0), sp(10, 0), sp(10, 7), sp(0, 7), sp(0, 0)]), rg.LineCurve(sp(6, 0), sp(6, 4.2))]

types = bim.read(wall_type, "wall_type")
if wall_type and not types:
    notes.append("wall_type has no wall_type records: using generic 200 mm")

flats = []
for i, c in enumerate(curves):
    flat = c.DuplicateCurve()
    flat.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
    if flat.GetLength() < 0.05 * u:
        notes.append("path %d is too short in plan: skipped" % i)
        continue
    flats.append(flat)

records, spans = [], []
for base_lv in bases:
    z0, h, top_lv = storey(base_lv)
    spans.append((base_lv, z0, h))
    floor = float(base_lv["elevation"])
    group = []
    for k, flat in enumerate(flats):
        wt = types[min(k, len(types) - 1)] if types else aw.default_type()
        group.append(aw.make_wall("WL%02d" % (len(records) + len(group) + 1), wt, flat.DuplicateCurve(), z0, h,
                                  location or "centre", bool(flip), floor=floor, cut=cut if cut is not None else 1.0,
                                  level=base_lv.get("name"), top_level=top_lv.get("name") if top_lv else None, u=u))
    if join and len(group) > 1:
        notes += aw.join(group, u)  # storey by storey: walls stacked above each other never join
    records += group
for r in records:
    r["site"] = bim.site_of(site).get("name")
notes = list(dict.fromkeys(notes))

walls, solids, materials, colours, plan, centrelines = [], [], [], [], [], []
for r in records:
    try:
        w = aw.wall_of(r)
    except Exception as ex:
        notes.append("%s: %s" % (r["id"], ex))
        continue
    pieces = w.solids()
    if len(pieces) < len(w.layers):
        notes.append("%s: %d of %d layers could not be built (a curve too tight for the wall's thickness?)" % (r["id"], len(w.layers) - len(pieces), len(w.layers)))
    for b, layer in pieces:
        solids.append(b)
        materials.append(layer["material"])
        colours.append(aw.colour(layer["material"]))
    plan += w.plan()
    cl = w.centreline()
    if cl is not None:
        centrelines.append(cl)
    walls.append(bim.dump(r))

length = sum(float(r["length"]) for r in records)
by_type = {}
for r in records:
    by_type[r["type"]["name"]] = by_type.get(r["type"]["name"], 0) + 1
if len(spans) == 1:
    b0, z0, h = spans[0]
    where = "%.2f m high from %s%s (z %.2f to %.2f m)" % (h, b0.get("name"), (" %+.2f m" % offset) if offset else "", z0, z0 + h)
else:
    hs = [h for _, _, h in spans]
    where = "on %d storeys (%s), %s m high" % (len(spans), ", ".join(str(b.get("name")) for b, _, _ in spans),
                                             "%.2f" % hs[0] if max(hs) - min(hs) < 1e-6 else "%.2f-%.2f" % (min(hs), max(hs)))
info = "\n".join([
    "%d wall%s%s, %.2f m long in all, %s" % (
        len(records), "" if len(records) == 1 else "s", " (sample room)" if sample else "", length, where),
    "Types: " + ", ".join("%s x%d (%.0f mm)" % (k, n, 1000 * next(float(r["thickness"]) for r in records if r["type"]["name"] == k)) for k, n in by_type.items()),
    "Location line: %s%s; plan cut %.2f m above each base level" % (location or "centre", ", flipped" if flip else "", cut if cut is not None else 1.0),
    "%d solids in %.0f ms" % (len(solids), (time.time() - t0) * 1000),
] + notes)
