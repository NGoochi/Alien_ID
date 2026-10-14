# NODE_INPUTS: walls:list[object] | "wall records from walls or doors (or another windows node); a sample wall when unwired"
# NODE_INPUTS: wall:str | "which wall: id or index, several with commas, or 'all' (blank: the first; points: the nearest)" default=""
# NODE_INPUTS: positions:list[str] | "window centres along the wall (m): '1.2, 4.5'; negative = back from the end, '50%' = halfway (blank: mid-wall)" default="25%, 75%"
# NODE_INPUTS: points:list[Point3d] | "or points: each window goes to the nearest wall"
# NODE_INPUTS: width:float | "overall width, frame included (m)" min=0.3 max=6 default=1.2, height:float | "overall height (m)" min=0.3 max=4 default=1.2
# NODE_INPUTS: sill:float | "sill height above the floor (m)" min=0 max=3 default=0.9
# NODE_INPUTS: family:str | "window type" default="awning" options="fixed|casement|awning|hopper|sliding|double hung"
# NODE_INPUTS: hand:str | "casement: hinge side seen from the side it opens into; sliding: the side it opens towards" default="left" options="left|right"
# NODE_INPUTS: swing:str | "the side sashes open into" default="out" options="in|out"
# NODE_INPUTS: frame:float | "frame width (m)" min=0.02 max=0.2 default=0.05
# NODE_INPUTS: design:list[Geometry] | "a generated sash design (e.g. object_3d parts), fitted into each sash (optional)"
# NODE_INPUTS: name:str | "window type name for the library (blank: from the family, size and hand)" default="", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: openings:list[str] | "window records", walls:list[str] | "wall records with every opening so far", solids:list[Brep] | "the walls' layer solids with every opening cut", colours:list[Color] | "colour per solid"
# NODE_OUTPUTS: parts:list[Geometry] | "frames, sashes and glass together", part_colours:list[Color] | "colour per part", frames:list[Brep], sashes:list[Geometry] | "sashes (or the fitted design)", glass:list[Brep]
# NODE_OUTPUTS: plan:list[Curve] | "walls cut at the plan height, open at the windows", symbols:list[Curve] | "plan symbols: frame and sashes (the opening dashed when above the cut)", reflected:list[Curve] | "seen from below (reflected ceiling plan): head, frame, sashes", elevation:list[Curve] | "on the exterior face: outline, sashes and opening lines (dashed V points to the hinges)", info:str
# CATEGORY: Building elements
# Windows: hosted windows (fixed, casement, awning, hopper, sliding, double hung) cut into walls, with frames, sashes, glass and drawing symbols.
#
# Each window is an opening record added to its wall, so walls -> doors -> windows chain and every step cuts all the
# openings so far (solids). Windows stay on their stretch of wall (moved off corners and ends, with a note), are cut
# down to fit under the wall's top, and never overlap another opening (skipped, with a note); a window may sit over a
# door. Width and height are the hole in the wall; the frame (centred in the wall, up to 110 mm deep) fills it.
# Elevation marks follow the usual convention: dashed lines meet at the hinged side (casement: the hand side, awning:
# top, hopper: bottom); sliding and double hung get arrows. A wired design is scaled into each sash, front outwards.
# Save keeps the window TYPE (family, size, sill, hand, swing, frame, design) in the Alien library as a "window", drawn
# from the first window placed at its full height.
import math
import time

import alien_bim as bim
import alien_types as at
import alien_walls as aw

t0 = time.time()
u = bim.units()
fam = str(family or "awning").lower()
if fam not in ("fixed", "casement", "awning", "hopper", "sliding", "double hung"):
    fam = "awning"
hand = "right" if str(hand or "").lower().startswith("r") else "left"
swing = "in" if str(swing or "").lower().startswith("i") else "out"
width = min(max(width or 1.2, 0.2), 12.0)
height = min(max(height or 1.2, 0.2), 10.0)
sill = min(max(sill if sill is not None else 0.9, 0.0), 20.0)
fw = min(max(frame or 0.05, 0.01), min(width, height) / 4.0)

recs, sample = aw.walls_in(walls)
new, notes = aw.place(recs, "window", "W", wall, positions, points, width, height, sill,
                      {"family": fam, "hand": hand, "swing": swing, "frame": fw})
geoms, gbox = aw.design_of(design)

frames, sashes, glass, symbols, reflected, elevation = [], [], [], [], [], []
sash_mats = []
GAP = 0.003 * u


def pane(fr, x0, x1, y, z0, z1):
    g = fr.box(x0, x1, y - 0.005 * u, y + 0.005 * u, z0, z1)
    if g is not None:
        glass.append(g)


