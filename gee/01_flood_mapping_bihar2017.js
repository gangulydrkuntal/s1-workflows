/*******************************************************************************
 * USE CASE 1 - FLOOD INUNDATION MAPPING WITH SENTINEL-1 (CHANGE DETECTION +
 *              EDGE-BASED OTSU THRESHOLDING) AND VALIDATION AGAINST THE
 *              GLOBAL FLOOD DATABASE
 *
 * Test event : North Bihar (India) monsoon flood, August 2017 (Bagmati / Kamla /
 *              Adhwara basins - Darbhanga, Sitamarhi, Madhubani districts).
 *              Dartmouth Flood Observatory event DFO-4507, which is mapped in
 *              the Global Flood Database (Tellman et al., 2021, Nature) and is
 *              therefore an independent reference available inside GEE.
 *
 * Method (scientific basis)
 *   1. Pre-processing of the GEE COPERNICUS/S1_GRD product (already thermal-noise
 *      corrected, radiometrically calibrated to sigma0 and terrain corrected with
 *      SRTM-30 by ESA SNAP, see GEE catalog) following the ARD recipe of
 *      Mullissa et al. (2021, Remote Sensing 13, 1954):
 *        - additional border-noise / swath-edge masking via incidence angle;
 *        - speckle filtering in the LINEAR power domain with the Refined Lee
 *          filter (Lee, 1981; Lee et al., 1999) - implementation after
 *          G. Lemoine / Mullissa et al. (2021).
 *   2. Bi-temporal change detection on the log-ratio (dB difference) image
 *      (Bazi et al., 2005; Bovolo & Bruzzone, 2005), pre and post images taken
 *      from the SAME relative orbit to keep the imaging geometry identical
 *      (Twele et al., 2016; UN-SPIDER Recommended Practice).
 *   3. Automatic water threshold with Otsu's method applied on a bimodal sample
 *      drawn around Canny-detected land/water edges (Donchyts et al., 2016;
 *      Markert et al., 2020, Remote Sensing 12, 2469) - avoids the failure of a
 *      global Otsu on a non-bimodal scene histogram.
 *   4. Contextual exclusion masks: permanent water (JRC GSW seasonality >= 10
 *      months, Pekel et al., 2016), Height Above Nearest Drainage > 15 m
 *      (MERIT-Hydro, Yamazaki et al., 2019; Twele et al., 2016) and slope > 5 deg;
 *      minimum mapping unit by connected-pixel count.
 *   5. Validation: agreement with the Global Flood Database (MODIS, 250 m)
 *      event map on the GFD grid, restricted to cloud-free MODIS observations
 *      -> confusion matrix, OA, Cohen's kappa, precision, recall, F1, IoU.
 *   6. Impact: flooded area per land-cover class (ESA WorldCover) and exposed
 *      population (GHSL GHS-POP 2015).
 *
 * How to run: paste into the GEE Code Editor and press "Run". All datasets are
 * public catalog assets; no upload is needed.
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([85.70, 25.95, 86.45, 26.55]);   // North Bihar
var PRE_START = '2017-01-15', PRE_END = '2017-03-31';  // dry season (rabi crops), no flooding
var POST_START = '2017-08-12', POST_END = '2017-08-27'; // flood peak window
var GFD_EVENT_ID = 4507;            // Dartmouth Flood Observatory ID in the GFD
var DIFF_THRESHOLD_DB = -3;         // minimum backscatter decrease (dB) to call "change"
var HAND_MAX = 15;                  // m, Height Above Nearest Drainage cut-off
var SLOPE_MAX = 5;                  // degrees
var MMU_PIXELS = 10;                // minimum mapping unit (10 m pixels ~ 0.1 ha)
var INITIAL_WATER_DB = -16;         // only used to locate land/water edges for Otsu

Map.centerObject(AOI, 10);
Map.setOptions('HYBRID');

// =============================================================================
// 1. PRE-PROCESSING FUNCTIONS
// =============================================================================
function toLinear(imgDb) { return ee.Image(10).pow(imgDb.divide(10)); }
function toDb(imgLin) { return imgLin.log10().multiply(10); }

/** Remove low-quality swath edges (border noise / extreme incidence angles). */
function maskEdges(img) {
  var angle = img.select('angle');
  return img.updateMask(angle.gt(30.5).and(angle.lt(45.5)));
}

