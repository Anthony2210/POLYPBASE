import assert from 'node:assert/strict';
import test from 'node:test';
import { moduleLoader, hookRuntime, deferred, tick, one, elements } from './sharing-test-harness.mjs';

const organizationId = 17;
const labels = key => key;
const identity = { first_name: 'Test', last_name: 'Researcher', email: 'researcher@example.test' };
const incoming = {
  proposal_id: 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', state: 'pending', revision: 1,
  created_at: '2026-11-10T10:00:00Z', cancelled_at: null,
  source_institution: { name: 'Océan source' }, destination_institution: { name: 'Musée destination' },
  box: { global_code: 'BOX-001', strain_code: 'STRAIN-01', species_scientific_name: 'Aurelia aurita',
    global_strain_id: 'bbbbbbbb-bbbb-cccc-dddd-eeeeeeeeeeee', global_box_id: 'cccccccc-bbbb-cccc-dddd-eeeeeeeeeeee' },
  history_verified_at: '2026-11-09T10:00:00Z',
};
const outgoing = {
  ...incoming, box: { ...incoming.box, id: 81, ownership_sequence: 1 },
  created_by: identity, cancelled_by: null,
  history_verification: { verified_by: identity, verified_at: incoming.history_verified_at,
    snapshot_version: '1', sha256: 'synthetic-digest', polyp_state_revision: 0 },
};
const cancelled = {
  ...outgoing, proposal_id: 'dddddddd-bbbb-cccc-dddd-eeeeeeeeeeee', state: 'cancelled',
  cancelled_at: '2026-11-11T11:00:00Z', cancelled_by: identity,
  box: { ...outgoing.box, id: 82, global_code: 'BOX-002', species_scientific_name: 'Cassiopea xamachana' },
  source_institution: { name: 'Other source' }, destination_institution: { name: 'Other destination' },
};

// Only transport is mocked: donation API and organization header/lifetime logic are real.
// Fetch deliberately ignores abort so late server responses exercise publication guards.
function fixture(overrides = {}) {
  const requests = [];
  const storage = new Map([['polypbase.activeOrganizationId', String(organizationId)]]);
  const load = moduleLoader(overrides, {
    window: { localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key),
    }, location: { origin: 'https://app.test' }, addEventListener() {}, removeEventListener() {} },
    document: { cookie: '', body: {}, documentElement: { lang: 'en' }, querySelectorAll: () => [] },
    HTMLElement: class {},
    crypto: globalThis.crypto,
    fetch: (path, init) => {
      const pending = deferred();
      requests.push({ path, init, ...pending });
      return pending.promise;
    },
  });
  const client = load('src/api/client.ts');
  const api = load('src/api/boxDonations.ts');
  const reply = (index, data, status = 200) => requests[index].resolve(new Response(JSON.stringify(data), {
    status, headers: { 'Content-Type': 'application/json' },
  }));
  return { requests, storage, load, client, api, reply };
}

function hookFixture() {
  const runtime = hookRuntime();
  const f = fixture({ react: runtime.hooks });
  const useDonationRead = f.load('src/hooks/useDonationRead.ts').default;
  const signals = [];
  const render = (org = organizationId, resource = 'outgoing') => runtime.render(() => useDonationRead(org, resource, signal => {
    signals.push(signal);
    return resource === 'outgoing' || resource === 'incoming'
      ? f.api.listDonations(resource, org, signal) : f.api.getDonation(resource, org, signal);
  }));
  return { ...f, runtime, render, signals };
}

