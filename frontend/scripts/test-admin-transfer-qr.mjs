import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { resourceClient } from './resource-test-harness.mjs';
import { moduleLoader, hookRuntime, deferred, elements, one, tick } from './sharing-test-harness.mjs';

function compile(path, suffix = '') {
  return ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + suffix, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
}

function load(code, globals = {}) {
  const exports = {};
  vm.runInNewContext(code, { exports, ...globals });
  return exports;
}

// These private legacy components are retained in AdminView but not mounted by
// Administration. Exporting them here tests dormant behavior, not an active route.
const componentCode = compile('../src/components/AdminView.tsx', '\nexport { TransferCreateForm, LEGACY_TRANSFER_PREVIEW_LABELS };');
const qr = load(compile('../src/utils/qrLabels.ts'), { require: () => resourceClient(), URL, window: { location: { origin: 'https://polypbase.test' } } });
const locales = {
  fr: load(compile('../src/i18n/fr.ts')).fr,
  en: load(compile('../src/i18n/en.ts')).en,
};
const failureKeys = {
  'popup-blocked': 'qrLabelPopupBlocked',
  'qr-retrieval': 'qrLabelQrUnavailable',
  'fallback-resource': 'qrLabelResourceUnavailable',
  'image-preparation': 'qrLabelImagePreparationFailed',
  preparation: 'qrLabelPreparationFailed',
};
const productionExchanges = moduleLoader()('src/components/sharing/InstitutionalExchangesPage.tsx');

function harness(language = 'en') {
  const slots = [], effects = [], calls = [], transfers = [];
  let index = 0, tree;
  const hooks = {
    useState(initial) {
      const i = index++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) { const i = index++; return slots[i] ??= { current: initial }; },
    useMemo(fn) { return fn(); },
    useEffect(fn, deps) {
      const i = index++;
      if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) { slots[i] = deps; effects.push(fn); }
    },
  };
  const jsx = (type, props) => ({ type, props });
  const unusedModules = [
    'lucide-react', '../api/client', '../utils/accountMembers', '../utils/accountInvitations', '../utils/dateFormat',
    '../utils/memberMutationLock', '../utils/memberFeedback', '../utils/stepValue', '../utils/zoneOccupancy',
    './AccountInvitationsPanel', './AdminActionPanel', './AdminAuditSection', './BoxInventoryAdminSection', './ConfirmActionModal',
    './LabConfigurationAdminSection',
    './PageLoader', './PolypbaseIcon', './RowActionMenu', './SkeletonRows', './SpeciesStrainsAdminSection', './TaxonomyAdminSection',
  ];
  const modules = Object.fromEntries(unusedModules.map((name) => [name, {}]));
  Object.assign(modules, {
    react: hooks,
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../utils/userIdentity': load(compile('../src/utils/userIdentity.ts')),
    '../utils/errors': { getErrorMessage: (error) => error.message },
    '../utils/qrLabels': {
      ...qr,
      printQrLabels(items) {
        let resolve;
        const promise = new Promise((settle) => { resolve = settle; });
        calls.push({ items, resolve });
        return promise;
      },
    },
  });
  // Resolve the new entrypoint as real code, not an empty replacement module.
  // Active Administration integration is exercised separately below.
  modules['./sharing/InstitutionalExchangesPage'] = productionExchanges;
  const adminModule = load(componentCode, {
    URL,
    window: { location: { origin: 'https://polypbase.test' } },
    require(name) { assert.ok(Object.hasOwn(modules, name), `unexpected dependency: ${name}`); return modules[name]; },
  });
  const component = adminModule.TransferCreateForm;
  const box = {
    id: 17, global_code: 'ATL-AAU-1.001', local_code: 'AAU-1', status: 'active',
    species: { scientific_name: 'Aurelia aurita', common_name: 'Moon jelly' },
    strain: { code: 'AAU-1' }, thermal_zone: { id: 3, name: 'Zone 15' },
    organization: { id: 1, name: 'Source' },
  };
  const props = {
    profile: { is_superuser: true, interface_language: language }, boxes: [box],
    organizations: [box.organization, { id: 2, name: 'Target' }],
    t(key) { assert.ok(Object.hasOwn(locales[language], key), `missing ${language} key: ${key}`); return locales[language][key]; },
    async onCreateTransfer(payload) {
      transfers.push(payload);
      if (h.backendError) throw new Error(h.backendError);
      return { id: 42, transfer_date: '2026-10-02', polyp_count: payload.polyp_count, notes: payload.notes };
    },
  };
  function nodes(node = tree) {
    if (!node || typeof node !== 'object') return [];
    return [node, ...[node.props?.children].flat(Infinity).filter(Boolean).flatMap((child) => nodes(child))];
  }
  function text(node = tree) {
    if (node == null || typeof node === 'boolean') return '';
    if (Array.isArray(node)) return node.map(text).join('');
    return typeof node === 'object' ? text(node.props?.children ?? null) : String(node);
  }
  function render() { index = 0; tree = component(props); while (effects.length) effects.shift()(); return tree; }
  function button(key) {
    const found = nodes().find((node) => node.type === 'button' && text(node) === props.t(key));
    assert.ok(found, `missing button ${key}`);
    return found;
  }
  function fill() {
    nodes().find((node) => node.type === 'select').props.onChange({ target: { value: '2' } });
    nodes().find((node) => node.type === 'input' && node.props.type === 'number').props.onChange({ target: { value: '5' } });
    render();
  }
  async function submit() { await tree.props.onSubmit({ preventDefault() {} }); render(); }
  async function prepare() { render(); fill(); await submit(); assert.ok(nodes().some((node) => node.props?.className === 'transfer-package')); }
  const h = { render, nodes, text, button, fill, submit, prepare, calls, transfers, box, props,
    legacyPreviewLabels: adminModule.LEGACY_TRANSFER_PREVIEW_LABELS, backendError: null };
  return h;
}