/** Refined Lee speckle filter for ONE band in linear power units. */
function refinedLee(img) {
  var weights3 = ee.List.repeat(ee.List.repeat(1, 3), 3);
  var kernel3 = ee.Kernel.fixed(3, 3, weights3, 1, 1, false);
  var mean3 = img.reduceNeighborhood(ee.Reducer.mean(), kernel3);
  var variance3 = img.reduceNeighborhood(ee.Reducer.variance(), kernel3);

  var sampleWeights = ee.List([[0,0,0,0,0,0,0],[0,1,0,1,0,1,0],[0,0,0,0,0,0,0],
    [0,1,0,1,0,1,0],[0,0,0,0,0,0,0],[0,1,0,1,0,1,0],[0,0,0,0,0,0,0]]);
  var sampleKernel = ee.Kernel.fixed(7, 7, sampleWeights, 3, 3, false);
  var sampleMean = mean3.neighborhoodToBands(sampleKernel);
  var sampleVar = variance3.neighborhoodToBands(sampleKernel);

  var gradients = sampleMean.select(1).subtract(sampleMean.select(7)).abs();
  gradients = gradients.addBands(sampleMean.select(6).subtract(sampleMean.select(2)).abs());
  gradients = gradients.addBands(sampleMean.select(3).subtract(sampleMean.select(5)).abs());
  gradients = gradients.addBands(sampleMean.select(0).subtract(sampleMean.select(8)).abs());
  var maxGradient = gradients.reduce(ee.Reducer.max());
  var gradmask = gradients.eq(maxGradient);
  gradmask = gradmask.addBands(gradmask);

  var directions = sampleMean.select(1).subtract(sampleMean.select(4))
    .gt(sampleMean.select(4).subtract(sampleMean.select(7))).multiply(1);
  directions = directions.addBands(sampleMean.select(6).subtract(sampleMean.select(4))
    .gt(sampleMean.select(4).subtract(sampleMean.select(2))).multiply(2));
  directions = directions.addBands(sampleMean.select(3).subtract(sampleMean.select(4))
    .gt(sampleMean.select(4).subtract(sampleMean.select(5))).multiply(3));
  directions = directions.addBands(sampleMean.select(0).subtract(sampleMean.select(4))
    .gt(sampleMean.select(4).subtract(sampleMean.select(8))).multiply(4));
  directions = directions.addBands(directions.select(0).not().multiply(5));
  directions = directions.addBands(directions.select(1).not().multiply(6));
  directions = directions.addBands(directions.select(2).not().multiply(7));
  directions = directions.addBands(directions.select(3).not().multiply(8));
  directions = directions.updateMask(gradmask);
  directions = directions.reduce(ee.Reducer.sum());

  var sampleStats = sampleVar.divide(sampleMean.multiply(sampleMean));
  var sigmaV = sampleStats.toArray().arraySort().arraySlice(0, 0, 5)
    .arrayReduce(ee.Reducer.mean(), [0]);

  var rectWeights = ee.List.repeat(ee.List.repeat(0, 7), 3)
    .cat(ee.List.repeat(ee.List.repeat(1, 7), 4));
  var diagWeights = ee.List([[1,0,0,0,0,0,0],[1,1,0,0,0,0,0],[1,1,1,0,0,0,0],
    [1,1,1,1,0,0,0],[1,1,1,1,1,0,0],[1,1,1,1,1,1,0],[1,1,1,1,1,1,1]]);
  var rectKernel = ee.Kernel.fixed(7, 7, rectWeights, 3, 3, false);
  var diagKernel = ee.Kernel.fixed(7, 7, diagWeights, 3, 3, false);

  var dirMean = img.reduceNeighborhood(ee.Reducer.mean(), rectKernel).updateMask(directions.eq(1));
  var dirVar = img.reduceNeighborhood(ee.Reducer.variance(), rectKernel).updateMask(directions.eq(1));
  dirMean = dirMean.addBands(img.reduceNeighborhood(ee.Reducer.mean(), diagKernel).updateMask(directions.eq(2)));
  dirVar = dirVar.addBands(img.reduceNeighborhood(ee.Reducer.variance(), diagKernel).updateMask(directions.eq(2)));
  for (var i = 1; i < 4; i++) {
    dirMean = dirMean.addBands(img.reduceNeighborhood(ee.Reducer.mean(), rectKernel.rotate(i))
      .updateMask(directions.eq(2 * i + 1)));
    dirVar = dirVar.addBands(img.reduceNeighborhood(ee.Reducer.variance(), rectKernel.rotate(i))
      .updateMask(directions.eq(2 * i + 1)));
    dirMean = dirMean.addBands(img.reduceNeighborhood(ee.Reducer.mean(), diagKernel.rotate(i))
      .updateMask(directions.eq(2 * i + 2)));
    dirVar = dirVar.addBands(img.reduceNeighborhood(ee.Reducer.variance(), diagKernel.rotate(i))
      .updateMask(directions.eq(2 * i + 2)));
  }
  dirMean = dirMean.reduce(ee.Reducer.sum());
  dirVar = dirVar.reduce(ee.Reducer.sum());

  var varX = dirVar.subtract(dirMean.multiply(dirMean).multiply(sigmaV)).divide(sigmaV.add(1.0));
  var b = varX.divide(dirVar);
  var result = dirMean.add(b.multiply(img.subtract(dirMean)));
  return result.arrayProject([0]).arrayFlatten([['sum']]).float();
}

