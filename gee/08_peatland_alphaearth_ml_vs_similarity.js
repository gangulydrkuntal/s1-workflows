/*******************************************************************************
 * USE CASE 8 - PEATLAND DETECTION FROM ALPHAEARTH FOUNDATIONS EMBEDDINGS:
 *              SUPERVISED MACHINE LEARNING vs. EMBEDDING SIMILARITY SEARCH
 *
 * Data       : Google Satellite Embedding V1 (AlphaEarth Foundations, Brown et
 *              al., 2025, arXiv:2507.22291; CC-BY 4.0). 64-D unit vectors per
 *              10 m pixel per year summarising Sentinel-2, Landsat, Sentinel-1,
 *              LiDAR, DEM, climate and other inputs. Peat-forming conditions
 *              (wet, cool, gentle terrain, bog vegetation, its seasonal
 *              dynamics) are all encoded, which makes the embeddings a compact
 *              predictor set for digital peat mapping (Minasny et al., 2019).
 *
 * Site       : Peak District, England (same AOI and stress-test sites as use
 *              case 6 so results can be compared): Dark Peak blanket peat
 *              (Kinder Scout, Bleaklow) vs White Peak limestone (no peat).
 *
 * Reference  : (default) Global Peatland Map 2.0 (Greifswald Mire Centre /
 *              UNEP 2022, 1 km, GEE community catalog; CC BY-NC-SA). Labels =
 *              interior of "peat dominated" cells vs land > 2 km from any
 *              mapped peat/organic soil.
 *              (recommended) upload a national map - England Peat Map 2025,
 *              Unified Peat Map of Wales, Scotland Carbon & Peatland 2016.
 *
 * Methods compared
 *   1. ML: Random Forest (300 trees, probability output) on the 64 axes
 *      (embeddings are designed for tree-based classifiers and linear models).
 *   2. Similarity search: the peat training embeddings are clustered with
 *      k-means into K prototypes (captures intact bog, eroded peat, heather
 *      moorland ...); each prototype is re-normalised to unit length and the
 *      score of a pixel is its maximum cosine similarity (dot product) to any
 *      prototype - the standard embedding "similarity search" recipe, made
 *      robust to peatland heterogeneity by using several prototypes. It uses
 *      ONLY positive (peat) examples.
 *
 * Reliable train / validation / test design
 *   - Spatial blocks (0.05 deg, larger than the 1 km label cells) randomly
 *     assigned: 60 % train, 20 % validation, 20 % test.
 *   - Samples within 10 % of a block edge are discarded (buffer against
 *     spatial autocorrelation across block borders; Roberts et al., 2017).
 *   - TRAIN fits the RF and builds the similarity prototypes.
 *   - VALIDATION is used only to (a) choose each method's decision threshold
 *     (max F1) and (b) calibrate each score into a probability by histogram
 *     binning (Zadrozny & Elkan, 2001), so that "confidence" means the same
 *     thing for both methods.
 *   - TEST is touched once for the final, unbiased comparison:
 *       AUC (threshold-free), OA / kappa / precision / recall / F1 at the
 *       validation threshold, Brier score of calibrated probabilities,
 *       share of confident predictions (p >= 0.8 or <= 0.2) and their
 *       accuracy, reliability diagram, and McNemar's paired test of whether
 *       the two methods differ significantly (Dietterich, 1998).
 *   - Known-site stress tests (Kinder, Bleaklow, White Peak).
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([-2.05, 53.18, -1.65, 53.55]);   // Peak District
var YEAR = 2023;
var SCALE = 30;                 // sampling scale (m); embeddings average linearly
var BLOCK_DEG = 0.05;           // spatial block size
var EDGE_MARGIN = 0.1;          // fraction of block width discarded at edges
var N_PER_CLASS = 2000;
var N_TREES = 300;
var K_PROTOTYPES = 5;

var REFERENCE_MODE = 'GPM';     // 'GPM' or 'ASSET'
var REF_ASSET = null;           // e.g. 'users/<you>/England_Peat_Map_extent'
var REF_PROPERTY = null;        // null = every polygon is peat; else numeric class field
var REF_PEAT_VALUES = [];       // e.g. Scotland C&P 2016: [1, 2, 5]
var REF_NONPEAT_VALUES = [];    // e.g. Scotland C&P 2016: [4]

var KNOWN_SITES = ee.FeatureCollection([
  ee.Feature(ee.Geometry.Rectangle([-1.895, 53.375, -1.835, 53.400]), {name: 'Kinder Scout plateau', expected: 'peat'}),
  ee.Feature(ee.Geometry.Rectangle([-1.880, 53.450, -1.800, 53.475]), {name: 'Bleaklow plateau', expected: 'peat'}),
  ee.Feature(ee.Geometry.Rectangle([-1.820, 53.180, -1.720, 53.240]), {name: 'White Peak limestone', expected: 'non-peat'})
]);

var BANDS = [];
for (var b = 0; b < 64; b++) { BANDS.push('A' + ('0' + b).slice(-2)); }

// =============================================================================
// 1. EMBEDDINGS, MASKS, REFERENCE LABELS
// =============================================================================
var embCol = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
  .filterDate(YEAR + '-01-01', (YEAR + 1) + '-01-01').filterBounds(AOI);
var embProj = ee.Image(embCol.first()).select('A00').projection();
var emb = embCol.mosaic().setDefaultProjection(embProj).select(BANDS).clip(AOI);

var worldCover = ee.ImageCollection('ESA/WorldCover/v200').first().select('Map');
var water = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').unmask(0).gte(50)
  .or(worldCover.eq(80));
var built = worldCover.eq(50);
var valid = water.not().and(built.not()).rename('valid');

var gpm = ee.Image('projects/sat-io/open-datasets/GLOBAL-PEATLAND-DATABASE').select(0).unmask(0);
var label;
if (REFERENCE_MODE === 'GPM') {
  var peatLab = gpm.eq(1).focalMin(1000, 'circle', 'meters');
  var nonLab = gpm.gt(0).focalMax(2000, 'circle', 'meters').not();
  label = ee.Image(0).where(peatLab, 1).updateMask(peatLab.or(nonLab));
} else {
  var refFc = ee.FeatureCollection(REF_ASSET).filterBounds(AOI);
  if (REF_PROPERTY === null) {
    var inside = ee.Image(0).byte().paint(refFc, 1);
    var peatA = inside.focalMin(60, 'circle', 'meters');
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

// Reference layer for display (the uploaded map if any, else GPM)
var refDisplay = REFERENCE_MODE === 'GPM' ? gpm.updateMask(gpm.gt(0)) :
  ee.Image(0).byte().paint(ee.FeatureCollection(REF_ASSET).filterBounds(AOI), 1).selfMask();

// =============================================================================
// 2. SPATIAL BLOCKS: TRAIN / VALIDATION / TEST
// =============================================================================
var blockRand = ee.Image.random(42).reproject(ee.Projection('EPSG:4326').scale(BLOCK_DEG, BLOCK_DEG));
var split = ee.Image(2).where(blockRand.lt(0.8), 1).where(blockRand.lt(0.6), 0).rename('split');
var ll = ee.Image.pixelLonLat();
var fx = ll.select('longitude').divide(BLOCK_DEG); fx = fx.subtract(fx.floor());
var fy = ll.select('latitude').divide(BLOCK_DEG); fy = fy.subtract(fy.floor());
var blockInterior = fx.gt(EDGE_MARGIN).and(fx.lt(1 - EDGE_MARGIN))
  .and(fy.gt(EDGE_MARGIN)).and(fy.lt(1 - EDGE_MARGIN));

var samples = emb.addBands(label).addBands(split)
  .updateMask(valid).updateMask(blockInterior)
  .stratifiedSample({numPoints: N_PER_CLASS, classBand: 'peat', region: AOI, scale: SCALE,
    seed: 11, tileScale: 8, geometries: true})
  .filter(ee.Filter.notNull(BANDS));
var train = samples.filter(ee.Filter.eq('split', 0));
var val = samples.filter(ee.Filter.eq('split', 1));
var test = samples.filter(ee.Filter.eq('split', 2));

// =============================================================================
// 3. METHOD 1 - RANDOM FOREST
// =============================================================================
var rf = ee.Classifier.smileRandomForest({numberOfTrees: N_TREES, minLeafPopulation: 2, seed: 7})
  .setOutputMode('PROBABILITY')
  .train({features: train, classProperty: 'peat', inputProperties: BANDS});
var rfScore = emb.classify(rf).rename('rf').updateMask(valid);

// =============================================================================
// 4. METHOD 2 - SIMILARITY SEARCH TO K PEAT PROTOTYPES
// =============================================================================
var posTrain = train.filter(ee.Filter.eq('peat', 1));
var clusterer = ee.Clusterer.wekaKMeans({nClusters: K_PROTOTYPES, seed: 3}).train(posTrain, BANDS);
var posClustered = posTrain.cluster(clusterer, 'cl');
var protoFc = ee.FeatureCollection(ee.List.sequence(0, K_PROTOTYPES - 1).map(function (c) {
  var sub = posClustered.filter(ee.Filter.eq('cl', c));
  var n = sub.size();
  var mean = ee.List(sub.reduceColumns(ee.Reducer.mean().repeat(BANDS.length), BANDS).get('mean'));
  var norm = ee.Number(mean.map(function (x) { return ee.Number(x).pow(2); }).reduce(ee.Reducer.sum())).sqrt();
  return ee.Feature(null, {cl: c, n: n,
    proto: ee.Algorithms.If(n.gt(0), mean.map(function (x) { return ee.Number(x).divide(norm); }), null)});
})).filter(ee.Filter.gt('n', 0));
var protos = protoFc.aggregate_array('proto');
var simScore = ee.ImageCollection(protos.map(function (p) {
  return emb.multiply(ee.Image.constant(ee.List(p))).reduce(ee.Reducer.sum());
})).max().rename('sim').updateMask(valid);

// =============================================================================
// 5. VALIDATION: THRESHOLDS + CALIBRATION (no test data used)
// =============================================================================
var scores = rfScore.addBands(simScore);
function scoreFc(fc) {
  return scores.sampleRegions({collection: fc, properties: ['peat'], scale: SCALE, tileScale: 8})
    .filter(ee.Filter.notNull(['rf', 'sim']));
}
var valS = scoreFc(val);
var testS = scoreFc(test);

function bestThreshold(fc, prop, lo, hi, n) {
  var cand = ee.FeatureCollection(ee.List.sequence(lo, hi, null, n).map(function (t) {
    var tp = fc.filter(ee.Filter.eq('peat', 1)).filter(ee.Filter.gte(prop, t)).size();
    var fp = fc.filter(ee.Filter.eq('peat', 0)).filter(ee.Filter.gte(prop, t)).size();
    var fn = fc.filter(ee.Filter.eq('peat', 1)).filter(ee.Filter.lt(prop, t)).size();
    return ee.Feature(null, {t: t, f1: tp.multiply(2).divide(tp.multiply(2).add(fp).add(fn).max(1))});
  }));
  return ee.Number(cand.sort('f1', false).first().get('t'));
}
var simLo = ee.Number(valS.aggregate_min('sim')), simHi = ee.Number(valS.aggregate_max('sim'));
var thrRF = bestThreshold(valS, 'rf', 0.02, 0.98, 49);
var thrSim = bestThreshold(valS, 'sim', simLo, simHi, 60);

/** Histogram-binning calibration on the validation set. */
function calibration(fc, prop, lo, hi, nb) {
  lo = ee.Number(lo); hi = ee.Number(hi);
  var w = hi.subtract(lo).divide(nb);
  var bins = ee.FeatureCollection(ee.List.sequence(0, nb - 1).map(function (i) {
    i = ee.Number(i);
    var a = lo.add(w.multiply(i)), bnd = a.add(w);
    var sub = fc.filter(ee.Filter.gte(prop, a)).filter(ee.Filter.lt(prop, ee.Number(ee.Algorithms.If(i.eq(nb - 1), bnd.add(1e-6), bnd))));
    return ee.Feature(null, {x: a.add(w.divide(2)), y: sub.aggregate_mean('peat'), n: sub.size()});
  })).filter(ee.Filter.gte('n', 10));
  return {x: bins.aggregate_array('x'), y: bins.aggregate_array('y')};
}
var calRF = calibration(valS, 'rf', 0, 1, 10);
var calSim = calibration(valS, 'sim', simLo, simHi, 10);
var rfProb = rfScore.interpolate(calRF.x, calRF.y, 'clamp').rename('rf_cal');
var simProb = simScore.interpolate(calSim.x, calSim.y, 'clamp').rename('sim_cal');
var rfPeat = rfScore.gte(ee.Image.constant(thrRF)).rename('rf_peat');
var simPeat = simScore.gte(ee.Image.constant(thrSim)).rename('sim_peat');
var rfConf = rfProb.subtract(0.5).abs().multiply(2).rename('rf_conf');     // 0 = unsure, 1 = certain
var simConf = simProb.subtract(0.5).abs().multiply(2).rename('sim_conf');
var agreement = rfPeat.multiply(2).add(simPeat).rename('agree');           // 0 none, 1 sim only, 2 RF only, 3 both

