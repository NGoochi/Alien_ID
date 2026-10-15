# NODE_INPUTS: outline:list[Curve] | "wall lines to roof over, closed, one roof each (a 10 x 7 m sample room when unwired)"
# NODE_INPUTS: levels:list[object] | "level records (sample levels when unwired)"
# NODE_INPUTS: level:str | "level the roof sits on (the top of the walls): name, code or index (blank: the top level)" default=""
# NODE_INPUTS: form:str | "roof form" default="hip" options="hip|gable|dutch gable|skillion|flat"
# NODE_INPUTS: pitch:float | "pitch (degrees); flat roofs: the fall written on the plan (5 or less)" min=0 max=75 default=22.5
# NODE_INPUTS: overhang:float | "eaves overhang past the wall line (m)" min=0 max=3 default=0.6, gable_overhang:float | "verge overhang at gable ends and skillion sides (m)" min=0 max=3 default=0.3
# NODE_INPUTS: ridge:str | "ridge direction for gable, dutch gable, skillion and flat: auto = along the long side; skillion and flat fall across it" default="auto" options="auto|x|y"
# NODE_INPUTS: flip:bool | "skillion and flat: fall the other way" default=false
# NODE_INPUTS: fascia:float | "fascia and barge board depth (m), with gutters on the eaves; 0 = none" min=0 max=0.6 default=0.2
# NODE_INPUTS: infill:float | "gable ends, skillion sides and high walls: infill up to the roof's underside, this thick (m), centred on the wall line; 0 = leave open" min=0 max=1 default=0.2
# NODE_INPUTS: roof_type:object | "roof_types record (default: metal deck)", site:object | "site record (optional): x/y and placement, north for face aspects, scale for plan text"
# NODE_OUTPUTS: roofs:list[str] | "roof records", solids:list[Brep] | "one solid per layer per roof part, then fascias, barge boards, gutters, gablets and wall infill", materials:list[str] | "material per solid", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: ridges:list[Curve] | "ridge lines (and a skillion's high edge)", hips:list[Curve] | "hip lines", valleys:list[Curve] | "valley lines", eaves:list[Curve] | "roof edges: eaves, verges (gable rakes) and gablet bases"
# NODE_OUTPUTS: plan:list[Curve] | "roof plan: eaves outline, ridges, hips, valleys, the wall line dashed, fall arrows with the pitch", area:list[float] | "area of each roof on the slope (m2)", info:str
# CATEGORY: Building elements
# Roofs: hip, gable, dutch gable, skillion or flat roofs over wall outlines at a level, with eaves and verge overhangs, layered by a roof type; ridges, hips, valleys, eaves, a roof plan and records.
#
# The roof's underside sits on the level at the wall line, so the eaves drop overhang x tan(pitch) below it. Hip, gable
# and dutch-gable faces come from a straight skeleton, so L, T, U and other polygonal outlines (concave too) get proper
# hips and valleys; curves are followed with 10-degree segments (a circle hips to a cone). Gable ends are the short ends
# between two outside corners (ridge auto: shorter than both neighbours, so a rectangle's ends and an L's wing ends), or
# the ends across the ridge direction. Dutch gables hip each end, then stand a gablet from half way up. Skillion and flat
# roofs keep curved outlines exact. Each layer is the roof surface dropped by the layers above, its thickness / cos
# (pitch) deep with plumb edges; fascias cover the rafter ends, with gutters on the eaves. Where the underside rises
# above the level at the wall line (gable ends, skillion sides, a skillion's high wall) `infill` closes the wall up to
# it, so walls built to the level meet the roof. Each curve is its own roof: courtyards (an outline with a hole) aren't
# roofed as one yet.
import time

import alien_bim as bim
import alien_envelope as env

import Rhino.Geometry as rg
import System.Drawing

t0 = time.time()
u = bim.units()
tol = bim.tol()
notes = []

lv = bim.levels_of(levels)
if (level or "").strip():
    L = bim.level_at(lv, level)
    if L is None:
        L = lv[-1]
        notes.append("no level '%s': on %s" % (level, L.get("name")))
else:
    L = lv[-1]
rt = env.type_of(roof_type, "roof_type")
if roof_type and not bim.read(roof_type, "roof_type"):
    notes.append("roof_type has no roof_type record: using metal deck")
s = bim.site_of(site)
frame = env.Frame(s, u)
scale = float(s.get("scale") or 100)
form_k = str(form or "hip").strip().lower()
ridge_k = str(ridge or "auto").strip().lower()
if ridge_k not in ("auto", "x", "y"):
    notes.append("no ridge direction '%s': auto" % ridge)
    ridge_k = "auto"

