/*******************************************************************************
 * USE CASE 7 - AGRICULTURAL FIELD BOUNDARY DELINEATION FROM ALPHAEARTH
 *              FOUNDATIONS SATELLITE EMBEDDINGS (GOOGLE/SATELLITE_EMBEDDING)
 *
 * Data       : Google Satellite Embedding V1 (AlphaEarth Foundations, Brown et
 *              al., 2025, arXiv:2507.22291), CC-BY 4.0. One 64-D unit-length
 *              vector per 10 m pixel and calendar year, learned from
 *              Sentinel-2, Landsat, Sentinel-1, LiDAR, DEM, climate, etc. The
 *              vector summarises the pixel's whole annual trajectory, so two
 *              neighbouring fields with a different crop, sowing date or
 *              management have different embeddings even if they look alike
 *              on any single date. Distances on the unit sphere are
 *              meaningful (dot product = cosine similarity).
 *
 * Test sites : (default) UK_CAMBS - arable land between Cambridge and Ely,
 *              England, 2021. Reference in GEE: UKFields (Bancroft & Wilkins,
 *              2024, Zenodo 10.5281/zenodo.11110206), field polygons for the
 *              whole UK delineated with SAM on 2021 Sentinel-2 composites.
 *              (alt.) US_IOWA - Story County, Iowa, 2021. Reference in GEE:
 *              USDA NASS Crop Sequence Boundaries 2018-2025.
 *              Best reference for England (upload): RPA Land Parcels
 *              (derived from OS MasterMap) or RPA Parcel Points (OGL v3).
 *
 * Method (scientific basis)
 *   A. Boundary + extent "cutoff" delineation (Waldner & Diakogiannis, 2020,
 *      RSE 245, the post-processing used after FracTAL-ResUNet), with the
 *      learned boundary map replaced by an UNSUPERVISED edge map computed in
 *      embedding space:
 *        1. Multi-band Sobel gradient over all 64 embedding axes, combined by
 *           the L2 norm (Di Zenzo-type multispectral gradient) and scaled so
 *           that a step between unit vectors a and b gives the chord
 *           distance |a - b| (0 = identical, 2 = opposite; |a-b|^2 = 2-2cos).
 *        2. Boundary threshold chosen automatically with Otsu's method inside
 *           the agricultural extent (user can override with the slider).
 *        3. Agricultural extent = ESA WorldCover 2021 cropland + grassland.
 *        4. Field objects = 4-connected regions of (extent AND NOT boundary),
 *           vectorised, minimum size filter, then dilated by half a pixel
 *           band to close the boundary gap.
 *   B. Baseline: SNIC superpixels (Achanta & Susstrunk, 2017, CVPR) run
 *      directly on the 64-D embeddings.
 *
 * Validation (object-based and boundary-based)
 *   - For a random sample of reference fields fully inside the AOI: best-
 *     overlapping segment -> IoU, over-segmentation OS = 1 - |r n s| / |r|,
 *     under-segmentation US = 1 - |r n s| / |s| and the distance index
 *     D = sqrt((OS^2 + US^2) / 2) (Clinton et al., 2010, PE&RS 76(3));
 *     share of reference fields matched with IoU >= 0.5 (Persello et al.,
 *     2019, RSE 231; Waldner & Diakogiannis, 2020).
 *   - Boundary precision / recall / F1 with a +-20 m tolerance, evaluated only
 *     where the reference has coverage.
 *   - Optional: RPA Parcel Points -> share of segments containing exactly one
 *     parcel centroid (0 = spurious/fragment, >= 2 = merged fields).
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var SITE = 'UK_CAMBS';               // 'UK_CAMBS' or 'US_IOWA'
var SITES = {
  UK_CAMBS: {aoi: ee.Geometry.Rectangle([0.05, 52.30, 0.17, 52.38]), year: 2021,
    ref: 'projects/sat-io/open-datasets/UK-FIELDS',
    refName: 'UKFields 2021 (SAM on Sentinel-2; automated product)'},
  US_IOWA: {aoi: ee.Geometry.Rectangle([-93.70, 42.00, -93.58, 42.08]), year: 2021,
    ref: 'projects/nass-csb/assets/CSB1825_rev23/CSBIA1825',
    refName: 'USDA NASS Crop Sequence Boundaries 2018-2025'}
};
var REF_ASSET_OVERRIDE = null;   // e.g. 'users/<you>/RPA_land_parcels_cambs' (polygons)
var POINTS_ASSET = null;         // e.g. 'users/<you>/RPA_parcel_points_cambs' (OGL)
var MIN_FIELD_HA = 0.5;          // minimum object size
var BUFFER_M = 10;               // dilation closing the boundary band
var SNIC_SIZE = 25;              // SNIC seed spacing (pixels)
var SNIC_COMPACTNESS = 0.1;
var N_EVAL = 150;                // reference fields sampled for object metrics
var TOL_M = 20;                  // boundary tolerance (m)

var site = SITES[SITE];
var AOI = site.aoi;
var YEAR = site.year;
var REF_ASSET = REF_ASSET_OVERRIDE !== null ? REF_ASSET_OVERRIDE : site.ref;
var REF_NAME = REF_ASSET_OVERRIDE !== null ? 'Uploaded reference: ' + REF_ASSET_OVERRIDE : site.refName;
var MIN_PIX = Math.round(MIN_FIELD_HA * 100);   // 10 m pixels per ha = 100

Map.centerObject(AOI, 13);
Map.setOptions('SATELLITE');

// =============================================================================
// 1. EMBEDDINGS AND AGRICULTURAL EXTENT
// =============================================================================
var embCol = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
  .filterDate(YEAR + '-01-01', (YEAR + 1) + '-01-01')
  .filterBounds(AOI);
var embProj = ee.Image(embCol.first()).select('A00').projection();
var embFull = embCol.mosaic().setDefaultProjection(embProj);   // unclipped: no artificial edge at AOI border
var emb = embFull.clip(AOI);

var worldCover = ee.ImageCollection('ESA/WorldCover/v200').first().select('Map');
var extent = worldCover.eq(40).or(worldCover.eq(30)).rename('extent').clip(AOI);

// =============================================================================
// 2. EDGE STRENGTH IN EMBEDDING SPACE
// =============================================================================
var sobelX = ee.Kernel.fixed(3, 3, [[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], 1, 1, false);
var sobelY = ee.Kernel.fixed(3, 3, [[-1, -2, -1], [0, 0, 0], [1, 2, 1]], 1, 1, false);
var gx = embFull.convolve(sobelX), gy = embFull.convolve(sobelY);
// Sobel response to a step a->b is 4(b-a) per axis -> divide by 4 to get |a-b|
var grad = gx.pow(2).add(gy.pow(2)).reduce(ee.Reducer.sum()).sqrt().divide(4)
  .rename('grad').setDefaultProjection(embProj).clip(AOI);

// Otsu threshold inside the agricultural extent
function otsu(histogram) {
  histogram = ee.Dictionary(histogram);
  var counts = ee.Array(histogram.get('histogram'));
  var means = ee.Array(histogram.get('bucketMeans'));
  var size = means.length().get([0]);
  var total = counts.reduce(ee.Reducer.sum(), [0]).get([0]);
  var sum = means.multiply(counts).reduce(ee.Reducer.sum(), [0]).get([0]);
  var mean = sum.divide(total);
  var bss = ee.List.sequence(1, size.subtract(1)).map(function (i) {
    var aCounts = counts.slice(0, 0, i);
    var aCount = aCounts.reduce(ee.Reducer.sum(), [0]).get([0]);
    var aMean = means.slice(0, 0, i).multiply(aCounts).reduce(ee.Reducer.sum(), [0]).get([0]).divide(aCount);
    var bCount = total.subtract(aCount);
    var bMean = sum.subtract(aCount.multiply(aMean)).divide(bCount);
    return aCount.multiply(aMean.subtract(mean).pow(2)).add(bCount.multiply(bMean.subtract(mean).pow(2)));
  });
  return ee.Number(ee.List(means.toList()).get(ee.List(bss).indexOf(ee.List(bss).reduce(ee.Reducer.max()))));
}
var gradHist = grad.updateMask(extent).reduceRegion({
  reducer: ee.Reducer.histogram({maxBuckets: 255, minBucketWidth: 0.002}),
  geometry: AOI, crs: embProj, scale: 10, maxPixels: 1e10, tileScale: 4}).get('grad');
var otsuThr = otsu(gradHist);

// =============================================================================
// 3. DELINEATION FUNCTIONS
// =============================================================================
function toFields(binaryImg, label) {
  return binaryImg.selfMask().reduceToVectors({
    geometry: AOI, crs: embProj, scale: 10, geometryType: 'polygon', eightConnected: false,
    labelProperty: label, reducer: ee.Reducer.countEvery(), maxPixels: 1e10, tileScale: 4
  }).filter(ee.Filter.gte('count', MIN_PIX));
}

/** Method A: embedding edges + extent cutoff. thr = ee.Number threshold. */
function gradientFields(thr) {
  var boundary = grad.gt(ee.Image.constant(thr));
  boundary = boundary.updateMask(boundary.connectedPixelCount(25, true).gte(5)).unmask(0);
  var interior = extent.and(boundary.not()).rename('interior');
  var fields = toFields(interior, 'interior').map(function (f) {
    var g = f.geometry().buffer(BUFFER_M, 1);
    return ee.Feature(g, {area_ha: g.area(1).divide(1e4), method: 'embedding-edge'});
  });
  return {boundary: boundary.selfMask(), interior: interior, fields: fields};
}

