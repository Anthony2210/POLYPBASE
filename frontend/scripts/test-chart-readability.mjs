import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import * as scales from 'd3-scale';
import * as shapes from 'd3-shape';

const chartSource = readFileSync(new URL('../src/components/BiologicalTrendChart.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../src/styles/components/biological-trend-chart.css', import.meta.url), 'utf8');
function compile(source, dependencies = {}) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Date, require(name) {
    assert.ok(name in dependencies, `Unexpected dependency: ${name}`);
    return dependencies[name];
  } });
  return exports;
}
const utility = (name) => compile(readFileSync(new URL(`../src/utils/${name}.ts`, import.meta.url), 'utf8'));
const chart = compile(chartSource, {
  'react/jsx-runtime': {}, react: {}, 'd3-scale': scales, 'd3-shape': shapes,
  '../utils/userIdentity': utility('userIdentity'),
  '../utils/dateFormat': utility('dateFormat'),
  '../utils/chartBiology': utility('chartBiology'),
  '../utils/chartLocations': utility('chartLocations'),
});
const reading = (id, date, polypCount, ephyraeCount) => ({ id, date, polypCount, ephyraeCount });
const readings = [reading(1, '2026-01-01', 0, 0), reading(2, '2026-01-06', 250, 500), reading(3, '2026-06-30', 1200, 1000)];
const locations = [{ id: 1, name: 'A very long laboratory location name', startsAt: '2026-01-01', endsAt: null }];
const geometry = (width, compact = false, values = readings) => chart.buildGeometry(
  values, locations, [], '2026-01-01', '2026-06-30', compact, chart.resolveChartLayout(compact, width, true),
);

test('phone/tablet geometry fills the available padded card content, in both chart modes', () => {
  for (const viewport of [320, 375, 390, 768, 960, 1024, 1180]) {
    // Model page/card padding, not the full viewport as drawing space.
    const contentWidth = viewport - 64;
    for (const compact of [false, true]) {
      const result = geometry(contentWidth, compact);
      assert.equal(result.width, contentWidth);
      assert.equal(result.xPosition('2026-01-01'), 44);
      assert.equal(result.xPosition('2026-06-30'), contentWidth - 12);
      assert.equal(result.width - result.padding.left - result.padding.right, contentWidth - 56);
      assert.ok(result.plotHeight > 160);
      assert.ok(result.xPosition('2026-06-30') + 5.2 + 1.3 < result.width);
      assert.ok(result.yCount(0) + 5.2 + 1.3 < result.countHeight);
      assert.ok(result.yCount(1000) - 5.2 - 1.3 >= 0);
      for (const tick of result.timeTicks) {
        assert.ok(tick.x >= result.padding.left + 116);
        assert.ok(tick.x <= result.width - result.padding.right - 116);
      }
      for (let index = 1; index < result.timeTicks.length; index++) {
        assert.ok(result.timeTicks[index].x - result.timeTicks[index - 1].x >= 88);
      }
    }
  }
});

test('responsive geometry preserves dates, fixed count scale, zeros, overflow and gaps', () => {
  const phone = geometry(296);
  const desktop = chart.buildGeometry(readings, locations, [], '2026-01-01', '2026-06-30', false);
  assert.equal(phone.maxCount, 1000);
  assert.equal(phone.yCount(1200), phone.yCount(1000));
  assert.equal(phone.yCount(0), phone.countHeight - phone.padding.bottom);
  assert.deepEqual(JSON.parse(JSON.stringify(phone.plottedMeasurements)), readings);
  assert.equal(phone.measurementSegments.length, desktop.measurementSegments.length);
  const fraction = (result, date) => (result.xPosition(date) - result.padding.left) / (result.width - result.padding.left - result.padding.right);
  assert.ok(Math.abs(fraction(phone, '2026-01-06') - fraction(desktop, '2026-01-06')) < 1e-12);
  assert.equal(readings[0].polypCount, 0);
});

