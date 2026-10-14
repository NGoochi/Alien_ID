# NODE_INPUTS: preset:str | "a common roof build-up, or custom (from layers)" default="metal deck" options="metal deck|metal deck low pitch|concrete tile|terracotta tile|slate|membrane on concrete|green roof|custom"
# NODE_INPUTS: name:str | "type name (blank: the preset's)" default=""
# NODE_INPUTS: layers:list[str] | "layers top -> bottom, thickness in mm: 'metal roofing 20; battens 40; insulation 55; rafters 140*' (* = structure). Replaces the preset's layers when given" default=""
# NODE_INPUTS: colour:str | "colour of the roofing (the top layer)" default="as built" options="as built|charcoal|dark grey|mid grey|light grey|off-white|red|terracotta|green|blue"
# NODE_INPUTS: plane:Plane | "where the swatch sits (default: world origin)", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: roof_type:str | "roof_type record for roofs", names:list[str] | "layer names, top first", materials:list[str] | "layer materials", thicknesses:list[float] | "layer thicknesses (m)"
# NODE_OUTPUTS: total:float | "overall thickness, square to the roof (m)", swatch:list[Brep] | "a stepped sample, one solid per layer", colours:list[Color] | "colour per swatch solid", info:str
# CATEGORY: Building elements
# Roof type: a roof build-up (metal deck, concrete or terracotta tiles, slate, membrane on concrete, green roof... or your own layers) with the pitches it allows, for the roofs node.
#
# Layers run from the outside down, thicknesses in mm measured square to the roof. A * marks the structure (rafters,
# purlins, slab); without one the thickest structural layer is. Each type carries the pitch range its covering allows
# (metal deck 5 degrees up, low-pitch deck 1, tiles 15, slate 20, membranes and green roofs flat): roofs warns outside
# it. For custom layers the range comes from the top layer. Info shows the layers in the text form `layers` takes.
import alien_bim as bim
import alien_envelope as env

u = bim.units()
notes = []
pk = env.preset_key(env.ROOF_PRESETS, preset)
text = "; ".join(str(x) for x in (layers or []) if x is not None and str(x).strip())
parsed, problems = env.parse_layers(text) if text else ([], [])
notes += problems
if parsed:
    L = parsed
    source = "custom layers" if pk is None else "custom layers (replacing the %s preset)" % pk
    t_name = "Custom" if pk is None else "Custom " + pk
    top = L[0]["material"]
    lo, hi = {"metal deck": (2, 90), "roof tile": (15, 75), "terracotta": (15, 75), "slate": (20, 90),
              "membrane": (0, 5), "gravel": (0, 5), "vegetation": (0, 30), "soil": (0, 30)}.get(top, (0, 90))
elif pk:
    text_p, lo, hi = env.ROOF_PRESETS[pk]
    L = env.parse_layers(text_p)[0]
    source, t_name = "preset", pk[:1].upper() + pk[1:]
else:
    text_p, lo, hi = env.ROOF_PRESETS["metal deck"]
    L = env.parse_layers(text_p)[0]
    source, t_name = "default", "Metal deck"
    notes.append(("custom needs layers" if str(preset or "").strip().lower() in ("", "custom") else "no preset '%s'; give layers" % preset)
                 + ", e.g. 'metal roofing 20; battens 40; insulation 55; rafters 140*': showing metal deck")

ck = env.preset_key(env.ROOF_COLOURS, colour) or "as built"
if ck == "as built" and str(colour or "").strip().lower() not in ("", "as built"):
    notes.append("no colour '%s': as built" % colour)
rgb = env.ROOF_COLOURS[ck]

t = env.make_type("roof_type", (name or "").strip() or t_name + ("" if rgb is None else ", " + ck), L, pk or "custom",
                  min_pitch=lo, max_pitch=hi, colour=list(rgb) if rgb else None)
roof_type = bim.dump(t)
names = [l["name"] for l in L]
materials = [l["material"] for l in L]
thicknesses = [float(l["thickness"]) for l in L]
total = round(sum(thicknesses), 6)
swatch, colours = env.swatch(L, plane, u, tilt=15)
if rgb and colours:
    import System.Drawing
    colours[0] = System.Drawing.Color.FromArgb(255, *rgb)

if save:
    try:
        import alien_assets
        alien_assets.save_type("roof_type", t["name"], roof_type, "roof_types",
                               {"preset": preset, "name": name, "layers": text, "colour": colour},
                               geometry=swatch, colours=colours, summary="%.0f mm, %g-%g degrees" % (total * 1000, lo, hi))
        notes.append("saved to the library as %s" % t["name"])
    except Exception as ex:
        notes.append("could not save to the library: %s" % ex)

core = [l["name"] for l in L if l.get("core")]
info = "\n".join([
    "%s: %d layer%s, %.0f mm (%s)" % (t["name"], len(L), "" if len(L) == 1 else "s", total * 1000, source),
    "Top -> bottom: " + env.layers_text(L),
    "Structure: %s; pitch %s" % (", ".join(core) or "none", "0-%g°" % hi if hi < 90 and lo == 0 else "%g-%g°" % (lo, hi) if hi < 90 else "%g° and up" % lo),
] + notes)
