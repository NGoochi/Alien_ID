# NODE_INPUTS: construction:str | "how the stair is built" default="concrete in-situ" options="concrete in-situ|steel stringers|timber|precast"
# NODE_INPUTS: use:str | "sets the NCC limits: public / commercial (Vol 1) or private / domestic (Vol 2)" default="public / commercial" options="public / commercial|private / domestic"
# NODE_INPUTS: name:str | "type name (blank: from the construction and sizes)" default=""
# NODE_INPUTS: riser:float | "target riser (mm): each stair rounds it to a whole number of risers; NCC 115–190" min=100 max=250 default=175
# NODE_INPUTS: going:float | "going (mm), nosing to nosing; NCC 250–355 public, 240–355 private" min=150 max=450 default=280
# NODE_INPUTS: tread:float | "tread thickness (mm): steel and timber" min=5 max=100 default=40, nosing:float | "nosing projection (mm): steel and timber" min=0 max=50 default=20
# NODE_INPUTS: waist:float | "waist and landing slab thickness (mm): concrete and precast" min=80 max=400 default=175
# NODE_INPUTS: stringer:str | "stringer depth x thickness in mm, e.g. '250x12' (blank: steel 250x12 plate, timber 290x45)" default=""
# NODE_INPUTS: risers:str | "riser boards (steel and timber; concrete steps are always closed)" default="closed" options="closed|open"
# NODE_INPUTS: plane:Plane | "where the swatch sits (default: world origin)", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: stair_type:str | "stair_type record for stairs", swatch:list[Brep] | "four steps and a landing edge, 1 m wide", colours:list[Color] | "colour per swatch solid"
# NODE_OUTPUTS: section:list[Curve] | "the swatch cut through its middle (stringers seen beyond)", info:str
# CATEGORY: Building elements
# Stair type: how a stair is built (concrete in-situ, precast, steel stringers, timber), its riser and going targets, treads, nosings, stringers, open or closed risers, and the NCC limits its use sets.
#
# The stairs node reads this type: it rounds the target riser to a whole number of risers for each floor-to-floor
# height (staying inside the NCC range when the target is inside it), keeps the going, and checks the result. Public
# use takes NCC Volume 1 limits (goings 250–355 mm, clear width 1 m, at most 36 risers without a turn), private use
# Volume 2 (goings 240–355 mm); both: risers 115–190 mm, 2R+G 550–700 mm, 2–18 risers a flight, landings 750 mm,
# openings in open risers under 125 mm. These are first-pass design checks, not certification. Concrete and precast
# flights are a stepped waist slab (waist measured square to the pitch); steel and timber flights are treads with
# nosings between two stringers (outside the stair width), with riser boards unless open. Sizes are mm here; the
# record is in metres.
import alien_bim as bim
import alien_stairs as ss

import Rhino.Geometry as rg

u = bim.units()
t, notes = ss.stair_type(construction, use, name, ss.number(riser, 175) / 1000.0, ss.number(going, 280) / 1000.0,
                          ss.number(tread, 40) / 1000.0, ss.number(nosing, 20) / 1000.0, ss.number(waist, 175) / 1000.0,
                          stringer, risers)
stair_type = bim.dump(t)
if not any(w in str(construction or "").lower() for w in ("concrete", "precast", "steel", "timber", "wood")):
    notes.append("construction '%s' not known: %s" % (construction, ss.CONSTRUCTIONS[t["construction"]]["label"]))

# Swatch: four risers up from the plane's origin along its Y, 1 m wide (X 0..1), with the edge of a landing at the
# top: seen from the front-right in an iso view (and the library thumbnail), steps and side profile both show.
base = plane if isinstance(plane, rg.Plane) and plane.IsValid else rg.Plane.WorldXY
W, n = 1.0, 4
r, g = t["riser"], t["going"]
fp = rg.Plane(base.PointAt(W / 2 * u, 0), base.YAxis, -base.XAxis)
parts = ss.flight_solids(fp, t, n, r, g, W, False, u, nib=False)
lp = rg.Plane(fp.PointAt((n - 1) * g * u, 0, n * r * u), fp.XAxis, fp.YAxis)
parts += ss.landing_solids(lp, t, 0, 0.4, -W / 2, W / 2, u, r)
swatch = [b for b, _ in parts]
colours = [ss.colour(t["materials"].get(p, "concrete")) for _, p in parts]

