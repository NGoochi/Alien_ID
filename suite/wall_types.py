# NODE_INPUTS: preset:str | "a common Australian build-up, or custom (from layers)" default="brick veneer" options="brick veneer|double brick|timber stud|steel stud partition|concrete 200|precast 150|blockwork 190|CLT|rendered AAC|custom"
# NODE_INPUTS: name:str | "type name (blank: the preset's)" default=""
# NODE_INPUTS: layers:list[str] | "layers exterior -> interior, thickness in mm: 'brick 110; cavity 40; timber stud 90*; plasterboard 10' (* = core). Replaces the preset's layers when given" default=""
# NODE_INPUTS: plane:Plane | "where the swatch sits (default: world origin)"
# NODE_INPUTS: save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: wall_type:str | "wall_type record for walls", names:list[str] | "layer names, exterior first", materials:list[str] | "layer materials", thicknesses:list[float] | "layer thicknesses (m)"
# NODE_OUTPUTS: total:float | "overall thickness (m)", swatch:list[Brep] | "a stepped section sample, one solid per layer", colours:list[Color] | "colour per swatch solid", info:str
# CATEGORY: Building elements
# Wall type: a layered wall build-up (brick veneer, double brick, stud, concrete, CLT, AAC... or your own layers) for the walls node.
#
# Layers are listed from the exterior face to the interior face, thicknesses in mm ("110", "110mm", "0.11m" and "11cm"
# all work). A * marks the core (the structure: "core centre" location lines sit on its middle); without one the
# thickest structural layer is the core. Materials come from the layer names (brick, cavity/air, insulation, stud,
# steel, plasterboard, render, concrete, block, CLT, AAC, cladding...). Info shows the layers in the same text form, so
# a preset can be copied into `layers` and edited. Save keeps the type in the Alien library (the swatch is its picture).
import alien_bim as bim
import alien_walls as aw

import Rhino.Geometry as rg

u = bim.units()
notes = []
preset_key = next((k for k in aw.PRESETS if k.lower() == str(preset or "").strip().lower()), None)
text = "; ".join(str(x) for x in (layers or []) if x is not None and str(x).strip())

parsed, problems = aw.parse_layers(text) if text else ([], [])
notes += problems
if parsed:
    source = "custom layers" if preset_key is None else "custom layers (replacing the %s preset)" % preset_key
    t = aw.make_type((name or "").strip() or ("Custom" if preset_key is None else "Custom " + preset_key), parsed, preset_key or "custom")
elif preset_key:
    source = "preset"
    t = aw.preset_type(preset_key)
    if (name or "").strip():
        t["name"] = name.strip()
else:
    source = "default"
    t = aw.default_type()
    if (name or "").strip():
        t["name"] = name.strip()
    notes.append(("custom needs layers" if str(preset or "").strip().lower() in ("", "custom") else "no preset '%s'; give layers" % preset)
                 + ", e.g. 'brick 110; cavity 40; timber stud 90*; plasterboard 10': showing a generic 200 mm wall")

L = t["layers"]
wall_type = bim.dump(t)
names = [l["name"] for l in L]
materials = [l["material"] for l in L]
thicknesses = [float(l["thickness"]) for l in L]
total = round(sum(thicknesses), 6)

# Swatch: a short wall piece with each layer stepped back from the interior, exterior face towards +Y (as a wall
# drawn along +X would have it), so every layer shows in plan and in a 3D view.
base = plane if isinstance(plane, rg.Plane) and plane.IsValid else rg.Plane.WorldXY
step, height = 0.15, 0.6
length = 0.3 + step * len(L)
swatch, colours = [], []
y = total
for i, l in enumerate(L):
    th = float(l["thickness"])
    b = bim.box(base, 0, (length - step * (len(L) - 1 - i)) * u, (y - th) * u, y * u, 0, height * u)
    y -= th
    if b is not None:
        swatch.append(b)
        colours.append(aw.colour(l["material"]))

core = [l["name"] for l in L if aw.is_core(l)]
info = "\n".join([
    "%s: %d layer%s, %.0f mm (%s)" % (t["name"], len(L), "" if len(L) == 1 else "s", total * 1000, source),
    "Exterior -> interior: " + aw.layers_text(L),
    "Core: " + (", ".join(core) or "none"),
] + notes)

if save:  # the button: keep this type in the library, the swatch moved home to the origin
    import math
    import alien_assets
    # turned half a turn, so the thumbnail's iso camera (from -Y, +X) sees the exterior face and the flush end, where
    # every layer shows in section
    home = rg.Transform.Rotation(math.pi, rg.Vector3d.ZAxis, rg.Point3d.Origin) * rg.Transform.PlaneToPlane(base, rg.Plane.WorldXY)
    kept = []
    for sw in swatch:
        sw = sw.DuplicateBrep()
        sw.Transform(home)
        kept.append(sw)
    alien_assets.save_type("wall_type", t["name"], wall_type, "wall_types",
                           {"preset": str(preset or ""), "name": str(name or ""), "layers": text}, geometry=kept,
                           colours=colours, names=names if len(names) == len(kept) else None, summary="%.0f mm, %d layer%s" % (total * 1000, len(L), "" if len(L) == 1 else "s"))
    info += "\nsaved to the library as %s" % t["name"]
