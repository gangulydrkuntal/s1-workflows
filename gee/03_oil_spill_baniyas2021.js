/*******************************************************************************
 * USE CASE 3 - MARINE OIL SPILL DETECTION WITH SENTINEL-1 (ADAPTIVE DARK-SPOT
 *              DETECTION, FEATURE EXTRACTION, LOOK-ALIKE SCREENING)
 *
 * Test event : Baniyas thermal power plant fuel-oil leak, Syria, from
 *              23 August 2021. Sentinel-1 imaged the slick on 24 and 25 August
 *              (Copernicus "Image of the Day", 27 Aug 2021); by the end of
 *              August the slick was reported to cover ~800 km2 and reached
 *              Cyprus (Orbital EOS / CNN, 31 Aug 2021). The spill was later
 *              tracked with Sentinel-1/-2 in Marine Pollution Bulletin (2023,
 *              "Tracking the behavior of an accidental oil spill and its
 *              impacts on the marine environment in the Eastern
 *              Mediterranean", ScienceDirect PII S0025326X2301322X).
 *
 * Method (scientific basis)
 *   Oil films damp the centimetre-scale capillary/Bragg waves that dominate
 *   C-band sea clutter, producing "dark spots" in VV backscatter (Alpers &
 *   Huhnerfuss, 1988; Brekke & Solberg, 2005, RSE 95; Fingas & Brown, 2014,
 *   Mar. Pollut. Bull.). The standard 3-stage processing chain is
 *   implemented (Solberg et al., 1999/2007, IEEE TGRS; Topouzelis, 2008, Sensors):
 *     1. Pre-processing: GRD sigma0 VV (calibrated, noise-removed by ESA SNAP),
 *        linear-domain multilooking to 40 m (4x4 boxcar -> ENL ~ 70, strong
 *        speckle reduction while keeping slick shapes), land mask (LSIB
 *        coastline + 1.5 km buffer).
 *     2. Dark-spot detection by ADAPTIVE thresholding: each pixel is compared
 *        to the local sea-clutter background estimated with a large (6 km)
 *        focal median on a 250 m grid; a pixel is dark if it is K dB below
 *        background. This removes the strong incidence-angle and wind-field
 *        trends of sea clutter. Low-wind zones (background < -22 dB, i.e.
 *        wind < ~2-3 m/s, no Bragg waves) are excluded because slicks are
 *        indistinguishable there. Wind is checked with ERA5 10 m wind at the
 *        acquisition hour; the valid detection window is ~2-3 to ~10-12 m/s
 *        (Brekke & Solberg, 2005).
 *     3. Object features + look-alike screening: majority filter, minimum
 *        area, vectorisation; per object: area, contrast (dB below
 *        background), perimeter-based shape complexity
 *        C = P / (2 sqrt(pi A)) (Topouzelis, 2008). Objects with strong damping
 *        and elongated/irregular shape are labelled "likely oil"; the rest are
 *        "possible oil / look-alike" (biogenic films, rain cells, upwelling,
 *        wind shadow) that require an analyst or ML classifier.
 *
 * Validation strategy
 *   a) Temporal NEGATIVE CONTROL: identical processing on a pre-spill image
 *      (1-21 Aug 2021) of the same sea area -> false-alarm area.
 *   b) Area / location comparison with the reported slick (24-25 Aug imaged
 *      by Copernicus, ~800 km2 by ~31 Aug) using the time series chart.
 *   c) Optional: a reference slick polygon asset (e.g. digitised from the
 *      Copernicus / EMSA CleanSeaNet product) -> IoU, precision, recall.
 ******************************************************************************/