def sash(fr, x0, x1, y0, y1, z0, z1):
    """One opening sash: the fitted design when there is one, else an aluminium ring with its glass."""
    if geoms:
        got = fr.fit(geoms, gbox, x0, x1, y0, y1, z0, z1, front=1, mirror=(hand == "right"))
        sashes.extend(got)
        sash_mats.extend(["timber"] * len(got))
        return
    sw = min(0.045 * u, (x1 - x0) / 4.0, (z1 - z0) / 4.0)
    ring = fr.ring(x0, x1, z0, z1, sw, y0, y1)
    if ring is not None:
        sashes.append(ring)
        sash_mats.append("aluminium")
    pane(fr, x0 + sw, x1 - sw, (y0 + y1) / 2.0, z0 + sw, z1 - sw)


first = None  # the first window's frame and parts: the library draws the type from it
for wi, op in new:
    k0 = (len(frames), len(sashes), len(glass))
    fr = aw.Frame(aw.wall_of(recs[wi]), op)
    w, h, f, T = fr.w, fr.h, fr.f, fr.T
    s = 1 if swing == "out" else -1
    hs = fr.hinge_sign(swing, hand)
    fd = min(T, 0.11 * u)                   # frame depth, centred in the wall
    iw, ih = w - 2 * f, h - 2 * f           # inside the frame
    xi, zi0, zi1 = iw / 2.0, f, h - f
    sd = min(0.04 * u, fd / 2.0)            # sash depth
    zc = fr.zcut
    ye = T / 2.0 + fr.sag + 0.005 * u
    wy = T / 2.0 + fr.sag                   # wall faces (sill lines)

    fb = fr.ring(-w / 2.0, w / 2.0, 0.0, h, f, -fd / 2.0, fd / 2.0)
    if fb is not None:
        frames.append(fb)
    elevation.append(fr.poly([(-w / 2.0, ye, 0), (w / 2.0, ye, 0), (w / 2.0, ye, h), (-w / 2.0, ye, h)], True))
    elevation.append(fr.poly([(-xi, ye, zi0), (xi, ye, zi0), (xi, ye, zi1), (-xi, ye, zi1)], True))
    reflected.append(fr.rect_plan(-w / 2.0, w / 2.0, -wy, wy, h))
    reflected.append(fr.rect_plan(-xi, xi, -fd / 2.0, fd / 2.0, h))

    def vee(apex, a, b):
        elevation.extend(fr.dashed((a[0], ye, a[1]), (apex[0], ye, apex[1])) + fr.dashed((b[0], ye, b[1]), (apex[0], ye, apex[1])))

    boxes = []  # sash plan boxes (x0, x1, y0, y1) for plan and reflected symbols
    if fam == "fixed":
        if geoms:
            sash(fr, -xi, xi, -sd / 2.0, sd / 2.0, zi0, zi1)
        else:
            pane(fr, -xi, xi, 0.0, zi0, zi1)
        boxes.append((-xi, xi, -0.005 * u, 0.005 * u))
    elif fam in ("casement", "awning", "hopper"):
        y0, y1 = sorted((s * fd / 2.0, s * (fd / 2.0 - sd)))
        sash(fr, -xi + GAP, xi - GAP, y0, y1, zi0 + GAP, zi1 - GAP)
        boxes.append((-xi, xi, y0, y1))
        if fam == "casement":
            vee((hs * xi, h / 2.0), (-hs * xi, zi0), (-hs * xi, zi1))
        elif fam == "awning":
            vee((0.0, zi1), (-xi, zi0), (xi, zi0))
        else:
            vee((0.0, zi0), (-xi, zi1), (xi, zi1))
    elif fam == "sliding":
        pw = iw / 2.0 + 0.025 * u
        track = min(0.022 * u, max(0.0, (fd - sd) / 2.0))
        for k, (xs, yc) in enumerate(((hs, -s * track), (-hs, s * track))):  # fixed sash on the opening side
            x0, x1 = sorted((xs * xi, xs * (xi - pw)))
            sash(fr, x0, x1, yc - sd / 2.0, yc + sd / 2.0, zi0 + GAP, zi1 - GAP)
            boxes.append((x0, x1, yc - sd / 2.0, yc + sd / 2.0))
            elevation.append(fr.poly([(x0, ye, zi0), (x1, ye, zi0), (x1, ye, zi1), (x0, ye, zi1)], True))
            if k == 1:
                xm = (x0 + x1) / 2.0
                elevation.extend(fr.arrow((xm - hs * pw * 0.25, ye, h / 2.0), (xm + hs * pw * 0.25, ye, h / 2.0)))
                if 0 < zc < h:
                    symbols.extend(fr.arrow((xm - hs * pw * 0.3, wy + 0.1 * u, zc), (xm + hs * pw * 0.3, wy + 0.1 * u, zc), plan=True))
    else:  # double hung: the top sash outside, the bottom one inside; both slide
        ph = ih / 2.0 + 0.025 * u
        track = min(0.022 * u, max(0.0, (fd - sd) / 2.0))
        for k, (z0, z1, yc) in enumerate(((zi0, zi0 + ph, -track), (zi1 - ph, zi1, track))):
            sash(fr, -xi + GAP, xi - GAP, yc - sd / 2.0, yc + sd / 2.0, z0, z1)
            boxes.append((-xi, xi, yc - sd / 2.0, yc + sd / 2.0))
            elevation.append(fr.poly([(-xi, ye, z0), (xi, ye, z0), (xi, ye, z1), (-xi, ye, z1)], True))
            zm, d = (z0 + z1) / 2.0, (z1 - z0) * 0.25
            elevation.extend(fr.arrow((0.0, ye, zm - d), (0.0, ye, zm + d)) if k == 0 else fr.arrow((0.0, ye, zm + d), (0.0, ye, zm - d)))

    for x0, x1, y0, y1 in boxes:
        reflected.append(fr.rect_plan(x0, x1, y0, y1, h))
    if zc <= 0:
        # above the plan cut: the opening dashed (overhead)
        for a, b in (((-w / 2.0, -wy), (w / 2.0, -wy)), ((w / 2.0, -wy), (w / 2.0, wy)), ((w / 2.0, wy), (-w / 2.0, wy)), ((-w / 2.0, wy), (-w / 2.0, -wy))):
            symbols.extend(fr.dashed((a[0], a[1], zc), (b[0], b[1], zc)))
    else:
        z = min(zc, h)
        for sgn in (-1, 1):  # jambs (cut) or the frame's ends (seen below)
            symbols.append(fr.rect_plan(min(sgn * w / 2.0, sgn * xi), max(sgn * w / 2.0, sgn * xi), -fd / 2.0, fd / 2.0, z))
        for yy in (-fd / 2.0, fd / 2.0):  # the frame's edges (plan keeps the wall faces below as the sill)
            symbols.append(fr.line((-xi, yy, z), (xi, yy, z)))
        for x0, x1, y0, y1 in boxes:
            symbols.append(fr.rect_plan(x0, x1, y0, y1, z))
    if first is None and abs(float(op["height"]) - height) < 1e-6:  # not one cut down to fit its wall
        first = (fr, frames[k0[0]:] + sashes[k0[1]:] + glass[k0[2]:],
                 [aw.colour("aluminium") for _ in frames[k0[0]:]] + [aw.colour(m) for m in sash_mats[k0[1]:]] + [aw.colour("glass", 90) for _ in glass[k0[2]:]])