// Each explicitly mounted component has independent hook state. This follows the
// real AdminView -> page -> sections -> detail/preview element props; it is not a
// DOM renderer. Only unrelated admin sections and leaf UI are replaced.
function administrationHarness(language = 'en', extraProps = {}) {
  const requests = [], mounted = [];
  const storage = new Map([['polypbase.activeOrganizationId', '1']]);
  let current;
  const hooks = Object.fromEntries(Object.keys(hookRuntime().hooks).map((key) => [key, (...args) => current.hooks[key](...args)]));
  const overrides = { react: { ...React, ...hooks, useId: () => 'admin-donation-dialog' } };
  for (const name of ['AccountInvitationsPanel', 'AdminActionPanel', 'AdminAuditSection', 'BoxInventoryAdminSection',
    'ConfirmActionModal', 'LabConfigurationAdminSection', 'PageLoader', 'SpeciesStrainsAdminSection', 'TaxonomyAdminSection',
    'BoxTrackingPreview', 'ModalPortal', 'PolypbaseIcon', 'SkeletonRows']) {
    overrides[`src/components/${name}.tsx`] = { default: () => null };
  }
  const load = moduleLoader(overrides, {
    window: { location: { origin: 'https://polypbase.test' }, localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key),
    }, addEventListener() {}, removeEventListener() {} },
    document: { cookie: '', body: {}, documentElement: { lang: language }, querySelectorAll: () => [] },
    HTMLElement: class {},
    crypto: globalThis.crypto,
    fetch(path, init) {
      // Let replies arrive even after abort to test stale-response guards.
      const pending = deferred();
      requests.push({ path, init, ...pending });
      return pending.promise;
    },
  });
  function mount(type, props) {
    const runtime = hookRuntime();
    let tree;
    let unmounted = false;
    const instance = {
      render(next = {}) {
        props = { ...props, ...next };
        current = runtime;
        try { tree = runtime.render(() => type(props)); runtime.commit(); } finally { current = null; }
        return tree;
      },
      get tree() { return tree; },
      unmount() {
        if (unmounted) return;
        unmounted = true;
        runtime.unmount();
      },
    };
    mounted.push(instance);
    instance.render();
    return instance;
  }
  const t = (key) => { assert.ok(Object.hasOwn(locales[language], key), `missing ${language} key: ${key}`); return locales[language][key]; };
  const AdminView = load('src/components/AdminView.tsx').default;
  load('src/utils/qrLabels.ts').printQrLabels = () => assert.fail('active donations must not prepare legacy QR labels');
  const root = mount(AdminView, {
    activeOrganizationId: 1, activeSection: 'transfers', speciesTarget: null,
    profile: { id: 9, is_superuser: true, active_organization: { id: 1, name: 'Source' }, organizations: [] },
    language, t, boxes: [], zones: [], isLoading: false, isOptionsLoading: false,
    onOpenBox() {}, onSelectSection() {},
    onRequestOptions() { assert.fail('donations do not need export options'); },
    onCreateTransfer() { assert.fail('the dormant biological transfer must not be called'); },
    ...extraProps,
  });
  const components = load('src/components/sharing/InstitutionalExchangesPage.tsx');
  const pageNode = () => one(root.tree, (node) => node.type === components.default);
  function openSections() {
    const node = pageNode();
    const page = mount(node.type, node.props);
    return elements(page.tree, (child) => child.type === components.DonationSection).map((child) => mount(child.type, child.props));
  }
  return { root, pageNode, openSections, mount, load, components, requests, storage, t,
    reply(index, data, status = 200) {
      requests[index].resolve(new Response(JSON.stringify(data), { status, headers: { 'Content-Type': 'application/json' } }));
    },
    cleanup() { for (const instance of mounted.toReversed()) instance.unmount(); },
  };
}

