import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { ast, boxAst, boxFunctionNames, functionNode } from './app-operation-test-harness.mjs';

function nodes(root, predicate) {
  const found = [];
  function visit(node) {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}
function imports(file, module, name, defaultImport = false) {
  const matches = file.statements.filter(node => ts.isImportDeclaration(node)
    && node.moduleSpecifier.text === module);
  assert.equal(matches.length, 1, `${file.fileName} must import ${module} once`);
  const clause = matches[0].importClause;
  assert.ok(clause && !clause.isTypeOnly);
  if (defaultImport) assert.equal(clause.name?.text, name);
  else assert.ok(clause.namedBindings?.elements.some(binding => binding.name.text === name
    && !binding.propertyName && !binding.isTypeOnly), `Missing runtime import: ${name}`);
}
function evaluate(code, context = {}) {
  return vm.runInNewContext(ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  }).outputText, context);
}

test('BoxPage and every moved helper/handler have one real owner, never an App fallback', () => {
  imports(ast, './components/BoxPage', 'BoxPage', true);
  for (const name of boxFunctionNames) {
    assert.equal(nodes(ast, node => ts.isFunctionDeclaration(node) && node.name?.text === name).length, 0, name);
    assert.equal(nodes(boxAst, node => ts.isFunctionDeclaration(node) && node.name?.text === name).length, 1, name);
    assert.equal(functionNode(name).getSourceFile(), boxAst, name);
  }
  assert.ok(functionNode('BoxPage').modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.DefaultKeyword));
  for (const name of ['mergeBoxDetail', 'upsertThermalZones', 'createMeasurement', 'updateMeasurement',
    'createSubculture', 'moveBox', 'correctMovement', 'refreshMovementState',
    'refreshBoxAfterMeasurement', 'loadLineageGraph', 'closeBoxPage']) {
    assert.equal(functionNode(name).getSourceFile(), ast, name);
    assert.equal(nodes(ast, node => ts.isFunctionDeclaration(node) && node.name?.text === name).length, 1, name);
    assert.equal(nodes(boxAst, node => ts.isFunctionDeclaration(node) && node.name?.text === name).length, 0, name);
  }
  assert.throws(() => functionNode('missingRequiredInstallation'), /Missing App.tsx function/);
});

test('BoxPage alone imports and renders the original movement banner with exact callback identities', () => {
  imports(boxAst, './LatestMovementBanner', 'LatestMovementBanner', true);
  assert.equal(nodes(ast, node => ts.isImportDeclaration(node)
    && node.moduleSpecifier.text.endsWith('/LatestMovementBanner')).length, 0);
  const banners = file => nodes(file, node => (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node))
    && node.tagName.getText() === 'LatestMovementBanner');
  assert.equal(banners(ast).length, 0);
  const calls = banners(boxAst);
  assert.equal(calls.length, 1);
  const expressions = Object.fromEntries(calls[0].attributes.properties.map(attribute => {
    assert.ok(ts.isJsxAttribute(attribute) && ts.isJsxExpression(attribute.initializer));
    return [attribute.name.getText(), attribute.initializer.expression.getText()];
  }));
  assert.deepEqual(expressions, {
    box: 'box', zones: 'zones', language: 'language', canEdit: 'canWriteLabData', isDesktop: 'isDesktopApp',
    isOperationCurrent: '() => operationLifetimeRef.current && isOperationCurrent()', onCorrect: 'onCorrectMovement',
    correctionRequest: 'movementCorrectionRequest', onCorrectionRequestHandled: '() => setMovementCorrectionRequest(null)',
    onRefresh: 'onRefreshMovementState', t: 't',
  });
  const context = {
    React: { createElement: (type, props) => ({ type, props }) }, LatestMovementBanner: 'LatestMovementBanner',
    box: { id: 7, movements: [{ id: 9, revision: 0 }] }, zones: [{ id: 12 }], language: 'fr',
    canWriteLabData: true, isDesktopApp: true, operationLifetimeRef: { current: true }, isOperationCurrent: () => true,
    onCorrectMovement: () => 'correct', onRefreshMovementState: () => 'refresh', t: () => 'translated',
    movementCorrectionRequest: null, setMovementCorrectionRequest: () => {},
  };
  const render = () => evaluate(`(() => (${calls[0].getText()}))()`, context);
  assert.equal(render().type, context.LatestMovementBanner);
  for (const [prop, expression] of Object.entries(expressions)) {
    if (!['isOperationCurrent', 'onCorrectionRequestHandled'].includes(prop)) assert.equal(render().props[prop], context[expression], prop);
  }
  assert.equal(render().props.isOperationCurrent(), true);
  context.operationLifetimeRef.current = false;
  assert.equal(render().props.isOperationCurrent(), false);
  context.operationLifetimeRef.current = true;
  context.isOperationCurrent = () => false;
  assert.equal(render().props.isOperationCurrent(), false);
  context.canWriteLabData = false;
  assert.equal(render().props.canEdit, false);
  context.isDesktopApp = false;
  assert.equal(render().props.isDesktop, false);
});