// =============================================================================
// 0. USER PARAMETERS
// =============================================================================
var AOI = ee.Geometry.Rectangle([35.30, 34.90, 35.98, 35.75]);   // Syrian coast, Baniyas-Latakia
var BANIYAS = ee.Geometry.Point([35.94, 35.18]);
var SPILL_START = '2021-08-23', SPILL_END = '2021-09-06';
var CONTROL_START = '2021-08-01', CONTROL_END = '2021-08-21';
var K_DB = 3.0;              // darkness threshold below local background (dB)
var LOW_WIND_DB = -22;       // background sigma0 VV below which the sea is too calm
var MIN_AREA_KM2 = 0.5;      // minimum object size
var ML_SCALE = 40;           // multilook pixel size (m)
var REFERENCE_SLICK = null;  // optional: 'users/<you>/baniyas_slick_20210825'

Map.centerObject(AOI, 9);
Map.setOptions('SATELLITE');

var PIX_KM2 = (ML_SCALE * ML_SCALE) / 1e6;
var MIN_PIX = Math.ceil(MIN_AREA_KM2 / PIX_KM2);

// =============================================================================
// 1. DATA
// =============================================================================
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(AOI)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .select(['VV', 'angle'])
  .map(function (img) { return img.set('day', img.date().format('YYYY-MM-dd')); });

var land = ee.Image(0).byte().paint(
  ee.FeatureCollection('USDOS/LSIB_SIMPLE/2017').filterBounds(AOI.buffer(30000)), 1);
var sea = land.focalMax(1500, 'circle', 'meters').not().clip(AOI);

function toLinear(db) { return ee.Image(10).pow(db.divide(10)); }
function toDb(lin) { return lin.log10().multiply(10); }

// =============================================================================
// 2. DETECTION CHAIN
// =============================================================================
function detect(day) {
  var dayCol = s1.filter(ee.Filter.eq('day', day));
  var first = ee.Image(dayCol.first());
  var proj = first.select('VV').projection();
  var mlProj = proj.atScale(ML_SCALE);

  // 2.1 multilooking in linear power
  var vvLin = toLinear(dayCol.select('VV').mosaic()).setDefaultProjection(proj);
  var ml = vvLin.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 64})
    .reproject(mlProj).updateMask(sea).rename('VV');

  // 2.2 local sea-clutter background (robust to the slick itself)
  var bg = ml.reduceResolution({reducer: ee.Reducer.mean(), maxPixels: 64})
    .reproject(proj.atScale(250))
    .focalMedian(6000, 'circle', 'meters')
    .reproject(proj.atScale(250));
  var bgDb = toDb(bg).rename('bg_dB');
  var ratioDb = toDb(ml.divide(bg)).rename('ratio_dB');   // <0 = darker
  var lowWind = bgDb.lt(LOW_WIND_DB);

  // 2.3 adaptive threshold + morphology + minimum area
  var dark = ratioDb.lt(-K_DB).and(lowWind.not()).unmask(0)
    .focalMode(1, 'square', 'pixels').reproject(mlProj);
  var darkClean = dark.updateMask(dark.connectedPixelCount(1024, true).gte(MIN_PIX))
    .selfMask().updateMask(sea).rename('dark').reproject(mlProj);

  // 2.4 ERA5 10 m wind at acquisition time
  var t = first.date();
  var era = ee.Image(ee.ImageCollection('ECMWF/ERA5/HOURLY')
    .filterDate(t.advance(-1, 'hour'), t.advance(1, 'hour')).first());
  var wind = era.select('u_component_of_wind_10m').hypot(era.select('v_component_of_wind_10m'))
    .rename('wind');

  return {day: day, date: t, dbImg: toDb(ml).rename('VV_dB'), ratio: ratioDb, bgDb: bgDb,
    lowWind: lowWind, dark: darkClean, wind: wind, mlProj: mlProj};
}