const donationFixture = {
  proposal_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', state: 'pending', revision: 1,
  created_at: '2026-11-10T10:00:00Z', cancelled_at: null,
  source_institution: { name: 'Source' }, destination_institution: { name: 'Destination' },
  box: { id: 17, global_code: 'ATL-AAU-1.001', species_scientific_name: 'Aurelia aurita', strain_code: 'AAU-1',
    ownership_sequence: 1, global_strain_id: 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee', global_box_id: 'cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee' },
  created_by: { first_name: 'Test', last_name: 'Researcher', email: 'researcher@example.test' }, cancelled_by: null,
  history_verified_at: '2026-11-09T10:00:00Z',
  history_verification: { verified_by: { first_name: 'Test', last_name: 'Researcher', email: 'researcher@example.test' }, verified_at: '2026-11-09T10:00:00Z', snapshot_version: '1', sha256: 'test-only', polyp_state_revision: 0 },
};

for (const language of ['fr', 'en']) {
  test(`${language}: Administration mounts real donation lists and reads detail instead of the legacy QR transfer`, async (context) => {
    const h = administrationHarness(language);
    context.after(h.cleanup);
    assert.equal(h.pageNode().key, '9:1', 'account and active institution define the page lifetime');
    assert.equal(h.pageNode().props.organizationId, 1);
    const sections = h.openSections();
    assert.equal(sections.length, 2);
    assert.deepEqual(h.requests.map((request) => request.path), ['/api/box-donations/outgoing/', '/api/box-donations/incoming/']);
    for (const request of h.requests) {
      assert.equal(request.init.method ?? 'GET', 'GET');
      assert.equal(request.init.headers.get('X-Organization-Id'), '1');
      assert.equal(request.init.cache, 'no-store');
      assert.equal(request.init.credentials, 'include');
      assert.equal(request.init.signal.aborted, false);
    }
    for (const section of sections) assert.ok(elements(section.tree, (node) => node.props?.role === 'status').length);
    const incoming = { ...donationFixture, box: { ...donationFixture.box } };
    delete incoming.box.id;
    h.reply(0, { results: [donationFixture] });
    h.reply(1, { results: [incoming] });
    await tick();
    for (const section of sections) {
      section.render();
      const row = one(section.tree, (node) => node.type === 'article');
      assert.ok(row.props['aria-label'].includes(donationFixture.box.global_code));
      assert.ok(row.props['aria-label'].includes(h.t('donationPending')));
      assert.equal(elements(section.tree, (node) => node.props?.className === 'transfer-package').length, 0);
    }
    const { RowActionMenu } = h.load('src/components/RowActionMenu.tsx');
    one(sections[0].tree, (node) => node.type === RowActionMenu).props.onAction('details');
    sections[0].render();
    const detailNode = one(sections[0].tree, (node) => node.type === h.components.DonationDetailDialog);
    const detail = h.mount(detailNode.type, detailNode.props);
    assert.equal(h.requests[2].path, `/api/box-donations/${donationFixture.proposal_id}/`);
    h.reply(2, { ...donationFixture, box: { ...donationFixture.box, global_code: 'DETAIL-NOT-SUMMARY' }, history_verification: { ...donationFixture.history_verification, current: true } });
    await tick(); detail.render();
    assert.ok(elements(detail.tree, (node) => node.type === 'strong' && node.props.children === 'DETAIL-NOT-SUMMARY').length);
    assert.equal(h.requests.length, 3, 'no legacy transfer, personal sharing or box collection requests');
  });
}

const previewType = (h) => h.load('src/components/sharing/ExchangeFormPreview.tsx').default;
const select = (tree, field) => one(tree, (node) => node.type === 'select' && node.props.id?.endsWith(`-${field}`));
const change = (node, value) => node.props.onChange({ target: { value } });
const submitButton = (tree) => one(tree, (node) => node.type === 'button' && node.props.type === 'submit');
const confirmations = (tree) => elements(tree, (node) => node.type === 'input' && node.props.type === 'checkbox');
const sentStatus = (h, section) => elements(section.tree, (node) => node.props.role === 'status' && node.props.children === h.t('donationProposalSent'));
const outgoingPath = '/api/box-donations/outgoing/';
const preflightPath = '/api/box-donations/preflight/';
const choicePaths = (org) => ['/api/box-donations/destinations/', `/api/boxes/?organization=${org}&status=active&q=&limit=20&offset=0`];
const historyHash = '0123456789abcdef'.repeat(4);

function openSend(h, outgoing, org = 1) {
  const first = h.requests.length;
  one(outgoing.tree, (node) => node.type === 'button' && [node.props.children].flat().includes(h.t('donationSend'))).props.onClick();
  const node = one(outgoing.render(), (child) => child.type === previewType(h));
  assert.equal(node.props.organizationId, org);
  const form = h.mount(node.type, node.props);
  assertRequests(h, first, org, choicePaths(org).map((path) => ['GET', path]));
  return form;
}

function assertRequests(h, first, org, expected) {
  assert.deepEqual(h.requests.slice(first).map(({ path, init }) => [init.method ?? 'GET', path, init.headers.get('X-Organization-Id')]),
    expected.map(([method, path]) => [method, path, String(org)]));
}

function replyWithChoices(h, first, org, boxId, destinationId) {
  h.reply(first, { results: [{ institution_id: destinationId, name: `Destination ${org}`, country: 'France' }] });
  h.reply(first + 1, { count: 1, next: null, previous: null, results: [
    { id: boxId, global_code: `BOX-${boxId}`, status: 'active', organization: { id: org } },
  ] });
}

async function submit(tree) {
  let prevented = false;
  one(tree, (node) => node.type === 'form').props.onSubmit({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  await tick();
}

test('Administration Send requires explicit choices, preflight and history confirmation before creating a donation', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const [outgoing] = h.openSections();
  h.reply(0, { results: [] }); h.reply(1, { results: [] });
  await tick(); outgoing.render();

  const form = openSend(h, outgoing);
  assert.equal(submitButton(form.tree).props.disabled, true);
  const boxId = donationFixture.box.id;
  const destinationId = 'destination-1';
  replyWithChoices(h, 2, 1, boxId, destinationId);
  await tick();
  let tree = form.render();
  const type = select(tree, 'type');
  assert.equal(type.props.defaultValue, 'donation');
  assert.deepEqual(elements(type, (node) => node.type === 'option').map((node) => [node.props.value, Boolean(node.props.disabled)]),
    [['donation', false], ['live', true], ['frozen', true], ['email_frozen', true]]);
  assert.equal(select(tree, 'box').props.value, '');
  assert.equal(select(tree, 'institution').props.value, '');
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  change(select(tree, 'box'), String(boxId));
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  assert.equal(h.requests.length, 4, 'loading choices and submitting without both selections must not write');

  change(select(tree, 'institution'), destinationId);
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, false);
  assert.equal(confirmations(tree).length, 0);
  await submit(tree);
  assertRequests(h, 4, 1, [['POST', preflightPath]]);
  const selection = { source_box_id: boxId, destination_institution_id: destinationId };
  assert.deepEqual(JSON.parse(h.requests[4].init.body), selection);
  h.reply(4, { history: { sha256: historyHash } });
  await tick(); tree = form.render();
  const [confirmation] = confirmations(tree);
  assert.equal(confirmation.props.checked, false);
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  assert.equal(h.requests.length, 5, 'preflight alone cannot send');

  confirmation.props.onChange({ target: { checked: true } });
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, false);
  await submit(tree);
  assertRequests(h, 4, 1, [['POST', preflightPath], ['POST', outgoingPath]]);
  const body = JSON.parse(h.requests[5].init.body);
  assert.match(body.request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.deepEqual(body, { ...selection, request_id: body.request_id, history_verified: true, history_sha256: historyHash });
  h.reply(5, donationFixture, 201);
  await tick(); outgoing.render();
  assert.equal(elements(outgoing.tree, (node) => node.type === previewType(h)).length, 0);
  form.unmount();
  assert.equal(sentStatus(h, outgoing).length, 1);
  assertRequests(h, 4, 1, [['POST', preflightPath], ['POST', outgoingPath], ['GET', outgoingPath]]);
  h.reply(6, { results: [donationFixture] });
  await tick(); outgoing.render();
  assert.ok(one(outgoing.tree, (node) => node.type === 'article').props['aria-label'].includes(donationFixture.box.global_code));
});

test('Administration closes Send without refreshing and isolates late choices across institution switches', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const sections = h.openSections();
  const [outgoing] = sections;
  h.reply(0, { results: [donationFixture] }); h.reply(1, { results: [] });
  await tick(); outgoing.render();
  const closed = openSend(h, outgoing);
  one(closed.tree, (node) => node.type === 'button' && node.props['aria-label'] === h.t('close')).props.onClick();
  assert.equal(elements(outgoing.render(), (node) => node.type === previewType(h)).length, 0);
  closed.unmount();
  assert.ok(h.requests.slice(2, 4).every((request) => request.init.signal.aborted));
  assert.equal(elements(outgoing.tree, (node) => node.type === 'article').length, 1);
  assert.equal(h.requests.length, 4, 'closing does not write or refresh the list');

  const old = openSend(h, outgoing);
  h.load('src/api/client.ts').setActiveOrganizationContext(2);
  h.root.render({ activeOrganizationId: 2 });
  assert.equal(h.pageNode().key, '9:2');
  assert.ok(h.requests.slice(4, 6).every((request) => request.init.signal.aborted));
  // Resolve during the context change, before the old keyed page is torn down.
  replyWithChoices(h, 4, 1, 18, 'previous');
  await tick();
  const stale = old.render();
  assert.equal(select(stale, 'box').props.disabled, true);
  assert.equal(select(stale, 'institution').props.disabled, true);
  old.unmount();
  for (const section of sections) section.unmount();

  const [next] = h.openSections();
  assertRequests(h, 6, 2, [['GET', outgoingPath], ['GET', '/api/box-donations/incoming/']]);
  h.reply(6, { results: [] }); h.reply(7, { results: [] });
  await tick(); next.render();
  assert.equal(elements(next.tree, (node) => node.type === previewType(h)).length, 0);
  const current = openSend(h, next, 2);
  replyWithChoices(h, 8, 2, 27, 'current');
  await tick();
  replyWithChoices(h, 2, 1, 17, 'closed');
  await tick();
  const tree = current.render();
  for (const [field, value] of [['box', 27], ['institution', 'current']]) {
    const input = select(tree, field);
    assert.equal(input.props.value, '');
    assert.deepEqual(elements(input, (node) => node.type === 'option').map((node) => node.props.value), ['', value]);
  }
  assert.equal(submitButton(tree).props.disabled, true);
  assert.equal(sentStatus(h, next).length, 0);
  assert.equal(h.requests.length, 10, 'late choices must not trigger writes or refreshes');
});

