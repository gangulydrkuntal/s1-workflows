# Sentinel-1 use cases for the Google Earth Engine Code Editor

Seven self-contained JavaScript workflows for the [GEE Code Editor](https://code.earthengine.google.com/).
Use cases 1–6 each implement a published, widely cited Sentinel-1 method; use case 7 uses the AlphaEarth Foundations satellite embeddings, which include Sentinel-1 among their inputs. Each runs on a test site that has
independent reference data, and each builds its own UI: a layer list, a legend, statistics, charts
and click-to-inspect time series.

| # | Script | Application | Method (key reference) | Test site | Reference / validation |
|---|--------|-------------|------------------------|-----------|------------------------|
| 1 | `01_flood_mapping_bihar2017.js` | Flood inundation | Log-ratio change detection + edge-based Otsu (Donchyts et al. 2016; Markert et al. 2020; Twele et al. 2016) | North Bihar, India, Aug 2017 (DFO 4507) | **Global Flood Database** (Tellman et al. 2021, *Nature*), in GEE: confusion matrix, OA, kappa, F1, IoU |
| 2 | `02_building_damage_pwtt_turkey2023.js` | Building damage | Pixel-Wise T-Test, PWTT (Ballinger 2025, *RSE*) | Antakya, Türkiye, earthquake 6 Feb 2023 | **Negative control city** (Mersin) gives an empirical false-positive rate. Optional UNOSAT / Copernicus EMSR648 points give a ROC curve and AUC |
| 3 | `03_oil_spill_baniyas2021.js` | Marine oil spill | Adaptive dark-spot detection + object features (Solberg et al. 2007; Brekke & Solberg 2005; Topouzelis 2008) | Baniyas, Syria, 23 Aug 2021 onwards | **Pre-spill negative control** (false-alarm area), ERA5 wind check, area vs time compared with the reported slick (imaged 24–25 Aug; ~800 km² by ~31 Aug). Optional reference polygon gives IoU |
| 4 | `04_omnibus_change_detection_deforestation.js` | Multi-temporal change detection | Sequential omnibus likelihood-ratio test (Conradsen et al. 2016, *IEEE TGRS*; Canty et al. 2020, *Remote Sens.*) | Jaci-Paraná Extractive Reserve, Rondônia, Brazil, 2020–2021 | **Hansen Global Forest Change v1.12**: stratum-weighted accuracy (Olofsson et al. 2014) and year-of-change agreement |
| 5 | `05_crop_classification_rf_cdl.js` | Crop-type mapping (ML) | Random Forest on dense S1 time series (Veloso et al. 2017; Belgiu & Drăguţ 2016) | Red River Valley, ND/MN, USA, 2021 | **USDA Cropland Data Layer 2021**, with a spatially blocked train/test split: OA, kappa, PA/UA/F1, confusion matrix |
| 6 | `06_peatland_mapping_uk.js` | UK peat-soil extent (ML) | Random Forest on terrain-flattened S1 statistics + LiDAR slope/TPI/TWI + climate (+ S2); digital soil mapping (Minasny et al. 2019; Karlson et al. 2023; Vollrath et al. 2020) | Peak District, England (Dark Peak blanket peat vs White Peak limestone) | Spatially blocked hold-out (compared against a random split), ROC/AUC, feature-group ablation, known-site stress tests. Optional: national peat map upload and **field depth probes** (the only truly independent test) |
| 7 | `07_field_boundaries_alphaearth.js` | Field boundary delineation | Unsupervised edge map in AlphaEarth embedding space + extent "cutoff" (Waldner & Diakogiannis 2020), with a SNIC superpixel baseline (Achanta & Süsstrunk 2017) | Cambridgeshire, England, 2021 (or Story County, Iowa) | **UKFields** (UK) or **USDA Crop Sequence Boundaries** (US), both in GEE: IoU, over/under-segmentation and D index (Clinton et al. 2010), boundary F1. Optional: RPA parcel polygons / Parcel Points (England) |

## How to run

1. Open <https://code.earthengine.google.com/> with an Earth Engine-enabled account.
2. Create a new script, paste the contents of one `.js` file, and click **Run**.
3. The map zooms to the test site. The left panel shows the legend, the statistics, the validation
   metrics and the charts. Click on the map to plot a pixel's backscatter time series.
4. To analyse another area or event, edit the `USER PARAMETERS` block at the top of the script
   (AOI, dates, thresholds).

All inputs are public Earth Engine catalog datasets, plus one GEE community-catalog dataset (the
Microsoft building footprints). No uploads are needed. The only optional inputs are your own
reference layers in use cases 2 and 3.

Heavy statistics (the building-level aggregation in #2, the area time series in #3) run when you
press a button, so the map loads fast. Every script also queues an `Export` task, which you can
start from the **Tasks** tab to get the full-resolution products.

## Sentinel-1 pre-processing chain

`COPERNICUS/S1_GRD` (and `S1_GRD_FLOAT`) in GEE has already been processed with ESA SNAP: orbit
file applied, GRD border-noise removal, thermal-noise removal, radiometric calibration to σ⁰, and
Range-Doppler terrain correction with SRTM-30. The scripts add the application-specific steps
recommended in the literature, notably Mullissa et al. (2021), *Sentinel-1 SAR Backscatter
Analysis Ready Data Preparation in Google Earth Engine*:

| Step | #1 Flood | #2 Damage | #3 Oil | #4 Change | #5 Crops |
|------|:-------:|:--------:|:-----:|:--------:|:-------:|
| IW mode, VV+VH (VV only for oil) | ✔ | ✔ | ✔ | ✔ | ✔ |
| Swath-edge / incidence-angle mask | ✔ | – | – | – (full-cover filter) | ✔ |
| Linear power domain for filtering / statistics | ✔ | ✔ | ✔ | ✔ | ✔ |
| Speckle filter | Refined Lee | Lee MMSE (ENL 5) | 4×4 multilook (40 m) | **none**, see note | 15-day temporal mean + 3×3 boxcar |
| Same relative orbit for comparisons | ✔ | ✔ (one test per orbit) | – | ✔ | – (γ⁰ normalisation) |
| Incidence-angle handling | same orbit | same orbit | local background ratio | same orbit | γ⁰ = σ⁰/cos θ |
| Terrain / context masks | HAND > 15 m, slope > 5°, JRC permanent water | Dynamic World built-up | land mask + 1.5 km coastal buffer, low-wind mask | Hansen forest domain | CDL confidence ≥ 80, field interiors |

Use case 6 (peat) uses linear `S1_GRD_FLOAT`, masks swath edges, and applies **angular-based
radiometric terrain flattening** (volume model) with layover/shadow masking (Vollrath et al. 2020).
It then combines both orbit directions into temporal statistics: median, 10th/90th percentile,
standard deviation, winter and summer medians, seasonal difference and VH/VV ratio. About a year
of images, averaged over 30 m pixels, does the speckle reduction.

Note on #4: the omnibus test is a likelihood-ratio test on the Wishart/Gamma speckle statistics
with a known number of looks (ENL = 4.4 for IW GRD). Speckle filtering would change those
statistics and invalidate the p-values, so Canty et al. (2020) explicitly use unfiltered linear
intensities.

## Scientific notes and expected behaviour per use case

### 1. Flood mapping (North Bihar, Aug 2017)
- **Why change detection plus a threshold:** a single-image threshold confuses floodwater with
  permanently dark surfaces (smooth tarmac, sand, radar shadow). Requiring a ≥ 3 dB drop relative to
  a dry-season reference from the same orbit removes most of these.
- **Otsu on an edge buffer:** the scene histogram is usually not bimodal. Sampling only around
  Canny-detected land/water edges makes it bimodal, so Otsu works reliably.
- **Validation caveats:** the GFD map is a multi-day MODIS **maximum** extent at 250 m with
  cloud gaps, whereas S1 gives a 10 m snapshot. Agreement is computed only where MODIS had clear
  views, using a majority rule on the GFD grid. Expect high overall accuracy and kappa, and
  moderate recall: C-band misses flooding under crops and trees, and some flooding happened
  between S1 passes.

### 2. Building damage (Antakya, 2023 earthquake)
- **Physics:** collapsed buildings lose their dihedral (double-bounce) scattering and gain rubble,
  which causes a persistent shift in mean backscatter. The t-test scales this shift by each pixel's
  own pre-event variability, using about 30 pre-event images per orbit. This makes the test far
  more robust than a single image pair.
- **Settings:** the published defaults are used (12-month baseline, 1-month inference window,
  T > 3.3, 50/100/150 m multi-scale smoothing). For high-confidence severe damage only, raise the
  threshold to 4–5.
- **Validation:** Mersin, about 250 km away and undamaged, is processed with identical code; the
  share of its built-up area flagged as damaged is the empirical false-positive rate. For full
  building-level validation, download the UNOSAT damage assessment for Antakya (UNITAR/UNOSAT, via
  HDX) or the Copernicus EMS EMSR648 grading. Upload it as a table asset, then set
  `REFERENCE_ASSET`, `DAMAGE_PROPERTY` and `DAMAGED_VALUES`. The script then draws a ROC curve and
  computes the AUC; the paper reports AUC 0.81–0.88 against UNOSAT in Gaza and Ukraine.

### 3. Oil spill (Baniyas, Aug 2021)
- **Physics:** oil damps the Bragg-scale capillary waves, so slicks appear 3–10 dB darker than
  surrounding sea in VV. Detection is only reliable at about 2–3 to 10–12 m/s wind, so ERA5 wind at
  the acquisition hour is reported with every image.
- **Adaptive threshold:** pixels are compared with a 6 km focal-median background. This removes
  incidence-angle and wind-field trends and is robust to the slick itself.
- **Look-alikes:** low-wind areas, biogenic films, rain cells and upwelling also look dark. Objects
  are labelled *likely oil* (damping ≥ K+1 dB and complex/elongated shape, C ≥ 1.8) or
  *possible oil / look-alike*. The feature-space scatter plot is there to support the analyst's
  decision. Final confirmation needs an analyst or a trained classifier, as in operational systems
  such as EMSA CleanSeaNet.
- **Validation:** the pre-spill images (1–21 Aug 2021) show the background false-alarm level. The
  time-series button plots dark-spot area against date, which should rise sharply after 23 Aug.

### 4. Omnibus change detection (Jaci-Paraná, 2020–2021)
- **Sequential test:** first an omnibus test of whether *anything* changed in the k-image series,
  then the factorised R_j tests locate *when* it changed, re-starting after each change (Conradsen
  et al. 2016). Outputs: first change (`smap`), last change (`cmap`), number of changes (`fmap`),
  per-interval flags (`bmap`), and the direction of the first change (increase, decrease or mixed).
- **Validation:** the domain is forest intact on 1 Jan 2020. S1 change is compared with Hansen loss
  in 2020–2021 on a stratified random sample, with stratum-weighted accuracy estimates. The script
  reports two variants: any significant change, and decrease-only changes (canopy removal typically
  lowers VH).

### 5. Crop classification (Red River Valley, 2021)
- **Features:** 14 composites of 15 days each (April–October) × {VV, VH, VH/VV} capture crop
  phenology. For example, the VH rise of corn and soybean in July contrasts with the early decline
  of wheat after harvest.
- **Label quality:** only CDL pixels with confidence ≥ 80 inside homogeneous 3×3 neighbourhoods are
  used, which limits label noise and mixed pixels.
- **Honest accuracy:** the train/test split is a checkerboard of ~5 km blocks, so training and test
  samples never share a block. This avoids the inflated accuracies that random pixel splits give
  under spatial autocorrelation. CDL is itself a classification (major-crop accuracy ≈ 85–97 %),
  so the figures measure agreement with the best available reference.

### 6. UK peatland mapping (Peak District)

**Research basis.** Peat is a soil, defined by an organic layer of a minimum depth. C-band radar
penetrates only a few centimetres, so no satellite measures peat directly. National and research
peat maps are therefore built the way digital soil mapping works: a machine-learning model links
known peat locations to proxies (Minasny et al. 2019, *Earth-Sci. Rev.* 196:102870):
- **Sentinel-1:** surface wetness and vegetation structure, and their seasonal dynamics. UK
  blanket-bog studies relate backscatter and coherence to water table and soil moisture (Toca et
  al. 2023, Forsinard Flows; Lees et al. 2021; InSAR coherence in the Flow Country, *Remote Sens.*
  2025).
- **Topography:** peat forms on gentle slopes, plateaux and water-receiving hollows. Slope and TWI
  are among the strongest predictors of peat depth in UK uplands (Gatis et al. 2019, *Geoderma*,
  Dartmoor; Finlayson et al. 2021, *Soil Use Manage.*; Aitkenhead 2017/2020, Scotland).
- **Climate:** blanket bog needs high rainfall and many rain days (Lindsay 1995).
- **Fusion:** combining S1, S2 and terrain gives 80–90 % accuracy for peatland types (Karlson et al.
  2023, *JGR Biogeosciences*).
- **National precedent:** the **England Peat Map (Natural England, May 2025)** used the same recipe
  (Sentinel-1/2, EA LiDAR slope, geology, climate, random forest, more than 300,000 training
  points) and reports over 95 % accuracy for peat extent.

**Plan implemented in the script.**
1. Pre-process Sentinel-1 for one hydrological year (S1A+S1B). Apply terrain flattening and
   layover/shadow masks, then compute 15 temporal backscatter features.
2. Build covariates at 30 m on the British National Grid: EA 1 m LiDAR DTM aggregated to 30 m
   (Copernicus GLO-30 outside England), slope, TPI at 300 m and 1 km, TWI (MERIT-Hydro upstream
   area), WorldClim rainfall and temperature, and optional Sentinel-2 NDVI/NDMI/NDWI
   growing-season medians (Cloud Score+ masked).
3. Exclude water and built-up land from both training and prediction. Reservoirs and urban areas
   were among the failures publicly reported for the national map.
4. Take labels either from the **Global Peatland Map 2.0** (default, in GEE; interior of
   peat-dominated 1 km cells vs land more than 2 km from any mapped peat) or from an uploaded
   national map:
   - England Peat Map extent (Natural England / Defra Data Services Platform);
   - Unified Peat Map of Wales (UKCEH EIDC, doi:10.5285/58139ce6-63f9-4444-9f77-fc7b5dcc00d8) or
     its 2022 update on DataMapWales;
   - Scotland Carbon & Peatland 2016 (classes 1, 2, 5 = peat; 4 = non-peat; 3 left out as
     ambiguous).
5. Train a Random Forest with probability output, and produce a peat probability map, a binary
   map (p ≥ 0.5) and an uncertainty layer.

**Validation design.** No single test is enough here, so the script layers several:

| Test | What it shows | Limitation |
|------|---------------|-----------|
| Spatially blocked hold-out (~3 × 4 km checkerboard) | Accuracy, kappa and F1 when predicting into unseen areas | Measured against the label map, so it is *agreement*, not truth |
| Same model with a random split | How much spatial autocorrelation inflates accuracy (reported in percentage points) | Diagnostic only |
| ROC / AUC | Discrimination power of the probability, independent of the 0.5 threshold | Same labels as above |
| Feature-group ablation | Whether Sentinel-1 actually adds information beyond terrain and climate | Same labels |
| Known-site stress tests | Kinder Scout and Bleaklow plateaux (deep blanket peat) should be ≥ 70 % peat; the White Peak limestone plateau should be ≤ 10 %. Shown as PASS/FAIL | Only three sites; the polygons are approximate |
| Field depth probes (optional upload) | Real accuracy against measured depth (default threshold 40 cm, the English "deep peat" definition), plus probability vs depth | Needs access to probe data (England Peat Map surveys, Moors for the Future, Peatland ACTION) |

**Challenges.**
1. **Sensing physics.** S1 responds to the top few centimetres of vegetation and soil moisture, so
   peat *extent* is inferred and *depth* cannot be mapped with S1. Depth needs probes, GPR or
   airborne gamma radiometrics (Gatis et al. 2019).
2. **Land-use masking of peat.** Drained arable peat (the Fens), grassland on peat and conifer
   plantations on peat look like mineral land. Conversely, wet acid grassland, rush pasture and
   heather on thin peaty podzols look like peat. A model trained in the Peak District uplands
   will not transfer to lowland fens or the Flow Country without retraining.
3. **Inconsistent definitions.** England uses ≥ 40 cm for deep peat and 10–40 cm for peaty soils;
   Scotland uses > 50 cm; international usage is often ≥ 30 cm. Reference maps built on different
   definitions disagree, so state the definition you use.
4. **Reference quality.** The GPM 2.0 is generalised to 1 km, so its labels are noisy at 30 m and
   default-mode accuracies are optimistic about real-world performance. The England Peat Map was
   criticised after release for showing peat on limestone pavement, reservoirs, rivers, granite
   tors and quarries, for misreading shadows as bare peat, and for missing some known peat SSSIs.
   Natural England advises against using it to locate deep peat on individual landholdings.
   Accuracy against any map is agreement with that map; only field data validates.
5. **Upland terrain.** Terrain flattening and layover/shadow masks reduce slope-induced radiometry
   but don't remove it. Steep cloughs and edges remain the least reliable areas.
6. **Temporal variability.** Drought years (2018, 2022) change peat surface wetness, so the
   features should come from a typical year or several years. Revisit dropped to 12 days after
   Sentinel-1B failed in Dec 2021; Sentinel-1C (2025) restores density for recent years.
7. **Scale mismatch.** 30 m predictions are trained on 1 km labels (GPM mode), and TWI uses a
   ~90 m flow-accumulation grid.
8. **Licensing.** The Global Peatland Map 2.0 is **CC BY-NC-SA (non-commercial)**. For commercial
   work, train on national open data and check each licence before use.

### 7. Field boundaries from AlphaEarth Foundations embeddings

**Data.** `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL` (AlphaEarth Foundations v2.1, Brown et al. 2025,
arXiv:2507.22291, CC-BY 4.0) holds one 64-dimensional unit-length vector per 10 m pixel per year,
learned from Sentinel-2, Landsat, Sentinel-1, LiDAR, elevation, climate and other sources. Each
vector summarises the pixel's whole annual trajectory. Two adjacent fields that differ in crop,
sowing date or management therefore differ in embedding space even when they look alike on any
single image. The dot product between two vectors equals their cosine similarity.

**Method.**
1. *Edge map:* a Sobel gradient on all 64 axes, combined with the L2 norm (a multispectral
   gradient) and scaled so a step between vectors a and b equals the chord distance |a − b|
   (|a − b|² = 2 − 2 cos θ).
2. *Boundary threshold:* chosen with Otsu's method inside the agricultural extent; a slider lets
   you override it.
3. *Extent:* ESA WorldCover 2021 cropland + grassland.
4. *Fields:* 4-connected regions of (extent AND NOT boundary), vectorised, with small objects
   removed and a 10 m dilation to close the boundary band. This is the "cutoff" post-processing of
   Waldner & Diakogiannis (2020), with their CNN boundary probability replaced by a label-free
   edge map.
5. *Baseline:* SNIC superpixels run directly on the 64-D embeddings.
6. *Interactive check:* click a pixel to map the cosine similarity of every other pixel to it.

**Validation.**

| Metric | Definition | Reference |
|--------|-----------|-----------|
| IoU of best-matching segment, % matched (IoU ≥ 0.5) | Per reference field, for a random sample of 150 fields fully inside the AOI | Persello et al. 2019; Waldner & Diakogiannis 2020 |
| Over-segmentation OS, under-segmentation US, D | OS = 1 − \|r∩s\|/\|r\|; US = 1 − \|r∩s\|/\|s\|; D = √((OS² + US²)/2) | Clinton et al. 2010 |
| Boundary precision / recall / F1 | ±20 m tolerance, evaluated only where the reference has coverage | standard boundary-F measure |
| Parcel-point check (optional) | Share of segments with exactly 1, 0, or ≥ 2 RPA parcel centroids | RPA Parcel Points (OGL v3) |

Both methods are scored on the same reference sample, so the comparison is like-for-like.

**References available, and how much to trust them.**
- **UKFields** (`projects/sat-io/open-datasets/UK-FIELDS`, Bancroft & Wilkins 2024, CC-BY 4.0): UK
  fields segmented with SAM on 2021 Sentinel-2 composites and masked to Dynamic World cropland. It
  is an automated product, so scores against it are agreement, not truth.
- **USDA Crop Sequence Boundaries** (`projects/nass-csb/assets/CSB1825_rev23/CSBIA1825`): built
  from the 30 m CDL plus road and rail networks, so boundaries are coarse and adjacent fields with
  the same crop sequence are merged. AlphaEarth v2.1 also used the CDL as a training target, so
  US scores are likely **optimistic**.
- **RPA Land Parcels** (England, derived from OS MasterMap): the most reliable reference, with its
  own licence conditions on the Defra Data Services Platform. **RPA Parcel Points** (centroids) are
  OGL v3 and work with the parcel-point check. Upload either and set `REF_ASSET_OVERRIDE` /
  `POINTS_ASSET`.

**Challenges.**
1. **Resolution.** Hedges, ditches and tramlines are 2–5 m wide, narrower than a 10 m pixel, and
   show up only through mixed pixels. Fields under ~1 ha are poorly resolved.
2. **Same crop, no physical divide.** Neighbouring fields with the same crop and management have
   near-identical annual embeddings and merge. Fen fields separated only by drains are a typical
   case.
3. **Internal edges.** Within-field heterogeneity (wet hollows, soil changes, in-field trees,
   partial harvest) can create spurious boundaries that over-segment a field.
4. **Embedding artefacts.** The catalog notes residual swath and tiling artefacts; they can appear
   as long straight false edges.
5. **Interpretability.** The 64 axes are not physical quantities, and thresholds vary by site
   (hence Otsu plus a manual override).
6. **One year at a time.** Boundaries that change within a year, such as split or merged fields
   or rotational grazing, can't be resolved from a single annual embedding.
7. **Unsupervised baseline.** Supervised deep models trained on raw imagery (FracTAL-ResUNet,
   Fields of The World, Delineate Anything) generally achieve higher object accuracy. This
   workflow is a fast, global, label-free baseline and a feature source; the embeddings can
   also be fed to such models.

## Testing performed

- `node --check` syntax check on every script. Use case 6 was also run with each label mode (GPM, uploaded polygons, uploaded class field), with and without Sentinel-2, and with the probe validation enabled. Use case 7 was run for both sites, with and without the parcel-point check.
- Execution of every script in a mock Earth Engine runtime (Node `Proxy` objects standing in for
  `ee`, `ui`, `Map` and `Export`). Server-side `map`/`iterate` callbacks and client-side
  `evaluate`/`onClick`/`onChange` callbacks are invoked, which catches undefined variables, typos
  and JavaScript errors.
- Every dataset ID and band name was checked against the official
  [earthengine-catalog](https://github.com/google/earthengine-catalog) definitions:
  `GLOBAL_FLOOD_DB/MODIS_EVENTS/V1` (`flooded`, `clear_views`, `jrc_perm_water`, `id`),
  `JRC/GSW1_4/GlobalSurfaceWater` (`seasonality`), `MERIT/Hydro/v1_0_1` (`hnd`),
  `JRC/GHSL/P2023A/GHS_POP/2015` (`population_count`), `ESA/WorldCover/v100` (`Map`),
  `GOOGLE/DYNAMICWORLD/V1` (`built`), `USDA/NASS/CDL` (`cropland`, `confidence`),
  `UMD/hansen/global_forest_change_2024_v1_12`, `ECMWF/ERA5/HOURLY`, `USDOS/LSIB_SIMPLE/2017`,
  `UK/EA/ENGLAND_1M_TERRAIN/2022` (`dtm`), `COPERNICUS/DEM/GLO30` (`DEM`), `WORLDCLIM/V1/BIO`
  (`bio01`, `bio12`), `MERIT/Hydro/v1_0_1` (`upa`), and the community asset
  `projects/sat-io/open-datasets/GLOBAL-PEATLAND-DATABASE` (1 = peat dominated, 2 = peat in a soil mosaic),
  `GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL` (`A00`–`A63`), `ESA/WorldCover/v200` (`Map`), and the community assets
  `projects/sat-io/open-datasets/UK-FIELDS` and `projects/nass-csb/assets/CSB1825_rev23/CSBIA1825`.
- The PWTT implementation follows the author's reference code
  ([oballinger/PWTT](https://github.com/oballinger/PWTT)). The omnibus test is a line-by-line port
  of the Python code published with Canty et al. (2020).
- **Not yet done:** none of the scripts has been run in a live Earth Engine session, so the
  validation numbers in the panels have not been observed. Run them in the Code Editor first. If
  an interactive computation times out, reduce the AOI or use the queued Export task.

## References

- Achanta, R., Süsstrunk, S. (2017). Superpixels and polygons using simple non-iterative clustering. *CVPR 2017*.
- Bancroft, S., Wilkins, J. (2024). UKFields (1.0.0) [dataset]. Zenodo. doi:10.5281/zenodo.11110206.
- Brown, C.F., Kazmierski, M.R., Pasquarella, V.J., et al. (2025). AlphaEarth Foundations: an embedding field model for accurate and efficient global mapping from sparse label data. arXiv:2507.22291.
- Clinton, N., Holt, A., Scarborough, J., Yan, L., Gong, P. (2010). Accuracy assessment measures for object-based image segmentation goodness. *Photogramm. Eng. Remote Sens.* 76(3), 289–299.
- Persello, C., et al. (2019). Delineation of agricultural fields in smallholder farms from satellite images using fully convolutional networks and combinatorial grouping. *Remote Sensing of Environment* 231.
- Waldner, F., Diakogiannis, F.I. (2020). Deep learning on edge: extracting field boundaries from satellite images with a convolutional neural network. *Remote Sensing of Environment* 245.
- Aitkenhead, M.J. (2017). Mapping peat in Scotland with remote sensing and site characteristics. *European Journal of Soil Science* 68.
- Finlayson, A., et al. (2021). Estimating organic surface horizon depth for peat and peaty soils across a Scottish upland catchment using linear mixed models with topographic and geological covariates. *Soil Use and Management*.
- Gatis, N., et al. (2019). Mapping upland peat depth using airborne radiometric and lidar survey data. *Geoderma* 335.
- Greifswald Mire Centre (2022). Global Peatland Map 2.0. UNEP Global Peatlands Assessment.
- Karlson, M., Bastviken, D., et al. (2023). Multi-source mapping of peatland types using Sentinel-1, Sentinel-2 and terrain derivatives – a comparison between five high-latitude landscapes. *JGR Biogeosciences*.
- Lindsay, R. (1995). *Bogs: the ecology, classification and conservation of ombrotrophic mires*. Scottish Natural Heritage.
- Minasny, B., et al. (2019). Digital mapping of peatlands – a critical review. *Earth-Science Reviews* 196, 102870.
- Natural England (2025). England Peat Map (NERR149 final report and user guide).
- Toca, L., et al. (2023). Potential for peatland water table depth monitoring using Sentinel-1 SAR backscatter: case study of Forsinard Flows, Scotland, UK. *Remote Sensing* 15.
- Vollrath, A., Mullissa, A., Reiche, J. (2020). Angular-based radiometric slope correction for Sentinel-1 on Google Earth Engine. *Remote Sensing* 12(11), 1867.
- Ballinger, O. (2025). Open access battle damage detection via pixel-wise T-test on Sentinel-1 imagery. *Remote Sensing of Environment*. arXiv:2405.06323.
- Bazi, Y., Bruzzone, L., Melgani, F. (2005). An unsupervised approach based on the generalized Gaussian model to automatic change detection in multitemporal SAR images. *IEEE TGRS* 43(4).
- Belgiu, M., Drăguţ, L. (2016). Random forest in remote sensing: a review of applications and future directions. *ISPRS J. Photogramm.* 114.
- Brekke, C., Solberg, A.H.S. (2005). Oil spill detection by satellite remote sensing. *Remote Sensing of Environment* 95(1).
- Canty, M.J., Nielsen, A.A., Conradsen, K., Skriver, H. (2020). Statistical analysis of changes in Sentinel-1 time series on the Google Earth Engine. *Remote Sensing* 12(1), 46. doi:10.3390/rs12010046.
- Conradsen, K., Nielsen, A.A., Skriver, H. (2016). Determining the points of change in time series of polarimetric SAR data. *IEEE TGRS* 54(5).
- Donchyts, G., et al. (2016). A 30 m resolution surface water mask including estimation of positional and thematic differences using Landsat 8, SRTM and OpenStreetMap: a case study in the Murray-Darling Basin. *Remote Sensing* 8(5).
- Fingas, M., Brown, C. (2014). Review of oil spill remote sensing. *Marine Pollution Bulletin* 83(1).
- Hansen, M.C., et al. (2013). High-resolution global maps of 21st-century forest cover change. *Science* 342.
- Lee, J.-S., Grunes, M.R., de Grandi, G. (1999). Polarimetric SAR speckle filtering and its implication for classification. *IEEE TGRS* 37(5).
- Markert, K.N., et al. (2020). Comparing Sentinel-1 surface water mapping algorithms and radiometric terrain correction processing in Southeast Asia utilizing Google Earth Engine. *Remote Sensing* 12(15), 2469.
- Mullissa, A., et al. (2021). Sentinel-1 SAR backscatter analysis ready data preparation in Google Earth Engine. *Remote Sensing* 13(10), 1954.
- Olofsson, P., et al. (2014). Good practices for estimating area and assessing accuracy of land change. *Remote Sensing of Environment* 148.
- Pekel, J.-F., et al. (2016). High-resolution mapping of global surface water and its long-term changes. *Nature* 540.
- Roberts, D.R., et al. (2017). Cross-validation strategies for data with temporal, spatial, hierarchical, or phylogenetic structure. *Ecography* 40.
- Solberg, A.H.S., Brekke, C., Husøy, P.O. (2007). Oil spill detection in Radarsat and Envisat SAR images. *IEEE TGRS* 45(3).
- Tellman, B., et al. (2021). Satellite imaging reveals increased proportion of population exposed to floods. *Nature* 596.
- Topouzelis, K. (2008). Oil spill detection by SAR images: dark formation detection, feature extraction and classification algorithms. *Sensors* 8(10).
- Twele, A., Cao, W., Plank, S., Martinis, S. (2016). Sentinel-1-based flood mapping: a fully automated processing chain. *Int. J. Remote Sensing* 37(13).
- Veloso, A., et al. (2017). Understanding the temporal behavior of crops using Sentinel-1 and Sentinel-2-like data for agricultural applications. *Remote Sensing of Environment* 199.
- Yamazaki, D., et al. (2019). MERIT Hydro: a high-resolution global hydrography map based on latest topography datasets. *Water Resources Research* 55.