/** Apply the Refined Lee filter to every band of a linear-power image. */
function refinedLeeMulti(imgLin) {
  var bands = imgLin.bandNames();
  var filtered = ee.ImageCollection(bands.map(function (b) {
    return refinedLee(imgLin.select([b])).rename([b]);
  })).toBands().rename(bands);
  return filtered;
}

// =============================================================================
// 2. SENTINEL-1 DATA SELECTION (same relative orbit pre/post)
// =============================================================================
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(AOI)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.eq('resolution_meters', 10))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .map(maskEdges);

var postAll = s1.filterDate(POST_START, POST_END);
// Relative orbit with most post-event acquisitions over the AOI
var orbit = ee.Number(postAll.aggregate_array('relativeOrbitNumber_start')
  .reduce(ee.Reducer.mode()));
var postCol = postAll.filter(ee.Filter.eq('relativeOrbitNumber_start', orbit));
var preCol = s1.filterDate(PRE_START, PRE_END)
  .filter(ee.Filter.eq('relativeOrbitNumber_start', orbit));

// Native 10 m projection (composites otherwise default to 1-degree WGS84)
var s1Proj = ee.Image(postCol.first()).select('VV').projection();

// Pre-event reference: temporal median (robust, reduces speckle), then Refined Lee.
var preLin = toLinear(preCol.select(['VV', 'VH']).median()).setDefaultProjection(s1Proj);
// Post-event: per-image Refined Lee, then per-pixel minimum (= maximum observed
// flood extent within the window, consistent with the GFD "maximum extent").
var postLin = postCol.select(['VV', 'VH']).map(function (img) {
  return refinedLeeMulti(toLinear(img).setDefaultProjection(s1Proj));
}).min().setDefaultProjection(s1Proj);

var preDb = toDb(refinedLeeMulti(preLin)).clip(AOI);
var postDb = toDb(postLin).clip(AOI);
var diffDb = postDb.subtract(preDb).rename(['dVV', 'dVH']);   // log-ratio image

