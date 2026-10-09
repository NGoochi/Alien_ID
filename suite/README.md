# Preloaded suite

Nodes that ship with Alien (brief, Phase 1c). Each is one plain script file: drop it in the script repository and
attach it to an Alien node (or `add(script=...)` over MCP).

| Node | What it does |
|---|---|
| `vic_site_map.py` | Victorian address → lot boundary, neighbouring lots, named road centrelines, building footprints and massing, zone, overlays, bushfire prone area, site area and dimensions |
| `branch_picker.py` | Pass chosen branches of a data tree through: `0, 2, 4-6`, `-1`, `*`, `even`/`odd`, in the order given; the rest go out of `rest`; `branches` lists every branch with its size and contents |
| `levels.py` | Stack of levels from z0 (`base`): `count`, a default floor-to-floor `height`, per-level `heights` ("4.5, 3, , 3.6", a blank keeps the default); planes, elevations, names, floor plates (a wired outline, or a 20 x 12 m sample), roof plane |
| `scale_figure.py` | A person as a true-height silhouette for elevations and sections: `who` ("a woman pushing a pram"), `height`, `view` (side, front, back, three-quarter); outline curves, planar fill, solid hatch |
| `scale_tree.py` | A tree silhouette at true size: `species`, `height`, `view` elevation or plan (`spread` = canopy width); canopy gaps kept as holes |
| `alien_graphics.py` | Not a node: the shared helper the two graphics nodes import (prompting, image model, cache, tracing, scaling). Keep it in the same folder |

All nodes take sizes in metres and scale them to the model's units.

## Context graphics (scale_figure, scale_tree)

- **How it works:** an image model draws the subject as a black-on-white silhouette. The prompt is built from the inputs,
  with strict rules: one subject, solid black, white margin all round, no ground, shadow or text. `alien_graphics`
  then traces the image:
  - grey image, then marching squares with sub-pixel edges
  - drops islands and gaps smaller than the `detail` threshold
  - Douglas-Peucker simplification
  - smooth periodic curves, with a polyline fallback where a smooth fit would cross itself
  - scales the drawing so its height (or canopy width in plan) is true size, standing on the plane's origin
- **Checks:** a drawing that is blank, cut off at the image edge, or has a non-white background is redrawn once with a
  stricter prompt.
- **Model:** `imageModel` in settings.json, default `google/gemini-3.1-flash-lite-image`: about 4–9 s and US$0.034 per
  drawing. GPT-5 Image Mini draws crisper figures but took 34 s, too long while Grasshopper waits.
- **Cache:** every drawing is cached in `<repository>/.alien/cache/graphics`. Changing size, detail or the plane costs
  nothing (about 30–160 ms); `variant` asks for a new drawing of the same request.
- **Outputs:** `outline` (curves), `fill` (planar Breps, which preview in Grasshopper), `hatch` (Rhino solid hatches:
  these don't preview in Grasshopper, but bake as real hatches), `image` (the drawing's file).
- **Default placement:** figures and elevation trees stand in the XZ plane at the origin, as seen in the Front view; plan
  trees lie in XY.
- **Checked on 2026-10-09** (Rhino model in millimetres):

  | Drawing | Size | Holes kept |
  |---|---|---|
  | Walking adult | exactly 1,750 mm tall, feet on z0 | — |
  | Woman with pram | 1.68 m | pram wheels |
  | 8 m street tree, elevation | 8 m | 22 canopy gaps |
  | 7 m canopy, plan | 7 m | — |

  The hatch and outlines baked into Rhino.

## branch_picker and levels: checked on 2026-10-09

- **branch_picker:** fed with Series → Partition List, every pick form worked:
  - `2, 0` (keeps that order), `1-3`, `-1`, `*`, `even`
  - out of range or junk (`9, x`) is ignored and reported
  - `flatten` merges the picked branches
- **levels:**
  - `count` 6, `heights` "4.5, 3, , 3.6", `base` 1.2 → elevations 1.2 / 5.7 / 8.7 / 11.9 / 15.5 / 18.7, roof 21.9 m
  - a wired Circle became the plates
  - a junk height ("three") falls back to the default with a note
- **Known limit:** Alien hands a script a tree as a list of branches, so the picker works by branch index; the original
  path labels such as {0;2} are not kept.

## vic_site_map

- **Inputs:** `address` (or `lat,lon`), `radius` (context in metres, 10–300), `assumed_height` (for buildings with no
  known height; 0 leaves them flat), `refresh`.
- **Outputs:**
  - Geometry: `site`, `lots`, `roads` with `road_names`, `footprints`, `buildings`.
  - Data: `zone`, `overlays`, `area`, and `info` (summary, sources and caveats).
- **Geometry:** metres scaled to the model's units, centred on the site, Y = true north, flat at z = 0.
- **Data:**
  - Vicmap (State of Victoria, CC BY 4.0):
    - Address (geocoding)
    - Property and Parcel (lots)
    - Transport (roads)
    - Planning (zones, overlays, bushfire prone area)
  - Building footprints come from the Overture Maps buildings tiles, read straight from the PMTiles archive by HTTP range
    (Overture merges OpenStreetMap with Microsoft and Google ML footprints). If Overture fails, it falls back to
    OpenStreetMap through Overpass.
  - OpenStreetMap Nominatim is the geocoding fallback. A match on the street or suburb only is refused, rather than
    mapping a stranger's lot.
- **Heights:**
  - In order of preference: Overture `height`, then storeys × 3 m + 1 m roof, then the tallest building part.
  - Most Victorian heights come from Microsoft ML Buildings, which ran about 2.3 m low against measured heights in inner
    Melbourne (Street Scheme benchmark). `info` says so whenever they are used.
  - Buildings with no height get `assumed_height` (default 7 m, two storeys).
- **Speed:** about 2–10 s the first time for an address, while Grasshopper waits; after that it is instant from the
  cache in the temp folder (30 days). The Overture archive slices are cached per release, so a second address in the
  same area mostly skips the download.
- **Checked on 2026-10-09:**

  | Address | Result |
  |---|---|
  | 201 Napier St Fitzroy | 4,710 m², matches ArchiSkills; NRZ1; HO180 and DCPO1 |
  | 1/114 Blyth St Brunswick | Strata lot |
  | CBD coordinates | CCZ2, 9 overlays |
  | Monbulk | Bushfire prone, BMO |

  "12 Smith St" asks for the suburb. A made-up address gives a clear error.
- Not a Planning Property Report: confirm with VicPlan before relying on it.