/** Vectorise dark spots and compute object features. */
function objects(r) {
  var vec = r.dark.addBands(r.ratio).reduceToVectors({
    geometry: AOI, crs: r.mlProj, scale: ML_SCALE, geometryType: 'polygon', eightConnected: true,
    labelProperty: 'dark',
    reducer: ee.Reducer.mean().combine({reducer2: ee.Reducer.count(), sharedInputs: true}),
    maxPixels: 1e10, tileScale: 4
  });
  return vec.filter(ee.Filter.gte('count', MIN_PIX)).map(function (f) {
    var areaM2 = ee.Number(f.get('count')).multiply(ML_SCALE * ML_SCALE);
    var perim = f.geometry().perimeter(10);
    var complexity = perim.divide(areaM2.multiply(Math.PI).sqrt().multiply(2));
    var contrast = ee.Number(f.get('mean')).multiply(-1);
    var likely = contrast.gte(K_DB + 1).and(complexity.gte(1.8));
    return f.set({area_km2: areaM2.divide(1e6), contrast_dB: contrast,
      complexity: complexity, perimeter_km: perim.divide(1000),
      dist_to_source_km: f.geometry().centroid(10).distance(BANIYAS, 10).divide(1000),
      class: ee.Algorithms.If(likely, 'likely oil', 'possible oil / look-alike')});
  });
}

function darkArea(r) {
  return ee.Number(ee.Image.pixelArea().divide(1e6).updateMask(r.dark).reduceRegion({
    reducer: ee.Reducer.sum(), geometry: AOI, crs: r.mlProj, scale: ML_SCALE, maxPixels: 1e10, tileScale: 4
  }).get('area'));
}

function meanWind(r) {
  return ee.Number(r.wind.reduceRegion({reducer: ee.Reducer.mean(), geometry: AOI,
    scale: 27830, maxPixels: 1e8}).get('wind'));
}

// =============================================================================
// 3. USER INTERFACE
// =============================================================================
var panel = ui.Panel({style: {width: '420px', padding: '8px'}});
ui.root.insert(0, panel);
panel.add(ui.Label('Sentinel-1 Oil Spill Detection', {fontSize: '20px', fontWeight: 'bold'}));
panel.add(ui.Label('Baniyas power-plant spill, Syria, Aug-Sep 2021', {fontSize: '12px', color: '#555'}));

