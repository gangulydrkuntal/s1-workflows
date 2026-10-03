# Sentinel-1 use cases for the Google Earth Engine Code Editor

Five self-contained JavaScript workflows for the [GEE Code Editor](https://code.earthengine.google.com/).
Each one implements a published, widely cited Sentinel-1 method. Each runs on a test site that has
independent reference data, and each builds its own UI: a layer list, a legend, statistics, charts
and click-to-inspect time series.

| # | Script | Application | Method (key reference) | Test site | Reference / validation |
|---|--------|-------------|------------------------|-----------|------------------------|
| 1 | `01_flood_mapping_bihar2017.js` | Flood inundation | Log-ratio change detection + edge-based Otsu (Donchyts et al. 2016; Markert et al. 2020; Twele et al. 2016) | North Bihar, India, Aug 2017 (DFO 4507) | **Global Flood Database** (Tellman et al. 2021, *Nature*), in GEE: confusion matrix, OA, kappa, F1, IoU |
| 2 | `02_building_damage_pwtt_turkey2023.js` | Building damage | Pixel-Wise T-Test, PWTT (Ballinger 2025, *RSE*) | Antakya, Türkiye, earthquake 6 Feb 2023 | **Negative control city** (Mersin) gives an empirical false-positive rate. Optional UNOSAT / Copernicus EMSR648 points give a ROC curve and AUC |
| 3 | `03_oil_spill_baniyas2021.js` | Marine oil spill | Adaptive dark-spot detection + object features (Solberg et al. 2007; Brekke & Solberg 2005; Topouzelis 2008) | Baniyas, Syria, 23 Aug 2021 onwards | **Pre-spill negative control** (false-alarm area), ERA5 wind check, area vs time compared with the reported slick (imaged 24–25 Aug; ~800 km² by ~31 Aug). Optional reference polygon gives IoU |
| 4 | `04_omnibus_change_detection_deforestation.js` | Multi-temporal change detection | Sequential omnibus likelihood-ratio test (Conradsen et al. 2016, *IEEE TGRS*; Canty et al. 2020, *Remote Sens.*) | Jaci-Paraná Extractive Reserve, Rondônia, Brazil, 2020–2021 | **Hansen Global Forest Change v1.12**: stratum-weighted accuracy (Olofsson et al. 2014) and year-of-change agreement |
| 5 | `05_crop_classification_rf_cdl.js` | Crop-type mapping (ML) | Random Forest on dense S1 time series (Veloso et al. 2017; Belgiu & Drăguţ 2016) | Red River Valley, ND/MN, USA, 2021 | **USDA Cropland Data Layer 2021**, with a spatially blocked train/test split: OA, kappa, PA/UA/F1, confusion matrix |

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

## Testing performed

- `node --check` syntax check on every script.
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
  `UMD/hansen/global_forest_change_2024_v1_12`, `ECMWF/ERA5/HOURLY`, `USDOS/LSIB_SIMPLE/2017`.
- The PWTT implementation follows the author's reference code
  ([oballinger/PWTT](https://github.com/oballinger/PWTT)). The omnibus test is a line-by-line port
  of the Python code published with Canty et al. (2020).
- **Not yet done:** none of the scripts has been run in a live Earth Engine session, so the
  validation numbers in the panels have not been observed. Run them in the Code Editor first. If
  an interactive computation times out, reduce the AOI or use the queued Export task.

## References

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