const BoxTrackingPreview = () => null;
const ModalPortal = () => null;
function componentFixture() {
  const runtime = hookRuntime();
  const f = fixture({
    react: { ...runtime.hooks, useId: () => 'donation-test-id' },
    'src/components/BoxTrackingPreview.tsx': { default: BoxTrackingPreview },
    'src/components/ModalPortal.tsx': { default: ModalPortal },
    'src/components/PolypbaseIcon.tsx': { default: () => null },
    'src/components/SkeletonRows.tsx': { default: () => null },

    'src/hooks/useAnchoredPopover.ts': { useAnchoredPopover: () => ({
      anchorRef: { current: null }, panelRef: { current: null }, position: { visibility: 'visible' }, id: 'test-menu',
    }) },
  });
  const components = f.load('src/components/sharing/InstitutionalExchangesPage.tsx');
  const Preview = f.load('src/components/sharing/ExchangeFormPreview.tsx').default;
  const renderPreview = (extra = {}) => runtime.render(() => Preview({ organizationId, language: 'en', t: labels, onClose() {}, onSent() {}, ...extra }));
  const opened = [];
  const props = { organizationId, language: 'en', t: labels, onOpenBox: (...args) => opened.push(args), direction: 'outgoing' };
  const render = (extra = {}) => runtime.render(() => components.DonationSection({ ...props, ...extra }));
  const renderDetail = extra => runtime.render(() => components.DonationDetailDialog({ organizationId, t: labels, onClose() {}, ...extra }));
  return { ...f, runtime, components, Preview, props, render, renderDetail, renderPreview, opened };
}
const rows = tree => elements(tree, node => node.type === 'article');
const text = tree => {
  if (Array.isArray(tree)) return tree.map(text).join(' ');
  if (tree == null || typeof tree === 'boolean') return '';
  return typeof tree === 'object' ? text(tree.props?.children) : String(tree);
};
const change = (node, value) => node.props.onChange({ target: { value } });
const button = (tree, label) => one(tree, node => node.type === 'button' && text(node).trim() === label);

for (const direction of ['outgoing', 'incoming']) {
  test(`${direction} API preserves the real {results} envelope, integer organization header and no-store`, async () => {
    const f = fixture();
    const controller = new AbortController();
    const payload = { results: [direction === 'outgoing' ? outgoing : incoming] };
    const first = f.api.listDonations(direction, organizationId, controller.signal);
    const second = f.api.listDonations(direction, organizationId, controller.signal);
    assert.equal(f.requests.length, 2, 'reads are not shared across resource lifetimes');
    for (const [index, request] of f.requests.entries()) {
      assert.equal(request.path, `/api/box-donations/${direction}/`);
      assert.equal(request.init.headers.get('X-Organization-Id'), '17', 'integer PK, not portable UUID');
      assert.equal(request.init.cache, 'no-store');
      assert.equal(request.init.credentials, 'include');
      assert.equal(request.init.signal.aborted, false);
      f.reply(index, payload);
    }
    assert.deepEqual(await first, payload);
    assert.deepEqual(await second, payload);
  });
}

test('detail API uses the encoded proposal endpoint and returns its distinct detail response', async () => {
  const f = fixture();
  const detail = { ...outgoing, history_verification: { ...outgoing.history_verification, current: false } };
  const pending = f.api.getDonation('proposal/with ?#', organizationId, new AbortController().signal);
  assert.equal(f.requests[0].path, '/api/box-donations/proposal%2Fwith%20%3F%23/');
  assert.equal(f.requests[0].init.headers.get('X-Organization-Id'), '17');
  assert.equal(f.requests[0].init.cache, 'no-store');
  f.reply(0, detail);
  assert.deepEqual(await pending, detail);
});

test('mismatched, UUID or missing organization context and pre-aborted reads never reach fetch', async () => {
  const f = fixture();
  await assert.rejects(f.api.listDonations('outgoing', 18, new AbortController().signal), f.client.ApiResourceCancelledError);
  f.storage.set('polypbase.activeOrganizationId', incoming.box.global_box_id);
  await assert.rejects(f.api.getDonation(incoming.proposal_id, organizationId, new AbortController().signal), f.client.ApiResourceCancelledError);
  f.storage.clear();
  await assert.rejects(f.api.listDonations('incoming', organizationId, new AbortController().signal), f.client.ApiResourceCancelledError);
  f.client.setActiveOrganizationContext(organizationId);
  const controller = new AbortController(); controller.abort();
  await assert.rejects(f.api.getDonation(incoming.proposal_id, organizationId, controller.signal), { name: 'AbortError' });
  assert.equal(f.requests.length, 0);
});

for (const kind of ['caller abort', 'organization switch']) {
  test(`API ${kind} aborts transport and rejects a late successful response`, async () => {
    const f = fixture(); const controller = new AbortController();
    const pending = f.api.listDonations('outgoing', organizationId, controller.signal);
    const rejected = assert.rejects(pending, error => error.name === 'AbortError' || error instanceof f.client.ApiResourceCancelledError);
    if (kind === 'caller abort') controller.abort(); else f.client.setActiveOrganizationContext(18);
    assert.equal(f.requests[0].init.signal.aborted, true);
    f.reply(0, { results: [outgoing] });
    await rejected;
  });
}

