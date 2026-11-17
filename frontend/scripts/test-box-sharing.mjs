import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { moduleLoader, hookRuntime, deferred, tick, read, one, elements } from './sharing-test-harness.mjs';

const profile = { id: 10, email: 'recipient@example.test', memberships: [{ organization: { id: 1 }, role: 'admin' }] };
const other = { ...profile, id: 11, email: 'other@example.test' };
const labels = key => key;
const descriptor = name => ({ name, group: name.split('.')[0], mandatory: name === 'source.institution_name', requires: name.startsWith('observation.') && name !== 'observation.time' ? ['observation.time'] : [] });
const options = { schema: 'polypbase.scientific-snapshot', schema_version: '1.0', frozen_capture_available: true, modes: ['live', 'frozen'],
  mandatory_selectors: ['source.institution_name'], selectors: ['source.institution_name', 'source.box_code', 'observation.time', 'observation.polyp_count', 'observation.ephyrae_count'].map(descriptor),
  lifetime_policy: { configured: true, invitation_max_lifetime_seconds: 86400, access_max_lifetime_seconds: 604800 } };
const snapshot = { schema: 'polypbase.scientific-snapshot', schema_version: '1.0', disclosed: ['source.institution_name', 'observation.time', 'observation.polyp_count'],
  source: { institution_name: { state: 'value', value: 'Synthetic institution' } }, observations: [
    { time: { precision: 'iso_week', iso_year: 2026, iso_week: 20, measured_on: null }, values: { polyp_count: { state: 'value', value: 0 } } },
    { time: { precision: 'legacy_unverified', iso_year: 2026, iso_week: 21, measured_on: '2026-05-18' }, values: { polyp_count: { state: 'null' } } },
  ] };

function clientFixture() {
  const requests = [];
  const storage = new Map([['polypbase.activeOrganizationId', '1']]);
  const load = moduleLoader({}, {
    window: { localStorage: { getItem: key => storage.get(key) ?? null }, location: { origin: 'https://app.test' } },
    document: { cookie: 'csrftoken=synthetic-csrf' },
    fetch: async (path, init) => { requests.push({ path, init }); return new Response('{}', { headers: { 'Content-Type': 'application/json' } }); },
  });
  return { requests, storage, load, api: load('src/api/boxSharing.ts').sharingApi };
}

test('recipient GETs and invitation POSTs use no organization context, no-store and existing CSRF', async () => {
  const { requests, api } = clientFixture();
  const id = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
  await Promise.all([api.received(), api.received()]);
  await api.read(id); await api.inspect('synthetic-secret'); await api.accept('synthetic-secret');
  assert.equal(requests.length, 5, 'personal GETs must not deduplicate between account lifetimes');
  for (const request of requests) {
    assert.equal(request.init.headers.has('X-Organization-Id'), false);
    assert.equal(request.init.cache, 'no-store'); assert.equal(request.init.credentials, 'include');
    assert.ok(request.path.startsWith('/api/shared-boxes/'));
    assert.ok(!request.path.includes('synthetic-secret'));
  }
  for (const request of requests.slice(3)) {
    assert.equal(request.init.method, 'POST'); assert.equal(request.init.headers.get('X-CSRFToken'), 'synthetic-csrf');
    assert.deepEqual(JSON.parse(request.init.body), { token: 'synthetic-secret' });
  }
});
test('source calls preserve active institution semantics, bounded pagination and revision payload', async () => {
  const { requests, api, storage } = clientFixture();
  await api.list(1, 25, 7); await api.update(1, 3, { expected_policy_revision: 4, selectors: ['source.institution_name'] });
  assert.equal(requests[0].path, '/api/admin/box-shares/?limit=25&offset=25&box_id=7');
  assert.equal(requests[0].init.headers.get('X-Organization-Id'), '1');
  assert.equal(requests[1].init.headers.get('X-Organization-Id'), '1');
  assert.equal(JSON.parse(requests[1].init.body).expected_policy_revision, 4);
  storage.set('polypbase.activeOrganizationId', '2');
  assert.throws(() => api.create(1, {}), /Organization resource context changed/);
  assert.equal(requests.length, 2, 'no forged source organization request');
});

