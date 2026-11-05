// Regression tests for the review corrections of Laboratory configuration
// (salinity precision, explicit labels, reference-salinity wording, frozen
// controls while saving) and for its inline-actions refinement: pencil and
// delete icons, inline row editing, centered creation dialog, no steppers.
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
const app = read('../src/App.tsx');
const zone = {
  id: 7,
  name: 'Armoire 15',
  capacity: 40,
  target_temperature_c: '15.0',
  salinity_psu: '32.25',
  box_count: 3,
  is_active: true,
  organization: { id: 1 },
};

// -- Salinity precision ---------------------------------------------------------

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

test('a fractional salinity survives the edit draft and the update payload', () => {
  const editDraft = locations.buildLabLocationDraft(zone);
  assert.equal(editDraft.salinity, '32.25');
  const unchanged = locations.parseLabLocationDraft(editDraft);
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, unchanged.values)), {});
  const edited = locations.parseLabLocationDraft({ ...editDraft, salinity: '37.25' });
  assert.deepEqual(plain(locations.getChangedLabLocationValues(zone, edited.values)), { salinity_psu: '37.25' });
});

// -- Rendering harness ------------------------------------------------------------

class ApiError extends Error {
  constructor(message, data) { super(message); this.data = data; }
}
class ApiResourceCancelledError extends Error {}

/**
 * `force(initial, index)` may replace the value of the index-th useState call
 * of the render (section first, then its children) to show a given state.
 */
function loadSection(force = () => undefined) {
  let index = 0;
  const reactModule = {
    ...React,
    useState: (initial) => {
      const forced = force(initial, index++);
      return forced === undefined ? React.useState(initial) : [forced, () => {}];
    },
  };
  return load('../src/components/LabConfigurationAdminSection.tsx', {
    react: reactModule,
    'react/jsx-runtime': jsxRuntime,
    '../api/client': { ApiError, ApiResourceCancelledError },
    '../hooks/useMutationDialog': {
      default: () => ({
        dialogRef: { current: null }, initialFocusRef: { current: null }, isBusy: false, close() {}, async submit() {},
      }),
    },
    '../utils/environmentFormat': load('../src/utils/environmentFormat.ts'),
    '../utils/errors': { getErrorMessage: (error) => error.message },
    '../utils/labLocations': locations,
    '../utils/memberFeedback': load('../src/utils/memberFeedback.ts'),
    './ModalPortal': { default: ({ children }) => React.createElement('div', { 'data-portal': '' }, children) },
    './PolypbaseIcon': { default: ({ name }) => React.createElement('svg', { 'data-icon': name }) },
  });
}

const translator = (language) => (key) => (language === 'fr' ? fr : en)[key] ?? key;

function render(component, props, { language = 'fr', force } = {}) {
  const module = loadSection(force);
  const element = React.createElement(module[component], { t: translator(language), ...props });
  return renderToStaticMarkup(element);
}

function renderSection({ zones = [zone], language = 'fr', force } = {}) {
  return render('default', {
    organizationId: 1, zones, onCreateZone: async () => ({ id: 1 }), onUpdateZone: async () => {},
    onLoadZoneDeletion: async () => ({}), onDeleteZone: async () => {}, onOpenZone() {},
  }, { language, force });
}

function renderEditRow({ editedZone = zone, language = 'fr', force } = {}) {
  const module = loadSection(force);
  return renderToStaticMarkup(React.createElement('table', null, React.createElement('tbody', null,
    React.createElement(module.LabLocationEditRow, {
      zone: editedZone, onCancel() {}, onUpdate: async () => {}, onSaved() {}, t: translator(language),
    }))));
}

function renderCreateDialog({ language = 'fr', force } = {}) {
  return render('LabLocationCreateDialog', {
    organizationId: 1, onCancel() {}, onCreate: async () => ({ id: 1 }), onSaved() {},
  }, { language, force });
}