test('shared write permission and location errors are imported from neutral real owners', () => {
  for (const [file, prefix] of [[ast, './'], [boxAst, '../']]) {
    imports(file, `${prefix}utils/profilePermissions`, 'userCanWriteLabData');
    imports(file, `${prefix}utils/boxErrors`, 'isBoxLocationChangedError');
    for (const name of ['userCanWriteLabData', 'isBoxLocationChangedError']) {
      assert.equal(nodes(file, node => ts.isFunctionDeclaration(node) && node.name?.text === name).length, 0);
    }
  }
  assert.equal(functionNode('userCanWriteLabData').getSourceFile().fileName, 'profilePermissions.ts');
  assert.equal(functionNode('isBoxLocationChangedError').getSourceFile().fileName, 'boxErrors.ts');
  const constants = file => nodes(file, node => ts.isVariableDeclaration(node) && node.name.getText() === 'SALINITY_STEP');
  assert.equal(constants(ast).length, 0);
  assert.equal(constants(boxAst).length, 1);
  assert.equal(constants(boxAst)[0].initializer.getText(), '1');
});

// Exact expressions from the App invocation, including movement callbacks. This keeps
// App's route, lifetime, callbacks and loading contract separate from Box UI tests.
const wiring = {
  key: '`${activeOrganizationId}:${selectedBoxId}`',
  box: 'selectedBoxDetail ?? selectedBox', zones: 'data.zones', profile: 'data.profile',
  language: 'language', qrLabelSelection: 'qrLabelSelection',
  isLoading: 'isLoading || isBoxLoading || isBoxCodePending || isBoxDetailPending',
  onCreateMeasurement: 'createMeasurement',
  isOperationCurrent: '() => operationGeneration === organizationRequestGenerationRef.current',
  onUpdateMeasurement: 'updateMeasurement', onRefreshMeasurementState: 'refreshBoxAfterMeasurement',
  onCreateSubculture: 'createSubculture', onMoveBox: 'moveBox',
  onCorrectMovement: 'correctMovement', onRefreshMovementState: 'refreshMovementState',
  onDeactivateBox: 'deactivateBox', onReactivateBox: 'reactivateBox', onLoadLineageGraph: 'loadLineageGraph',
  measurementPrefill: 'measurementPrefill', onMeasurementPrefillConsumed: '() => setMeasurementPrefill(null)',
  onOpenBox: 'openBox', onOpenZone: 'openZone', onAddQrLabel: 'addQrLabelToSelection',
  onBack: 'closeBoxPage', onOpenQrLabelSelection: 'openQrLabelSelection', confirmAction: 'confirmAction', t: 't',
};
const calls = nodes(ast, node => ts.isJsxSelfClosingElement(node) && node.tagName.getText() === 'BoxPage');