/** Method B: SNIC superpixels on the 64-D embedding. */
var snic = ee.Algorithms.Image.Segmentation.SNIC({
  image: emb, size: SNIC_SIZE, compactness: SNIC_COMPACTNESS, connectivity: 8,
  neighborhoodSize: 2 * SNIC_SIZE,
  seeds: ee.Algorithms.Image.Segmentation.seedGrid(SNIC_SIZE)
}).select('clusters').reproject(embProj).updateMask(extent);
var snicFields = snic.reduceToVectors({
  geometry: AOI, crs: embProj, scale: 10, geometryType: 'polygon', eightConnected: false,
  labelProperty: 'cluster', reducer: ee.Reducer.countEvery(), maxPixels: 1e10, tileScale: 4
}).filter(ee.Filter.gte('count', MIN_PIX)).map(function (f) {
  return f.set({area_ha: f.geometry().area(1).divide(1e4), method: 'SNIC'});
});

// =============================================================================
// 4. VALIDATION FUNCTIONS
// =============================================================================
var refAll = ee.FeatureCollection(REF_ASSET).filterBounds(AOI);
var refEval = refAll.filter(ee.Filter.contains({leftValue: AOI, rightField: '.geo'}))
  .map(function (f) { return f.set('area_ha', f.geometry().area(1).divide(1e4)); })
  .filter(ee.Filter.gte('area_ha', MIN_FIELD_HA))
  .randomColumn('r', 1).sort('r').limit(N_EVAL);