test('Administration donation read errors retry only the failed direction with real API reads', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const [outgoing, incoming] = h.openSections();
  h.reply(0, { detail: 'Forbidden' }, 403);
  h.reply(1, { results: [] });
  await tick(); outgoing.render(); incoming.render();
  const errorNode = one(outgoing.tree, (node) => typeof node.type === 'function' && node.props.error);
  const error = h.mount(errorNode.type, errorNode.props);
  assert.equal(one(error.tree, (node) => node.type === 'p').props.children, h.t('donationForbidden'));
  assert.ok(elements(incoming.tree, (node) => node.type === 'strong' && node.props.children === h.t('donationEmpty')).length);
  one(error.tree, (node) => node.type === 'button').props.onClick();
  outgoing.render();
  assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].path, '/api/box-donations/outgoing/');
  assert.equal(h.requests[2].init.headers.get('X-Organization-Id'), '1');
  assert.equal(h.requests[2].init.cache, 'no-store');
  h.reply(2, { results: [donationFixture] });
  await tick(); outgoing.render();
  assert.equal(elements(outgoing.tree, (node) => node.type === 'article').length, 1);
  assert.equal(elements(outgoing.tree, (node) => node.props?.error).length, 0);
});

test('Administration keys the donation page by account and institution and unmount aborts its reads', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const sections = h.openSections();
  const oldKey = h.pageNode().key;
  h.root.render({ profile: { id: 10, is_superuser: true, organizations: [] } });
  assert.equal(h.pageNode().key, '10:1');
  assert.notEqual(h.pageNode().key, oldKey);
  for (const section of sections) section.unmount();
  assert.ok(h.requests.every((request) => request.init.signal.aborted));
  h.storage.set('polypbase.activeOrganizationId', '2');
  h.root.render({ activeOrganizationId: 2 });
  assert.equal(h.pageNode().key, '10:2');
  assert.equal(h.pageNode().props.organizationId, 2);
  const next = h.openSections();
  assert.equal(h.requests.length, 4);
  for (const request of h.requests.slice(2)) assert.equal(request.init.headers.get('X-Organization-Id'), '2');
  // Transport deliberately permits late responses: the real hook must reject them.
  h.reply(0, { results: [donationFixture] }); h.reply(1, { results: [donationFixture] });
  h.reply(2, { results: [] }); h.reply(3, { results: [] });
  await tick();
  for (const section of next) {
    section.render();
    assert.equal(elements(section.tree, (node) => node.type === 'article').length, 0);
    assert.ok(elements(section.tree, (node) => node.type === 'strong' && node.props.children === h.t('donationEmpty')).length);
  }
});

