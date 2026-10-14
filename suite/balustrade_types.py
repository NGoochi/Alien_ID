# NODE_INPUTS: style:str | "infill" default="glass and posts" options="frameless glass|glass and posts|vertical bars|horizontal rails|wires|solid sheet|timber"
# NODE_INPUTS: name:str | "type name (blank: from the style and height)" default=""
# NODE_INPUTS: height:float | "height to the top on floors and landings (m); NCC at least 1.0" min=0.5 max=2 default=1.0
# NODE_INPUTS: stair_height:float | "height above the nosing line along stair flights (m); NCC at least 0.865; 0 = as height" min=0 max=2 default=0
# NODE_INPUTS: posts:float | "post spacing (m); frameless glass: the pane width" min=0.3 max=3 default=1.2, post:float | "post size (mm), square; 0 = by style (50, timber 90)" min=0 max=200 default=0
# NODE_INPUTS: gap:float | "clear gap between bars, rails or wires (mm); NCC at most 125" min=10 max=300 default=100, bottom:float | "gap under the infill (mm); NCC at most 125" min=0 max=300 default=50
# NODE_INPUTS: handrail:str | "handrail on top" default="by style" options="by style|round 50|round 42|square 50|rect 65x20|timber 70x42|none"
# NODE_INPUTS: infill:float | "glass or sheet thickness, bar or rail size (mm); 0 = by style" min=0 max=60 default=0
# NODE_INPUTS: plane:Plane | "where the swatch sits (default: world origin)", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: balustrade_type:str | "balustrade_type record for balustrades", swatch:list[Brep] | "a sample run: 1.2 m on a floor, then up a stair pitch", colours:list[Color] | "colour per swatch solid"
# NODE_OUTPUTS: elevation:list[Curve] | "the swatch's front outlines", info:str
# CATEGORY: Building elements
# Balustrade type: frameless glass, glass and posts, vertical bars, horizontal rails, wires, solid sheet or timber, with heights, post spacing, gaps and handrail, checked against the NCC barrier rules.
#
# Heights are to the top of the balustrade (the top of its handrail): `height` on floors and landings, `stair_height`
# above the nosing line on flights. Bars are spaced so no clear gap is wider than `gap`; rails and wires are spread
# up the height the same way; glass and sheet stop 10 mm off the posts. Styles set the post size, infill size,
# materials and handrail unless given. First-pass NCC checks (not certification): at least 1 m on floors and landings,
# 865 mm on flights, no opening a 125 mm sphere passes (infill gaps and the gap underneath), horizontal rails and wires
# climbable where the floor is more than 4 m above the surface below, a handrail along flights, and a note to check the
# wire tables for wire balustrades.
import alien_bim as bim
import alien_stairs as ss

import Rhino.Geometry as rg

u = bim.units()
bt, notes = ss.balustrade_type(style, name, height, stair_height, posts, (post or 0) / 1000.0, (gap if gap is not None else 100) / 1000.0,
                               (bottom if bottom is not None else 50) / 1000.0, handrail, (infill or 0) / 1000.0)
balustrade_type = bim.dump(bt)

# Swatch: 1.2 m flat, then 1.8 m up a 32 degree stair pitch, along the plane's X.
base = plane if isinstance(plane, rg.Plane) and plane.IsValid else rg.Plane.WorldXY
pts = [base.PointAt(0, 0), base.PointAt(1.2 * u, 0), base.PointAt(3.0 * u, 0, 1.8 * 0.625 * u)]
bv = ss.resample(ss.vertices_of(pts, bim.tol()), bt["spacing"] * u, False, u)
res = ss.build_balustrade(bv, bt, u, end_posts=True)
swatch, colours, elevation = res["solids"], res["colours"], res["elevation"]

checks = ss.barrier_checks(bt, bt["height"], bt["stair_height"], True, True, max(ss.infill_gap(bt), res["max_gap"]), bt["bottom"])
hr = bt.get("handrail")
lines = [
    "%s: %s, %.0f mm on floors, %.0f mm above the nosing line on stairs" % (bt["name"], bt["style"], bt["height"] * 1000, bt["stair_height"] * 1000),
    "Posts %s; infill %s %.0f mm%s; %.0f mm under; handrail %s" % (
        ("%.0f mm at %.2f m" % (bt["post"] * 1000, bt["spacing"])) if bt.get("post") else "none (panes %.2f m on spigots)" % bt["spacing"],
        bt["infill"], bt["size"] * 1000, (", gaps %.0f mm" % (bt["gap"] * 1000)) if bt["infill"] in ("bars", "rails", "wires") else "",
        bt["bottom"] * 1000, hr["name"] if hr else "none"),
    "NCC barrier checks (first-pass, not certification) on the swatch:",
] + ["  " + ss.rule_text(c) for c in checks]

if save:
    try:
        import alien_assets
        inputs = {"style": style, "name": name, "height": height, "stair_height": stair_height, "posts": posts, "post": post,
                  "gap": gap, "bottom": bottom, "handrail": handrail, "infill": infill}
        alien_assets.save_type("balustrade_type", bt["name"], balustrade_type, "balustrade_types", inputs, geometry=swatch,
                               colours=colours, summary="%s, %.0f mm" % (bt["style"], bt["height"] * 1000))
        notes.append("saved to the library as %s" % bt["name"])
    except Exception as ex:
        notes.append("could not save to the library: %s" % ex)

info = "\n".join(lines + notes)
