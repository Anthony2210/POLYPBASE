// Regression tests for the review corrections of Laboratory configuration:
// salinity precision, explicit labels, reference-salinity wording, frozen
// controls while saving.
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

const plain = (value) => JSON.parse(JSON.stringify(value));
const locations = load('../src/utils/labLocations.ts', {
  './biologicalSalinity': load('../src/utils/biologicalSalinity.ts'),
});
const fr = load('../src/i18n/fr.ts').fr;
const en = load('../src/i18n/en.ts').en;
const section = read('../src/components/LabConfigurationAdminSection.tsx');
const zone = { id: 7, name: 'Armoire 15', capacity: 40, target_temperature_c: '15.0', salinity_psu: '32.25' };

// -- Finding 1: salinity precision ---------------------------------------------

test('salinity stepping keeps two stored decimals and round-trips exactly', () => {
  const up = locations.stepLabSalinity('32.25', 5);
  assert.equal(up, '37.25');
  assert.equal(locations.stepLabSalinity(up, -5), '32.25');
  assert.equal(locations.stepLabSalinity('32,25', 5), '37.25');
  assert.equal(locations.stepLabSalinity('32.5', 5), '37.5');
  assert.equal(locations.stepLabSalinity('0.05', -5), '0');
  assert.equal(locations.stepLabSalinity('0', 5), '5');
  assert.equal(locations.stepLabSalinity('', 5), '5');
  // Unreadable entries stay as typed so validation reports them.
  assert.equal(locations.stepLabSalinity('32.255', 5), '32.255');
  assert.equal(locations.stepLabSalinity('abc', 5), 'abc');
});

test('capacity stepping stays in whole boxes, never below zero', () => {
  assert.equal(locations.stepLabCapacity('45', 10), '55');
  assert.equal(locations.stepLabCapacity('55', -10), '45');
  assert.equal(locations.stepLabCapacity('5', -10), '0');
  assert.equal(locations.stepLabCapacity('0', 10), '10');
  assert.equal(locations.stepLabCapacity('', 10), '10');
  assert.equal(locations.stepLabCapacity('', -10), '0');
  assert.equal(locations.stepLabCapacity('1.5', 10), '1.5');
});

test('configured salinity is displayed at its stored precision, null stays distinct from zero', () => {
  assert.equal(locations.formatLabSalinity('32.25'), '32.25');
  assert.equal(locations.formatLabSalinity('32.50'), '32.5');
  assert.equal(locations.formatLabSalinity('30.00'), '30');
  assert.equal(locations.formatLabSalinity('0.00'), '0');
  assert.equal(locations.formatLabSalinity(null), '-');
  assert.equal(locations.formatLabSalinity(''), '-');
  assert.match(section, /\{formatLabSalinity\(zone\.salinity_psu\)\}/);
  assert.doesNotMatch(section, /utils\/stepValue|DecimalValue\(/, 'the shared one-decimal helper is not used here');
});

test('the shared one-decimal helper keeps its behavior for other consumers', () => {
  const shared = load('../src/utils/stepValue.ts');
  assert.equal(shared.incrementDecimalValue('32.15', 5), '37.2');
});

test('a fractional salinity survives edit, stepping and the update payload', () => {
  const editDraft = locations.buildLabLocationDraft(zone);
  assert.equal(editDraft.salinity, '32.25');
  const roundTrip = locations.stepLabSalinity(locations.stepLabSalinity(editDraft.salinity, 5), -5);
  const unchanged = locations.parseLabLocationDraft({ ...editDraft, salinity: roundTrip });
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, unchanged.values)), {});
  const stepped = locations.parseLabLocationDraft({ ...editDraft, salinity: locations.stepLabSalinity(editDraft.salinity, 5) });
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, stepped.values)), { salinity_psu: '37.25' });
});

// -- Rendered editor (only the portal panel and icon are replaced) -------------

class ApiError extends Error {
  constructor(message, data) { super(message); this.data = data; }
}
class ApiResourceCancelledError extends Error {}

function renderEditor({ saving = false, editedZone = null, language = 'fr' } = {}) {
  const catalog = language === 'fr' ? fr : en;
  // The editor's only boolean state is isSaving: force it to render the pending state.
  const reactModule = saving
    ? { ...React, useState: (initial) => (initial === false ? [true, () => {}] : React.useState(initial)) }
    : React;
  const module = load('../src/components/LabConfigurationAdminSection.tsx', {
    react: reactModule,
    'react/jsx-runtime': jsxRuntime,
    '../api/client': { ApiError, ApiResourceCancelledError },
    '../utils/environmentFormat': load('../src/utils/environmentFormat.ts'),
    '../utils/errors': { getErrorMessage: (error) => error.message },
    '../utils/labLocations': locations,
    '../utils/memberFeedback': load('../src/utils/memberFeedback.ts'),
    './AdminActionPanel': { default: ({ children }) => React.createElement('div', { 'data-panel': '' }, children) },
    './PolypbaseIcon': { default: () => null },
  });
  return renderToStaticMarkup(React.createElement(module.LabLocationEditor, {
    zone: editedZone, organizationId: 1, onCancel() {}, onCreate: async () => ({ id: 1 }),
    onUpdate: async () => {}, onSaved() {}, t: (key) => catalog[key] ?? key,
  }));
}

