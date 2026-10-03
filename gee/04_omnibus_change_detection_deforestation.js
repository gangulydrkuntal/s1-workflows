/*******************************************************************************
 * USE CASE 4 - MULTI-TEMPORAL CHANGE DETECTION WITH THE SEQUENTIAL OMNIBUS
 *              LIKELIHOOD-RATIO TEST (complex Wishart / Gamma) - DEFORESTATION
 *
 * Test site  : Jaci-Parana Extractive Reserve (Rondonia, Brazilian Amazon), one
 *              of the most deforested protected areas of the Amazon in
 *              2019-2021 (PRODES / MapBiomas / Global Forest Watch).
 *              Reference: Hansen et al. (2013, Science) Global Forest Change
 *              v1.12 annual loss (independent Landsat product, in GEE).
 *
 * Method (scientific basis)
 *   - Conradsen, Nielsen & Skriver (2016), IEEE TGRS 54(5): "Determining the
 *     points of change in time series of polarimetric SAR data" - omnibus test
 *     for equality of k covariance matrices and its factorisation into a
 *     sequence of tests R_j that locate WHEN changes occur.
 *   - Canty, Nielsen, Conradsen & Skriver (2020), Remote Sensing 12(1), 46:
 *     "Statistical Analysis of Changes in Sentinel-1 Time Series on the Google
 *     Earth Engine" - adaptation to dual-pol (VV,VH) intensity GRD data on GEE:
 *     the covariance matrix is diagonal, |C| = VV * VH, the equivalent number
 *     of looks of the GRD product is m = 4.4, and -2 log R_j ~ chi2 with
 *     2 degrees of freedom (2(k-1) for the omnibus Q).
 *   Key requirements honoured here (Canty et al., 2020):
 *     * LINEAR intensities (COPERNICUS/S1_GRD_FLOAT), not dB;
 *     * NO speckle filtering (the test relies on the Wishart/Gamma speckle
 *       statistics with ENL m);
 *     * a single relative orbit (identical geometry) and images that fully
 *       cover the AOI;
 *     * significance level alpha (default 0.01) and optional median filtering
 *       of the omnibus p-values to suppress isolated false alarms.
 *   Outputs: cmap (last change), smap (FIRST change interval), fmap (number of
 *   changes), bmap (change flags per interval) and the direction of the first
 *   change (backscatter increase / decrease / mixed).
 *
 * Validation
 *   Within forest that was intact at the start of 2020 (Hansen treecover2000
 *   >= 50 % and no loss before 2020), S1 changes are compared with Hansen loss
 *   in 2020-2021 on a stratified random sample (Olofsson et al., 2014, RSE:
 *   stratum-weighted accuracy estimates), plus year-of-change agreement.
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([-64.45, -9.55, -64.30, -9.40]);   // Jaci-Parana RESEX
var START = '2020-01-01', END = '2022-01-01';
var ALPHA = 0.01;          // significance level
var ENL = 4.4;             // equivalent number of looks of S1 IW GRD (Canty et al., 2020)
var MEDIAN_FILTER = true;  // 3x3 median on omnibus p-values
var HANSEN = ee.Image('UMD/hansen/global_forest_change_2024_v1_12');

Map.centerObject(AOI, 12);
Map.setOptions('SATELLITE');

// =============================================================================
// 1. TIME SERIES: one image per month, single relative orbit, full AOI cover
// =============================================================================
var s1all = ee.ImageCollection('COPERNICUS/S1_GRD_FLOAT')
  .filterBounds(AOI)
  .filterDate(START, END)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VH'))
  .filter(ee.Filter.contains({leftField: '.geo', rightValue: AOI}));

var orbit = ee.Number(s1all.aggregate_array('relativeOrbitNumber_start').reduce(ee.Reducer.mode()));
var s1 = s1all.filter(ee.Filter.eq('relativeOrbitNumber_start', orbit))
  .sort('system:time_start')
  .map(function (img) { return img.set('ym', img.date().format('YYYY-MM')); })
  .distinct('ym')                        // first acquisition of every month
  .sort('system:time_start');

var imList = s1.toList(100).map(function (img) {
  return ee.Image(img).select(['VV', 'VH']).clip(AOI);
});
var k = imList.length();
var dates = s1.aggregate_array('system:time_start').map(function (t) {
  return ee.Date(t).format('YYYY-MM-dd');
});

// =============================================================================
// 2. SEQUENTIAL OMNIBUS TEST (JavaScript port of Canty et al., 2020)
// =============================================================================
function det(im) { return ee.Image(im).select(0).multiply(ee.Image(im).select(1)); }

function chi2cdf(chi2, df) {
  return ee.Image(chi2.divide(2)).gammainc(ee.Number(df).divide(2));
}

function logDetSum(list, j) {
  var sumj = ee.ImageCollection(ee.List(list).slice(0, j)).reduce(ee.Reducer.sum());
  return det(sumj).log();
}

function logDet(list, j) {
  return det(ee.Image(ee.List(list).get(ee.Number(j).subtract(1)))).log();
}

/** p-value and -2 m log R_j for the test "image j equals images 1..j-1". */
function pval(list, j, m) {
  j = ee.Number(j);
  var m2logRj = logDetSum(list, j.subtract(1)).multiply(j.subtract(1))
    .add(logDet(list, j))
    .add(ee.Number(2).multiply(j).multiply(j.log()))
    .subtract(ee.Number(2).multiply(j.subtract(1)).multiply(j.subtract(1).log()))
    .subtract(logDetSum(list, j).multiply(j))
    .multiply(-2).multiply(m);
  var pv = ee.Image.constant(1).subtract(chi2cdf(m2logRj, 2));
  return {pv: pv, m2logRj: m2logRj};
}