test('hook consumes {results}, does not restart for inline callbacks, and refresh masks previous data', async () => {
  const f = hookFixture();
  assert.equal(f.render().state.status, 'loading'); f.runtime.commit();
  f.reply(0, { results: [outgoing] }); await tick();
  assert.deepEqual(f.render().state.data, { results: [outgoing] }); f.runtime.commit();
  assert.equal(f.requests.length, 1);
  f.render().refresh();
  assert.equal(f.render().state.status, 'loading', 'old rows disappear before effects run');
  f.runtime.commit();
  assert.equal(f.signals[0].aborted, true, 'refresh cleans up the previous hook lifetime');
  f.reply(1, { results: [] }); await tick();
  assert.deepEqual(f.render().state.data, { results: [] });
  f.runtime.unmount();
});

test('resource switch aborts the old hook read and ignores its out-of-order response', async () => {
  const f = hookFixture(); f.render(); f.runtime.commit();
  assert.equal(f.render(organizationId, 'incoming').state.status, 'loading'); f.runtime.commit();
  assert.equal(f.signals[0].aborted, true); assert.equal(f.requests[0].init.signal.aborted, true);
  f.reply(1, { results: [incoming] }); await tick();
  f.reply(0, { results: [outgoing] }); await tick();
  assert.deepEqual(f.render(organizationId, 'incoming').state.data, { results: [incoming] });
  f.runtime.unmount();
});

test('organization switch masks ready rows immediately and aborts in-flight requests', async () => {
  const f = hookFixture(); f.render(); f.runtime.commit();
  f.reply(0, { results: [outgoing] }); await tick();
  assert.equal(f.render().state.status, 'ready');
  f.render().refresh(); f.render(); f.runtime.commit();
  f.client.setActiveOrganizationContext(18);
  assert.equal(f.requests[1].init.signal.aborted, true);
  assert.equal(f.render().state.status, 'loading');
  assert.equal(f.render(18).state.status, 'loading'); f.runtime.commit();
  assert.equal(f.requests[2].init.headers.get('X-Organization-Id'), '18');
  f.reply(2, { results: [] }); await tick();
  f.reply(1, { results: [outgoing] }); await tick();
  assert.deepEqual(f.render(18).state.data, { results: [] });
  f.runtime.unmount();
});

test('unmount aborts the hook and transport and prevents late publication', async () => {
  const f = hookFixture(); f.render(); f.runtime.commit(); f.runtime.unmount();
  assert.equal(f.signals[0].aborted, true); assert.equal(f.requests[0].init.signal.aborted, true);
  f.reply(0, { results: [outgoing] }); await tick();
  assert.equal(f.render().state.status, 'loading');
});

for (const boundary of ['unmount', 'organization', 'resource', 'refresh']) {
  for (const outcome of ['success', 'error']) {
    test(`deferred ${outcome} publication is rejected after ${boundary}`, async () => {
      const f = hookFixture(); f.render(); f.runtime.commit(); f.runtime.defer(true);
      f.reply(0, outcome === 'success' ? { results: [outgoing] } : { detail: 'Denied' }, outcome === 'success' ? 200 : 403);
      await tick(); // The response has already passed API checks; React has not applied its updater.
      let org = organizationId; let resource = 'outgoing';
      if (boundary === 'unmount') f.runtime.unmount();
      if (boundary === 'organization') { f.client.setActiveOrganizationContext(18); org = 18; }
      if (boundary === 'resource') resource = 'incoming';
      if (boundary === 'refresh') { f.runtime.defer(false); f.render().refresh(); }
      if (boundary !== 'unmount') { f.render(org, resource); f.runtime.commit(); }
      f.runtime.publish(); f.runtime.defer(false);
      assert.equal(f.render(org, resource).state.status, 'loading', 'no queued old rows or errors may become visible');
      f.runtime.unmount();
    });
  }
}

