import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

function load(path, imports = {}, globals = {}, extra = '') {
  const { outputText } = ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + extra, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Intl, Date, ...globals, require(name) {
    assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
    return imports[name];
  } });
  return exports;
}

const catalogs = { fr: load('../src/i18n/fr.ts').fr, en: load('../src/i18n/en.ts').en };
const scaleUtils = load('../src/utils/temperatureScale.ts');
const probeStatus = load('../src/utils/probeStatus.ts');
// Local constructor makes the expected clock labels independent of the runner's timezone.
const oldest = new Date(2026, 10, 4, 8, 12).toISOString();
const newest = new Date(2026, 10, 4, 9, 34).toISOString();
const summary = (overrides = {}) => ({
  evaluated_at: newest, temperature_c: 27, included_probe_count: 2,
  oldest_measured_at: oldest, newest_measured_at: newest,
  expires_at: new Date(Date.parse(newest) + 180_000).toISOString(),
  eligible_probe_count: 3, no_reading_probe_count: 0, stale_probe_count: 1,
  unavailable_probe_count: 0, future_probe_count: 0, invalid_interval_probe_count: 0,
  ...overrides,
});
const daily = (overrides = {}) => ({
  date: '2026-11-03', average_temperature_c: 21, min_temperature_c: 19,
  max_temperature_c: 23, measurement_count: 12, computed_from_probes: true, ...overrides,
});
// A manual location keeps the static ruler: it has no time series to draw.
const zone = (overrides = {}) => ({
  id: 17, organization: { id: 1 }, target_temperature_c: '20',
  probes: [{ id: 1, is_collected: false }], latest_temperature: daily(), ...overrides,
});
const collectedZone = (overrides = {}) => zone({ probes: [{ id: 1, is_collected: true }], ...overrides });
const trendStub = () => null;

function text(node) {
  if (Array.isArray(node)) return node.map(text).join('');
  if (React.isValidElement(node)) return text(node.props.children);
  return node == null || typeof node === 'boolean' ? '' : String(node);
}
function find(node, predicate) {
  if (Array.isArray(node)) return node.flatMap(child => find(child, predicate));
  if (!React.isValidElement(node)) return [];
  return [...(predicate(node) ? [node] : []), ...find(node.props.children, predicate)];
}
function hasClass(name) {
  return node => (node.props.className ?? '').split(' ').includes(name);
}

// Render the actual panel, not a duplicated presentation helper. Only resource
// hooks and unrelated child components are stubbed; scale, dates and copy are real.
function panel({ language = 'fr', result = { summary: summary(), error: null }, currentZone = zone() } = {}) {
  const copy = catalogs[language];
  const document = { documentElement: { lang: language } };
  const dates = load('../src/utils/dateFormat.ts', {}, { document });
  const noop = () => null;
  const slots = [];
  let cursor = 0, tree;
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], value => { slots[index] = typeof value === 'function' ? value(slots[index]) : value; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useEffect() {}, useLayoutEffect() {},
  };
  const imports = {
    react: hooks, 'react/jsx-runtime': jsxRuntime,
    '../api/client': {}, '../utils/errors': {},
    '../utils/dateFormat': dates, '../utils/probeStatus': probeStatus,
    '../utils/temperatureScale': scaleUtils,
    '../hooks/useThermalControl': { default: received => {
      assert.equal(received, currentZone, 'Read freshness belongs to the rendered zone'); return result;
    } },
    '../hooks/useProbeMonitoring': { default: () => ({ monitoring: undefined, error: null, revalidate() {} }) },
  };
  for (const name of ['BoxTrackingPreview', 'DetailBackButton', 'ModalPortal', 'PageLoader', 'PolypbaseIcon']) {
    imports[`./${name}`] = { default: noop };
  }
  imports['./ZoneProbeCharts'] = { ZoneProbeChart: noop, ZoneThermalTrend: trendStub, useZoneChartReadings: () => ({ initial: { settled: true, data: null } }) };
  imports['./ZoneMovementHistory'] = { ZoneRecentMovements: noop };
  const { TemperatureControlPanel } = load('../src/components/ZonesView.tsx', imports, { document }, '\nexport { TemperatureControlPanel };');
  function render(nextResult = result, nextZone = currentZone) {
    result = nextResult; currentZone = nextZone; cursor = 0;
    tree = TemperatureControlPanel({ zone: currentZone, canRecordManualTemperature: false,
      onRecordManualTemperature() { throw new Error('Rendering must not write'); },
      t(key) { assert.equal(typeof copy[key], 'string', `Missing ${language} copy: ${key}`); return copy[key]; },
    });
  }
  render();
  return {
    copy, dates, render,
    get tree() { return tree; },
    nodes: name => find(tree, hasClass(name)),
    text: () => text(tree),
    statuses: () => find(tree, node => node.props.role === 'status'),
  };
}