function legendRow(color, label) {
  return ui.Panel([
    ui.Label('', {backgroundColor: '#' + color, padding: '8px', margin: '2px 6px 2px 0'}),
    ui.Label(label, {margin: '2px 0', fontSize: '12px'})
  ], ui.Panel.Layout.flow('horizontal'));
}
panel.add(ui.Label('Legend', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(legendRow('ff00ff', 'Likely oil (strong damping + elongated shape)'));
panel.add(legendRow('ffff00', 'Possible oil / look-alike'));
panel.add(legendRow('00ffff', 'Dark-spot pixels (adaptive threshold)'));
panel.add(legendRow('7f7f7f', 'Low-wind zone (excluded)'));
panel.add(legendRow('ff0000', 'Spill source (Baniyas)'));

panel.add(ui.Label('Select Sentinel-1 acquisition:', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
var dateSelect = ui.Select({placeholder: 'loading dates...'});
panel.add(dateSelect);
var statsLabel = ui.Label('', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(statsLabel);
var chartPanel = ui.Panel();
panel.add(chartPanel);

var controlLabel = ui.Label('Computing pre-spill negative control...', {whiteSpace: 'pre', fontSize: '12px'});
panel.add(ui.Label('Validation', {fontWeight: 'bold', margin: '8px 0 4px 0'}));
panel.add(controlLabel);
var tsPanel = ui.Panel();
panel.add(ui.Button({label: 'Compute slick-area time series (all dates)', onClick: areaTimeSeries}));
panel.add(tsPanel);

var layersKeep = 0;
function show(day) {
  // Remove previous result layers
  while (Map.layers().length() > layersKeep) { Map.layers().remove(Map.layers().get(layersKeep)); }
  var r = detect(day);
  var obj = objects(r);
  Map.addLayer(r.dbImg, {min: -28, max: -5}, 'S1 VV sigma0 (dB, 40 m multilook) ' + day, true);
  Map.addLayer(r.ratio, {min: -8, max: 4, palette: ['08306b', '4292c6', 'f7f7f7', 'fdae6b']},
    'Contrast to local background (dB)', false);
  Map.addLayer(r.wind.clip(AOI), {min: 0, max: 12, palette: ['ffffcc', 'a1dab4', '41b6c4', '2c7fb8', '253494']},
    'ERA5 10 m wind speed (m/s)', false);
  Map.addLayer(r.lowWind.selfMask().updateMask(sea), {palette: ['7f7f7f'], opacity: 0.5}, 'Low-wind zone', false);
  Map.addLayer(r.dark, {palette: ['00ffff']}, 'Dark-spot pixels', false);
  Map.addLayer(obj.filter(ee.Filter.eq('class', 'possible oil / look-alike'))
    .style({color: 'ffff00', fillColor: 'ffff0044', width: 1}), {}, 'Possible oil / look-alike');
  Map.addLayer(obj.filter(ee.Filter.eq('class', 'likely oil'))
    .style({color: 'ff00ff', fillColor: 'ff00ff55', width: 1}), {}, 'Likely oil');
  Map.addLayer(ee.FeatureCollection([ee.Feature(BANIYAS)]).style({color: 'ff0000', pointSize: 6}), {}, 'Baniyas source');

  statsLabel.setValue('Processing ' + day + ' ...');
  ee.Dictionary({
    n: obj.size(),
    nLikely: obj.filter(ee.Filter.eq('class', 'likely oil')).size(),
    areaLikely: obj.filter(ee.Filter.eq('class', 'likely oil')).aggregate_sum('area_km2'),
    areaAll: obj.aggregate_sum('area_km2'),
    wind: meanWind(r),
    platform: ee.Image(s1.filter(ee.Filter.eq('day', day)).first()).get('platform_number'),
    pass: ee.Image(s1.filter(ee.Filter.eq('day', day)).first()).get('orbitProperties_pass'),
    time: r.date.format('YYYY-MM-dd HH:mm')
  }).evaluate(function (d, err) {
    if (err) { statsLabel.setValue('Error: ' + err); return; }
    var windFlag = (d.wind >= 2 && d.wind <= 12) ? 'OK (2-12 m/s detection window)' :
      'OUTSIDE optimal window - interpret with care';
    statsLabel.setValue('Acquisition: ' + d.time + ' UTC, S1' + d.platform + ', ' + d.pass +
      '\nERA5 mean wind: ' + d.wind.toFixed(1) + ' m/s -> ' + windFlag +
      '\nDark objects >= ' + MIN_AREA_KM2 + ' km2: ' + d.n + ' (' + d.areaAll.toFixed(1) + ' km2)' +
      '\nLikely-oil objects: ' + d.nLikely + ' (' + d.areaLikely.toFixed(1) + ' km2)');
  });

  chartPanel.clear();
  chartPanel.add(ui.Chart.feature.byFeature(obj.sort('area_km2', false).limit(15), 'label',
      ['area_km2', 'contrast_dB', 'complexity'])
    .setChartType('Table').setOptions({title: 'Largest objects'}));
  chartPanel.add(ui.Chart.feature.groups(obj, 'complexity', 'contrast_dB', 'class')
    .setChartType('ScatterChart')
    .setOptions({title: 'Object feature space (look-alike screening)',
      hAxis: {title: 'Shape complexity C = P / (2 sqrt(pi A))'}, vAxis: {title: 'Contrast (dB below background)'},
      pointSize: 4, colors: ['#ff00ff', '#c9b400']}));
  chartPanel.add(ui.Chart.image.histogram({image: r.ratio.updateMask(sea), region: AOI, scale: 200, maxBuckets: 80})
    .setOptions({title: 'Histogram of contrast to background (dB)', legend: {position: 'none'},
      hAxis: {title: 'dB'}, colors: ['#08519c']}));

  // Optional reference polygon validation
  if (REFERENCE_SLICK !== null) {
    var refImg = ee.Image(0).byte().paint(ee.FeatureCollection(REFERENCE_SLICK), 1).updateMask(sea);
    var pred = r.dark.unmask(0);
    var c = refImg.multiply(2).add(pred).rename('c').reduceRegion({
      reducer: ee.Reducer.frequencyHistogram(), geometry: AOI, crs: r.mlProj, scale: ML_SCALE,
      maxPixels: 1e10, tileScale: 4});
    ee.Dictionary(c.get('c')).evaluate(function (h) {
      var fp = h['1'] || 0, fn = h['2'] || 0, tp = h['3'] || 0;
      print('Reference validation ' + day, {precision: tp / (tp + fp), recall: tp / (tp + fn),
        IoU: tp / (tp + fp + fn)});
    });
  }
}

// Temporal negative control: first available pre-spill acquisition
var controlDays = s1.filterDate(CONTROL_START, CONTROL_END).aggregate_array('day').distinct().sort();
controlDays.evaluate(function (days) {
  if (!days || days.length === 0) { controlLabel.setValue('No pre-spill image found.'); return; }
  var results = days.slice(0, 3).map(function (d) {
    var r = detect(d);
    return ee.Feature(null, {day: d, area: darkArea(r), wind: meanWind(r)});
  });
  ee.FeatureCollection(results).evaluate(function (fc, err) {
    if (err) { controlLabel.setValue('Error: ' + err); return; }
    var txt = 'Negative control (pre-spill, same processing):';
    fc.features.forEach(function (f) {
      txt += '\n  ' + f.properties.day + ': dark area ' + f.properties.area.toFixed(1) +
        ' km2, wind ' + f.properties.wind.toFixed(1) + ' m/s';
    });
    txt += '\nThese values are the false-alarm level of the detector\n(look-alikes) in this sea area.';
    txt += '\nReference: slick imaged 24-25 Aug 2021 (Copernicus);\n~800 km2 reported by ~31 Aug 2021.';
    controlLabel.setValue(txt);
  });
});

function areaTimeSeries() {
  tsPanel.clear();
  tsPanel.add(ui.Label('Computing...'));
  var days = s1.filterDate(CONTROL_START, SPILL_END).aggregate_array('day').distinct().sort();
  days.evaluate(function (list) {
    var fc = ee.FeatureCollection(list.map(function (d) {
      var r = detect(d);
      return ee.Feature(null, {day: d.slice(5), dark_km2: darkArea(r), wind: meanWind(r)});
    }));
    tsPanel.clear();
    tsPanel.add(ui.Chart.feature.byFeature(fc, 'day', ['dark_km2', 'wind'])
      .setChartType('ComboChart')
      .setOptions({title: 'Dark-spot area vs time (spill start 23 Aug 2021)',
        hAxis: {title: 'Date (MM-dd, 2021)'},
        series: {0: {type: 'bars', targetAxisIndex: 0, color: '#c51b8a'},
                 1: {type: 'line', targetAxisIndex: 1, color: '#2c7fb8', pointSize: 4}},
        vAxes: {0: {title: 'Dark area (km2)'}, 1: {title: 'ERA5 wind (m/s)'}}}));
  });
}

// Populate date selector with spill-period acquisitions
s1.filterDate(SPILL_START, SPILL_END).aggregate_array('day').distinct().sort().evaluate(function (days) {
  if (!days || days.length === 0) { statsLabel.setValue('No Sentinel-1 image in the spill window.'); return; }
  dateSelect.items().reset(days);
  dateSelect.setPlaceholder('choose a date');
  dateSelect.onChange(show);
  // Default: the 25 Aug image if present (imaged by Copernicus), else the first one
  dateSelect.setValue(days.indexOf('2021-08-25') >= 0 ? '2021-08-25' : days[0]);
});
