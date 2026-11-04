# Reusable prompt: floor-plan image → CAD (DXF) + GIS (GeoPackage)

> Copy everything from **"PROMPT START"** to **"PROMPT END"** into a new session,
> attach the drawing image(s), and fill in the `RUN CONFIG` block.
> The reference implementation that this prompt was distilled from lives next to
> this file: `build_floor_plan_dxf.py`, `build_geopackage.py`, `check_overlay.py`.

---

## PROMPT START

### 0. Role, goal and the one rule that overrides everything

You are converting an architectural floor-plan **image** (scan, screenshot or
photo of a screen) into:

1. a **DXF** CAD drawing in metres, and
2. a **GeoPackage** with the same geometry as GIS layers,

so that **shape, size, proportion, alignment, position, adjacency and every
small detail** match the source drawing, and every annotation is linked to the
part of the drawing it describes.

**Overriding rule: never invent, assume or "tidy up" data.** Every coordinate
must come from (a) a dimension written on the drawing, or (b) a measurement
taken from the image's pixels, and you must record which. If neither exists,
or the two disagree beyond tolerance, **stop and ask**. Do not fill gaps with
"typical" values unless the human approves each one. This prompt runs on many
drawings in bulk, so one silent assumption repeats across the whole batch.

### RUN CONFIG (human fills this in; you validate it in Phase 0)

```yaml
drawing_files:        [ <path or attachment name>, ... ]
drawing_kind:         floor plan | site plan | section | other: ___
source_type:          scan | screenshot | photo-of-screen | photo-of-paper
units_on_drawing:     m | mm | ft-in | unknown
layout_in_output:     same-as-source      # keep sheets/floors side by side as drawn
                                          # (do NOT stack floors unless "stacked" is set here)
stack_floors:         false               # true only if the human gives a shared reference point
georeference:         none | { crs: EPSG:____, control_points: [...] }
outputs:              [dxf, gpkg]         # optional: png previews, overlay images
dxf_version:          R2010
wall_thickness_default: ask               # never assume; take from drawing or ask
tolerances:
  position_m:         0.05                # max drawn-vs-measured offset after calibration
  area_pct:           0.5                 # computed vs labelled area
  angle_deg:          0.5
output_dir:           <folder>
batch_mode:           false               # true = stop at the first unresolved query per drawing
                                          # and carry on with the next drawing
```

---

### PHASE 0 — Self-check and auto-correct this prompt before doing any work

Do this first and show the result to the human. **Do not draw anything yet.**

1. Restate the RUN CONFIG back. Flag anything missing, contradictory or
   impossible, for example "stack_floors: true but no shared reference point
   given", or "units unknown and no dimension text visible".
2. Look at the drawing(s) and say which sections of this prompt apply and which
   don't. For example, Phase 6.6 (stepped hatched walls) only applies if hatched
   walls exist. Add any pattern you see that this prompt does not cover to an
   **"Uncovered patterns"** list, with a proposed method for each.
3. Produce a **corrected run plan**: the phases, in order, adjusted to this drawing.
4. Produce the first **Query list** (template in §11). Group the questions into:
   - **Blocking:** cannot start without an answer.
   - **Non-blocking:** you can proceed, and the affected item is marked
     `UNVERIFIED` until it is answered.
5. **Wait for human approval** of the run plan and answers to the blocking
   queries. In `batch_mode`, write the queries to `queries_<drawing>.md` and
   move to the next drawing.

---

### PHASE 1 — Inventory: list everything before measuring anything

Make a numbered **element register** (a table) of every distinct thing on the
drawing. Nothing may appear in the output that isn't in this register, and
nothing in the register may be missing from the output.

For each element, record:
- ID
- type (from the pattern catalogue, §10)
- floor/sheet
- approximate pixel bbox
- its text labels (verbatim, including odd spellings)
- neighbouring elements

Read **all** text verbatim, including:
- room names and codes, areas and dimension strings
- partial or cut-off text at the image edges
- labels such as "Glass", "Air-Con", "Access" and "Cln cbd"
- titles and stray numbers such as "2000" or "2006"

Never correct the spelling, and never merge two labels into one.

Note relationships explicitly:
- which blocks touch
- which are **separated by a gap**
- which walls are shared
- which floors or sheets are laid out side by side, and how far apart