for (const language of ['fr', 'en']) {
  test(`${language}: recent mean and daily mean/min/max are distinct, with daily mean minus target unchanged`, () => {
    const view = panel({ language });
    const marker = view.nodes('temperature-observed-marker');
    assert.equal(marker.length, 1);
    assert.equal(text(marker[0]), `${view.copy.temperatureRecentMean} 27.0°C`);
    const facts = view.nodes('temperature-facts')[0];
    assert.equal(text(facts), `${view.copy.temperatureDailyMean}21.0°C${view.copy.temperatureDailyGap}+1.0°C`);
    const range = view.nodes('temperature-range-values')[0];
    assert.equal(text(range), `${view.copy.minTemperature}19.0°C${view.copy.maxTemperature}23.0°C`);
    const dailyPeriod = view.copy.temperatureDailyPeriod.replace('{date}', view.dates.formatDisplayDate('2026-11-03'));
    assert.equal(text(view.nodes('temperature-daily-period')[0]), dailyPeriod);
    assert.equal(view.nodes('temperature-summary')[0].props['aria-label'], dailyPeriod);
    const accessible = view.nodes('temperature-ruler')[0].props['aria-label'];
    for (const fact of [`${view.copy.temperatureRecentMean} 27.0°C`, `${view.copy.temperatureDailyMean} 21.0°C`,
      `${view.copy.minTemperature} 19.0°C`, `${view.copy.maxTemperature} 23.0°C`, dailyPeriod]) {
      assert.ok(accessible.includes(fact), `Accessible ruler must retain ${fact}`);
    }
    view.render({ summary: summary({ temperature_c: -5 }), error: null });
    assert.equal(text(view.nodes('temperature-facts')[0]), text(facts), 'Recent changes cannot change the daily delta');
    assert.equal(text(view.nodes('temperature-range-values')[0]), text(range));
  });

  test(`${language}: observation period includes full dates and times, using a range only for distinct instants`, () => {
    const view = panel({ language });
    const expectedPeriod = `${view.dates.formatDisplayDateTime(oldest)} — ${view.dates.formatDisplayDateTime(newest)}`;
    const expected = view.copy.temperatureRecentPeriod.replace('{count}', '2').replace('{period}', expectedPeriod);
    const heading = view.nodes('zone-temperature-heading')[0];
    assert.ok(text(heading).includes(expected));
    assert.match(text(heading), /2026/);
    assert.match(text(heading), /08:12/);
    assert.match(text(heading), /09:34/);
    assert.match(text(heading), language === 'fr' ? /nov\./ : /Nov/);
    assert.ok(view.nodes('temperature-ruler')[0].props['aria-label'].includes(expected));
    view.render({ summary: summary({ oldest_measured_at: newest, included_probe_count: 1 }), error: null });
    const single = view.copy.temperatureRecentPeriod.replace('{count}', '1').replace('{period}', view.dates.formatDisplayDateTime(newest));
    assert.ok(text(view.nodes('zone-temperature-heading')[0]).includes(single));
    assert.doesNotMatch(text(view.nodes('zone-temperature-heading')[0]), / — |08:12|\{count\}|\{period\}/);
  });

  test(`${language}: zero is a recent measurement and daily zero/delta/minimum are not missing`, () => {
    const view = panel({ language, result: { summary: summary({ temperature_c: 0 }), error: null },
      currentZone: zone({ target_temperature_c: '0', latest_temperature: daily({ average_temperature_c: 0, min_temperature_c: 0, max_temperature_c: 2 }) }) });
    assert.equal(text(view.nodes('temperature-observed-marker')[0]), `${view.copy.temperatureRecentMean} 0.0°C`);
    assert.equal(text(view.nodes('temperature-target-marker')[0]), `${view.copy.targetTemperature} 0.0°C`);
    assert.equal(text(view.nodes('temperature-facts')[0]), `${view.copy.temperatureDailyMean}0.0°C${view.copy.temperatureDailyGap}0.0°C`);
    assert.ok(text(view.nodes('temperature-range-values')[0]).includes(`${view.copy.minTemperature}0.0°C`));
    assert.ok(!view.text().includes(view.copy.temperatureNoRecent));
  });

  test(`${language}: pending is busy loading, not a successful absence of recent measurements`, () => {
    const view = panel({ language, result: { summary: undefined, error: null } });
    const paragraphs = find(view.nodes('zone-temperature-heading')[0], node => node.type === 'p');
    assert.equal(paragraphs.length, 1);
    assert.equal(text(paragraphs[0]), view.copy.loading);
    assert.equal(paragraphs[0].props['aria-busy'], true);
    assert.equal(paragraphs[0].props.role, undefined);
    assert.equal(view.statuses().length, 0);
    assert.ok(!view.text().includes(view.copy.temperatureNoRecent));
    assert.ok(!view.text().includes(view.copy.temperatureRecentError));
    assert.equal(view.nodes('temperature-observed-marker').length, 0);
    const accessible = view.nodes('temperature-ruler')[0].props['aria-label'];
    assert.ok(accessible.includes(view.copy.loading));
    assert.ok(!accessible.includes(view.copy.temperatureNoRecent));
  });

  test(`${language}: a successful empty response ends loading without a redundant no-recent message`, () => {
    const view = panel({ language, result: { summary: undefined, error: null } });
    view.render({ summary: summary({ temperature_c: null, included_probe_count: 0,
      oldest_measured_at: null, newest_measured_at: null, expires_at: null, stale_probe_count: 3 }), error: null });
    // The absence of a fresh probe is shown by the missing recent mean, not by a sentence.
    assert.equal(find(view.nodes('zone-temperature-heading')[0], node => node.type === 'p').length, 0);
    assert.equal(view.statuses().length, 0);
    assert.ok(!view.text().includes(view.copy.loading));
    assert.ok(!view.text().includes(view.copy.temperatureRecentError));
    assert.ok(!view.text().includes(view.copy.temperatureNoRecent));
    assert.equal(view.nodes('temperature-observed-marker').length, 0);
    const accessible = view.nodes('temperature-ruler')[0].props['aria-label'];
    assert.ok(!accessible.includes(view.copy.temperatureNoRecent));
    assert.ok(!accessible.includes(view.copy.temperatureRecentMean));
    assert.doesNotMatch(accessible, /null|undefined|, ,/);
    assert.ok(view.text().includes(`${view.copy.temperatureDailyMean}21.0°C`));
  });

  test(`${language}: refresh error is localized and cannot substitute a daily marker`, () => {
    const view = panel({ language, result: { summary: undefined, error: 'Internal transport details' } });
    assert.equal(view.statuses().length, 1);
    assert.equal(text(view.statuses()[0]), view.copy.temperatureRecentError);
    assert.ok(!view.text().includes(view.copy.temperatureNoRecent), 'A failed request cannot establish that observations are absent');
    assert.ok(!view.text().includes(view.copy.loading));
    const headingParagraphs = find(view.nodes('zone-temperature-heading')[0], node => node.type === 'p');
    assert.equal(headingParagraphs.length, 1);
    assert.equal(headingParagraphs[0], view.statuses()[0]);
    assert.equal(headingParagraphs[0].props['aria-busy'], false);
    assert.ok(view.nodes('temperature-ruler')[0].props['aria-label'].includes(view.copy.temperatureRecentError));
    assert.ok(!view.nodes('temperature-ruler')[0].props['aria-label'].includes(view.copy.temperatureNoRecent));
    assert.ok(!view.text().includes('Internal transport details'));
    assert.equal(view.nodes('temperature-observed-marker').length, 0);
    assert.ok(view.text().includes(`${view.copy.temperatureDailyMean}21.0°C`));
    view.render({ summary: summary(), error: null });
    assert.equal(view.statuses().length, 0);
    assert.equal(view.nodes('temperature-observed-marker').length, 1);
  });
}

