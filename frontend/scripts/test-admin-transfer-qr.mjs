import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import {
  moduleLoader,
  hookRuntime,
  deferred,
  elements,
  one,
  tick,
} from './sharing-test-harness.mjs';
import { loadI18n } from './i18n-runtime-test-helper.mjs';

const { translations: locales } = loadI18n();

// Each explicitly mounted component has independent hook state. This follows the
// real AdminView -> page -> sections -> detail/preview element props; it is not a
// DOM renderer. Only unrelated admin sections and leaf UI are replaced.
function administrationHarness(language = 'en', extraProps = {}) {
  const requests = [],
    mounted = [];
  const storage = new Map([['polypbase.activeOrganizationId', '1']]);
  let current;
  const hooks = Object.fromEntries(
    Object.keys(hookRuntime().hooks).map((key) => [
      key,
      (...args) => current.hooks[key](...args),
    ]),
  );
  const overrides = {
    react: { ...React, ...hooks, useId: () => 'admin-donation-dialog' },
  };
  for (const name of [
    'AccountInvitationsPanel',
    'AdminActionPanel',
    'AdminAuditSection',
    'BoxInventoryAdminSection',
    'ConfirmActionModal',
    'LabConfigurationAdminSection',
    'PageLoader',
    'SpeciesStrainsAdminSection',
    'BoxTrackingPreview',
    'ModalPortal',
    'PolypbaseIcon',
    'SkeletonRows',
  ]) {
    overrides[`src/components/${name}.tsx`] = { default: () => null };
  }
  const load = moduleLoader(overrides, {
    window: {
      location: { origin: 'https://polypbase.test' },
      localStorage: {
        getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value),
        removeItem: (key) => storage.delete(key),
      },
      addEventListener() {},
      removeEventListener() {},
    },
    document: {
      cookie: '',
      body: {},
      documentElement: { lang: language },
      querySelectorAll: () => [],
    },
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
        try {
          tree = runtime.render(() => type(props));
          runtime.commit();
        } finally {
          current = null;
        }
        return tree;
      },
      get tree() {
        return tree;
      },
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
  const t = (key) => {
    assert.ok(
      Object.hasOwn(locales[language], key),
      `missing ${language} key: ${key}`,
    );
    return locales[language][key];
  };
  const AdminView = load('src/components/AdminView.tsx').default;
  load('src/utils/qrLabels.ts').printQrLabels = () =>
    assert.fail('active donations must not prepare legacy QR labels');
  const root = mount(AdminView, {
    activeOrganizationId: 1,
    activeSection: 'transfers',
    speciesTarget: null,
    profile: {
      id: 9,
      is_superuser: true,
      active_organization: { id: 1, name: 'Source' },
      organizations: [],
    },
    language,
    t,
    boxes: [],
    zones: [],
    isLoading: false,
    onOpenBox() {},
    onSelectSection() {},
    onRequestOptions() {
      assert.fail('donations do not need export options');
    },
    ...extraProps,
  });
  const components = load(
    'src/components/sharing/InstitutionalExchangesPage.tsx',
  );
  const pageNode = () =>
    one(root.tree, (node) => node.type === components.default);
  function openSections() {
    const node = pageNode();
    const page = mount(node.type, node.props);
    return elements(
      page.tree,
      (child) => child.type === components.DonationSection,
    ).map((child) => mount(child.type, child.props));
  }
  return {
    root,
    pageNode,
    openSections,
    mount,
    load,
    components,
    requests,
    storage,
    t,
    reply(index, data, status = 200) {
      requests[index].resolve(
        new Response(JSON.stringify(data), {
          status,
          headers: { 'Content-Type': 'application/json' },
        }),
      );
    },
    cleanup() {
      for (const instance of mounted.toReversed()) instance.unmount();
    },
  };
}

const donationFixture = {
  proposal_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
  state: 'pending',
  revision: 1,
  created_at: '2026-11-10T10:00:00Z',
  cancelled_at: null,
  source_institution: { name: 'Source' },
  destination_institution: { name: 'Destination' },
  box: {
    id: 17,
    global_code: 'ATL-AAU-1.001',
    species_scientific_name: 'Aurelia aurita',
    strain_code: 'AAU-1',
    ownership_sequence: 1,
    global_strain_id: 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee',
    global_box_id: 'cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee',
  },
  created_by: {
    first_name: 'Test',
    last_name: 'Researcher',
    email: 'researcher@example.test',
  },
  cancelled_by: null,
  history_verified_at: '2026-11-09T10:00:00Z',
  history_verification: {
    verified_by: {
      first_name: 'Test',
      last_name: 'Researcher',
      email: 'researcher@example.test',
    },
    verified_at: '2026-11-09T10:00:00Z',
    snapshot_version: '1',
    sha256: 'test-only',
    polyp_state_revision: 0,
  },
};