test('Administration never mounts exchanges for a missing institution or non-admin profile', (context) => {
  const h = administrationHarness('en', { activeOrganizationId: null });
  context.after(h.cleanup);
  assert.equal(elements(h.root.tree, (node) => node.type === h.components.default).length, 0);
  h.root.render({ activeOrganizationId: 1, profile: {
    id: 9, is_superuser: false, organizations: [{ id: 1, name: 'Source' }],
    active_organization: { id: 1, name: 'Source' },
    memberships: [{ organization: { id: 1 }, role: 'technician' }],
  } });
  assert.equal(h.root.tree, null);
  assert.equal(h.requests.length, 0);
});

test('unmounted transfer preview preserves the two existing missing-key fallbacks explicitly', () => {
  const i18n = load(compile('../src/i18n/index.ts'), {
    require(name) {
      if (name === './fr') return { fr: locales.fr };
      if (name === './en') return { en: locales.en };
      if (name === './ja') return load(compile('../src/i18n/ja.ts'));
      throw new Error(`Unexpected dependency: ${name}`);
    },
  });
  const labels = harness().legacyPreviewLabels;
  assert.deepEqual(Object.keys(labels), ['species', 'cultureStatus']);
  for (const language of ['fr', 'en']) {
    const t = i18n.createTranslator(language);
    for (const [key, label] of Object.entries(labels)) {
      assert.equal(Object.hasOwn(locales[language], key), false);
      assert.equal(label, key);
      assert.equal(label, t(key));
    }
  }
});

