# NODE_INPUTS: curve:Curve | "closed curve to offset"
# NODE_INPUTS: distance:float | "step per offset" min=0.001 default=1.0
# NODE_INPUTS: count:int | "number of offsets" min=1 max=50 default=5
# NODE_OUTPUTS: curves:list[Curve], areas:list[float]
# Offset a closed curve inwards N times (picks the smaller-area side each step)

import Rhino
import Rhino.Geometry as rg

try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except Exception:
    tol = 0.001

# sample geometry when nothing is wired
if curve is None:
    curve = rg.Circle(rg.Plane.WorldXY, 10.0).ToNurbsCurve()

def area(c):
    amp = rg.AreaMassProperties.Compute(c)
    return amp.Area if amp is not None else 0.0

curves = []
areas = []
current = curve
cur_area = area(current)

for _ in range(int(count)):
    cands = []
    for d in (distance, -distance):
        arr = current.Offset(rg.Plane.WorldXY, d, tol, rg.CurveOffsetCornerStyle.Sharp)
        if arr is None or len(arr) == 0 or arr[0] is None:
            continue
        a = area(arr[0])
        if a > 0:
            cands.append((a, arr[0]))
    if not cands:
        break
    cands.sort(key=lambda t: t[0])
    next_area, next_c = cands[0]
    if next_area >= cur_area:   # no inward progress
        break
    curves.append(next_c)
    areas.append(next_area)
    current = next_c
    cur_area = next_area
