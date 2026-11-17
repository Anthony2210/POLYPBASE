import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

import { zoneChartReadings } from './zone-chart-test-harness.mjs';

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
const probeStatus = load('../src/utils/probeStatus.ts');
const chartUtils = load('../src/utils/probeChart.ts');
const seriesUtils = load('../src/utils/probeSeries.ts', { './probeChart': chartUtils });
const periodUtils = load('../src/utils/probePeriod.ts');
const environment = load('../src/utils/environmentFormat.ts');
const start = new Date(2026, 10, 4, 22, 17).getTime();
const end = new Date(2026, 10, 5, 2, 17).getTime();
const raw = (time, value) => [time, null, String(value), 1, value, value, time];
const probe = (id, values = [0, 30]) => ({
  id, code: `P${id}`, location: `Shelf ${id}`, is_active: id !== 2,
  sampling_interval_seconds: 60, points: [],
  point_sources: [values.map((value, index) => raw(start + (index + 1) * 60_000, value))],
});
const payload = (overrides = {}) => ({
  zone_id: 17, start: new Date(start).toISOString(), end: new Date(end).toISOString(),
  resolution_minutes: 1, target_temperature_c: 20,
  probes: [probe(1), probe(2, [12, 40])],
  probe_average: [[start + 60_000, 6, 2], [start + 120_000, 35, 2]],
  summary: { mean_temperature_c: 23.4, target_deviation_c: 3.4, min_temperature_c: 0,
    max_temperature_c: 40, contributing_probe_count: 2, measurement_count: 4 },
  ...overrides,
});
const zone = (overrides = {}) => ({
  id: 17, organization: { id: 1 }, target_temperature_c: '99',
  probes: [{ id: 1, is_collected: false }, { id: 2, is_collected: true }],
  latest_temperature: { date: '2026-11-03', average_temperature_c: 91, min_temperature_c: 89, max_temperature_c: 93, measurement_count: 12 },
  ...overrides,
});
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
const hasClass = name => node => (node.props.className ?? '').split(' ').includes(name);
const noop = () => null;