function sessionFixture(sequence) {
  let requests = 0;
  const load = moduleLoader({ 'src/api/client.ts': {
    ApiError: class ApiError extends Error { constructor(status, message, data) { super(message); this.status = status; this.data = data; } },
    apiGet: async (_path, options) => { assert.equal(options.skipOrganizationContext, true); const value = sequence[requests++]; if (value instanceof Error) throw value; return value; },
    getStoredActiveOrganizationId: () => 1,
    ApiResourceCancelledError: class extends Error {},
  } });
  return { load, session: load('src/api/recipientSession.ts'), client: load('src/api/client.ts'), requests: () => requests };
}
for (const changed of [other, { ...profile, email: 'changed@example.test' }]) {
  test(`account or mailbox changed after grant response discards protected content: ${changed.email}`, async () => {
    const f = sessionFixture([profile, changed]); let reads = 0;
    await assert.rejects(f.session.readForRecipient(profile, new AbortController().signal, async () => { reads++; return snapshot; }), error => error.status === 401);
    assert.equal(reads, 1); assert.equal(f.requests(), 2);
  });
}
test('account mismatch before a read never requests any grant or source data', async () => {
  const f = sessionFixture([other]); let reads = 0;
  await assert.rejects(f.session.readForRecipient(profile, new AbortController().signal, async () => { reads++; }), error => error.status === 401);
  assert.equal(reads, 0);
});
test('SessionAuthentication 403 is distinguished from an authenticated invitation mismatch', async () => {
  const f = sessionFixture([profile, profile]);
  const mismatch = new f.client.ApiError(403, '', { code: 'invitation_recipient_mismatch' });
  await assert.rejects(f.session.readForRecipient(profile, new AbortController().signal, async () => { throw mismatch; }), error => error === mismatch);
  const sequence = [profile];
  const lost = sessionFixture(sequence);
  sequence.push(new lost.client.ApiError(403, ''));
  await assert.rejects(lost.session.readForRecipient(profile, new AbortController().signal, async () => {
    throw new lost.client.ApiError(403, '');
  }), error => error.status === 401);
});
test('source session authorization requires actual Admin membership without a superuser shortcut', async () => {
  const f = sessionFixture([profile, profile]);
  const source = f.load('src/api/sourceSharingSession.ts');
  await source.readForSourceAdmin(profile, 1, new AbortController().signal, async () => true);
  assert.throws(() => source.requireSourceMembership({ ...profile, is_superuser: true, memberships: [{ organization: { id: 1 }, role: 'viewer' }] }, 1), error => error.status === 403);
  assert.throws(() => source.requireSourceMembership({ ...profile, memberships: [{ organization: { id: 2 }, role: 'admin' }] }, 1), error => error.status === 403);
});