/** Best-match object metrics for each reference field. */
function objectMetrics(segFc) {
  var per = refEval.map(function (r) {
    var g = r.geometry();
    var cands = segFc.filterBounds(g).map(function (s) {
      var sg = s.geometry();
      return s.set({inter: sg.intersection(g, ee.ErrorMargin(1)).area(1), sArea: sg.area(1)});
    });
    var best = ee.Feature(cands.sort('inter', false).first());
    var has = cands.size().gt(0);
    var inter = ee.Number(ee.Algorithms.If(has, best.get('inter'), 0));
    var sA = ee.Number(ee.Algorithms.If(has, best.get('sArea'), 1));
    var rA = g.area(1);
    var os = ee.Number(1).subtract(inter.divide(rA));
    var us = ee.Number(1).subtract(inter.divide(sA));
    return r.set({iou: inter.divide(rA.add(sA).subtract(inter)), os: os, us: us,
      D: os.pow(2).add(us.pow(2)).divide(2).sqrt(), n_overlapping: cands.size()});
  });
  return per;
}

function summarise(per) {
  return ee.Dictionary({
    n: per.size(),
    meanIoU: per.aggregate_mean('iou'),
    medianIoU: per.reduceColumns(ee.Reducer.median(), ['iou']).get('median'),
    matched50: per.filter(ee.Filter.gte('iou', 0.5)).size().divide(per.size()),
    OS: per.aggregate_mean('os'), US: per.aggregate_mean('us'), D: per.aggregate_mean('D')
  });
}

/** Boundary precision / recall / F1 within +-TOL_M, only where the reference has coverage. */
function boundaryF1(segFc) {
  var refEdge = ee.Image(0).byte().paint(refAll, 1, 1);
  var predEdge = ee.Image(0).byte().paint(segFc, 1, 1);
  var coverage = ee.Image(0).byte().paint(refAll, 1).focalMax(30, 'circle', 'meters');
  var refNear = refEdge.focalMax(TOL_M, 'circle', 'meters');
  var predNear = predEdge.focalMax(TOL_M, 'circle', 'meters');
  var p = refNear.rename('p').updateMask(predEdge).updateMask(coverage);
  var r = predNear.rename('r').updateMask(refEdge).updateMask(coverage);
  var s = p.addBands(r).reduceRegion({reducer: ee.Reducer.mean(), geometry: AOI, crs: embProj,
    scale: 10, maxPixels: 1e10, tileScale: 4});
  var prec = ee.Number(s.get('p')), rec = ee.Number(s.get('r'));
  return ee.Dictionary({precision: prec, recall: rec,
    F1: prec.multiply(rec).multiply(2).divide(prec.add(rec).max(1e-9))});
}