test('filter helper searches code, species and directional institution, with accent/case normalization', () => {
  const { filterDonations } = componentFixture().components;
  const data = [outgoing, cancelled];
  const ids = (...args) => Array.from(filterDonations(data, ...args), row => row.proposal_id);
  assert.deepEqual(ids('outgoing', '  bOx-001  ', '', ''), [outgoing.proposal_id]);
  assert.deepEqual(ids('outgoing', 'AURELIA', '', ''), [outgoing.proposal_id]);
  assert.deepEqual(ids('outgoing', 'musee', '', ''), [outgoing.proposal_id]);
  assert.deepEqual(ids('incoming', 'OCEAN', '', ''), [outgoing.proposal_id]);
  assert.deepEqual(ids('outgoing', 'ocean', '', ''), []);
  assert.deepEqual(ids('incoming', 'musee', '', ''), []);
  assert.deepEqual(ids('outgoing', '', 'donation', 'pending'), [outgoing.proposal_id]);
  assert.deepEqual(ids('outgoing', '', 'donation', 'cancelled'), [cancelled.proposal_id]);
  assert.deepEqual(ids('outgoing', '', 'transfer', ''), []);
  assert.deepEqual(ids('outgoing', 'aurelia', 'donation', 'cancelled'), []);
});

for (const direction of ['outgoing', 'incoming']) {
  test(`${direction} rows map pending/cancelled and expose only owned box previews`, async () => {
    const f = componentFixture();
    const render = () => f.render({ direction });
    assert.equal(elements(render(), node => node.props?.role === 'status' && node.props['aria-label'] === 'loading').length, 1);
    f.runtime.commit();
    const data = direction === 'outgoing' ? [outgoing, cancelled] : [incoming, { ...incoming, proposal_id: cancelled.proposal_id,
      state: 'cancelled', cancelled_at: cancelled.cancelled_at, box: { ...incoming.box, global_code: 'BOX-002' } }];
    f.reply(0, { results: data }); await tick();
    const tree = render();
    assert.equal(rows(tree).length, 2);
    assert.equal(rows(tree)[0].props['aria-label'], 'BOX-001, donationPending');
    assert.equal(rows(tree)[1].props['aria-label'], 'BOX-002, donationCancelled');
    assert.match(text(tree), /donationPending/); assert.match(text(tree), /donationCancelled/);
    if (direction === 'outgoing') {
      const preview = one(rows(tree)[0], node => node.type === BoxTrackingPreview);
      assert.equal(preview.props.boxId, 81); assert.equal(preview.props.code, 'BOX-001');
      assert.equal(preview.props.speciesName, 'Aurelia aurita'); assert.equal(preview.props.language, 'en');
      preview.props.onOpenBox(preview.props.boxId, preview.props.code);
      assert.deepEqual(f.opened, [[81, 'BOX-001']]);
      assert.notEqual(button(tree, 'donationSend').props.disabled, true);
      assert.equal(button(tree, 'donationSend').props['aria-describedby'], undefined);
      assert.doesNotMatch(text(tree), /donationSendUnavailable/);
      assert.match(text(tree), /Musée destination/);
    } else {
      assert.equal(elements(tree, node => node.type === BoxTrackingPreview || node.type === 'a').length, 0);
      assert.equal(one(rows(tree)[0], node => node.type === 'strong').props.children, 'BOX-001');
      assert.match(text(tree), /Océan source/);
      assert.equal(elements(tree, node => node.type === 'button' && text(node).includes('donationRefresh')).length, 0);
      f.runtime.commit(); assert.equal(f.requests.length, 1);
    }
    f.runtime.unmount();
  });
}

test('closing the send form preserves the list; successful send closes it and refetches outgoing rows', async () => {
  const f = componentFixture(); f.render(); f.runtime.commit();
  f.reply(0, { results: [outgoing, cancelled] }); await tick();
  let tree = f.render();
  assert.equal(elements(tree, node => node.type === f.Preview).length, 0);
  button(tree, 'donationSend').props.onClick();
  tree = f.render();
  const preview = one(tree, node => node.type === f.Preview);
  assert.equal(preview.props.t, labels);
  assert.equal(preview.props.organizationId, organizationId);
  assert.equal(preview.props.language, 'en');
  assert.equal(rows(tree).length, 2);
  assert.doesNotMatch(text(tree), /donationSendUnavailable/);
  f.runtime.commit(); assert.equal(f.requests.length, 1);
  preview.props.onClose(); tree = f.render();
  assert.equal(elements(tree, node => node.type === f.Preview).length, 0);
  assert.equal(rows(tree).length, 2);
  assert.equal(f.requests.length, 1);
  button(tree, 'donationSend').props.onClick();
  one(f.render(), node => node.type === f.Preview).props.onSent();
  tree = f.render();
  assert.equal(elements(tree, node => node.type === f.Preview).length, 0);
  assert.match(text(tree), /donationProposalSent/);
  assert.equal(rows(tree).length, 0);
  f.runtime.commit();
  assert.deepEqual(f.requests.map(request => request.path), Array(2).fill('/api/box-donations/outgoing/'));
  f.reply(1, { results: [outgoing, cancelled, { ...outgoing, proposal_id: 'new', box: { ...outgoing.box, global_code: 'BOX-NEW' } }] });
  await tick();
  assert.deepEqual(rows(f.render()).map(row => row.props['aria-label']), ['BOX-001, donationPending', 'BOX-002, donationCancelled', 'BOX-NEW, donationPending']);
  assert.equal(elements(f.render({ direction: 'incoming' }), node => node.type === 'button' && text(node).includes('donationSend')).length, 0);
  f.runtime.unmount();
});