function attributes(tag) {
  return Object.fromEntries([...tag.matchAll(/\s([a-zA-Z-]+)(?:="([^"]*)")?/g)].map(([, name, value]) => [name, value ?? '']));
}

const tags = (html, name) => [...html.matchAll(new RegExp(`<${name}([^>]*)>`, 'g'))].map(([, attrs]) => attributes(attrs));
const buttons = (html) => [...html.matchAll(/<button([^>]*)>([\s\S]*?)<\/button>/g)]
  .map(([, attrs, content]) => ({ ...attributes(attrs), content }));
const text = (html) => html.replace(/<[^>]+>/g, '');
const decode = (value) => value.replace(/&#x27;/g, "'").replace(/&amp;/g, '&');

// -- Row actions ------------------------------------------------------------------

test('each row shows a pencil and a delete icon instead of the Modifier text button', () => {
  const html = renderSection({ zones: [zone, { ...zone, id: 8, name: 'Étuve 13', capacity: 0, box_count: 0 }] });
  const rowButtons = buttons(html);
  assert.equal(rowButtons.length, 5, 'add button plus two icon actions per row');
  const [edit, remove] = rowButtons.slice(1, 3);
  for (const action of [edit, remove]) {
    assert.equal(action.type, 'button');
    assert.match(action.class, /\bicon-button\b/);
    assert.equal(text(action.content), '', 'icon only: no visible text');
  }
  assert.equal(edit['aria-label'], 'Modifier Armoire 15');
  assert.equal(edit.title, 'Modifier');
  assert.match(edit.content, /data-icon="edit"/);
  assert.equal(remove['aria-label'], 'Supprimer Armoire 15');
  assert.equal(remove.title, 'Supprimer');
  assert.equal(remove['aria-haspopup'], 'dialog');
  assert.match(remove.content, /data-icon="delete"/);
  assert.equal(buttons(html)[3]['aria-label'], 'Modifier Étuve 13');
  assert.doesNotMatch(html, />Modifier</, 'the visible Modifier text button is gone');
  assert.doesNotMatch(html, /<input/, 'the read state stays free of inputs');
  // Zero is shown as a value, unset capacity is not involved here.
  assert.match(html, /<td class="lab-location-number">0<\/td>/);
});

test('icon actions keep English accessible names', () => {
  const [, edit, remove] = buttons(renderSection({ language: 'en' }));
  assert.deepEqual([edit['aria-label'], edit.title], ['Edit Armoire 15', 'Edit']);
  assert.deepEqual([remove['aria-label'], remove.title], ['Delete Armoire 15', 'Delete']);
});

test('the pencil switches only its row to edit mode and the other pencils wait', () => {
  const second = { ...zone, id: 8, name: 'Étuve 13', target_temperature_c: '20.0' };
  // useState #1 of the section is the edited row id.
  const html = renderSection({ zones: [zone, second], force: (initial, index) => (index === 1 ? 7 : undefined) });
  assert.equal(tags(html, 'tr').filter((row) => row.class === 'lab-location-edit-row').length, 1);
  assert.equal(tags(html, 'input').length, 4);
  const other = buttons(html).find((button) => button['aria-label'] === 'Modifier Étuve 13');
  assert.ok(other);
  assert.equal(other.disabled, '', 'one row is edited at a time');
  assert.match(section, /disabled=\{editingZone !== null\}/);
  assert.match(section, /onClick=\{\(\) => setEditingZoneId\(zone\.id\)\}/);
});

// -- Inline editing ---------------------------------------------------------------

test('the edit row puts each supported field in its own cell, bound to the row form', () => {
  const html = renderEditRow();
  const cells = [...html.matchAll(/<td[^>]*>/g)];
  assert.equal(cells.length, 6, 'same columns as the read row');
  const form = tags(html, 'form');
  assert.equal(form.length, 1);
  const inputs = tags(html, 'input');
  assert.deepEqual(inputs.map((input) => decode(input['aria-label'])), [
    'Nom de l’emplacement', 'Capacité maximale', 'Température consigne (°C)', 'Salinité de référence (PSU)',
  ]);
  assert.deepEqual(inputs.map((input) => input.value), ['Armoire 15', '40', '15', '32.25']);
  for (const input of inputs) {
    assert.equal(input.type, 'text', 'no number input and no spin buttons');
    assert.equal(input.form, form[0].id, 'Enter in any cell submits the row');
  }
  assert.equal(new Set(inputs.map((input) => input.id)).size, 4);
  // The active box count stays a fact, not an editable field.
  assert.match(html, /<td class="lab-location-number">3<\/td>/);
  const actions = buttons(html);
  assert.deepEqual(actions.map((button) => [button.type, button.content]), [
    ['button', 'Annuler'], ['submit', 'Enregistrer'],
  ]);
  assert.equal(actions[1].disabled, '', 'nothing to save until a value changes');
});

test('no plus/minus stepper remains in the creation dialog or the row editor', () => {
  for (const html of [renderEditRow(), renderCreateDialog()]) {
    assert.doesNotMatch(html, /zone-step-field|aria-label="[^"]*[+-]\d+"/);
    for (const input of tags(html, 'input')) assert.equal(input.type, 'text');
  }
  assert.doesNotMatch(section, /LabStepField|stepLab|CAPACITY_STEP|SALINITY_STEP|zone-step-field/);
  assert.equal(locations.stepLabCapacity, undefined);
  assert.equal(locations.stepLabSalinity, undefined);
});

test('the edit row is frozen while a save is in flight', () => {
  // The form's only boolean state is isSaving.
  const html = renderEditRow({ force: (initial) => (initial === false ? true : undefined) });
  assert.match(html, /<tr class="lab-location-edit-row" aria-busy="true">/);
  for (const input of tags(html, 'input')) assert.equal(input.disabled, '', input['aria-label']);
  for (const button of buttons(html)) assert.equal(button.disabled, '', button.content);
  assert.match(html, />Enregistrement\.\.\.</);
});

test('a failed save keeps the row open with server field errors and the request error', () => {
  const html = renderEditRow({
    force: (initial) => {
      if (initial === null) return 'Réseau indisponible';
      if (initial && typeof initial === 'object' && !Object.keys(initial).length) return { capacity: 'Valeur trop grande.' };
      return undefined;
    },
  });
  const capacity = tags(html, 'input')[1];
  assert.equal(capacity['aria-invalid'], 'true');
  const error = html.match(new RegExp(`<small class="member-field-error" id="${capacity['aria-describedby']}">([^<]+)</small>`));
  assert.equal(error?.[1], 'Valeur trop grande.');
  assert.match(html, /<tr class="lab-location-edit-feedback"><td colSpan="6"><p class="inline-error" role="alert">Réseau indisponible<\/p><\/td><\/tr>/);
  assert.deepEqual(tags(html, 'input').map((input) => input.value), ['Armoire 15', '40', '15', '32.25'], 'draft kept');
});

test('Escape cancels the row editor unless a save is running', () => {
  assert.match(section, /function handleKeyDown\(event: KeyboardEvent<HTMLTableRowElement>\) \{\s*if \(event\.key !== 'Escape'\) return;\s*event\.preventDefault\(\);\s*form\.closeUnlessSaving\(\);/);
  assert.match(section, /function closeUnlessSaving\(\) \{\s*if \(!isSaving\) onCancel\(\);/);
});

test('closing the row editor returns focus to its pencil', () => {
  assert.match(section, /function stopEditing\(zoneId: number\) \{\s*returnFocusZoneIdRef\.current = zoneId;\s*setEditingZoneId\(null\);/);
  assert.match(section, /const button = editButtonsRef\.current\.get\(returnFocusZoneIdRef\.current\);/);
});

// -- Creation dialog ----------------------------------------------------------------

test('creation opens a centered modal dialog, not the side panel', () => {
  const html = renderCreateDialog();
  assert.match(html, /^<div data-portal=""><div class="modal-backdrop" role="presentation">/);
  const [dialog] = tags(html, 'section');
  assert.equal(dialog.role, 'dialog');
  assert.equal(dialog['aria-modal'], 'true');
  assert.equal(dialog.class, 'lab-location-modal');
  const heading = html.match(/<h2 id="([^"]+)">([^<]+)<\/h2>/);
  assert.deepEqual([heading?.[1], heading?.[2]], [dialog['aria-labelledby'], 'Ajouter un emplacement']);
  assert.doesNotMatch(html, /admin-action-panel/);
  assert.doesNotMatch(section, /AdminActionPanel/);
  assert.match(section, /onClick=\{\(\) => setIsCreating\(true\)\}/);
  const actions = buttons(html).map((button) => [button.type, text(button.content)]);
  assert.deepEqual(actions, [['button', ''], ['button', 'Annuler'], ['submit', 'Enregistrer']]);
  assert.equal(buttons(html)[0]['aria-label'], 'Fermer');
});

test('every visible creation label is explicitly bound to its own input', () => {
  const html = renderCreateDialog();
  const labels = [...html.matchAll(/<label([^>]*)>([^<]*)<\/label>/g)]
    .map(([, attrs, label]) => ({ for: attributes(attrs).for, text: decode(label) }));
  assert.deepEqual(labels.map((label) => label.text), [
    'Nom de l’emplacement', 'Capacité maximale', 'Température consigne (°C)', 'Salinité de référence (PSU)',
  ]);
  const inputs = tags(html, 'input');
  assert.equal(inputs.length, 4);
  for (const [index, label] of labels.entries()) assert.equal(label.for, inputs[index].id, label.text);
  assert.deepEqual(inputs.map((input) => input.value), ['', '', '', '']);
  assert.doesNotMatch(html, /<label[^>]*>[^<]*<(input|button)/);
});

test('two editors never share input ids', () => {
  const ids = (html) => tags(html, 'input').map((input) => input.id);
  const all = [...ids(renderCreateDialog()), ...ids(renderEditRow())];
  assert.equal(all.length, 8);
  assert.ok(section.includes('const idPrefix = useId();'));
});

test('the creation dialog is frozen while a save is in flight', () => {
  const html = renderCreateDialog({ force: (initial) => (initial === false ? true : undefined) });
  assert.match(html, /<fieldset class="lab-location-fields" disabled="">/);
  const fieldset = html.match(/<fieldset[^>]*>([\s\S]*?)<\/fieldset>/);
  assert.equal([...fieldset[1].matchAll(/<input/g)].length, 4);
  for (const button of buttons(html)) assert.equal(button.disabled, '', text(button.content) || button['aria-label']);
  assert.match(html, /aria-busy="true"/);
  assert.match(html, />Enregistrement\.\.\.</);
});

test('reference salinity wording is used in the table and both editors, in both languages', () => {
  assert.equal(fr.adminLabReferenceSalinity, 'Salinité de référence (PSU)');
  assert.equal(en.adminLabReferenceSalinity, 'Reference salinity (PSU)');
  assert.match(section, /<th scope="col">\{t\('adminLabReferenceSalinity'\)\}<\/th>/);
  assert.doesNotMatch(section, /adminZoneSalinity/);
  assert.match(renderCreateDialog({ language: 'en' }), /<label for="[^"]+">Reference salinity \(PSU\)<\/label>/);
  assert.match(renderEditRow({ language: 'en' }), /aria-label="Reference salinity \(PSU\)"/);
  // Shared and observed-salinity labels elsewhere are untouched.
  assert.equal(fr.adminZoneSalinity, 'Salinité (PSU)');
  assert.equal(fr.zoneSalinityShort, 'Sal. armoire');
  assert.equal(en.adminZoneSalinity, 'Salinity (PSU)');
});

// -- Deletion ------------------------------------------------------------------------

const never = () => new Promise(() => {});

/** Renders the dialog in a given check state (effects do not run on the server). */
function renderDeletion({ check = null, deleting = false, boxCount = 3, language = 'fr', error = null } = {}) {
  return render('LabLocationDeleteDialog', {
    zone: { ...zone, box_count: boxCount }, onClose() {}, onLoadDeletion: never, onDelete: never,
  }, {
    language,
    force: (initial) => {
      if (check && initial && initial.status === 'loading') return check;
      if (deleting && initial === false) return true;
      if (error && initial === null) return error;
      return undefined;
    },
  });
}

const ready = (overrides = {}) => ({ status: 'ready', deletable: true, reasons: [], boxCount: 0, ...overrides });

test('the delete dialog names the location, states its active boxes and checks first', () => {
  const html = renderDeletion();
  const [dialog] = tags(html, 'section');
  assert.equal(dialog.role, 'dialog');
  assert.equal(dialog['aria-modal'], 'true');
  assert.equal(dialog['aria-busy'], 'true');
  assert.match(html, /^<div data-portal=""><div class="modal-backdrop confirm-action-backdrop" role="presentation">/);
  const heading = html.match(/<h2 id="([^"]+)">([^<]+)<\/h2>/);
  assert.deepEqual([heading?.[1], heading?.[2]], [dialog['aria-labelledby'], 'Supprimer Armoire 15']);
  assert.match(text(html), /Attention, il y a 3 boîtes actives dans cet emplacement\./);
  assert.match(html, /role="status">Vérification de l’historique de l’emplacement\.\.\.</);
  // While checking, no destructive action is offered.
  assert.deepEqual(buttons(html).map((button) => text(button.content) || button['aria-label']), ['Fermer', 'Fermer']);
  for (const id of dialog['aria-describedby'].split(' ')) assert.match(html, new RegExp(`id="${id}"`));
});

test('a never-used location gets a real, enabled confirmation', () => {
  const html = renderDeletion({ check: ready() });
  assert.match(text(html), /Aucune boîte active dans cet emplacement\./, 'zero boxes is stated, not hidden');
  assert.match(text(html), /Cet emplacement n’a jamais été utilisé\. La suppression est définitive\./);
  const [close, cancel, confirm] = buttons(html);
  assert.equal(close['aria-label'], 'Fermer');
  assert.equal(cancel.content, 'Annuler');
  assert.equal(confirm.content, 'Supprimer');
  assert.equal(confirm.disabled, undefined, 'the confirmation is really available');
  assert.match(confirm.class, /confirm-action-submit is-danger/);
  assert.match(tags(html, 'section')[0].class, /\bis-danger\b/);
  assert.match(renderDeletion({ check: ready(), language: 'en' }), />Delete</);
});

test('a used location explains why and offers no deletion at all', () => {
  const html = renderDeletion({ check: ready({ deletable: false, reasons: ['occupied', 'history'], boxCount: 3 }) });
  assert.match(text(html), /Attention, il y a 3 boîtes actives dans cet emplacement\./);
  assert.match(text(html), /Un emplacement qui contient des boîtes ne peut pas être supprimé\./);
  assert.match(text(html), /Cet emplacement a déjà été utilisé ou a des données associées : il est conservé avec son historique\./);
  assert.deepEqual(buttons(html).map((button) => text(button.content) || button['aria-label']), ['Fermer', 'Fermer']);
  assert.doesNotMatch(html, />Supprimer</, 'no permanently disabled confirmation');
  assert.doesNotMatch(tags(html, 'section')[0].class, /is-danger|is-warning/);

  const previouslyUsed = renderDeletion({ check: ready({ deletable: false, reasons: ['history'], boxCount: 0 }) });
  assert.match(text(previouslyUsed), /Aucune boîte active dans cet emplacement\./);
  assert.match(text(previouslyUsed), /déjà été utilisé/, 'zero active boxes is not proof of non-use');
  assert.doesNotMatch(text(previouslyUsed), /contient des boîtes/);
  // A server answer that is deletable but lists a reason is never offered.
  assert.match(section, /deletable: eligibility\.deletable && eligibility\.reasons\.length === 0,/);
});

test('a failed eligibility check offers a retry, never a deletion', () => {
  const html = renderDeletion({ check: { status: 'error', message: 'Réseau indisponible' } });
  assert.match(html, /role="alert">Impossible de vérifier cet emplacement\. Réseau indisponible</);
  assert.deepEqual(buttons(html).map((button) => text(button.content) || button['aria-label']), ['Fermer', 'Fermer', 'Réessayer']);
});

test('a deletion in progress freezes the dialog', () => {
  const html = renderDeletion({ check: ready(), deleting: true });
  assert.match(html, /aria-busy="true"/);
  for (const button of buttons(html)) assert.equal(button.disabled, '', text(button.content) || button['aria-label']);
  assert.match(html, />Suppression\.\.\.</);
});

test('the server refusal is read without ever implying deletion', () => {
  const refusal = locations.getLabLocationDeletionRefusal;
  assert.deepEqual(plain(refusal({ code: 'thermal_zone_in_use', reasons: ['occupied', 'history'] })),
    { code: 'in_use', reasons: ['occupied', 'history'] });
  assert.deepEqual(plain(refusal({ code: 'thermal_zone_in_use', reasons: ['occupied'] })),
    { code: 'in_use', reasons: ['occupied'] });
  assert.deepEqual(plain(refusal({ code: 'thermal_zone_in_use', reasons: ['probe'] })),
    { code: 'in_use', reasons: ['history'] }, 'unknown reason stays a refusal');
  assert.deepEqual(plain(refusal({ code: 'thermal_zone_in_use' })), { code: 'in_use', reasons: ['history'] });
  assert.deepEqual(plain(refusal({ code: 'thermal_zone_changed' })), { code: 'changed' });
  assert.equal(refusal({ detail: 'Not found.' }), null);
  assert.equal(refusal(null), null);
});

function deletionHarness({ deletable = true, outcome }) {
  const state = {
    check: { status: 'ready', deletable, reasons: [], boxCount: 0 },
    deleting: false, deleteError: null, rechecks: 0, deletes: [],
  };
  const context = {
    exports: {},
    isDeleting: false,
    check: state.check,
    zone,
    isMountedRef: { current: true },
    t: translator('fr'),
    ApiError,
    ApiResourceCancelledError,
    getLabLocationDeletionRefusal: locations.getLabLocationDeletionRefusal,
    getErrorMessage: (error) => error.message,
    setIsDeleting: (value) => { state.deleting = value; },
    setDeleteError: (value) => { state.deleteError = value; },
    setCheck: (value) => { state.check = value; },
    setCheckRequest: () => { state.rechecks += 1; },
    onDelete: (id) => { state.deletes.push(id); return outcome(); },
  };
  const confirmDeletion = evaluate(
    `${extractFunctionText('confirmDeletion')}\nexports.confirmDeletion = confirmDeletion;`, 'confirmDeletion', context,
  );
  return { state, completion: confirmDeletion() };
}

test('confirming deletes once; refusals and errors keep the location and say why', async () => {
  const success = deletionHarness({ outcome: async () => {} });
  await success.completion;
  assert.deepEqual(success.state.deletes, [7]);
  assert.equal(success.state.deleteError, null);
  assert.equal(success.state.deleting, false);

  const used = deletionHarness({
    outcome: async () => { throw new ApiError('Conflict', { code: 'thermal_zone_in_use', reasons: ['history'] }); },
  });
  await used.completion;
  assert.deepEqual(plain(used.state.check), { status: 'ready', deletable: false, reasons: ['history'], boxCount: 0 });
  assert.equal(used.state.deleteError, null);

  const changed = deletionHarness({
    outcome: async () => { throw new ApiError('Conflict', { code: 'thermal_zone_changed' }); },
  });
  await changed.completion;
  assert.equal(changed.state.deleteError, fr.adminLabDeleteChanged);
  assert.equal(changed.state.rechecks, 1, 'eligibility is read again');

  const network = deletionHarness({ outcome: async () => { throw new Error('offline'); } });
  await network.completion;
  assert.equal(network.state.deleteError, 'offline');
  assert.equal(network.state.check.deletable, true, 'a network error is not a refusal');

  const cancelled = deletionHarness({ outcome: async () => { throw new ApiResourceCancelledError('switch'); } });
  await cancelled.completion;
  assert.equal(cancelled.state.deleteError, null);

  const blocked = deletionHarness({ deletable: false, outcome: async () => {} });
  await blocked.completion;
  assert.deepEqual(blocked.state.deletes, [], 'an ineligible location is never sent');
});

test('a successful deletion refreshes the list, announces it and keeps focus in the section', () => {
  assert.match(section, /async function deleteZone\(zoneId: number\) \{[\s\S]*?await onDeleteZone\(zoneId\);\s*setDeletingZoneId\(null\);\s*setAnnouncement\(t\('adminLabDeleted'\)\);[\s\S]*?addButtonRef\.current\?\.focus\(\);/);
  assert.match(section, /onLoadDeletion=\{onLoadZoneDeletion\}\s*onDelete=\{deleteZone\}/);
  const remove = app.match(/async function deleteThermalZone[\s\S]*?\r?\n  }\r?\n/)?.[0];
  assert.ok(remove);
  assert.match(remove, /await apiDelete<void>\(`\/api\/thermal-zones\/\$\{zoneId\}\/`\);/);
  assert.match(remove, /refreshAfterMutation\(async \(\) => \{\s*assertCurrent\(\);\s*await refreshZones\(\);/);
  const load = app.match(/async function getThermalZoneDeletion[\s\S]*?\r?\n  }\r?\n/)?.[0];
  assert.match(load, /apiGet<ThermalZoneDeletionEligibility>\(`\/api\/thermal-zones\/\$\{zoneId\}\/deletion\/`\)/);
  assert.match(app, /onLoadZoneDeletion=\{getThermalZoneDeletion\}\s*onDeleteZone=\{deleteThermalZone\}/);
});

test('deletion is audited as a deletion in the history presentation', () => {
  const presentation = read('../src/utils/auditPresentation.ts');
  assert.match(presentation, /deletion: 'auditActionDeletion',/);
  assert.match(presentation, /\['Thermal zone deleted: ', 'auditSummaryLocationDeleted'\]/);
  assert.match(presentation, /prefix: 'Thermal zone deleted: ', render: \(rest, t\) => fillTemplate\(t\('auditDescriptionZoneDeleted'\)/);
  assert.equal(fr.auditActionDeletion, 'Suppression enregistrée');
  assert.equal(en.auditActionDeletion, 'Deletion saved');
  assert.equal(fr.auditDescriptionZoneDeleted, 'Emplacement supprimé : {zone}');
  assert.equal(en.auditSummaryLocationDeleted, 'Location deleted');
});

// -- Submission ----------------------------------------------------------------------

function extractFunctionText(name, inside = null) {
  const file = ts.createSourceFile('Section.tsx', section, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found;
  (function visit(node, owner) {
    const isOwner = ts.isFunctionDeclaration(node) && node.name?.text === inside;
    if (ts.isFunctionDeclaration(node) && node.name?.text === name && (!inside || owner)) found = node.getText(file);
    if (inside && owner && ts.isPropertyAssignment(node) && node.name.getText(file) === name) {
      found = node.initializer.getText(file);
    }
    ts.forEachChild(node, (child) => visit(child, owner || isOwner));
  })(file, false);
  assert.ok(found, `${inside ?? 'section'}: ${name}`);
  return found;
}

function evaluate(source, exportName, context) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  vm.runInNewContext(`${outputText}`, context);
  return context.exports[exportName];
}

function submitHarness({ kind, editedZone = null, draft }) {
  let resolveRequest;
  let rejectRequest;
  const request = new Promise((resolve, reject) => { resolveRequest = resolve; rejectRequest = reject; });
  const state = { saving: false, fieldErrors: {}, formError: null, draftWrites: 0, saved: [], requests: [] };
  const save = evaluate(
    `exports.save = ${extractFunctionText('save', kind === 'edit' ? 'LabLocationEditRow' : 'LabLocationCreateDialog')};`,
    'save',
    {
      exports: {},
      zone: editedZone,
      organizationId: 1,
      getChangedLabLocationValues: locations.getChangedLabLocationValues,
      onCreate: (payload) => { state.requests.push(['create', payload]); return request; },
      onUpdate: (id, payload) => { state.requests.push(['update', id, payload]); return request; },
    },
  );
  const focused = { tag: 'submit-button' };
  const context = {
    exports: {},
    document: { activeElement: focused },
    HTMLElement: Object,
    draft,
    isSaving: false,
    isMountedRef: { current: true },
    focusAfterSaveRef: { current: null },
    t: (key) => key,
    VALIDATION_MESSAGES: {},
    parseLabLocationDraft: locations.parseLabLocationDraft,
    getLabLocationApiFieldErrors: locations.getLabLocationApiFieldErrors,
    getErrorMessage: (error) => error.message,
    ApiError,
    ApiResourceCancelledError,
    save,
    setIsSaving: (value) => { state.saving = value; },
    setFieldErrors: (value) => { state.fieldErrors = value; },
    setFormError: (value) => { state.formError = value; },
    setDraft: () => { state.draftWrites += 1; },
    onCancel: () => state.saved.push('cancel'),
    onSaved: (id) => state.saved.push(id),
  };
  const handleSubmit = evaluate(
    `${extractFunctionText('handleSubmit')}\nexports.handleSubmit = handleSubmit;`, 'handleSubmit', context,
  );
  const completion = handleSubmit({ preventDefault() {} });
  return { state, context, focused, completion, resolveRequest, rejectRequest };
}

const editedDraft = { ...locations.buildLabLocationDraft(zone), salinity: '37.25' };

test('inline save: only the changed field is sent, then the row closes', async () => {
  const h = submitHarness({ kind: 'edit', editedZone: zone, draft: editedDraft });
  await Promise.resolve();
  assert.equal(h.state.saving, true);
  assert.deepEqual(plain(h.state.requests), [['update', 7, { salinity_psu: '37.25' }]]);
  assert.equal(h.context.focusAfterSaveRef.current, h.focused, 'focused control remembered');
  assert.deepEqual(h.state.saved, []);
  h.resolveRequest();
  await h.completion;
  assert.deepEqual(h.state.saved, [7]);
  assert.equal(h.state.saving, false);
  assert.equal(h.context.focusAfterSaveRef.current, null, 'success closes; the pencil gets focus back');
  assert.equal(h.state.draftWrites, 0);
});

test('inline save without a real change closes without any request', async () => {
  const h = submitHarness({ kind: 'edit', editedZone: zone, draft: locations.buildLabLocationDraft(zone) });
  await h.completion;
  assert.deepEqual(h.state.requests, []);
  assert.deepEqual(h.state.saved, ['cancel']);
});

test('inline save failure: draft kept for correction, errors shown, focus restored', async () => {
  const h = submitHarness({ kind: 'edit', editedZone: zone, draft: editedDraft });
  h.rejectRequest(new ApiError('Bad Request', { salinity_psu: ['Too many digits.'] }));
  await h.completion;
  assert.equal(h.state.saving, false);
  assert.deepEqual(h.state.saved, []);
  assert.deepEqual(plain(h.state.fieldErrors), { salinity: 'Too many digits.' });
  assert.equal(h.state.draftWrites, 0, 'the submitted draft is not reset');
  assert.equal(h.context.focusAfterSaveRef.current, h.focused, 'the effect refocuses this control once enabled');

  const refused = submitHarness({ kind: 'edit', editedZone: zone, draft: editedDraft });
  refused.rejectRequest(new ApiError('Ce compte ne peut pas modifier cette zone.', { detail: 'Ce compte ne peut pas modifier cette zone.' }));
  await refused.completion;
  assert.equal(refused.state.formError, 'Ce compte ne peut pas modifier cette zone.', 'a refused write is reported, not faked');
  assert.deepEqual(refused.state.saved, []);
});

test('creation sends zero as a value, keeps the server default type and reports failures', async () => {
  const created = submitHarness({ kind: 'create', draft: { name: 'Armoire 30', capacity: '0', targetTemperature: '0', salinity: '' } });
  await Promise.resolve();
  assert.deepEqual(plain(created.state.requests), [['create', {
    organization: 1, name: 'Armoire 30', capacity: 0, target_temperature_c: '0', salinity_psu: null,
  }]]);
  created.resolveRequest({ id: 31 });
  await created.completion;
  assert.deepEqual(created.state.saved, [31]);

  const network = submitHarness({ kind: 'create', draft: { name: 'Armoire 31', capacity: '', targetTemperature: '', salinity: '' } });
  network.rejectRequest(new Error('offline'));
  await network.completion;
  assert.equal(network.state.formError, 'offline');
  assert.equal(network.state.saving, false);
  assert.deepEqual(network.state.saved, []);
});

test('an institution switch discards a pending save silently', async () => {
  const h = submitHarness({ kind: 'edit', editedZone: zone, draft: editedDraft });
  h.rejectRequest(new ApiResourceCancelledError('cancelled'));
  await h.completion;
  assert.equal(h.state.formError, null);
  assert.deepEqual(plain(h.state.fieldErrors), {});
  assert.deepEqual(h.state.saved, []);
});

test('focus returns to the submitting control after a failed save', () => {
  assert.match(section, /useEffect\(\(\) => \{\s*if \(isSaving \|\| !focusAfterSaveRef\.current\) return;[\s\S]*?control\.focus\(\);\s*\}, \[isSaving\]\);/);
});
