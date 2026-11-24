import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { moduleLoader, hookRuntime, read, elements } from './sharing-test-harness.mjs';

const labels = key => key;

const descriptor = name => ({ name, group: name.split('.')[0], mandatory: name === 'source.institution_name', requires: name.startsWith('observation.') && name !== 'observation.time' ? ['observation.time'] : [] });
const options = { schema: 'polypbase.scientific-snapshot', schema_version: '1.0', frozen_capture_available: true, modes: ['live', 'frozen'],
  mandatory_selectors: ['source.institution_name'], selectors: ['source.institution_name', 'source.box_code', 'observation.time', 'observation.polyp_count', 'observation.ephyrae_count'].map(descriptor),
  lifetime_policy: { configured: true, invitation_max_lifetime_seconds: 86400, access_max_lifetime_seconds: 604800 } };
const disclosed = ['source.institution_name', 'observation.time', 'observation.polyp_count'];

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

const utils = moduleLoader()('src/utils/boxSharing.ts');
test('disclosure has no defaults, mandatory fields and server dependencies must be selected explicitly', () => {
  assert.equal(utils.validSelection(options, []), false);
  assert.equal(utils.validSelection(options, ['source.box_code']), false);
  assert.equal(utils.validSelection(options, ['source.institution_name', 'observation.polyp_count']), false);
  assert.equal(utils.validSelection(options, disclosed), true);
  assert.equal(utils.validSelection(options, ['source.institution_name', 'unknown.field']), false);
  assert.equal(utils.selectorIsLocked(descriptor('observation.polyp_count'), options.selectors, []), true);
  assert.equal(utils.selectorIsLocked(descriptor('observation.time'), options.selectors, disclosed), true);
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
test('retired personal routes and non-desktop Administration redirect before rendering', () => {
  for (const [path, desktop] of [
    ['/shared-boxes', true], ['/shared-boxes/accept', true],
    ['/shared-boxes/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee', true],
    ['/administration/transfers', false],
  ]) {
    const runtime = hookRuntime();
    const replacements = [];
    const location = { pathname: path };
    const workspace = () => null;
    const load = moduleLoader({
      react: { ...React, ...runtime.hooks, lazy: () => workspace },
      'src/hooks/useIsDesktopApp.ts': { useIsDesktopApp: () => desktop },
      'src/i18n/index.ts': { createTranslator: () => labels, getStoredInterfaceLanguage: () => 'fr' },
      'src/components/PageLoader.tsx': { default: () => null },
    }, {
      window: {
        location,
        history: { replaceState: (_state, _title, url) => { replacements.push(url); location.pathname = url; } },
        addEventListener() {}, removeEventListener() {},
      },
    });
    const AppEntry = load('src/AppEntry.tsx').default;
    assert.equal(runtime.render(() => AppEntry()), null, `${path}: no intermediate UI`);
    runtime.commit();
    assert.deepEqual(replacements, ['/']);
    assert.equal(elements(runtime.render(() => AppEntry()), node => node.type === workspace).length, 1);
    runtime.unmount();
  }
});

test('routing retires the personal portal and keeps Administration desktop-only', () => {
  const entry = read('src/AppEntry.tsx');
  assert.match(entry, /const retiredSharing = isSharedPath\(path\)/);
  assert.doesNotMatch(entry, /SharedAccessApp/);
  assert.match(entry, /if \(!blockedAdministration && !retiredSharing\) return/);
  assert.match(entry, /window\.history\.replaceState\(null, '', '\/'\)/);
  assert.match(read('src/App.tsx'), /activeTab === 'admin' && !isDesktopApp/);
  assert.match(read('src/AppEntry.tsx'), /path\.startsWith\('\/administration'\) && !isDesktopApp/);
  assert.match(entry, /if \(blockedAdministration \|\| retiredSharing\) return null/);
  assert.doesNotMatch(read('src/components/ProfileView.tsx'), /navigateShared|shared-boxes|shareReceived|shareAreas/);
  const admin = read('src/components/AdminView.tsx');
  assert.equal((admin.match(/key: 'transfers'[^\n]+label: 'adminTabTransfers'/g) ?? []).length, 1);
  assert.match(admin, /<InstitutionalExchangesPage/);
  assert.doesNotMatch(admin, /BoxSharingAdminSection/);
  assert.doesNotMatch(read('src/components/AdminView.tsx'), /<TransfersAdminSection/);
});
