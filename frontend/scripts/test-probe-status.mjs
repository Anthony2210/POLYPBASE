import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

function load(path, imports = {}, globals = {}, extra = '') {
  const source = readFileSync(new URL(path, import.meta.url), 'utf8') + extra;
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, Intl, Date, require: (name) => imports[name] ?? {}, ...globals });
  return exports;
}

const status = load('../src/utils/probeStatus.ts');
const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;
const now = Date.UTC(2026, 9, 29, 12, 38);

test('elapsed time uses completed minutes, hours and days, never the fallback threshold', () => {
  for (const [minutes, count, unit] of [[0, 0, 'minute'], [59, 59, 'minute'], [60, 1, 'hour'], [540, 9, 'hour'], [1439, 23, 'hour'], [1440, 1, 'day'], [3000, 2, 'day']]) {
    const result = status.getProbeElapsedTime(new Date(now - minutes * 60000).toISOString(), now);
    assert.equal(result.count, count);
    assert.equal(result.unit, unit);
  }
  for (const value of [null, undefined, '', 'invalid']) assert.equal(status.getProbeElapsedTime(value, now), null);
  assert.equal(status.getProbeElapsedTime(new Date(now + 60000).toISOString(), now).count, 0);
});

test('short reading dates omit the year in both UI languages', () => {
  const date = new Date(2026, 9, 29, 3, 38).toISOString();
  assert.match(status.formatProbeReadingTime(date, 'fr-FR'), /29 oct.*03:38/);
  assert.match(status.formatProbeReadingTime(date, 'en-GB'), /29 Oct.*03:38/);
  assert.doesNotMatch(status.formatProbeReadingTime(date, 'fr-FR'), /2026/);
});

function panel(monitoring, canRecord = true, language = 'fr') {
  let clock = now;
  let cursor = 0;
  const slots = [];
  const effects = [];
  const timers = new Map();
  const events = {};
  const hooks = {
    useState(initial) {
      const index = cursor++;
      if (!(index in slots)) slots[index] = typeof initial === 'function' ? initial() : initial;
      return [slots[index], (value) => { slots[index] = value; }];
    },
    useRef(value) { const index = cursor++; return slots[index] ??= { current: value }; },
    useEffect(effect) { effects.push(effect); },
    useLayoutEffect() {},
  };
  const noop = () => null;
  const { TemperatureControlPanel, formatTemperatureDelta } = load('../src/components/ZonesView.tsx', {
    react: hooks, 'react/jsx-runtime': jsxRuntime,
    '../hooks/useProbeMonitoring': { default: () => ({ monitoring, error: null, revalidate() {} }) },
    '../utils/probeStatus': status,
    '../utils/dateFormat': { formatDisplayDate: (value) => value, formatDisplayDateTime: (value) => value, getDocumentLocale: () => language === 'fr' ? 'fr-FR' : 'en-GB' },
    '../utils/temperatureScale': { buildTemperatureScale: () => null },
    './PolypbaseIcon': { default: noop },
  }, {
    Date: class extends Date { static now() { return clock; } },
    setInterval(fn, delay) { assert.equal(delay, 60000); timers.set(1, fn); return 1; },
    clearInterval(id) { timers.delete(id); },
    addEventListener(name, fn) { events[name] = fn; },
    removeEventListener(name) { delete events[name]; },
    document: { addEventListener(name, fn) { events[name] = fn; }, removeEventListener(name) { delete events[name]; } },
  }, String.fromCharCode(10) + 'export { TemperatureControlPanel, formatTemperatureDelta };');
  let tree;
  const render = () => {
    cursor = 0; effects.length = 0;
    tree = TemperatureControlPanel({
      zone: { id: 1, probes: [{ is_collected: true }], target_temperature_c: '0', latest_temperature: { date: '2026-10-29', average_temperature_c: 0, computed_from_probes: true } },
      canRecordManualTemperature: canRecord, t: (key) => (language === 'fr' ? fr : en)[key] ?? key, onRecordManualTemperature() {},
    });
  };
  function find(predicate, node = tree) {
    if (Array.isArray(node)) return node.flatMap((child) => find(predicate, child));
    if (!React.isValidElement(node)) return [];
    return [...(predicate(node) ? [node] : []), ...find(predicate, node.props.children ?? null)];
  }
  function text(node = tree) {
    if (Array.isArray(node)) return node.map(text).join('');
    if (React.isValidElement(node)) return text(node.props.children ?? null);
    return node == null ? '' : String(node);
  }
  render();
  return { render, find, text, formatTemperatureDelta, timers, events, effects, advance(minutes) { clock += minutes * 60000; } };
}

