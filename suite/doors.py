# NODE_INPUTS: walls:list[object] | "wall records from walls (or from windows / another doors node); a sample wall when unwired"
# NODE_INPUTS: wall:str | "which wall: id or index, several with commas, or 'all' (blank: the first; points: the nearest)" default=""
# NODE_INPUTS: positions:list[str] | "door centres along the wall (m): '1.2, 4.5'; negative = back from the end, '50%' = halfway (blank: mid-wall)" default=""
# NODE_INPUTS: points:list[Point3d] | "or points: each door goes to the nearest wall"
# NODE_INPUTS: width:float | "overall width, frame included (m)" min=0.5 max=6 default=0.9, height:float | "overall height from the floor (m)" min=1.5 max=4 default=2.1
# NODE_INPUTS: family:str | "door type" default="single swing" options="single swing|double swing|sliding|pocket|bifold"
# NODE_INPUTS: hand:str | "hinge side seen from the side it opens into (sliding, pocket, bifold: the side it opens towards)" default="left" options="left|right"
# NODE_INPUTS: swing:str | "the side it opens into (in = the wall's interior)" default="in" options="in|out"
# NODE_INPUTS: frame:float | "frame (jamb) width (m)" min=0.02 max=0.2 default=0.04
# NODE_INPUTS: design:list[Geometry] | "a generated leaf design (e.g. object_3d parts), fitted into each leaf (optional)"
# NODE_INPUTS: name:str | "door type name for the library (blank: from the family, size and hand)" default="", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: openings:list[str] | "door records", walls:list[str] | "wall records with every opening so far (wire into windows)", solids:list[Brep] | "the walls' layer solids with every opening cut", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: parts:list[Geometry] | "frames, leaves and glass together", part_colours:list[Color] | "colour per part", frames:list[Brep], leaves:list[Geometry] | "leaves (or the fitted design)", glass:list[Brep]
# NODE_OUTPUTS: plan:list[Curve] | "walls cut at the plan height, open at the doors", symbols:list[Curve] | "plan symbols: frame, leaf drawn open, swing arc", reflected:list[Curve] | "seen from below (reflected ceiling plan): head, frame, closed leaves", elevation:list[Curve] | "on the exterior face: outline and opening lines (dashed V points to the hinges)", info:str
# CATEGORY: Building elements
# Doors: hosted doors (swing, double, sliding, pocket, bifold) cut into walls, with frames, leaves, plan swings and elevation marks.
#
# Each door is an opening record added to its wall, so walls -> doors -> windows chain and every step cuts all the
# openings so far (solids). Doors stay on their stretch of wall (clear of corners and ends: moved, with a note) and
# never overlap another opening (skipped, with a note). Width and height are the hole in the wall; the frame fills it.
# Hand is seen from the side the door opens into: left = hinges on your left. A wired design (e.g. object_3d parts)
# is scaled into each leaf box: its width along the wall, its height up, its depth to the leaf, its front to the swing
# side, mirrored for right-hand doors. Save keeps the door TYPE (family, size, hand, swing, frame, design) in the
# Alien library as a "door", drawn from the first door placed at its full height.
import math
import time

import alien_bim as bim
import alien_types as at
import alien_walls as aw

t0 = time.time()
u = bim.units()
fam = str(family or "single swing").lower()
if fam not in ("single swing", "double swing", "sliding", "pocket", "bifold"):
    fam = "single swing"
hand = "right" if str(hand or "").lower().startswith("r") else "left"
swing = "out" if str(swing or "").lower().startswith("o") else "in"
width = min(max(width or 0.9, 0.3), 12.0)
height = min(max(height or 2.1, 0.5), 10.0)
fw = min(max(frame or 0.04, 0.01), width / 4.0)

recs, sample = aw.walls_in(walls)
new, notes = aw.place(recs, "door", "D", wall, positions, points, width, height, 0.0,
                      {"family": fam, "hand": hand, "swing": swing, "frame": fw})
geoms, gbox = aw.design_of(design)