const destination = (institution_id, name, country) => ({ institution_id, name, country });
const directory = [destination('jp', 'Zeta', 'Japan'), destination('fr-z', 'Zeta', 'France'),
  destination('missing', 'Unknown country lab', ''), destination('fr-a', 'Alpha', ' France '),
  destination('blank', 'Blank country lab', '   ')];
const sourceBox = (id = 81, global_code = 'BOX-001', org = organizationId, status = 'active') =>
  ({ id, global_code, status, organization: { id: org } });
const boxPage = (...results) => ({ count: results.length, next: null, previous: null, results });
const field = (tree, suffix) => one(tree, node => ['select', 'input'].includes(node.type) && node.props.id?.endsWith(`-${suffix}`));
const options = tree => elements(tree, node => node.type === 'option');
const choiceStatus = (tree, suffix) => {
  const input = field(tree, suffix);
  const wrapper = one(tree, node => node.type === 'div' && node.props.children?.includes?.(input));
  // The harness leaves child components as JSX elements rather than rendering them.
  const status = one(wrapper, node => typeof node.type === 'function');
  return status.type(status.props);
};
const submitButton = tree => one(tree, node => node.type === 'button' && node.props.type === 'submit');
const submit = tree => one(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {} });

function assertRequest(request, method, path, org = organizationId) {
  assert.equal(request.path, path);
  assert.equal(request.init.method ?? 'GET', method);
  assert.equal(request.init.headers.get('X-Organization-Id'), String(org));
}

test('donation form renders sorted optgroups, merges missing countries and disables non-donation types', async () => {
  const f = componentFixture();
  const loading = f.renderPreview(); f.runtime.commit();
  assert.equal(loading.type, ModalPortal);
  assert.equal(one(loading, node => node.props?.role === 'dialog').props['aria-modal'], 'true');
  assert.equal(field(loading, 'box').props.disabled, true);
  assert.equal(field(loading, 'institution').props.disabled, true);
  assert.equal(submitButton(loading).props.disabled, true);
  assertRequest(f.requests[0], 'GET', '/api/box-donations/destinations/');
  assertRequest(f.requests[1], 'GET', '/api/boxes/?organization=17&status=active&q=&limit=20&offset=0');
  f.reply(0, { results: directory });
  f.reply(1, boxPage(sourceBox(), sourceBox(82, 'INACTIVE', organizationId, 'archived')));
  await tick();
  const tree = f.renderPreview();
  const type = field(tree, 'type');
  assert.equal(type.props.defaultValue, 'donation');
  assert.deepEqual(options(type).map(node => [node.props.value, Boolean(node.props.disabled)]), [
    ['donation', false], ['live', true], ['frozen', true], ['email_frozen', true],
  ]);
  assert.deepEqual(options(field(tree, 'box')).map(node => node.props.value), ['', 81]);
  assert.deepEqual(elements(field(tree, 'institution'), node => node.type === 'optgroup').map(group => [group.props.label,
    options(group).map(option => [option.props.value, text(option)])]), [
    ['donationCountryUnspecified', [['blank', 'Blank country lab'], ['missing', 'Unknown country lab']]],
    ['France', [['fr-a', 'Alpha'], ['fr-z', 'Zeta']]],
    ['Japan', [['jp', 'Zeta']]],
  ]);
  assert.equal(submitButton(tree).props.disabled, true, 'options alone do not enable preflight');
  f.runtime.unmount();
});