for (const [name, result, currentZone] of [
  ['no summary', { summary: undefined, error: null }, zone()],
  ['no probes', { summary: summary({ temperature_c: null, included_probe_count: 0, eligible_probe_count: 0,
    oldest_measured_at: null, newest_measured_at: null, expires_at: null, stale_probe_count: 0 }), error: null }, zone({ probes: [] })],
  ['no fresh observations', { summary: summary({ temperature_c: null, included_probe_count: 0,
    oldest_measured_at: null, newest_measured_at: null, expires_at: null, stale_probe_count: 3 }), error: null }, zone()],
]) {
  test(`${name}: daily statistics remain available without masquerading as a recent measurement`, () => {
    const view = panel({ result, currentZone });
    assert.equal(view.nodes('temperature-observed-marker').length, 0);
    assert.equal(view.text().includes(view.copy.loading), !result.summary);
    assert.ok(!view.text().includes(view.copy.temperatureNoRecent));
    assert.ok(view.text().includes(`${view.copy.temperatureDailyMean}21.0°C`));
    assert.equal(view.nodes('temperature-observed-range').length, 1);
    const accessible = view.nodes('temperature-ruler')[0].props['aria-label'];
    assert.equal(accessible.includes(view.copy.loading), !result.summary);
    assert.ok(!accessible.includes(view.copy.temperatureNoRecent));
    assert.ok(!accessible.includes(view.copy.temperatureRecentMean));
    assert.ok(!text(view.nodes('zone-temperature-heading')[0]).includes('2026-11-03'));
  });
}

