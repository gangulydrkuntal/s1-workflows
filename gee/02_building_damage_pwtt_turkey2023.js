/*******************************************************************************
 * USE CASE 2 - BUILDING DAMAGE ASSESSMENT WITH THE PIXEL-WISE T-TEST (PWTT)
 *
 * Test event : 6 February 2023 Kahramanmaras earthquake sequence (Mw 7.8 / 7.5),
 *              city of Antakya (Hatay province, Turkiye) - one of the most
 *              heavily damaged cities, with building-level reference damage
 *              grading published by UNOSAT (UNITAR) and Copernicus EMS
 *              (activation EMSR648).
 *
 * Method (scientific basis)
 *   Ballinger, O. (2025) "Open access battle damage detection via pixel-wise
 *   T-test on Sentinel-1 imagery", Remote Sensing of Environment
 *   (ScienceDirect PII S0034425725004298; preprint arXiv:2405.06323). Building-level AUC 0.88
 *   (Ukraine) and 0.81 (Gaza) against UNOSAT labels. The algorithm is a
 *   statistical amplitude change detector that exploits the long, dense
 *   Sentinel-1 archive rather than a single image pair:
 *     1. Sentinel-1 GRD in linear power (COPERNICUS/S1_GRD_FLOAT), VV + VH.
 *     2. Lee (MMSE) speckle filter per image (ENL = 5, 3x3 window).
 *     3. Natural-log transform (makes speckle additive and ~Gaussian).
 *     4. For EACH relative orbit separately (identical viewing geometry):
 *        two-sample pooled t-test between the pre-event stack (12 months) and
 *        the post-event stack (1 month):
 *            t = |mean_post - mean_pre| / (s_p * sqrt(1/n_pre + 1/n_post))
 *        A collapsed building destroys the dihedral (double-bounce) scattering
 *        and adds rubble -> a persistent change of the backscatter mean that is
 *        large relative to the pixel's own natural variability.
 *     5. Maximum t across orbits and across VV / VH.
 *     6. Restrict to built-up areas (Dynamic World 'built' probability > 0.1),
 *        Gaussian focal median (10 m) and multi-scale circular convolutions
 *        (50, 100, 150 m) averaged with equal weights.
 *     7. Damage if T > 3.3 (default of the published method).
 *     8. Aggregation to building footprints (Microsoft Global ML Building
 *        Footprints, GEE community catalog) -> mean T per building.
 *
 * Validation strategy
 *   a) NEGATIVE CONTROL (always run): the identical workflow is applied to
 *      Mersin, a large city ~250 km away that was NOT damaged by the
 *      earthquake. The share of built-up area flagged there is an empirical
 *      false-positive rate.
 *   b) REFERENCE DATA (optional): upload the UNOSAT damage assessment points for
 *      Antakya (HDX / UNOSAT "CE20230206TUR") or Copernicus EMSR648 grading
 *      points as a GEE table asset and set REFERENCE_ASSET below. The script then
 *      computes detection rate, ROC curve and AUC (damaged reference points vs
 *      built-up pixels of the negative-control city as negatives).
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([36.12, 36.18, 36.20, 36.24]);         // Antakya
var CONTROL = ee.Geometry.Rectangle([34.56, 36.77, 34.66, 36.83]);     // Mersin (undamaged)
var EVENT_DATE = ee.Date('2023-02-06');
var PRE_MONTHS = 12;        // baseline length
var POST_MONTHS = 1;        // inference window after the event
var T_THRESHOLD = 3.3;      // published default (2 = sensitive, 4-5 = conservative)
var BUILDINGS = ee.FeatureCollection('projects/sat-io/open-datasets/MSBuildings/Turkey');

// Optional reference data (leave null if not uploaded)
var REFERENCE_ASSET = null;           // e.g. 'users/<you>/UNOSAT_Antakya_damage_points'
var DAMAGE_PROPERTY = 'Main_Damag';   // attribute holding the damage class
var DAMAGED_VALUES = ['Destroyed', 'Severe Damage', 'Moderate Damage'];

Map.centerObject(AOI, 14);
Map.setOptions('SATELLITE');

// =============================================================================
// 1. PRE-PROCESSING
// =============================================================================
/** Lee MMSE filter (as in the PWTT reference implementation). */
function leeFilter(image) {
  var bands = ['VV', 'VH'];
  var enl = 5;
  var eta = 1.0 / Math.sqrt(enl);
  var stats = image.select(bands).reduceNeighborhood({
    reducer: ee.Reducer.mean().combine({reducer2: ee.Reducer.variance(), sharedInputs: true}),
    kernel: ee.Kernel.square(1, 'pixels'),
    optimization: 'window'
  });
  var zBar = stats.select(['VV_mean', 'VH_mean']);
  var varZ = stats.select(['VV_variance', 'VH_variance']);
  var varX = varZ.subtract(zBar.pow(2).multiply(eta * eta)).divide(1 + eta * eta);
  var b = varX.divide(varZ);
  b = b.where(b.lt(0), 0);
  var out = ee.Image(1).subtract(b).multiply(zBar.abs())
    .add(b.multiply(image.select(bands))).rename(bands);
  return image.addBands(out, null, true);
}

