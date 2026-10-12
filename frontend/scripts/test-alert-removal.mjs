import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { loadComponent } from './ui-primitives-test-harness.mjs';

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
const runtimeModules = { 'react/jsx-runtime': require('react/jsx-runtime') };
const PolypbaseIcon = loadComponent('PolypbaseIcon', runtimeModules);
const { outputText: dateFormatCode } = ts.transpileModule(readSource('utils/dateFormat.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const dateFormatExports = {};
vm.runInNewContext(dateFormatCode, {
  exports: dateFormatExports,
  document: { documentElement: { lang: 'fr' } },
});
const presentationModules = { ...runtimeModules, './PolypbaseIcon': { default: PolypbaseIcon } };
const boxFunctions = {
  BoxSearchStatus: loadComponent('BoxSearchStatus', runtimeModules),
  RecentAccessList: loadComponent('RecentAccessList', presentationModules),
  SuggestionList: loadComponent('SuggestionList', {
    ...presentationModules,
    '../utils/dateFormat': dateFormatExports,
  }),
};

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
    assert.match(html, /<small>16 sept\. 2026<\/small>/);
    assert.doesNotMatch(html, /2026-09-16/);
    assert.match(html, /aria-selected="true"/);
    assert.doesNotMatch(html, /alert|NaN|undefined/);
    const recent = renderToStaticMarkup(React.createElement(boxFunctions.RecentAccessList, {
      boxes: [zeroBox], isPhoneLayout, onSelectBox: noop, t,
    }));
    assert.match(recent, /ZERO-BOX/);
    assert.doesNotMatch(recent, /alert|NaN|undefined/);
  }
});

function nodes(tree, predicate) {
  if (!tree || typeof tree !== 'object') return [];
  return [ ...(predicate(tree) ? [tree] : []),
    ...[tree.props?.children].flat(Infinity).flatMap(child => nodes(child, predicate)) ];
}

const sampleBoxes = [
  { id: 7, global_code: 'BOX-7', species: { scientific_name: 'Aurelia aurita' }, thermal_zone: { name: 'Cold room' },
    latest_measurement: { polyp_count: 0, ephyrae_count: 0, measured_on: '2026-09-16' } },
  { id: 3, global_code: 'BOX-3', species: { scientific_name: 'Cassiopea' }, thermal_zone: null, latest_measurement: null },
];

for (const status of ['idle', 'loading', 'error']) {
  test(`real search status exposes ${status} feedback and retries only on activation`, () => {
    let retries = 0;
    const tree = boxFunctions.BoxSearchStatus({ status, onRetry: () => retries++, t });
    const html = renderToStaticMarkup(tree);
    assert.equal(tree.props.role, status === 'error' ? 'alert' : 'status');
    assert.match(html, status === 'error' ? /boxCollectionLoadError/ : /loading/);
    const buttons = nodes(tree, node => node.type === 'button');
    assert.equal(retries, 0);
    assert.equal(buttons.length, status === 'error' ? 1 : 0);
    if (status === 'error') {
      assert.equal(buttons[0].props.type, 'button');
      assert.equal(buttons[0].props.children, 'boxCollectionRetry');
      buttons[0].props.onClick();
      assert.equal(retries, 1);
    }
  });
}