frames, leaves, glass, symbols, reflected, elevation = [], [], [], [], [], []
part_mats = {"frames": [], "leaves": []}
LT = 0.04 * u  # leaf thickness
GAP = 0.003 * u


def panel(fr, x0, x1, y0, y1, z0, z1, stile):
    """A glazed panel: an aluminium frame ring and its glass."""
    ring = fr.ring(x0, x1, z0, z1, stile, y0, y1)
    if ring is not None:
        leaves.append(ring)
        part_mats["leaves"].append("aluminium")
    ym = (y0 + y1) / 2.0
    g = fr.box(x0 + stile, x1 - stile, ym - 0.005 * u, ym + 0.005 * u, z0 + stile, z1 - stile)
    if g is not None:
        glass.append(g)


def leaf(fr, x0, x1, y0, y1, z0, z1, front, glazed=False, mirror=False):
    """One leaf: the fitted design when there is one (mirrored for right hands), else a solid leaf (or a glazed panel)."""
    if geoms:
        got = fr.fit(geoms, gbox, x0, x1, y0, y1, z0, z1, front=front, mirror=(hand == "right") != mirror)
        leaves.extend(got)
        part_mats["leaves"].extend(["timber"] * len(got))
    elif glazed:
        panel(fr, x0, x1, y0, y1, z0, z1, min(0.05 * u, (x1 - x0) / 4.0))
    else:
        b = fr.box(x0, x1, y0, y1, z0, z1)
        if b is not None:
            leaves.append(b)
            part_mats["leaves"].append("clt")  # a paler timber than the frame


def plan_rect(fr, p, q, t, z):
    """A thin rectangle from p to q (local x, y) with thickness t to its left, at local z."""
    dx, dy = q[0] - p[0], q[1] - p[1]
    n = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / n * t, dx / n * t
    return fr.poly([(p[0], p[1], z), (q[0], q[1], z), (q[0] + nx, q[1] + ny, z), (p[0] + nx, p[1] + ny, z)], True)