---

### PHASE 2 — Calibrate: measure the image, never eyeball it

Lesson learned: estimating coordinates by eye from a downscaled view was the
root cause of most errors in the first attempt.

1. **Work at full resolution.** Get the true pixel size (`PIL.Image.open().size`).
   If you plan in a downscaled view, keep the factor `K = full_width / view_width`
   and convert every coordinate.
2. **Pixel profiling (the core measuring tool).** Take dark-pixel runs along a
   row or column, averaged over 3–6 pixels across. Each run is a wall line,
   glass dash, hatch stroke or text.

   ```python
   import numpy as np
   from PIL import Image
   im = np.asarray(Image.open(SRC).convert("L")).astype(int)

   def runs(v, offset, thr=110):
       d = v < thr
       out, i = [], 0
       while i < len(d):
           if d[i]:
               j = i
               while j < len(d) and d[j]:
                   j += 1
               out.append((i + offset, j + offset))
               i = j
           else:
               i += 1
       return out

   col = im[y0:y1, x - 3:x + 3].mean(1)    # vertical profile at column x
   row = im[y - 3:y + 3, x0:x1].mean(0)    # horizontal profile at row y
   ```

   For complex wall faces, print an **ASCII map** of rows or columns
   (`'#' if v < 100 else '+' if v < 150 else '.'`) and read the steps, piers
   and gaps from it.
3. **Watch for text contamination.** Dimension text often sits right next to
   wall faces. Before accepting a run as a wall, cross-check it at two or three
   offsets along the wall, and drop runs that only appear where text is
   printed.
4. **Fit the scale per block and per axis.** Photos of screens, and many
   scans, are not uniformly scaled: in the reference job the ground floor was
   about 48 px/m and the first floor about 46 px/m horizontally and 45 px/m
   vertically. Fit a separate affine mapping (`x = (px - x0)/sx`,
   `y = (y0 - py)/sy`) for each block, from **two or more measured faces with
   known dimensions**, and report the residuals. If the residual is above the
   position tolerance, the image has perspective or skew: ask whether to apply
   a homography (`cv2.getPerspectiveTransform` from four known corners) before
   going further.
5. **Find out what each dimension measures.** It may be to the inside face, the
   outside face or the wall centre-line.

   Lesson learned: 13-OFF was labelled 9.31 × 6.03 m. I first drew that as the
   inside, which made the room 0.8 m too wide (56 m² against the labelled
   49.53 m²). The pixels showed that 9.31 m was the **outside** width including
   two 0.4 m walls, so the inside was 9.31 − 0.8 = 8.51 m, and
   8.51 × 5.82 = 49.53 m² exactly.

   **Rule:** for every room with an area label, check
   `width × height (− cut-outs) == label` with each reading of the dimension
   (inside, outside, centre-line). The reading that matches both the label and
   the measured pixels wins. If none matches, raise a query.
6. **Check segment sums.** Dimension strings along a wall must add up to the
   wall length. In the reference job the top-wall string
   0.5 + 1.6 + 0.2 + 1.83 + 0.21 + 1.83 + 0.21 + 1.59 + 0.54 = 8.51 m confirmed
   both the inside width and the position of every pier. Do this for every
   string. A string that doesn't close is a query, not something to distribute
   silently.

Record every calibrated value in a **measurement log**:
element, source (`label` / `pixel` / `label+pixel`), value, residual, notes.

---

### PHASE 3 — Reconcile labels against the drawing (and stop where they conflict)

For each element:

| Situation | Action |
|---|---|
| Label and pixels agree within tolerance | Use the label value. Log both. |
| Label exists, pixels disagree | Check which dimension reading applies (Phase 2.5). If still in conflict, **query**. Do not average. |
| No label; clear pixel measurement | Use the pixels and mark the source as `pixel`. |
| No label; pixels unclear (blur, text overlap, cut-off) | **Query.** Mark `UNVERIFIED` and leave it out of the output until answered. |
| Drawing's own numbers disagree (e.g. 3.64 × 4.3 = 15.65 but labelled 15.74 m²) | Keep the dimensions, keep the area label as text, and record the difference. Never adjust geometry to fit a label. |
| Net vs gross area (piers or chimney breasts inside a room) | Keep the drawn shape, report both areas, and say which one the label matches. |

