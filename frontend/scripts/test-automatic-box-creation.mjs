import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import ts from 'typescript';
import { appHarness } from './app-operation-test-harness.mjs';
import { createBoxHarness, createPilotageHarness, plain, text } from './create-box-harness.mjs';

const references = { strains: [{ id: 1, code: 'AUR', species: 101, species_scientific_name: 'Aurelia aurita' }] };
const save = h => h.panel.nodes.find(n => n.type === 'button' && n.props.type === 'submit');
const strain = h => h.panel.nodes.find(n => n.type === 'select' && n.props['aria-label']);
const field = (h, name) => h.panel.field(`create-box-${name}-field`);
async function ready(h = createBoxHarness()) {
  h.env.requests.at(-1).resolve(references);
  await h.panel.settle();
  if (!field(h, 'location').disabled) h.panel.change(field(h, 'location'), '10');
  return h;
}
async function send(h) {
  const result = h.panel.submit();
  h.confirmations.at(-1).resolve(true);
  await h.panel.settle();
  return { result };
}
const committed = { id: 42, global_code: 'AUR.1042' };

test('automatic form has ordered required fields, no predicted code or collection request', async () => {
  const h = await ready();
  assert.equal(h.boxCollectionRequests, 0);
  assert.equal(h.panel.byClass('create-box-code-field'), undefined);
  assert.equal(h.panel.byClass('create-box-number-field'), undefined);
  const controls = h.panel.nodes.filter(n => ['select', 'input', 'textarea'].includes(n.type));
  assert.deepEqual(controls.map(n => n.props.type ?? n.type), ['search', 'select', 'select', 'date', 'textarea']);
  assert.equal(strain(h).props.required, true);
  assert.equal(field(h, 'date').props.required, true);
  h.panel.change(field(h, 'date'), '');
  assert.equal(save(h).disabled, true);
  await h.panel.submit();
  assert.equal(h.confirmations.length, 0);
  h.unmount();
});

test('synchronous double submit before confirmation sends exactly one frozen UUID intent', async () => {
  const h = await ready();
  h.panel.change(field(h, 'date'), '2026-10-01');
  h.panel.change(h.panel.nodes.find(n => n.type === 'textarea'), ' note ');
  const first = h.panel.submit();
  await h.panel.submit();
  assert.equal(h.confirmations.length, 1);
  h.panel.flush();
  assert.equal(strain(h).disabled, true);
  h.confirmations[0].resolve(true);
  await h.panel.settle();
  assert.equal(h.mutations.length, 1);
  const payload = plain(h.mutations[0].payload);
  assert.match(payload.request_id, /^[0-9a-f-]{36}$/);
  assert.deepEqual(payload, { strain: 1, thermal_zone: 10, entered_on: '2026-10-01', volume_liters: null, notes: 'note', request_id: payload.request_id });
  await h.panel.submit();
  assert.equal(h.mutations.length, 1);
  h.mutations[0].resolve(committed);
  await first;
  h.unmount();
});

for (const kind of ['network', '500', '400', '403', '409', 'cancelled']) test(`${kind}: frozen intent and UUID survive retry, closing and remount`, async () => {
  const h = await ready();
  const { result } = await send(h);
  const payload = plain(h.mutations[0].payload);
  const failure = kind === 'network' ? new Error('Lost response') : kind === 'cancelled' ? new h.api.ApiResourceCancelledError()
    : new h.api.ApiError(Number(kind), 'Server refusal', kind === '409' ? { code: 'box_creation_request_conflict' } : null);
  h.mutations[0].reject(failure);
  await result;
  h.panel.flush();
  assert.equal(strain(h).disabled, true);
  assert.equal(field(h, 'date').disabled, true);
  assert.equal(h.panel.nodes.find(n => n.type === 'textarea').disabled, true);
  assert.ok(text(h.panel.value).includes('createBoxPendingIntent'));
  assert.equal(text(save(h)), 'createBoxRetry');
  if (kind === '409') assert.ok(text(h.panel.value).includes('createBoxRequestConflict'));
  if (kind === '403') assert.ok(text(h.panel.value).includes('createBoxForbidden'));
  h.panel.setProps({ isOpen: false });
  h.panel.setProps({ isOpen: true });
  const retry = h.panel.submit();
  await h.panel.settle();
  assert.deepEqual(plain(h.mutations[1].payload), payload);
  assert.equal(h.confirmations.length, 1);
  h.mutations[1].reject(new Error('Still uncertain'));
  await retry;
  h.unmount();
  const restored = createBoxHarness({}, { environment: h.env });
  assert.equal(strain(restored).disabled, true);
  const recovered = restored.panel.submit();
  await restored.panel.settle();
  assert.deepEqual(plain(restored.mutations[0].payload), payload);
  assert.equal(restored.confirmations.length, 0);
  restored.mutations[0].resolve(committed);
  await recovered;
  restored.panel.flush();
  assert.ok(text(restored.panel.value).includes(committed.global_code));
  assert.deepEqual(restored.navigations, []);
  restored.unmount();
});