for (const language of ['fr', 'en']) {
  test(`${language}: Administration mounts real donation lists and reads detail instead of the legacy QR transfer`, async (context) => {
    const h = administrationHarness(language);
    context.after(h.cleanup);
    assert.equal(
      h.pageNode().key,
      '9:1',
      'account and active institution define the page lifetime',
    );
    assert.equal(h.pageNode().props.organizationId, 1);
    const sections = h.openSections();
    assert.equal(sections.length, 2);
    assert.deepEqual(
      h.requests.map((request) => request.path),
      ['/api/box-donations/outgoing/', '/api/box-donations/incoming/'],
    );
    for (const request of h.requests) {
      assert.equal(request.init.method ?? 'GET', 'GET');
      assert.equal(request.init.headers.get('X-Organization-Id'), '1');
      assert.equal(request.init.cache, 'no-store');
      assert.equal(request.init.credentials, 'include');
      assert.equal(request.init.signal.aborted, false);
    }
    for (const section of sections)
      assert.ok(
        elements(section.tree, (node) => node.props?.role === 'status').length,
      );
    const incoming = { ...donationFixture, box: { ...donationFixture.box } };
    delete incoming.box.id;
    h.reply(0, { results: [donationFixture] });
    h.reply(1, { results: [incoming] });
    await tick();
    for (const section of sections) {
      section.render();
      const row = one(section.tree, (node) => node.type === 'article');
      assert.ok(
        row.props['aria-label'].includes(donationFixture.box.global_code),
      );
      assert.ok(row.props['aria-label'].includes(h.t('donationPending')));
      assert.equal(
        elements(
          section.tree,
          (node) => node.props?.className === 'transfer-package',
        ).length,
        0,
      );
    }
    const { RowActionMenu } = h.load('src/components/RowActionMenu.tsx');
    one(sections[0].tree, (node) => node.type === RowActionMenu).props.onAction(
      'details',
    );
    sections[0].render();
    const detailNode = one(
      sections[0].tree,
      (node) => node.type === h.components.DonationDetailDialog,
    );
    const detail = h.mount(detailNode.type, detailNode.props);
    assert.equal(
      h.requests[2].path,
      `/api/box-donations/${donationFixture.proposal_id}/`,
    );
    h.reply(2, {
      ...donationFixture,
      box: { ...donationFixture.box, global_code: 'DETAIL-NOT-SUMMARY' },
      history_verification: {
        ...donationFixture.history_verification,
        current: true,
      },
    });
    await tick();
    detail.render();
    assert.ok(
      elements(
        detail.tree,
        (node) =>
          node.type === 'strong' &&
          node.props.children === 'DETAIL-NOT-SUMMARY',
      ).length,
    );
    assert.equal(
      h.requests.length,
      3,
      'no legacy transfer, personal sharing or box collection requests',
    );
  });
}

const previewType = (h) =>
  h.load('src/components/sharing/ExchangeFormPreview.tsx').default;
const select = (tree, field) =>
  one(
    tree,
    (node) => node.type === 'select' && node.props.id?.endsWith(`-${field}`),
  );
const change = (node, value) => node.props.onChange({ target: { value } });
const submitButton = (tree) =>
  one(tree, (node) => node.type === 'button' && node.props.type === 'submit');
const confirmations = (tree) =>
  elements(
    tree,
    (node) => node.type === 'input' && node.props.type === 'checkbox',
  );
const sentStatus = (h, section) =>
  elements(
    section.tree,
    (node) =>
      node.props.role === 'status' &&
      node.props.children === h.t('donationProposalSent'),
  );
const outgoingPath = '/api/box-donations/outgoing/';
const preflightPath = '/api/box-donations/preflight/';
const choicePaths = (org) => [
  '/api/box-donations/destinations/',
  `/api/boxes/?organization=${org}&status=active&q=&limit=20&offset=0`,
];
const historyHash = '0123456789abcdef'.repeat(4);

function openSend(h, outgoing, org = 1) {
  const first = h.requests.length;
  one(
    outgoing.tree,
    (node) =>
      node.type === 'button' &&
      [node.props.children].flat().includes(h.t('donationSend')),
  ).props.onClick();
  const node = one(outgoing.render(), (child) => child.type === previewType(h));
  assert.equal(node.props.organizationId, org);
  const form = h.mount(node.type, node.props);
  assertRequests(
    h,
    first,
    org,
    choicePaths(org).map((path) => ['GET', path]),
  );
  return form;
}

