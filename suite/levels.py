# NODE_INPUTS: count:int | "number of levels" min=1 max=100 default=4, height:float | "floor-to-floor height (m)" min=0.5 max=20 default=3.2
# NODE_INPUTS: heights:list[str] | "per-level heights (m), from the ground up, e.g. '4.5, 3, , 3.6' (a blank keeps height)"
# NODE_INPUTS: base:float | "elevation of the first level (m)" default=0, outline:Curve | "floor plate to copy to every level (optional)"
# NODE_OUTPUTS: planes:list[Plane] | "one per level", elevations:list[float] | "level z (m)", floor_heights:list[float] | "floor-to-floor of each level (m)"
# NODE_OUTPUTS: names:list[str], plates:list[Curve] | "floor plate at each level", roof:Plane | "top of the last level", total:float | "base to roof (m)"
# CATEGORY: Massing & objects
# Levels: a stack of building levels from z0, each with its own floor-to-floor height, with planes, elevations and floor plates.
import re

import Rhino
import Rhino.Geometry as rg

doc = Rhino.RhinoDoc.ActiveDoc
scale = Rhino.RhinoMath.UnitScale(Rhino.UnitSystem.Meters, doc.ModelUnitSystem) if doc else 1.0

count = max(1, int(count or 1))
height = height if height and height > 0 else 3.2

# per-level overrides: '4.5, 3, , 3.6' or one value per line; blanks and junk keep the default
custom = []
for item in heights or []:
    for tok in re.split(r"[,;\n]", str(item)):
        tok = tok.strip()
        try:
            custom.append(float(tok) if tok else None)
        except ValueError:
            print("not a height: %r (using %g m)" % (tok, height))
            custom.append(None)
floor_heights = [(custom[i] if i < len(custom) and custom[i] and custom[i] > 0 else height) for i in range(count)]

elevations, z = [], base or 0.0
for h in floor_heights:
    elevations.append(round(z, 4))
    z += h
total = round(z - (base or 0.0), 4)

names = ["Ground" if i == 0 else "Level %d" % i for i in range(count)]
planes = [rg.Plane(rg.Point3d(0, 0, e * scale), rg.Vector3d.ZAxis) for e in elevations]
roof = rg.Plane(rg.Point3d(0, 0, z * scale), rg.Vector3d.ZAxis)

if outline is None:
    # sample floor plate when nothing is wired: 20 x 12 m
    outline = rg.Rectangle3d(rg.Plane.WorldXY, 20 * scale, 12 * scale).ToNurbsCurve()
start = outline.PointAtStart.Z
plates = []
for e in elevations:
    c = outline.DuplicateCurve()
    c.Translate(rg.Vector3d(0, 0, e * scale - start))
    plates.append(c)