// =============================================================================
// 3. EDGE-BASED OTSU THRESHOLD (Donchyts et al., 2016; Markert et al., 2020)
// =============================================================================
function otsu(histogram) {
  histogram = ee.Dictionary(histogram);
  var counts = ee.Array(histogram.get('histogram'));
  var means = ee.Array(histogram.get('bucketMeans'));
  var size = means.length().get([0]);
  var total = counts.reduce(ee.Reducer.sum(), [0]).get([0]);
  var sum = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0]);
  var mean = sum.divide(total);
  var indices = ee.List.sequence(1, size.subtract(1));
  // Between-class sum of squares for every candidate split
  var bss = indices.map(function (i) {
    var aCounts = counts.slice(0, 0, i);
    var aCount = aCounts.reduce(ee.Reducer.sum(), [0]).get([0]);
    var aMeans = means.slice(0, 0, i);
    var aMean = aMeans.multiply(aCounts).reduce(ee.Reducer.sum(), [0]).get([0]).divide(aCount);
    var bCount = total.subtract(aCount);
    var bMean = sum.subtract(aCount.multiply(aMean)).divide(bCount);
    return aCount.multiply(aMean.subtract(mean).pow(2))
      .add(bCount.multiply(bMean.subtract(mean).pow(2)));
  });
  return ee.Number(ee.List(means.toList()).get(ee.List(bss).indexOf(ee.List(bss).reduce(ee.Reducer.max()))));
}

var vv = postDb.select('VV');
var binary = vv.lt(INITIAL_WATER_DB).rename('binary');
var canny = ee.Algorithms.CannyEdgeDetector({image: binary, threshold: 1, sigma: 1});
var longEdges = canny.gt(0).selfMask().connectedPixelCount(50, true).gte(20);
var edgeBuffer = longEdges.unmask(0).focalMax(150, 'square', 'meters');
var histSample = vv.updateMask(edgeBuffer);

var histDict = histSample.reduceRegion({
  reducer: ee.Reducer.histogram({maxBuckets: 255, minBucketWidth: 0.1}),
  geometry: AOI, scale: 30, maxPixels: 1e10, tileScale: 4
});
var waterThreshold = ee.Number(ee.Algorithms.If(
  histDict.get('VV'), otsu(histDict.get('VV')), INITIAL_WATER_DB))
  .max(-26).min(-12);                                   // physically plausible range

// =============================================================================
// 4. FLOOD CLASSIFICATION + CONTEXTUAL MASKS
// =============================================================================
var gsw = ee.Image('JRC/GSW1_4/GlobalSurfaceWater');
var permanentWater = gsw.select('seasonality').gte(10).unmask(0);
var hand = ee.Image('MERIT/Hydro/v1_0_1').select('hnd');
var slope = ee.Terrain.slope(ee.Image('USGS/SRTMGL1_003'));

var openWaterPost = vv.lt(ee.Image.constant(waterThreshold));
var decrease = diffDb.select('dVV').lt(DIFF_THRESHOLD_DB);

var floodRaw = openWaterPost.and(decrease)
  .and(permanentWater.not())
  .and(hand.lt(HAND_MAX))
  .and(slope.lt(SLOPE_MAX));
var flood = floodRaw.updateMask(floodRaw.connectedPixelCount(100, true).gte(MMU_PIXELS))
  .selfMask().rename('flood').setDefaultProjection(s1Proj);

// =============================================================================
// 5. AREA, LAND COVER AND POPULATION STATISTICS
// =============================================================================
var pixelAreaKm2 = ee.Image.pixelArea().divide(1e6);
var floodAreaKm2 = ee.Number(pixelAreaKm2.updateMask(flood).reduceRegion({
  reducer: ee.Reducer.sum(), geometry: AOI, scale: 20, maxPixels: 1e10, tileScale: 4
}).get('area'));