const monitoring = (manual = true, last = new Date(now - 9 * 3600000).toISOString()) => ({
  has_collected_probes: true, manual_entry_available: manual, last_reading_at: last, fallback_after_hours: 6,
});

test('silent status is compact, factual and translated without a provenance label or probe icon', () => {
  const view = panel(monitoring());
  assert.match(view.text(), / — il y a 9 h/);
  assert.match(view.text(), /Sondes silencieuses/);
  assert.match(view.text(), /Aucune mesure reçue depuis 9 h. Vous pouvez saisir la température à la main en attendant./);
  assert.doesNotMatch(view.text(), /6 h|Moyenne du jour|·/);
  assert.equal(view.find((node) => node.props.className === 'zone-probe-stale').length, 0);
  const button = view.find((node) => node.props.className === 'secondary-button zone-probe-readings-action')[0];
  assert.equal(typeof button.props.children, 'string');
  assert.equal(view.find((node) => node.type === 'time')[0].props.dateTime, monitoring().last_reading_at);
  assert.match(panel(monitoring(), true, 'en').text(), /9 h ago.*Silent probes.*No reading received for 9 h/);
});

test('server status and user permission remain authoritative, including missing and inactive probes', () => {
  for (const available of [true, false]) for (const canRecord of [true, false]) {
    const view = panel(monitoring(available), canRecord);
    assert.equal(view.find((node) => node.props['aria-label'] === fr.manualTemperatureAction).length, available && canRecord ? 1 : 0);
    assert.equal(view.find((node) => node.props.className === 'zone-probe-silent-badge').length, available ? 1 : 0);
  }
  const absent = panel(monitoring(true, null));
  assert.match(absent.text(), /Aucune mesure reçue pour l/);
  assert.doesNotMatch(absent.text(), /depuis|il y a|6 h/);
  const inactive = panel({ ...monitoring(), has_collected_probes: false });
  assert.equal(inactive.find((node) => node.props.className === 'zone-probe-silent-badge').length, 0);
  assert.equal(inactive.find((node) => node.props.className === 'secondary-button zone-probe-readings-action').length, 1);
});

test('elapsed labels update by timer and on foregrounding, then clean up without granting permission', () => {
  const view = panel(monitoring(false));
  const cleanup = view.effects.map((effect) => effect()).filter(Boolean);
  view.advance(60); view.timers.get(1)(); view.render();
  assert.match(view.text(), /il y a 10 h/);
  view.advance(24 * 60); view.events.visibilitychange(); view.render();
  assert.match(view.text(), /il y a 1 j/);
  assert.equal(view.find((node) => node.props['aria-label'] === fr.manualTemperatureAction).length, 0);
  cleanup.forEach((fn) => fn());
  assert.equal(view.timers.size, 0);
  assert.equal(Object.keys(view.events).length, 0);
});

test('rounded zero deltas have no sign while nonzero deltas retain their sign', () => {
  const { formatTemperatureDelta } = panel(monitoring());
  for (const value of [0, -0, -0.001, -0.049, 0.001, 0.049, 20.01 - 20.02]) assert.equal(formatTemperatureDelta(value), '0.0°C');
  assert.equal(formatTemperatureDelta(-0.051), '−0.1°C');
  assert.equal(formatTemperatureDelta(0.051), '+0.1°C');
});