test('a recent value without any daily data still renders its own marker and period', () => {
  const view = panel({ currentZone: zone({ latest_temperature: null }) });
  assert.equal(view.nodes('temperature-observed-marker').length, 1);
  assert.equal(view.nodes('temperature-summary').length, 0);
  assert.equal(view.nodes('temperature-daily-period').length, 0);
  assert.equal(view.nodes('temperature-observed-range').length, 0);
  assert.ok(!view.nodes('temperature-ruler')[0].props['aria-label'].includes(view.copy.temperatureDailyMean));
});

test('with no recent, daily or target data the panel has no invented ruler or values', () => {
  const view = panel({ result: { summary: undefined, error: null },
    currentZone: zone({ probes: [], latest_temperature: null, target_temperature_c: null }) });
  assert.ok(view.text().includes(view.copy.loading));
  assert.ok(!view.text().includes(view.copy.temperatureNoRecent));
  for (const name of ['temperature-ruler', 'temperature-observed-marker', 'temperature-summary', 'temperature-daily-period']) {
    assert.equal(view.nodes(name).length, 0);
  }
  assert.doesNotMatch(view.text(), /0\.0°C|NaN|undefined/);
});

for (const recent of [-40, 75]) {
  test(`recent ${recent}°C expands the real scale beyond daily range instead of clamping the marker`, () => {
    const view = panel({ result: { summary: summary({ temperature_c: recent }), error: null } });
    const expected = scaleUtils.buildTemperatureScale({ target: 20, average: recent, minimum: 19, maximum: 23 });
    assert.ok(expected.domain[0] < recent && recent < expected.domain[1]);
    const rulerTicks = view.nodes('temperature-ruler-tick');
    assert.deepEqual(rulerTicks.map(tick => Number(text(tick))), Array.from(expected.ticks));
    const position = node => Number.parseFloat(node.props.style['--temperature-position']);
    const marker = view.nodes('temperature-observed-marker')[0];
    assert.equal(position(marker), expected.project(recent));
    assert.ok(position(marker) > 7 && position(marker) < 93, 'The marker must not sit at a clamped domain edge');
    assert.equal(position(view.nodes('temperature-target-marker')[0]), expected.project(20));
    const band = view.nodes('temperature-observed-range')[0].props.style;
    assert.equal(Number.parseFloat(band['--temperature-range-start']), expected.project(19));
    assert.equal(Number.parseFloat(band['--temperature-range-end']), expected.project(23));
    assert.ok(recent < 19 ? position(marker) < expected.project(19) : position(marker) > expected.project(23));
    assert.equal(text(marker), `${view.copy.temperatureRecentMean} ${recent.toFixed(1)}°C`);
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: a collected location replaces the ruler with the trend, keeps recent and daily facts distinct`, () => {
    const view = panel({ language, currentZone: collectedZone() });
    assert.equal(view.nodes('temperature-ruler').length, 0);
    assert.equal(find(view.tree, node => node.type === trendStub).length, 1);
    assert.equal(text(view.nodes('temperature-recent-facts')[0]),
      `${view.copy.temperatureRecentMean}27.0°C${view.copy.targetTemperature}20.0°C`);
    assert.equal(text(view.nodes('temperature-facts')[0]), `${view.copy.temperatureDailyMean}21.0°C${view.copy.temperatureDailyGap}+1.0°C`);
    assert.equal(text(view.nodes('temperature-range-values')[0]), `${view.copy.minTemperature}19.0°C${view.copy.maxTemperature}23.0°C`);
    view.render({ summary: summary({ temperature_c: -5 }), error: null });
    assert.equal(text(view.nodes('temperature-facts')[0]), `${view.copy.temperatureDailyMean}21.0°C${view.copy.temperatureDailyGap}+1.0°C`,
      'The recent mean cannot change the daily gap');
  });

  test(`${language}: recent zero stays a value, a missing recent mean stays absent`, () => {
    const zero = panel({ language, currentZone: collectedZone({ target_temperature_c: '0' }), result: { summary: summary({ temperature_c: 0 }), error: null } });
    assert.equal(text(zero.nodes('temperature-recent-facts')[0]), `${zero.copy.temperatureRecentMean}0.0°C${zero.copy.targetTemperature}0.0°C`);
    const missing = panel({ language, currentZone: collectedZone(), result: { summary: summary({ temperature_c: null, included_probe_count: 0,
      oldest_measured_at: null, newest_measured_at: null, expires_at: null }), error: null } });
    assert.ok(!text(missing.nodes('temperature-recent-facts')[0]).includes(missing.copy.temperatureRecentMean));
  });
}