for (const language of ['fr', 'en']) {
  for (const [reason, key] of Object.entries(failureKeys)) {
    test(`dormant legacy QR / ${language}: ${reason} is factual, retry keeps the prepared transfer and does not recreate it`, async () => {
      const h = harness(language);
      await h.prepare();
      const packageBefore = h.text(h.nodes().find((node) => node.props?.className === 'transfer-package').props.children[0]);
      h.button('adminTransferPrintLabel').props.onClick();
      assert.equal(h.calls.length, 1);
      assert.equal(h.calls[0].items.length, 1);
      assert.equal(JSON.stringify(h.calls[0].items[0]), JSON.stringify(qr.buildQrLabelItem(h.box)));
      h.calls[0].resolve({ status: 'failed', reason });
      await tick(); h.render();
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'alert')), locales[language][key]);
      assert.equal(h.nodes().filter((node) => node.props?.className === 'inline-error').length, 1);
      h.button('qrLabelRetry').props.onClick();
      h.render();
      assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
      assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), locales[language].qrLabelPreparing);
      assert.equal(JSON.stringify(h.calls[1].items), JSON.stringify(h.calls[0].items));
      h.calls[1].resolve({ status: 'prepared' });
      await tick(); h.render();
      assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), '');
      assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
      assert.equal(h.text(h.nodes().find((node) => node.props?.className === 'transfer-package').props.children[0]), packageBefore);
      assert.equal(h.transfers.length, 1);
    });
  }

  test(`dormant legacy QR / ${language}: cancellation is not an error and printing remains available`, async () => {
    const h = harness(language);
    await h.prepare();
    h.button('adminTransferPrintLabel').props.onClick();
    h.calls[0].resolve({ status: 'cancelled' });
    await tick(); h.render();
    assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
    assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
    assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), '');
    h.button('adminTransferPrintLabel').props.onClick();
    assert.equal(h.calls.length, 2);
    h.calls[1].resolve({ status: 'prepared' });
    await tick(); h.render();
    assert.equal(h.transfers.length, 1);
  });
}