function s1Collection(region) {
  return ee.ImageCollection('COPERNICUS/S1_GRD_FLOAT')
    .filterBounds(region)
    .filter(ee.Filter.eq('instrumentMode', 'IW'))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
    .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'));
}

// =============================================================================
// 2. PIXEL-WISE T-TEST
// =============================================================================
/** Pooled two-sample t-test for one orbit. Returns |t| for VV and VH. */
function orbitTTest(col) {
  var pre = col.filterDate(EVENT_DATE.advance(-PRE_MONTHS, 'month'), EVENT_DATE);
  var post = col.filterDate(EVENT_DATE, EVENT_DATE.advance(POST_MONTHS, 'month'));
  var preMean = pre.mean(), postMean = post.mean();
  var preSd = pre.reduce(ee.Reducer.stdDev()), postSd = post.reduce(ee.Reducer.stdDev());
  var preN = pre.select('VV').count(), postN = post.select('VV').count();
  var pooledSd = preSd.pow(2).multiply(preN.subtract(1))
    .add(postSd.pow(2).multiply(postN.subtract(1)))
    .divide(preN.add(postN).subtract(2)).sqrt();
  var denom = pooledSd.multiply(ee.Image(1).divide(preN).add(ee.Image(1).divide(postN)).sqrt());
  var t = postMean.subtract(preMean).divide(denom).abs().rename(['VV', 'VH']);
  var valid = preN.gte(3).and(postN.gte(2));
  return t.updateMask(valid).addBands(preN.rename('n_pre')).addBands(postN.rename('n_post'));
}

var EMPTY = ee.Image.constant([0, 0, 0, 0]).rename(['VV', 'VH', 'n_pre', 'n_post'])
  .toFloat().updateMask(0);

/** Full PWTT for a region; returns T_statistic (smoothed) and damage mask. */
function pwtt(region) {
  var base = s1Collection(region);
  var orbits = base.filterDate(EVENT_DATE, EVENT_DATE.advance(POST_MONTHS, 'month'))
    .aggregate_array('relativeOrbitNumber_start').distinct();
  var perOrbit = ee.ImageCollection(orbits.map(function (orb) {
    var col = base.filter(ee.Filter.eq('relativeOrbitNumber_start', orb))
      .map(leeFilter)
      .map(function (img) {
        return ee.Image(img.select(['VV', 'VH']).log().copyProperties(img, ['system:time_start']));
      });
    // Orbits without a sufficient baseline yield a fully masked image
    var nPreImgs = col.filterDate(EVENT_DATE.advance(-PRE_MONTHS, 'month'), EVENT_DATE).size();
    return ee.Image(ee.Algorithms.If(nPreImgs.gte(3), orbitTTest(col).toFloat(), EMPTY));
  }));
  var tMax = perOrbit.select(['VV', 'VH']).max();
  var maxChange = tMax.select('VV').max(tMax.select('VH')).rename('max_change');

  // Built-up mask from Dynamic World (pre-event mean probability)
  var built = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1')
    .filterBounds(region)
    .filterDate(EVENT_DATE.advance(-PRE_MONTHS, 'month'), EVENT_DATE)
    .select('built').mean();
  var urbanMask = built.gt(0.1);

  var tSmooth = maxChange.updateMask(urbanMask)
    .focalMedian(10, 'gaussian', 'meters').clip(region);
  var k50 = tSmooth.convolve(ee.Kernel.circle(50, 'meters', true));
  var k100 = tSmooth.convolve(ee.Kernel.circle(100, 'meters', true));
  var k150 = tSmooth.convolve(ee.Kernel.circle(150, 'meters', true));
  var T = tSmooth.add(k50).add(k100).add(k150).multiply(0.25)
    .updateMask(maxChange.mask()).updateMask(urbanMask).rename('T_statistic');
  return {
    T: T,
    damage: T.gt(T_THRESHOLD).rename('damage'),
    raw: maxChange,
    nPre: perOrbit.select('n_pre').max(),
    nPost: perOrbit.select('n_post').max(),
    orbits: orbits,
    urban: urbanMask
  };
}