const utils = moduleLoader()('src/utils/boxSharing.ts');
test('disclosure has no defaults, mandatory fields and server dependencies must be selected explicitly', () => {
  assert.equal(utils.validSelection(options, []), false);
  assert.equal(utils.validSelection(options, ['source.box_code']), false);
  assert.equal(utils.validSelection(options, ['source.institution_name', 'observation.polyp_count']), false);
  assert.equal(utils.validSelection(options, snapshot.disclosed), true);
  assert.equal(utils.validSelection(options, ['source.institution_name', 'unknown.field']), false);
  assert.equal(utils.selectorIsLocked(descriptor('observation.polyp_count'), options.selectors, []), true);
  assert.equal(utils.selectorIsLocked(descriptor('observation.time'), options.selectors, snapshot.disclosed), true);
});
test('UTC expiry parsing is explicit and rejects normalization, missing input and policy defaults', () => {
  assert.equal(utils.utcInput('2026-10-25T02:30'), '2026-10-25T02:30:00.000Z');
  assert.equal(utils.utcInput('2026-02-30T12:00'), null);
  assert.equal(utils.utcInput(''), null);
  assert.equal(utils.validExpiry('2026-11-12T13:00', 7200, Date.parse('2026-11-12T12:00Z')), true);
  assert.equal(utils.validExpiry('2026-11-12T15:00', 7200, Date.parse('2026-11-12T12:00Z')), false);
  assert.equal(utils.validExpiry('2026-11-12T13:00', null, Date.parse('2026-11-12T12:00Z')), false);
});
test('invitation fragment parsing rejects duplicate, missing or oversized secrets; grants are UUID routes only', () => {
  assert.equal(utils.parseInvitationToken('#token=synthetic'), 'synthetic');
  assert.equal(utils.parseInvitationToken('#token=a&token=b'), null);
  assert.equal(utils.parseInvitationToken('#other=synthetic'), null);
  assert.equal(utils.parseInvitationToken('#token=' + 'a'.repeat(201)), null);
  assert.equal(utils.sharedRoute('/shared-boxes/accept'), 'accept');
  assert.equal(utils.sharedRoute('/shared-boxes/7'), 'invalid');
  assert.equal(utils.sharedRoute('/shared-boxes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee'), 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee');
});
test('scientific zero, NULL, blank, source-absent and undisclosed remain distinct', () => {
  assert.equal(utils.fieldText({ state: 'value', value: 0 }, labels), '0');
  assert.equal(utils.fieldText({ state: 'null' }, labels), 'shareNull');
  assert.equal(utils.fieldText({ state: 'blank' }, labels), 'shareBlank');
  assert.equal(utils.fieldText({ state: 'absent' }, labels), 'shareAbsent');
  assert.equal(utils.fieldText(undefined, labels), 'shareUndisclosed');
  assert.equal(utils.fieldText({ state: 'value', value: 'known' }, labels), 'known', 'scientific text is not an enum');
});
test('scientific rendering uses only disclosed fields, ISO weeks never invent dates, and DOM history is bounded', () => {
  const View = moduleLoader()('src/components/sharing/ScientificSnapshotView.tsx').default;
  const html = renderToStaticMarkup(React.createElement(View, { snapshot: { ...snapshot,
    identity: { species_scientific_name: { state: 'value', value: 'HIDDEN_SPECIES' } },
    observations: Array.from({ length: 151 }, (_, index) => index === 1 ? snapshot.observations[1] : snapshot.observations[0]),
  }, t: labels }));
  assert.match(html, /<td>0<\/td>/); assert.match(html, /shareNull/); assert.match(html, /shareWeekOnly/);
  assert.doesNotMatch(html, /HIDDEN_SPECIES|2026-05-18|\/api\/|href=.*boxes\//);
  assert.equal((html.match(/<tr>/g) ?? []).length, 51, '50 records plus table header');
  const empty = renderToStaticMarkup(React.createElement(View, { snapshot: { ...snapshot, observations: [] }, t: labels }));
  assert.match(empty, /shareHistoryEmpty/);
  const hidden = renderToStaticMarkup(React.createElement(View, { snapshot: { ...snapshot, disclosed: ['source.institution_name'], observations: undefined }, t: labels }));
  assert.match(hidden, /shareHistoryUndisclosed/);
  const unsupported = renderToStaticMarkup(React.createElement(View, { snapshot: { ...snapshot, schema_version: '99' }, t: labels }));
  assert.match(unsupported, /shareUnsupported/); assert.doesNotMatch(unsupported, /Synthetic institution/);
});

function resourceFixture() {
  const runtime = hookRuntime(); const pending = [];
  const load = moduleLoader({ react: runtime.hooks });
  const useResource = load('src/hooks/useSharingResource.ts').useSharingResource;
  const render = key => runtime.render(() => useResource(key, signal => { const request = deferred(); pending.push({ ...request, signal }); return request.promise; }));
  return { runtime, pending, render };
}
test('resource changes mask old data before effects, abort old requests and reject late responses', async () => {
  const f = resourceFixture(); f.render('account-A:grant-A'); f.runtime.commit(); await tick();
  f.pending[0].resolve(snapshot); await tick(); assert.equal(f.render('account-A:grant-A').data, snapshot);
  assert.equal(f.render('account-B:grant-B').data, undefined, 'no one-frame cached snapshot');
  f.runtime.commit(); await tick(); assert.equal(f.pending[0].signal.aborted, true);
  f.pending[1].resolve({ new: true }); await tick(); assert.equal(f.render('account-B:grant-B').data.new, true);
  f.runtime.unmount();
});
test('refresh clears protected data and a denied grant never falls back to Box APIs', async () => {
  const f = resourceFixture(); f.render('grant'); f.runtime.commit(); await tick(); f.pending[0].resolve(snapshot); await tick();
  f.render('grant').refresh(); assert.equal(f.render('grant').data, undefined); await tick();
  const error = new Error('share_revoked'); f.pending[1].reject(error); await tick();
  assert.equal(f.render('grant').data, undefined); assert.equal(f.render('grant').error, error); assert.equal(f.pending.length, 2);
  f.runtime.unmount();
});
test('deferred React publication is rechecked after unmount/account invalidation', async () => {
  const f = resourceFixture(); f.render('old'); f.runtime.commit(); await tick(); f.runtime.defer(true);
  f.pending[0].resolve(snapshot); await tick(); f.runtime.unmount(); f.runtime.publish();
  assert.equal(f.render('old').data, undefined);
});

test('source selector catalogue and FROZEN capability drive native accessible controls', () => {
  const controls = moduleLoader()('src/components/sharing/SharingControls.tsx');
  const html = renderToStaticMarkup(React.createElement(controls.DisclosureControls, { options, selected: [], onChange() {}, t: labels }));
  assert.equal((html.match(/checked=/g) ?? []).length, 0);
  assert.match(html, /required=""/); assert.match(html, /shareRequired/);
  const mode = renderToStaticMarkup(React.createElement(controls.ModeControls, { options: { ...options, frozen_capture_available: false }, mode: '', onChange() {}, t: labels }));
  assert.match(mode, /disabled=""/); assert.match(mode, /shareErrorFrozen/);
});

function editorFixture(share) {
  const runtime = hookRuntime(); const calls = []; const confirmations = [];
  let previewPayload;
  const api = {
    preview: async (_org, _box, selected) => { previewPayload = { ...snapshot, disclosed: selected }; return { snapshot: previewPayload }; },
    create: async (_org, input) => { calls.push({ method: 'create', input }); return { share: { id: 3 } }; },
    update: async (_org, id, input) => { calls.push({ method: 'update', id, input }); return { share: { ...share, policy_revision: 3 } }; },
  };
  const load = moduleLoader({ react: runtime.hooks, 'src/api/boxSharing.ts': { sharingApi: api },
    'src/api/sourceSharingSession.ts': { readForSourceAdmin: (_profile, _org, _signal, op) => op() },
    'src/hooks/useSharingConfirmation.ts': { useSharingConfirmation: () => ({ confirmAction: value => { const pending = deferred(); confirmations.push({ ...pending, value }); return pending.promise; }, confirmActionModal: null }) },
  });
  const Component = load('src/components/sharing/ShareEditor.tsx').default;
  const props = { profile, organizationId: 1, options, share, language: 'fr', t: labels, onSaved(value) { calls.push({ saved: value }); }, onCancel() {} };
  const render = () => { const tree = runtime.render(() => Component(props)); runtime.commit(); return tree; };
  return { runtime, render, calls, confirmations, props, api, settle: async () => { await tick(); render(); await tick(); return render(); } };
}
function changeChild(tree, name, value) { one(tree, node => typeof node.type === 'function' && node.type.name === name).props.onChange(value); }

test('creation needs matching server preview, explicit dates, chosen mode and intentional confirmation', async () => {
  const f = editorFixture(); let tree = f.render(); await f.settle();
  one(tree, node => typeof node.type === 'function' && node.type.name === 'SourceBoxLookup').props.onSelect({ id: 7, global_code: 'SYN-001', organization: { id: 1, name: 'Synthetic', slug: null } });
  tree = f.render(); changeChild(tree, 'DisclosureControls', snapshot.disclosed); changeChild(tree, 'ModeControls', 'live');
  one(tree, node => node.type === 'input' && node.props.type === 'email').props.onChange({ target: { value: 'recipient@example.test' } });
  const dates = elements(tree, node => typeof node.type === 'function' && node.type.name === 'ExpiryField');
  dates[0].props.onChange(new Date(Date.now() + 3600000).toISOString().slice(0, 16));
  dates[1].props.onChange(new Date(Date.now() + 7200000).toISOString().slice(0, 16));
  tree = f.render(); assert.equal(one(tree, node => node.type === 'button' && node.props.type === 'submit').props.disabled, true);
  await f.settle(); tree = f.render();
  one(tree, node => node.type === 'button' && node.props.children === 'sharePreview').props.onClick();
  tree = await f.settle(); assert.equal(one(tree, node => node.type === 'button' && node.props.type === 'submit').props.disabled, false);
  const pending = one(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  assert.equal(f.calls.length, 0); assert.equal(f.confirmations.length, 1);
  assert.ok(f.confirmations[0].value.details.some(item => item.value === 'recipient@example.test'));
  f.confirmations[0].resolve(true); await pending;
  assert.equal(f.calls[0].method, 'create'); assert.equal(f.calls[0].input.mode, 'live');
  assert.ok(f.calls[0].input.access_expires_at.endsWith('Z'));
  f.runtime.unmount();
});
test('FROZEN expansion sends revision and recapture flags only after explicit confirmation', async () => {
  const share = { id: 3, box: { id: 7, global_code: 'SYN-001' }, recipient_email: profile.email, mode: 'frozen', selectors: ['source.institution_name'], policy_revision: 2,
    invitation: { expires_at: new Date(Date.now() + 3600000).toISOString() }, access_expires_at: new Date(Date.now() + 7200000).toISOString() };
  const f = editorFixture(share); let tree = f.render(); await f.settle();
  changeChild(tree, 'DisclosureControls', snapshot.disclosed); tree = await f.settle();
  one(tree, node => node.type === 'button' && node.props.children === 'sharePreview').props.onClick(); tree = await f.settle();
  const saving = one(tree, node => node.type === 'form').props.onSubmit({ preventDefault() {} });
  assert.match(f.confirmations[0].value.message, /shareConfirmExpansion/); assert.match(f.confirmations[0].value.message, /shareConfirmRecapture/);
  assert.equal(f.calls.length, 0); f.confirmations[0].resolve(true); await saving;
  assert.equal(f.calls[0].input.expected_policy_revision, 2); assert.equal(f.calls[0].input.confirm_expansion, true); assert.equal(f.calls[0].input.confirm_recapture, true);
  assert.equal(Object.hasOwn(f.calls[0].input, 'access_expires_at'), false, 'do not silently extend access');
  f.runtime.unmount();
});
test('unconfigured policy disables creation instead of inventing lifetimes', async () => {
  const f = editorFixture(); f.props.options = { ...options, lifetime_policy: { configured: false, invitation_max_lifetime_seconds: null, access_max_lifetime_seconds: null } };
  const tree = f.render(); assert.equal(one(tree, node => node.type === 'button' && node.props.type === 'submit').props.disabled, true);
  assert.equal(elements(tree, node => typeof node.type === 'function' && node.type.name === 'ExpiryField').length, 0);
  f.runtime.unmount();
});
test('routing mounts separate access contexts and keeps Administration desktop-only', () => {
  assert.match(read('src/AppEntry.tsx'), /isSharedPath\(path\) \? <SharedAccessApp[^\n]+: <App \/>/);
  assert.match(read('src/App.tsx'), /activeTab === 'admin' && !isDesktopApp/);
  assert.match(read('src/AppEntry.tsx'), /path\.startsWith\('\/administration'\) && !isDesktopApp/);
  assert.match(read('src/AppEntry.tsx'), /if \(blockedAdministration\) return null/);
  const source = read('src/components/sharing/SharedAccessApp.tsx');
  assert.doesNotMatch(source, /api\/boxes|api\/measurements|api\/exports|BoxPage|onOpenBox|localStorage|sessionStorage/);
  assert.match(source, /sharing-readonly-banner/);
  assert.match(read('src/components/AdminView.tsx'), /key: 'transfers'[^\n]+label: 'adminTabTransfers'/);
  assert.doesNotMatch(read('src/components/AdminView.tsx'), /<TransfersAdminSection/);
  assert.match(read('src/styles/pages/box-sharing.css'), /\.sharing-readonly-banner\s*\{[^}]*position: sticky;[^}]*top: 0;/);
});