# Section: cut through the middle; steel and timber add the near stringer's outline, seen beyond the cut.
section = []
cut = rg.Plane(fp.Origin, fp.XAxis, fp.ZAxis)
for b, p in parts:
    ok, crvs, pts = rg.Intersect.Intersection.BrepPlane(b, cut, bim.tol())
    if ok and crvs:
        section += [c for c in rg.Curve.JoinCurves(crvs) or crvs]
if t.get("stringer"):
    sb = t["stringer"]["b"]
    beyond = rg.Plane(fp.PointAt(0, -(W / 2 + sb / 2) * u), fp.XAxis, fp.ZAxis)
    for b, p in parts:
        if p != "stringer":
            continue
        ok, crvs, pts = rg.Intersect.Intersection.BrepPlane(b, beyond, bim.tol())
        if ok and crvs:
            for c in rg.Curve.JoinCurves(crvs) or crvs:
                c.Translate(fp.YAxis * ((W / 2 + sb / 2) * u))
                section.append(c)

lim = ss.limits_for(t)
checks = ss.type_checks(t)
if t["construction"] in ("concrete", "precast"):
    desc = "waist %.0f mm" % (t["waist"] * 1000)
else:
    desc = "treads %.0f mm, nosing %.0f mm, stringers %.0fx%.0f, %s risers" % (
        t["tread"] * 1000, t["nosing"] * 1000, t["stringer"]["d"] * 1000, t["stringer"]["b"] * 1000, t["risers"])
lines = [
    "%s: %s, %s use (%s)" % (t["name"], ss.CONSTRUCTIONS[t["construction"]]["label"], t["use"], ss.SOURCE[t["use"]]),
    "Targets: riser %.0f mm, going %.0f mm, 2R+G %.0f mm; %s" % (r * 1000, g * 1000, (2 * r + g) * 1000, desc),
    "Checks of the targets (first-pass, not certification):",
] + ["  " + ss.rule_text(c) for c in checks] + [
    "Each stair rounds the riser to its floor-to-floor height, then checks 2–%d risers a flight, landings of %.0f mm%s." % (
        lim["flight"][1], lim["landing"] * 1000, ", a %.0f mm clear width and %d risers without a turn" % (lim["width"] * 1000, lim["consecutive"]) if lim.get("width") else ""),
]
if not all(c["ok"] for c in checks if c["rule"] in ("riser", "going", "2R+G")):
    s = ss.suggestion(3.0, t)
    if s:
        lines.append("A compliant pair for a 3.0 m storey: %d risers of %.1f mm with a %.0f mm going" % (s[0], s[1] * 1000, s[2] * 1000))
if t.get("risers") == "open" and r - t["tread"] > lim["opening"]:
    lines.append("Open risers: treads at least %.0f mm thick (or a riser bar) keep the gap under %.0f mm" % ((r - lim["opening"]) * 1000, lim["opening"] * 1000))

if save:
    try:
        import alien_assets
        inputs = {"construction": construction, "use": use, "name": name, "riser": riser, "going": going, "tread": tread,
                  "nosing": nosing, "waist": waist, "stringer": stringer, "risers": risers}
        alien_assets.save_type("stair_type", t["name"], stair_type, "stair_types", inputs, geometry=swatch, colours=colours,
                               summary="riser %.0f, going %.0f mm, %s" % (r * 1000, g * 1000, t["use"]))
        notes.append("saved to the library as %s" % t["name"])
    except Exception as ex:
        notes.append("could not save to the library: %s" % ex)

info = "\n".join(lines + notes)
