# NODE_INPUTS: geometry:Geometry | "Geometry to measure"
# NODE_OUTPUTS: dimensions:str | "Bounding-box dimensions (X, Y, Z)"
# Reports the axis-aligned bounding-box dimensions of the input geometry as text.
import Rhino.Geometry as rg

# Sample geometry makes the node display a result when nothing is connected.
if geometry is None or (isinstance(geometry, (list, tuple)) and len(geometry) == 0):
    geometry = rg.Box(
        rg.Plane.WorldXY,
        rg.Interval(0.0, 10.0),
        rg.Interval(0.0, 6.0),
        rg.Interval(0.0, 4.0)
    )

items = geometry if isinstance(geometry, (list, tuple)) else [geometry]
boxes = []

for item in items:
    if item is None:
        continue
    if isinstance(item, rg.BoundingBox):
        box = item
    elif hasattr(item, "GetBoundingBox"):
        box = item.GetBoundingBox(True)
    else:
        continue
    if box.IsValid:
        boxes.append(box)

if boxes:
    min_x = min(box.Min.X for box in boxes)
    min_y = min(box.Min.Y for box in boxes)
    min_z = min(box.Min.Z for box in boxes)
    max_x = max(box.Max.X for box in boxes)
    max_y = max(box.Max.Y for box in boxes)
    max_z = max(box.Max.Z for box in boxes)
    dimensions = f"X: {max_x - min_x:g}, Y: {max_y - min_y:g}, Z: {max_z - min_z:g}"
else:
    dimensions = "No valid geometry"