var res = pwtt(AOI);
var ctl = pwtt(CONTROL);

// =============================================================================
// 3. BUILDING-LEVEL AGGREGATION
// =============================================================================
var bldg = BUILDINGS.filterBounds(AOI);
// Raster of footprints for fast, tile-based display
var footprintMask = ee.Image(0).byte().paint(bldg, 1).selfMask();
var buildingT = res.T.updateMask(footprintMask);

function buildingStats() {
  var withT = res.T.reduceRegions({
    collection: bldg, reducer: ee.Reducer.mean().setOutputs(['T_mean']), scale: 10, tileScale: 8
  }).filter(ee.Filter.notNull(['T_mean']))
    .map(function (f) { return f.set('damaged', ee.Number(f.get('T_mean')).gt(T_THRESHOLD)); });
  return withT;
}

// =============================================================================
// 4. MAP LAYERS
// =============================================================================
var tVis = {min: 2, max: 6, palette: ['ffffb2', 'fecc5c', 'fd8d3c', 'f03b20', 'bd0026']};
Map.addLayer(res.urban.selfMask(), {palette: ['999999'], opacity: 0.3}, 'Built-up mask (Dynamic World)', false);
Map.addLayer(res.raw, {min: 0, max: 6, palette: ['000000', 'ffffff']}, 'Raw max |t| (VV/VH, all orbits)', false);
Map.addLayer(res.T, tVis, 'PWTT T-statistic (smoothed)', false);
Map.addLayer(res.damage.selfMask(), {palette: ['ff0000'], opacity: 0.6}, 'Damage (T > ' + T_THRESHOLD + ')', true);
Map.addLayer(buildingT, tVis, 'T-statistic on building footprints', true);
Map.addLayer(ee.Image().byte().paint(AOI, 1, 2), {palette: ['00ffff']}, 'AOI Antakya');
Map.addLayer(ctl.T, tVis, 'Negative control (Mersin) T-statistic', false);
Map.addLayer(ee.Image().byte().paint(CONTROL, 1, 2), {palette: ['00ff00']}, 'Negative control AOI (Mersin)');

// =============================================================================
// 5. STATISTICS + NEGATIVE CONTROL
// =============================================================================
function damageShare(r, region) {
  var stats = ee.Image.cat(r.damage.unmask(0).updateMask(r.urban).rename('d'),
                           r.urban.rename('u'))
    .reduceRegion({reducer: ee.Reducer.mean(), geometry: region, scale: 10,
                   maxPixels: 1e9, tileScale: 4});
  return ee.Number(stats.get('d'));
}

// =============================================================================
// 6. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '410px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Sentinel-1 Building Damage (PWTT)', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Kahramanmaras earthquake 2023-02-06 | Antakya, Hatay', {fontSize: '12px', color: '#555'}));
panel.add(ui.Label('Pixel-wise t-test, Ballinger (2025, RSE)', {fontSize: '12px', color: '#555'}));

// Colour-bar legend
function colorBar(vis, title) {
  var bar = ui.Thumbnail({
    image: ee.Image.pixelLonLat().select(0),
    params: {bbox: [0, 0, 1, 0.1], dimensions: '300x12', format: 'png',
             min: 0, max: 1, palette: vis.palette},
    style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '20px'}
  });
  var labels = ui.Panel([
    ui.Label(String(vis.min), {margin: '2px 8px'}),
    ui.Label(String((vis.min + vis.max) / 2), {margin: '2px 8px', textAlign: 'center', stretch: 'horizontal'}),
    ui.Label(String(vis.max) + '+', {margin: '2px 8px'})
  ], ui.Panel.Layout.flow('horizontal'));
  return ui.Panel([ui.Label(title, {fontWeight: 'bold'}), bar, labels]);
}
panel.add(colorBar(tVis, 'PWTT T-statistic'));
panel.add(ui.Panel([
  ui.Label('', {backgroundColor: '#ff0000', padding: '8px', margin: '2px 6px 2px 8px'}),
  ui.Label('Damaged (T > ' + T_THRESHOLD + ')', {fontSize: '12px'})
], ui.Panel.Layout.flow('horizontal')));