**Re-measure before you fix (lesson learned).** In the reference job a
"0.12 m offset" in a glass partition turned out to come from the overlay's own
scale, not the geometry. Before changing any element, re-measure it in pixels
with the fitted scale for that block.

---

### PHASE 4 — Geometry model (data first, code second)

Put all geometry in **plain data tables** at the top of the build script, one
row per element, with a comment giving its source (label or pixel). Coordinates
are in metres, with Y up and the origin at a clearly stated building corner.

- **Each floor or sheet gets its own local frame plus a sheet offset**, so the
  output layout matches the source layout. Do not stack floors unless the RUN
  CONFIG says so and gives a shared reference point (lesson learned: stacking
  on an assumed corner was rejected by the human).
- `ROOMS`: name → (floor, polygon of inside faces). Use real vertices for L
  shapes, steps, chamfers and angled walls. Never simplify to a bounding
  rectangle.
- `CIRCULATION`: corridors and lobbies that are enclosed by walls but not
  labelled. They are needed for walls and hatches.
- `DOORS`: (room, floor, hinge point, closed-end point, swing side ±1). The
  leaf is drawn at 90° and the arc runs from the closed end to the open leaf.
  Check every door against the image on four things: **which jamb is the
  hinge, which way the leaf points, which room it swings into, and the width
  of the opening.**

  Lesson learned: the entrance, 01-REC, 04-WCS and the cupboard doors were all
  drawn mirrored or swinging the wrong way in the first pass.
- `WINDOWS`: wall breaks shown with the drawing's own window symbol, such as
  triple lines or a thin sill. Never invent them from gaps.
- `GLASS`: dashed partitions, as polylines.
- `PARTITIONS`: solid internal walls, as centre-line plus thickness.
- `WALL_BLOCKS`: piers, columns, corner thickenings and chimney breasts, as
  polygons.
- `HATCHED_WALLS`: outer rectangle plus the **stepped** inner face (Phase 6.6).
- `STAIRS`: every tread, winder, landing edge and flight line seen. Count them.
- `TEXT`: (text, pixel x, pixel y, height, rotation). Convert to metres with
  the **same per-block mapping** fitted in Phase 2.

**Gaps between blocks are geometry too.** Record them explicitly, for example
`Y13_OUTER_BOTTOM = 8.97` and `Y_LOWER_OUTER_TOP = 8.84`.

Lesson learned: growing every room outward to make the walls joined 13-OFF to
the block below it and ran its hatched side walls down into that block. The
source showed its own 0.11 m bottom wall, a 0.13 m gap, then the lower block's
own 0.09 m top wall.

---

### PHASE 5 — Self-check list for every element (before building)

- [ ] Dimensions match labels, or the reason they don't is logged.
- [ ] Area matches the label within `area_pct`, or the reason is logged.
- [ ] Position checked against pixels within `position_m` in that block's
      fitted frame.
- [ ] Neighbours are right: shared walls are shared, gaps are kept, nothing
      overlaps unless the drawing shows it.
- [ ] Alignment is right: walls that line up on the drawing line up in the
      model (same x or y), and walls that are offset stay offset.
- [ ] Doors: the hinge, leaf, swing room and width have each been checked.
- [ ] No **extra** lines. Lesson learned: a corridor wall that didn't exist was
      added, and a thick wall became two parallel lines.
- [ ] No **missing** lines. Every line in the element register is drawn.

---

### PHASE 6 — Build the DXF (exact method used)

**Install the tools:**
`pip install ezdxf shapely matplotlib pillow numpy geopandas pyogrio`
(`opencv-python-headless` only if perspective correction is approved.)

**6.1 Document setup**
- Start with `ezdxf.new("R2010", setup=True)`.
- Set the units to metres: `doc.units = ezdxf.units.M` and `$INSUNITS = 6`.
- Add a text style, and a dashed linetype for glass.

**6.2 Layers.** Use these layers, so the CAD user can switch each element type
on and off:

