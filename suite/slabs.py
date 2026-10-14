# NODE_INPUTS: outline:list[Curve] | "slab outlines, closed (a 10 x 7 m sample room when unwired); outlines at several heights each go to the level at their height"
# NODE_INPUTS: levels:list[object] | "level records (sample levels when unwired)"
# NODE_INPUTS: which:str | "levels to slab: floors (all but the top level, which is the roof's), all, suspended (not the lowest or top), indices from 0 = lowest ('1-3'), names or codes ('L01, Level 2')" default="floors"
# NODE_INPUTS: slab_type:list[object] | "slab_types record(s), one per chosen level from the lowest (the last repeats; default: concrete 200)"
# NODE_INPUTS: offset:float | "edge offset from the outline (m): + outward (e.g. to the outer face of the walls), - inward" min=-2 max=5 default=0
# NODE_INPUTS: openings:list[Curve] | "holes, closed curves: stair voids (stairs.voids), shafts, atria; ones crossing the edge cut notches"
# NODE_INPUTS: through:str | "which slabs an opening cuts: its level = the level at the curve's height; auto = that, unless it lies at the outline's height (a sketch: then every slab)" default="auto" options="auto|every level|its level"
# NODE_INPUTS: setdown:float | "top of slab below the level (m), e.g. 0.05 for a wet area" min=0 max=1 default=0
# NODE_INPUTS: site:object | "site record (optional): places the sample outline"
# NODE_OUTPUTS: slabs:list[str] | "slab records", solids:list[Brep] | "one solid per layer per slab", materials:list[str] | "material per solid", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: edges:list[Curve] | "slab edges at the top: outer edges and void edges (for balustrades)", plan:list[Curve] | "plan linework at the top: slab edges and a cross in each void"
# NODE_OUTPUTS: area:list[float] | "net area of each slab (m2)", info:str
# CATEGORY: Building elements
# Slabs: layered floor slabs at chosen levels from closed outlines, with an edge offset, openings (stair voids, shafts) and setdowns; slab records, layer solids, edges and plan.
#
# Every outline gets a slab at every chosen level (its top at the level, less the setdown), unless the outlines sit at
# several heights (levels.plates, say): then each goes to the level at its height. Layers hang down from the top. An
# opening cuts the slabs whose level it sits at (within 1 m), as a stairs node's voids do; one drawn at the outline's
# height, or nowhere near a level, cuts every slab (a shaft). Openings across the edge cut notches; overlapping ones are
# merged. `which` defaults to every level but the top one, which the roofs node covers: type `all` to slab it too.
import time
from collections import OrderedDict

import alien_bim as bim
import alien_envelope as env

import Rhino.Geometry as rg

t0 = time.time()
u = bim.units()
tol = bim.tol()
notes = []

lv = bim.levels_of(levels)
chosen, problems = env.pick_levels(lv, which)
notes += problems
types = bim.read(slab_type, "slab_type")
if not types and any(str(x).strip() for x in (slab_type or []) if x is not None):
    notes.append("slab_type has no slab_type records: using concrete 200")

curves = [c for c in (outline or []) if isinstance(c, rg.Curve)]
sample = not curves
if outline and sample:
    notes.append("outline has no curves: showing the sample room")
if sample:
    pl = bim.site_plane(site, u)
    pts = [pl.PointAt(x * u, y * u) for x, y in ((0, 0), (10, 0), (10, 7), (0, 7), (0, 0))]
    curves = [rg.PolylineCurve(pts)]

outs = []  # (flat closed outline after the offset, height it was drawn at (m))
for i, c in enumerate(curves):
    f = env.closed_flat(c, tol)
    if f is None:
        notes.append("outline %d is not a closed planar curve: skipped" % i)
        continue
    g = env.offset_closed(f, (offset or 0.0) * u, tol)
    if g is None:
        notes.append("outline %d vanishes at an edge offset of %.2f m: skipped" % (i, offset))
        continue
    outs.append((g, c.GetBoundingBox(True).Center.Z / u))
by_height = len(set(round(z, 2) for _, z in outs)) > 1
heights = set(round(z, 2) for _, z in outs)

ops = []  # (flat closed opening, height (m), the level it sits at or None)
for i, c in enumerate(o for o in (openings or []) if o is not None):
    f = env.closed_flat(c, tol) if isinstance(c, rg.Curve) else None
    if f is None:
        notes.append("opening %d is not a closed planar curve: skipped" % i)
        continue
    z = c.GetBoundingBox(True).Center.Z / u
    ops.append((f, z, env.nearest_level(lv, z)))
mode = str(through or "auto").strip().lower()