function assertRequests(h, first, org, expected) {
  assert.deepEqual(
    h.requests
      .slice(first)
      .map(({ path, init }) => [
        init.method ?? 'GET',
        path,
        init.headers.get('X-Organization-Id'),
      ]),
    expected.map(([method, path]) => [method, path, String(org)]),
  );
}

function replyWithChoices(h, first, org, boxId, destinationId) {
  h.reply(first, {
    results: [
      {
        institution_id: destinationId,
        name: `Destination ${org}`,
        country: 'France',
      },
    ],
  });
  h.reply(first + 1, {
    count: 1,
    next: null,
    previous: null,
    results: [
      {
        id: boxId,
        global_code: `BOX-${boxId}`,
        status: 'active',
        organization: { id: org },
      },
    ],
  });
}

async function submit(tree) {
  let prevented = false;
  one(tree, (node) => node.type === 'form').props.onSubmit({
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  await tick();
}

test('Administration Send requires explicit choices, preflight and history confirmation before creating a donation', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const [outgoing] = h.openSections();
  h.reply(0, { results: [] });
  h.reply(1, { results: [] });
  await tick();
  outgoing.render();

  const form = openSend(h, outgoing);
  assert.equal(submitButton(form.tree).props.disabled, true);
  const boxId = donationFixture.box.id;
  const destinationId = 'destination-1';
  replyWithChoices(h, 2, 1, boxId, destinationId);
  await tick();
  let tree = form.render();
  const type = select(tree, 'type');
  assert.equal(type.props.defaultValue, 'donation');
  assert.deepEqual(
    elements(type, (node) => node.type === 'option').map((node) => [
      node.props.value,
      Boolean(node.props.disabled),
    ]),
    [
      ['donation', false],
      ['live', true],
      ['frozen', true],
      ['email_frozen', true],
    ],
  );
  assert.equal(select(tree, 'box').props.value, '');
  assert.equal(select(tree, 'institution').props.value, '');
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  change(select(tree, 'box'), String(boxId));
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  assert.equal(
    h.requests.length,
    4,
    'loading choices and submitting without both selections must not write',
  );

  change(select(tree, 'institution'), destinationId);
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, false);
  assert.equal(confirmations(tree).length, 0);
  await submit(tree);
  assertRequests(h, 4, 1, [['POST', preflightPath]]);
  const selection = {
    source_box_id: boxId,
    destination_institution_id: destinationId,
  };
  assert.deepEqual(JSON.parse(h.requests[4].init.body), selection);
  h.reply(4, { history: { sha256: historyHash } });
  await tick();
  tree = form.render();
  const [confirmation] = confirmations(tree);
  assert.equal(confirmation.props.checked, false);
  assert.equal(submitButton(tree).props.disabled, true);
  await submit(tree);
  assert.equal(h.requests.length, 5, 'preflight alone cannot send');

  confirmation.props.onChange({ target: { checked: true } });
  tree = form.render();
  assert.equal(submitButton(tree).props.disabled, false);
  await submit(tree);
  assertRequests(h, 4, 1, [
    ['POST', preflightPath],
    ['POST', outgoingPath],
  ]);
  const body = JSON.parse(h.requests[5].init.body);
  assert.match(
    body.request_id,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i,
  );
  assert.deepEqual(body, {
    ...selection,
    request_id: body.request_id,
    history_verified: true,
    history_sha256: historyHash,
  });
  h.reply(5, donationFixture, 201);
  await tick();
  outgoing.render();
  assert.equal(
    elements(outgoing.tree, (node) => node.type === previewType(h)).length,
    0,
  );
  form.unmount();
  assert.equal(sentStatus(h, outgoing).length, 1);
  assertRequests(h, 4, 1, [
    ['POST', preflightPath],
    ['POST', outgoingPath],
    ['GET', outgoingPath],
  ]);
  h.reply(6, { results: [donationFixture] });
  await tick();
  outgoing.render();
  assert.ok(
    one(outgoing.tree, (node) => node.type === 'article').props[
      'aria-label'
    ].includes(donationFixture.box.global_code),
  );
});

