# NODE_INPUTS: preset:str | "a common floor build-up, or custom (from layers)" default="concrete 200" options="concrete 200|post-tensioned 250|hollowcore 200 + topping|CLT 180 + screed|timber joist floor|waffle 300|steel deck composite 150|slab on ground 100|custom"
# NODE_INPUTS: name:str | "type name (blank: from the preset, finish and soffit)" default=""
# NODE_INPUTS: layers:list[str] | "layers top -> bottom, thickness in mm: 'screed 50; CLT 180*; plasterboard ceiling 13' (* = structure). Replaces the preset's layers when given" default=""
# NODE_INPUTS: finish:str | "floor finish laid on top" default="none" options="none|polished concrete|tiles|timber boards|carpet|vinyl|screed|raised floor"
# NODE_INPUTS: soffit:str | "underside: as built keeps the build-up's own; the others replace its ceiling" default="as built" options="as built|exposed|plasterboard|suspended ceiling|render|acoustic panels"
# NODE_INPUTS: plane:Plane | "where the swatch sits (default: world origin)", save:bool | "add this type to the Alien library" button
# NODE_OUTPUTS: slab_type:str | "slab_type record for slabs", names:list[str] | "layer names, top first", materials:list[str] | "layer materials", thicknesses:list[float] | "layer thicknesses (m)"
# NODE_OUTPUTS: total:float | "overall thickness (m)", swatch:list[Brep] | "a stepped sample, one solid per layer", colours:list[Color] | "colour per swatch solid", info:str
# CATEGORY: Building elements
# Slab type: a floor build-up (concrete, post-tensioned, hollowcore, CLT, timber joists, waffle, steel deck... or your own layers) with a finish and a soffit, for the slabs node.
#
# Layers run from the top of the floor down, thicknesses in mm ("200", "200mm", "0.2m" and "20cm" all work). A * marks
# the structure; without one the thickest structural layer is. Materials come from the layer names (concrete, screed,
# CLT, joists, steel deck, tiles, carpet, insulation, plasterboard, ceiling void...). The finish is laid on top; a
# soffit other than "as built" replaces the build-up's ceiling (exposed removes it). Info shows the result in the same
# text form, so it can be copied into `layers` and edited. The swatch draws membranes 4 mm thick so they show.
import alien_bim as bim
import alien_envelope as env

u = bim.units()
notes = []
pk = env.preset_key(env.SLAB_PRESETS, preset)
text = "; ".join(str(x) for x in (layers or []) if x is not None and str(x).strip())
parsed, problems = env.parse_layers(text) if text else ([], [])
notes += problems
if parsed:
    L = parsed
    source = "custom layers" if pk is None else "custom layers (replacing the %s preset)" % pk
    base_name = "Custom" if pk is None else "Custom " + pk
elif pk:
    L = env.parse_layers(env.SLAB_PRESETS[pk])[0]
    source, base_name = "preset", pk[:1].upper() + pk[1:]
else:
    L = env.parse_layers(env.SLAB_PRESETS["concrete 200"])[0]
    source, base_name = "default", "Concrete 200"
    notes.append(("custom needs layers" if str(preset or "").strip().lower() in ("", "custom") else "no preset '%s'; give layers" % preset)
                 + ", e.g. 'screed 50; CLT 180*; plasterboard ceiling 13': showing concrete 200")

fin = env.preset_key(env.FINISHES, finish) or "none"
if fin == "none" and str(finish or "").strip().lower() not in ("", "none"):
    notes.append("no finish '%s': none" % finish)
sof = env.preset_key(env.SOFFITS, soffit) or "as built"
if sof == "as built" and str(soffit or "").strip().lower() not in ("", "as built"):
    notes.append("no soffit '%s': as built" % soffit)

extra_top = env.parse_layers(env.FINISHES[fin])[0] if env.FINISHES[fin] else []
for l in extra_top:
    l["core"], l["function"] = False, "finish"
L = extra_top + L
if env.SOFFITS[sof] is not None:
    # drop the build-up's own ceiling (and its void) before hanging the new soffit
    while len(L) > 1 and not L[-1].get("core") and (L[-1]["function"] in ("ceiling", "air") or L[-1]["material"] in ("plasterboard", "render")):
        L.pop()
    under = env.parse_layers(env.SOFFITS[sof])[0] if env.SOFFITS[sof] else []
    for l in under:
        l["core"] = False
        l["function"] = "air" if l["material"] == "air" else "ceiling"
    L += under

label = base_name + ("" if fin == "none" else ", " + fin) + ("" if sof == "as built" else ", " + sof + " soffit")
t = env.make_type("slab_type", (name or "").strip() or label, L, pk or "custom", finish=fin, soffit=sof)
slab_type = bim.dump(t)
names = [l["name"] for l in L]
materials = [l["material"] for l in L]
thicknesses = [float(l["thickness"]) for l in L]
total = round(sum(thicknesses), 6)
swatch, colours = env.swatch(L, plane, u)

if save:
    try:
        import alien_assets
        alien_assets.save_type("slab_type", t["name"], slab_type, "slab_types",
                               {"preset": preset, "name": name, "layers": text, "finish": finish, "soffit": soffit},
                               geometry=swatch, colours=colours, summary="%.0f mm, %d layer%s" % (total * 1000, len(L), "" if len(L) == 1 else "s"))
        notes.append("saved to the library as %s" % t["name"])
    except Exception as ex:
        notes.append("could not save to the library: %s" % ex)

core = [l["name"] for l in L if l.get("core")]
info = "\n".join([
    "%s: %d layer%s, %.0f mm (%s)" % (t["name"], len(L), "" if len(L) == 1 else "s", total * 1000, source),
    "Top -> bottom: " + env.layers_text(L),
    "Structure: " + (", ".join(core) or "none") + "; finish: %s; soffit: %s" % (fin, sof),
] + notes)