test('late choices from the previous organization never replace the new organization choices', async () => {
  const f = componentFixture(); f.renderPreview(); f.runtime.commit();
  f.client.setActiveOrganizationContext(18);
  const render = () => f.renderPreview({ organizationId: 18 });
  assert.equal(field(render(), 'institution').props.disabled, true); f.runtime.commit();
  assert.equal(f.requests[0].init.signal.aborted, true);
  assert.equal(f.requests[1].init.signal.aborted, true);
  assertRequest(f.requests[2], 'GET', '/api/box-donations/destinations/', 18);
  assertRequest(f.requests[3], 'GET', '/api/boxes/?organization=18&status=active&q=&limit=20&offset=0', 18);
  f.reply(2, { results: [destination('new', 'Current destination', 'Canada')] });
  f.reply(3, boxPage(sourceBox(181, 'CURRENT-BOX', 18))); await tick();
  f.reply(0, { results: directory }); f.reply(1, boxPage(sourceBox())); await tick();
  const tree = render();
  assert.deepEqual(options(field(tree, 'institution')).map(node => node.props.value), ['', 'new']);
  assert.deepEqual(options(field(tree, 'box')).map(node => node.props.value), ['', 181]);
  f.runtime.unmount();
});

test('source and destination errors can be retried; empty results remain disabled and search can recover', async () => {
  const f = componentFixture(); f.renderPreview(); f.runtime.commit();
  f.reply(0, { detail: 'Denied' }, 403); f.reply(1, { detail: 'Unavailable' }, 500); await tick();
  let tree = f.renderPreview();
  for (const [suffix, key] of [['institution', 'donationForbidden'], ['box', 'donationChoicesError']]) {
    assert.equal(field(tree, suffix).props.disabled, true);
    const error = choiceStatus(tree, suffix);
    assert.equal(text(one(error, node => node.props.role === 'alert')), key);
    button(error, 'lineageRetry').props.onClick();
  }
  assert.equal(submitButton(tree).props.disabled, true);
  f.renderPreview(); f.runtime.commit();
  assert.deepEqual(f.requests.slice(2).map(request => request.path), f.requests.slice(0, 2).map(request => request.path));
  f.reply(2, { results: [] }); f.reply(3, boxPage()); await tick();
  tree = f.renderPreview();
  for (const [suffix, key] of [['institution', 'donationNoDestination'], ['box', 'shareNoBox']]) {
    assert.equal(field(tree, suffix).props.disabled, true);
    const empty = choiceStatus(tree, suffix);
    assert.equal(text(one(empty, node => node.props.role === 'status')), key);
    assert.equal(elements(empty, node => node.props?.role === 'alert').length, 0);
  }
  change(field(tree, 'search'), '  BOX-001  ');
  button(f.renderPreview(), 'shareSearch').props.onClick();
  f.renderPreview(); f.runtime.commit();
  assert.equal(f.requests.length, 5);
  assertRequest(f.requests[4], 'GET', '/api/boxes/?organization=17&status=active&q=BOX-001&limit=20&offset=0');
  f.reply(4, boxPage(sourceBox())); await tick();
  tree = f.renderPreview();
  assert.equal(field(tree, 'box').props.disabled, false);
  assert.equal(field(tree, 'institution').props.disabled, true);
  f.runtime.unmount();
});

