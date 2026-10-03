/*******************************************************************************
 * USE CASE 6 - PEATLAND (PEAT SOIL) EXTENT MAPPING IN THE UK WITH SENTINEL-1,
 *              TERRAIN (SLOPE, TPI, TWI), CLIMATE AND OPTIONAL SENTINEL-2
 *
 * Test site  : Peak District, England. Contains a sharp, well-documented
 *              natural contrast: the Dark Peak (Millstone Grit) carries
 *              extensive deep blanket peat (Kinder Scout, Bleaklow, Black Hill),
 *              whereas the White Peak (Carboniferous limestone plateau) has
 *              mineral soils and no peat. It also contains the classic false
 *              positives reported for the 2025 England Peat Map: reservoirs,
 *              limestone, gritstone edges/quarries and topographic shadow.
 *
 * IMPORTANT - WHAT IS (AND IS NOT) MEASURED
 *   Sentinel-1 C-band (5.6 cm) penetrates only a few cm into wet vegetation /
 *   soil. It cannot "see" peat depth. Peat is a SOIL property; it is mapped
 *   here as in digital soil mapping (Minasny et al., 2019, Earth-Sci. Rev.
 *   196:102870) from proxies that control or reflect peat formation:
 *     - surface wetness and vegetation structure and their seasonal dynamics
 *       (Sentinel-1 backscatter statistics; Toca et al., 2023, Forsinard;
 *       Asmuss et al., 2019; Bechtold et al., 2018),
 *     - topography: peat accumulates on gentle slopes, plateaux, hollows and
 *       water-receiving positions (slope, TPI, TWI; Parry et al., 2012;
 *       Gatis et al., 2019, Geoderma; Finlayson et al., 2021),
 *     - climate: UK blanket bog requires ~>1000-1200 mm/yr rainfall and cool
 *       temperatures (Lindsay, 1995),
 *     - optionally Sentinel-2 vegetation/moisture indices (Karlson et al.,
 *       2023, JGR-Biogeosciences: S1 + S2 + terrain fusion 80-90 % OA).
 *   The same family of predictors (S1, S2, LiDAR slope, climate, geology,
 *   random forest) underlies Natural England's England Peat Map (2025) and
 *   the James Hutton Institute's Scottish peat maps (Aitkenhead, 2017/2020).
 *
 * Method
 *   1. Sentinel-1 GRD (linear, COPERNICUS/S1_GRD_FLOAT), IW, VV+VH, one
 *      hydrological year (Oct 2020 - Sep 2021, S1A + S1B), both passes.
 *      Angular-based radiometric terrain flattening (volume model) and
 *      layover/shadow masking (Vollrath, Mullissa & Reiche, 2020, Remote
 *      Sens. 12:1867) - essential in upland terrain.
 *      Temporal statistics per polarisation (dB): median, 10th/90th
 *      percentile, standard deviation, winter (DJF) and summer (JJA) medians,
 *      summer-winter difference; VH-VV cross-ratio. Speckle is suppressed by
 *      temporal aggregation (~150-250 images) and 30 m averaging.
 *   2. Terrain at 30 m (British National Grid): Environment Agency 1 m LiDAR
 *      DTM (England) aggregated to 30 m, Copernicus GLO-30 fallback; slope,
 *      TPI at 300 m and 1000 m, TWI = ln(a / tan(beta)) with MERIT-Hydro
 *      upstream area.
 *   3. Climate: WorldClim annual precipitation and mean temperature.
 *   4. Labels: (A) default, runs with no upload: interior of "peat dominated"
 *      1 km cells of the Global Peatland Map 2.0 (Greifswald Mire Centre /
 *      UNEP, in the GEE community catalog) vs. areas > 2 km from any mapped
 *      peat/organic soil; (B) recommended: national reference uploaded as an
 *      asset (England Peat Map 2025 extent, Unified Peat Map of Wales,
 *      Scotland Carbon & Peatland 2016).
 *      Water (JRC GSW) and built-up (ESA WorldCover) are excluded from training
 *      AND prediction - they are reported as "not assessed".
 *   5. Random Forest (300 trees), probability output.
 *
 * Validation (designed to be reliable and to expose weaknesses)
 *   a) Spatially blocked hold-out (checkerboard of ~3 x 4 km blocks) vs. the
 *      label source; the optimistic random-split accuracy is shown beside it
 *      to quantify spatial-autocorrelation inflation (Roberts et al., 2017;
 *      Ploton et al., 2020).
 *   b) ROC curve / AUC of the peat probability.
 *   c) Feature-group ablation: terrain+climate only, S1 only, S1+terrain+
 *      climate, and +S2 -> the real added value of Sentinel-1.
 *   d) Known-site stress tests: % predicted peat on Kinder Scout and Bleaklow
 *      plateaux (deep blanket peat, expected ~100 %) and on the White Peak
 *      limestone plateau (mineral soils, expected ~0 %).
 *   e) Optional, the only truly independent test: field peat-depth probe
 *      points (e.g. England Peat Map survey data, Peatland ACTION / Moors for
 *      the Future surveys) with a depth threshold (England "deep peat" >= 40 cm).
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([-2.05, 53.18, -1.65, 53.55]);   // Peak District
var CRS = 'EPSG:27700';                 // British National Grid
var SCALE = 30;                         // analysis resolution (m)
var S1_START = '2020-10-01', S1_END = '2021-10-01';   // hydrological year, S1A+S1B
var USE_S2 = true;
var N_PER_CLASS = 1500;
var BLOCK_DEG = 0.04;                   // spatial block size for hold-out
var N_TREES = 300;

// Reference labels: 'GPM' (default, no upload) or 'ASSET'
var REFERENCE_MODE = 'GPM';
var REF_ASSET = null;          // e.g. 'users/<you>/England_Peat_Map_extent'
var REF_PROPERTY = null;       // null = every polygon is peat; else numeric class field
var REF_PEAT_VALUES = [];      // e.g. Scotland C&P 2016: [1, 2, 5]
var REF_NONPEAT_VALUES = [];   // e.g. Scotland C&P 2016: [4]  (class 3 left out = ambiguous)

// Optional independent field data (points with peat depth in cm)
var PROBE_ASSET = null;        // e.g. 'users/<you>/peat_depth_probes'
var DEPTH_PROPERTY = 'depth_cm';
var PEAT_DEPTH_CM = 40;        // England deep-peat threshold (Scotland uses 50 cm)

// Known sites for stress tests
var KNOWN_SITES = ee.FeatureCollection([
  ee.Feature(ee.Geometry.Rectangle([-1.895, 53.375, -1.835, 53.400]),
    {name: 'Kinder Scout plateau', expected: 'peat'}),
  ee.Feature(ee.Geometry.Rectangle([-1.880, 53.450, -1.800, 53.475]),
    {name: 'Bleaklow plateau', expected: 'peat'}),
  ee.Feature(ee.Geometry.Rectangle([-1.820, 53.180, -1.720, 53.240]),
    {name: 'White Peak limestone plateau', expected: 'non-peat'})
]);

Map.centerObject(AOI, 10);
Map.setOptions('TERRAIN');
var proj = ee.Projection(CRS).atScale(SCALE);

// =============================================================================
// 1. MASKS: water and built-up are excluded (known false positives)
// =============================================================================
var worldCover = ee.ImageCollection('ESA/WorldCover/v200').first().select('Map');
var water = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').unmask(0).gte(50)
  .or(worldCover.eq(80));
var built = worldCover.eq(50);
var valid = water.not().and(built.not()).rename('valid');

// =============================================================================
// 2. TERRAIN AND CLIMATE COVARIATES
// =============================================================================
var glo30 = ee.ImageCollection('COPERNICUS/DEM/GLO30').filterBounds(AOI).select('DEM').mosaic()
  .setDefaultProjection({crs: 'EPSG:4326', scale: 30});
var lidar = ee.Image('UK/EA/ENGLAND_1M_TERRAIN/2022').select('dtm');
var dem = lidar.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 1024}).reproject(proj)
  .unmask(glo30.resample('bilinear').reproject(proj)).rename('elev');

var slope = ee.Terrain.slope(dem).rename('slope');
var tpi300 = dem.subtract(dem.focalMean(300, 'circle', 'meters')).rename('tpi300');
var tpi1000 = dem.subtract(dem.focalMean(1000, 'circle', 'meters')).rename('tpi1000');
var upa = ee.Image('MERIT/Hydro/v1_0_1').select('upa');          // upstream area, km2 (~90 m)
var sca = upa.multiply(1e6).divide(90);                           // specific catchment area (m)
var tanB = slope.multiply(Math.PI / 180).tan().max(0.001);
var twi = sca.divide(tanB).log().rename('twi');
var bio = ee.Image('WORLDCLIM/V1/BIO');
var precip = bio.select('bio12').rename('precip');                // mm / yr
var tmean = bio.select('bio01').multiply(0.1).rename('tmean');    // deg C

var TC_BANDS = ['elev', 'slope', 'tpi300', 'tpi1000', 'twi', 'precip', 'tmean'];
var terrainClimate = ee.Image.cat(dem, slope, tpi300, tpi1000, twi, precip, tmean);

// =============================================================================
// 3. SENTINEL-1: TERRAIN FLATTENING (Vollrath et al., 2020) + TEMPORAL FEATURES
// =============================================================================
var HALF_PI = Math.PI / 2;
var demFlat = glo30.resample('bilinear');   // same DEM family as the GRD terrain correction

function terrainFlatten(image) {
  var elev = demFlat.reproject(proj);
  var heading = ee.Number(ee.Terrain.aspect(image.select('angle'))
    .reduceRegion({reducer: ee.Reducer.mean(), geometry: AOI, scale: 1000}).get('aspect'));
  heading = ee.Number(ee.Algorithms.If(heading.gt(180), heading.subtract(360), heading));
  var thetaI = image.select('angle').multiply(Math.PI / 180);
  var phiI = ee.Image.constant(heading).multiply(Math.PI / 180);
  var alphaS = ee.Terrain.slope(elev).multiply(Math.PI / 180);
  var aspect = ee.Terrain.aspect(elev);
  var phiS = aspect.where(aspect.gt(180), aspect.subtract(360)).multiply(-Math.PI / 180);
  var phiR = phiI.subtract(phiS);
  var alphaR = alphaS.tan().multiply(phiR.cos()).atan();          // range-direction slope
  var gamma0 = image.select(['VV', 'VH']).divide(thetaI.cos());
  // Volume scattering model (appropriate for vegetated / moorland surfaces)
  var scf = ee.Image(HALF_PI).subtract(thetaI).add(alphaR).tan()
    .divide(ee.Image(HALF_PI).subtract(thetaI).tan());
  var layover = alphaR.lt(thetaI);
  var shadow = alphaR.gt(ee.Image(HALF_PI).subtract(thetaI).multiply(-1));
  var angleOk = image.select('angle').gt(30.5).and(image.select('angle').lt(45.5));
  return ee.Image(gamma0.multiply(scf).updateMask(layover.and(shadow)).updateMask(angleOk)
    .copyProperties(image, ['system:time_start', 'orbitProperties_pass']));
}

var s1Raw = ee.ImageCollection('COPERNICUS/S1_GRD_FLOAT')
  .filterBounds(AOI)
  .filterDate(S1_START, S1_END)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'));
var s1Db = s1Raw.map(terrainFlatten).map(function (img) {
  return ee.Image(img.log10().multiply(10).copyProperties(img, ['system:time_start']));
});

var s1Median = s1Db.median().rename(['VV_med', 'VH_med']);
var s1Pct = s1Db.reduce(ee.Reducer.percentile([10, 90]))
  .rename(['VV_p10', 'VV_p90', 'VH_p10', 'VH_p90']);
var s1Sd = s1Db.reduce(ee.Reducer.stdDev()).rename(['VV_sd', 'VH_sd']);
var s1Winter = s1Db.filter(ee.Filter.calendarRange(12, 2, 'month')).median().rename(['VV_win', 'VH_win']);
var s1Summer = s1Db.filter(ee.Filter.calendarRange(6, 8, 'month')).median().rename(['VV_sum', 'VH_sum']);
var s1Season = s1Summer.subtract(s1Winter).rename(['VV_season', 'VH_season']);
var s1Ratio = s1Median.select('VH_med').subtract(s1Median.select('VV_med')).rename('VHVV_ratio');
var s1Features = ee.Image.cat(s1Median, s1Pct, s1Sd, s1Winter, s1Summer, s1Season, s1Ratio);
var S1_BANDS = ['VV_med', 'VH_med', 'VV_p10', 'VV_p90', 'VH_p10', 'VH_p90', 'VV_sd', 'VH_sd',
  'VV_win', 'VH_win', 'VV_sum', 'VH_sum', 'VV_season', 'VH_season', 'VHVV_ratio'];

// =============================================================================
// 4. OPTIONAL SENTINEL-2 (growing-season medians, Cloud Score+ masking)
// =============================================================================
var S2_BANDS = ['NDVI', 'NDMI', 'NDWI'];
var s2Features = ee.Image.cat(ee.Image(0), ee.Image(0), ee.Image(0)).rename(S2_BANDS).updateMask(0);
if (USE_S2) {
  var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
    .filterBounds(AOI)
    .filterDate('2020-01-01', '2022-12-31')
    .filter(ee.Filter.calendarRange(5, 9, 'month'))
    .linkCollection(ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'), ['cs_cdf'])
    .map(function (img) {
      var m = img.updateMask(img.select('cs_cdf').gte(0.6));
      return ee.Image.cat(
        m.normalizedDifference(['B8', 'B4']).rename('NDVI'),
        m.normalizedDifference(['B8', 'B11']).rename('NDMI'),
        m.normalizedDifference(['B3', 'B8']).rename('NDWI'));
    });
  s2Features = s2.median();
}

var ALL_BANDS = USE_S2 ? TC_BANDS.concat(S1_BANDS).concat(S2_BANDS) : TC_BANDS.concat(S1_BANDS);
var features = ee.Image.cat(terrainClimate, s1Features, s2Features).select(ALL_BANDS).clip(AOI);

// =============================================================================
// 5. REFERENCE LABELS
// =============================================================================
var gpm = ee.Image('projects/sat-io/open-datasets/GLOBAL-PEATLAND-DATABASE').select(0).unmask(0);
var gpmPeatDominated = gpm.eq(1);
var gpmAnyPeat = gpm.gt(0);

var label;
if (REFERENCE_MODE === 'GPM') {
  var peatLab = gpmPeatDominated.focalMin(1000, 'circle', 'meters');      // interior only
  var nonLab = gpmAnyPeat.focalMax(2000, 'circle', 'meters').not();       // far from any peat
  label = ee.Image(0).where(peatLab, 1).updateMask(peatLab.or(nonLab));
} else {
  var refFc = ee.FeatureCollection(REF_ASSET).filterBounds(AOI);
  if (REF_PROPERTY === null) {
    var inside = ee.Image(0).byte().paint(refFc, 1);
    var peatA = inside.focalMin(60, 'circle', 'meters');                  // avoid boundary pixels
    var nonA = inside.focalMax(150, 'circle', 'meters').not();
    label = ee.Image(0).where(peatA, 1).updateMask(peatA.or(nonA));
  } else {
    var cls = ee.Image(0).paint(refFc, REF_PROPERTY);
    var isPeat = cls.remap(REF_PEAT_VALUES, ee.List.repeat(1, REF_PEAT_VALUES.length), 0);
    var isNon = cls.remap(REF_NONPEAT_VALUES, ee.List.repeat(1, REF_NONPEAT_VALUES.length), 0);
    label = ee.Image(0).where(isPeat, 1).updateMask(isPeat.or(isNon));
  }
}
label = label.rename('peat').toByte();

// =============================================================================
// 6. SAMPLING WITH SPATIAL BLOCKS
// =============================================================================
var lonlat = ee.Image.pixelLonLat();
var fold = lonlat.select('longitude').divide(BLOCK_DEG).floor()
  .add(lonlat.select('latitude').divide(BLOCK_DEG).floor()).mod(2).abs().rename('fold');

var samples = features.addBands(label).addBands(fold).updateMask(valid)
  .stratifiedSample({numPoints: N_PER_CLASS, classBand: 'peat', region: AOI, scale: SCALE,
    projection: proj, seed: 21, tileScale: 8, geometries: true})
  .filter(ee.Filter.notNull(ALL_BANDS))
  .randomColumn('rnd', 5);

var trainB = samples.filter(ee.Filter.eq('fold', 0)), testB = samples.filter(ee.Filter.eq('fold', 1));
var trainR = samples.filter(ee.Filter.lt('rnd', 0.5)), testR = samples.filter(ee.Filter.gte('rnd', 0.5));

function rf() {
  return ee.Classifier.smileRandomForest({numberOfTrees: N_TREES, minLeafPopulation: 2, seed: 7});
}
function trainEval(bands, train, test) {
  var clf = rf().train({features: train, classProperty: 'peat', inputProperties: bands});
  var cm = test.classify(clf, 'pred').errorMatrix('peat', 'pred', [0, 1]);
  var pa = ee.Number(cm.producersAccuracy().get([1, 0]));
  var ua = ee.Number(cm.consumersAccuracy().get([0, 1]));
  return ee.Dictionary({OA: cm.accuracy(), kappa: cm.kappa(), PA_peat: pa, UA_peat: ua,
    F1_peat: pa.multiply(ua).multiply(2).divide(pa.add(ua).max(1e-9)), matrix: cm.array()});
}

// Feature-group ablation (all on the same spatial blocks)
var groups = {'Terrain + climate': TC_BANDS, 'Sentinel-1 only': S1_BANDS,
  'S1 + terrain + climate': TC_BANDS.concat(S1_BANDS)};
if (USE_S2) { groups['S1 + S2 + terrain + climate'] = ALL_BANDS; }
var ablation = ee.Dictionary({});
Object.keys(groups).forEach(function (g) { ablation = ablation.set(g, trainEval(groups[g], trainB, testB)); });
var randomSplit = trainEval(ALL_BANDS, trainR, testR);

// Final model: probability output, trained on ALL samples for mapping
var probClfTest = rf().setOutputMode('PROBABILITY')
  .train({features: trainB, classProperty: 'peat', inputProperties: ALL_BANDS});
var probClf = rf().setOutputMode('PROBABILITY')
  .train({features: samples, classProperty: 'peat', inputProperties: ALL_BANDS});
var prob = features.classify(probClf).rename('peat_prob').updateMask(valid);
var peatMap = prob.gte(0.5).rename('peat_pred');
var importance = ee.Dictionary(rf().train({features: samples, classProperty: 'peat',
  inputProperties: ALL_BANDS}).explain().get('importance'));

// ROC on spatial hold-out
var testProb = testB.classify(probClfTest, 'prob');
var nPos = testProb.filter(ee.Filter.eq('peat', 1)).size();
var nNeg = testProb.filter(ee.Filter.eq('peat', 0)).size();
var roc = ee.FeatureCollection(ee.List.sequence(0, 1, 0.05).map(function (th) {
  return ee.Feature(null, {
    threshold: th,
    TPR: testProb.filter(ee.Filter.eq('peat', 1)).filter(ee.Filter.gte('prob', th)).size().divide(nPos),
    FPR: testProb.filter(ee.Filter.eq('peat', 0)).filter(ee.Filter.gte('prob', th)).size().divide(nNeg)
  });
}));

// Known-site stress tests
var siteStats = KNOWN_SITES.map(function (f) {
  var s = ee.Image.cat(peatMap.toFloat(), prob, valid.toFloat().rename('validFrac'))
    .reduceRegion({reducer: ee.Reducer.mean(), geometry: f.geometry(), crs: proj, scale: SCALE, maxPixels: 1e9, tileScale: 4});
  return f.set({peat_pct: ee.Number(s.get('peat_pred')).multiply(100),
    mean_prob: s.get('peat_prob'), assessed_pct: ee.Number(s.get('validFrac')).multiply(100)});
});

// =============================================================================
// 7. MAP LAYERS
// =============================================================================
var probVis = {min: 0, max: 1, palette: ['f7f4ea', 'd9c9a0', 'a68a4b', '6b4f2a', '2b1d0e']};
Map.addLayer(slope.clip(AOI), {min: 0, max: 30, palette: ['ffffff', '444444']}, 'Slope (deg, LiDAR/GLO-30)', false);
Map.addLayer(twi.clip(AOI), {min: 4, max: 14, palette: ['ffffcc', '41b6c4', '081d58']}, 'Topographic wetness index', false);
Map.addLayer(s1Features.select(['VV_win', 'VH_win', 'VV_sd']).clip(AOI),
  {min: [-14, -21, 0.5], max: [-4, -11, 3]}, 'S1 RGB: VV winter, VH winter, VV temporal SD', false);
Map.addLayer(gpm.updateMask(gpm.gt(0)).clip(AOI), {min: 1, max: 2, palette: ['5b3a1a', 'c49a6c']},
  'Global Peatland Map 2.0 (1 km; 1 = peat dominated, 2 = mosaic)', false);
Map.addLayer(label.clip(AOI), {min: 0, max: 1, palette: ['3a87c8', '6b4f2a']}, 'Training label zones (brown peat, blue non-peat)', false);
Map.addLayer(fold.clip(AOI), {min: 0, max: 1, palette: ['1f78b4', 'e31a1c'], opacity: 0.3}, 'Spatial blocks (blue train / red test)', false);
Map.addLayer(prob, probVis, 'Peat probability (RF)', true);
Map.addLayer(peatMap.selfMask(), {palette: ['4a2c0f']}, 'Predicted peat (p >= 0.5)', false);
Map.addLayer(prob.gt(0.35).and(prob.lt(0.65)).selfMask(), {palette: ['ff00ff']}, 'Uncertain (0.35 < p < 0.65)', false);
Map.addLayer(water.or(built).selfMask().clip(AOI), {palette: ['9e9e9e']}, 'Not assessed (water / built-up)', true);
Map.addLayer(ee.Image().byte().paint(KNOWN_SITES, 1, 2), {palette: ['ffff00']}, 'Stress-test sites');
Map.addLayer(samples.style({color: '000000', pointSize: 1}), {}, 'Samples', false);

// =============================================================================
// 8. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '450px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('UK Peatland Mapping: Sentinel-1 + terrain', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Peak District | RF on terrain-flattened S1 statistics, LiDAR slope/TPI/TWI, climate' +
  (USE_S2 ? ', S2' : ''), {fontSize: '12px', color: '#555'}));
panel.add(ui.Label('Labels: ' + (REFERENCE_MODE === 'GPM' ?
  'Global Peatland Map 2.0 (1 km) - demonstration mode; accuracies = agreement with GPM, not truth.' :
  'uploaded national reference: ' + REF_ASSET), {fontSize: '11px', color: '#a00'}));

var bar = ui.Thumbnail({image: ee.Image.pixelLonLat().select(0),
  params: {bbox: [0, 0, 1, 0.1], dimensions: '300x12', format: 'png', min: 0, max: 1, palette: probVis.palette},
  style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '20px'}});
panel.add(ui.Label('Peat probability', {fontWeight: 'bold'}));
panel.add(bar);
panel.add(ui.Panel([ui.Label('0 (mineral)', {margin: '2px 8px', fontSize: '11px'}),
  ui.Label('1 (peat)', {margin: '2px 8px', fontSize: '11px', textAlign: 'right', stretch: 'horizontal'})],
  ui.Panel.Layout.flow('horizontal')));
function legendRow(color, text) {
  return ui.Panel([ui.Label('', {backgroundColor: '#' + color, padding: '8px', margin: '2px 6px 2px 8px'}),
    ui.Label(text, {fontSize: '12px', margin: '2px 0'})], ui.Panel.Layout.flow('horizontal'));
}
panel.add(legendRow('9e9e9e', 'Not assessed: water (JRC GSW) / built-up (WorldCover)'));
panel.add(legendRow('ff00ff', 'Uncertain prediction (0.35-0.65)'));
panel.add(legendRow('ffff00', 'Stress-test sites (Kinder, Bleaklow, White Peak)'));

var valLabel = ui.Label('Training models and validating...', {whiteSpace: 'pre', fontSize: '12px', fontFamily: 'monospace'});
panel.add(ui.Label('Validation', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(valLabel);
var chartsPanel = ui.Panel();
panel.add(chartsPanel);

ee.Dictionary({abl: ablation, rnd: randomSplit, nTrain: trainB.size(), nTest: testB.size(),
  roc: roc.sort('FPR').reduceColumns(ee.Reducer.toList(2), ['FPR', 'TPR']).get('list'),
  sites: siteStats.reduceColumns(ee.Reducer.toList(5), ['name', 'expected', 'peat_pct', 'mean_prob', 'assessed_pct']).get('list'),
  nS1: s1Raw.size()
}).evaluate(function (r, err) {
  if (err) { valLabel.setValue('Error: ' + err); return; }
  var pts = r.roc.concat([[1, 1]]); pts.unshift([0, 0]);
  var auc = 0;
  for (var i = 1; i < pts.length; i++) { auc += (pts[i][0] - pts[i - 1][0]) * (pts[i][1] + pts[i - 1][1]) / 2; }
  var full = r.abl[Object.keys(groups)[Object.keys(groups).length - 1]];
  var t = 'S1 images used: ' + r.nS1 + ' | samples train/test: ' + r.nTrain + '/' + r.nTest +
    '\n\nSPATIAL-BLOCK HOLD-OUT (full model)' +
    '\n OA ' + (100 * full.OA).toFixed(1) + ' %  kappa ' + full.kappa.toFixed(3) + '  AUC ' + auc.toFixed(3) +
    '\n Peat: PA ' + (100 * full.PA_peat).toFixed(1) + ' %  UA ' + (100 * full.UA_peat).toFixed(1) +
    ' %  F1 ' + full.F1_peat.toFixed(3) +
    '\nRandom split (optimistic): OA ' + (100 * r.rnd.OA).toFixed(1) + ' %  kappa ' + r.rnd.kappa.toFixed(3) +
    '\n -> inflation from spatial autocorrelation: ' + (100 * (r.rnd.OA - full.OA)).toFixed(1) + ' pp' +
    '\n\nABLATION (spatial blocks)       OA%   kappa   F1';
  Object.keys(groups).forEach(function (g) {
    var m = r.abl[g];
    t += '\n ' + (g + '                              ').slice(0, 29) + ('   ' + (100 * m.OA).toFixed(1)).slice(-5) +
      '  ' + m.kappa.toFixed(3) + '  ' + m.F1_peat.toFixed(3);
  });
  t += '\n\nSTRESS TESTS (known sites)        peat%  p_mean  assessed%';
  r.sites.forEach(function (s) {
    var ok = (s[1] === 'peat') ? (s[2] >= 70 ? 'PASS' : 'FAIL') : (s[2] <= 10 ? 'PASS' : 'FAIL');
    t += '\n ' + (s[0] + ' (' + s[1] + ')                     ').slice(0, 33) +
      ('    ' + s[2].toFixed(0)).slice(-5) + '  ' + s[3].toFixed(2) + '  ' + ('    ' + s[4].toFixed(0)).slice(-5) + '  ' + ok;
  });
  valLabel.setValue(t);

  var ablFc = ee.FeatureCollection(Object.keys(groups).map(function (g) {
    return ee.Feature(null, {group: g, OA: r.abl[g].OA, kappa: r.abl[g].kappa, F1: r.abl[g].F1_peat});
  }));
  chartsPanel.add(ui.Chart.feature.byFeature(ablFc, 'group', ['OA', 'kappa', 'F1']).setChartType('ColumnChart')
    .setOptions({title: 'Added value of each predictor group (spatial-block hold-out)',
      vAxis: {viewWindow: {min: 0, max: 1}}, colors: ['#6b4f2a', '#c49a6c', '#2c7fb8'], hAxis: {slantedText: true}}));
  chartsPanel.add(ui.Chart.feature.byFeature(roc.sort('FPR'), 'FPR', ['TPR']).setChartType('LineChart')
    .setOptions({title: 'ROC, spatial hold-out (AUC = ' + auc.toFixed(3) + ')',
      hAxis: {title: 'False positive rate', viewWindow: {min: 0, max: 1}},
      vAxis: {title: 'True positive rate', viewWindow: {min: 0, max: 1}}, legend: {position: 'none'},
      pointSize: 3, colors: ['#6b4f2a']}));
});

// Variable importance
var impFc = ee.FeatureCollection(importance.keys().map(function (k) {
  return ee.Feature(null, {feature: k, importance: importance.get(k)});
})).sort('importance', false).limit(15);
panel.add(ui.Chart.feature.byFeature(impFc, 'feature', ['importance']).setChartType('BarChart')
  .setOptions({title: 'Random Forest variable importance (top 15)', legend: {position: 'none'}, colors: ['#6b4f2a']}));

// Class-conditional distributions of slope and S1 winter VV (training samples)
function distChart(prop, min, max, nBins, title, unit) {
  var width = (max - min) / nBins;
  var hist = function (cls) {
    return ee.Array(samples.filter(ee.Filter.eq('peat', cls))
      .reduceColumns(ee.Reducer.fixedHistogram(min, max, nBins), [prop]).get('histogram'));
  };
  var hp = hist(1), hn = hist(0);
  var sp = hp.slice(1, 1, 2).reduce(ee.Reducer.sum(), [0]).get([0, 0]);
  var sn = hn.slice(1, 1, 2).reduce(ee.Reducer.sum(), [0]).get([0, 0]);
  var fc = ee.FeatureCollection(ee.List.sequence(0, nBins - 1).map(function (i) {
    i = ee.Number(i);
    return ee.Feature(null, {bin: ee.Number(min).add(i.add(0.5).multiply(width)),
      peat: ee.Number(hp.get([i, 1])).divide(sp), nonpeat: ee.Number(hn.get([i, 1])).divide(sn)});
  }));
  return ui.Chart.feature.byFeature(fc, 'bin', ['peat', 'nonpeat']).setChartType('AreaChart')
    .setOptions({title: title, hAxis: {title: unit}, vAxis: {title: 'relative frequency'},
      colors: ['#6b4f2a', '#3a87c8'], series: {0: {labelInLegend: 'peat labels'}, 1: {labelInLegend: 'non-peat labels'}}});
}
panel.add(distChart('slope', 0, 40, 20, 'Slope distribution by label class', 'slope (deg)'));
panel.add(distChart('VV_win', -22, -2, 20, 'Winter S1 VV (terrain-flattened) by label class', 'gamma0 VV (dB)'));
panel.add(distChart('twi', 2, 16, 20, 'Topographic wetness index by label class', 'TWI'));

// Area statistics on demand
var areaLabel = ui.Label('', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Button({label: 'Compute peat area statistics for the AOI', onClick: function () {
  areaLabel.setValue('Computing (30 m)...');
  var px = ee.Image.pixelArea().divide(1e6);
  ee.Dictionary(ee.Image.cat(px.updateMask(peatMap).rename('pred'),
      px.updateMask(gpmPeatDominated).rename('gpm1'), px.updateMask(valid).rename('assessed'))
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: AOI, crs: proj, scale: SCALE, maxPixels: 1e10, tileScale: 16}))
    .evaluate(function (d, err) {
      if (err) { areaLabel.setValue('Error: ' + err + ' (use the Export task)'); return; }
      areaLabel.setValue('Assessed area: ' + d.assessed.toFixed(0) + ' km2' +
        '\nPredicted peat (p >= 0.5): ' + d.pred.toFixed(1) + ' km2' +
        '\nGPM 2.0 peat-dominated cells: ' + d.gpm1.toFixed(1) + ' km2');
    });
}}));
panel.add(areaLabel);

// Optional independent validation with field probes
if (PROBE_ASSET !== null) {
  var probeLabel = ui.Label('Validating against field peat-depth probes...', {whiteSpace: 'pre', fontSize: '12px'});
  panel.add(ui.Label('Independent field validation', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
  panel.add(probeLabel);
  var probes = ee.FeatureCollection(PROBE_ASSET).filterBounds(AOI).map(function (f) {
    return f.set('obs_peat', ee.Number(f.get(DEPTH_PROPERTY)).gte(PEAT_DEPTH_CM));
  });
  var probeS = prob.unmask(-1).rename('prob').sampleRegions({collection: probes,
    properties: [DEPTH_PROPERTY, 'obs_peat'], scale: SCALE, projection: proj, tileScale: 4})
    .filter(ee.Filter.gte('prob', 0))
    .map(function (f) { return f.set('pred_peat', ee.Number(f.get('prob')).gte(0.5)); });
  var pcm = probeS.errorMatrix('obs_peat', 'pred_peat', [0, 1]);
  ee.Dictionary({n: probeS.size(), oa: pcm.accuracy(), kappa: pcm.kappa(),
    pa: pcm.producersAccuracy().get([1, 0]), ua: pcm.consumersAccuracy().get([0, 1])})
    .evaluate(function (d, err) {
      if (err) { probeLabel.setValue('Error: ' + err); return; }
      probeLabel.setValue('Probes: ' + d.n + ' (peat = depth >= ' + PEAT_DEPTH_CM + ' cm)' +
        '\nOA ' + (100 * d.oa).toFixed(1) + ' %  kappa ' + d.kappa.toFixed(3) +
        '\nPeat PA ' + (100 * d.pa).toFixed(1) + ' %  UA ' + (100 * d.ua).toFixed(1) + ' %');
    });
  panel.add(ui.Chart.feature.byFeature(probeS, DEPTH_PROPERTY, ['prob']).setChartType('ScatterChart')
    .setOptions({title: 'Predicted peat probability vs measured peat depth', hAxis: {title: 'depth (cm)'},
      vAxis: {title: 'probability', viewWindow: {min: 0, max: 1}}, pointSize: 3, legend: {position: 'none'},
      colors: ['#6b4f2a']}));
}

// Challenges (always visible)
panel.add(ui.Label('Known challenges', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(ui.Label(
  '- C-band sees vegetation and surface wetness, not peat depth; extent is inferred from proxies.\n' +
  '- Drained, cultivated (e.g. Fens) or afforested peat looks like mineral land; wet acid grassland and\n' +
  '  heather on thin peaty podzols look like peat.\n' +
  '- Peat definitions differ (England deep peat >= 40 cm; Scotland > 50 cm; peaty soils 10-40 cm).\n' +
  '- Reference maps are generalised (GPM 1 km) or disputed (England Peat Map 2025): accuracy vs a map\n' +
  '  measures agreement, only field probes measure truth.\n' +
  '- Upland terrain: layover/shadow masked, residual slope effects remain; shadows were a reported\n' +
  '  failure of the national map.\n' +
  '- Year-to-year wetness (droughts 2018/2022) shifts backscatter; S1B lost after Dec 2021.\n' +
  '- A model trained here is not transferable to lowland fens or the Flow Country without retraining.',
  {fontSize: '11px', whiteSpace: 'pre', color: '#444'}));

// Click: S1 time series and probability
panel.add(ui.Label('Click the map for the pixel S1 time series and peat probability.', {fontSize: '12px', color: '#555'}));
var clickPanel = ui.Panel();
panel.add(clickPanel);
Map.onClick(function (c) {
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  clickPanel.clear();
  clickPanel.add(ui.Chart.image.series({imageCollection: s1Db, region: pt, reducer: ee.Reducer.mean(), scale: SCALE})
    .setOptions({title: 'Terrain-flattened gamma0 (dB)', pointSize: 3, lineWidth: 0, colors: ['#1b9e77', '#d95f02']}));
  var pl = ui.Label('p(peat) = ...');
  clickPanel.add(pl);
  prob.reduceRegion({reducer: ee.Reducer.first(), geometry: pt, crs: proj, scale: SCALE}).get('peat_prob')
    .evaluate(function (v) { pl.setValue('p(peat) = ' + (v === null ? 'not assessed' : v.toFixed(2))); });
});

// Exports
Export.image.toDrive({image: prob.toFloat(), description: 'peat_probability_PeakDistrict', region: AOI,
  crs: CRS, scale: SCALE, maxPixels: 1e10});
Export.table.toDrive({collection: samples, description: 'peat_training_samples', fileFormat: 'CSV'});