function pointMetrics(segFc) {
  var pts = ee.FeatureCollection(POINTS_ASSET).filterBounds(AOI);
  var joined = ee.Join.saveAll({matchesKey: 'pts', outer: true}).apply(segFc, pts,
    ee.Filter.intersects({leftField: '.geo', rightField: '.geo', maxError: 1}));
  var counted = ee.FeatureCollection(joined).map(function (f) {
    var l = ee.List(ee.Algorithms.If(f.get('pts'), f.get('pts'), []));
    return ee.Feature(null, {nPts: l.size()});
  });
  var n = counted.size();
  return ee.Dictionary({
    segments: n,
    exactlyOne: counted.filter(ee.Filter.eq('nPts', 1)).size().divide(n),
    none: counted.filter(ee.Filter.eq('nPts', 0)).size().divide(n),
    merged: counted.filter(ee.Filter.gte('nPts', 2)).size().divide(n)
  });
}

// =============================================================================
// 5. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '440px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Field Boundaries from AlphaEarth Embeddings', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Site: ' + SITE + ' | year ' + YEAR + ' | Google Satellite Embedding V1 (64-D, 10 m)',
  {fontSize: '12px', color: '#555'}));
panel.add(ui.Label('Reference: ' + REF_NAME, {fontSize: '11px', color: '#a00'}));

function legendRow(color, text, outline) {
  var sw = outline ? {border: '2px solid #' + color, padding: '6px', margin: '2px 6px 2px 8px'} :
    {backgroundColor: '#' + color, padding: '8px', margin: '2px 6px 2px 8px'};
  return ui.Panel([ui.Label('', sw), ui.Label(text, {fontSize: '12px', margin: '2px 0'})],
    ui.Panel.Layout.flow('horizontal'));
}
panel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(legendRow('ffff00', 'Fields: embedding-edge method', true));
panel.add(legendRow('00ffff', 'Fields: SNIC superpixel baseline', true));
panel.add(legendRow('ff00ff', 'Reference field boundaries', true));
panel.add(legendRow('ff3300', 'Embedding boundary pixels'));
var gradVis = {min: 0, max: 0.6, palette: ['000000', '3b0f70', '8c2981', 'de4968', 'fe9f6d', 'fcfdbf']};
panel.add(ui.Label('Edge strength (chord distance, 0-0.6+)', {fontSize: '12px', margin: '4px 8px'}));
panel.add(ui.Thumbnail({image: ee.Image.pixelLonLat().select(0),
  params: {bbox: [0, 0, 1, 0.1], dimensions: '300x12', format: 'png', min: 0, max: 1, palette: gradVis.palette},
  style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '20px'}}));

// Base layers
Map.addLayer(emb, {bands: ['A01', 'A16', 'A09'], min: -0.3, max: 0.3}, 'Embedding RGB (A01, A16, A09)', true);
Map.addLayer(grad, gradVis, 'Edge strength in embedding space', false);
Map.addLayer(extent.selfMask(), {palette: ['b8e186'], opacity: 0.4}, 'Agricultural extent (WorldCover 30/40)', false);
Map.addLayer(ee.Image().byte().paint(refAll, 1, 2), {palette: ['ff00ff']}, 'Reference boundaries', true);
Map.addLayer(snicFields.style({color: '00ffff', fillColor: '00000000', width: 1}), {}, 'SNIC fields (baseline)', false);

