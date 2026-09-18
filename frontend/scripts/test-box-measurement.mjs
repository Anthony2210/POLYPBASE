import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/boxMeasurement.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const exports = {};
vm.runInNewContext(outputText, { exports });

function measurement(overrides = {}) {
  return {
    id: 1,
    measured_on: '2026-09-16',
    polyp_count: 12,
    ephyrae_count: 3,
    strobila_count: 0,
    salinity_psu: null,
    culture_status: 'good',
    needs_attention: false,
    notes: '',
    user: 'tech',
    created_at: '2026-09-16T08:00:00Z',
    can_edit: true,
    edit_deadline: '2026-09-17T08:00:00Z',
    edit_restriction: null,
    ...overrides,
  };
}

test('ISO weeks run Monday through Sunday across year boundaries', () => {
  assert.equal(exports.getIsoWeekStart('2026-09-14'), '2026-09-14');
  assert.equal(exports.getIsoWeekStart('2026-09-20'), '2026-09-14');
  assert.equal(exports.getIsoWeekStart('2026-09-21'), '2026-09-21');
  assert.equal(exports.getIsoWeekStart('2020-12-31'), '2020-12-28');
  assert.equal(exports.getIsoWeekStart('2021-01-03'), '2020-12-28');
  assert.equal(exports.getIsoWeekStart('2021-01-04'), '2021-01-04');
});

test('finds the weekly measurement and treats zero-zero as recorded', () => {
  const zero = measurement({ measured_on: '2026-09-14', polyp_count: 0, ephyrae_count: 0 });
  assert.equal(exports.findMeasurementForWeek([zero], '2026-09-20').id, zero.id);
  assert.equal(exports.findMeasurementForWeek([zero], '2026-09-21'), null);
  assert.equal(
    JSON.stringify(exports.getMeasurementFormValues(zero)),
    JSON.stringify({
      measuredOn: '2026-09-14',
      polypCount: '0',
      ephyraeCount: '0',
      salinity: '',
      notes: '',
    }),
  );
  assert.equal(exports.formatMeasurementCount(0), '0');
  assert.equal(exports.formatMeasurementCount(null), '-');
});

// The server decides editability: `can_edit` is the only input that unlocks a
// measurement. No role hint and no clock takes part, which is why this helper
// has no time parameter at all.
test('server capability controls the create, edit and locked states', () => {
  // No measurement this week: the server explicitly allows creation.
  assert.equal(exports.getMeasurementEditorMode({
    measurement: null, canCreateMeasurement: true,
  }), 'create');

  // Existing measurement the server still allows to be corrected.
  assert.equal(exports.getMeasurementEditorMode({
    measurement: measurement(), canCreateMeasurement: false,
  }), 'edit');

  // Existing measurement with the correction window closed by the server.
  assert.equal(exports.getMeasurementEditorMode({
    measurement: measurement({ can_edit: false, edit_restriction: 'edit_window_expired' }),
    canCreateMeasurement: true,
  }), 'locked');

  // The client never overrides an existing measurement's server capability.
  assert.equal(exports.getMeasurementEditorMode({
    measurement: measurement(), canCreateMeasurement: false,
  }), 'edit');

  // No measurement and no server creation capability: nothing to enter.
  assert.equal(exports.getMeasurementEditorMode({
    measurement: null, canCreateMeasurement: false,
  }), 'read_only');
});

test('recognizes only stable weekly and deadline error contracts', () => {
  assert.equal(exports.isMeasurementWeekConflict({
    status: 409, data: { code: 'measurement_week_conflict' },
  }), true);
  assert.equal(exports.isMeasurementWeekConflict({
    status: 400, data: { code: 'measurement_week_conflict' },
  }), false);
  assert.equal(exports.isMeasurementEditWindowExpired({
    status: 403, data: { code: 'edit_window_expired' },
  }), true);
  assert.equal(exports.isMeasurementEditWindowExpired({ status: 403, data: {} }), false);
});