| Layer | Content |
|---|---|
| `A-WALL` | Inside wall faces |
| `A-WALL-OUTER` | Outer building line |
| `A-WALL-POCHE` | Solid wall fill |
| `A-WALL-HATCH` | Hatched walls |
| `A-GLAZ` | Glass partitions (dashed) |
| `A-DOOR` | Door leaves and swing arcs |
| `A-STAIR` | Stair lines |
| `A-AREA-BOUNDARY` | Closed room outlines for the AREA command (layer off) |
| `A-ANNO-ROOM` | Room labels |
| `A-ANNO-DIM` | Dimension text |
| `A-ANNO-TITLE` | Titles |
| `A-UNVERIFIED` | Anything awaiting human review (red); must be empty at final delivery |

**6.3 Door openings.** Each door cuts a band through the wall:
`LineString([hinge, end]).buffer(0.25, cap_style="flat")`. Subtract it from
every wall line and fill with shapely `difference`. This opens both faces of
the wall.

**6.4 Wall lines.**
- Draw each room's and corridor's outline ring minus the door bands as
  `LWPOLYLINE`s.
- Outer building line: grow the spaces in each block by the measured wall
  thickness (`buffer(t, join_style="mitre")`), union them, and fill small
  internal voids by taking each polygon's exterior.
- **Build each separated block on its own** and clip it at its measured outer
  face, so real gaps survive.

**6.5 Solid wall fill.**
`footprint − union(spaces) − door bands ∪ partitions`, written as a SOLID
`HATCH`: an outer path with `flags=1`, and holes with `flags=0`.

**6.6 Hatched walls with a stepped inner face.** Trace the inner face with an
ASCII profile. Model it as:
- recessed bays at the base inner face,
- piers of the measured width that stand out by the measured depth, and
- corner blocks (often L-shaped and of different sizes at each corner).

Build the room polygon as `rectangle.difference(union(blocks))`. The hatched
band is `outer − room − (any solid strip the drawing shows unhatched)`. Use
`ANSI31` at a scale that looks like the source.

Lesson learned: a plain rectangular hatched band was rejected. The source had
recessed bays between 0.21 m piers standing 0.1 m proud, with thickened
corners.

**6.7 Doors.** Draw the leaf as a `LINE` and the swing as an `ARC`. Sort the
start and end angles by swing side.

**6.8 Stairs.** Draw every tread, winder and landing line that is seen as a
polyline.

**6.9 Text.**
- Place each label with `add_text(...).set_placement(xy, align=MIDDLE_CENTER)`,
  at its original height and rotation (vertical text = 90°).
- Link each label to the **smallest space containing it**. If it sits in no
  space (for example on a wall), link it to the nearest room.
- Titles and site labels go in a `TITLES` group.

**6.10 Groups.** Make one DXF `GROUP` per room or space. It holds the walls,
doors, glass, stairs, hatch and labels, so selecting a room selects its
annotation.

**6.11 Check the file.** Run `ezdxf.recover.readfile()`, which must report
**0 errors**, and print every room's area next to its label.

**6.12 Preview.** Render with the `ezdxf.addons.drawing` matplotlib backend,
using `BackgroundPolicy.WHITE` and `ColorPolicy.COLOR`.

Lesson learned: other colour policies turned white or black layers invisible
on a white background.

---

### PHASE 7 — Verify by overlay (mandatory, zone by zone)

1. Draw the DXF entities back onto the **source image**, using each block's
   fitted pixel mapping:
   - walls red, glass orange, doors green, stairs magenta, label anchors as
     blue dots,
   - on top of a 35%-whitened copy of the source.

   Reference script: `check_overlay.py`.
2. Inspect each block and each wall close up, at 1.5–2× zoom. Look at:
   - the outer faces,
   - the inner faces,
   - every door's hinge, leaf and swing,
   - glass lines,
   - piers and steps,
   - gaps between blocks,
   - stair lines,
   - whether every label dot lands on its text.
3. List each gap with its size and direction, for example "0.45 m too far
   right" or "door hinged on the wrong jamb". **Re-measure in pixels before
   fixing** (Phase 3 rule), and correct the report if a gap turns out to come
   from the overlay.
4. Fix, rebuild and re-overlay. Repeat until every zone is within tolerance.
   Include the final overlay images with the output.

---

### PHASE 8 — GeoPackage export (exact method used)