curves = [c for c in (outline or []) if isinstance(c, rg.Curve)]
sample = not curves
if outline and sample:
    notes.append("outline has no curves: showing the sample room")
if sample:
    pl = bim.site_plane(site, u)
    curves = [rg.PolylineCurve([pl.PointAt(x * u, y * u) for x, y in ((0, 0), (10, 0), (10, 7), (0, 7), (0, 0))])]

# what the covering allows
lo, hi = float(rt.get("min_pitch") or 0), float(rt.get("max_pitch") or 90)
p_eff = 0.0 if form_k == "flat" else float(pitch or 0)
if p_eff < lo and form_k != "flat":
    notes.append("%s needs at least %g° (pitch %g°)" % (rt["name"], lo, p_eff))
elif form_k == "flat" and lo >= 10:
    notes.append("%s needs a pitched roof (%g° or more)" % (rt["name"], lo))
elif p_eff > hi:
    notes.append("%s suits pitches up to %g° (pitch %g°)" % (rt["name"], hi, p_eff))
top_rgb = rt.get("colour")

roofs, solids, materials, colours, ridges, hips, valleys, eaves, plan, area = [], [], [], [], [], [], [], [], [], []
counts = {}
for i, c in enumerate(curves):
    flat = env.closed_flat(c, tol)
    if flat is None:
        notes.append("outline %d is not a closed planar curve: skipped" % i)
        continue
    try:
        r = env.build_roof(flat, form_k, pitch, overhang, gable_overhang, ridge_k, bool(flip), max(0.0, fascia or 0.0), rt,
                           float(L["elevation"]), frame, tol, scale, infill=infill if infill is not None else 0.2)
    except Exception as ex:
        notes.append("outline %d: %s" % (i, ex))
        continue
    rid = "RF-%02d" % (len(roofs) + 1)
    for b, m, role in r["solids"]:
        solids.append(b)
        materials.append(m)
        if top_rgb and role == 0:  # the roofing, in the type's colour
            colours.append(System.Drawing.Color.FromArgb(255, *top_rgb))
        else:
            colours.append(env.colour(m))
    ridges += r["lines"]["ridge"]
    hips += r["lines"]["hip"]
    valleys += r["lines"]["valley"]
    eaves += r["lines"]["eave"]
    plan += r["plan"]
    area.append(round(r["area"], 4))
    counts[r["form"]] = counts.get(r["form"], 0) + 1
    wall = flat.DuplicateCurve()
    wall.Translate(rg.Vector3d(0, 0, float(L["elevation"]) * u))
    fp = r["footprint"]
    roofs.append(bim.pack(
        "roof", id=rid, form=r["form"], pitch=0.0 if r["form"] == "flat" else float(pitch or 0), level=L.get("name"),
        level_code=L.get("code"), elevation=float(L["elevation"]), outline=bim.curve_record(wall, u),
        footprint={"points": [bim.pt(frame.world(p[0], p[1], r["eave"])) for p in fp + fp[:1]], "closed": True},
        overhang=overhang, gable_overhang=gable_overhang, type=rt, thickness=r["thickness"], eave=r["eave"],
        ridge=r["ridge"], area=round(r["area"], 4), plan_area=round(r["plan_area"], 4), faces=r["faces"],
        lines={k: [[bim.pt(x.PointAtStart, u), bim.pt(x.PointAtEnd, u)] for x in v if isinstance(x, rg.LineCurve)]
               for k, v in r["lines"].items()},
        site=s.get("name")))
    for m in r["notes"]:
        if m not in notes:
            notes.append(m)

recs = [bim.first(x, "roof") for x in roofs]
info = "\n".join([
    "%d roof%s%s (%s) on %s (z %.2f m), pitch %s, eaves %.2f m%s" % (
        len(roofs), "" if len(roofs) == 1 else "s", " (sample room)" if sample else "",
        ", ".join("%s x%d" % kv for kv in counts.items()) or form_k, L.get("name"), float(L["elevation"]),
        "%g°" % (pitch or 0) if form_k != "flat" else "flat", overhang or 0,
        ", verges %.2f m" % (gable_overhang or 0) if form_k in ("gable", "skillion") else ""),
    "%s, %.0f mm; %.1f m2 on the slope over %.1f m2 of plan; ridge at z %.2f m" % (
        rt["name"], float(rt["thickness"]) * 1000, sum(area), sum(r["plan_area"] for r in recs),
        max([r["ridge"] for r in recs] or [0])),
    "%d ridges, %d hips, %d valleys; %d solids in %.0f ms" % (len(ridges), len(hips), len(valleys), len(solids), (time.time() - t0) * 1000),
] + notes)
