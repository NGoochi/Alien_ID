# NODE_INPUTS: path:list[Curve] | "wall location lines: lines, polylines, arcs, splines, closed outlines (a sample room when unwired)"
# NODE_INPUTS: wall_type:list[object] | "wall_types record(s), one per path (the last repeats; default: generic 200 mm)"
# NODE_INPUTS: levels:list[object] | "level records (sample levels when unwired)"
# NODE_INPUTS: base:str | "base level: name, code or index (blank: the first)" default="", top:str | "top level: name, code or index (blank: the level above the base)" default=""
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
# offset up to the top level (the next level up by default) unless `height` is given.
import time

import alien_bim as bim
import alien_walls as aw

import Rhino.Geometry as rg

t0 = time.time()
u = bim.units()
notes = []

lv = bim.levels_of(levels)
base_lv = bim.level_at(lv, base if (base or "").strip() else None)
if base_lv is None:
    base_lv = lv[0]
    notes.append("no level '%s': using %s" % (base, base_lv.get("name")))
floor = float(base_lv["elevation"])
z0 = floor + (offset or 0.0)
top_lv = None
if height and height > 0:
    h = float(height)
else:
    if (top or "").strip():
        top_lv = bim.level_at(lv, top)
        if top_lv is None:
            notes.append("no level '%s': using the level above" % top)
        elif float(top_lv["elevation"]) <= z0 + 1e-6:
            notes.append("top level %s is not above the base: using the level above" % top_lv.get("name"))
            top_lv = None
    if top_lv is None:
        top_lv = bim.level_above(lv, base_lv)
    h = (float(top_lv["elevation"]) if top_lv else bim.level_top(lv, base_lv)) - z0
    if h <= 0.01:
        notes.append("the top is not above the base (%.2f m): walls made 3.0 m high" % h)
        h = 3.0

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

records = []
for i, c in enumerate(curves):
    flat = c.DuplicateCurve()
    flat.Transform(rg.Transform.PlanarProjection(rg.Plane.WorldXY))
    if flat.GetLength() < 0.05 * u:
        notes.append("path %d is too short in plan: skipped" % i)
        continue
    wt = types[min(len(records), len(types) - 1)] if types else aw.default_type()
    records.append(aw.make_wall("WL%02d" % (len(records) + 1), wt, flat, z0, h, location or "centre", bool(flip),
                                floor=floor, cut=cut if cut is not None else 1.0, level=base_lv.get("name"),
                                top_level=top_lv.get("name") if top_lv else None, u=u))
for r in records:
    r["site"] = bim.site_of(site).get("name")

if join and len(records) > 1:
    notes += aw.join(records, u)

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
info = "\n".join([
    "%d wall%s%s, %.2f m long in all, %.2f m high from %s%s (z %.2f to %.2f m)" % (
        len(records), "" if len(records) == 1 else "s", " (sample room)" if sample else "", length, h,
        base_lv.get("name"), (" %+.2f m" % offset) if offset else "", z0, z0 + h),
    "Types: " + ", ".join("%s x%d (%.0f mm)" % (k, n, 1000 * next(float(r["thickness"]) for r in records if r["type"]["name"] == k)) for k, n in by_type.items()),
    "Location line: %s%s; plan cut %.2f m above %s" % (location or "centre", ", flipped" if flip else "", cut if cut is not None else 1.0, base_lv.get("name")),
    "%d solids in %.0f ms" % (len(solids), (time.time() - t0) * 1000),
] + notes)
