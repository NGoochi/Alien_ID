# NODE_INPUTS: plane:Plane | "layout plane", count:int | "number of panels" min=1 max=500 default=6, numbers:list[int] | "explicit labels (overrides count)", panel_w:float min=1 default=120, panel_h:float min=1 default=80, gap:float min=0 default=16, cols:int min=1 max=50 default=3, thickness:float min=0 default=3
# NODE_OUTPUTS: panels, outlines, labels, report:str | "summary"
# Lays out numbered fabrication panels on a grid: extruded plates with 7-segment number pockets.

import Rhino.Geometry as rg

# sample layout plane when nothing is wired in
base = plane if isinstance(plane, rg.Plane) else rg.Plane.WorldXY

nums = [int(v) for v in numbers] if numbers else list(range(1, int(count) + 1))

SEGS = {0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
        5: "afgcd", 6: "afgecd", 7: "abc", 8: "abcdefg", 9: "abcdfg"}


def rect(p, x0, x1, y0, y1):
    return rg.Rectangle3d(p, rg.Interval(x0, x1), rg.Interval(y0, y1)).ToNurbsCurve()


def digit_curves(p, d, x, y, w, h, t):
    """segment rectangles for one digit, cell origin (x, y) at lower-left"""
    parts = {
        "a": (x + t, x + w - t, y + h - t, y + h),
        "g": (x + t, x + w - t, y + (h - t) / 2.0, y + (h + t) / 2.0),
        "d": (x + t, x + w - t, y, y + t),
        "f": (x, x + t, y + (h + t) / 2.0, y + h - t),
        "e": (x, x + t, y + t, y + (h - t) / 2.0),
        "b": (x + w - t, x + w, y + (h + t) / 2.0, y + h - t),
        "c": (x + w - t, x + w, y + t, y + (h - t) / 2.0),
    }
    return [rect(p, *parts[s]) for s in SEGS.get(d, "")]


panels, outlines, labels = [], [], []

for i, n in enumerate(nums):
    px = (i % cols) * (panel_w + gap)
    py = -(i // cols) * (panel_h + gap)
    p = rg.Plane(base.Origin + base.XAxis * px + base.YAxis * py, base.XAxis, base.YAxis)

    outline = rect(p, 0.0, panel_w, 0.0, panel_h)
    outlines.append(outline)

    if thickness > 0:
        ext = rg.Extrusion.Create(outline, thickness, True)
        if ext:
            panels.append(ext.ToBrep())

    s = "".join(c for c in str(abs(int(n))) if c.isdigit())
    dh = panel_h * 0.5
    dw = dh * 0.55
    dgap = dh * 0.18
    total = len(s) * dw + (len(s) - 1) * dgap
    x0 = (panel_w - total) / 2.0
    y0 = (panel_h - dh) / 2.0
    t = dh * 0.14
    for k, ch in enumerate(s):
        labels.extend(digit_curves(p, int(ch), x0 + k * (dw + dgap), y0, dw, dh, t))

report = "%d panels, %d label curves" % (len(outlines), len(labels))