test('explicit confirmation gates send, duplicate submits are locked, and uncertain send replays the exact body', async () => {
  const f = componentFixture();
  let sent = 0;
  const render = () => f.renderPreview({ onSent: () => sent++ });
  render(); f.runtime.commit();
  f.reply(0, { results: directory }); f.reply(1, boxPage(sourceBox())); await tick();
  change(field(render(), 'box'), '81');
  change(field(render(), 'institution'), 'fr-a');
  const selection = { source_box_id: 81, destination_institution_id: 'fr-a' };
  const hash = '0123456789abcdef'.repeat(4);
  let tree = render();
  assert.equal(submitButton(tree).props.disabled, false);
  submit(tree); submit(tree);
  assert.equal(f.requests.length, 3);
  assertRequest(f.requests[2], 'POST', '/api/box-donations/preflight/');
  assert.deepEqual(JSON.parse(f.requests[2].init.body), selection);
  f.reply(2, { history: { sha256: hash } }); await tick();
  tree = render();
  const confirmation = tree => one(tree, node => node.type === 'input' && node.props.type === 'checkbox');
  assert.equal(one(tree, node => node.type === 'a').props.href, '/boxes/BOX-001');
  assert.equal(confirmation(tree).props.checked, false);
  assert.equal(submitButton(tree).props.disabled, true);
  submit(tree); await tick();
  assert.equal(f.requests.length, 3, 'forced submit without confirmation must not send');
  confirmation(render()).props.onChange({ target: { checked: true } });
  tree = render();
  assert.equal(submitButton(tree).props.disabled, false);
  submit(tree); submit(tree);
  assert.equal(f.requests.length, 4);
  assertRequest(f.requests[3], 'POST', '/api/box-donations/outgoing/');
  const body = f.requests[3].init.body;
  const { request_id } = JSON.parse(body);
  assert.match(request_id, /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i);
  assert.deepEqual(JSON.parse(body), { ...selection, request_id, history_verified: true, history_sha256: hash });
  assert.equal(submitButton(render()).props.disabled, true);
  f.requests[3].reject(new TypeError('Mock connection lost')); await tick();
  tree = render();
  assert.equal(text(one(tree, node => node.props?.role === 'alert')), 'donationSendUncertain');
  assert.equal(sent, 0);
  assert.equal(field(tree, 'box').props.disabled, true);
  assert.equal(field(tree, 'institution').props.disabled, true);
  assert.equal(confirmation(tree).props.disabled, true);
  assert.equal(text(submitButton(tree)), 'lineageRetry');
  assert.equal(submitButton(tree).props.disabled, false);
  submit(tree); submit(tree);
  assert.equal(f.requests.length, 5, 'no additional preflight or duplicate retry');
  assertRequest(f.requests[4], 'POST', '/api/box-donations/outgoing/');
  assert.equal(f.requests[4].init.body, body);
  f.reply(4, outgoing, 201); await tick();
  assert.equal(sent, 1);
  f.runtime.unmount();
});

test('rendered search/type/state controls combine filters and reset without additional requests', async () => {
  const f = componentFixture(); f.render(); f.runtime.commit();
  f.reply(0, { results: [outgoing, cancelled] }); await tick();
  let tree = f.render();
  change(one(tree, node => node.type === 'input' && node.props.type === 'search'), 'musee');
  tree = f.render(); assert.equal(rows(tree).length, 1);
  let selects = elements(tree, node => node.type === 'select');
  change(selects[0], 'donation'); change(selects[1], 'cancelled');
  tree = f.render(); assert.equal(rows(tree).length, 0); assert.match(text(tree), /donationNoMatch/);
  one(tree, node => node.type === 'button' && node.props['aria-label'] === 'overviewResetFilters').props.onClick();
  tree = f.render(); assert.equal(rows(tree).length, 2);
  assert.equal(one(tree, node => node.type === 'input').props.value, '');
  selects = elements(tree, node => node.type === 'select');
  assert.deepEqual(selects.map(node => node.props.value), ['', '']);
  change(selects[1], 'cancelled'); tree = f.render();
  assert.equal(rows(tree).length, 1); assert.match(text(tree), /Cassiopea xamachana/);
  assert.equal(f.requests.length, 1, 'filters are local to the actual results envelope');
  f.runtime.unmount();
});

test('empty results have their own unfiltered empty state', async () => {
  const f = componentFixture(); f.render(); f.runtime.commit(); f.reply(0, { results: [] }); await tick();
  assert.match(text(f.render()), /donationEmpty/); assert.doesNotMatch(text(f.render()), /donationNoMatch/);
  f.runtime.unmount();
});

for (const [status, key] of [[403, 'donationForbidden'], [404, 'donationNotFound'], [500, 'donationReadError']]) {
  for (const view of ['list', 'detail']) {
    test(`${view} HTTP ${status} maps to ${key} and retries the same read`, async () => {
      const f = componentFixture();
      const render = () => view === 'list' ? f.render() : f.renderDetail({ proposalId: incoming.proposal_id });
      render(); f.runtime.commit(); f.reply(0, { detail: 'Synthetic failure' }, status); await tick();
      const tree = render();
      const errorComponent = one(tree, node => typeof node.type === 'function' && node.props?.error instanceof f.client.ApiError);
      assert.equal(errorComponent.props.error.status, status);
      const alert = errorComponent.type(errorComponent.props);
      assert.equal(alert.props.role, 'alert'); assert.match(text(alert), new RegExp(key));
      assert.equal(rows(tree).length, 0);
      button(alert, 'lineageRetry').props.onClick();
      assert.equal(elements(render(), node => node.props?.role === 'status' && node.props['aria-label'] === 'loading').length, 1);
      f.runtime.commit(); assert.equal(f.requests.length, 2); assert.equal(f.requests[1].path, f.requests[0].path);
      f.reply(1, view === 'list' ? { results: [outgoing] } : incoming); await tick();
      assert.equal(elements(render(), node => node.props?.error instanceof f.client.ApiError).length, 0);
      f.runtime.unmount();
    });
  }
}