for (const isPhoneLayout of [false, true]) {
  const suggestionProps = {
    boxes: sampleBoxes, isPhoneLayout, listId: 'focused-results', resultIdPrefix: 'focused-box',
    selectedBoxId: 3, totalCount: 28, heading: 'Focused results', t,
  };

  test(`real suggestions preserve zero-count empty feedback and optional clear (phone=${isPhoneLayout})`, () => {
    for (const hasClear of [false, true]) {
      let clears = 0;
      const tree = boxFunctions.SuggestionList({ ...suggestionProps, boxes: [], totalCount: 0,
        onSelectBox: () => assert.fail('Empty results cannot select'),
        ...(hasClear ? { onClear: () => clears++ } : {}),
      });
      const html = renderToStaticMarkup(tree);
      assert.match(html, /aria-label="Focused results"/);
      assert.match(html, /aria-live="polite">0<\/span>/);
      assert.match(html, /id="focused-results" role="status"/);
      assert.match(html, /searchNoResults/);
      assert.doesNotMatch(html, /role="listbox"|role="option"/);
      const buttons = nodes(tree, node => node.type === 'button');
      assert.equal(buttons.length, Number(hasClear));
      assert.equal(clears, 0);
      if (hasClear) {
        assert.equal(buttons[0].props.children, 'searchClear');
        buttons[0].props.onClick();
        assert.equal(clears, 1);
      } else assert.doesNotMatch(html, /searchClear/);
    }
  });

  test(`real suggestions preserve IDs, selection timing, responsive columns and measurements (phone=${isPhoneLayout})`, () => {
    const selections = [];
    const tree = boxFunctions.SuggestionList({ ...suggestionProps, onSelectBox: id => selections.push(id) });
    const html = renderToStaticMarkup(tree);
    assert.match(html, /id="focused-results" role="listbox"/);
    assert.match(html, /aria-live="polite">28<\/span>/);
    const options = nodes(tree, node => node.props?.role === 'option');
    assert.deepEqual(options.map(node => node.props.id), ['focused-box-7', 'focused-box-3']);
    assert.deepEqual(options.map(node => node.props['aria-selected']), [false, true]);
    assert.deepEqual(options.map(node => node.props.className), ['suggestion-row', 'suggestion-row is-selected']);
    assert.deepEqual(selections, [], 'Rendering must not select a result');
    for (const [index, option] of options.entries()) {
      assert.equal(option.props.type, 'button');
      const row = renderToStaticMarkup(option);
      assert.match(row, /suggestion-identity/);
      assert.match(row, /suggestion-reading/);
      assert.match(row, /suggestion-location/);
      assert.match(row, /class="suggestion-chevron" aria-hidden="true"/);
      assert.match(row, /<svg/);
      if (isPhoneLayout) {
        assert.doesNotMatch(row, /suggestion-context/);
        assert.match(row, /<small>(Aurelia aurita|Cassiopea)<\/small>/);
      } else {
        assert.match(row, /suggestion-context/);
        assert.doesNotMatch(row, /<small>(Aurelia aurita|Cassiopea)<\/small>/);
      }
      if (index === 0) {
        assert.match(row, /0 polyps, 0 ephyrae/);
        assert.match(row, /<small>16 sept\. 2026<\/small>/);
        assert.match(row, /Cold room/);
        assert.doesNotMatch(row, /noMeasurementHistory|noZone/);
      } else {
        assert.match(row, /noMeasurementHistory/);
        assert.match(row, /noZone/);
        assert.doesNotMatch(row, /0 polyps|sept\. 2026/);
      }
      option.props.onClick();
      assert.deepEqual(selections, sampleBoxes.slice(0, index + 1).map(box => box.id));
    }
    const unselected = boxFunctions.SuggestionList({ ...suggestionProps, selectedBoxId: null, onSelectBox: noop });
    assert.ok(nodes(unselected, node => node.props?.role === 'option').every(node => node.props['aria-selected'] === false));
  });

  test(`real recent access preserves empty feedback, input ordering and responsive chevrons (phone=${isPhoneLayout})`, () => {
    const selections = [];
    const props = { isPhoneLayout, onSelectBox: id => selections.push(id), t };
    const empty = boxFunctions.RecentAccessList({ ...props, boxes: [] });
    assert.match(renderToStaticMarkup(empty), /noRecentScans/);
    assert.equal(nodes(empty, node => node.type === 'button').length, 0);
    const tree = boxFunctions.RecentAccessList({ ...props, boxes: sampleBoxes });
    assert.equal(tree.props['aria-label'], 'recentAccessAriaLabel');
    const html = renderToStaticMarkup(tree);
    assert.match(html, /recentAccess<\/h2>/);
    const buttons = nodes(tree, node => node.type === 'button');
    assert.equal(buttons.length, 2);
    assert.deepEqual(selections, []);
    buttons.forEach((button, index) => {
      const row = renderToStaticMarkup(button);
      assert.match(row, new RegExp(sampleBoxes[index].global_code));
      assert.match(row, new RegExp(sampleBoxes[index].species.scientific_name));
      assert.match(row, index === 0 ? /Cold room/ : /noZone/);
      if (isPhoneLayout) assert.doesNotMatch(row, /recent-box-chevron|<svg/);
      else {
        assert.match(row, /class="recent-box-chevron" aria-hidden="true"/);
        assert.match(row, /<svg/);
      }
      assert.equal(button.props.type, 'button');
      button.props.onClick();
      assert.deepEqual(selections, sampleBoxes.slice(0, index + 1).map(box => box.id));
    });
  });
}

test('unrelated lifecycle warnings, confirmation variants, and accessible errors remain', () => {
  assert.match(readSource('components/BoxLifecycleModal.tsx'), /boxLifecycleActiveWithoutLocationWarning/);
  assert.match(readSource('components/BoxInventoryBatchModal.tsx'), /boxInventoryBatchActiveWarning/);
  assert.match(readSource('components/ConfirmActionModal.tsx'), /'warning' \| 'danger'/);
  assert.match(readSource('components/ZonesView.tsx'), /role="alert"/);
  assert.match(readSource('components/ApplicationErrorNotice.tsx'), /role="alert"/);
});
