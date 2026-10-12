# Preloaded suite

Nodes that ship with Alien (brief, Phase 1c). Each is one plain script file: drop it in the script repository and
attach it to an Alien node (or `add(script=...)` over MCP). The plugin copies them into `<repository>/suite` and keeps
unedited copies up to date (`.alien/suite.json` records what was installed; edited or deleted files are left alone).

| Node | Category | What it does |
|---|---|---|
| `vic_site_map.py` | Site & context | Victorian address → lot, neighbouring lots, roads, terrain and contours, 3D streets (carriageway, kerbs, footpaths, nature strips), trees, building massing on the ground, zone, overlays, bushfire prone area, site area |
| `site_views.py` | Views & drawings | Plan, site plan, X and Y centreline sections and four elevations around the site, as Rhino named views (sections clipped) and an optional A3–A1 layout sheet at standard scales |
| `object_3d.py` | Massing & objects | Describe a thing ("a dining chair"), optionally with a reference image, and a model writes RhinoCommon code that builds it from solids at real size, with part names and materials |
| `material_texture.py` | Materials | A finish ("board-formed concrete") becomes a seamless texture: a Custom Preview material and, on request, a Rhino PBR material mapped at real-world size |
| `branch_picker.py` | Data & lists | Pass chosen branches of a data tree through: `0, 2, 4-6`, `-1`, `*`, `even`/`odd`, in the order given; the rest go out of `rest`; `branches` lists every branch with its size and contents |
| `levels.py` | Massing & objects | Stack of levels from z0 (`base`): `count`, a default floor-to-floor `height`, per-level `heights` ("4.5, 3, , 3.6", a blank keeps the default); planes, elevations, names, floor plates (a wired outline, or a 20 x 12 m sample), roof plane |
| `scale_figure.py` | Entourage | A person as a true-height silhouette for elevations and sections: `who` ("a woman pushing a pram"), `height`, `view` (side, front, back, three-quarter); outline curves, planar fill, solid hatch |
| `scale_tree.py` | Entourage | A tree silhouette at true size: `species`, `height`, `view` elevation or plan (`spread` = canopy width); canopy gaps kept as holes |
| `alien_graphics.py` | – | Helper for the two graphics nodes (prompting, image model, cache, tracing, scaling) |
| `alien_ai.py` | – | Helper: Alien's settings, OpenRouter chat (with images) and image generation, caches in `.alien/cache` |
| `alien_assets.py` | – | Helper: saves what nodes generate to the asset library, with thumbnails |
| `alien_vicdata.py` | – | Helper for `vic_site_map`: terrain, trees and street cross-sections from open data |

Helpers are not nodes; keep them in the same folder as the nodes that import them. All nodes take sizes in metres and
scale them to the model's units. The line after the header, `# CATEGORY: ...`, places a node in the panel's library.

## Asset library (what the nodes generate)

`object_3d`, `material_texture`, `scale_figure` and `scale_tree` save what they make to
`<repository>/.alien/assets/<kind>/<id>/`: `asset.json` (name, prompt, the inputs that made it, size, materials), a
`thumb.png`, and the files (`geometry.3dm` in metres, `texture.png`, `drawing.jpg`, `build.py`, the reference image).
The id comes from what made it, so regenerating the same thing updates it. The panel's **Generated** tab lists them:

- **Bake block:** an object or drawing becomes a Rhino block (defined once, scaled from metres), inserted at the origin
  on the "Alien assets" layer.
- **Add material:** a texture becomes a PBR render material at its real tile size, applied to the selected objects.
- **Place node:** places the node that made it with the same inputs (free: the result is cached).

## object_3d

- **Only Generate spends credits.** Changing the description, style, image, detail or variant waits for the
  Generate button ("Press Generate to build …"; nothing is sent until then). A design built before comes back free from
  the cache, with or without the button. The default request ("a dining chair") ships with its design, so a new node
  shows a chair at no cost.
