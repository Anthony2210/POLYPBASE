import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const srcRoot = new URL('../src/', import.meta.url);
const readSource = (path) => readFileSync(new URL(path, srcRoot), 'utf8');

function sourceFiles(directory = srcRoot) {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const url = new URL(entry.name, directory);
    return entry.isDirectory() ? sourceFiles(new URL(`${entry.name}/`, directory)) : [url];
  });
}

// Execute the actual presentation functions without loading App's data workflows.
function loadFunctions(path, names, globals = {}) {
  const source = readSource(path);
  const ast = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const functions = ast.statements.filter((node) => ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
  assert.equal(functions.length, names.length);
  const selectedSource = functions.map((node) => node.getText(ast)).join('\n');
  const { outputText } = ts.transpileModule(`${selectedSource}\nexport { ${names.join(', ')} };`, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
      jsx: ts.JsxEmit.ReactJSX,
    },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports, require, ...globals });
  return exports;
}

const t = (key) => key;
const noop = () => {};
const zoneFunctions = loadFunctions('components/ZonesView.tsx', [
  'ZonesView', 'buildZoneOverviewEntry', 'getZoneThermalStatus',
  'getZoneOccupancyPercentage', 'getTemperatureMarkerPosition',
  'parseTemperatureNumber', 'formatTemperature', 'formatSalinity', 'formatZoneOccupancy',
], { PageLoader: ({ label }) => React.createElement('p', { role: 'status' }, label) });
const boxFunctions = loadFunctions('App.tsx', ['RecentAccessList', 'SuggestionList'], {
  PolypbaseIcon: () => null,
  formatDisplayDate: (value) => value,
});

function zone(overrides = {}) {
  return {
    id: 1,
    name: 'Test location',
    organization: { id: 1, name: 'Test institution' },
    capacity: 0,
    target_temperature_c: '0.00',
    latest_temperature: { average_temperature_c: '0.00' },
    latest_salinity: { salinity_psu: '0.00' },
    probes: [],
    ...overrides,
  };
}

function renderZone(value, isLoading = false) {
  return renderToStaticMarkup(React.createElement(zoneFunctions.ZonesView, {
    zones: [value], boxes: [], isLoading, onOpenZone: noop, t,
  }));
}

test('source contracts have no active alert data, endpoints, state, or local decrease interpretation', () => {
  const removedWorkflow = /active_alert(?:s|_count)|\bBoxAlert\b|BoxChecksModal|ZoneAlertsModal|ZoneAlertItem|\/api\/alerts\/|onResolveAlert|resolvingAlertId|alertResolveError|isChecksOpen|zoneAlertModal|polypDropDetected|polypDropCount|showLocalPolypDrop|hasPolypDropAlert|checkCount|(?:temperature|salinity)NeedsAttention|getZoneAlertItems|BellIcon/;
  for (const path of sourceFiles().filter((url) => /\.tsx?$/.test(url.pathname))) {
    assert.doesNotMatch(readFileSync(path, 'utf8'), removedWorkflow, path.pathname);
  }
});

test('dead alert selectors and active alert translations are absent', () => {
  for (const path of sourceFiles().filter((url) => /\.css$/.test(url.pathname))) {
    assert.doesNotMatch(readFileSync(path, 'utf8'), /box-alert|entity-header__alert|zone-card-alert|zone-alert|recent-alert|suggestion-alert|has-alerts|box-check|check-severity|bell-icon|alert-resolve|--alert-/, path.pathname);
  }
  for (const language of ['fr', 'en']) {
    assert.doesNotMatch(readSource(`i18n/${language}.ts`), /^\s*(?:boxChecks\w*|alertResolve\w*|boxAlertBanner\w*|activeAlerts|polypDropAdvice\w*|checkImportance\w*|detectedSignal|suggestedAction|boxAttention|boxAttentionTitle|boxesHealthy|zoneNoAttention|zoneSummaryAttention|zoneAttentionTitle|zoneFilterAttention|zoneOverviewAttention\w*|zoneOverviewNoProbe|zoneOverviewMissingMeasurements|temperatureOk|temperatureWatch|problemSummary):/m);
    assert.match(readSource(`i18n/${language}.ts`), /auditDescriptionAlertResolved:/);
    assert.match(readSource(`i18n/${language}.ts`), /auditObjectAlert:/);
    assert.match(readSource(`i18n/${language}.ts`), /auditSummaryAlertResolved:/);
  }
});