var worldCover = ee.ImageCollection('ESA/WorldCover/v100').first().select('Map');
var lcNames = ee.Dictionary({'10': 'Tree cover', '20': 'Shrubland', '30': 'Grassland',
  '40': 'Cropland', '50': 'Built-up', '60': 'Bare', '70': 'Snow/ice', '80': 'Water',
  '90': 'Herb. wetland', '95': 'Mangroves', '100': 'Moss/lichen'});
var lcStats = pixelAreaKm2.updateMask(flood).addBands(worldCover).reduceRegion({
  reducer: ee.Reducer.sum().group({groupField: 1, groupName: 'lc'}),
  geometry: AOI, scale: 20, maxPixels: 1e10, tileScale: 4
});
var lcFc = ee.FeatureCollection(ee.List(lcStats.get('groups')).map(function (g) {
  g = ee.Dictionary(g);
  var code = ee.Number(g.get('lc')).format('%d');
  return ee.Feature(null, {lc: lcNames.get(code, code), area_km2: g.get('sum')});
}));

var pop = ee.Image('JRC/GHSL/P2023A/GHS_POP/2015').select('population_count');
var floodFracPop = flood.unmask(0).reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 1024})
  .reproject(pop.projection());
var exposedPop = ee.Number(pop.multiply(floodFracPop).reduceRegion({
  reducer: ee.Reducer.sum(), geometry: AOI, crs: pop.projection(),
  scale: pop.projection().nominalScale(), maxPixels: 1e10, tileScale: 4
}).get('population_count'));

// =============================================================================
// 6. VALIDATION AGAINST THE GLOBAL FLOOD DATABASE (independent, MODIS-based)
// =============================================================================
var gfdCol = ee.ImageCollection('GLOBAL_FLOOD_DB/MODIS_EVENTS/V1');
var gfdById = gfdCol.filter(ee.Filter.eq('id', GFD_EVENT_ID));
var gfdEvent = ee.Image(ee.Algorithms.If(gfdById.size().gt(0), gfdById.first(),
  gfdCol.filterBounds(AOI).filterDate('2017-07-01', '2017-09-30').first()));
var gfdProj = gfdEvent.select('flooded').projection();

var gfdRef = gfdEvent.select('flooded').and(gfdEvent.select('jrc_perm_water').not());
var gfdClear = gfdEvent.select('clear_views').gte(1);     // MODIS actually saw the ground

// S1 flood fraction on the 250 m GFD grid (majority rule), only where S1 valid
var s1Valid = postDb.select('VV').mask().and(preDb.select('VV').mask());
var s1Frac = flood.unmask(0).updateMask(s1Valid)
  .reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 1024})
  .reproject(gfdProj);
var s1Bin = s1Frac.gte(0.5);
// code: 0 = TN, 1 = FP, 2 = FN, 3 = TP
var codes = gfdRef.multiply(2).add(s1Bin).updateMask(gfdClear).updateMask(s1Frac.mask())
  .toInt().rename('code');
var freq = ee.Dictionary(codes.reduceRegion({
  reducer: ee.Reducer.frequencyHistogram(), geometry: AOI, crs: gfdProj,
  scale: gfdProj.nominalScale(), maxPixels: 1e10, tileScale: 4
}).get('code'));

function metrics(freq) {
  var tn = ee.Number(freq.get('0', 0)), fp = ee.Number(freq.get('1', 0));
  var fn = ee.Number(freq.get('2', 0)), tp = ee.Number(freq.get('3', 0));
  var n = tn.add(fp).add(fn).add(tp);
  var oa = tp.add(tn).divide(n);
  var pe = tp.add(fp).multiply(tp.add(fn)).add(tn.add(fn).multiply(tn.add(fp))).divide(n.pow(2));
  var precision = tp.divide(tp.add(fp).max(1));
  var recall = tp.divide(tp.add(fn).max(1));
  return ee.Dictionary({
    TP: tp, FP: fp, FN: fn, TN: tn,
    OverallAccuracy: oa,
    Kappa: oa.subtract(pe).divide(ee.Number(1).subtract(pe)),
    Precision_UA: precision,
    Recall_PA: recall,
    F1: precision.multiply(recall).multiply(2).divide(precision.add(recall).max(1e-9)),
    IoU_CSI: tp.divide(tp.add(fp).add(fn).max(1))
  });
}
var valMetrics = metrics(freq);