/** Array of p-values: for each start ell, [p(R_2) .. p(R_k-ell+1), p(Q_ell)]. */
function pValues(list, m) {
  list = ee.List(list);
  var kk = list.length();
  return ee.List.sequence(1, kk.subtract(1)).map(function (ell) {
    ell = ee.Number(ell);
    var listEll = list.slice(ell.subtract(1));
    var js = ee.List.sequence(2, kk.subtract(ell).add(1));
    var fc = ee.FeatureCollection(js.map(function (j) {
      var r = pval(listEll, j, m);
      return ee.Feature(null, {pv: r.pv, m2logRj: r.m2logRj});
    }));
    var m2logQl = ee.ImageCollection(fc.aggregate_array('m2logRj')).sum();
    var pvQl = ee.Image.constant(1).subtract(chi2cdf(m2logQl, ee.Number(2).multiply(kk.subtract(ell))));
    return ee.List(fc.aggregate_array('pv')).add(pvQl);
  });
}

function filterJ(current, prev) {
  var pv = ee.Image(current);
  prev = ee.Dictionary(prev);
  var pvQ = ee.Image(prev.get('pvQ'));
  var i = ee.Number(prev.get('i'));
  var cmap = ee.Image(prev.get('cmap'));
  var smap = ee.Image(prev.get('smap'));
  var fmap = ee.Image(prev.get('fmap'));
  var bmap = ee.Image(prev.get('bmap'));
  var alpha = ee.Image(prev.get('alpha'));
  var j = ee.Number(prev.get('j'));
  var cmapj = cmap.multiply(0).add(i.add(j).subtract(1));
  // Change if R_j and the omnibus Q are both significant and no change has
  // been registered since the current start point
  var tst = pv.lt(alpha).and(pvQ.lt(alpha)).and(cmap.eq(i.subtract(1)));
  cmap = cmap.where(tst, cmapj);
  fmap = fmap.where(tst, fmap.add(1));
  smap = ee.Image(ee.Algorithms.If(i.eq(1), smap.where(tst, cmapj), smap));
  var idx = i.add(j).subtract(2);
  var bname = bmap.bandNames().get(idx);
  var tmp = bmap.select([idx]).where(tst, 1).rename([bname]);
  bmap = bmap.addBands(tmp, [bname], true);
  return ee.Dictionary({i: i, j: j.add(1), alpha: alpha, pvQ: pvQ,
    cmap: cmap, smap: smap, fmap: fmap, bmap: bmap});
}

function filterI(current, prev) {
  current = ee.List(current);
  var pvs = current.slice(0, -1);
  var pvQ = ee.Image(current.get(-1));
  prev = ee.Dictionary(prev);
  var i = ee.Number(prev.get('i'));
  var alpha = ee.Image(prev.get('alpha'));
  var median = prev.get('median');
  pvQ = ee.Image(ee.Algorithms.If(median, pvQ.focalMedian(1.5), pvQ));
  var first = ee.Dictionary({i: i, j: 1, alpha: alpha, pvQ: pvQ,
    cmap: prev.get('cmap'), smap: prev.get('smap'), fmap: prev.get('fmap'), bmap: prev.get('bmap')});
  var result = ee.Dictionary(ee.List(pvs).iterate(filterJ, first));
  return ee.Dictionary({i: i.add(1), alpha: alpha, median: median,
    cmap: result.get('cmap'), smap: result.get('smap'), fmap: result.get('fmap'), bmap: result.get('bmap')});
}