test('dormant legacy QR: synchronous guard blocks duplicate clicks before rendering and while busy', async () => {
  const h = harness();
  await h.prepare();
  const click = h.button('adminTransferPrintLabel').props.onClick;
  click(); click();
  assert.equal(h.calls.length, 1);
  h.render();
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
  h.button('adminTransferPrintLabel').props.onClick(); click();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick(); h.render();
  const retry = h.button('qrLabelRetry').props.onClick;
  retry(); retry(); click();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'prepared' });
  await tick(); h.render();
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
});

test('dormant legacy QR: old pending failure cannot attach to a newly prepared transfer package', async () => {
  const h = harness();
  await h.prepare();
  h.button('adminTransferPrintLabel').props.onClick();
  h.render();
  h.fill();
  assert.equal(h.nodes().find((node) => node.props?.type === 'submit').props.disabled, false);
  await h.submit();
  assert.equal(h.transfers.length, 2);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.nodes().some((node) => node.type === 'button' && h.text(node) === locales.en.qrLabelRetry), false);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'failed', reason: 'qr-retrieval' });
  await tick(); h.render();
  assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'alert')), locales.en.qrLabelQrUnavailable);
  h.button('qrLabelRetry').props.onClick();
  h.calls[2].resolve({ status: 'prepared' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.transfers.length, 2);
});

test('dormant legacy QR: submission invalidates the old package before rendering or backend completion', async () => {
  const h = harness();
  await h.prepare();
  const oldClick = h.button('adminTransferPrintLabel').props.onClick;
  oldClick();
  h.fill();
  const createTransfer = h.props.onCreateTransfer;
  let resolveTransfer;
  h.props.onCreateTransfer = (payload) => new Promise((resolve) => {
    resolveTransfer = async () => resolve(await createTransfer(payload));
  });
  h.render();
  const submitting = h.submit();
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick();
  oldClick();
  assert.equal(h.calls.length, 1);
  h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  await resolveTransfer();
  await submitting;
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'prepared' });
  await tick(); h.render();
});

test('dormant legacy QR: backend transfer errors and QR preparation errors remain independent', async () => {
  const h = harness();
  await h.prepare();
  h.button('adminTransferPrintLabel').props.onClick();
  h.calls[0].resolve({ status: 'failed', reason: 'image-preparation' });
  await tick(); h.render();
  assert.equal(h.transfers.length, 1);
  assert.equal(h.nodes().filter((node) => node.props?.className === 'inline-error').length, 1);
  h.backendError = 'Transfer rejected by server';
  h.fill(); await h.submit();
  assert.equal(h.text(h.nodes().find((node) => node.props?.className === 'inline-error')), h.backendError);
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.calls.length, 1);
  h.backendError = null;
  h.fill(); await h.submit();
  assert.equal(h.nodes().some((node) => node.props?.className === 'inline-error'), false);
  h.button('adminTransferPrintLabel').props.onClick();
  h.calls[1].resolve({ status: 'cancelled' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.className === 'inline-error'), false);
  assert.equal(h.transfers.length, 3);
});