// =============================================================================
// 6. TEST: FINAL, UNBIASED COMPARISON
// =============================================================================
var testC = ee.Image.cat(rfScore, simScore, rfProb, simProb)
  .sampleRegions({collection: test, properties: ['peat'], scale: SCALE, tileScale: 8})
  .filter(ee.Filter.notNull(['rf', 'sim', 'rf_cal', 'sim_cal']))
  .map(function (f) {
    var y = ee.Number(f.get('peat'));
    var pr = ee.Number(f.get('rf')).gte(thrRF), ps = ee.Number(f.get('sim')).gte(thrSim);
    return f.set({pred_rf: pr, pred_sim: ps,
      ok_rf: pr.eq(y), ok_sim: ps.eq(y),
      se_rf: ee.Number(f.get('rf_cal')).subtract(y).pow(2),
      se_sim: ee.Number(f.get('sim_cal')).subtract(y).pow(2)});
  });

function testMetrics(fc, score, pred, cal, se, lo, hi) {
  var P = fc.filter(ee.Filter.eq('peat', 1)), N = fc.filter(ee.Filter.eq('peat', 0));
  var nP = P.size(), nN = N.size();
  var tp = P.filter(ee.Filter.eq(pred, 1)).size(), fn = nP.subtract(tp);
  var fp = N.filter(ee.Filter.eq(pred, 1)).size(), tn = nN.subtract(fp);
  var n = nP.add(nN);
  var oa = tp.add(tn).divide(n);
  var pe = tp.add(fp).multiply(nP).add(tn.add(fn).multiply(nN)).divide(n.pow(2));
  var prec = tp.divide(tp.add(fp).max(1)), rec = tp.divide(nP.max(1));
  var roc = ee.List.sequence(lo, hi, null, 41).map(function (t) {
    return [N.filter(ee.Filter.gte(score, t)).size().divide(nN), P.filter(ee.Filter.gte(score, t)).size().divide(nP)];
  });
  var conf = fc.filter(ee.Filter.or(ee.Filter.gte(cal, 0.8), ee.Filter.lte(cal, 0.2)));
  return ee.Dictionary({n: n, OA: oa, kappa: oa.subtract(pe).divide(ee.Number(1).subtract(pe)),
    precision: prec, recall: rec, F1: prec.multiply(rec).multiply(2).divide(prec.add(rec).max(1e-9)),
    brier: fc.aggregate_mean(se), confShare: conf.size().divide(n),
    confAcc: conf.aggregate_mean(pred === 'pred_rf' ? 'ok_rf' : 'ok_sim'), roc: roc});
}
var mRF = testMetrics(testC, 'rf', 'pred_rf', 'rf_cal', 'se_rf', 0, 1);
var mSim = testMetrics(testC, 'sim', 'pred_sim', 'sim_cal', 'se_sim', simLo, simHi);
var mcnemar = ee.Dictionary({
  b: testC.filter(ee.Filter.eq('ok_rf', 1)).filter(ee.Filter.eq('ok_sim', 0)).size(),
  c: testC.filter(ee.Filter.eq('ok_rf', 0)).filter(ee.Filter.eq('ok_sim', 1)).size()});

