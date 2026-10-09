# NODE_INPUTS: breps:list[Brep] | "breps to colour", seed:int | "random seed" min=0 max=9999 default=1
# NODE_OUTPUTS: colors:list[Color] | "random colour per brep", areas:list[float] | "surface area per brep", ranked:list[Brep] | "breps sorted by area, largest first"
# Assigns a seeded random colour to each brep and reports its area, plus breps ranked by area.
import random
import Rhino.Geometry as rg
import System.Drawing as sd

# Unwired: use sample spheres so the node shows a result straight away
if not breps:
    breps = [rg.Sphere(rg.Point3d(i * 3.0, 0, 0), 1.0 + i * 0.5).ToBrep() for i in range(5)]

rng = random.Random(seed)

areas = []
for b in breps:
    amp = rg.AreaMassProperties.Compute(b)
    areas.append(amp.Area if amp else 0.0)

colors = [sd.Color.FromArgb(rng.randint(40, 255), rng.randint(40, 255), rng.randint(40, 255)) for _ in breps]

ranked = [b for _, b in sorted(zip(areas, breps), key=lambda p: -p[0])]
