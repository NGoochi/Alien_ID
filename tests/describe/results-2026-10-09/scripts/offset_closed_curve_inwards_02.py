# NODE_INPUTS: curve:Curve | "Closed planar curve to offset" 
# NODE_INPUTS: distance:float | "Offset distance per step" min=0.001 max=1000 default=1
# NODE_INPUTS: count:int | "Number of inward offsets" min=1 max=50 default=5
# NODE_OUTPUTS: result:list[Curve], log:str
# Creates successive inward offsets of a closed planar curve.

import Rhino
import Rhino.Geometry as rg

tol = Rhino.RhinoDoc.ActiveDoc.ModelAbsoluteTolerance

# Sample geometry makes the node show a result before a curve is connected.
source = curve
if source is None:
    source = rg.Circle(rg.Plane.WorldXY, 10.0).ToNurbsCurve()

result = []
log = ""

if not source.IsClosed:
    log = "Input curve must be closed."
else:
    ok, plane = source.TryGetPlane(tol)
    if not ok:
        log = "Input curve must be planar."
    else:
        current = source
        for i in range(max(1, int(count))):
            candidates = current.Offset(plane, float(distance), tol,
                                        rg.CurveOffsetCornerStyle.Sharp)
            if not candidates:
                log = "Offset stopped at step {}.".format(i + 1)
                break

            # Of the two offset directions, the inward one has the smaller area.
            measured = []
            for candidate in candidates:
                amp = rg.AreaMassProperties.Compute(candidate)
                if amp:
                    measured.append((amp.Area, candidate))
            if not measured:
                log = "Could not measure offset area at step {}.".format(i + 1)
                break

            current = min(measured, key=lambda item: item[0])[1]
            result.append(current)

        if not log:
            log = "Created {} inward offset(s).".format(len(result))