test('App keeps the exact BoxPage invocation, route gate and component prop contract', () => {
  assert.equal(calls.length, 1);
  const expressions = Object.fromEntries(calls[0].attributes.properties.map(attribute => {
    assert.ok(ts.isJsxAttribute(attribute) && ts.isJsxExpression(attribute.initializer));
    return [attribute.name.getText(), attribute.initializer.expression.getText()];
  }));
  assert.deepEqual(expressions, wiring);
  let gate = calls[0].parent;
  while (gate && !ts.isJsxExpression(gate)) gate = gate.parent;
  assert.ok(gate);
  assert.match(gate.expression.getText(), /^activeTab === 'pilotage' && isBoxRoute && \(/);
  const parameter = functionNode('BoxPage').parameters[0];
  assert.ok(ts.isObjectBindingPattern(parameter.name));
  assert.deepEqual(parameter.name.elements.map(element => element.name.getText()).sort(),
    Object.keys(wiring).filter(name => name !== 'key').sort());
});

test('real App JSX forwards fixture identities, loading flags and current-operation callback', () => {
  assert.equal(calls.length, 1);
  const consumed = [];
  const context = {
    React: { createElement: (type, props) => ({ type, props }) }, BoxPage: 'BoxPage',
    activeOrganizationId: 3, selectedBoxId: 7,
    selectedBoxDetail: { id: 7, detail: true }, selectedBox: { id: 7 },
    data: { zones: [{ id: 12 }], profile: { id: 5 } }, language: 'fr', qrLabelSelection: [{ boxId: 7 }],
    isLoading: false, isBoxLoading: false, isBoxCodePending: false, isBoxDetailPending: false,
    operationGeneration: 4, organizationRequestGenerationRef: { current: 4 },
    measurementPrefill: { box_id: 7, id: 9 }, setMeasurementPrefill: value => consumed.push(value),
  };
  const callbacks = Object.fromEntries(['createMeasurement', 'updateMeasurement', 'refreshBoxAfterMeasurement',
    'createSubculture', 'moveBox', 'correctMovement', 'refreshMovementState',
    'deactivateBox', 'reactivateBox', 'loadLineageGraph', 'openBox',
    'openZone', 'addQrLabelToSelection', 'closeBoxPage', 'openQrLabelSelection', 'confirmAction', 't']
    .map(name => [name, () => name]));
  Object.assign(context, callbacks);
  const render = () => evaluate(`(() => (${calls[0].getText()}))()`, context).props;
  const props = render();
  assert.equal(props.key, '3:7');
  assert.equal(props.box, context.selectedBoxDetail);
  for (const [prop, value] of Object.entries({ zones: context.data.zones, profile: context.data.profile,
    language: context.language, qrLabelSelection: context.qrLabelSelection, measurementPrefill: context.measurementPrefill })) {
    assert.equal(props[prop], value, prop);
  }
  for (const [prop, expression] of Object.entries(wiring)) {
    if (Object.hasOwn(callbacks, expression)) assert.equal(props[prop], callbacks[expression], prop);
  }
  assert.equal(props.isOperationCurrent(), true);
  context.organizationRequestGenerationRef.current++;
  assert.equal(props.isOperationCurrent(), false);
  props.onMeasurementPrefillConsumed();
  assert.deepEqual(consumed, [null]);
  context.selectedBoxDetail = null;
  assert.equal(render().box, context.selectedBox);
  assert.equal(render().isLoading, false);
  for (const flag of ['isLoading', 'isBoxLoading', 'isBoxCodePending', 'isBoxDetailPending']) {
    context[flag] = true;
    assert.equal(render().isLoading, true, flag);
    context[flag] = false;
  }
});

test('moved permission helpers preserve organization matching and independent archive capabilities', () => {
  const context = {};
  evaluate(functionNode('userCanWriteLabData').getText().replace(/^export /, ''), context);
  evaluate(functionNode('userCanArchiveBox').getText(), context);
  for (const [profile, write, archive] of [
    [null, false, false], [{ is_superuser: true }, true, true],
    ...['admin', 'lab_technician', 'read_only'].map(role => [
      { is_superuser: false, memberships: [{ organization: { id: 7 }, role }] },
      ['admin', 'lab_technician'].includes(role), role === 'admin',
    ]),
  ]) {
    assert.equal(context.userCanWriteLabData(profile, 7), write);
    assert.equal(context.userCanArchiveBox(profile, 7), archive);
    if (profile && !profile.is_superuser) {
      assert.equal(context.userCanWriteLabData(profile, 8), false);
      assert.equal(context.userCanArchiveBox(profile, 8), false);
    }
  }
});

test('neutral location error helper retains instanceof, status and stable-code checks', () => {
  class ApiError extends Error {
    constructor(status, data) { super('Controlled error'); this.status = status; this.data = data; }
  }
  const context = { ApiError };
  evaluate(functionNode('isBoxLocationChangedError').getText().replace(/^export /, ''), context);
  assert.equal(context.isBoxLocationChangedError(new ApiError(409, { code: 'box_location_changed' })), true);
  for (const error of [null, new Error('unrelated'), { status: 409, data: { code: 'box_location_changed' } },
    new ApiError(403, { code: 'box_location_changed' }), new ApiError(409, null),
    new ApiError(409, 'box_location_changed'), new ApiError(409, {}), new ApiError(409, { code: 'different' })]) {
    assert.equal(context.isBoxLocationChangedError(error), false);
  }
});