// Reliability diagram data (test set, calibrated probabilities)
function reliability(fc, cal, name) {
  return ee.FeatureCollection(ee.List.sequence(0, 9).map(function (i) {
    i = ee.Number(i);
    var sub = fc.filter(ee.Filter.gte(cal, i.divide(10))).filter(ee.Filter.lt(cal, i.add(1).divide(10).add(ee.Number(ee.Algorithms.If(i.eq(9), 1e-6, 0)))));
    return ee.Feature(null, {pred: sub.aggregate_mean(cal), obs: sub.aggregate_mean('peat'), n: sub.size(), method: name});
  })).filter(ee.Filter.gte('n', 5));
}
var relFc = reliability(testC, 'rf_cal', 'Random Forest').merge(reliability(testC, 'sim_cal', 'Similarity search'))
  .merge(ee.FeatureCollection([ee.Feature(null, {pred: 0, obs: 0, method: 'Perfect calibration'}),
    ee.Feature(null, {pred: 1, obs: 1, method: 'Perfect calibration'})]));

// Known-site stress tests
var siteStats = KNOWN_SITES.map(function (f) {
  var s = ee.Image.cat(rfPeat.toFloat(), simPeat.toFloat()).reduceRegion({reducer: ee.Reducer.mean(),
    geometry: f.geometry(), crs: embProj, scale: SCALE, maxPixels: 1e9, tileScale: 4});
  return f.set({rf_pct: ee.Number(s.get('rf_peat')).multiply(100), sim_pct: ee.Number(s.get('sim_peat')).multiply(100)});
});