solids, colours, _, plan, problems = aw.cut_all(recs)
notes += problems
openings = [bim.dump(op) for _, op in new]
walls = [bim.dump(r) for r in recs]
parts = frames + sashes + glass
part_colours = [aw.colour("aluminium") for _ in frames] + [aw.colour(m) for m in sash_mats] + [aw.colour("glass", 90) for _ in glass]

count = {}
for _, op in new:
    count[op["wall"]] = count.get(op["wall"], 0) + 1
total = sum(len(r["openings"]) for r in recs)
info = "\n".join([
    "%d %s window%s (%.2f x %.2f m, sill %.2f m%s)%s: %s" % (
        len(new), fam, "" if len(new) == 1 else "s", width, height, sill,
        (", %s hand" % hand if fam in ("casement", "sliding") else "") + (", opens %s" % swing if fam in ("casement", "awning", "hopper") else ""),
        " on a sample wall" if sample else "",
        ", ".join("%d on %s" % (n, k) for k, n in count.items()) or "none placed"),
    "At: " + (", ".join("%s %.2f m" % (op["id"], op["at"]) for _, op in new) or "-"),
    "%d opening%s cut through %d wall%s in %.0f ms%s" % (total, "" if total == 1 else "s", len(recs), "" if len(recs) == 1 else "s",
                                                       (time.time() - t0) * 1000, "; sashes from the wired design (%d parts)" % len(geoms) if geoms else ""),
] + notes)

if save:  # the button: keep this window type in the library
    rec = at.opening_type("window", name, fam, width, height, hand, swing, fw, geoms, sill)
    if first is None:
        info += "\nnot saved: no window was placed at its full height to draw the type from"
    else:
        at.save_opening("window", rec, first[0], first[1], first[2], {"family": fam, "width": width, "height": height,
                        "sill": sill, "hand": hand, "swing": swing, "frame": fw, "name": str(name or "").strip()}, "windows")
        info += "\nsaved to the library as %s" % rec["name"] + (" (with the wired design)" if geoms else "")