- **Width, depth and height resize the built design; they never call the model.** After a build, each slider shows
  that design's size and spans half to twice it (the node reports this with `NODE_CONTROLS`). Each axis scales on its
  own (a wider sofa keeps its height); 0 or unset means as built. Sizes set for one design reset when the design
  changes. The library keeps each design once, at its built size.
- **How it works:** the model (setting `model3d`, default `anthropic/claude-sonnet-5.5`) writes
  `def build(rg, math): return [(name, material, brep), ...]`: metres, Z up, standing on z = 0, front facing -Y, from
  robust primitives (boxes, cylinders, lofts, pipes; no booleans or fillets). Alien runs it with only `rg` and `math`
  (no imports, files or network), checks the parts and the overall size (3 cm–8 m), and sends any error back: up to 3
  attempts. The code is cached per request, so re-solving is instant.
- **Inputs:** `thing`, `style`, `image` (a file path or URL: the model copies its shape and proportions), `detail`
  (simple or detailed), `variant`, the `generate` button, `width` / `depth` / `height`, `plane`.
- **Outputs:** `parts` (Breps), `names`, `materials`, `colours` (a preview colour per material: wire `parts` and
  `colours` into Custom Preview), `size`, `info`, `code`.
- **Checked on 2026-10-12** (one attempt each): dining chair 18 parts, 0.44 × 0.42 × 0.90 m, US$0.017; dining table
  for six 15 parts, 1.80 × 0.90 × 0.74 m, US$0.011; toilet with cistern 14 parts, US$0.015; lounge armchair from a
  generated product photo, 17 parts in walnut and leather with the photo's raked back and splayed legs, US$0.019.
- **Fixed on 2026-10-12:** sizes used to be part of the prompt, so every slider release called the model again
  (US$0.017–0.021 each) and saved a new library object. Rechecked after the fix: resizing, re-pressing Generate on a
  built design and switching between built designs made no calls and no assets; a new bar stool and a wall basin each
  cost one call (US$0.013, US$0.020) and one asset.

## material_texture

- **How it works:** the image model paints a flat, evenly lit, tileable swatch of the finish at about `size` metres
  across. If its edges don't already match (seam check: edge jumps more than 1.35 × the texture's own detail), it is
  rolled half a tile and cross-faded over the seam cross. Already-tiling patterns (boards, bricks) are left as drawn,
  because blending them only ghosts.