function changeMaps(list, median, alpha, m) {
  list = ee.List(list);
  var kk = list.length();
  var pvArr = ee.List(pValues(list, m));
  var cmap = ee.Image(list.get(0)).select(0).multiply(0);
  var bmap = ee.Image.constant(ee.List.repeat(0, kk.subtract(1))).add(cmap);
  var first = ee.Dictionary({i: 1, alpha: ee.Image.constant(alpha), median: median,
    cmap: cmap, smap: cmap, fmap: cmap, bmap: bmap});
  return ee.Dictionary(pvArr.iterate(filterI, first));
}

var result = changeMaps(imList, MEDIAN_FILTER, ALPHA, ENL);
var cmap = ee.Image(result.get('cmap')).byte().rename('cmap');
var smap = ee.Image(result.get('smap')).byte().rename('smap');
var fmap = ee.Image(result.get('fmap')).byte().rename('fmap');
var bmap = ee.Image(result.get('bmap')).byte();

// Direction of change for every interval (Loewner-order analogue for diagonal
// matrices): 1 = both VV and VH increase, 2 = both decrease, 3 = mixed.
var dirStack = ee.ImageCollection(ee.List.sequence(1, k.subtract(1)).map(function (s) {
  s = ee.Number(s);
  var a = ee.Image(imList.get(s.subtract(1))).focalMean(1.5);
  var b = ee.Image(imList.get(s)).focalMean(1.5);
  var r = b.divide(a);
  var inc = r.select(0).gt(1).and(r.select(1).gt(1));
  var dec = r.select(0).lt(1).and(r.select(1).lt(1));
  return ee.Image(3).where(inc, 1).where(dec, 2).byte();
})).toBands();
var firstDir = dirStack.toArray().arrayGet(smap.subtract(1).max(0).toInt())
  .updateMask(smap.gt(0)).rename('direction');

// =============================================================================
// 3. VALIDATION AGAINST HANSEN GLOBAL FOREST CHANGE
// =============================================================================
var lossyear = HANSEN.select('lossyear');
var forest2020 = HANSEN.select('treecover2000').gte(50)
  .and(lossyear.eq(0).or(lossyear.gte(20)));
var refLoss = lossyear.eq(20).or(lossyear.eq(21)).rename('ref');
var predChange = smap.gt(0).rename('pred');
var predDecrease = smap.gt(0).and(firstDir.unmask(0).eq(2)).rename('pred_dec');

// Year of first S1 change (from interval index -> image date)
var years = ee.List([0]).cat(dates.slice(1).map(function (d) { return ee.Number.parse(ee.String(d).slice(0, 4)); }));
var stack = refLoss.addBands(predChange).addBands(predDecrease).addBands(smap)
  .addBands(lossyear.rename('lossyear')).updateMask(forest2020).clip(AOI);

var N = refLoss.updateMask(forest2020).rename('ref').reduceRegion({
  reducer: ee.Reducer.frequencyHistogram(), geometry: AOI, scale: 30, maxPixels: 1e9}).get('ref');
var sample = stack.stratifiedSample({numPoints: 600, classBand: 'ref', region: AOI, scale: 10,
  seed: 42, tileScale: 8, geometries: true});

function weightedMetrics(sample, predBand, N) {
  N = ee.Dictionary(N);
  var N0 = ee.Number(N.get('0', 0)), N1 = ee.Number(N.get('1', 0));
  var s0 = sample.filter(ee.Filter.eq('ref', 0)), s1s = sample.filter(ee.Filter.eq('ref', 1));
  var n0 = s0.size(), n1 = s1s.size();
  var tp = s1s.filter(ee.Filter.eq(predBand, 1)).size().multiply(N1.divide(n1.max(1)));
  var fn = s1s.filter(ee.Filter.eq(predBand, 0)).size().multiply(N1.divide(n1.max(1)));
  var fp = s0.filter(ee.Filter.eq(predBand, 1)).size().multiply(N0.divide(n0.max(1)));
  var tn = s0.filter(ee.Filter.eq(predBand, 0)).size().multiply(N0.divide(n0.max(1)));
  var tot = tp.add(fn).add(fp).add(tn);
  var prec = tp.divide(tp.add(fp).max(1e-9)), rec = tp.divide(tp.add(fn).max(1e-9));
  var oa = tp.add(tn).divide(tot);
  var pe = tp.add(fp).multiply(tp.add(fn)).add(tn.add(fn).multiply(tn.add(fp))).divide(tot.pow(2));
  return ee.Dictionary({n_loss: n1, n_noloss: n0, OA: oa, kappa: oa.subtract(pe).divide(ee.Number(1).subtract(pe)),
    precision: prec, recall: rec, F1: prec.multiply(rec).multiply(2).divide(prec.add(rec).max(1e-9)),
    lossArea_km2_ref: N1.multiply(900).divide(1e6)});
}