// Threshold controls
var thrLabel = ui.Label('Threshold: computing...', {fontSize: '12px'});
panel.add(ui.Label('Boundary threshold', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(thrLabel);
var slider = ui.Slider({min: 0.02, max: 0.6, step: 0.01, value: 0.15, style: {stretch: 'horizontal'}});
var useOtsu = ui.Checkbox({label: 'Use automatic Otsu threshold', value: true});
panel.add(useOtsu);
panel.add(slider);
var metricsLabel = ui.Label('', {whiteSpace: 'pre', fontSize: '12px', fontFamily: 'monospace'});
var chartsPanel = ui.Panel();

var layerRefs = {};
function setLayer(name, obj, vis, shown) {
  if (layerRefs[name]) { Map.layers().remove(layerRefs[name]); }
  layerRefs[name] = ui.Map.Layer(obj, vis, name, shown);
  Map.layers().add(layerRefs[name]);
}

function run() {
  var thr = useOtsu.getValue() ? otsuThr : ee.Number(slider.getValue());
  var res = gradientFields(thr);
  setLayer('Embedding boundary pixels', res.boundary, {palette: ['ff3300']}, false);
  setLayer('Fields: embedding-edge method', res.fields.style({color: 'ffff00', fillColor: '00000000', width: 1}), {}, true);

  metricsLabel.setValue('Delineating fields and computing metrics...');
  chartsPanel.clear();
  var perGrad = objectMetrics(res.fields);
  var perSnic = objectMetrics(snicFields);
  var out = {thr: thr, nGrad: res.fields.size(), nSnic: snicFields.size(), nRefAoi: refAll.size(),
    medGrad: res.fields.reduceColumns(ee.Reducer.median(), ['area_ha']).get('median'),
    medRef: refEval.reduceColumns(ee.Reducer.median(), ['area_ha']).get('median'),
    objGrad: summarise(perGrad), objSnic: summarise(perSnic),
    bGrad: boundaryF1(res.fields), bSnic: boundaryF1(snicFields)};
  if (POINTS_ASSET !== null) { out.ptGrad = pointMetrics(res.fields); out.ptSnic = pointMetrics(snicFields); }

  ee.Dictionary(out).evaluate(function (r, err) {
    if (err) { metricsLabel.setValue('Error: ' + err + '\nTry a smaller AOI or use the Export tasks.'); return; }
    function row(name, a, b, pct) {
      var f = function (v) { return pct ? (100 * v).toFixed(1) : v.toFixed(3); };
      return '\n' + (name + '                      ').slice(0, 22) + ('        ' + f(a)).slice(-8) + ('        ' + f(b)).slice(-8);
    }
    var t = 'Threshold used: ' + r.thr.toFixed(3) + ' (chord distance)' +
      '\nSegments: edge=' + r.nGrad + ', SNIC=' + r.nSnic + ' | reference fields in AOI: ' + r.nRefAoi +
      '\nMedian field size: edge ' + r.medGrad.toFixed(2) + ' ha, reference ' + r.medRef.toFixed(2) + ' ha' +
      '\n\nOBJECT METRICS (n=' + r.objGrad.n + ' ref fields)  edge    SNIC' +
      row('Mean IoU', r.objGrad.meanIoU, r.objSnic.meanIoU) +
      row('Median IoU', r.objGrad.medianIoU, r.objSnic.medianIoU) +
      row('Matched IoU>=0.5 (%)', r.objGrad.matched50, r.objSnic.matched50, true) +
      row('Over-segm. OS', r.objGrad.OS, r.objSnic.OS) +
      row('Under-segm. US', r.objGrad.US, r.objSnic.US) +
      row('Distance D (0=best)', r.objGrad.D, r.objSnic.D) +
      '\n\nBOUNDARY METRICS (tolerance +-' + TOL_M + ' m)' +
      row('Precision', r.bGrad.precision, r.bSnic.precision) +
      row('Recall', r.bGrad.recall, r.bSnic.recall) +
      row('Boundary F1', r.bGrad.F1, r.bSnic.F1);
    if (r.ptGrad) {
      t += '\n\nPARCEL-POINT CHECK (share of segments)' +
        row('Exactly 1 parcel (%)', r.ptGrad.exactlyOne, r.ptSnic.exactlyOne, true) +
        row('0 parcels (%)', r.ptGrad.none, r.ptSnic.none, true) +
        row('>=2 parcels merged (%)', r.ptGrad.merged, r.ptSnic.merged, true);
    }
    metricsLabel.setValue(t);
    thrLabel.setValue('Threshold in use: ' + r.thr.toFixed(3) + (useOtsu.getValue() ? ' (automatic Otsu)' : ' (slider)'));

    var cmp = ee.FeatureCollection([
      ee.Feature(null, {metric: 'Mean IoU', edge: r.objGrad.meanIoU, snic: r.objSnic.meanIoU}),
      ee.Feature(null, {metric: 'Matched (IoU>=0.5)', edge: r.objGrad.matched50, snic: r.objSnic.matched50}),
      ee.Feature(null, {metric: '1 - D', edge: 1 - r.objGrad.D, snic: 1 - r.objSnic.D}),
      ee.Feature(null, {metric: 'Boundary F1', edge: r.bGrad.F1, snic: r.bSnic.F1})
    ]);
    chartsPanel.add(ui.Chart.feature.byFeature(cmp, 'metric', ['edge', 'snic']).setChartType('ColumnChart')
      .setOptions({title: 'Embedding-edge vs SNIC baseline (higher = better)', vAxis: {viewWindow: {min: 0, max: 1}},
        colors: ['#c9b400', '#00a6c4'], series: {0: {labelInLegend: 'embedding-edge'}, 1: {labelInLegend: 'SNIC'}}}));
  });

  chartsPanel.add(ui.Chart.feature.histogram({features: perGrad, property: 'iou', minBucketWidth: 0.05})
    .setOptions({title: 'IoU of best-matching segment per reference field (edge method)',
      hAxis: {title: 'IoU', viewWindow: {min: 0, max: 1}}, legend: {position: 'none'}, colors: ['#c9b400']}));
  var sizes = res.fields.map(function (f) { return f.set('src', 'segments'); })
    .merge(refEval.map(function (f) { return f.set('src', 'reference'); }))
    .map(function (f) { return f.set('log10_ha', ee.Number(f.get('area_ha')).log10()); });
  chartsPanel.add(ui.Chart.feature.histogram({features: sizes.filter(ee.Filter.eq('src', 'segments')),
      property: 'log10_ha', minBucketWidth: 0.1})
    .setOptions({title: 'Segment size distribution, log10(ha)', legend: {position: 'none'}, colors: ['#c9b400']}));
  chartsPanel.add(ui.Chart.feature.histogram({features: sizes.filter(ee.Filter.eq('src', 'reference')),
      property: 'log10_ha', minBucketWidth: 0.1})
    .setOptions({title: 'Reference field size distribution (sample), log10(ha)', legend: {position: 'none'}, colors: ['#ff00ff']}));

  // Colour the evaluated reference fields by IoU
  setLayer('Reference fields coloured by IoU (edge method)',
    ee.Image().float().paint(perGrad, 'iou'), {min: 0, max: 1, palette: ['d7191c', 'fdae61', 'a6d96a', '1a9641']}, false);

  Export.table.toDrive({collection: res.fields, description: 'fields_embedding_edge_' + SITE + '_' + YEAR,
    fileFormat: 'GeoJSON'});
}

panel.add(ui.Button({label: 'Run / re-run delineation', onClick: run}));
panel.add(ui.Label('Validation', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(metricsLabel);
panel.add(chartsPanel);

// Click: cosine similarity of every pixel to the clicked pixel (how "field-like" it is)
panel.add(ui.Label('Click a field to map embedding similarity to that pixel.', {fontSize: '12px', color: '#555'}));
Map.onClick(function (c) {
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  var v = ee.Image.constant(ee.Dictionary(emb.reduceRegion({reducer: ee.Reducer.first(), geometry: pt,
    crs: embProj, scale: 10})).values(emb.bandNames()));
  var sim = emb.multiply(v).reduce(ee.Reducer.sum()).rename('cosine');
  setLayer('Cosine similarity to clicked pixel', sim, {min: 0.6, max: 1, palette: ['000000', '2c7fb8', 'ffffcc']}, true);
});

panel.add(ui.Label('Known challenges', {fontWeight: 'bold', margin: '10px 0 4px 0'}));
panel.add(ui.Label(
  '- 10 m pixels: hedges, ditches and tramlines narrower than a pixel are only seen through\n' +
  '  mixed pixels; fields < ~1 ha are poorly resolved.\n' +
  '- Annual embedding: adjacent fields with the same crop and management have near-\n' +
  '  identical vectors and merge (under-segmentation), unless a hedge/road separates them.\n' +
  '- Within-field variation (wet patches, soils, trees) can create spurious internal edges.\n' +
  '- Residual swath/tiling artefacts of the embedding can appear as straight false edges.\n' +
  '- References: UKFields is itself automated (agreement, not truth); CSB is built from the\n' +
  '  30 m CDL and AlphaEarth v2.1 used CDL as a training target, so US scores are optimistic.\n' +
  '  RPA parcels (England) are the most reliable reference but a parcel is not always a\n' +
  '  managed field.\n' +
  '- Unsupervised and label-free: supervised deep models (FracTAL-ResUNet, FTW, Delineate\n' +
  '  Anything) on raw imagery usually delineate better; this is a fast, global baseline.',
  {fontSize: '11px', whiteSpace: 'pre', color: '#444'}));

// Initial run
run();