test('Administration closes Send without refreshing and isolates late choices across institution switches', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const sections = h.openSections();
  const [outgoing] = sections;
  h.reply(0, { results: [donationFixture] });
  h.reply(1, { results: [] });
  await tick();
  outgoing.render();
  const closed = openSend(h, outgoing);
  one(
    closed.tree,
    (node) =>
      node.type === 'button' && node.props['aria-label'] === h.t('close'),
  ).props.onClick();
  assert.equal(
    elements(outgoing.render(), (node) => node.type === previewType(h)).length,
    0,
  );
  closed.unmount();
  assert.ok(
    h.requests.slice(2, 4).every((request) => request.init.signal.aborted),
  );
  assert.equal(
    elements(outgoing.tree, (node) => node.type === 'article').length,
    1,
  );
  assert.equal(
    h.requests.length,
    4,
    'closing does not write or refresh the list',
  );

  const old = openSend(h, outgoing);
  h.load('src/api/client.ts').setActiveOrganizationContext(2);
  h.root.render({ activeOrganizationId: 2 });
  assert.equal(h.pageNode().key, '9:2');
  assert.ok(
    h.requests.slice(4, 6).every((request) => request.init.signal.aborted),
  );
  // Resolve during the context change, before the old keyed page is torn down.
  replyWithChoices(h, 4, 1, 18, 'previous');
  await tick();
  const stale = old.render();
  assert.equal(select(stale, 'box').props.disabled, true);
  assert.equal(select(stale, 'institution').props.disabled, true);
  old.unmount();
  for (const section of sections) section.unmount();

  const [next] = h.openSections();
  assertRequests(h, 6, 2, [
    ['GET', outgoingPath],
    ['GET', '/api/box-donations/incoming/'],
  ]);
  h.reply(6, { results: [] });
  h.reply(7, { results: [] });
  await tick();
  next.render();
  assert.equal(
    elements(next.tree, (node) => node.type === previewType(h)).length,
    0,
  );
  const current = openSend(h, next, 2);
  replyWithChoices(h, 8, 2, 27, 'current');
  await tick();
  replyWithChoices(h, 2, 1, 17, 'closed');
  await tick();
  const tree = current.render();
  for (const [field, value] of [
    ['box', 27],
    ['institution', 'current'],
  ]) {
    const input = select(tree, field);
    assert.equal(input.props.value, '');
    assert.deepEqual(
      elements(input, (node) => node.type === 'option').map(
        (node) => node.props.value,
      ),
      ['', value],
    );
  }
  assert.equal(submitButton(tree).props.disabled, true);
  assert.equal(sentStatus(h, next).length, 0);
  assert.equal(
    h.requests.length,
    10,
    'late choices must not trigger writes or refreshes',
  );
});

test('Administration donation read errors retry only the failed direction with real API reads', async (context) => {
  const h = administrationHarness();
  context.after(h.cleanup);
  const [outgoing, incoming] = h.openSections();
  h.reply(0, { detail: 'Forbidden' }, 403);
  h.reply(1, { results: [] });
  await tick();
  outgoing.render();
  incoming.render();
  const errorNode = one(
    outgoing.tree,
    (node) => typeof node.type === 'function' && node.props.error,
  );
  const error = h.mount(errorNode.type, errorNode.props);
  assert.equal(
    one(error.tree, (node) => node.type === 'p').props.children,
    h.t('donationForbidden'),
  );
  assert.ok(
    elements(
      incoming.tree,
      (node) =>
        node.type === 'strong' && node.props.children === h.t('donationEmpty'),
    ).length,
  );
  one(error.tree, (node) => node.type === 'button').props.onClick();
  outgoing.render();
  assert.equal(h.requests.length, 3);
  assert.equal(h.requests[2].path, '/api/box-donations/outgoing/');
  assert.equal(h.requests[2].init.headers.get('X-Organization-Id'), '1');
  assert.equal(h.requests[2].init.cache, 'no-store');
  h.reply(2, { results: [donationFixture] });
  await tick();
  outgoing.render();
  assert.equal(
    elements(outgoing.tree, (node) => node.type === 'article').length,
    1,
  );
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
  for (const request of h.requests.slice(2))
    assert.equal(request.init.headers.get('X-Organization-Id'), '2');
  // Transport deliberately permits late responses: the real hook must reject them.
  h.reply(0, { results: [donationFixture] });
  h.reply(1, { results: [donationFixture] });
  h.reply(2, { results: [] });
  h.reply(3, { results: [] });
  await tick();
  for (const section of next) {
    section.render();
    assert.equal(
      elements(section.tree, (node) => node.type === 'article').length,
      0,
    );
    assert.ok(
      elements(
        section.tree,
        (node) =>
          node.type === 'strong' &&
          node.props.children === h.t('donationEmpty'),
      ).length,
    );
  }
});

test('Administration never mounts exchanges for a missing institution or non-admin profile', (context) => {
  const h = administrationHarness('en', { activeOrganizationId: null });
  context.after(h.cleanup);
  assert.equal(
    elements(h.root.tree, (node) => node.type === h.components.default).length,
    0,
  );
  h.root.render({
    activeOrganizationId: 1,
    profile: {
      id: 9,
      is_superuser: false,
      organizations: [{ id: 1, name: 'Source' }],
      active_organization: { id: 1, name: 'Source' },
      memberships: [{ organization: { id: 1 }, role: 'technician' }],
    },
  });
  assert.equal(h.root.tree, null);
  assert.equal(h.requests.length, 0);
});