// Year agreement for correctly detected loss pixels
var tpSample = sample.filter(ee.Filter.eq('ref', 1)).filter(ee.Filter.eq('pred', 1)).map(function (f) {
  var y = ee.Number(years.get(ee.Number(f.get('smap')).int()));
  return f.set('s1year', y, 'same_year', y.eq(ee.Number(f.get('lossyear')).add(2000)));
});

// =============================================================================
// 4. MAP LAYERS
// =============================================================================
var dateVis = {min: 1, max: 23, palette: ['440154', '3b528b', '21918c', '5ec962', 'fde725']};
var vvMean = ee.ImageCollection(imList).mean();
Map.addLayer(ee.Image.cat(
    toDb(ee.Image(imList.get(0)).select('VH')), toDb(ee.Image(imList.get(-1)).select('VH')),
    toDb(ee.Image(imList.get(-1)).select('VH'))),
  {min: -22, max: -10}, 'VH first (R) vs last (G,B) image', false);
Map.addLayer(toDb(vvMean.select('VV')), {min: -15, max: -3}, 'Mean VV (dB)', false);
Map.addLayer(HANSEN.select('treecover2000').updateMask(forest2020), {min: 50, max: 100, palette: ['c7e9c0', '00441b']},
  'Forest at start of 2020 (Hansen)', false);
Map.addLayer(refLoss.selfMask(), {palette: ['ff0000']}, 'Reference: Hansen loss 2020-2021', false);
Map.addLayer(fmap.selfMask(), {min: 1, max: 4, palette: ['ffffb2', 'fd8d3c', 'bd0026']}, 'Change frequency (fmap)', false);
Map.addLayer(cmap.selfMask(), dateVis, 'Last change interval (cmap)', false);
Map.addLayer(firstDir, {min: 1, max: 3, palette: ['ff0000', '0000ff', 'ffff00']},
  'Direction of first change (red +, blue -, yellow mixed)', false);
Map.addLayer(smap.selfMask(), dateVis, 'First change interval (smap)', true);
Map.addLayer(ee.Image().byte().paint(AOI, 1, 2), {palette: ['ffffff']}, 'AOI');

function toDb(img) { return img.log10().multiply(10); }

// =============================================================================
// 5. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '420px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Sentinel-1 Omnibus Change Detection', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Sequential complex-Wishart LRT (Conradsen 2016; Canty 2020)\n' +
  'Deforestation, Jaci-Parana RESEX, 2020-2021', {fontSize: '12px', color: '#555', whiteSpace: 'pre'}));