for (const presentation of ['modal', 'inline']) test(`${presentation}: committed code retained until explicit open, with modal page focus`, async () => {
  const h = createBoxHarness({ presentation });
  if (presentation === 'inline') h.panel.click(h.panel.byClass('create-box-toggle'));
  await ready(h);
  const target = h.env.external('section', { 'data-box-page-focus-target': true, tabIndex: -1 });
  const { result } = await send(h);
  h.mutations[0].resolve(committed);
  await result;
  h.panel.flush();
  assert.ok(text(h.panel.value).includes('createBoxSuccess'));
  assert.ok(text(h.panel.value).includes(committed.global_code));
  assert.deepEqual(h.navigations, []);
  if (presentation === 'modal') {
    assert.equal(h.env.document.activeElement, h.panel.byClass('create-box-open-created'));
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
  } else {
    h.panel.click(h.panel.byClass('create-box-toggle'));
    h.panel.click(h.panel.byClass('create-box-toggle'));
  }
  assert.ok(text(h.panel.value).includes(committed.global_code));
  h.panel.click(h.panel.nodes.find(n => n.type === 'button' && text(n) === 'createBoxOpenCreated'));
  assert.deepEqual(h.navigations, [42]);
  h.unmount();
  assert.equal(h.env.frameCount, presentation === 'modal' ? 1 : 0);
  h.env.flushFrames();
  if (presentation === 'modal') assert.equal(h.env.document.activeElement, target);
});

test('ordinary draft survives closing and reopening without becoming a request', async () => {
  const h = await ready();
  h.panel.change(field(h, 'date'), '2026-09-09');
  h.panel.change(h.panel.nodes.find(n => n.type === 'textarea'), 'Draft');
  h.panel.setProps({ isOpen: false });
  h.panel.setProps({ isOpen: true });
  assert.equal(field(h, 'date').props.value, '2026-09-09');
  assert.equal(h.panel.nodes.find(n => n.type === 'textarea').props.value, 'Draft');
  assert.equal(h.mutations.length, 0);
  h.unmount();
});

test('switching institutions isolates pending intent and restores the original key on return', async () => {
  const h = await ready(createPilotageHarness());
  const { result } = await send(h);
  const intent = plain(h.mutations[0].payload);
  const original = h.panel;
  h.setParentProps({ activeOrganizationId: 2, profile: { ...h.props.profile, active_organization: { id: 2 }, memberships: [{ organization: { id: 2 }, role: 'admin' }] } });
  assert.equal(strain(h).disabled, false);
  h.mutations[0].resolve(committed);
  await result;
  assert.equal(original.updatesAfterUnmount, 0);
  assert.deepEqual(h.navigations, []);
  h.setParentProps({ activeOrganizationId: 1, profile: h.props.profile });
  assert.equal(strain(h).disabled, true);
  const retry = h.panel.submit();
  await h.panel.settle();
  assert.deepEqual(plain(h.mutations[1].payload), intent);
  h.mutations[1].resolve(committed);
  await retry;
  h.unmount();
});

test('session persistence failure prevents the request rather than risking a duplicate on remount', async () => {
  const h = await ready();
  h.env.window.sessionStorage.setItem = () => { throw new Error('Storage denied'); };
  const { result } = await send(h);
  await result;
  h.panel.flush();
  assert.equal(h.mutations.length, 0);
  assert.equal(strain(h).disabled, false);
  assert.ok(text(h.panel.value).includes('createBoxRequestError'));
  h.unmount();
});