first = None  # the first door's frame and parts: the library draws the type from it
for wi, op in new:
    k0 = (len(frames), len(leaves), len(glass))
    fr = aw.Frame(aw.wall_of(recs[wi]), op)
    w, h, f, T = fr.w, fr.h, fr.f, fr.T
    s = 1 if swing == "out" else -1          # +y is the exterior
    hs = fr.hinge_sign(swing, hand)          # +1: hinges / opening side at +x
    lw, lh = w - 2 * f, h - f                # the leaf zone inside the frame
    glazed = fam in ("sliding", "bifold")
    depth = T if not glazed else min(T, 0.15 * u)
    fy0, fy1 = -depth / 2.0, depth / 2.0
    yf = s * depth / 2.0                     # the frame face on the swing side
    zc = min(max(fr.zcut, 0.0), h)           # plan cut, local

    fb = fr.ring(-w / 2.0, w / 2.0, 0.0, h, f, fy0, fy1, bottom=False)
    if fb is not None:
        frames.append(fb)
        part_mats["frames"].append("aluminium" if glazed else "timber")
    # frame in plan: the two jambs
    for sgn in (-1, 1):
        symbols.append(fr.rect_plan(min(sgn * w / 2.0, sgn * (w / 2.0 - f)), max(sgn * w / 2.0, sgn * (w / 2.0 - f)), fy0, fy1, zc))

    ye = T / 2.0 + fr.sag + 0.005 * u        # just proud of the exterior face, for elevation marks
    elevation.append(fr.poly([(-w / 2.0, ye, 0), (w / 2.0, ye, 0), (w / 2.0, ye, h), (-w / 2.0, ye, h)], True))
    reflected.append(fr.rect_plan(-w / 2.0, w / 2.0, -T / 2.0 - fr.sag, T / 2.0 + fr.sag, h))
    reflected.append(fr.rect_plan(-w / 2.0 + f, w / 2.0 - f, fy0, fy1, h))

    def vee(xa, xb, z0, z1):
        """Dashed opening lines on one leaf: from the corners at xb to the middle of the hinge edge at xa."""
        elevation.extend(fr.dashed((xb, ye, z0), (xa, ye, (z0 + z1) / 2.0)) + fr.dashed((xb, ye, z1), (xa, ye, (z0 + z1) / 2.0)))

    ly0, ly1 = sorted((yf, yf - s * LT))
    if fam in ("single swing", "double swing"):
        n = 1 if fam == "single swing" else 2
        pw = lw / n
        for k in range(n):
            hx = hs * lw / 2.0 if n == 1 else (-lw / 2.0 if k == 0 else lw / 2.0)   # hinge jamb
            sx = -1 if hx > 0 else 1                                               # towards the strike
            x0, x1 = sorted((hx + sx * GAP, hx + sx * (pw - GAP / 2.0)))
            leaf(fr, x0, x1, ly0, ly1, 0.01 * u, lh - GAP, s, mirror=(k == 1))  # a pair mirrors its second leaf
            reflected.append(fr.rect_plan(x0, x1, ly0, ly1, h))
            # plan: the leaf open at 90 degrees into the swing side, and its arc
            symbols.append(fr.rect_plan(min(hx, hx + sx * LT), max(hx, hx + sx * LT), min(yf, yf + s * pw), max(yf, yf + s * pw), zc))
            c45 = math.cos(math.radians(45))
            symbols.append(fr.arc((hx, yf + s * pw, zc), (hx + sx * pw * c45, yf + s * pw * c45, zc), (hx + sx * pw, yf, zc)))
            elevation.append(fr.poly([(x0, ye, 0), (x1, ye, 0), (x1, ye, lh), (x0, ye, lh)], True))
            vee(hx, hx + sx * pw, 0.0, lh)
    elif fam == "sliding":
        pw = lw / 2.0 + 0.025 * u
        track = min(0.025 * u, max(0.0, (depth - LT) / 2.0))
        for k, (xs, yc) in enumerate(((hs, -s * track), (-hs, s * track))):   # fixed panel at the opening side, sliding one in front
            x0, x1 = sorted((xs * lw / 2.0, xs * (lw / 2.0 - pw)))
            y0, y1 = yc - LT / 2.0, yc + LT / 2.0
            leaf(fr, x0, x1, y0, y1, 0.01 * u, lh - GAP, s, glazed=True)
            symbols.append(fr.rect_plan(x0, x1, y0, y1, zc))
            reflected.append(fr.rect_plan(x0, x1, y0, y1, h))
            elevation.append(fr.poly([(x0, ye, 0), (x1, ye, 0), (x1, ye, lh), (x0, ye, lh)], True))
            if k == 1:
                xm = (x0 + x1) / 2.0
                symbols.extend(fr.arrow((xm - hs * pw * 0.3, yc + s * 0.12 * u, zc), (xm + hs * pw * 0.3, yc + s * 0.12 * u, zc), plan=True))
                elevation.extend(fr.arrow((xm - hs * pw * 0.25, ye, lh / 2.0), (xm + hs * pw * 0.25, ye, lh / 2.0)))
    elif fam == "pocket":
        leaf(fr, -lw / 2.0 + GAP, lw / 2.0 - GAP, -LT / 2.0, LT / 2.0, 0.01 * u, lh - GAP, s)
        reflected.append(fr.rect_plan(-lw / 2.0, lw / 2.0, -LT / 2.0, LT / 2.0, h))
        # plan: the leaf half into its pocket, the pocket dashed inside the wall beside the opening
        sh = hs * lw / 2.0
        symbols.append(fr.rect_plan(-lw / 2.0 + sh, lw / 2.0 + sh, -LT / 2.0, LT / 2.0, zc))
        for yy in (-LT / 2.0 - 0.01 * u, LT / 2.0 + 0.01 * u):
            symbols.extend(fr.dashed((hs * w / 2.0, yy, zc), (hs * (w / 2.0 + lw), yy, zc)))
        symbols.extend(fr.dashed((hs * (w / 2.0 + lw), -LT / 2.0 - 0.01 * u, zc), (hs * (w / 2.0 + lw), LT / 2.0 + 0.01 * u, zc)))
        symbols.extend(fr.arrow((-hs * lw * 0.2, s * 0.15 * u, zc), (hs * lw * 0.2, s * 0.15 * u, zc), plan=True))
        elevation.append(fr.poly([(-lw / 2.0, ye, 0), (lw / 2.0, ye, 0), (lw / 2.0, ye, lh), (-lw / 2.0, ye, lh)], True))
        elevation.extend(fr.arrow((-hs * lw * 0.25, ye, lh / 2.0), (hs * lw * 0.25, ye, lh / 2.0)))
        # the pocket itself is not cut into the wall
    else:  # bifold: panels fold towards the hand side, out into the swing side
        n = max(2, int(math.ceil(lw / (0.9 * u) - 1e-9)))
        pw = lw / n
        a = math.radians(60)
        p = (hs * lw / 2.0, yf)
        for k in range(n):
            x0, x1 = sorted((hs * lw / 2.0 - hs * k * pw, hs * lw / 2.0 - hs * (k + 1) * pw))
            leaf(fr, x0 + GAP / 2.0, x1 - GAP / 2.0, ly0, ly1, 0.01 * u, lh - GAP, s, glazed=True)
            reflected.append(fr.rect_plan(x0, x1, ly0, ly1, h))
            q = (p[0] - hs * pw * math.cos(a), yf + (s * pw * math.sin(a) if k % 2 == 0 else 0.0))
            symbols.append(plan_rect(fr, p, q, 0.02 * u, zc))
            p = q
            elevation.append(fr.poly([(x0, ye, 0), (x1, ye, 0), (x1, ye, lh), (x0, ye, lh)], True))
            # panels hinge in pairs: the V points to the hinge between them
            if k % 2 == 0:
                vee(x0 if hs < 0 else x1, x1 if hs < 0 else x0, 0.0, lh)
            else:
                vee(x1 if hs < 0 else x0, x0 if hs < 0 else x1, 0.0, lh)
    if first is None and abs(float(op["height"]) - height) < 1e-6:  # not one cut down to fit its wall
        first = (fr, frames[k0[0]:] + leaves[k0[1]:] + glass[k0[2]:],
                 [aw.colour(m) for m in part_mats["frames"][k0[0]:] + part_mats["leaves"][k0[1]:]] + [aw.colour("glass", 90) for _ in glass[k0[2]:]])