function attributes(tag) {
  return Object.fromEntries([...tag.matchAll(/\s([a-zA-Z-]+)(?:="([^"]*)")?/g)].map(([, name, value]) => [name, value ?? '']));
}

// -- Finding 2: explicit labels -------------------------------------------------

test('every visible field label is explicitly bound to its own input', () => {
  const html = renderEditor({ editedZone: zone });
  const labels = [...html.matchAll(/<label([^>]*)>([^<]*)<\/label>/g)]
    .map(([, attrs, text]) => ({ for: attributes(attrs).for, text }));
  assert.deepEqual(labels.map((label) => label.text), [
    'Nom de l’emplacement', 'Capacité maximale', 'Température consigne (°C)', 'Salinité de référence (PSU)',
  ]);
  const inputs = [...html.matchAll(/<input([^>]*)>/g)].map(([, attrs]) => attributes(attrs));
  assert.equal(inputs.length, 4);
  const ids = inputs.map((input) => input.id);
  assert.equal(new Set(ids).size, 4, 'unique input ids');
  for (const [index, label] of labels.entries()) {
    assert.ok(label.for, `${label.text}: label has htmlFor`);
    assert.equal(label.for, ids[index], `${label.text}: bound to its input, not to a stepper button`);
  }
  // No label wraps a control, so implicit association cannot target a button.
  assert.doesNotMatch(html, /<label[^>]*>[^<]*<(input|button)/);
  assert.deepEqual(inputs.map((input) => input.value), ['Armoire 15', '40', '15', '32.25']);
  const steppers = [...html.matchAll(/<button type="button" aria-label="([^"]+)"/g)].map(([, name]) => name);
  assert.deepEqual(steppers, [
    'Capacité maximale -10', 'Capacité maximale +10',
    'Salinité de référence (PSU) -5', 'Salinité de référence (PSU) +5',
  ]);
});

test('two editors never share input ids', () => {
  const first = renderEditor();
  const second = renderEditor();
  const ids = (html) => [...html.matchAll(/<input[^>]* id="([^"]+)"/g)].map(([, id]) => id);
  assert.equal(ids(first).length, 4);
  assert.equal(new Set(ids(first)).size, 4);
  assert.ok(section.includes('const idPrefix = useId();'));
  assert.equal(ids(second).length, 4);
});

// -- Finding 3: reference salinity wording -------------------------------------

test('reference salinity wording is used in the table and the editor, in both languages', () => {
  assert.equal(fr.adminLabReferenceSalinity, 'Salinité de référence (PSU)');
  assert.equal(en.adminLabReferenceSalinity, 'Reference salinity (PSU)');
  assert.match(section, /<th scope="col">\{t\('adminLabReferenceSalinity'\)\}<\/th>/);
  assert.doesNotMatch(section, /adminZoneSalinity/);
  assert.match(renderEditor({ language: 'en' }), /<label for="[^"]+">Reference salinity \(PSU\)<\/label>/);
  // Shared and observed-salinity labels elsewhere are untouched.
  assert.equal(fr.adminZoneSalinity, 'Salinité (PSU)');
  assert.equal(fr.zoneSalinityShort, 'Sal. armoire');
  assert.equal(en.adminZoneSalinity, 'Salinity (PSU)');
});

// -- Finding 4: editing during save --------------------------------------------

test('all fields and steppers are frozen while a save is in flight', () => {
  const idle = renderEditor();
  assert.match(idle, /<fieldset class="lab-location-fields member-add-grid">/);
  const saving = renderEditor({ saving: true });
  const fieldset = saving.match(/<fieldset[^>]*>([\s\S]*?)<\/fieldset>/);
  assert.ok(fieldset);
  assert.match(fieldset[0], /^<fieldset[^>]* disabled=""/);
  assert.equal([...fieldset[1].matchAll(/<input/g)].length, 4, 'every input sits in the disabled fieldset');
  assert.equal([...fieldset[1].matchAll(/<button/g)].length, 4, 'every stepper sits in the disabled fieldset');
  const outside = saving.replace(fieldset[0], '');
  assert.doesNotMatch(outside, /<input/);
  // Save and Cancel keep their own guards.
  for (const button of outside.match(/<button[^>]*>/g)) assert.match(button, /disabled=""/);
  assert.match(saving, />Enregistrement\.\.\.</);
  assert.match(section, /function closeUnlessSaving\(\) \{\s*if \(!isSaving\) onCancel\(\);/);
});

function submitHarness({ editedZone = null, draft }) {
  const file = ts.createSourceFile('Section.tsx', section, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let handler;
  (function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === 'handleSubmit') handler = node.getText(file);
    ts.forEachChild(node, visit);
  })(file);
  assert.ok(handler);
  let resolveRequest;
  let rejectRequest;
  const request = new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
  const state = { saving: false, fieldErrors: {}, formError: null, draftWrites: 0, saved: [], requests: [] };
  const focused = { tag: 'submit-button' };
  const context = {
    exports: {},
    document: { activeElement: focused },
    HTMLElement: Object,
    draft,
    zone: editedZone,
    isSaving: false,
    organizationId: 1,
    isMountedRef: { current: true },
    focusAfterSaveRef: { current: null },
    t: (key) => key,
    VALIDATION_MESSAGES: {},
    parseLabLocationDraft: locations.parseLabLocationDraft,
    getChangedLabLocationValues: locations.getChangedLabLocationValues,
    getLabLocationApiFieldErrors: locations.getLabLocationApiFieldErrors,
    getErrorMessage: (error) => error.message,
    ApiError,
    ApiResourceCancelledError,
    setIsSaving: (value) => { state.saving = value; },
    setFieldErrors: (value) => { state.fieldErrors = value; },
    setFormError: (value) => { state.formError = value; },
    setDraft: () => { state.draftWrites += 1; },
    onCancel: () => state.saved.push('cancel'),
    onSaved: (id) => state.saved.push(id),
    onCreate: (payload) => { state.requests.push(['create', payload]); return request; },
    onUpdate: (id, payload) => { state.requests.push(['update', id, payload]); return request; },
  };
  const { outputText } = ts.transpileModule(`${handler}\nexports.handleSubmit = handleSubmit;`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(outputText, context);
  const completion = context.exports.handleSubmit({ preventDefault() {} });
  return { state, context, focused, completion, resolveRequest, rejectRequest };
}

const editedDraft = { ...locations.buildLabLocationDraft(zone), salinity: '37.25' };

test('pending save then success: draft untouched while pending, then saved and closed', async () => {
  const h = submitHarness({ editedZone: zone, draft: editedDraft });
  assert.equal(h.state.saving, true);
  assert.deepEqual(plain(h.state.requests), [['update', 7, { salinity_psu: '37.25' }]]);
  assert.equal(h.context.focusAfterSaveRef.current, h.focused, 'focused control remembered');
  assert.deepEqual(h.state.saved, []);
  h.resolveRequest();
  await h.completion;
  assert.deepEqual(h.state.saved, [7]);
  assert.equal(h.state.saving, false);
  assert.equal(h.context.focusAfterSaveRef.current, null, 'success closes; the panel restores focus to its opener');
  assert.equal(h.state.draftWrites, 0);
});

test('pending save then failure: draft kept for correction, errors shown, focus restored', async () => {
  const h = submitHarness({ editedZone: zone, draft: editedDraft });
  h.rejectRequest(new ApiError('Bad Request', { salinity_psu: ['Too many digits.'] }));
  await h.completion;
  assert.equal(h.state.saving, false);
  assert.deepEqual(h.state.saved, []);
  assert.deepEqual(plain(h.state.fieldErrors), { salinity: 'Too many digits.' });
  assert.equal(h.state.draftWrites, 0, 'the submitted draft is not reset');
  assert.equal(h.context.focusAfterSaveRef.current, h.focused, 'the effect refocuses this control once enabled');

  const network = submitHarness({ draft: { name: 'Armoire 30', capacity: '0', targetTemperature: '', salinity: '' } });
  assert.deepEqual(plain(network.state.requests), [['create', {
    organization: 1, name: 'Armoire 30', capacity: 0, target_temperature_c: null, salinity_psu: null,
  }]]);
  network.rejectRequest(new Error('offline'));
  await network.completion;
  assert.equal(network.state.formError, 'offline');
  assert.equal(network.state.saving, false);
  assert.deepEqual(network.state.saved, []);
});

test('focus returns to the submitting control after a failed save', () => {
  assert.match(section, /useEffect\(\(\) => \{\s*if \(isSaving \|\| !focusAfterSaveRef\.current\) return;[\s\S]*?control\.focus\(\);\s*\}, \[isSaving\]\);/);
});
