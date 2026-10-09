# NODE_INPUTS: breps:list[Brep] | "breps to colour"
# NODE_INPUTS: seed:int | "random seed" min=0 max=9999 default=1
# NODE_INPUTS: jitter:float | "hue jitter" min=0 max=0.5 default=0.15
# NODE_OUTPUTS: breps, colors:list[Color], info:str
# Gives each brep a random colour whose hue is ordered by surface area.
import random
import System.Drawing as sd
import Rhino.Geometry as rg

if not breps:
    # sample geometry: three boxes of different sizes
    breps = [
        rg.Brep.CreateFromBox(rg.Box(rg.Plane.WorldXY, rg.Interval(0, 1), rg.Interval(0, 1), rg.Interval(0, 1))),
        rg.Brep.CreateFromBox(rg.Box(rg.Plane.WorldXY, rg.Interval(0, 2), rg.Interval(0, 1), rg.Interval(0, 1))),
        rg.Brep.CreateFromBox(rg.Box(rg.Plane.WorldXY, rg.Interval(0, 1), rg.Interval(0, 3), rg.Interval(0, 1))),
    ]

breps = [b for b in breps if b is not None]
seed = 1 if seed is None else int(seed)
jitter = 0.15 if jitter is None else float(jitter)
rng = random.Random(seed)


def hsv_to_rgb(h, s, v):
    h = h % 1.0
    i = int(h * 6.0)
    f = h * 6.0 - i
    p = v * (1.0 - s)
    q = v * (1.0 - s * f)
    t = v * (1.0 - s * (1.0 - f))
    i %= 6
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return int(r * 255), int(g * 255), int(b * 255)


areas = [b.GetArea() for b in breps]
n = len(breps)
order = sorted(range(n), key=lambda i: areas[i])
hues = [0.0] * n
for rank, idx in enumerate(order):
    base = 0.5 if n == 1 else rank / float(n - 1)
    hues[idx] = (base + rng.uniform(-jitter, jitter)) % 1.0

colors = []
lines = []
for i, b in enumerate(breps):
    r, g, bl = hsv_to_rgb(hues[i], 0.65, 0.95)
    colors.append(sd.Color.FromArgb(255, r, g, bl))
    lines.append("brep %d  area %.3f  rgb(%d,%d,%d)" % (i, areas[i], r, g, bl))

info = "\n".join(lines)
