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
| `bim_site.py` | Building elements | The building suite's base: a site record (name, project base point, grid rotation, true north, AHD datum, drawing scale, boundary) that every building node reads through `site`. Takes vic_site_map's `site` and `datum`; origin at the lot's corner, centre or the world origin; grid aligned to the lot's long axis or longest edge; north point and base-point marker |
| `levels.py` | Building elements | Named levels (basements, storeys, roof) from a ground level above the site origin: `count`, `height`, per-storey `heights` ("4.5, 3, , 3.6"); naming Level 1 / L1 / L01 / Level 01 / LVL 1 / custom ("Floor #"); level records with RLs from the site datum, level lines, datum-symbol heads and plan labels in paper mm at the drawing scale. The old outputs (planes, elevations, names, plates, roof, total) stay |
| `grids.py` | Building elements | Revit/ArchiCAD-style grids on the site's origin and rotation: bays "7.2, 7.2, 8.4" or "3x7.2", numbers along X and letters along Y (I and O skipped), extensions, bubble heads at either or both ends, custom angled or curved grids, intersections named "A/1", bay and overall dimensions; the footprint on the outermost grids (`outline`, and `edges` as wall lines) |
| `wall_types.py` | Building elements | A wall build-up: Australian presets (brick veneer, double brick, timber stud, steel stud partition, concrete 200, precast 150, blockwork 190, CLT, rendered AAC) or layers typed exterior → interior in mm (`brick 110; cavity 40; timber stud 90*; plasterboard 10`, `*` = core); layer names, materials, thicknesses, a swatch |
| `walls.py` | Building elements | Layered walls along lines, polylines, arcs, splines or closed outlines, from a base level to the next (or a named level, or a height; base `all` repeats them on every storey); location line centre / exterior / interior / core centre; mitred corners, L and T joins between walls; wall records, one solid per layer, plan linework at the cut height, centrelines |
| `doors.py` | Building elements | Doors hosted in walls (single or double swing, sliding, pocket, bifold) at distances along a wall (`1.2, -1, 50%`) or at points; kept clear of corners, ends and each other; every opening so far cut through the walls (`walls` chains into windows); frames, leaves, glass; plan symbols (leaf drawn open with its swing), a reflected view, elevation marks; a generated design (object_3d parts) can be fitted into each leaf |
| `windows.py` | Building elements | Windows hosted the same way (fixed, casement, awning, hopper, sliding, double hung) at a sill height, chained after doors; frames, sashes, glass; plan, reflected and elevation symbols (dashed V to the hinge side: side, top or bottom hung) |
| `curtain_wall.py` | Building elements | A curtain wall along a straight, faceted, curved or closed path between levels: mullions at a panel width (equal divide, fixed from start, centred), transoms at levels and typed heights ("1.2, 2.4", "@1.5", "-0.9"), box / I / T / round / fin / flat bar profiles in mm, optional warren, pratt, howe or vierendeel trusses; plan cut, curtain grid, cell surfaces, records |
| `beams_columns.py` | Building elements | A structural frame on the grids: columns at intersections (all, perimeter, or a skip list) one per storey, beams along the grids at each level, in Australian steel (UB, UC, PFC, SHS, RHS, CHS, EA), glulam, LVL or concrete; beams trimmed to the faces they meet; member records, tonnage |
| `structure_edit.py` | Building elements | Edits a frame: resize (a name or up/down a size), move, rotate, remove or extend members picked by id wildcards (`B-L02-*`) or filters (`level=L02 role=beam`), several edits at once; ids kept, framing beams re-trimmed |
| `connectors.py` | Building elements | Connections found from the members: base plates and anchors, cap plates, column splices, end plates, fin plates, cleats, bolts (M16/M20/M24), optional welds; joint records and a schedule ("BP A/1 GF: PL 505x508x25, 4 M24 anchors") |
| `slab_types.py` | Building elements | A floor build-up: presets (concrete 200, post-tensioned 250, hollowcore 200 + topping, CLT 180 + screed, timber joist floor, waffle 300, steel deck composite 150, slab on ground) or layers top → bottom in mm (`screed 50; CLT 180*; plasterboard ceiling 13`); finish on top, soffit below; swatch; Save to the library |
| `slabs.py` | Building elements | Layered slabs at chosen levels ("floors" = all but the top, "all", "suspended", indices, names) from closed outlines: edge offset, setdown, a type per level; openings cut at their own level (stair voids) or through every slab; notches; slab records, solids, edges for balustrades, plan with void crosses, net areas |
| `roof_types.py` | Building elements | A roof build-up: metal deck, low-pitch deck, concrete or terracotta tile, slate, membrane on concrete, green roof, or custom layers; the pitch range the covering allows and a roofing colour; Save to the library |
| `roofs.py` | Building elements | Hip, gable, dutch gable, skillion or flat roofs over any simple outline (L, T, U, concave via a straight skeleton) on a level: eaves and verge overhangs, ridge direction, layered solids, fascias, barges, gutters, wall infill up to the roof at gable ends and skillion sides; ridges, hips, valleys, eaves; a roof plan with fall arrows; faces with pitch, aspect and area |
| `stair_types.py` | Building elements | How a stair is built (concrete in-situ, precast, steel stringers, timber), riser and going targets, treads, nosings, waist, stringers, open or closed risers, public or private NCC limits checked; swatch and section; Save to the library |
| `stairs.py` | Building elements | Straight, L, U or spiral stairs between two levels from a base line: risers fitted to the height, landings past 18 risers, first-pass NCC checks per rule; solids, a plan (UP arrow, cut line, dashed above), balustrade edges on the nosing line, and the upper-floor void for `slabs.openings` |
| `balustrade_types.py` | Building elements | Frameless glass, glass and posts, vertical bars, horizontal rails, wires, solid sheet or timber; heights on floors and above the nosing line, posts, gaps, handrail; NCC barrier checks; Save to the library |
| `balustrades.py` | Building elements | Balustrades along slab edges, stair edges or any curve, following their slope or set on a level: posts at corners and spacing, infill, handrails, plan and elevation linework, first-pass NCC checks (heights, 125 mm gaps, climbable rails) |
| `section_profile.py` | Building elements | A structural section from the Australian catalogue (UB, UC, PFC, SHS, RHS, CHS, EA, UA, glulam, LVL, sawn, concrete) or a custom shape: a profile record (area, Ix, Iy, Zx, Zy, r) that beams_columns and structure_edit take as a section, a dimensioned section drawing and hatch, a 1 m sample; Save to the library |
| `object_2d.py` | Create | Describe a flat thing (a car or furniture in plan, a person in elevation, a tree canopy) or give a picture; press Generate and a model draws lines, fills and hatches at real size; width/depth resize the cached design free; saved to the library as a 2D object |
| `symbol.py` | Create | Annotation symbols (north arrow, section or level marker, tag, scale bar, logo) drawn by a model at paper size with text slots ("A \| A201"), placed at points and scaled by the drawing scale (north arrows follow the site's north); `block` makes a Rhino block; saved to the library |
| `display_mode.py` | Create | Describe a look ("white clay with soft shadows", "blueprint") or give a reference image; the model returns a checked JSON look; `install` creates or updates the Rhino display mode "Alien · <name>" from a built-in base (never changing built-in modes); `apply` shows it; saved to the library |
| `views.py` | Views & drawings | Named views of any geometry (or a layer): plans and RCPs per level, elevations, X/Y or wired sections (lettered, with plan marks), isometrics and a perspective, framed with a margin, in a display mode; `make` replaces the views and clipping planes made with the same prefix |
| `layout.py` | Views & drawings | Named views on A4–A0 sheets as locked details at the largest standard scale that fits (or one or a forced scale), auto grid, one per sheet or rows; view titles with scales and a title block; `make` replaces the sheets made with the same prefix |
| `exploded_axo.py` | Views & drawings | Pulls geometry apart by branch, part or height band (stacked, along a vector or radially), with dashed explosion lines and labels; `view` saves a parallel isometric named view framing the result |
| `branch_picker.py` | Data & lists | Pass chosen branches of a data tree through: `0, 2, 4-6`, `-1`, `*`, `even`/`odd`, in the order given; the rest go out of `rest`; `branches` lists every branch with its size and contents |
| `scale_figure.py` | Entourage | A person as a true-height silhouette for elevations and sections: `who` ("a woman pushing a pram"), `height`, `view` (side, front, back, three-quarter); outline curves, planar fill, solid hatch |
| `scale_tree.py` | Entourage | A tree silhouette at true size: `species`, `height`, `view` elevation or plan (`spread` = canopy width); canopy gaps kept as holes |
| `alien_graphics.py` | – | Helper for the two graphics nodes (prompting, image model, cache, tracing, scaling) |
| `alien_ai.py` | – | Helper: Alien's settings, OpenRouter chat (with images) and image generation, caches in `.alien/cache` |
| `alien_assets.py` | – | Helper: saves what nodes generate to the asset library, with thumbnails |
| `alien_vicdata.py` | – | Helper for `vic_site_map`: terrain, trees and street cross-sections from open data |
| `alien_bim.py` | – | Helper for the building suite: records (JSON between nodes), units, site plane, levels and grids lookup with samples, grid intersections, text as curves, material colours, section profiles and member solids |
| `alien_walls.py` | – | Helper for the wall nodes: wall types, wall solids rebuilt from records, joins, opening placement and cutting, symbols |
| `alien_sections.py` | – | Helper for the structure nodes: Australian steel, timber and concrete sections by name (or a profile record), with sizes and kg/m |
| `alien_envelope.py` | – | Helper for slabs and roofs: slab and roof types, slab regions with openings, the weighted straight skeleton for hip and gable roofs, layered roof solids, fascias and gutters |
| `alien_stairs.py` | – | Helper for stairs and balustrades: NCC limit tables and checks, stair and balustrade types, flight, landing and spiral solids, balustrades along polylines |
| `alien_types.py` | – | Helper for door and window types: the record, the parts moved home, saving to the library |
| `alien_codegen.py` | – | Helper for the generators (object_2d, symbol, display_mode): the model loop with retries, running model-written code with only rg and math, caches, the 2D drawing model, text slots, thumbnails |
| `alien_views.py` | – | Helper for views, layout and exploded_axo: the registry and tags for named views, clipping planes and layout pages, saving views through the Perspective viewport, plane cuts, isometric directions |

Helpers are not nodes; keep them in the same folder as the nodes that import them. All nodes take sizes in metres and
scale them to the model's units. The line after the header, `# CATEGORY: ...`, places a node in the panel's library.

## Building suite (bim_site, levels, grids, walls, doors, windows, curtain_wall, slabs, roofs, stairs, balustrades, structure)

Built 2026-10-13 as a first pass at BIM-style modelling. The nodes pass elements as **records**: JSON text, one per
element (`{"alien": "level", "name": "Level 1", "code": "L01", "elevation": 3.2, ...}`), so any node, a native panel or
an agent can read them; lengths are metres in world coordinates, geometry pins are in model units. Wire them by name:

```
bim_site.site ─┬─> levels.site ──> levels.levels ─┬─> walls / curtain_wall / beams_columns .levels
               ├─> grids.site ───> grids.grids ───┴─> beams_columns.grids
               └─> walls.site      wall_types.wall_type ─> walls.wall_type
walls.walls ─> doors.walls ─> doors.walls ─> windows.walls      (each step cuts every opening so far)
beams_columns.members ─> structure_edit.members ─> connectors.members
```

- Every node shows sample geometry when unwired (sample levels, grids, room, frame or wall), so it works alone.
- Annotation (level heads, grid bubbles, dimensions) is sized in paper mm at the site's drawing scale.
- `tests/bim/chain.py` places the whole chain on the open canvas, wires it, and checks every node solved, every solid is
  valid and closed, and the timings (2026-10-13: 11 nodes, 13 wires, all ok, about 3 s for the whole solve).
- Selecting `levels` and `grids` and describing "a floor slab at every level" in the panel builds a new node already
  wired to both (it reads the records with `json.loads`).
- **Slabs, roofs, stairs, balustrades (2026-10-14):** `levels` -> `slabs` (`slab_types`), `roofs` (`roof_types`),
  `stairs` (`stair_types`) -> `balustrades` (`balustrade_types`); wire `stairs.voids` into `slabs.openings` and
  `stairs.edges` (or `slabs.edges`) into `balustrades.path`. Each edge keeps the walkable side on its left, so a
  positive balustrade offset moves onto the floor. NCC checks are first-pass design checks, not certification.
- **One footprint (2026-10-15):** wire `grids.edges` into `walls.path` (base `all`) and `grids.outline` into
  `slabs`, `roofs` and `levels` (`outline`), and walls, slabs, roof and frame line up on the same grid; unwired, each
  draws its own sample room. The panel's project agent places this whole chain with its building tool.
- **Type library:** every type node (walls, slabs, roofs, stairs, balustrades, door and window types in `doors` and
  `windows`, `section_profile`) has a Save button that adds the type to the panel's object library (Building types),
  with a thumbnail and the inputs to rebuild it (Place node). The record can be copied as JSON from there too.
- `tests/bim/chain.py` now places 22 nodes (the whole building suite plus views and exploded_axo) with 30 wires:
  all ok, every solid valid (2026-10-14).
- Limits of this pass: openings don't see walls that butt into their host; pocket doors don't cut a pocket; braces and
  inclined members get no connectors; bolts have no holes; curved grids are intersected on their chords by readers
  other than `grids`; roofs over courtyards (outlines with holes) and roof-to-roof joins aren't handled; stairs have
  no winders; no schedules yet.

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

- **Only Generate paints.** A texture is keyed by finish and variant, never by size: `size` is the real-world size of one
  tile, so moving it re-tiles the same texture for free (info notes the size it was painted at; Generate again repaints
  at the new scale). Seamless on/off, `add` and `name` never call the model. Textures painted before this change are
  found by their old prompt.
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

## Paid calls: only Generate spends credits

Every suite node that calls a model (object_3d, material_texture, scale_figure, scale_tree) has a **Generate**
button. A new request (text, a dropdown choice, variant) waits for it, with nothing sent; a result made before comes
back free from `.alien/cache`. Sliders and toggles (sizes, tile size, detail, smooth, plane) only rework the cached
result. Checked on 2026-10-12: 20 slider moves across the four nodes and 7 description changes made no model calls and
no new cache files.

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
  stricter prompt (only while Generate runs). The check uses a fixed detail, so the detail slider never causes a redraw.
- **Speed:** fills are built per outline from the traced polylines; a detailed tree (183 loops) solves in 0.6 s
  (it took about a minute when the fill was built from the smooth curves in one call).
- **Model:** `imageModel` in settings.json, default `google/gemini-3.1-flash-lite-image`: about 4–9 s and US$0.034 per
  drawing. GPT-5 Image Mini draws crisper figures but took 34 s, too long while Grasshopper waits.
- **Cache:** every drawing is cached in `<repository>/.alien/cache/graphics`. Changing size, detail or the plane costs
  nothing; a new who/species, view or `variant` waits for Generate.
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

- **Address suggestions:** typing in `address` lists matching Victorian addresses from Vicmap's address index
  (`suggest="vic-address"`, about 0.1 s per lookup; arrow keys or a click pick one; it needs the street number first).
- **Radius buckets:** data is fetched for a context bucket (60, 120, 200 or 300 m) and trimmed to `radius`, so sliding
  the radius within a bucket refetches nothing.
- **Vicmap outage fallback:** on 12 Oct 2026 Vicmap's ArcGIS property and parcel layers stopped answering spatial
  queries after a republish. The node then reads the same layers from the open-data WFS (`v_property_mp_address`,
  `v_parcel_mp`) and says so in `info`.
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
  LiDAR height and canopy half-widths. Each is modelled by shape: broad (clumps of lumpy low-poly blobs round a split
  trunk with limbs), columnar (stacked clumps), eucalypt (open, high crown on visible limbs), conifer (stacked cones) or
  palm (slender trunk and drooping fronds). Shape comes from the species (City of Melbourne) where known, else from
  proportions; each tree is seeded from its id, so it looks the same every run. Trees under 1.5 m (planted after the LiDAR)
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