// =============================================================================
// 7. SPLIT-PANEL MAPS (slider) WITH REFERENCE LAYERS ON BOTH SIDES
// =============================================================================
var probVis = {min: 0, max: 1, palette: ['f7f4ea', 'd9c9a0', 'a68a4b', '6b4f2a', '2b1d0e']};
var confVis = {min: 0, max: 1, palette: ['ff00ff', 'f7f7f7', '1a9850']};
var diffVis = {min: -0.5, max: 0.5, palette: ['2166ac', 'f7f7f7', 'b2182b']};
var agreeVis = {min: 0, max: 3, palette: ['ffffff00', '00a6c4', 'e6a100', '4a2c0f']};

function addCommonLayers(m) {
  m.addLayer(emb, {bands: ['A01', 'A16', 'A09'], min: -0.3, max: 0.3}, 'Embedding RGB (A01, A16, A09)', false);
  m.addLayer(refDisplay, {min: 1, max: 2, palette: ['5b3a1a', 'c49a6c']},
    REFERENCE_MODE === 'GPM' ? 'REFERENCE: Global Peatland Map 2.0 (1 = peat, 2 = mosaic)' : 'REFERENCE: uploaded peat map', false);
  m.addLayer(label.clip(AOI), {min: 0, max: 1, palette: ['3a87c8', '6b4f2a']}, 'Label zones used (brown peat / blue non-peat)', false);
  m.addLayer(split.clip(AOI).updateMask(blockInterior), {min: 0, max: 2, palette: ['1f78b4', 'ffbf00', 'e31a1c'], opacity: 0.35},
    'Spatial blocks (blue train / amber validation / red test)', false);
  m.addLayer(ee.Image(ee.Image('projects/sat-io/open-datasets/ML-GLOBAL-PEATLAND-EXTENT').select(0)).clip(AOI),
    {min: 0, max: 100, palette: ['ffffff', '6b4f2a']}, 'Peat-ML fractional cover (Melton 2022, comparison)', false);
  m.addLayer(agreement.selfMask(), agreeVis, 'Agreement (brown both, amber RF only, cyan similarity only)', false);
  m.addLayer(rfProb.subtract(simProb), diffVis, 'Calibrated probability difference (RF - similarity)', false);
  m.addLayer(water.or(built).selfMask().clip(AOI), {palette: ['9e9e9e']}, 'Not assessed (water / built-up)', true);
  m.addLayer(ee.Image().byte().paint(KNOWN_SITES, 1, 2), {palette: ['ffff00']}, 'Stress-test sites', true);
}