- **Outputs:** `material` (wire into Custom Preview's M), `preview` (3 × 3 tiles at real size, to see the repeat),
  `texture` (the PNG), `name`, `info`. `add` creates or replaces a Rhino PBR material (base colour + bump, WCS box
  mapping, one tile per `size` metres). `add` is a button, so re-solving doesn't add it again.
- **Checked on 2026-10-12:** board-formed concrete, 1.2 m tile; the document material has both textures in WCS box
  mapping at the right repeat. US$0.034 per texture.

## site_views

- **Inputs:** `site` (curves, e.g. vic_site_map's `site`), `context` (m beyond the site), `geometry` (to find ground and
  top), `align` (true north, or the site's long axis), `sheet` (none, A3, A2, A1), `run`.
- **What `run` makes:** named views "Alien · Plan", "Alien · Site plan", "Alien · Section X/Y" (through the site
  centre, clipped, looking north and west) and "Alien · Elevation north/south/east/west" (clipped 0.5 m outside the
  site so the site's own buildings show). `run` is a button: each click replaces what the last one made.
  With a sheet, a layout page with all eight as details: plans on top, sections
  and elevations in rows, at one standard scale (the site plan gets its own), in Pen display mode, labelled. Running
  again replaces everything it made (tagged objects, views and pages).
- **Outputs (without running):** `views` (names), `planes` (camera planes), `frames` (view rectangles), `sections` (cut
  lines), `info`.
- **Checked on 2026-10-12:** Fitzroy site on A1: plan 1:500, site plan 1:1000, sections and elevations 1:500; each
  named view restores with its clipping plane.

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
  known height; 0 leaves them flat), `refresh` (a button: fetch again once), and (v2) `terrain`, `streets`, `trees` toggles and `contour_step`.
- **Outputs:**
  - Geometry: `site`, `lots`, `roads` with `road_names`, `footprints`, `buildings`.
  - v2: `ground` (terrain mesh), `contours` (at whole multiples of `contour_step`, m AHD), `datum` (the AHD height of
    z = 0), `carriageway`, `footpath`, `nature_strip`, `kerb` (meshes), `canopy` and `trunk` (tree meshes).
  - Data: `zone`, `overlays`, `area`, and `info` (summary, sources and caveats).
- **Geometry:** metres scaled to the model's units, centred on the site, Y = true north. z = 0 is the ground at the site
  centre. The curves stay flat at z = 0 for modelling; the ground, buildings (on the lowest ground under each
  footprint), streets and trees follow the terrain.
- **Terrain (v2):** Geoscience Australia's 5 m LiDAR DEM (bare earth, AHD) where it covers the site, gaps filled from the
  statewide Vicmap Elevation 10 m DEM (read as LERC tiles with a pure-Python decoder); surface models (Terrarium,
  Open-Meteo) only if both fail, because they read rooftops. A 2 m grid (at most 201 × 201 points).
- **Streets (v2):** each Vicmap Transport centreline gets a cross-section from its road class, fitted to the road
  reserve measured from Vicmap Property road casement polygons (Napier St 20.1 m, Collins St 30.2 m): carriageway,
  median, nature strip, footpath. Laneways (reserve ≤ 7.5 m) are wall-to-wall with no kerb. Verges are paved in central
  and commercial zones (CCZ, ACZ, C1Z, MUZ, DZ...). The bands and reserves are overlaid into planar regions, each region
  classified, meshed and draped on the ground; verges sit a 150 mm kerb above the road, with kerb faces between.
- **Trees (v2):** Vicmap Vegetation Tree Urban points (metro Melbourne, Ballarat, Shepparton, Wangaratta and Sale) with
  LiDAR height and canopy half-widths: an ellipsoid crown and a trunk each. Trees under 1.5 m (planted after the LiDAR)
  get a height from their canopy; points inside a building footprint that read roof height are left out. Species come
  from the City of Melbourne inventory inside that council.
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
- **Speed:** about 2–10 s the first time for an address (up to ~35 s in the CBD), while Grasshopper waits; after that
  about 1 s from the cache in the temp folder (30 days; each v2 layer is cached on its own). The Overture archive slices
  are cached per release, so a second address in the same area mostly skips the download. A 240 m radius with streets
  takes about 6 s even from the cache (street regions and kerbs).
- **Checked on 2026-10-09:**

  | Address | Result |
  |---|---|
  | 201 Napier St Fitzroy | 4,710 m², matches ArchiSkills; NRZ1; HO180 and DCPO1 |
  | 1/114 Blyth St Brunswick | Strata lot |
  | CBD coordinates | CCZ2, 9 overlays |
  | Monbulk | Bushfire prone, BMO |

  "12 Smith St" asks for the suburb. A made-up address gives a clear error.
- **v2 checked on 2026-10-12:**

  | Site | Terrain | Streets | Trees |
  |---|---|---|---|
  | 201 Napier St Fitzroy (r 60) | GA 5 m + Vicmap fill, 28.7–39.1 m AHD, datum 32.07 | 65 centrelines, 58 measured | 300+ |
  | Monbulk (r 120) | Vicmap 10 m, ~35 m fall, 79 contours | draped on the slope | dense canopy |
  | 120 Collins St (r 60) | GA 5 m, 16.8–28.2 m AHD | 29 of 33 measured, paved verges (CCZ1), a grassed median | 51, species for 27 |
- Not a Planning Property Report: confirm with VicPlan before relying on it.