test('network rejection maps to generic read error without publishing rows', async () => {
  const f = componentFixture(); f.render(); f.runtime.commit();
  f.requests[0].reject(new TypeError('Synthetic offline failure')); await tick();
  const errorComponent = one(f.render(), node => typeof node.type === 'function' && node.props?.error);
  assert.match(text(errorComponent.type(errorComponent.props)), /donationReadError/);
  assert.equal(rows(f.render()).length, 0); f.runtime.unmount();
});

test('real row menu action opens a dialog whose data comes from detail endpoint, never summary reuse', async () => {
  const f = componentFixture(); f.render(); f.runtime.commit();
  f.reply(0, { results: [outgoing] }); await tick();
  const tree = f.render();
  const menu = one(rows(tree)[0], node => typeof node.type === 'function' && node.props?.actions);
  assert.equal(menu.props.actions.length, 1);
  assert.equal(menu.props.actions[0].action, 'details'); assert.equal(menu.props.actions[0].label, 'donationDetails');
  assert.ok(!menu.props.actions[0].disabled);
  // Execute the real menu with its own hook instance; only portal/positioning are mocked.
  const runtime = hookRuntime();
  const load = moduleLoader({ react: runtime.hooks, 'react-dom': { createPortal: child => child },
    'src/hooks/useAnchoredPopover.ts': { useAnchoredPopover: () => ({ anchorRef: { current: null },
      panelRef: { current: null }, position: { visibility: 'visible' }, id: 'test-menu' }) } }, { document: { body: {} } });
  const Menu = load('src/components/RowActionMenu.tsx').RowActionMenu;
  const renderMenu = () => runtime.render(() => Menu(menu.props));
  const trigger = one(renderMenu(), node => node.type === 'button');
  assert.equal(trigger.props['aria-haspopup'], 'menu'); trigger.props.onClick();
  const menuTree = renderMenu();
  one(menuTree, node => node.props?.role === 'menuitem').props.onClick();
  const dialog = one(f.render(), node => node.props?.proposalId === outgoing.proposal_id);
  assert.equal(dialog.props.organizationId, organizationId);
  assert.equal(f.requests.length, 1, 'creating a dialog element does not recycle summary data');

  const detailFixture = componentFixture();
  const renderDetail = () => detailFixture.renderDetail(dialog.props);
  const loadingDetail = renderDetail();
  assert.equal(elements(loadingDetail, node => node.props?.role === 'status').length, 1);
  assert.doesNotMatch(text(loadingDetail), /BOX-001/, 'no summary shown while the detail is loading');
  detailFixture.runtime.commit();
  assert.equal(detailFixture.requests[0].path, `/api/box-donations/${outgoing.proposal_id}/`);
  const detail = { ...cancelled, proposal_id: outgoing.proposal_id, box: { ...cancelled.box, global_code: 'DETAIL-ONLY' },
    history_verification: { ...cancelled.history_verification, current: false } };
  detailFixture.reply(0, detail); await tick();
  const detailTree = renderDetail();
  assert.match(text(detailTree), /DETAIL-ONLY/); assert.doesNotMatch(text(detailTree), /BOX-001/);
  assert.match(text(detailTree), /donationCancelledAt/); assert.match(text(detailTree), /donationCancelled/);
  assert.match(text(detailTree), /donationAcceptanceUnavailable/);
  button(detailTree, 'close').props.onClick();
  assert.equal(elements(f.render(), node => node.props?.proposalId).length, 0);
  runtime.unmount(); detailFixture.runtime.unmount(); f.runtime.unmount();
});

test('pending incoming detail renders without cancellation facts or ordinary box links', async () => {
  const f = componentFixture(); const render = () => f.renderDetail({ proposalId: incoming.proposal_id });
  render(); f.runtime.commit(); f.reply(0, incoming); await tick();
  const tree = render();
  assert.match(text(tree), /donationPending/); assert.doesNotMatch(text(tree), /donationCancelledAt/);
  assert.equal(elements(tree, node => node.type === BoxTrackingPreview || node.type === 'a').length, 0);
  assert.equal(one(tree, node => node.props?.role === 'dialog').props['aria-modal'], 'true');
  f.runtime.unmount();
});