def cuts(op, level):
    _, z, at = op
    if mode == "every level":
        return True
    if mode == "its level":
        return at is level
    if at is None or (not by_height and round(z, 2) in heights):
        return True   # a sketch at the outline's height, or nowhere near a level: a shaft through every slab
    return at is level


slabs, solids, materials, colours, edges, plan, area = [], [], [], [], [], [], []
regions = {}  # (outline index, openings) -> (faces, used, holes, notes): the same at every level
used_ops, thin_all = set(), set()
for li, L in enumerate(chosen):
    t = env.type_of(types[min(li, len(types) - 1)], "slab_type") if types else env.default_slab_type()
    keep, thin = env.solid_layers(t, tol / u)
    thin_all.update(l["name"] for l in thin)
    top = float(L["elevation"]) - (setdown or 0.0)
    n = 0
    for ci, (c, cz) in enumerate(outs):
        if by_height and env.nearest_level(lv, cz, 1e9) is not L:
            continue
        mine = [k for k, op in enumerate(ops) if cuts(op, L)]
        key = (ci, tuple(mine))
        if key not in regions:
            regions[key] = env.slab_region(c, [ops[k][0] for k in mine], tol)
        faces, used, holes, rnotes = regions[key]
        for m in rnotes:
            if m not in notes:
                notes.append(m)
        if not faces:
            continue
        used_ops.update(k for k in mine if any(ops[k][0] is o for o in used))
        n += 1
        sid = "SL-%s-%d" % (L.get("code") or L.get("name") or li, n)
        net = 0.0
        loops = []
        for f in faces:
            amp = rg.AreaMassProperties.Compute(f)
            net += amp.Area / (u * u) if amp else 0.0
            a = 0.0
            for l in keep:
                th = float(l["thickness"])
                b = env.drop_prism(f, a - top, th, u)
                if b is not None:
                    solids.append(b)
                    materials.append(l["material"])
                    colours.append(env.colour(l["material"]))
                a += th
            for e in rg.Curve.JoinCurves(f.DuplicateNakedEdgeCurves(True, True), tol) or []:
                e.Translate(rg.Vector3d(0, 0, top * u))
                loops.append(e)
        edges += loops
        plan += loops
        for h in holes:
            for x in env.void_cross(h, tol):
                x.Translate(rg.Vector3d(0, 0, top * u))
                plan.append(x)
        rec_outline = c.DuplicateCurve()
        rec_outline.Translate(rg.Vector3d(0, 0, top * u))
        rec_ops = []
        for o in used:
            o2 = o.DuplicateCurve()
            o2.Translate(rg.Vector3d(0, 0, top * u))
            rec_ops.append(bim.curve_record(o2, u))
        thick = float(t["thickness"])
        slabs.append(bim.pack("slab", id=sid, level=L.get("name"), level_code=L.get("code"), elevation=float(L["elevation"]),
                              top=top, bottom=top - thick, thickness=thick, setdown=setdown or 0.0, type=t,
                              outline=bim.curve_record(rec_outline, u), openings=rec_ops, area=round(net, 4),
                              perimeter=round(sum(e.GetLength() for e in loops) / u, 4), edge_offset=offset or 0.0,
                              site=bim.site_of(site).get("name")))
        area.append(round(net, 4))

if ops and len(used_ops) < len(ops):
    notes.append("%d opening%s cut no slab (outside the outlines, or at a level not chosen)" % (len(ops) - len(used_ops), "" if len(ops) - len(used_ops) == 1 else "s"))
if thin_all:
    notes.append("too thin to model at this tolerance (kept in the records): " + ", ".join(sorted(thin_all)))
if not chosen:
    notes.append("no levels chosen")
recs = [bim.first(s, "slab") for s in slabs]
by_type = {}
for r in recs:
    by_type.setdefault(r["type"]["name"], [0, r["thickness"]])[0] += 1
info = "\n".join([
    "%d slab%s%s at %s, %.1f m2 in all" % (len(slabs), "" if len(slabs) == 1 else "s", " (sample outline)" if sample else "",
                                           ", ".join(OrderedDict((r["level"], 1) for r in recs)) or "no levels", sum(area)),
    "Types: " + (", ".join("%s x%d (%.0f mm)" % (k, v[0], v[1] * 1000) for k, v in by_type.items()) or "none"),
    "Edge offset %+.2f m, setdown %.2f m, %d opening%s (%s)%s" % (offset or 0.0, setdown or 0.0, len(ops), "" if len(ops) == 1 else "s", mode,
                                                         ", outlines matched to levels by height" if by_height else ""),
    "%d solids in %.0f ms" % (len(solids), (time.time() - t0) * 1000),
] + notes)