Read the geometry back from the **same** data tables and DXF, so the two
outputs can't drift apart. Use `geopandas` with the `pyogrio` engine.

**8.1 Layers.** Each layer has the fields `room_id`, `floor` and `level`.

| Layer | Geometry | Contents |
|---|---|---|
| `rooms` | Polygon | `room_type`, `ref_code`, `area_label_m2`, `area_calc_m2`, `labels` |
| `wall_fill` | Polygon | Solid wall bands |
| `wall_hatch` | Polygon | Hatched walls (`element`, `area_m2`) |
| `walls` | LineString | `part`: inner face, outer face, or pier/partition/chimney |
| `glazing` | LineString | Glass partitions |
| `doors` | LineString | `part`: leaf or swing arc |
| `stairs` | LineString | Stair lines |
| `annotations` | Point | `text`, `kind`, `rotation`, `height_m` |

**8.2 Layout and CRS.**
- Keep the source layout (floors side by side) unless `stack_floors: true`.
- No CRS unless `georeference` is given. Never invent a location.

**8.3 Styling.** Optional: QGIS default styles in a `layer_styles` table.
Label the annotations from the `text` field, with size `"height_m"` × 1.35 in
map units and rotation `-"rotation"`.

**8.4 Checking.**
- Count the features in each layer.
- List any annotations not linked to a room (only titles should be).
- Compare `area_calc_m2` with `area_label_m2`.
- Render the GeoPackage itself to a PNG and compare it with the DXF preview.

---

### PHASE 9 — Report back (every run)

1. **Outputs:** the file paths, the DXF check result and the feature counts.
2. **Area table:** labelled area against computed area for each room, with the
   reason for any difference.
3. **Measurement log summary:** which values came from labels, which from
   pixels, and which are `UNVERIFIED`.
4. **Uncertainty register:** every judgement call, with its evidence and the
   alternatives.
5. **Open queries** for human review (§11).
6. The overlay images.

Report results as they are: if something is unverified or failed, say so.

---

### 10. Pattern catalogue (what to look for, and how to model each)

**Walls and shells**
- Plain or thick walls (draw as solid fill).
- Single-line walls (ask for the thickness).
- Hatched or cross-hatched walls (§6.6).
- Stepped or recessed faces, piers, columns (square or round).
- Pilasters, chimney breasts, ducts and risers.
- Angled or chamfered corners (use real vertices, and confirm the angle from
  pixels).
- Curved walls (fit an arc from three or more pixel points, and give its
  radius).

**Openings**
- Single, double and unequal doors.
- Sliding, pocket, folding and revolving doors.
- Glazed doors in partitions.
- Door-less openings.
- Windows, which are wall breaks drawn with the window symbol.
- Roller shutters.

Check the hinge, swing room and width individually for each.

**Partitions**
- Dashed or glazed partitions.
- Solid partitions, including stubs and returns.
- Partitions that stop short of a wall: keep the gap.

**Vertical circulation**
- Straight, dog-leg, winder and spiral stairs: count the treads, and draw every
  landing line and flight line.
- Lifts, ramps (slope arrows) and ladders.