var leftMap = ui.Map();
var rightMap = ui.Map();
leftMap.setOptions('TERRAIN'); rightMap.setOptions('TERRAIN');
leftMap.addLayer(rfProb, probVis, 'RF calibrated peat probability', false);
leftMap.addLayer(rfConf, confVis, 'RF confidence (|p - 0.5| x 2)', false);
leftMap.addLayer(rfPeat.selfMask(), {palette: ['4a2c0f']}, 'RF peat (validation threshold)', true);
rightMap.addLayer(simProb, probVis, 'Similarity calibrated peat probability', false);
rightMap.addLayer(simConf, confVis, 'Similarity confidence (|p - 0.5| x 2)', false);
rightMap.addLayer(simScore, {min: 0.5, max: 1, palette: ['000000', '3b0f70', 'de4968', 'fcfdbf']}, 'Raw max cosine similarity', false);
rightMap.addLayer(simPeat.selfMask(), {palette: ['4a2c0f']}, 'Similarity peat (validation threshold)', true);
addCommonLayers(leftMap);
addCommonLayers(rightMap);
leftMap.add(ui.Label('LEFT: Random Forest', {fontWeight: 'bold', position: 'top-left'}));
rightMap.add(ui.Label('RIGHT: Similarity search', {fontWeight: 'bold', position: 'top-right'}));
var linker = ui.Map.Linker([leftMap, rightMap]);
var splitPanel = ui.SplitPanel({firstPanel: leftMap, secondPanel: rightMap, wipe: true, style: {stretch: 'both'}});
leftMap.centerObject(AOI, 10);

// =============================================================================
// 8. SIDE PANEL: legend, metrics, charts
// =============================================================================
var panel = ui.Panel({style: {width: '440px', padding: '8px'}});
ui.root.clear();
ui.root.add(panel);
ui.root.add(splitPanel);