const sourceAst = path => ts.createSourceFile(path, readFileSync(new URL(path, import.meta.url), 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
function nodes(root, predicate) {
  const found = [];
  function visit(node) { if (predicate(node)) found.push(node); ts.forEachChild(node, visit); }
  visit(root);
  return found;
}

test('AST: App creation posts only to automatic endpoint and retains guarded cache publication', () => {
  const ast = sourceAst('../src/App.tsx');
  const create = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text === 'createBox')[0];
  const posts = nodes(create, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'apiPost');
  assert.equal(posts.length, 1);
  assert.equal(posts[0].arguments[0].text, '/api/boxes/automatic/');
  assert.equal(posts[0].arguments[1].getText(ast), 'payload');
  const body = create.getText(ast);
  assert.match(body, /assertCurrent\(\);[\s\S]*commit\(detail.id\)/);
  assert.match(body, /mergeBoxDetail\(current, detail\)/);
  assert.match(body, /upsertBoxes\(current.boxes, \[detail\]\)/);
  assert.match(body, /await publication.reconcile\(\);/);
});

test('AST: automatic payload type excludes all client code fields and requires UUID/date', () => {
  const ast = sourceAst('../src/types.ts');
  const type = ast.statements.find(n => ts.isTypeAliasDeclaration(n) && n.name.text === 'BoxCreatePayload');
  const fields = new Map(type.type.members.map(n => [n.name.getText(ast), n]));
  assert.deepEqual([...fields.keys()].sort(), ['entered_on', 'notes', 'request_id', 'strain', 'thermal_zone', 'volume_liters']);
  for (const name of ['entered_on', 'request_id', 'strain', 'thermal_zone']) assert.equal(fields.get(name).questionToken, undefined);
  for (const name of ['notes', 'volume_liters']) assert.ok(fields.get(name).questionToken);
});

test('AST: no prediction fetch; synchronous lock and persisted UUID precede the only mutation', () => {
  const ast = sourceAst('../src/components/CreateBoxPanel.tsx');
  const source = ast.getFullText();
  assert.doesNotMatch(source, /buildNextBoxCode|boxCodeMatchesBoxNumber|onRequestBoxCollection|isBoxCollectionReady|globalCode|boxNumber/);
  const submit = nodes(ast, n => ts.isFunctionDeclaration(n) && n.name?.text === 'handleSubmit')[0];
  const body = submit.getText(ast);
  assert.ok(body.indexOf('submissionRef.current = true') < body.indexOf('await confirmAction'));
  assert.ok(body.indexOf('window.sessionStorage.setItem') < body.indexOf('await onCreateBox'));
  assert.match(body, /request_id: window.crypto.randomUUID\(\)/);
  assert.equal(nodes(submit, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'onCreateBox').length, 1);
  assert.equal(nodes(submit, n => ts.isCallExpression(n) && n.expression.getText(ast) === 'onSelectBox').length, 0);
  assert.match(body, /onCreateBox\(\{ \.\.\.intent \}\)/);
  assert.match(body, /setCreatedBox\(created\)/);
  assert.doesNotMatch(body, /removeItem|pendingIntentRef.current = null/);
  const key = nodes(ast, n => ts.isVariableDeclaration(n) && n.name.getText(ast) === 'storageKey')[0].initializer;
  assert.ok(ts.isTemplateExpression(key));
  assert.equal(key.head.text, 'polypbase.automaticBox.');
  assert.deepEqual(key.templateSpans.map(span => span.expression.getText(ast)), ['profile?.id', 'organizationId']);
});

const resolveButton = h => h.panel.byClass('create-box-resolve');
async function failedCreation(h = createBoxHarness()) {
  await ready(h);
  h.panel.change(h.panel.nodes.find(n => n.type === 'textarea'), 'Keep my draft');
  h.panel.change(field(h, 'date'), '2026-10-02');
  const { result } = await send(h);
  h.mutations[0].reject(new h.api.ApiError(400, 'Inactive zone'));
  await result;
  h.panel.flush();
  return h;
}
function resolve(h) {
  return resolveButton(h).props.onClick();
}

test('resolution created bypasses obsolete choices and uses retained success UI without navigating', async () => {
  const h = await failedCreation();
  const intent = plain(h.mutations[0].payload);
  h.panel.setProps({ exportOptions: { zones: [] } });
  const pending = resolve(h);
  h.panel.flush();
  assert.equal(h.resolutions[0].requestId, intent.request_id);
  assert.equal(resolveButton(h).disabled, true);
  assert.equal(text(resolveButton(h)), 'createBoxResolving');
  assert.equal(save(h).disabled, true);
  assert.equal(strain(h).disabled, true);
  h.resolutions[0].resolve({ status: 'created', box: committed });
  await pending;
  h.panel.flush();
  assert.ok(text(h.panel.value).includes(committed.global_code));
  assert.deepEqual(h.navigations, []);
  assert.equal(h.mutations.length, 1);
  assert.equal(h.env.document.activeElement, h.panel.byClass('create-box-open-created'));
  assert.equal(JSON.parse(h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1')).request_id, intent.request_id);
  h.unmount();
});

test('cancelled resolution unlocks and preserves draft, clears key, and next confirmed creation gets new UUID', async () => {
  const h = await failedCreation();
  const firstId = h.mutations[0].payload.request_id;
  const pending = resolve(h);
  h.resolutions[0].resolve({ status: 'cancelled' });
  await pending;
  h.panel.flush();
  assert.equal(h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1'), null);
  assert.equal(strain(h).disabled, false);
  assert.equal(strain(h).props.value, 1);
  assert.equal(field(h, 'location').props.value, 10);
  assert.equal(field(h, 'date').props.value, '2026-10-02');
  assert.equal(h.panel.nodes.find(n => n.type === 'textarea').props.value, 'Keep my draft');
  assert.ok(text(h.panel.value).includes('createBoxRequestCancelled'));
  assert.equal(resolveButton(h), undefined);
  assert.equal(h.panel.nodes.some(n => n.props.className === 'inline-error'), false);
  h.panel.change(field(h, 'date'), '2026-10-03');
  const next = h.panel.submit();
  assert.equal(h.confirmations.length, 2);
  h.confirmations[1].resolve(true);
  await h.panel.settle();
  assert.notEqual(h.mutations[1].payload.request_id, firstId);
  assert.equal(h.mutations[1].payload.entered_on, '2026-10-03');
  h.mutations[1].resolve(committed);
  await next;
  h.unmount();
});

test('restored pending request can resolve without eligible choices; cancelled invalid picks become empty', async () => {
  const h = await failedCreation();
  const id = h.mutations[0].payload.request_id;
  h.unmount();
  const restored = createBoxHarness({ exportOptions: { zones: [] } }, { environment: h.env });
  restored.env.requests.at(-1).resolve({ strains: [] });
  await restored.panel.settle();
  const pending = resolve(restored);
  assert.equal(restored.resolutions[0].requestId, id);
  restored.resolutions[0].resolve({ status: 'cancelled' });
  await pending;
  restored.panel.flush();
  assert.equal(strain(restored).props.value, '');
  assert.equal(field(restored, 'location').props.value, '');
  assert.equal(strain(restored).disabled, false);
  assert.equal(save(restored).disabled, true);
  assert.equal(restored.panel.nodes.find(n => n.type === 'textarea').props.value, 'Keep my draft');
  assert.ok(text(strain(restored)).includes('createBoxSelectStrain'));
  restored.unmount();
});

for (const kind of ['network', '403']) test(`failed ${kind} resolution retains frozen exact intent and supports retry`, async () => {
  const h = await failedCreation();
  const stored = h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1');
  const pending = resolve(h);
  h.resolutions[0].reject(kind === 'network' ? new Error('Lost reply') : new h.api.ApiError(403, 'Permission changed'));
  await pending;
  h.panel.flush();
  assert.equal(h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1'), stored);
  assert.equal(strain(h).disabled, true);
  assert.equal(resolveButton(h).disabled, false);
  assert.ok(text(h.panel.value).includes(kind === 'network' ? 'createBoxResolveError' : 'Permission changed'));
  const retry = resolve(h);
  assert.equal(h.resolutions[1].requestId, JSON.parse(stored).request_id);
  h.resolutions[1].resolve({ status: 'cancelled' });
  await retry;
  h.panel.flush();
  assert.equal(strain(h).disabled, false);
  h.unmount();
});

test('synchronous resolve/resolve/create and create/resolve races share one submission guard', async () => {
  const h = await failedCreation();
  const pending = resolve(h);
  await resolve(h);
  await h.panel.submit();
  assert.equal(h.resolutions.length, 1);
  assert.equal(h.mutations.length, 1);
  h.resolutions[0].reject(new Error('Uncertain'));
  await pending;
  h.panel.flush();
  const retryCreate = h.panel.submit();
  await resolve(h);
  assert.equal(h.resolutions.length, 1);
  assert.equal(h.mutations.length, 2);
  h.mutations[1].reject(new Error('Uncertain again'));
  await retryCreate;
  h.unmount();
});

for (const outcome of ['created', 'cancelled', 'failed']) test(`institution switch ignores stale ${outcome} resolution and retains original UUID`, async () => {
  const h = await failedCreation(createPilotageHarness());
  const stored = h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1');
  const old = h.panel;
  const pending = resolve(h);
  h.setParentProps({ activeOrganizationId: 2, profile: { ...h.props.profile, active_organization: { id: 2 }, memberships: [{ organization: { id: 2 }, role: 'admin' }] } });
  if (outcome === 'failed') h.resolutions[0].reject(new Error('Late failure'));
  else h.resolutions[0].resolve(outcome === 'created' ? { status: 'created', box: committed } : { status: 'cancelled' });
  await pending;
  assert.equal(old.updatesAfterUnmount, 0);
  assert.equal(strain(h).disabled, false);
  assert.deepEqual(h.navigations, []);
  assert.equal(h.env.window.sessionStorage.getItem('polypbase.automaticBox.7.1'), stored);
  h.setParentProps({ activeOrganizationId: 1, profile: h.props.profile });
  assert.equal(strain(h).disabled, true);
  const retry = resolve(h);
  assert.equal(h.resolutions[1].requestId, JSON.parse(stored).request_id);
  h.resolutions[1].resolve({ status: 'cancelled' });
  await retry;
  h.unmount();
});

for (const status of ['created', 'cancelled']) test(`App resolution ${status} uses guarded endpoint and normal cache publication`, async () => {
  const h = appHarness();
  const before = h.state.data;
  const pending = h.context.resolveBoxCreation('request-uuid');
  assert.equal(h.requests[0].url, '/api/boxes/automatic/resolve/');
  assert.deepEqual(plain(h.requests[0].payload), { request_id: 'request-uuid' });
  const response = status === 'created' ? { status, box: committed } : { status };
  h.requests[0].resolve(response);
  assert.deepEqual(plain(await pending), response);
  if (status === 'created') {
    assert.equal(h.state.data.boxDetails[42].global_code, committed.global_code);
    assert.equal(h.state.data.boxes[0].global_code, committed.global_code);
    assert.equal(h.state.data.overview, null);
    assert.equal(h.state.data.exportOptions, null);
  } else assert.equal(h.state.data, before);
});

test('App resolution rejects old institution result before publication', async () => {
  const h = appHarness();
  const before = h.state.data;
  const pending = h.context.resolveBoxCreation('request-uuid');
  const rejected = assert.rejects(pending);
  h.switchOrganization();
  h.requests[0].resolve({ status: 'created', box: committed });
  await rejected;
  assert.equal(h.state.data, before);
});

test('AST: resolution uses same guarded publication and clears intent only for confirmed cancellation', () => {
  const app = sourceAst('../src/App.tsx');
  const callback = nodes(app, n => ts.isFunctionDeclaration(n) && n.name?.text === 'resolveBoxCreation')[0].getText(app);
  assert.match(callback, /getOperationRequests\(operationGeneration\)/);
  assert.match(callback, /assertCurrent\(\);\s*if \(result.status === 'created'\)/);
  assert.match(callback, /beginBoxMutation\(true\)/);
  assert.match(callback, /mergeBoxDetail\(current, detail\)/);
  assert.match(callback, /await publication.reconcile\(\);\s*assertCurrent\(\)/);
  const panel = sourceAst('../src/components/CreateBoxPanel.tsx');
  const handler = nodes(panel, n => ts.isFunctionDeclaration(n) && n.name?.text === 'handleResolveRequest')[0].getText(panel);
  assert.ok(handler.indexOf('submissionRef.current = true') < handler.indexOf('await onResolveBoxCreation'));
  assert.match(handler, /onResolveBoxCreation\(intent.request_id\)/);
  assert.match(handler, /if \(!operationLifetimeRef.current \|\| !isOperationCurrent\(\)\) return;/);
  assert.match(handler, /result.status === 'cancelled'[\s\S]*removeItem\(storageKey\)[\s\S]*pendingIntentRef.current = null/);
  assert.doesNotMatch(handler, /randomUUID|onCreateBox\(/);
});

for (const language of ['fr', 'en']) test(`${language} resolution copy is translated through failure and cancellation`, async () => {
  const h = createBoxHarness();
  const t = h.env.load('../src/i18n/index.ts').createTranslator(language);
  h.panel.setProps({ t });
  await failedCreation(h);
  assert.equal(text(resolveButton(h)), language === 'fr' ? 'Résoudre la demande' : 'Resolve request');
  assert.ok(text(h.panel.value).includes(t('createBoxResolveHelp')));
  const first = resolve(h);
  h.resolutions[0].reject(new Error('Lost response'));
  await first;
  h.panel.flush();
  assert.ok(text(h.panel.value).includes(t('createBoxResolveError')));
  const second = resolve(h);
  h.resolutions[1].resolve({ status: 'cancelled' });
  await second;
  h.panel.flush();
  assert.ok(text(h.panel.value).includes(t('createBoxRequestCancelled')));
  h.unmount();
});