// Exercise real JSX and state updates at component boundaries, without rendering SVG
// or requesting resources. Effects have explicit commit phases for focus assertions.
function harness(path, imports, globals = {}, exportName = 'default', extra = '') {
  const slots = [];
  let cursor = 0, tree, props, effects;
  const same = (a, b) => a && b && a.length === b.length && a.every((value, i) => Object.is(value, b[i]));
  const effect = (run, deps) => {
    const index = cursor++;
    if (!same(slots[index]?.deps, deps)) effects.push(() => {
      slots[index]?.cleanup?.();
      slots[index] = { deps, cleanup: run() };
    });
  };
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], next => { slots[index] = typeof next === 'function' ? next(slots[index]) : next; }];
    },
    useRef(initial) { const index = cursor++; return slots[index] ??= { current: initial }; },
    useMemo(factory) { cursor++; return factory(); },
    useEffect: effect, useLayoutEffect: effect,
  };
  const component = load(path, { react: hooks, 'react/jsx-runtime': jsxRuntime, ...imports }, globals, extra)[exportName];
  return {
    get tree() { return tree; },
    render(next = props) { props = next; cursor = 0; effects = []; tree = component(props); return this; },
    commit() { effects.forEach(run => run()); },
    nodes(predicate) { return find(tree, predicate); },
    text() { return text(tree); },
  };
}
function translator(language) {
  return key => {
    assert.equal(typeof catalogs[language][key], 'string', `Missing ${language} copy: ${key}`);
    return catalogs[language][key];
  };
}
function monitoring({ language = 'fr', data = payload(), error = null, isLoading = false, currentZone = zone() } = {}) {
  const document = { documentElement: { lang: language } };
  const chart = noop;
  let retries = 0;
  const state = { data, error, isLoading, retry() { retries++; } };
  const view = harness('../src/components/ThermalMonitoringChart.tsx', {
    '../hooks/useThermalMonitoring': { default: received => { assert.equal(received, currentZone); return state; } },
    '../utils/dateFormat': load('../src/utils/dateFormat.ts', {}, { document }),
    '../utils/environmentFormat': environment, '../utils/probePeriod': periodUtils,
    '../utils/probeSeries': seriesUtils,
    './ProbeSeriesChart': { default: chart,
      seriesClassName: slot => `probe-series-${slot + 1}`,
      averageSeriesStyle: series => ({ '--series-color': seriesUtils.averageSeriesColor(series) }),
    },
  }, { document }).render({ zone: currentZone, t: translator(language) });
  return Object.assign(view, { copy: catalogs[language], state,
    chart: () => view.nodes(node => node.type === chart)[0]?.props,
    metrics: () => view.nodes(node => node.type === 'dd').map(text),
    toggles: () => view.nodes(node => node.type === 'button' && typeof node.props['aria-pressed'] === 'boolean'),
    getRetries: () => retries,
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: ordered metrics use the backend summary, not the latest average or daily aggregate`, () => {
    const view = monitoring({ language });
    assert.deepEqual(view.nodes(node => node.type === 'dt').map(text),
      ['temperatureAverage', 'thermalMonitoringDeviation', 'thermalMonitoringExtrema'].map(key => view.copy[key]));
    assert.deepEqual(view.metrics(), ['23.4°C', '+3.4°C', '0.0°C / 40.0°C']);
    assert.doesNotMatch(view.text(), /35\.0°C|91\.0°C|89\.0°C|93\.0°C|99\.0°C/);
    assert.equal(view.nodes(node => node.type === 'dl').length, 1);
    assert.equal(view.nodes(hasClass('temperature-ruler')).length, 0);
    assert.equal(view.nodes(hasClass('temperature-daily-period')).length, 0);
    assert.equal(view.nodes(node => /alert|danger|warning|safe-band|is-ok|is-watch/.test(node.props.className ?? '')).length, 0);
  });

  test(`${language}: shared chart receives raw probes, server average, target and fixed exact window across midnight`, () => {
    const view = monitoring({ language });
    const chart = view.chart();
    assert.deepEqual({ ...chart.window }, { start, end });
    assert.deepEqual(Array.from(chart.series[0].readings, point => Array.from(point)), [[start + 60_000, 0], [start + 120_000, 30]]);
    assert.equal(chart.series[0].intervals.length, 0);
    assert.equal(chart.series.length, 2);
    assert.deepEqual(Array.from(chart.series, entry => [entry.id, entry.slot]), [[1, 1], [2, 0]], 'Collected probes retain historical color slots');
    assert.deepEqual(Array.from(chart.average, entry => ({ ...entry })), [
      { start: start + 60_000, end: start + 120_000, mean: 6, contributors: 2 },
      { start: start + 120_000, end: start + 180_000, mean: 35, contributors: 2 },
    ]);
    assert.equal(chart.target, 20, 'The API target wins over stale zone data');
    assert.equal(chart.locale, language === 'fr' ? 'fr-FR' : 'en-GB');
    const format = new Intl.DateTimeFormat(chart.locale, { dateStyle: 'medium', timeStyle: 'short' });
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    assert.equal(chart.ariaLabel, `${view.copy.temperatureControl}, ${format.format(start)} → ${format.format(end)} (${timezone})`);
    assert.match(chart.ariaLabel, /22:17/);
    assert.match(chart.ariaLabel, /02:17/);
    assert.equal(chart.labels.keyboardDescription, view.copy.thermalMonitoringKeyboard);
    assert.equal(chart.labels.average, view.copy.probeAverage);
    assert.deepEqual(Object.keys(chart).sort(), ['ariaLabel', 'average', 'domain', 'height', 'hiddenIds', 'labels', 'locale', 'series', 'target', 'window'].sort(), 'No zoom/reset/period callbacks');
    assert.equal(view.nodes(node => node.type === 'input' || node.type === 'select').length, 0);
    assert.deepEqual(view.nodes(node => node.type === 'button'), view.toggles(), 'The compact legend is the only chart control');
  });

  test(`${language}: compact legend identifies probes and toggles only visibility, never summary or average`, () => {
    const view = monitoring({ language });
    const legend = view.nodes(hasClass('thermal-monitoring-legend'))[0];
    assert.equal(legend.type, 'ul');
    assert.equal(legend.props['aria-label'], view.copy.probeLegendProbe);
    assert.equal(view.nodes(node => node.type === 'table').length, 0);
    assert.ok(text(legend).includes(view.copy.probeAverage));
    assert.equal(text(view.toggles()[0]), 'P1Shelf 1');
    assert.equal(text(view.toggles()[1]), `P2 (${view.copy.probeInactive})Shelf 2`);
    const before = view.chart();
    for (const toggle of view.toggles()) {
      assert.equal(toggle.props.type, 'button');
      assert.equal(toggle.props['aria-pressed'], true);
      assert.equal(toggle.props.title, view.copy.probeSeriesToggle.replace('{probe}', toggle === view.toggles()[0] ? 'P1' : 'P2'));
      toggle.props.onClick();
    }
    view.render();
    assert.ok(view.toggles().every(toggle => toggle.props['aria-pressed'] === false));
    assert.deepEqual(Array.from(view.chart().hiddenIds), [1, 2]);
    assert.equal(view.nodes(hasClass('is-hidden')).length, 2);
    assert.deepEqual(view.metrics(), ['23.4°C', '+3.4°C', '0.0°C / 40.0°C']);
    assert.deepEqual(view.chart().average, before.average);
    assert.deepEqual(view.chart().domain, before.domain);
    assert.deepEqual(view.chart().series, before.series);
    view.toggles()[0].props.onClick(); view.render();
    assert.equal(view.toggles()[0].props['aria-pressed'], true);
    assert.deepEqual(Array.from(view.chart().hiddenIds), [2]);
  });

  test(`${language}: one probe, zero summary, zero target and zero average are real values`, () => {
    const view = monitoring({ language, data: payload({ probes: [probe(1, [0])], target_temperature_c: 0,
      probe_average: [[start + 60_000, 0, 1]],
      summary: { mean_temperature_c: 0, target_deviation_c: 0, min_temperature_c: 0, max_temperature_c: 0, contributing_probe_count: 1, measurement_count: 1 },
    }) });
    assert.deepEqual(view.metrics(), ['0.0°C', '0.0°C', '0.0°C / 0.0°C']);
    assert.equal(view.chart().target, 0);
    assert.equal(view.chart().average[0].mean, 0);
    assert.equal(view.chart().series[0].readings[0][1], 0);
    assert.equal(view.toggles().length, 1);
    assert.equal(view.nodes(node => node.props.role === 'status').length, 0);
    assert.ok(view.chart().domain[0] < 0 && view.chart().domain[1] > 0);
  });

  test(`${language}: an exact midnight end stays exclusive and is not replaced by now or daily bounds`, () => {
    const midnight = new Date(2026, 10, 5, 0, 0).getTime();
    const serverStart = midnight - 4 * 3_600_000;
    const view = monitoring({ language, data: payload({ start: new Date(serverStart).toISOString(), end: new Date(midnight).toISOString() }) });
    assert.deepEqual({ ...view.chart().window }, { start: serverStart, end: midnight });
    const format = new Intl.DateTimeFormat(view.chart().locale, { dateStyle: 'medium', timeStyle: 'short' });
    assert.ok(view.chart().ariaLabel.includes(`${format.format(serverStart)} → ${format.format(midnight)}`));
    assert.match(view.chart().ariaLabel, /00:00/);
    assert.deepEqual(view.metrics(), ['23.4°C', '+3.4°C', '0.0°C / 40.0°C']);
  });

  test(`${language}: absent average and missing summary never trigger client or daily fallback`, () => {
    const data = payload({ probe_average: [] });
    const view = monitoring({ language, data });
    assert.equal(view.chart().average.length, 0);
    assert.equal(view.chart().labels.average, undefined);
    assert.ok(!text(view.nodes(hasClass('thermal-monitoring-legend'))[0]).includes(view.copy.probeAverage));
    assert.equal(view.toggles().length, 2);
    assert.deepEqual(view.metrics(), ['23.4°C', '+3.4°C', '0.0°C / 40.0°C']);
    view.state.data = { ...payload(), summary: { ...data.summary, mean_temperature_c: null, target_deviation_c: null,
      min_temperature_c: null, max_temperature_c: null } };
    view.render();
    assert.equal(view.chart().average.length, 2);
    assert.deepEqual(view.metrics(), ['-', '-', '- / -']);
    assert.doesNotMatch(view.text(), /35\.0°C|91\.0°C|99\.0°C/);
  });

  test(`${language}: negative deviation and absent target are not inferred from curve or zone values`, () => {
    const data = payload();
    const view = monitoring({ language, data: { ...data, summary: { ...data.summary, mean_temperature_c: 18.6, target_deviation_c: -1.4 } } });
    assert.deepEqual(view.metrics(), ['18.6°C', '−1.4°C', '0.0°C / 40.0°C']);
    view.state.data = { ...data, target_temperature_c: null, summary: { ...data.summary, target_deviation_c: null } };
    view.render();
    assert.equal(view.chart().target, null);
    assert.deepEqual(view.metrics(), ['23.4°C', '-', '0.0°C / 40.0°C']);
  });

  test(`${language}: loading, empty and error remain distinct with no daily fallback; retry retains data`, () => {
    const view = monitoring({ language, data: null, isLoading: true });
    assert.equal(view.tree.props['aria-busy'], true);
    assert.equal(text(view.nodes(node => node.props.role === 'status')[0]), view.copy.loading);
    assert.equal(view.chart(), undefined);
    assert.deepEqual(view.metrics(), ['-', '-', '- / -']);
    assert.ok(!view.text().includes(view.copy.thermalMonitoringEmpty));
    view.state.isLoading = false;
    view.state.data = payload({ probes: [], probe_average: [], summary: { mean_temperature_c: null, target_deviation_c: null,
      min_temperature_c: null, max_temperature_c: null, contributing_probe_count: 0, measurement_count: 0 } });
    view.render();
    assert.equal(view.tree.props['aria-busy'], false);
    assert.equal(text(view.nodes(node => node.props.role === 'status')[0]), view.copy.thermalMonitoringEmpty);
    assert.deepEqual(view.metrics(), ['-', '-', '- / -']);
    assert.deepEqual({ ...view.chart().window }, { start, end });
    assert.equal(view.toggles().length, 0);
    view.state.data = null; view.state.error = new Error('Private transport details'); view.render();
    assert.ok(view.text().includes(view.copy.temperatureRecentError));
    assert.ok(!view.text().includes(view.copy.thermalMonitoringEmpty));
    assert.ok(!view.text().includes(view.copy.loading));
    assert.doesNotMatch(view.text(), /Private transport details|91\.0°C/);
    view.nodes(node => node.type === 'button' && text(node) === view.copy.probeRetry)[0].props.onClick();
    assert.equal(view.getRetries(), 1);
    view.state.data = payload(); view.render();
    assert.equal(view.chart().series.length, 2);
    assert.deepEqual(view.metrics(), ['23.4°C', '+3.4°C', '0.0°C / 40.0°C']);
    view.state.data = null; view.state.error = null; view.state.isLoading = true; view.render();
    assert.equal(view.chart(), undefined);
    assert.equal(view.toggles().length, 0);
    assert.deepEqual(view.metrics(), ['-', '-', '- / -']);
  });
}

function panel({ language = 'fr', currentZone = zone(), canRecord = true, monitoring = { has_collected_probes: true, manual_entry_available: true, last_reading_at: null }, error = null } = {}) {
  const document = { documentElement: { lang: language } };
  const chart = zoneChartReadings();
  const chartCalls = [];
  const Monitoring = () => null;
  const Historical = () => null;
  let retries = 0;
  const view = harness('../src/components/ZonesView.tsx', {
    '../api/client': {}, '../utils/errors': {},
    '../utils/dateFormat': load('../src/utils/dateFormat.ts', {}, { document }), '../utils/probeStatus': probeStatus,
    '../hooks/useProbeMonitoring': { default: (received, initial) => {
      assert.equal(received, currentZone); assert.equal(initial, chart.initial);
      return { monitoring, error, revalidate() { retries++; } };
    } },
    './ThermalMonitoringChart': { default: Monitoring },
    './ZoneProbeCharts': { ZoneProbeChart: Historical, useZoneChartReadings: (received, enabled) => {
      chartCalls.push({ zone: received, enabled }); return chart;
    } },
    './ZoneMovementHistory': { ZoneRecentMovements: noop },
    ...Object.fromEntries(['BoxTrackingPreview', 'DetailBackButton', 'ModalPortal', 'PageLoader', 'PolypbaseIcon'].map(name => [`./${name}`, { default: noop }])),
  }, { document }, 'TemperatureControlPanel', '\nexport { TemperatureControlPanel };')
    .render({ zone: currentZone, canRecordManualTemperature: canRecord,
      onRecordManualTemperature() { assert.fail('Rendering must not write'); }, t: translator(language) });
  return Object.assign(view, { chart, chartCalls, Monitoring, Historical, copy: catalogs[language], getRetries: () => retries });
}
for (const language of ['fr', 'en']) {
  test(`${language}: panel has exactly one period header and delegates monitoring while retaining the historical chart`, () => {
    const currentZone = zone();
    const view = panel({ language, currentZone });
    const heading = view.nodes(hasClass('zone-temperature-heading'))[0];
    assert.equal(text(heading), view.copy.temperatureControl + view.copy.thermalMonitoringPeriod);
    assert.equal(view.text().split(view.copy.thermalMonitoringPeriod).length - 1, 1);
    const child = view.nodes(node => node.type === view.Monitoring);
    assert.equal(child.length, 1); assert.equal(child[0].props.zone, currentZone);
    assert.equal(child[0].props.t('thermalMonitoringPeriod'), view.copy.thermalMonitoringPeriod);
    const history = view.nodes(node => node.type === view.Historical);
    assert.equal(history.length, 1); assert.equal(history[0].props.chart, view.chart);
    assert.equal(history[0].props.zone, currentZone);
    assert.deepEqual(view.chartCalls, [{ zone: currentZone, enabled: true }]);
    assert.ok(view.tree.props.children.includes(history[0]));
    assert.equal(view.nodes(node => node.type === 'dd').length, 0, 'The panel no longer owns daily or recent numeric facts');
    assert.equal(view.nodes(hasClass('temperature-ruler')).length, 0);
  });

  test(`${language}: manual action opens the existing modal and restores action focus on cancel`, () => {
    const view = panel({ language }); view.commit();
    const action = view.nodes(node => node.props['aria-label'] === view.copy.manualTemperatureAction)[0];
    assert.equal(action.props.type, 'button');
    assert.equal(action.props.className, 'icon-button zone-temperature-action');
    let focused = 0; action.ref.current = { focus() { focused++; } };
    action.props.onClick(); view.render(); view.commit();
    assert.equal(view.nodes(node => node.props['aria-label'] === view.copy.manualTemperatureAction).length, 0);
    const modal = view.nodes(node => node.type.name === 'TemperatureEntryModal')[0];
    assert.ok(modal); assert.equal(modal.props.isSaving, false); assert.equal(modal.props.value, '');
    modal.props.onValueChange('0'); view.render();
    assert.equal(view.nodes(node => node.type.name === 'TemperatureEntryModal')[0].props.value, '0');
    modal.props.onCancel(); view.render(); view.commit();
    assert.equal(view.nodes(node => node.type.name === 'TemperatureEntryModal').length, 0);
    assert.equal(focused, 1);
    assert.equal(view.nodes(node => node.type === view.Historical).length, 1);
  });

  test(`${language}: server manual capability and permission still gate action; monitoring error offers retry`, () => {
    for (const canRecord of [false, true]) for (const available of [false, true]) {
      const view = panel({ language, canRecord, monitoring: { has_collected_probes: true, manual_entry_available: available, last_reading_at: null } });
      assert.equal(view.nodes(node => node.props['aria-label'] === view.copy.manualTemperatureAction).length, canRecord && available ? 1 : 0);
      assert.ok(view.text().includes(view.copy.probeNoReadingYet));
    }
    const view = panel({ language, error: 'Private transport details' });
    const status = view.nodes(node => node.props.role === 'status')[0];
    assert.equal(text(status), view.copy.probeMonitoringError + view.copy.probeRetry);
    view.nodes(node => node.type === 'button' && text(node) === view.copy.probeRetry)[0].props.onClick();
    assert.equal(view.getRetries(), 1);
    assert.doesNotMatch(view.text(), /Private transport details/);
    assert.equal(view.nodes(node => node.type === view.Monitoring).length, 1);
  });
}
test('manual-only and uninstrumented locations still mount monitoring, without enabling the historical chart', () => {
  for (const probes of [[], [{ id: 1, is_collected: false }]]) {
    const currentZone = zone({ probes });
    const view = panel({ currentZone, monitoring: { has_collected_probes: false, manual_entry_available: true } });
    assert.equal(view.nodes(node => node.type === view.Monitoring).length, 1);
    assert.equal(view.nodes(node => node.type === view.Historical).length, 0);
    assert.deepEqual(view.chartCalls, [{ zone: currentZone, enabled: false }]);
    assert.equal(view.nodes(node => node.props['aria-label'] === view.copy.manualTemperatureAction).length, 1);
    assert.doesNotMatch(view.text(), /91\.0°C|89\.0°C|93\.0°C/);
  }
});
