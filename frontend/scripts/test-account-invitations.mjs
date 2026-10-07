import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');

function load(path, imports = {}) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected dependency: ${name}`);
      return imports[name];
    },
  });
  return exports;
}

const identity = load('utils/userIdentity.ts');
const adminSource = read('components/AdminView.tsx');
const adminAst = ts.createSourceFile('AdminView.tsx', adminSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const displayNameFunction = adminAst.statements.find((node) =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'getMemberDisplayName');
assert.ok(displayNameFunction, 'Missing getMemberDisplayName in AdminView');
const displayNameExports = {};
vm.runInNewContext(ts.transpileModule(
  `${displayNameFunction.getText(adminAst)}\nexport { getMemberDisplayName };`,
  { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } },
).outputText, { exports: displayNameExports, ...identity });
const { getMemberDisplayName } = displayNameExports;

const invitationsUtil = load('utils/accountInvitations.ts');
const accountMembers = load('utils/accountMembers.ts');
const memberFeedback = load('utils/memberFeedback.ts');
const { AccountInvitationsTable } = load('components/AccountInvitationsPanel.tsx', {
  react: { useEffect() {}, useRef: (initial) => ({ current: initial }), useState: (initial) => [initial, () => {}] },
  'react/jsx-runtime': jsxRuntime,
  '../utils/accountInvitations': invitationsUtil,
});
const catalogues = Object.fromEntries(['fr', 'en'].map((language) => [language, load(`i18n/${language}.ts`)[language]]));

const SERVER_NOW = Date.parse('2026-10-05T10:00:00Z');
const pending = (overrides = {}) => ({
  id: 1, full_name: 'Ada LOVELACE', first_name: 'Ada', last_name: 'LOVELACE', email: 'ada@example.org', role: 'viewer', role_label: 'Lecteur',
  status: 'pending', expires_at: '2026-10-06T09:59:00Z', can_resend: false, ...overrides,
});
const legacy = (overrides = {}) => pending({
  id: 2, full_name: '', first_name: '', last_name: '', email: 'old@example.org', status: 'expired', expires_at: null, can_resend: true, ...overrides,
});

const nodes = (tree) => Array.isArray(tree) ? tree.flatMap(nodes)
  : React.isValidElement(tree) ? [tree, ...nodes(tree.props.children)] : [];
const text = (tree) => Array.isArray(tree) ? tree.map(text).join('')
  : React.isValidElement(tree) ? text(tree.props.children) : String(tree ?? '');
const hasClass = (node, name) => node.props.className?.split(/\s+/).includes(name) ?? false;

function render(invitations, { language = 'fr', busyIds = new Set(), onResend = () => {}, serverNowMs = SERVER_NOW, feedback = null } = {}) {
  const t = (key) => {
    assert.equal(typeof catalogues[language][key], 'string', `Missing ${language}.${key}`);
    return catalogues[language][key];
  };
  return AccountInvitationsTable({
    invitations, serverNowMs, busyIds, feedback, onResend, t,
    getDisplayName: getMemberDisplayName,
  });
}
const rows = (tree) => nodes(tree).filter((node) => hasClass(node, 'invitation-row'));
const resendButtons = (tree) => nodes(tree).filter((node) => hasClass(node, 'invitation-resend'));

for (const language of ['fr', 'en']) {
  test(`${language}: invitation rows use the real structured identity helper and email fallback`, () => {
    const cases = [
      [legacy({ first_name: '  ÉLISE-ANNE ', last_name: ' du  Pont-Müller ', full_name: 'Wrong Name' }), 'Élise-Anne DU PONT-MÜLLER'],
      [legacy({ first_name: 'JEAN LUC', last_name: '', full_name: 'Wrong Name' }), 'Jean Luc'],
      [legacy({ first_name: '', last_name: 'du pont', full_name: 'Wrong Name' }), 'DU PONT'],
      [legacy(), 'old@example.org'],
    ];
    for (const [invitation, expected] of cases) {
      const tree = render([invitation], { language });
      const name = nodes(rows(tree)[0]).find((node) => node.type === 'strong');
      assert.equal(text(name), expected);
      assert.equal(getMemberDisplayName(invitation), identity.formatReadableUserIdentity(invitation));
      assert.ok(resendButtons(tree)[0].props['aria-label'].endsWith(` ${expected}`));
      assert.doesNotMatch(text(tree), /Wrong Name/);
    }
    assert.match(adminSource, /getDisplayName=\{getMemberDisplayName\}/);
    assert.doesNotMatch(adminSource, /\.full_name|displayName\.split/);
  });
}

test('server clock offset corrects a skewed local clock', () => {
  const localReceivedAt = SERVER_NOW - 3 * 60 * 1000;
  const offset = invitationsUtil.getServerClockOffset('2026-10-05T10:00:00Z', localReceivedAt);
  assert.equal(offset, 3 * 60 * 1000);
  const expiresAt = '2026-10-05T10:05:00Z';
  // Two local minutes later the server is at 10:02, so one minute remains.
  const view = invitationsUtil.getInvitationView(
    { status: 'pending', expires_at: expiresAt },
    localReceivedAt + 2 * 60 * 1000 + offset,
  );
  assert.equal(view.status, 'pending');
  assert.equal(view.remainingMs, 3 * 60 * 1000);
  assert.equal(invitationsUtil.getServerClockOffset('not a date', 5), 0);
});

test('an invitation is pending up to and including its expiry, then expired', () => {
  const expiry = Date.parse('2026-10-05T12:00:00Z');
  const item = { status: 'pending', expires_at: '2026-10-05T12:00:00Z' };
  assert.equal(invitationsUtil.getInvitationView(item, expiry).status, 'pending');
  assert.equal(invitationsUtil.getInvitationView(item, expiry).remainingMs, 0);
  assert.equal(invitationsUtil.getInvitationView(item, expiry + 1).status, 'expired');
  assert.equal(invitationsUtil.needsExpiryRevalidation(item, expiry), false);
  assert.equal(invitationsUtil.needsExpiryRevalidation(item, expiry + 1), true);
});

test('a backend expired or legacy invitation never becomes pending and needs no revalidation', () => {
  const legacyItem = { status: 'expired', expires_at: null };
  assert.deepEqual({ ...invitationsUtil.getInvitationView(legacyItem, 0) }, { status: 'expired', remainingMs: null });
  assert.equal(invitationsUtil.needsExpiryRevalidation(legacyItem, Date.now()), false);
  const expiredWithDate = { status: 'expired', expires_at: '2099-01-01T00:00:00Z' };
  assert.equal(invitationsUtil.getInvitationView(expiredWithDate, 0).status, 'expired');
});

test('countdown formatting is stable and language neutral', () => {
  const format = invitationsUtil.formatInvitationCountdown;
  assert.equal(format(23 * 3600_000 + 59 * 60_000), '23 h 59 min');
  assert.equal(format(12 * 60_000 + 5_000), '12 min 05 s');
  assert.equal(format(45_000), '45 s');
  assert.equal(format(0), '0 s');
  assert.equal(format(-5), '0 s');
});

test('rendering shows the countdown for a pending row and no resend when the backend forbids it', () => {
  const tree = render([pending()]);
  const [row] = rows(tree);
  assert.ok(hasClass(row, 'is-pending'));
  assert.ok(!hasClass(row, 'is-expired'));
  assert.match(text(tree), /Invitation en cours/);
  assert.match(text(tree), /Expire dans 23 h 59 min/);
  assert.equal(resendButtons(tree).length, 0);
});

test('an expired legacy row is expired, has no fabricated date and stays actionable', () => {
  const calls = [];
  const tree = render([legacy()], { onResend: (item) => calls.push(item.id) });
  const [row] = rows(tree);
  assert.ok(hasClass(row, 'is-expired'));
  assert.match(text(tree), /Expirée/);
  assert.doesNotMatch(text(tree), /Invitation ancienne|Expire dans/);
  assert.equal(nodes(tree).filter((node) => node.type === 'time').length, 0);
  const [button] = resendButtons(tree);
  assert.equal(text(button), 'Renvoyer');
  assert.equal(button.props.type, 'button');
  assert.equal(button.props.disabled, undefined);
  assert.equal(button.props['aria-disabled'], false);
  assert.match(button.props['aria-label'], /^Renvoyer l’invitation à old@example\.org$/);
  button.props.onClick();
  assert.deepEqual(calls, [2]);
});

test('a pending row turns expired visually when the server clock passes its expiry', () => {
  const afterExpiry = Date.parse('2026-10-06T09:59:01Z');
  const [row] = rows(render([pending({ can_resend: true })], { serverNowMs: afterExpiry }));
  assert.ok(hasClass(row, 'is-expired'));
});

test('resend is shown only when the backend allows it', () => {
  const tree = render([pending({ id: 1, can_resend: false }), legacy({ id: 2, can_resend: true }), legacy({ id: 3, can_resend: false })]);
  assert.equal(resendButtons(tree).length, 1);
});

test('a busy resend blocks a second submit and announces busy state', () => {
  const calls = [];
  const tree = render([legacy()], { busyIds: new Set([2]), onResend: () => calls.push('resend') });
  const [button] = resendButtons(tree);
  assert.equal(button.props['aria-disabled'], true);
  assert.equal(button.props['aria-busy'], true);
  assert.equal(text(button), 'Renvoi...');
  button.props.onClick();
  assert.deepEqual(calls, []);
});

test('feedback tone highlights only the matching row', () => {
  const tree = render([legacy({ id: 2 }), legacy({ id: 3 })], { feedback: { id: 3, tone: 'positive' } });
  const [first, second] = rows(tree);
  assert.ok(!hasClass(first, 'is-updated-positive'));
  assert.ok(hasClass(second, 'is-updated-positive'));
});

test('English rendering uses the English catalogue', () => {
  const tree = render([legacy()], { language: 'en' });
  assert.match(text(tree), /Expired/);
  assert.equal(text(resendButtons(tree)[0]), 'Resend');
  assert.match(resendButtons(tree)[0].props['aria-label'], /^Resend the invitation to /);
});

test('visible counters are derived from the members the API returns', () => {
  // The backend already removed pending invitations and hidden technical
  // memberships from `members`, so the counters cannot include them.
  const members = [{ role: 'admin' }, { role: 'viewer' }, { role: 'lab_technician' }];
  assert.deepEqual({ ...accountMembers.getMemberRoleCounts(members) }, {
    all: 3, admin: 1, lab_technician: 1, viewer: 1,
  });
  assert.equal(accountMembers.getMemberRoleCounts([]).all, 0);
});

test('resend feedback is positive and translated', () => {
  assert.deepEqual({ ...memberFeedback.MEMBER_MUTATION_FEEDBACK.resend_invitation }, {
    key: 'manageInvitationResent', tone: 'positive',
  });
});

const NEW_KEYS = [
  'manageInvitationsTitle', 'manageInvitationPending',
  'manageInvitationExpired', 'manageInvitationExpiresIn', 'manageColValidity', 'manageInvitationResend',
  'manageInvitationResending', 'manageInvitationResendFor', 'manageInvitationResent', 'manageNoInvitations',
  'manageErrorInvitationNotPending', 'manageErrorInvitationStillValid', 'manageInvitationsRetry',
  'auditDescriptionInvitationResent', 'auditDescriptionTeamVisibilityUpdated',
];

test('new invitation texts exist in French and English without a middle dot', () => {
  for (const key of NEW_KEYS) {
    for (const language of ['fr', 'en']) {
      const value = catalogues[language][key];
      assert.equal(typeof value, 'string', `${language}.${key}`);
      assert.ok(value.length > 0);
      assert.ok(!value.includes('·') && !value.includes('•'), `${language}.${key} uses a middle dot`);
    }
  }
  assert.equal(catalogues.fr.manageInvitationsTitle, 'Invitations');
  assert.equal(catalogues.en.manageInvitationsTitle, 'Invitations');
  assert.equal(catalogues.fr.manageInvitationExpired, 'Expirée');
});

// Evaluate the actual section JSX without mounting hooks or making requests.
const teamSection = adminAst.statements.find((node) =>
  ts.isFunctionDeclaration(node) && node.name?.text === 'AccountManagementSection');
const teamReturn = teamSection.body.statements.find((node) => ts.isReturnStatement(node));
const teamRenderSource = ts.transpileModule(`export function renderTeam() { ${teamReturn.getText(adminAst)} }`, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
}).outputText;

function renderTeam(overrides = {}) {
  const members = [{ membership_id: 7, first_name: 'Grace', last_name: 'HOPPER', email: 'grace@example.org', role: 'viewer', role_label: 'Lecteur', is_active: true, last_login: null }];
  const noop = () => {};
  const exports = {};
  const context = {
    exports, require: () => jsxRuntime,
    t: (key) => catalogues.fr[key],
    isMemberFormOpen: false, feedback: null, loadError: null, invitationsError: null,
    data: { members }, filteredMembers: members, memberCounts: accountMembers.getMemberRoleCounts(members), roleFilter: 'all',
    setRoleFilter: noop, toggleRoleFilter: noop,
    getMemberDisplayName, getMemberRowClassName: memberFeedback.getMemberRowClassName,
    getMemberRowActions: accountMembers.getMemberRowActions,
    getAccountMemberRoleLabel: accountMembers.getAccountMemberRoleLabel, memberActionLabel: noop,
    busyMemberIds: new Set(), RowActionMenu: noop, PolypbaseIcon: noop, SkeletonRows: noop,
    AccountInvitationsPanel: noop, handleInvitationResend: noop, loadInvitations: noop,
    invitationSnapshot: { invitations: [pending(), legacy()], serverTime: '2026-10-05T10:00:00Z', receivedAtMs: SERVER_NOW },
    confirmActionModal: null,
    ...overrides,
  };
  vm.runInNewContext(teamRenderSource, context);
  return { tree: exports.renderTeam(), context };
}

test('members and invitations render together, with the invitation subsection immediately after the member table', () => {
  const { tree, context } = renderTeam();
  const children = tree.props.children.flat().filter(React.isValidElement);
  const memberTable = children.findIndex((node) => hasClass(node, 'member-table-shell'));
  const subsection = children[memberTable + 1];
  assert.ok(memberTable >= 0);
  assert.match(text(children[memberTable]), /Grace HOPPER/);
  assert.equal(subsection.type, 'section');
  assert.ok(hasClass(subsection, 'account-invitations'));
  const heading = nodes(subsection).find((node) => node.type === 'h3');
  assert.equal(text(heading), 'Invitations');
  assert.equal(subsection.props['aria-labelledby'], heading.props.id);
  const panel = nodes(subsection).find((node) => node.props.snapshot);
  assert.equal(panel.props.snapshot, context.invitationSnapshot);
  assert.equal(panel.props.onResend, context.handleInvitationResend);
  assert.equal(panel.props.onRevalidate, context.loadInvitations);
  assert.equal(panel.props.busyIds, context.busyMemberIds);
  assert.ok(nodes(tree).every((node) => !['tablist', 'tab', 'tabpanel'].includes(node.props.role)));
  assert.doesNotMatch(teamSection.getText(adminAst), /activeTab|setActiveTab|handleTabKeyDown|account-tab/);
  const css = read('styles/pages/administration.css');
  const headingStyle = css.match(/\.account-invitations h3 \{[^}]*\}/)[0];
  assert.match(headingStyle, /font-style: italic/);
  assert.match(headingStyle, /font-weight: 700/);
  assert.doesNotMatch(read('components/AccountInvitationsPanel.tsx'), />\s*(Renvoyer|Expirée|Resend|Expired)\s*</);
});

test('invitation loading, empty, error and retry states remain below the members', () => {
  for (const overrides of [
    { invitationSnapshot: null },
    { invitationSnapshot: { invitations: [] } },
    { invitationSnapshot: null, invitationsError: 'Invitation load failed' },
    { invitationsError: 'Invitation refresh failed' },
    { filteredMembers: [], data: { members: [] } },
  ]) {
    const { tree, context } = renderTeam(overrides);
    const subsection = nodes(tree).find((node) => hasClass(node, 'account-invitations'));
    assert.ok(subsection);
    if (context.filteredMembers.length) assert.match(text(tree), /Grace HOPPER/);
    if (context.invitationsError) {
      assert.match(text(subsection), /Invitation (load|refresh) failed/);
      const retry = nodes(subsection).find((node) => node.type === 'button');
      assert.equal(text(retry), 'Réessayer');
      retry.props.onClick();
    }
    const panel = nodes(subsection).find((node) => node.props.snapshot);
    assert.equal(Boolean(panel), Boolean(context.invitationSnapshot?.invitations.length));
    const skeleton = nodes(subsection).find((node) => node.props.count === 3);
    assert.equal(Boolean(skeleton), !context.invitationSnapshot && !context.invitationsError);
    if (context.invitationSnapshot?.invitations.length === 0) {
      assert.match(text(subsection), /Aucune invitation/);
    }
  }
});

test('the expired row reuses the inventory inactive treatment and keeps the action actionable', () => {
  const css = read('styles/pages/administration.css');
  assert.match(css, /\.box-inventory-row\.is-dead \{ background: var\(--color-surface-subtle\); \}/);
  const expired = css.match(/\.invitation-table tbody tr\.invitation-row\.is-expired,[^{]*\{[^}]*\}/)[0];
  assert.match(expired, /background: var\(--color-surface-subtle\)/);
  assert.match(css, /\.invitation-table tbody tr\.invitation-row\.is-expired td:first-child \{\s*box-shadow: inset 2px 0 0 var\(--color-muted\)/);
  const attenuated = css.match(/\.invitation-row\.is-expired \.member-identity strong,[^{]*\{[^}]*\}/)[0];
  assert.doesNotMatch(attenuated, /opacity/);
  assert.doesNotMatch(css, /\.is-expired[^{]*\.invitation-resend/);
  assert.match(css, /\.invitation-resend:focus-visible \{[^}]*outline: 2px solid var\(--color-primary\)/);
});

// ---- F6 / R1 / R2: list coherence ----

function deferred() {
  let resolve; let reject;
  const promise = new Promise((res, rej) => { resolve = res; reject = rej; });
  return { promise, resolve, reject };
}

function loadWithGlobals(path, globals) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports, ...globals });
  return exports;
}

const PATH = '/api/accounts/invitations/';
const row = (id, status, overrides = {}) => pending({
  id, status, email: `user${id}@example.org`,
  expires_at: status === 'pending' ? '2026-10-07T10:00:00Z' : null,
  can_resend: status === 'expired', ...overrides,
});
const listBody = (invitations, serverTime = '2026-10-06T10:00:00Z') => ({ invitations, server_time: serverTime });

// The real api client (with its GET deduplication) behind a controllable fetch.
function realStack({ invalidate = true } = {}) {
  const fetches = [];
  let organizationId = '1';
  let mounted = true;
  const client = loadWithGlobals('api/client.ts', {
    Headers, URL,
    window: {
      localStorage: { getItem: () => organizationId, setItem() {}, removeItem() {} },
      location: { origin: 'http://polypbase.test' },
    },
    document: { cookie: '' },
    fetch: (path, init) => {
      const d = deferred();
      fetches.push({ path, organization: new Headers(init.headers).get('X-Organization-Id'), d });
      return d.promise;
    },
  });
  const changes = [];
  const errors = [];
  const store = invitationsUtil.createInvitationsStore({
    request: () => client.apiGet(PATH),
    invalidateRequest: invalidate ? () => client.invalidateInFlightGet(PATH) : () => {},
    isActive: () => mounted,
    onChange: (snapshot, options) => changes.push({ snapshot, options }),
    onError: (error) => errors.push(error),
    now: () => 42,
  });
  const respond = (index, body, status = 200) => fetches[index].d.resolve(new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  }));
  return {
    client, store, fetches, changes, errors, respond,
    setOrganization: (id) => { organizationId = id; },
    unmount: () => { mounted = false; },
    statuses: () => [...(store.getSnapshot()?.invitations ?? [])].map((item) => `${item.id}:${item.status}`),
  };
}

async function settle() {
  for (let index = 0; index < 5; index += 1) await new Promise((resolve) => setImmediate(resolve));
}

test('a normal invitation load applies its response', async () => {
  const h = realStack();
  const load = h.store.load();
  h.respond(0, listBody([row(1, 'expired')]));
  assert.equal(await load, true);
  assert.deepEqual(h.statuses(), ['1:expired']);
  assert.equal(h.changes[0].snapshot.receivedAtMs, 42);
  assert.equal(h.changes[0].options.keepError, false);
});

test('R1: a load after a resend never joins the GET that captured the pre-resend state', async () => {
  const h = realStack();
  const initial = h.store.load();
  h.respond(0, listBody([row(1, 'expired')]));
  await initial;

  const staleLoad = h.store.load(); // e.g. expiry revalidation or retry, in flight
  assert.equal(h.fetches.length, 2);
  // The resend succeeds and its server row is applied.
  assert.equal(h.store.applyMutation(row(1, 'pending'), '2026-10-06T10:00:05Z'), true);
  // A new logical load starts before the old GET resolves.
  const freshLoad = h.store.load();
  assert.equal(h.fetches.length, 3, 'the post-mutation load must start its own request');

  h.respond(1, listBody([row(1, 'expired')]));
  h.respond(2, listBody([row(1, 'pending')], '2026-10-06T10:00:06Z'));
  assert.equal(await staleLoad, false);
  assert.equal(await freshLoad, true);
  assert.deepEqual(h.statuses(), ['1:pending']);
});

test('R1: the stale payload loses even when the fresh GET finishes first', async () => {
  const h = realStack();
  const initial = h.store.load();
  h.respond(0, listBody([row(1, 'expired')]));
  await initial;
  const staleLoad = h.store.load();
  h.store.applyMutation(row(1, 'pending'), '2026-10-06T10:00:05Z');
  const freshLoad = h.store.load();
  h.respond(2, listBody([row(1, 'pending')]));
  await freshLoad;
  h.respond(1, listBody([row(1, 'expired')]));
  await staleLoad;
  await settle();
  assert.deepEqual(h.statuses(), ['1:pending']);
  assert.ok(h.changes.slice(1).every((change) => change.snapshot.invitations[0].status === 'pending'));
});

test('R1 control: without invalidation the real client reuses the old GET (the reviewed bug)', async () => {
  const h = realStack({ invalidate: false });
  const initial = h.store.load();
  h.respond(0, listBody([row(1, 'expired')]));
  await initial;
  h.store.load();
  h.store.applyMutation(row(1, 'pending'), '2026-10-06T10:00:05Z');
  const freshLoad = h.store.load();
  assert.equal(h.fetches.length, 2, 'deduplicated onto the pre-mutation request');
  h.respond(1, listBody([row(1, 'expired')]));
  await freshLoad;
  assert.deepEqual(h.statuses(), ['1:expired']);
});

test('invalidation keeps normal deduplication for requests started afterwards', async () => {
  const h = realStack();
  const old = h.client.apiGet(PATH);
  h.client.invalidateInFlightGet(PATH);
  const fresh = h.client.apiGet(PATH);
  assert.equal(h.fetches.length, 2);
  // The old request settling must not remove the fresh shared entry.
  h.respond(0, listBody([]));
  await old;
  const joined = h.client.apiGet(PATH);
  assert.equal(h.fetches.length, 2, 'joins the fresh in-flight request');
  h.respond(1, listBody([row(3, 'pending')]));
  assert.deepEqual(await fresh, await joined);
});

test('in-flight GETs stay scoped to the active organization', async () => {
  const h = realStack();
  const first = h.client.apiGet(PATH);
  h.setOrganization('2');
  const second = h.client.apiGet(PATH);
  assert.equal(h.fetches.length, 2);
  assert.deepEqual(h.fetches.map((item) => item.organization), ['1', '2']);
  h.respond(0, listBody([row(1, 'expired')]));
  h.respond(1, listBody([row(2, 'expired')]));
  assert.notDeepEqual(await first, await second);
});

test('R2: a created invitation stays visible when a resend discards its refresh', async () => {
  const h = realStack();
  const initial = h.store.load();
  h.respond(0, listBody([row(1, 'expired')]));
  await initial;

  // Creation of B succeeds: its server row is shown, then a refresh starts.
  assert.equal(h.store.applyMutation(row(2, 'pending')), true);
  const creationRefresh = h.store.load();
  // Resend of A succeeds before that refresh is accepted.
  h.store.applyMutation(row(1, 'pending'), '2026-10-06T10:00:05Z');
  h.respond(1, listBody([row(1, 'expired'), row(2, 'pending')]));
  assert.equal(await creationRefresh, false);

  assert.deepEqual(h.statuses(), ['1:pending', '2:pending']);
});

test('R2: a created invitation is kept when its refresh fails, and the error is reported', async () => {
  const h = realStack();
  const initial = h.store.load();
  h.respond(0, listBody([row(1, 'pending')]));
  await initial;
  h.store.applyMutation(row(2, 'pending'));
  const refresh = h.store.load();
  h.respond(1, { detail: 'down' }, 503);
  assert.equal(await refresh, false);
  assert.equal(h.errors.length, 1);
  assert.deepEqual(h.statuses(), ['1:pending', '2:pending']);
});

test('without a loaded list, a mutation defers to the reload instead of inventing a clock', async () => {
  const h = realStack();
  assert.equal(h.store.applyMutation(row(2, 'pending')), false);
  assert.equal(h.store.getSnapshot(), null);
  const load = h.store.load();
  h.respond(0, listBody([row(2, 'pending')]));
  assert.equal(await load, true);
  assert.deepEqual(h.statuses(), ['2:pending']);
});

test('a GET started before a mutation cannot overwrite it, nor show its error', async () => {
  const h = realStack();
  const staleLoad = h.store.load();
  h.store.applyMutation(row(1, 'pending'));
  h.respond(0, { detail: 'down' }, 503);
  assert.equal(await staleLoad, false);
  assert.equal(h.errors.length, 0);
  assert.equal(h.changes.length, 0);
});

test('only the latest of overlapping loads is applied, even if it finishes first', async () => {
  const h = realStack();
  const first = h.store.load();
  h.client.invalidateInFlightGet(PATH); // force two real requests for this check
  const second = h.store.load();
  h.respond(1, listBody([], 'new'));
  h.respond(0, listBody([], 'old'));
  assert.equal(await second, true);
  assert.equal(await first, false);
  assert.deepEqual(h.changes.map((change) => change.snapshot.serverTime), ['new']);
});

test('after an organization switch, old loads and mutations no longer apply', async () => {
  const h = realStack();
  const load = h.store.load();
  h.unmount(); // the section is keyed by organization and unmounts
  assert.equal(h.store.applyMutation(row(9, 'pending'), 'x'), false);
  h.respond(0, listBody([row(9, 'expired')]));
  assert.equal(await load, false);
  assert.equal(h.changes.length, 0);
  const source = read('components/AdminView.tsx');
  assert.match(source, /<AccountManagementSection\s+key=\{profile\.active_organization\?\.id \?\? 'no-organization'\}/);
});

test('create and resend handlers apply the server row through the store', () => {
  const source = read('components/AdminView.tsx');
  const create = source.slice(source.indexOf('async function handleAddMember'), source.indexOf('// Clears any pending row feedback'));
  assert.ok(create.indexOf('applyMutation(member.invitation)') > 0);
  assert.ok(create.indexOf('applyMutation(member.invitation)') < create.indexOf('await loadInvitations()'));
  const resend = source.slice(source.indexOf('async function handleInvitationResend'), source.indexOf('function memberActionLabel'));
  assert.match(resend, /applyMutation\(response\.invitation, response\.server_time\)/);
  assert.doesNotMatch(source, /setInvitationSnapshot\(\(current\)/);
  assert.match(source, /invalidateRequest: \(\) => invalidateInFlightGet\(INVITATIONS_PATH\)/);
});

// ---- F7: bounded revalidation ----

test('expiry revalidation retries after a failure and stops once refreshed', async () => {
  const attempts = { current: 0 };
  const waits = [];
  const answers = [false, true];
  const outcome = await invitationsUtil.runInvitationRevalidation({
    attempts,
    revalidate: async () => answers.shift(),
    wait: async (ms) => { waits.push(ms); },
    isCancelled: () => false,
  });
  assert.equal(outcome, 'refreshed');
  assert.equal(attempts.current, 2);
  assert.deepEqual(waits, [2000]);
});

test('expiry revalidation is bounded when the backend keeps failing', async () => {
  const attempts = { current: 0 };
  let calls = 0;
  const outcome = await invitationsUtil.runInvitationRevalidation({
    attempts,
    revalidate: async () => { calls += 1; return false; },
    wait: async () => {},
    isCancelled: () => false,
  });
  assert.equal(outcome, 'exhausted');
  assert.equal(calls, invitationsUtil.INVITATION_REVALIDATION_DELAYS_MS.length);
  // A later run shares the counter and does not start a new series.
  const again = await invitationsUtil.runInvitationRevalidation({
    attempts, revalidate: async () => { calls += 1; return true; }, wait: async () => {}, isCancelled: () => false,
  });
  assert.equal(again, 'exhausted');
  assert.equal(calls, 3);
});

test('a cancelled revalidation stops before asking the backend', async () => {
  const attempts = { current: 1 };
  let calls = 0;
  const outcome = await invitationsUtil.runInvitationRevalidation({
    attempts, revalidate: async () => { calls += 1; return true; }, wait: async () => {}, isCancelled: () => true,
  });
  assert.equal(outcome, 'cancelled');
  assert.equal(calls, 0);
});

test('after expiry, a failed refresh then recovery shows Renvoyer only from backend state', async () => {
  const expiresAt = '2026-10-05T10:00:00Z';
  const afterExpiry = Date.parse(expiresAt) + 1000;
  const backendPending = pending({ expires_at: expiresAt, can_resend: false });
  // Local expiry: the row looks expired but has no resend action yet.
  let tree = render([backendPending], { serverNowMs: afterExpiry });
  assert.ok(hasClass(rows(tree)[0], 'is-expired'));
  assert.equal(resendButtons(tree).length, 0);
  assert.equal(invitationsUtil.needsExpiryRevalidation(backendPending, afterExpiry), true);

  // First refresh fails, the second returns the backend verdict.
  const answers = [new Error('offline'), { ...backendPending, status: 'expired', can_resend: true }];
  let current = backendPending;
  const outcome = await invitationsUtil.runInvitationRevalidation({
    attempts: { current: 0 },
    revalidate: async () => {
      const answer = answers.shift();
      if (answer instanceof Error) return false;
      current = answer;
      return true;
    },
    wait: async () => {},
    isCancelled: () => false,
  });
  assert.equal(outcome, 'refreshed');
  tree = render([current], { serverNowMs: afterExpiry });
  assert.equal(resendButtons(tree).length, 1);
  assert.equal(invitationsUtil.needsExpiryRevalidation(current, afterExpiry), false);
});

test('the panel drives bounded revalidation and the section offers an explicit retry', () => {
  const panel = read('components/AccountInvitationsPanel.tsx');
  assert.match(panel, /runInvitationRevalidation\(\{/);
  assert.doesNotMatch(panel, /setInterval\([^)]*onRevalidate/);
  const source = read('components/AdminView.tsx');
  assert.match(source, /onClick=\{\(\) => loadInvitations\(\)\}/);
  assert.match(source, /t\('manageInvitationsRetry'\)/);
});

// ---- F8: accessible errors ----

test('invitation errors are announced as alerts, separate from success status', () => {
  const source = read('components/AdminView.tsx');
  assert.match(source, /className="inline-error invitation-error" role="alert"/);
  assert.match(source, /<p className="sr-only" role="status">\{feedback\?\.message \?\? ''\}<\/p>/);
  assert.equal(source.match(/invitation-error" role="alert"/g).length, 1);
  for (const language of ['fr', 'en']) {
    assert.equal(typeof catalogues[language].manageInvitationsRetry, 'string');
  }
});