panel.add(ui.Label('Peatland from AlphaEarth Embeddings', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Random Forest vs similarity search | Peak District | embeddings ' + YEAR +
  '\nDrag the slider on the map to compare methods.', {fontSize: '12px', color: '#555', whiteSpace: 'pre'}));
panel.add(ui.Label('Reference: ' + (REFERENCE_MODE === 'GPM' ?
  'Global Peatland Map 2.0 (1 km) - test scores = agreement with GPM, not field truth.' : REF_ASSET),
  {fontSize: '11px', color: '#a00'}));

function legendRow(color, text) {
  return ui.Panel([ui.Label('', {backgroundColor: '#' + color, padding: '8px', margin: '2px 6px 2px 8px'}),
    ui.Label(text, {fontSize: '12px', margin: '2px 0'})], ui.Panel.Layout.flow('horizontal'));
}
function colorBar(vis, title, lo, hi) {
  return ui.Panel([ui.Label(title, {fontSize: '12px', margin: '4px 8px 0 8px'}),
    ui.Thumbnail({image: ee.Image.pixelLonLat().select(0),
      params: {bbox: [0, 0, 1, 0.1], dimensions: '300x10', format: 'png', min: 0, max: 1, palette: vis.palette},
      style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '16px'}}),
    ui.Panel([ui.Label(lo, {fontSize: '10px', margin: '0 8px'}),
      ui.Label(hi, {fontSize: '10px', margin: '0 8px', textAlign: 'right', stretch: 'horizontal'})],
      ui.Panel.Layout.flow('horizontal'))]);
}
panel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(legendRow('4a2c0f', 'Peat (method-specific validation threshold)'));
panel.add(legendRow('e6a100', 'Agreement layer: RF only'));
panel.add(legendRow('00a6c4', 'Agreement layer: similarity only'));
panel.add(legendRow('5b3a1a', 'Reference peat (GPM class 1 / uploaded map)'));
panel.add(legendRow('9e9e9e', 'Not assessed (water / built-up)'));
panel.add(colorBar(probVis, 'Calibrated peat probability', '0', '1'));
panel.add(colorBar(confVis, 'Confidence', '0 (unsure)', '1 (certain)'));
panel.add(colorBar(diffVis, 'Probability difference RF - similarity', '-0.5 (sim higher)', '+0.5 (RF higher)'));

