# Demo definitions

Eight Grasshopper definitions for showing Alien, each a fresh file with a title and short instructions on the canvas.
Built and checked by `tests/demos/build.py` (every node solves, no errors or unset outputs, after reopening from
disk); the previews are viewport captures from that run.

**Set up:** copy this folder into your Alien script folder as `demos/` (the definitions find their nodes at
`repo:suite/...` and `repo:demos/...`), then open any `.gh`. Rhino in any units: the suite and the demo nodes work in
metres and scale.

| File | Shows | Try |
|---|---|---|
| `01_site_map.gh` | `vic_site_map` for 201 Napier Street, Fitzroy, styled in greys: terrain, contours, white massing, dark carriageways, footpaths, nature strips, kerbs, see-through tree canopies, lot lines and the site boundary draped on the ground ([iso](previews/01_site_map.png), [plan](previews/01_site_map_plan.png)) | Type another Victorian address; change a swatch to restyle a layer |
| `02_object_3d.gh` | `object_3d`: a mid-century lounge chair written by a model (US$0.019), and a table and chair from the library, laid out by `spots`; the generated code in a panel ([preview](previews/02_object_3d.png)) | Change `thing` or `style` and press generate; drag width, depth, height |
| `03_entourage_2d.gh` | 2D entourage for an elevation: image-traced people, a cyclist and a street tree (`scale_figure`, `scale_tree`) and a car drawn by code (`object_2d`), standing on one ground line ([preview](previews/03_entourage_2d.png)) | Change `who` / `species` / `thing` and press generate |
| `04_frame_connections.gh` | Catalogue sections (`section_profile`: 250UC89.5 columns, 410UB53.7 beams) into a two-bay frame (`beams_columns`), the roof beams taken down a size (`structure_edit`), and base plates, end and fin plates, bolts and welds at every joint (`connectors`) with a schedule ([frame](previews/04_frame_connections.png), [joint](previews/04_frame_connections_joint.png)) | Another size on either profile; edit lines on structure_edit |
| `05_attractor_facade.gh` | A perforated rainscreen on a curved facade: openings swell towards an attractor point and panels darken with them, glass behind ([preview](previews/05_attractor_facade.png)) | Drag `u` / `v`, or click `attractor` and pick a point; shape, reach, panel count |
| `06_texture_material.gh` | `material_texture` paints rough sawn cedar boards (US$0.034), made seamless, as a preview material and (press `add`) a Rhino render material; `texture_mapping` maps a wall, plinth and bench at real size; one slider sets the tile size for both ([preview](previews/06_texture_material.png)) | Another finish and generate; the tile size slider |
| `07_exploded_axo.gh` | The project agent's building tool house (site, levels, grids, walls, doors, windows, slabs, stair, balustrades, roof) pulled apart storey by storey beside the original (`exploded_axo`, height bands) with dashed explosion lines and labels ([preview](previews/07_exploded_axo.png)) | Spacing, mode (vertical stack, radial...); press `view` for a named isometric view |
| `08_framed_tower.gh` | Ten storeys on a 4 x 3 bay, 8.4 m grid: steel frame at every intersection, post-tensioned slabs 0.45 m past the grid, curtain wall 0.7 m out with transoms at each slab edge ([preview](previews/08_framed_tower.png)) | Storeys on levels, bays on grids: everything follows |

**Demo nodes** (in this folder, used by the definitions):
- `spots.py`: five placement planes in a row, in metres, so a layout holds in any model units.
- `attractor_facade.py`: the attractor facade.
- `texture_mapping.py`: real-size texture coordinates for any geometry (Grasshopper previews map each face 0-1).
- `drape_curves.py`: lays flat curves (lot lines) onto a terrain mesh.

**On another machine:** the generated pieces (the lounge chair, the car, the cyclist, the cedar texture) and the site
data come from this machine's cache (`<script folder>/.alien/cache`). Elsewhere the site map fetches its data on
first open (free), and the generator nodes wait for generate (paid, as above). The table, chair, couple, walker
and tree are the same: press generate once. Rebuild everything with `python3 tests/demos/build.py` (Rhino open).