solids, colours, _, plan, problems = aw.cut_all(recs)
notes += problems
openings = [bim.dump(op) for _, op in new]
walls = [bim.dump(r) for r in recs]
parts = frames + leaves + glass
part_colours = [aw.colour(m) for m in part_mats["frames"] + part_mats["leaves"]] + [aw.colour("glass", 90) for _ in glass]

count = {}
for _, op in new:
    count[op["wall"]] = count.get(op["wall"], 0) + 1
total = sum(len(r["openings"]) for r in recs)
info = "\n".join([
    "%d %s door%s (%.2f x %.2f m, %s hand, opens %s)%s: %s" % (
        len(new), fam, "" if len(new) == 1 else "s", width, height, hand, swing, " on a sample wall" if sample else "",
        ", ".join("%d on %s" % (n, k) for k, n in count.items()) or "none placed"),
    "At: " + (", ".join("%s %.2f m" % (op["id"], op["at"]) for _, op in new) or "-"),
    "%d opening%s cut through %d wall%s in %.0f ms%s" % (total, "" if total == 1 else "s", len(recs), "" if len(recs) == 1 else "s",
                                                       (time.time() - t0) * 1000, "; leaves from the wired design (%d parts)" % len(geoms) if geoms else ""),
] + notes)

if save:  # the button: keep this door type in the library
    rec = at.opening_type("door", name, fam, width, height, hand, swing, fw, geoms)
    if first is None:
        info += "\nnot saved: no door was placed at its full height to draw the type from"
    else:
        at.save_opening("door", rec, first[0], first[1], first[2], {"family": fam, "width": width, "height": height,
                        "hand": hand, "swing": swing, "frame": fw, "name": str(name or "").strip()}, "doors")
        info += "\nsaved to the library as %s" % rec["name"] + (" (with the wired design)" if geoms else "")
