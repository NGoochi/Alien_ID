# NODE_INPUTS: breps:list[Brep] | "Breps to colour; uses a sample sphere when empty"
# NODE_INPUTS: seed:int | "Random seed" min=0 max=100000 default=1
# NODE_OUTPUTS: geometry, colors, areas
# Assigns deterministic random colours to Breps, with brightness scaled by area.
import random
import Rhino.Geometry as rg
from System.Drawing import Color

def color_from_hsv(h, s, v):
    i = int(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1.0 - s), v * (1.0 - f * s), v * (1.0 - (1.0 - f) * s)
    i %= 6
    r, g, b = ((v, t, p) if i == 0 else
               (q, v, p) if i == 1 else
               (p, v, t) if i == 2 else
               (p, q, v) if i == 3 else
               (t, p, v) if i == 4 else (v, p, q))
    return Color.FromArgb(int(r * 255), int(g * 255), int(b * 255))

items = list(breps) if breps else []
if not items:
    # Sample geometry keeps the node preview-ready when no Breps are supplied.
    items = [rg.Sphere(rg.Point3d.Origin, 2.0).ToBrep()]

geometry = items
areas = []
for item in items:
    amp = rg.AreaMassProperties.Compute(item)
    areas.append(amp.Area if amp else 0.0)

low = min(areas) if areas else 0.0
high = max(areas) if areas else 0.0
rng = random.Random(seed)
colors = []
for area in areas:
    t = (area - low) / (high - low) if high > low else 0.5
    colors.append(color_from_hsv(rng.random(), 0.65 + 0.3 * rng.random(), 0.55 + 0.45 * t))
