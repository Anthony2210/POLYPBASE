import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

function read(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

function load(path, modules = {}) {
  const { outputText } = ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(modules, name), `${path}: unexpected dependency ${name}`);
      return modules[name];
    },
  });
  return exports;
}

const biologicalSalinity = load('../src/utils/biologicalSalinity.ts');
const locations = load('../src/utils/labLocations.ts', { './biologicalSalinity': biologicalSalinity });
const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;
const adminView = read('../src/components/AdminView.tsx');
const section = read('../src/components/LabConfigurationAdminSection.tsx');
const app = read('../src/App.tsx');

const zone = {
  name: 'Armoire 15',
  capacity: 40,
  target_temperature_c: '15.0',
  salinity_psu: '32.50',
};

function draft(overrides = {}) {
  return { name: 'Armoire 15', capacity: '', targetTemperature: '', salinity: '', ...overrides };
}

function plain(value) {
  return JSON.parse(JSON.stringify(value));
}

test('Laboratory configuration is an enabled Administration section on its existing route', () => {
  assert.match(adminView, /\{ key: 'environment', panelId: 'admin-environment', label: 'adminTabLocations', scope: 'aquarium' \}/);
  const disabled = adminView.match(/const DISABLED_ADMIN_SECTIONS[\s\S]*?\n\]\);/)?.[0];
  assert.ok(disabled);
  assert.doesNotMatch(disabled, /'environment'/);
  for (const key of ['references', 'transfers', 'organizations']) assert.match(disabled, new RegExp(`'${key}'`));
  assert.match(app, /environment: '\/administration\/laboratory'/);
  assert.match(adminView, /displayedSection === 'environment' && activeOrganizationId != null \? \(\s*<LabConfigurationAdminSection\s+key=\{activeOrganizationId\}/);
  assert.equal(fr.adminTabLocations, 'Configuration du laboratoire');
  assert.equal(en.adminTabLocations, 'Laboratory configuration');
});

test('Administration stays desktop-only and admin-only around the new section', () => {
  assert.match(adminView, /if \(!profile \|\| !userHasAdminRole\(profile\)\) return null/);
  assert.match(app, /activeTab === 'admin' && !isDesktopApp/);
  assert.match(app, /activeTab === 'admin' && isDesktopApp/);
});

test('the old environment workspace and its probe controls are gone', () => {
  for (const name of ['EnvironmentAdminSection', 'ZoneCapacityManager', 'ZoneCreateForm', 'ProbeCreateForm']) {
    assert.doesNotMatch(adminView, new RegExp(`function ${name}\\b`));
  }
  assert.doesNotMatch(section, /onCreateProbe|probe_type|probes/);
});

test('occupancy comes from the server count, never from the partial box collection', () => {
  assert.match(section, /\{zone\.box_count\}/);
  assert.doesNotMatch(section, /boxes\b|requestBoxCollection|boxCollection/);
  // No speculative capacity colors or warnings.
  assert.doesNotMatch(section, /getZoneOccupancyLevel|zone-occupancy|is-warning|is-full/);
});

test('an unset capacity shows a dash while zero stays a value', () => {
  assert.match(section, /\{zone\.capacity \?\? '-'\}/);
});

test('empty fields mean not set while zero is a configured value', () => {
  const result = locations.parseLabLocationDraft(draft({ capacity: '0', targetTemperature: '0', salinity: '0' }));
  assert.equal(result.ok, true);
  assert.deepEqual(plain(result.values), {
    name: 'Armoire 15', capacity: 0, target_temperature_c: '0', salinity_psu: '0',
  });

  const empty = locations.parseLabLocationDraft(draft({ name: '  Étuve 13  ' }));
  assert.deepEqual(plain(empty.values), {
    name: 'Étuve 13', capacity: null, target_temperature_c: null, salinity_psu: null,
  });
});

test('decimal comma is accepted and invalid values are reported per field', () => {
  const comma = locations.parseLabLocationDraft(draft({ targetTemperature: ' 15,5 ', salinity: '32,25' }));
  assert.equal(comma.values.target_temperature_c, '15.5');
  assert.equal(comma.values.salinity_psu, '32.25');

  const invalid = locations.parseLabLocationDraft({
    name: '   ', capacity: '1.5', targetTemperature: '15.25', salinity: '-1',
  });
  assert.equal(invalid.ok, false);
  assert.deepEqual(plain(invalid.errors), {
    name: 'required', capacity: 'capacity', targetTemperature: 'temperature', salinity: 'salinity',
  });
  for (const capacity of ['-1', 'abc', '1e3', '99999999999']) {
    assert.equal(locations.parseLabLocationDraft(draft({ capacity })).errors?.capacity, 'capacity', capacity);
  }
  assert.equal(locations.parseLabLocationDraft(draft({ targetTemperature: '1000' })).ok, false);
  assert.equal(locations.parseLabLocationDraft(draft({ targetTemperature: '-2.5' })).ok, true);
});

