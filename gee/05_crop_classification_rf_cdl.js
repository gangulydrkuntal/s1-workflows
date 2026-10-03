/*******************************************************************************
 * USE CASE 5 - CROP-TYPE MAPPING FROM DENSE SENTINEL-1 TIME SERIES WITH
 *              RANDOM FOREST, VALIDATED AGAINST THE USDA CROPLAND DATA LAYER
 *
 * Test site  : Red River Valley (Grand Forks, North Dakota / Polk County,
 *              Minnesota, USA), season 2021 (Sentinel-1A + 1B, 6-day revisit).
 *              Very flat terrain (no terrain-induced radiometric distortion)
 *              and a diverse crop mix: corn, soybean, spring wheat, sugar beet,
 *              grass/hay. Reference: USDA NASS Cropland Data Layer 2021
 *              (USDA/NASS/CDL), with per-pixel confidence.
 *
 * Method (scientific basis)
 *   - Phenology-driven C-band backscatter dynamics of crops (VV, VH and the
 *     VH/VV cross-ratio are sensitive to canopy structure, biomass and water
 *     content): Veloso et al. (2017, RSE 199); Vreugdenhil et al. (2018,
 *     Remote Sens. 10); Van Tricht et al. (2018, Remote Sens. 10); Arias et al.
 *     (2020, Remote Sens. 12).
 *   - Random Forest (Breiman, 2001) is the most widely used and robust
 *     classifier for such multi-temporal features (Belgiu & Dragut, 2016,
 *     ISPRS J.; Inglada et al., 2016).
 *   Processing:
 *     1. GRD sigma0 (calibrated, noise removed, terrain corrected by SNAP);
 *        edge masking; conversion to linear power;
 *     2. incidence-angle normalisation gamma0 = sigma0 / cos(theta) (flat
 *        terrain, both orbit directions combined);
 *     3. 15-day mean composites (multi-temporal speckle reduction) + 3x3
 *        boxcar, April-October -> VV, VH and VH/VV ratio in dB (~42 features);
 *     4. training labels from CDL, restricted to high-confidence (>= 80) and
 *        homogeneous field-interior pixels (3x3 identical class) to limit label
 *        noise and mixed pixels;
 *     5. SPATIALLY BLOCKED train/test split (checkerboard of ~5 km blocks) to
 *        avoid optimistic accuracies from spatial autocorrelation
 *        (Roberts et al., 2017, Ecography; Ploton et al., 2020, Nat. Commun.);
 *     6. Random Forest (300 trees), accuracy assessment on held-out blocks:
 *        confusion matrix, OA, kappa, per-class producer's/user's accuracy and
 *        F1; variable importance; class temporal signatures.
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([-97.35, 47.70, -96.85, 48.05]);
var YEAR = 2021;
var SEASON_START = ee.Date.fromYMD(YEAR, 4, 1);
var N_PERIODS = 14;           // 14 x 15 days = 1 Apr - 28 Oct
var PERIOD_DAYS = 15;
var N_TREES = 300;
var POINTS_PER_CLASS = 700;
var BLOCK_DEG = 0.05;         // spatial block size for train/test split

var CLASSES = [
  {name: 'Other',              color: 'bdbdbd'},
  {name: 'Corn',               color: 'ffd300'},
  {name: 'Soybeans',           color: '267000'},
  {name: 'Spring wheat',       color: 'd8b56b'},
  {name: 'Sugarbeets',         color: 'a800e2'},
  {name: 'Grass/pasture/hay',  color: 'e8ffbf'},
  {name: 'Forest/wetland',     color: '7cafaf'},
  {name: 'Developed',          color: '9c9c9c'},
  {name: 'Water',              color: '4970a3'}
];
var CLASS_NAMES = CLASSES.map(function (c) { return c.name; });
var PALETTE = CLASSES.map(function (c) { return c.color; });

Map.centerObject(AOI, 11);
Map.setOptions('HYBRID');

// =============================================================================
// 1. REFERENCE LABELS FROM CDL
// =============================================================================
var cdlImg = ee.ImageCollection('USDA/NASS/CDL').filterDate(YEAR + '-01-01', (YEAR + 1) + '-01-01').first();
var cdl = cdlImg.select('cropland');
var conf = cdlImg.select('confidence');
var cdlCodes = [1, 5, 23, 41, 36, 37, 176, 141, 142, 143, 190, 195, 121, 122, 123, 124, 111];
var newCodes = [1, 2, 3, 4, 5, 5, 5, 6, 6, 6, 6, 6, 7, 7, 7, 7, 8];
var label = cdl.remap(cdlCodes, newCodes, 0).rename('class');
var homogeneous = label.focalMin(1, 'square', 'pixels').eq(label.focalMax(1, 'square', 'pixels'));
var trainable = conf.gte(80).and(homogeneous);

// =============================================================================
// 2. SENTINEL-1 FEATURES
// =============================================================================
function prep(img) {
  var angle = img.select('angle');
  var lin = ee.Image(10).pow(img.select(['VV', 'VH']).divide(10));
  var gamma0 = lin.divide(angle.multiply(Math.PI / 180).cos());
  return gamma0.updateMask(angle.gt(30.5).and(angle.lt(45.5)))
    .copyProperties(img, ['system:time_start']);
}

var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(AOI)
  .filterDate(SEASON_START, SEASON_START.advance(N_PERIODS * PERIOD_DAYS, 'day'))
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .map(function (img) { return ee.Image(prep(img)); });

var s1Proj = ee.Image(ee.ImageCollection('COPERNICUS/S1_GRD').filterBounds(AOI)
  .filterDate(SEASON_START, SEASON_START.advance(30, 'day')).first()).select('VV').projection();
var periods = ee.List.sequence(0, N_PERIODS - 1);
var composites = ee.ImageCollection(periods.map(function (p) {
  p = ee.Number(p);
  var start = SEASON_START.advance(p.multiply(PERIOD_DAYS), 'day');
  var comp = s1.filterDate(start, start.advance(PERIOD_DAYS, 'day')).mean()
    .setDefaultProjection(s1Proj).focalMean(1, 'square', 'pixels');
  var vv = comp.select('VV').log10().multiply(10);
  var vh = comp.select('VH').log10().multiply(10);
  var ratio = vh.subtract(vv);
  var tag = ee.String('p').cat(p.format('%02d'));
  return ee.Image.cat(vv, vh, ratio)
    .rename([tag.cat('_VV'), tag.cat('_VH'), tag.cat('_RATIO')])
    .set('system:time_start', start.millis());
}));
var features = composites.toBands();
// toBands prefixes '<index>_'; strip it for readability
var featNames = features.bandNames().map(function (b) {
  return ee.String(b).replace('^[0-9]+_', '');
});
features = features.rename(featNames).clip(AOI);

// =============================================================================
// 3. SPATIALLY BLOCKED SAMPLING
// =============================================================================
var lonlat = ee.Image.pixelLonLat();
var fold = lonlat.select('longitude').divide(BLOCK_DEG).floor()
  .add(lonlat.select('latitude').divide(BLOCK_DEG).floor()).mod(2).abs().rename('fold');

var samples = features.addBands(label).addBands(fold).updateMask(trainable)
  .stratifiedSample({numPoints: POINTS_PER_CLASS, classBand: 'class', region: AOI,
    scale: 10, seed: 11, tileScale: 8, geometries: true})
  .filter(ee.Filter.notNull(featNames));
var train = samples.filter(ee.Filter.eq('fold', 0));
var test = samples.filter(ee.Filter.eq('fold', 1));

// =============================================================================
// 4. RANDOM FOREST
// =============================================================================
var rf = ee.Classifier.smileRandomForest({numberOfTrees: N_TREES, seed: 7})
  .train({features: train, classProperty: 'class', inputProperties: featNames});
var classified = features.classify(rf).rename('class');

var tested = test.classify(rf, 'predicted');
var cm = tested.errorMatrix('class', 'predicted', ee.List.sequence(0, CLASSES.length - 1));
var importance = ee.Dictionary(rf.explain().get('importance'));

// =============================================================================
// 5. MAP LAYERS
// =============================================================================
var vis = {min: 0, max: CLASSES.length - 1, palette: PALETTE};
Map.addLayer(features.select(['p05_VV', 'p08_VH', 'p11_RATIO']),
  {min: [-15, -24, -14], max: [-3, -12, -4]}, 'S1 RGB: VV(May-Jun) VH(Jul) ratio(Aug-Sep)', false);
Map.addLayer(label.clip(AOI), vis, 'Reference: CDL ' + YEAR + ' (remapped)', false);
Map.addLayer(trainable.selfMask().clip(AOI), {palette: ['ffffff']}, 'Eligible label pixels (conf>=80, interior)', false);
Map.addLayer(fold.clip(AOI), {min: 0, max: 1, palette: ['1f78b4', 'e31a1c'], opacity: 0.35},
  'Spatial blocks (blue = train, red = test)', false);
Map.addLayer(classified, vis, 'S1 Random Forest classification', true);
Map.addLayer(classified.neq(label).selfMask().clip(AOI), {palette: ['ff0000']}, 'Disagreement with CDL', false);
Map.addLayer(test.style({color: 'ff0000', pointSize: 2}), {}, 'Test samples', false);

// =============================================================================
// 6. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '440px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Sentinel-1 Crop-Type Classification', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Random Forest on 15-day VV/VH/ratio composites, ' + YEAR +
  ' | Red River Valley, USA', {fontSize: '12px', color: '#555'}));

panel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
CLASSES.forEach(function (c) {
  panel.add(ui.Panel([
    ui.Label('', {backgroundColor: '#' + c.color, padding: '8px', margin: '2px 6px 2px 0', border: '1px solid #999'}),
    ui.Label(c.name, {margin: '2px 0', fontSize: '12px'})
  ], ui.Panel.Layout.flow('horizontal')));
});

var accLabel = ui.Label('Training Random Forest and assessing accuracy on held-out blocks...',
  {whiteSpace: 'pre', fontSize: '12px', fontFamily: 'monospace'});
panel.add(ui.Label('Accuracy (independent spatial blocks vs CDL)', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(accLabel);

ee.Dictionary({
  matrix: cm.array(), oa: cm.accuracy(), kappa: cm.kappa(),
  pa: cm.producersAccuracy().project([0]), ua: cm.consumersAccuracy().project([1]),
  nTrain: train.size(), nTest: test.size()
}).evaluate(function (r, err) {
  if (err) { accLabel.setValue('Error: ' + err); return; }
  var txt = 'Train samples: ' + r.nTrain + ' | test samples: ' + r.nTest +
    '\nOverall accuracy: ' + (100 * r.oa).toFixed(1) + ' %   Kappa: ' + r.kappa.toFixed(3) +
    '\n\nClass               PA %   UA %   F1';
  var f1s = [];
  for (var i = 0; i < CLASSES.length; i++) {
    var pa = r.pa[i], ua = r.ua[i];
    var support = r.matrix[i].reduce(function (a, b) { return a + b; }, 0);
    if (support === 0) { f1s.push(null); continue; }
    var f1 = (pa + ua) > 0 ? 2 * pa * ua / (pa + ua) : 0;
    f1s.push(f1);
    var name = (CLASSES[i].name + '                    ').slice(0, 18);
    txt += '\n' + name + ' ' + ('     ' + (100 * pa).toFixed(1)).slice(-5) + '  ' +
      ('     ' + (100 * ua).toFixed(1)).slice(-5) + '  ' + f1.toFixed(2);
  }
  txt += '\n\nConfusion matrix (rows = CDL, cols = S1-RF):';
  r.matrix.forEach(function (row, i) {
    txt += '\n' + ('  ' + i).slice(-2) + ' | ' + row.map(function (v) { return ('    ' + v).slice(-4); }).join('');
  });
  txt += '\n\nNote: CDL is itself a classification (major-crop\naccuracies typically 85-97 %), so these figures\nmeasure agreement with the best available reference.';
  accLabel.setValue(txt);

  var f1Fc = ee.FeatureCollection(CLASSES.map(function (c, i) {
    return ee.Feature(null, {cls: c.name, F1: f1s[i] === null ? 0 : f1s[i]});
  }));
  f1Panel.add(ui.Chart.feature.byFeature(f1Fc, 'cls', ['F1']).setChartType('ColumnChart')
    .setOptions({title: 'Per-class F1 score (held-out blocks)', legend: {position: 'none'},
      vAxis: {viewWindow: {min: 0, max: 1}}, colors: ['#2c7fb8']}));
});
var f1Panel = ui.Panel();
panel.add(f1Panel);

// Variable importance (top 15)
var impFc = ee.FeatureCollection(importance.keys().map(function (k) {
  return ee.Feature(null, {feature: k, importance: importance.get(k)});
})).sort('importance', false).limit(15);
panel.add(ui.Chart.feature.byFeature(impFc, 'feature', ['importance']).setChartType('BarChart')
  .setOptions({title: 'Random Forest variable importance (top 15)', legend: {position: 'none'},
    colors: ['#984ea3']}));

// Temporal VH signatures per class (mean of training samples)
var vhNames = ee.List.sequence(0, N_PERIODS - 1).map(function (p) {
  return ee.String('p').cat(ee.Number(p).format('%02d')).cat('_VH');
});
var sigFc = ee.FeatureCollection(ee.List.sequence(0, N_PERIODS - 1).map(function (p) {
  p = ee.Number(p);
  var band = ee.String(vhNames.get(p));
  var props = ee.Dictionary.fromLists(
    ee.List(CLASS_NAMES).slice(1, 6),
    ee.List.sequence(1, 5).map(function (c) {
      return train.filter(ee.Filter.eq('class', c)).aggregate_mean(band);
    }));
  return ee.Feature(null, props.set('doy', SEASON_START.advance(p.multiply(PERIOD_DAYS).add(7), 'day').format('MM-dd')));
}));
panel.add(ui.Chart.feature.byFeature(sigFc, 'doy', CLASS_NAMES.slice(1, 6)).setChartType('LineChart')
  .setOptions({title: 'Mean VH backscatter trajectories (training samples)', hAxis: {title: 'Composite centre date'},
    vAxis: {title: 'VH gamma0 (dB)'}, pointSize: 3, lineWidth: 2, colors: PALETTE.slice(1, 6).map(function (c) {
      return c === 'e8ffbf' ? '#7fbf3f' : '#' + c; })}));

// Click to plot the pixel VH/VV time series
panel.add(ui.Label('Click on the map for the pixel VH and VH/VV trajectory.', {fontSize: '12px', color: '#555'}));
var clickPanel = ui.Panel();
panel.add(clickPanel);
Map.onClick(function (c) {
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  var ts = composites.map(function (img) {
    return ee.Image(img).select([1, 2], ['VH', 'VH_VV_ratio']);
  });
  clickPanel.clear();
  clickPanel.add(ui.Chart.image.series({imageCollection: ts, region: pt, reducer: ee.Reducer.mean(), scale: 10})
    .setOptions({title: 'Pixel trajectory (dB)', pointSize: 3, colors: ['#d95f02', '#7570b3']}));
});

Export.image.toDrive({image: classified.toByte(), description: 'S1_RF_crops_RedRiver_' + YEAR,
  region: AOI, scale: 10, maxPixels: 1e10});