test('zone cards retain factual zero temperatures, salinity, and capacity without alerts', () => {
  const value = zone();
  const entry = zoneFunctions.buildZoneOverviewEntry(value, []);
  assert.equal(entry.targetTemperature, 0);
  assert.equal(entry.measuredTemperature, 0);
  assert.equal(entry.referenceTemperature, 0);
  assert.equal(zoneFunctions.getZoneThermalStatus(entry), 'recorded');
  const html = renderZone(value);
  assert.match(html, /temperatureShort<\/small><strong>0\.0°C/);
  assert.match(html, /zoneTarget<\/small><strong>0\.0°C/);
  assert.match(html, /zoneSalinity<\/small><strong>0\.0/);
  assert.match(html, /zone-occupancy">0 \/ 0/);
  assert.match(html, /zone-card-target/);
  assert.match(html, /zone-card-current/);
  assert.equal((html.match(/<button/g) ?? []).length, 1);
  assert.doesNotMatch(html, /alert|dialog|severity|attention/i);
});

test('zone target is never substituted for a missing observed temperature or vice versa', () => {
  const targetOnly = renderZone(zone({ target_temperature_c: '20.00', latest_temperature: null, latest_salinity: null }));
  assert.match(targetOnly, /temperatureShort<\/small><strong>-/);
  assert.match(targetOnly, /zoneTarget<\/small><strong>20\.0°C/);
  assert.match(targetOnly, /zoneSalinity<\/small><strong>-/);
  assert.doesNotMatch(targetOnly, /zone-card-current/);

  const observedOnly = zone({ target_temperature_c: null, latest_temperature: { average_temperature_c: '0.00' } });
  assert.equal(zoneFunctions.buildZoneOverviewEntry(observedOnly, []).referenceTemperature, 0);
  const html = renderZone(observedOnly);
  assert.match(html, /temperatureShort<\/small><strong>0\.0°C/);
  assert.match(html, /zoneTarget<\/small><strong>-/);
  assert.doesNotMatch(html, /zone-card-target|zone-card-current/);
});

test('zone loading and navigation survive removal of the alert control', () => {
  assert.match(renderZone(zone(), true), /role="status">zonesTitle/);
  const value = zone();
  const entry = zoneFunctions.buildZoneOverviewEntry(value, [
    { thermal_zone: { id: 1 }, status: 'active', latest_measurement: null },
    { thermal_zone: { id: 1 }, status: 'inactive' },
    { thermal_zone: { id: 2 }, status: 'active' },
  ]);
  assert.equal(entry.livingBoxes, 1);
  let opened = null;
  const tree = zoneFunctions.ZonesView({ zones: [value], boxes: [], isLoading: false, onOpenZone: (id) => { opened = id; }, t });
  const card = tree.props.children.props.children.props.children[0];
  card.props.children.props.onClick();
  assert.equal(opened, value.id);
});

test('search and recent access need no alert fields and distinguish zero readings from missing readings', () => {
  const zeroBox = {
    id: 1, global_code: 'ZERO-BOX', species: { scientific_name: 'Aurelia aurita' }, thermal_zone: null,
    latest_measurement: { polyp_count: 0, ephyrae_count: 0, measured_on: '2026-09-16' },
  };
  const missingBox = { ...zeroBox, id: 2, global_code: 'MISSING-BOX', latest_measurement: null };
  for (const isPhoneLayout of [false, true]) {
    const html = renderToStaticMarkup(React.createElement(boxFunctions.SuggestionList, {
      boxes: [zeroBox, missingBox], isPhoneLayout, listId: 'results', resultIdPrefix: 'box',
      selectedBoxId: zeroBox.id, totalCount: 2, heading: 'Results', onSelectBox: noop, t,
    }));
    assert.match(html, /0 polyps, 0 ephyrae/);
    assert.equal((html.match(/noMeasurementHistory/g) ?? []).length, 1);
    assert.match(html, /2026-09-16/);
    assert.match(html, /aria-selected="true"/);
    assert.doesNotMatch(html, /alert|NaN|undefined/);
    const recent = renderToStaticMarkup(React.createElement(boxFunctions.RecentAccessList, {
      boxes: [zeroBox], isPhoneLayout, onSelectBox: noop, t,
    }));
    assert.match(recent, /ZERO-BOX/);
    assert.doesNotMatch(recent, /alert|NaN|undefined/);
  }
});

test('unrelated lifecycle warnings, confirmation variants, and accessible errors remain', () => {
  assert.match(readSource('components/BoxLifecycleModal.tsx'), /boxLifecycleActiveWithoutLocationWarning/);
  assert.match(readSource('components/BoxInventoryBatchModal.tsx'), /boxInventoryBatchActiveWarning/);
  assert.match(readSource('components/ConfirmActionModal.tsx'), /'warning' \| 'danger'/);
  assert.match(readSource('components/ZonesView.tsx'), /role="alert"/);
  assert.match(readSource('components/ApplicationErrorNotice.tsx'), /role="alert"/);
});