var legendPanel = ui.Panel();
panel.add(legendPanel);
var infoLabel = ui.Label('Loading time series...', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(infoLabel);

ee.Dictionary({dates: dates, orbit: orbit, k: k}).evaluate(function (r, err) {
  if (err) { infoLabel.setValue('Error: ' + err); return; }
  infoLabel.setValue('Relative orbit ' + r.orbit + ', ' + r.k + ' monthly images\n' +
    r.dates[0] + ' to ' + r.dates[r.dates.length - 1] + ' | alpha = ' + ALPHA + ', ENL = ' + ENL);
  var maxI = r.k - 1;
  var bar = ui.Thumbnail({
    image: ee.Image.pixelLonLat().select(0),
    params: {bbox: [0, 0, 1, 0.1], dimensions: '300x12', format: 'png', min: 0, max: 1, palette: dateVis.palette},
    style: {stretch: 'horizontal', margin: '0 8px', maxHeight: '20px'}
  });
  legendPanel.add(ui.Label('First change date (smap)', {fontWeight: 'bold'}));
  legendPanel.add(bar);
  legendPanel.add(ui.Panel([
    ui.Label(r.dates[1], {margin: '2px 8px', fontSize: '11px'}),
    ui.Label(r.dates[Math.round(maxI / 2)], {margin: '2px 8px', fontSize: '11px', textAlign: 'center', stretch: 'horizontal'}),
    ui.Label(r.dates[maxI], {margin: '2px 8px', fontSize: '11px'})
  ], ui.Panel.Layout.flow('horizontal')));
  // Re-style date layers with the real number of intervals
  Map.layers().forEach(function (l) {
    var n = l.getName();
    if (n === 'First change interval (smap)' || n === 'Last change interval (cmap)') {
      l.setVisParams({min: 1, max: maxI, palette: dateVis.palette});
    }
  });
});

var valLabel = ui.Label('Computing validation vs Hansen GFC (stratified sample)...',
  {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Label('Validation', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(valLabel);
ee.Dictionary({any: weightedMetrics(sample, 'pred', N), dec: weightedMetrics(sample, 'pred_dec', N),
  sameYear: tpSample.aggregate_mean('same_year'), nTP: tpSample.size()})
  .evaluate(function (r, err) {
    if (err) { valLabel.setValue('Error: ' + err); return; }
    function fmt(m) {
      return '  OA ' + (100 * m.OA).toFixed(1) + ' % | kappa ' + m.kappa.toFixed(3) +
        '\n  precision ' + (100 * m.precision).toFixed(1) + ' % | recall ' + (100 * m.recall).toFixed(1) +
        ' % | F1 ' + m.F1.toFixed(3);
    }
    valLabel.setValue('Domain: forest intact on 1 Jan 2020 (Hansen v1.12)' +
      '\nReference loss 2020-21: ' + r.any.lossArea_km2_ref.toFixed(1) + ' km2' +
      '\nSample: ' + r.any.n_loss + ' loss / ' + r.any.n_noloss + ' no-loss points (10 m)' +
      '\nStratum-weighted accuracy (Olofsson et al., 2014):' +
      '\nAny significant change:\n' + fmt(r.any) +
      '\nOnly backscatter-decrease changes:\n' + fmt(r.dec) +
      '\nYear agreement S1 vs Hansen (detected loss): ' + (100 * r.sameYear).toFixed(1) + ' % (n=' + r.nTP + ')' +
      '\n\nNotes: Hansen is annual (loss after the last S1 image\nor before the first one cannot be seen by S1);\nselective logging/degradation and moisture changes\ncause part of the disagreement.');
  });

// Share of pixels changing in each interval (sample-based estimate)
var bSample = bmap.sample({region: AOI, scale: 10, numPixels: 4000, seed: 3, tileScale: 8, geometries: false});
var intervalMeans = bmap.bandNames().map(function (b) { return bSample.aggregate_mean(b); });
var intervalFc = ee.FeatureCollection(ee.List.sequence(0, k.subtract(2)).map(function (i) {
  i = ee.Number(i);
  return ee.Feature(null, {date: dates.get(i.add(1)), pct: ee.Number(intervalMeans.get(i)).multiply(100)});
}));
panel.add(ui.Chart.feature.byFeature(intervalFc, 'date', ['pct']).setChartType('ColumnChart')
  .setOptions({title: 'Share of AOI with a significant change per interval',
    hAxis: {title: 'Image date (end of interval)', slantedText: true}, vAxis: {title: '% of pixels'},
    legend: {position: 'none'}, colors: ['#21918c']}));

// Click a pixel -> VV / VH time series
panel.add(ui.Label('Click on the map for the VV/VH time series of a pixel.', {fontSize: '12px', color: '#555'}));
var clickPanel = ui.Panel();
panel.add(clickPanel);
Map.onClick(function (c) {
  var pt = ee.Geometry.Point([c.lon, c.lat]);
  var ts = s1.map(function (img) {
    return ee.Image(toDb(img.select(['VV', 'VH'])).copyProperties(img, ['system:time_start']));
  });
  clickPanel.clear();
  clickPanel.add(ui.Chart.image.series({imageCollection: ts, region: pt, reducer: ee.Reducer.first(), scale: 10})
    .setOptions({title: 'Backscatter at clicked pixel (unfiltered, dB)', vAxis: {title: 'dB'},
      pointSize: 4, lineWidth: 1, colors: ['#1b9e77', '#d95f02']}));
});

Export.image.toDrive({image: ee.Image.cat(smap, cmap, fmap, firstDir.unmask(0).byte()),
  description: 'S1_omnibus_JaciParana_2020_2021', region: AOI, scale: 10, maxPixels: 1e10});
