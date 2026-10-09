# NODE_INPUTS: base:Curve, n:int | "number of inward offsets" min=1 max=50 default=5, d:float | "offset distance per step" min=0.01 max=100 default=1.0
# NODE_OUTPUTS: rings, log:str | "status text"
# Offsets a closed curve inward N times, each step from the previous ring.

import Rhino
import Rhino.Geometry as rg

if base is None:
    # Sample geometry so the node shows a result when nothing is wired
    base = rg.Circle(rg.Plane.WorldXY, 10.0).ToNurbsCurve()

tol = 0.01
try:
    tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance
except Exception:
    pass

plane = rg.Plane.WorldXY
ok, p = base.TryGetPlane()
if ok:
    plane = p

rings = []
log_lines = []
if not base.IsClosed:
    log_lines.append("warning: input curve is not closed")

current = base
for i in range(n):
    # Try both sides and keep the shorter result: inward is the shorter side for a closed curve
    best = None
    for sign in (1.0, -1.0):
        res = current.Offset(plane, sign * d, tol, rg.CurveOffsetCornerStyle.Sharp)
        if res and len(res) > 0:
            length = sum(c.GetLength() for c in res)
            if best is None or length < best[0]:
                best = (length, res)
    if best is None:
        log_lines.append("step %d: offset failed, stopped" % (i + 1))
        break
    # Use the longest piece if the offset split into several curves
    pieces = list(best[1])
    current = max(pieces, key=lambda c: c.GetLength())
    rings.append(current)
    log_lines.append("step %d: ok, length %.3f" % (i + 1, current.GetLength()))

log = "\n".join(log_lines) if log_lines else "done"