var resLabel = ui.Label('Training, validating and testing...', {whiteSpace: 'pre', fontSize: '12px', fontFamily: 'monospace'});
panel.add(ui.Label('Test-set comparison', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(resLabel);
var chartsPanel = ui.Panel();
panel.add(chartsPanel);

/** Two-sided p-value of McNemar's chi-square (df = 1) via erfc. */
function mcnemarP(b, c) {
  if (b + c === 0) { return 1; }
  var chi2 = Math.pow(Math.abs(b - c) - 1, 2) / (b + c);
  var x = Math.sqrt(chi2 / 2);
  var t = 1 / (1 + 0.3275911 * x);
  var erfc = t * (0.254829592 + t * (-0.284496736 + t * (1.421413741 + t * (-1.453152027 + t * 1.061405429)))) * Math.exp(-x * x);
  return erfc;
}
function auc(pts) {
  var p = pts.slice().sort(function (a, b) { return a[0] - b[0] || a[1] - b[1]; });
  p.unshift([0, 0]); p.push([1, 1]);
  var a = 0;
  for (var i = 1; i < p.length; i++) { a += (p[i][0] - p[i - 1][0]) * (p[i][1] + p[i - 1][1]) / 2; }
  return a;
}

ee.Dictionary({rf: mRF, sim: mSim, mc: mcnemar, thrRF: thrRF, thrSim: thrSim,
  nTrain: train.size(), nVal: valS.size(), nTest: testC.size(), nProto: protos.size(),
  sites: siteStats.reduceColumns(ee.Reducer.toList(4), ['name', 'expected', 'rf_pct', 'sim_pct']).get('list')
}).evaluate(function (r, err) {
  if (err) { resLabel.setValue('Error: ' + err); return; }
  var aRF = auc(r.rf.roc), aSim = auc(r.sim.roc);
  var p = mcnemarP(r.mc.b, r.mc.c);
  function row(name, a, b2, pct, lowerBetter) {
    var f = function (v) { return pct ? (100 * v).toFixed(1) : v.toFixed(3); };
    var win = lowerBetter ? (a < b2 ? '  <RF' : (b2 < a ? '  <SIM' : '')) : (a > b2 ? '  <RF' : (b2 > a ? '  <SIM' : ''));
    return '\n' + (name + '                        ').slice(0, 24) + ('        ' + f(a)).slice(-8) + ('        ' + f(b2)).slice(-8) + win;
  }
  var t = 'Samples: train ' + r.nTrain + ' | validation ' + r.nVal + ' | test ' + r.nTest +
    '\nThresholds (chosen on validation): RF p >= ' + r.thrRF.toFixed(2) + ', cosine >= ' + r.thrSim.toFixed(3) +
    '\nSimilarity prototypes: ' + r.nProto +
    '\n\nTEST METRICS                  RF     SIM' +
    row('AUC (threshold-free)', aRF, aSim) +
    row('Overall accuracy (%)', r.rf.OA, r.sim.OA, true) +
    row('Kappa', r.rf.kappa, r.sim.kappa) +
    row('Precision peat (%)', r.rf.precision, r.sim.precision, true) +
    row('Recall peat (%)', r.rf.recall, r.sim.recall, true) +
    row('F1 peat', r.rf.F1, r.sim.F1) +
    row('Brier score (cal.)', r.rf.brier, r.sim.brier, false, true) +
    row('Confident share (%)', r.rf.confShare, r.sim.confShare, true) +
    row('Accuracy if confident (%)', r.rf.confAcc, r.sim.confAcc, true) +
    '\n\nMcNemar paired test: RF-only correct ' + r.mc.b + ', SIM-only correct ' + r.mc.c +
    '\n  p = ' + p.toExponential(2) + (p < 0.05 ? '  -> difference is significant' : '  -> no significant difference') +
    '\n\nSTRESS TESTS                   RF%   SIM%';
  r.sites.forEach(function (s) {
    var ok = function (v) { return s[1] === 'peat' ? (v >= 70 ? 'P' : 'F') : (v <= 10 ? 'P' : 'F'); };
    t += '\n ' + (s[0] + ' (' + s[1] + ')                          ').slice(0, 30) +
      ('    ' + s[2].toFixed(0)).slice(-4) + ok(s[2]) + ('    ' + s[3].toFixed(0)).slice(-5) + ok(s[3]);
  });
  var moreConf = (r.rf.brier < r.sim.brier && aRF >= aSim) ? 'Random Forest' :
    ((r.sim.brier < r.rf.brier && aSim >= aRF) ? 'Similarity search' : 'mixed (see metrics)');
  t += '\n(P = pass, F = fail; peat sites >= 70 %, limestone <= 10 %)' +
    '\n\nMore confident AND better calibrated on test: ' + moreConf;
  resLabel.setValue(t);

  var rocFc = ee.FeatureCollection(r.rf.roc.map(function (q) { return ee.Feature(null, {FPR: q[0], TPR: q[1], method: 'Random Forest'}); })
    .concat(r.sim.roc.map(function (q) { return ee.Feature(null, {FPR: q[0], TPR: q[1], method: 'Similarity search'}); })));
  chartsPanel.add(ui.Chart.feature.groups(rocFc.sort('FPR'), 'FPR', 'TPR', 'method').setChartType('LineChart')
    .setOptions({title: 'ROC on TEST blocks (AUC RF ' + aRF.toFixed(3) + ', SIM ' + aSim.toFixed(3) + ')',
      hAxis: {title: 'False positive rate', viewWindow: {min: 0, max: 1}},
      vAxis: {title: 'True positive rate', viewWindow: {min: 0, max: 1}}, pointSize: 2, colors: ['#6b4f2a', '#00a6c4']}));
  var cmp = ee.FeatureCollection([
    ee.Feature(null, {metric: 'AUC', RF: aRF, SIM: aSim}),
    ee.Feature(null, {metric: 'F1', RF: r.rf.F1, SIM: r.sim.F1}),
    ee.Feature(null, {metric: 'Kappa', RF: r.rf.kappa, SIM: r.sim.kappa}),
    ee.Feature(null, {metric: '1 - Brier', RF: 1 - r.rf.brier, SIM: 1 - r.sim.brier}),
    ee.Feature(null, {metric: 'Confident share', RF: r.rf.confShare, SIM: r.sim.confShare})]);
  chartsPanel.add(ui.Chart.feature.byFeature(cmp, 'metric', ['RF', 'SIM']).setChartType('ColumnChart')
    .setOptions({title: 'Test-set comparison (higher = better)', vAxis: {viewWindow: {min: 0, max: 1}},
      colors: ['#6b4f2a', '#00a6c4']}));
});

panel.add(ui.Chart.feature.groups(relFc.sort('pred'), 'pred', 'obs', 'method').setChartType('LineChart')
  .setOptions({title: 'Reliability diagram (TEST): predicted vs observed peat fraction',
    hAxis: {title: 'Calibrated probability', viewWindow: {min: 0, max: 1}},
    vAxis: {title: 'Observed peat fraction', viewWindow: {min: 0, max: 1}},
    pointSize: 4}));

// Confidence distributions over the AOI (sampled)
var confSample = rfConf.addBands(simConf).sample({region: AOI, scale: 60, numPixels: 4000, seed: 5, geometries: false});
panel.add(ui.Chart.feature.histogram({features: confSample, property: 'rf_conf', minBucketWidth: 0.05})
  .setOptions({title: 'RF confidence distribution (AOI)', legend: {position: 'none'}, colors: ['#6b4f2a']}));
panel.add(ui.Chart.feature.histogram({features: confSample, property: 'sim_conf', minBucketWidth: 0.05})
  .setOptions({title: 'Similarity confidence distribution (AOI)', legend: {position: 'none'}, colors: ['#00a6c4']}));

// Area statistics on demand
var areaLabel = ui.Label('', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Button({label: 'Compute peat area and agreement statistics', onClick: function () {
  areaLabel.setValue('Computing (30 m)...');
  var px = ee.Image.pixelArea().divide(1e6);
  ee.Image.cat(px.updateMask(rfPeat).rename('rf'), px.updateMask(simPeat).rename('sim'),
      px.updateMask(agreement.eq(3)).rename('both'), px.updateMask(gpm.eq(1)).rename('gpm'))
    .reduceRegion({reducer: ee.Reducer.sum(), geometry: AOI, crs: embProj, scale: SCALE, maxPixels: 1e10, tileScale: 16})
    .evaluate(function (d, err) {
      if (err) { areaLabel.setValue('Error: ' + err); return; }
      areaLabel.setValue('Peat area RF: ' + d.rf.toFixed(1) + ' km2 | similarity: ' + d.sim.toFixed(1) + ' km2' +
        '\nBoth methods agree on peat: ' + d.both.toFixed(1) + ' km2 (Jaccard ' +
        (d.both / (d.rf + d.sim - d.both)).toFixed(2) + ')' +
        '\nGPM 2.0 peat-dominated: ' + d.gpm.toFixed(1) + ' km2');
    });
}}));
panel.add(areaLabel);

// Click: values of both methods at a pixel
var clickLabel = ui.Label('Click the map to read both probabilities at a pixel.', {fontSize: '12px', color: '#555'});
panel.add(clickLabel);
function onClick(c) {
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  ee.Image.cat(rfProb, simProb, simScore).reduceRegion({reducer: ee.Reducer.first(), geometry: pt, crs: embProj, scale: 10})
    .evaluate(function (d) {
      if (!d || d.rf_cal === null) { clickLabel.setValue('Not assessed here (water/built-up or no data).'); return; }
      clickLabel.setValue('RF p(peat) = ' + d.rf_cal.toFixed(2) + ' | similarity p(peat) = ' + d.sim_cal.toFixed(2) +
        ' (cosine ' + d.sim.toFixed(3) + ')');
    });
}
leftMap.onClick(onClick);
rightMap.onClick(onClick);

panel.add(ui.Label('Known challenges', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(ui.Label(
  '- Default labels are 1 km GPM cells: noisy at 30 m; scores = agreement with GPM.\n' +
  '- Similarity search uses peat examples only: it finds "peat-like" land (wet heath, acid\n' +
  '  grassland, moorland on thin peaty soils) and cannot learn what peat is NOT.\n' +
  '- RF needs both classes and can overfit label noise; calibration fixes scale, not bias.\n' +
  '- Embeddings describe the surface; drained, cultivated or afforested peat looks mineral.\n' +
  '- Embeddings are annual and not physically interpretable; prototypes are site-specific.\n' +
  '- Spatial blocks reduce but do not remove autocorrelation; transfer to other regions\n' +
  '  (Fens, Flow Country) needs new training data.\n' +
  '- GPM 2.0 is CC BY-NC-SA (non-commercial).',
  {fontSize: '11px', whiteSpace: 'pre', color: '#444'}));

Export.image.toDrive({image: ee.Image.cat(rfProb, simProb, rfPeat.toFloat(), simPeat.toFloat()).toFloat(),
  description: 'peat_alphaearth_rf_vs_similarity', region: AOI, crs: 'EPSG:27700', scale: SCALE, maxPixels: 1e10});
Export.table.toDrive({collection: testC, description: 'peat_alphaearth_test_predictions', fileFormat: 'CSV'});