var statsLabel = ui.Label('Computing area statistics and negative control...',
  {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Label('Results', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(statsLabel);

ee.Dictionary({
  orbits: res.orbits,
  shareAOI: damageShare(res, AOI),
  shareCTL: damageShare(ctl, CONTROL),
  nPre: res.nPre.reduceRegion({reducer: ee.Reducer.median(), geometry: AOI, scale: 50}).get('n_pre'),
  nPost: res.nPost.reduceRegion({reducer: ee.Reducer.median(), geometry: AOI, scale: 50}).get('n_post')
}).evaluate(function (r, err) {
  if (err) { statsLabel.setValue('Error: ' + err); return; }
  statsLabel.setValue(
    'Relative orbits used: ' + r.orbits.join(', ') +
    '\nMedian images per orbit: pre=' + r.nPre + ', post=' + r.nPost +
    '\n\nShare of built-up area flagged as damaged:' +
    '\n  Antakya (impacted):        ' + (100 * r.shareAOI).toFixed(1) + ' %' +
    '\n  Mersin (negative control): ' + (100 * r.shareCTL).toFixed(1) + ' %' +
    '\n  -> empirical false-positive rate ~ ' + (100 * r.shareCTL).toFixed(1) + ' %' +
    '\n     (signal-to-background ratio ' + (r.shareAOI / Math.max(r.shareCTL, 1e-4)).toFixed(1) + 'x)'
  );
});

// Histogram of T in impacted vs control built-up areas
var sampleA = res.T.rename('T').sample({region: AOI, scale: 10, numPixels: 3000, seed: 1, geometries: false})
  .map(function (f) { return f.set('city', 'Antakya (impacted)'); });
var sampleC = ctl.T.rename('T').sample({region: CONTROL, scale: 10, numPixels: 3000, seed: 1, geometries: false})
  .map(function (f) { return f.set('city', 'Mersin (control)'); });
var histChart = ui.Chart.feature.histogram({features: sampleA.merge(sampleC).filter(ee.Filter.notNull(['T'])),
  property: 'T', maxBuckets: 40})
  .setOptions({title: 'Distribution of T (built-up pixels, both cities pooled)',
    hAxis: {title: 'T-statistic'}, vAxis: {title: 'count'}, legend: {position: 'none'}, colors: ['#bd0026']});
panel.add(histChart);
var boxStats = ee.FeatureCollection([
  ee.Feature(null, {city: 'Antakya', p50: res.T.reduceRegion(ee.Reducer.median(), AOI, 20).get('T_statistic'),
                    p90: res.T.reduceRegion(ee.Reducer.percentile([90]), AOI, 20).get('T_statistic')}),
  ee.Feature(null, {city: 'Mersin', p50: ctl.T.reduceRegion(ee.Reducer.median(), CONTROL, 20).get('T_statistic'),
                    p90: ctl.T.reduceRegion(ee.Reducer.percentile([90]), CONTROL, 20).get('T_statistic')})
]);
panel.add(ui.Chart.feature.byFeature(boxStats, 'city', ['p50', 'p90']).setChartType('ColumnChart')
  .setOptions({title: 'Median and 90th percentile of T: impacted vs control',
    vAxis: {title: 'T'}, colors: ['#fd8d3c', '#bd0026'],
    series: {0: {labelInLegend: 'median'}, 1: {labelInLegend: '90th pct'}}}));

// Building-level statistics on demand (can be slow for very large AOIs)
var bLabel = ui.Label('', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Button({
  label: 'Compute building-level damage statistics',
  onClick: function () {
    bLabel.setValue('Aggregating T to Microsoft building footprints...');
    var withT = buildingStats();
    Map.addLayer(withT.filter(ee.Filter.eq('damaged', 1)).style({color: 'ff0000', fillColor: 'ff000066', width: 1}),
      {}, 'Damaged buildings (mean T > ' + T_THRESHOLD + ')');
    ee.Dictionary({n: withT.size(), d: withT.filter(ee.Filter.eq('damaged', 1)).size()})
      .evaluate(function (r, err) {
        if (err) { bLabel.setValue('Error: ' + err + '\nUse the Export task instead.'); return; }
        bLabel.setValue('Buildings analysed: ' + r.n + '\nBuildings flagged damaged: ' + r.d +
          ' (' + (100 * r.d / r.n).toFixed(1) + ' %)');
      });
  }
}));
panel.add(bLabel);

// =============================================================================
// 7. VALIDATION WITH UPLOADED REFERENCE (UNOSAT / Copernicus EMS) - OPTIONAL
// =============================================================================
var valLabel = ui.Label(REFERENCE_ASSET === null ?
  'Reference validation: set REFERENCE_ASSET (UNOSAT/EMSR648 points)\nto compute detection rate, ROC curve and AUC.' :
  'Computing ROC against reference data...', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Label('Validation with reference damage data', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(valLabel);

if (REFERENCE_ASSET !== null) {
  var ref = ee.FeatureCollection(REFERENCE_ASSET).filterBounds(AOI);
  var refLabeled = ref.map(function (f) {
    return f.set('label', ee.Algorithms.If(ee.List(DAMAGED_VALUES).contains(f.get(DAMAGE_PROPERTY)), 1, 0));
  });
  var positives = res.T.unmask(0).rename('T').sampleRegions({
    collection: refLabeled.filter(ee.Filter.eq('label', 1)), properties: ['label'], scale: 10, tileScale: 4});
  var refNeg = res.T.unmask(0).rename('T').sampleRegions({
    collection: refLabeled.filter(ee.Filter.eq('label', 0)), properties: ['label'], scale: 10, tileScale: 4});
  // Negatives: undamaged reference points if present, else control-city built-up pixels
  var ctlNeg = ctl.T.unmask(0).updateMask(ctl.urban).rename('T')
    .sample({region: CONTROL, scale: 10, numPixels: 5000, seed: 7, geometries: false})
    .map(function (f) { return f.set('label', 0); });
  var negatives = ee.FeatureCollection(ee.Algorithms.If(refNeg.size().gt(20), refNeg, ctlNeg));

  var nP = positives.size(), nN = negatives.size();
  var thresholds = ee.List.sequence(0, 10, 0.25);
  var roc = ee.FeatureCollection(thresholds.map(function (th) {
    var tpr = positives.filter(ee.Filter.gt('T', th)).size().divide(nP);
    var fpr = negatives.filter(ee.Filter.gt('T', th)).size().divide(nN);
    return ee.Feature(null, {threshold: th, TPR: tpr, FPR: fpr});
  }));
  panel.add(ui.Chart.feature.byFeature(roc.sort('FPR'), 'FPR', ['TPR']).setChartType('LineChart')
    .setOptions({title: 'ROC curve (reference damaged points vs negatives)',
      hAxis: {title: 'False positive rate', viewWindow: {min: 0, max: 1}},
      vAxis: {title: 'True positive rate', viewWindow: {min: 0, max: 1}},
      legend: {position: 'none'}, pointSize: 3, colors: ['#bd0026']}));
  ee.Dictionary({roc: roc.sort('FPR').reduceColumns(ee.Reducer.toList(2), ['FPR', 'TPR']).get('list'),
    nP: nP, nN: nN,
    det: positives.filter(ee.Filter.gt('T', T_THRESHOLD)).size().divide(nP),
    fa: negatives.filter(ee.Filter.gt('T', T_THRESHOLD)).size().divide(nN)})
    .evaluate(function (r, err) {
      if (err) { valLabel.setValue('Error: ' + err); return; }
      var pts = r.roc.concat([[1, 1]]); pts.unshift([0, 0]);
      var auc = 0;
      for (var i = 1; i < pts.length; i++) {
        auc += (pts[i][0] - pts[i - 1][0]) * (pts[i][1] + pts[i - 1][1]) / 2;
      }
      valLabel.setValue('Damaged reference points: ' + r.nP + ' | negatives: ' + r.nN +
        '\nDetection rate at T>' + T_THRESHOLD + ': ' + (100 * r.det).toFixed(1) + ' %' +
        '\nFalse-alarm rate at T>' + T_THRESHOLD + ': ' + (100 * r.fa).toFixed(1) + ' %' +
        '\nAUC: ' + auc.toFixed(3) + '  (published PWTT: 0.81-0.88)');
    });
}

// =============================================================================
// 8. EXPORTS
// =============================================================================
Export.image.toDrive({image: res.T.addBands(res.damage.toFloat()), description: 'PWTT_Antakya_2023',
  region: AOI, scale: 10, maxPixels: 1e10});
Export.table.toDrive({collection: buildingStats(), description: 'PWTT_Antakya_buildings',
  fileFormat: 'GeoJSON'});