// =============================================================================
// 7. MAP LAYERS
// =============================================================================
Map.addLayer(preDb.select('VV'), {min: -25, max: 0}, 'S1 VV pre-event (dB, Refined Lee)', false);
Map.addLayer(postDb.select('VV'), {min: -25, max: 0}, 'S1 VV post-event (dB, Refined Lee)', false);
Map.addLayer(ee.Image.cat(preDb.select('VV'), postDb.select('VV'), postDb.select('VV')),
  {min: -25, max: 0}, 'RGB change composite (R=pre, G=B=post)', true);
Map.addLayer(diffDb.select('dVV'), {min: -8, max: 8, palette: ['b2182b', 'f7f7f7', '2166ac']},
  'Log-ratio post-pre VV (dB)', false);
Map.addLayer(histSample.mask().selfMask(), {palette: ['ffff00']}, 'Otsu sampling zone (edge buffer)', false);
Map.addLayer(permanentWater.selfMask(), {palette: ['08306b']}, 'Permanent water (JRC GSW)', true);
Map.addLayer(gfdRef.selfMask(), {palette: ['ff7f00']}, 'Reference: Global Flood DB (MODIS, DFO 4507)', false);
Map.addLayer(flood, {palette: ['00e5ff']}, 'S1 flood extent (this study)', true);
Map.addLayer(codes, {min: 0, max: 3, palette: ['ffffff00', 'e41a1c', 'ff7f00', '4daf4a']},
  'Agreement vs GFD (FP red, FN orange, TP green)', false);
Map.addLayer(ee.Image().byte().paint(AOI, 1, 2), {palette: ['ffffff']}, 'AOI');