test('an edit draft shows stored values and sends only real changes', () => {
  assert.deepEqual(plain(locations.buildLabLocationDraft(zone)), {
    name: 'Armoire 15', capacity: '40', targetTemperature: '15', salinity: '32.5',
  });
  assert.deepEqual(plain(locations.buildLabLocationDraft({ ...zone, capacity: 0, salinity_psu: null })), {
    name: 'Armoire 15', capacity: '0', targetTemperature: '15', salinity: '',
  });

  const unchanged = locations.parseLabLocationDraft(locations.buildLabLocationDraft(zone)).values;
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, unchanged)), {});

  const edited = locations.parseLabLocationDraft(draft({ capacity: '0', targetTemperature: '15.0', salinity: '' })).values;
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, edited)), {
    capacity: 0, salinity_psu: null,
  });
  const fromNull = locations.getChangedLabLocationValues(
    { ...zone, capacity: null, target_temperature_c: null },
    { ...unchanged, capacity: 0, target_temperature_c: '0' },
  );
  assert.deepEqual(plain(fromNull), { capacity: 0, target_temperature_c: '0' });
});

test('server field errors are attached to the matching form field', () => {
  assert.deepEqual(plain(locations.getLabLocationApiFieldErrors({
    name: ['Une zone porte déjà ce nom dans cette structure.'],
    capacity: 'Invalid',
    detail: 'ignored',
  })), { name: 'Une zone porte déjà ce nom dans cette structure.', capacity: 'Invalid' });
  assert.deepEqual(plain(locations.getLabLocationApiFieldErrors(null)), {});
});

test('locations are ordered by target temperature, then naturally by name', () => {
  const ordered = [
    { ...zone, name: 'Armoire 10', target_temperature_c: null },
    { ...zone, name: 'Armoire 9', target_temperature_c: '20.0' },
    { ...zone, name: 'Armoire 10', target_temperature_c: '20.0' },
    { ...zone, name: 'Étuve', target_temperature_c: '0.0' },
  ].sort(locations.compareLabLocations).map((item) => `${item.name}@${item.target_temperature_c}`);
  assert.deepEqual(ordered, ['Étuve@0.0', 'Armoire 9@20.0', 'Armoire 10@20.0', 'Armoire 10@null']);
});

test('saving refreshes only the zone collection and sends partial updates', () => {
  const create = app.match(/async function createThermalZone[\s\S]*?\r?\n  }\r?\n/)?.[0];
  const update = app.match(/async function updateThermalZone[\s\S]*?\r?\n  }\r?\n/)?.[0];
  for (const body of [create, update]) {
    assert.ok(body);
    assert.match(body, /refreshAfterMutation\(async \(\) => \{\s*assertCurrent\(\);\s*await refreshZones\(\);/);
    assert.doesNotMatch(body, /requestBoxCollection|loadInitialData|window\.location/);
  }
  assert.match(create, /return \{ id: created\.id \};/);
  assert.match(update, /payload: ThermalZoneUpdatePayload/);
  assert.match(section, /const payload = getChangedLabLocationValues\(zone, values\);/);
});

test('the editor has explicit cancel, guarded close while saving and no-change save', () => {
  assert.match(section, /function closeUnlessSaving\(\) \{\s*if \(!isSaving\) onCancel\(\);/);
  assert.match(section, /disabled=\{isSaving \|\| !draft\.name\.trim\(\) \|\| !form\.hasChanges\}/);
  assert.match(section, /disabled=\{isSaving \|\| !draft\.name\.trim\(\)\}/);
  assert.match(section, /className="secondary-button" type="button" disabled=\{isSaving\} onClick=\{onCancel\}/);
  assert.match(section, /requestError instanceof ApiResourceCancelledError/);
});

test('every new label exists in French and English without a middle dot', () => {
  const keys = [...new Set([...section.matchAll(/t\('([A-Za-z]+)'\)/g)].map((match) => match[1]))];
  const messageKeys = [...section.matchAll(/: '(adminLab[A-Za-z]+)'/g)].map((match) => match[1]);
  for (const key of [...keys, ...messageKeys]) {
    assert.equal(typeof fr[key], 'string', `missing FR ${key}`);
    assert.equal(typeof en[key], 'string', `missing EN ${key}`);
    assert.doesNotMatch(fr[key], /·/);
  }
  assert.equal(fr.adminLabAddLocation, 'Ajouter un emplacement');
  assert.equal(fr.adminLabMaxCapacity, 'Capacité maximale');
  assert.equal(fr.adminLabEditTitle.includes('{zone}'), true);
  assert.equal(en.adminLabEditTitle.includes('{zone}'), true);
});