test('measurement workflow uses PATCH for weekly corrections and locks server-denied forms', () => {
  const appSource = readSource('../src/App.tsx');
  const buttonSource = readSource('../src/components/MeasurementSaveButton.tsx');

  assert.match(
    appSource,
    /if \(editingMeasurementId != null\) \{\s*await onUpdateMeasurement\(box\.id, editingMeasurementId, payload\);/s,
  );
  assert.match(appSource, /disabled=\{isMeasurementFormLocked\}/);
  assert.match(appSource, /isDisabled=\{isMeasurementFormLocked\}/);
  assert.match(
    appSource,
    /const canShowMeasurementForm = measurementEditorMode === 'create'\s*\|\| \(isMeasurementEditorOpen && Boolean\(editingMeasurement\?\.can_edit\)\);/s,
  );
  assert.match(appSource, /setMeasurementReferenceDate\(target\.measured_on\);/);
  assert.match(buttonSource, /disabled=\{isDisabled \|\| isSaving\}/);
});

test('weekly measurement defaults to one compact server-authorized summary', () => {
  const appSource = readSource('../src/App.tsx');

  assert.match(appSource, /const \[isMeasurementEditorOpen, setIsMeasurementEditorOpen\] = useState\(false\);/);
  assert.match(
    appSource,
    /const showWeeklyMeasurementSummary = Boolean\(weeklyMeasurement\)\s*&& \(!isMeasurementEditorOpen \|\| !editingMeasurement\?\.can_edit\);/s,
  );
  assert.match(appSource, /showWeeklyMeasurementSummary \? ' measurement-summary measurement-module' : ''/);
  assert.match(appSource, /weeklyMeasurement\.polyp_count/);
  assert.match(appSource, /weeklyMeasurement\.ephyrae_count/);
  assert.match(appSource, /formatMeasurementCount\(/);

  const summaryStart = appSource.indexOf('<div className="last-reading-comment-header">');
  const summarySource = appSource.slice(summaryStart, appSource.indexOf('\n        </section>', summaryStart));
  assert.notEqual(summaryStart, -1);
  assert.match(summarySource, /showWeeklyMeasurementSummary && weeklyMeasurement\?\.can_edit \? \(/);
  assert.match(summarySource, /className="icon-button measurement-summary-edit-button"/);
  assert.match(summarySource, /aria-label=\{t\('modifyWeeklyMeasurement'\)\}/);
  assert.match(summarySource, /title=\{t\('modifyWeeklyMeasurement'\)\}/);
  assert.match(summarySource, /<Pencil aria-hidden="true" size=\{18\} \/>/);
  assert.match(summarySource, /onClick=\{openWeeklyMeasurementEditor\}/);
  assert.match(summarySource, /weeklyMeasurement\.edit_restriction === 'edit_window_expired'/);
  assert.doesNotMatch(summarySource, /className="measurement-edit-button"/);
  assert.doesNotMatch(summarySource, /Date\.now|profile|userHas/);
});

test('opening, saving and cancelling an edit preserve the PATCH-only flow', () => {
  const appSource = readSource('../src/App.tsx');
  const openStart = appSource.indexOf('function openWeeklyMeasurementEditor()');
  const openSource = appSource.slice(openStart, appSource.indexOf('\n  function cancelMeasurementEdit()', openStart));
  const cancelStart = appSource.indexOf('function cancelMeasurementEdit()');
  const cancelSource = appSource.slice(cancelStart, appSource.indexOf('\n  async function handleSubculture(', cancelStart));
  const saveStart = appSource.indexOf('async function saveMeasurement()');
  const saveSource = appSource.slice(saveStart, appSource.indexOf('\n\n  function handleSubmit(', saveStart));

  assert.match(openSource, /if \(!weeklyMeasurement\?\.can_edit\) return;/);
  assert.match(openSource, /setForm\(getMeasurementFormValues\(weeklyMeasurement\)\);/);
  assert.match(openSource, /setEditingMeasurementId\(weeklyMeasurement\.id\);/);
  assert.match(openSource, /setIsMeasurementEditorOpen\(true\);/);

  assert.match(saveSource, /if \(editingMeasurementId != null\) \{\s*await onUpdateMeasurement\(box\.id, editingMeasurementId, payload\);/s);
  assert.match(saveSource, /else \{\s*await onCreateMeasurement\(box\.id, payload\);/s);
  assert.match(saveSource, /setIsMeasurementEditorOpen\(false\);/);

  assert.match(cancelSource, /setForm\(getMeasurementFormValues\(editingMeasurement\)\);/);
  assert.match(cancelSource, /setEditingMeasurementId\(null\);/);
  assert.match(cancelSource, /setIsMeasurementEditorOpen\(false\);/);
  assert.doesNotMatch(cancelSource, /onCreateMeasurement|onUpdateMeasurement|api(?:Post|Patch)/);
  assert.match(appSource, /editingMeasurementId != null \? \(\s*<button[\s\S]*onClick=\{cancelMeasurementEdit\}/);
});

test('measurement UI state resets between boxes and follows refreshed capabilities', () => {
  const appSource = readSource('../src/App.tsx');
  const resetStart = appSource.indexOf('setForm(getInitialMeasurementForm(defaultSalinity));');
  const resetEnd = appSource.indexOf('}, [box?.id]);', resetStart);
  const resetSource = appSource.slice(resetStart, resetEnd + '}, [box?.id]);'.length);

  assert.notEqual(resetStart, -1);
  assert.match(resetSource, /setEditingMeasurementId\(null\);/);
  assert.match(resetSource, /setIsMeasurementEditorOpen\(false\);/);
  assert.match(resetSource, /}, \[box\?\.id\]\);/);
  assert.match(appSource, /if \(editingMeasurement\?\.can_edit\) return;/);
  assert.match(appSource, /setIsMeasurementEditorOpen\(false\);/);
});

test('normal back action is non-desktop and keeps deterministic localized navigation', () => {
  const appSource = readSource('../src/App.tsx');
  const frSource = readSource('../src/i18n/fr.ts');
  const enSource = readSource('../src/i18n/en.ts');
  const backControls = appSource.match(/className="icon-button box-back-action"[\s\S]*?onClick=\{onBack\}[\s\S]*?<ArrowLeft aria-hidden="true" size=\{20\} \/>/g) ?? [];

  assert.equal(backControls.length, 2, 'one not-found escape and one normal-page control are defined');
  assert.match(
    appSource,
    /\{!isDesktopApp \? \(\s*<button\s*className="icon-button box-back-action"[\s\S]*?onClick=\{onBack\}/,
  );
  for (const control of backControls) {
    assert.match(control, /aria-label=\{t\('backToPilotage'\)\}/);
    assert.match(control, /title=\{t\('backToPilotage'\)\}/);
  }
  assert.doesNotMatch(appSource, /history\.back\(|window\.history\.back\(/);
  assert.match(frSource, /backToPilotage: 'Retour au suivi'/);
  assert.match(enSource, /backToPilotage: 'Back to tracking'/);
});

test('measurement summary and editor are mutually exclusive states of one anchored module', () => {
  const appSource = readSource('../src/App.tsx');
  const css = readSource('../src/styles/pages/box-detail.css');

  assert.match(
    appSource,
    /const isMeasurementEditorExpanded = Boolean\(weeklyMeasurement\)\s*&& isMeasurementEditorOpen\s*&& Boolean\(editingMeasurement\?\.can_edit\);/s,
  );
  assert.match(appSource, /weeklyMeasurement \? ' has-measurement-module' : ''/);
  assert.match(appSource, /showWeeklyMeasurementSummary \? ' measurement-summary measurement-module' : ''/);
  assert.match(appSource, /isMeasurementEditorExpanded \? ' measurement-module is-expanded' : ''/);
  assert.doesNotMatch(appSource, /renderCompactMeasurementSummary|keepWeeklySummaryWhileEditing/);
  assert.doesNotMatch(appSource, /className="measurement-summary-editing"|t\('measurementEditing'\)/);
  assert.match(appSource, /<div className="last-reading-comment-header">/);
  assert.doesNotMatch(appSource, /measurement-summary-action/);
  assert.match(
    css,
    /grid-template-columns: minmax\(150px, \.75fr\) repeat\(2, minmax\(110px, \.45fr\)\) minmax\(220px, 1\.8fr\);/,
  );
  assert.match(css, /\.measurement-summary-edit-button \{[^}]*position: absolute;[^}]*width: 44px;[^}]*height: 44px;/s);
  assert.match(
    css,
    /\.box-page-grid\.has-measurement-module \{\s*grid-template: "last" auto "insights" auto \/ minmax\(0, 1fr\);\s*\}/s,
  );
  assert.match(css, /\.measurement-module\.is-expanded \.fake-form \{\s*animation: measurement-editor-open 240ms ease-out;/s);
});

test('summary transition is restrained and reduced motion remains global', () => {
  const css = readSource('../src/styles/pages/box-detail.css');
  const baseCss = readSource('../src/styles/base.css');

  assert.match(css, /\.last-reading-card\.is-fresh \{ animation: reading-saved 280ms ease-out; \}/);
  assert.match(css, /from \{ opacity: 0; transform: translateY\(6px\); \}/);
  assert.match(baseCss, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(baseCss, /animation-duration: 0\.01ms !important;/);
});

test('weekly conflicts refresh server state before the error is shown', () => {
  const appSource = readSource('../src/App.tsx');
  const createStart = appSource.indexOf('async function createMeasurement(');
  const createSource = appSource.slice(
    createStart,
    appSource.indexOf('\n  async function createBox(', createStart),
  );

  assert.match(createSource, /isMeasurementWeekConflict\(requestError\)/);
  assert.match(createSource, /await refreshBoxAfterMeasurement\(boxId\);/);
});

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

// Only the button actually pressed may look pressed. The guarantee is structural:
// the pressed state is owned by each button instance, so there is no shared state
// keyed by the operation that could mark a sibling — or the same symbol in another
// field — as active.
test('a pressed stepper is the only control marked active', () => {
  const appSource = readSource('../src/App.tsx');
  const css = readSource('../src/styles/pages/box-detail.css');

  const buttonStart = appSource.indexOf('function StepperButton({');
  const buttonSource = appSource.slice(
    buttonStart,
    appSource.indexOf('\nfunction BoxChecksModal({', buttonStart),
  );
  assert.notEqual(buttonStart, -1);

  // The pressed state lives on the button instance and drives its own class.
  assert.match(buttonSource, /const \[isPressed, setIsPressed\] = useState\(false\);/);
  assert.match(
    buttonSource,
    /className=\{isPressed \? 'count-stepper-button is-pressed' : 'count-stepper-button'\}/,
  );

  // Release clears that same state, for that same button only.
  assert.match(buttonSource, /setIsPressed\(false\);/);
  for (const handler of ['onPointerUp', 'onPointerLeave', 'onPointerCancel', 'onBlur']) {
    const bound = buttonSource.match(new RegExp(`${handler}=\\{clearRepeat\\}`, 'g')) ?? [];
    assert.equal(bound.length, 1, `${handler} must clear the pressed state`);
  }

  // No pressed state shared between controls or keyed by the operation alone.
  assert.doesNotMatch(appSource, /pressedStepper|pressedDirection|pressedOperation|activeStepper/);

  // The pressed look is scoped to the single button carrying the class...
  assert.match(
    css,
    /\.count-stepper button\.is-pressed \{[^}]*background: var\(--color-primary-soft\);[^}]*border-color: var\(--color-primary\);/s,
  );
  // ...and hover only applies where a pointer really hovers, so a sticky touch
  // hover cannot leave a previously tapped stepper looking active.
  assert.match(css, /@media \(hover: hover\) \{\s*:where\(\.count-stepper button\):hover/s);
  // Nothing keys the visual state on a shared, non-unique attribute.
  assert.doesNotMatch(css, /\[aria-label/);
  assert.doesNotMatch(css, /\.count-stepper button:active/);
});