test('desktop sizing and markers stay unchanged; responsive points are modestly larger', () => {
  for (const compact of [true, false]) {
    const desktop = chart.resolveChartLayout(compact, 296, false);
    assert.equal(desktop.width, compact ? 640 : 860);
    assert.equal(desktop.countHeight, compact ? 238 : 260);
    assert.equal(desktop.pointRadius, 2.15);
    assert.equal(desktop.zeroRadius, 2.8);
    const mobile = chart.resolveChartLayout(compact, 296, true);
    assert.equal(mobile.pointRadius, 3);
    assert.equal(mobile.zeroRadius, 3.4);
  }
  assert.equal(chart.buildDiamondPath(20, 30, 3), 'M20 27 L23 30 L20 33 L17 30 Z');
  assert.match(chartSource, /visibleSeries\.ephyrae && measurement\.ephyraeCount != null && layout\.responsive/);
  assert.match(chartSource, /<circle className=\{`bio-trend-dot is-polyps/);
});

test('dense series keep every reading and partition hit areas instead of covering earlier points', () => {
  const dense = Array.from({ length: 31 }, (_, index) => reading(index, `2026-01-${String(index + 1).padStart(2, '0')}`, index, index));
  const result = geometry(296, false, dense);
  assert.equal(result.plottedMeasurements.length, 31);
  result.hitAreas.forEach((area, index) => {
    const x = result.xPosition(dense[index].date);
    assert.ok(area.width > 0 && area.width <= 44);
    assert.ok(area.left <= x && area.left + area.width >= x);
    if (index) assert.ok(result.hitAreas[index - 1].left + result.hitAreas[index - 1].width <= area.left + 1e-9);
  });
  const tied = chart.buildMeasurementHitAreas([44, 44, 50], 44, 284, true);
  assert.ok(tied.every((area) => area.width >= 0));
  assert.match(chartSource, /data-measurement-index=\{measurementIndex\}/);
  assert.match(chartSource, /handleMeasurementKey\(keyEvent, detail, measurementIndex\)/);
  assert.deepEqual(JSON.parse(JSON.stringify(chart.buildMeasurementHitAreas([44, 50], 44, 284, false))), [{ left: 31, width: 26 }, { left: 37, width: 26 }]);
});

test('responsive CSS contracts keep text readable, canvas fitted, and shape/color distinctions', () => {
  assert.doesNotMatch(css, /(?:width|min-width):\s*640px/);
  const rule = (selector) => {
    const start = css.lastIndexOf(`${selector} {`);
    assert.ok(start >= 0, `Missing rule: ${selector}`);
    return css.slice(start, css.indexOf('}', start) + 1);
  };
  const text = rule('.bio-trend.is-responsive');
  for (const token of ['axis', 'date', 'location']) assert.match(text, new RegExp(`--chart-${token}-size: \\.75rem`));
  assert.match(text, /--chart-legend-size: \.8125rem/);
  const svg = rule('.bio-trend.is-responsive .bio-trend-svg');
  for (const declaration of ['width: 100%', 'min-width: 0', 'height: auto', 'min-height: 0', 'aspect-ratio: auto']) assert.ok(svg.includes(declaration));
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend button'), /min-height: 44px/);
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend .is-ephyrae::before'), /rotate\(45deg\)/);
  assert.match(rule('.bio-trend.is-responsive .bio-trend-legend .is-polyps::before'), /border-radius: 50%/);
  assert.match(css, /\.bio-trend-dot\.is-polyps \{ --series-color: var\(--color-primary\)/);
  assert.match(css, /\.bio-trend-dot\.is-ephyrae \{ --series-color: var\(--color-ephyrae\)/);
  assert.match(chartSource, /entry\.contentRect\.width/);
  assert.match(chartSource, /canvas\.clientWidth - parseFloat\(style\.paddingLeft\) - parseFloat\(style\.paddingRight\)/);
  assert.match(chartSource, /width=\{band\.width - 8\} height=\{zoneBandHeight\}/);
  assert.match(chartSource, /const chartId = useId\(\)/);
  assert.match(chartSource, /clipPath=\{`url\(#\$\{chartId\}-location-\$\{bandIndex\}\)`\}/);
});