// =============================================================================
// 8. USER INTERFACE: legend, statistics, validation and charts
// =============================================================================
var panel = ui.Panel({style: {width: '400px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Sentinel-1 Flood Mapping', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('North Bihar flood, Aug 2017 | change detection + edge-based Otsu',
  {fontSize: '12px', color: '#555'}));

function legendRow(color, label) {
  return ui.Panel([
    ui.Label('', {backgroundColor: '#' + color, padding: '8px', margin: '2px 6px 2px 0'}),
    ui.Label(label, {margin: '2px 0', fontSize: '12px'})
  ], ui.Panel.Layout.flow('horizontal'));
}
panel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(legendRow('00e5ff', 'Flooded (S1, this study)'));
panel.add(legendRow('08306b', 'Permanent water (JRC GSW)'));
panel.add(legendRow('ff7f00', 'GFD reference flood (MODIS)'));
panel.add(legendRow('4daf4a', 'Agreement: true positive'));
panel.add(legendRow('e41a1c', 'Commission (S1 only)'));

var statsLabel = ui.Label('Computing statistics...', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Label('Results', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(statsLabel);

ee.Dictionary({
  orbit: orbit, nPre: preCol.size(), nPost: postCol.size(),
  threshold: waterThreshold, area: floodAreaKm2, pop: exposedPop,
  postDates: postCol.aggregate_array('system:time_start').map(function (t) {
    return ee.Date(t).format('YYYY-MM-dd');
  }),
  gfdBegan: ee.Algorithms.If(gfdEvent.get('system:time_start'),
    ee.Date(gfdEvent.get('system:time_start')).format('YYYY-MM-dd'), 'n/a'),
  gfdEnded: ee.Algorithms.If(gfdEvent.get('system:time_end'),
    ee.Date(gfdEvent.get('system:time_end')).format('YYYY-MM-dd'), 'n/a'),
  val: valMetrics
}).evaluate(function (r, err) {
  if (err) { statsLabel.setValue('Error: ' + err); return; }
  var v = r.val;
  statsLabel.setValue(
    'Relative orbit: ' + r.orbit + ' | pre images: ' + r.nPre + ' | post images: ' + r.nPost +
    '\nPost dates: ' + r.postDates.join(', ') +
    '\nOtsu water threshold (VV): ' + r.threshold.toFixed(2) + ' dB' +
    '\nFlooded area: ' + r.area.toFixed(1) + ' km2' +
    '\nExposed population (GHS-POP 2015): ' + Math.round(r.pop).toLocaleString() +
    '\n\nVALIDATION vs Global Flood Database' +
    '\n(GFD event ' + r.gfdBegan + ' to ' + r.gfdEnded + ', 250 m, cloud-free only)' +
    '\nTP=' + v.TP + '  FP=' + v.FP + '  FN=' + v.FN + '  TN=' + v.TN +
    '\nOverall accuracy: ' + (100 * v.OverallAccuracy).toFixed(1) + ' %' +
    '\nCohen kappa:      ' + v.Kappa.toFixed(3) +
    '\nPrecision (UA):   ' + (100 * v.Precision_UA).toFixed(1) + ' %' +
    '\nRecall (PA):      ' + (100 * v.Recall_PA).toFixed(1) + ' %' +
    '\nF1-score:         ' + v.F1.toFixed(3) +
    '\nIoU / CSI:        ' + v.IoU_CSI.toFixed(3) +
    '\n\nNote: GFD is a multi-day MODIS maximum extent (250 m)\nwhile S1 is a 10 m snapshot; under-detection of\nshallow vegetated flooding by C-band and MODIS\ncloud gaps both limit agreement.'
  );
});

// Histogram of post-event VV inside the Otsu sampling zone
var histChart = ui.Chart.image.histogram({
  image: histSample, region: AOI, scale: 30, maxBuckets: 120, maxPixels: 1e9
}).setOptions({
  title: 'Post-event VV histogram in edge-buffer (bimodal sample)',
  hAxis: {title: 'sigma0 VV (dB)'}, vAxis: {title: 'Pixel count'},
  legend: {position: 'none'}, colors: ['#1f78b4']
});
panel.add(histChart);

var lcChart = ui.Chart.feature.byFeature(lcFc, 'lc', ['area_km2'])
  .setChartType('ColumnChart')
  .setOptions({
    title: 'Flooded area by land cover (ESA WorldCover 2020)',
    hAxis: {title: 'Land-cover class'}, vAxis: {title: 'km2'},
    legend: {position: 'none'}, colors: ['#00a6c4']
  });
panel.add(lcChart);

// Click-to-inspect temporal backscatter profile
panel.add(ui.Label('Click on the map to plot the VV backscatter time series (2017).',
  {fontSize: '12px', color: '#555'}));
var clickPanel = ui.Panel();
panel.add(clickPanel);
Map.onClick(function (coords) {
  var pt = ee.Geometry.Point([coords.lon, coords.lat]);
  var ts = s1.filterDate('2017-01-01', '2017-12-31')
    .filter(ee.Filter.eq('relativeOrbitNumber_start', orbit)).select('VV');
  var chart = ui.Chart.image.series({imageCollection: ts, region: pt, reducer: ee.Reducer.mean(), scale: 10})
    .setOptions({title: 'VV backscatter at clicked point (same orbit)',
      vAxis: {title: 'dB'}, pointSize: 4, lineWidth: 1, colors: ['#08519c']});
  clickPanel.clear();
  clickPanel.add(chart);
});

// Optional export of the flood map
Export.image.toDrive({
  image: flood.unmask(0).toByte(), description: 'S1_flood_Bihar_2017', region: AOI,
  scale: 10, maxPixels: 1e10
});