**Blocks and layout**
- Separate blocks with gaps, setbacks and offsets.
- Annexes that are narrower or offset (for example 07-OFF, which sits off the
  main block's edge).
- Rotated blocks.
- Non-orthogonal buildings (fit the angle).

**Sheets**
- Several floors or sheets side by side.
- Cut-off edges: draw only what's visible, and say so.

**Annotation**
- Room name, code and area.
- Dimension strings, including partial ones.
- Level or floor labels, and site labels (parking, access).
- North arrow, scale bar and grid lines: use these for calibration and
  orientation when present.

**Symbols**
- Sanitary fittings, furniture, equipment ("Air-Con") and cupboards. Draw them
  only if they're in the element register, as blocks or text, and never add
  any.

**Image problems**
- Perspective or skew (homography, after asking).
- Blur, moiré and glare (widen the averaging, and query anything uncertain).
- Handwriting (read verbatim, and query anything unclear).

For anything not in this catalogue: describe it, propose a method, and ask
before building it.

---

### 11. Query template (raise before acting on unclear data)

```
Q<n> [BLOCKING | NON-BLOCKING] — <element ID / location>
What I see:      <pixel evidence, crop reference, measured values>
What the labels say: <verbatim text>
Conflict / gap:  <why I can't decide>
Options:         A) ... (implies ...)   B) ... (implies ...)
My recommendation: <option + reason>   — I will NOT apply it without your approval.
Until answered:  <element left out | drawn and flagged UNVERIFIED on layer A-UNVERIFIED>
```

Typical queries:
- Is a dimension to the inside face, the outside face or the centre-line?
- Wall thickness where there's no label.
- Which side a door is hinged on when the arc is unclear.
- Whether blocks touch or are separated by a gap.
- Whether two floors share a reference point (only if stacking is wanted).
- Text that can't be read, or is cut off.
- A sketch whose own numbers disagree.
- Perspective correction.
- Whether to model symbols such as furniture.

---

### 12. Acceptance criteria (all must pass before final delivery)

- [ ] Every element in the register is present, and nothing extra is drawn.
- [ ] Every label is reproduced verbatim at its position, height and rotation,
      and linked to its room or space.
- [ ] Areas match the labels within tolerance, or the difference is explained.
- [ ] The overlay shows walls, doors, glass, stairs, piers and gaps within
      `position_m` in **every** zone.
- [ ] Layout is the same as the source: floors and sheets are side by side,
      separate blocks stay separate, and offsets are kept.
- [ ] The DXF check reports 0 errors, the GeoPackage reads back, and the
      feature counts are reported.
- [ ] Nothing that is `UNVERIFIED` is presented as final, and every open query
      is listed.

## PROMPT END

---

## Appendix A — Iteration history this prompt encodes (reference job)

| # | Human feedback / issue found | Root cause | Rule now in prompt |
|---|---|---|---|
| 1 | First DXF: plausible but approximate | Coordinates estimated by eye from a downscaled view | Phase 2: full-resolution pixel profiling, scale fitted per block and per axis |
| 2 | 13-OFF 0.8 m too wide, area 56.14 vs 49.53 | 9.31 × 6.03 m read as inside, but it was the outside size | Phase 2.5: decide what each dimension measures, checked against the area label |
| 3 | Doors mirrored or swinging the wrong way (entrance, 01-REC, 04-WCS, cupboard) | Hinge side not checked for each door | Phase 4 `DOORS` and the Phase 5 door check |
| 4 | Extra corridor wall; thick wall drawn as two lines | Corridor walls guessed | Phase 5 "no extra lines"; walls built from spaces plus solid fill |
| 5 | Walls drawn as thin single lines | No solid wall fill or outer line | Phase 6.4 / 6.5 |
| 6 | Reported glass offset of 0.12 m was false | The overlay's own scale was wrong, not the geometry | Phase 3 "re-measure before you fix" |
| 7 | Stair pattern simplified | Only one flight drawn | Pattern catalogue: draw every flight and landing line |
| 8 | GeoPackage floors stacked on top of each other: rejected | Assumed a shared corner | RUN CONFIG `layout_in_output` / `stack_floors`; no stacking without a reference point |
| 9 | Hatched wall shape wrong | Plain band instead of bays, piers and corner blocks | Phase 6.6: trace the stepped face with an ASCII profile and segment sums |
| 10 | 13-OFF joined to the block below | Growing rooms outward merged separate blocks | Phase 4 "gaps are geometry"; Phase 6.4 per-block outline clipped at its outer face |
| 11 | Preview layers invisible | Wrong colour setting | Phase 6.12 |
| 12 | Overlay script failed | Relative paths | Run scripts from their own folder |

## Appendix B — Reference files

- `build_floor_plan_dxf.py`: the data tables (`ROOMS`, `CIRCULATION`,
  `DOORS`, `GLASS`, `PARTITIONS`, `WALL_BLOCKS_13`, `STAIRS`, `GF_TEXT`,
  `FF_TEXT`), the shapely wall model and the DXF writer.
- `build_geopackage.py`: DXF and data tables → GeoPackage, with QGIS styles.
- `check_overlay.py`: draws the output over the source image for checking.
- To adapt them to a new drawing, replace only the data tables and the pixel
  mappings (`gf()`, `ff()`). Keep the code paths, since they hold the fixes
  listed above.
