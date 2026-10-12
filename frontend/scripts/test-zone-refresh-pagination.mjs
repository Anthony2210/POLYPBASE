import assert from 'node:assert/strict';
import test from 'node:test';
import { appHarness, functionNode, tick } from './app-operation-test-harness.mjs';

const zonePath = '/api/thermal-zones/?limit=80';
const nextPath = '/api/thermal-zones/?limit=80&offset=80';
const zones = Array.from({ length: 81 }, (_, index) => ({
  id: index + 1, name: `Zone ${index + 1}`, is_active: index !== 80,
  salinity_psu: '0', probes: index === 80 ? [{ id: 9, code: 'Late probe' }] : [],
}));
const detail = { id: 7, thermal_zone: { id: 81 }, status: 'active' };
const conflict = Object.assign(new Error('Location changed'), { data: { code: 'box_location_changed' } });
const operations = [
  { name: 'move conflict', run: h => h.context.moveBox(7, { thermal_zone_id: 81 }), conflict: true },
  { name: 'initial location', run: h => h.context.assignBoxInitialLocation(7, { thermal_zone_id: 81 }), box: true },
  { name: 'create zone', run: h => h.context.createThermalZone({ name: 'New zone' }) },
  { name: 'update zone', run: h => h.context.updateThermalZone(81, { name: 'Updated zone' }) },
  { name: 'create probe', run: h => h.context.createProbe({ thermal_zone: 81, code: 'Late probe' }) },
  { name: 'successful move', run: h => h.context.moveBox(7, { thermal_zone_id: 81 }), box: true },
];

async function startRefresh(operation, { cancelled = false } = {}) {
  const h = appHarness();
  const baseline = zones.map(zone => ({ ...zone, name: `Old ${zone.name}`, probes: [] }));
  h.state.data.zones = baseline;
  const pending = operation.run(h);
  // Attach the conflict expectation before rejecting its mutation.
  const completion = cancelled ? assert.rejects(pending, h.context.ApiResourceCancelledError)
    : operation.conflict ? assert.rejects(pending, error => error === conflict) : pending;
  if (operation.conflict) h.requests[0].reject(conflict);
  else h.requests[0].resolve(detail);
  await tick();
  if (operation.conflict) h.requests.find(request => request.url === '/api/boxes/7/').resolve(detail);
  const firstPage = h.requests.find(request => request.url === zonePath);
  assert.ok(firstPage, `${operation.name} starts a zone refresh`);
  assert.equal(firstPage.method, 'apiGet');
  return { h, baseline, completion, firstPage };
}

for (const operation of operations) {
  test(`${operation.name} exhausts zone pages and publishes only the complete collection`, async () => {
    const { h, baseline, completion, firstPage } = await startRefresh(operation);
    firstPage.resolve({ results: zones.slice(0, 80), next: `https://polypbase.test${nextPath}` });
    await tick();
    const laterPage = h.requests.find(request => request.url === nextPath);
    assert.ok(laterPage, 'must follow response.next beyond the first 80 zones');
    assert.equal(h.state.data.zones, baseline, 'keep the existing complete collection while the later page is pending');
    if (operation.box) assert.equal(h.state.data.boxDetails[7], detail, 'successful mutation detail is already authoritative');
    if (operation.conflict) assert.equal(h.state.data.boxDetails[7], undefined, 'conflict detail and zones publish together');
    // Overlap checks the existing helper's first-occurrence ID deduplication too.
    laterPage.resolve({ results: [zones[79], zones[80]], next: null });
    await completion;
    assert.deepEqual(Array.from(h.state.data.zones, zone => zone.id), zones.map(zone => zone.id));
    assert.equal(h.state.data.zones[80], zones[80], 'inactive later-page zone and nested probe are retained');
    h.evaluate(functionNode('getCurrentThermalZone').getText());
    const currentZone = h.context.getCurrentThermalZone(detail, h.state.data.zones);
    assert.equal(currentZone, zones[80], 'Box zone lookup can consume the later-page zone');
    assert.equal(currentZone.salinity_psu, '0', 'scientific zero remains available');
    assert.equal(h.state.recovery, null);
  });

  test(`${operation.name} keeps complete zones on later-page failure without repeating the mutation`, async () => {
    const { h, baseline, completion, firstPage } = await startRefresh(operation);
    firstPage.resolve({ results: zones.slice(0, 80), next: nextPath });
    await tick();
    h.requests.at(-1).reject(new Error('Later zone page unavailable'));
    await completion;
    assert.equal(h.state.data.zones, baseline);
    if (operation.box) assert.equal(h.state.data.boxDetails[7], detail);
    if (operation.conflict) {
      assert.equal(h.state.data.boxDetails[7], undefined);
      assert.equal(h.state.recovery, null, 'the original movement conflict is rethrown, not turned into a successful mutation');
    } else {
      assert.ok(h.state.recovery, 'offer the existing read-only refresh recovery');
      const requestCount = h.requests.length;
      const recovery = h.state.recovery();
      assert.equal(h.requests.at(-1).url, zonePath, 'retry the full collection, not just the failed page');
      h.requests.at(-1).resolve({ results: zones.slice(0, 80), next: nextPath });
      await tick();
      assert.equal(h.state.data.zones, baseline);
      h.requests.at(-1).resolve({ results: zones.slice(80), next: null });
      await recovery;
      assert.equal(h.state.recovery, null);
      assert.equal(h.state.data.zones.length, 81);
      assert.equal(h.state.data.zones[80], zones[80]);
      assert.ok(h.requests.slice(requestCount).every(request => request.method === 'apiGet'));
    }
    assert.equal(h.requests.filter(request => request.method !== 'apiGet').length, 1);
  });

  test(`${operation.name} stops obsolete pagination and cannot publish into the replacement Organization`, async () => {
    const { h, completion, firstPage } = await startRefresh(operation, { cancelled: true });
    firstPage.resolve({ results: zones.slice(0, 80), next: nextPath });
    await tick();
    const laterPage = h.requests.at(-1);
    const requestCount = h.requests.length;
    assert.ok(h.requests.every(request => request.generation === 0));
    h.switchOrganization();
    const replacement = { boxes: [], boxDetails: {}, zones: [{ id: 201 }] };
    h.state.data = replacement;
    laterPage.resolve({ results: zones.slice(80), next: '/api/thermal-zones/?limit=80&offset=160' });
    await completion;
    assert.equal(h.requests.length, requestCount, 'do not fetch a third page in the new Organization');
    assert.equal(h.state.data, replacement);
    assert.equal(h.state.recovery, null, 'do not offer recovery for an obsolete Organization');
  });
}

for (const operation of operations.filter(operation => !operation.conflict)) {
  test(`${operation.name} preserves the original mutation failure and does not refresh`, async () => {
    const h = appHarness();
    const failure = new Error('Mutation rejected');
    const pending = operation.run(h);
    const rejected = assert.rejects(pending, error => error === failure);
    h.requests[0].reject(failure);
    await rejected;
    assert.equal(h.requests.length, 1);
    assert.equal(h.state.recovery, null);
    assert.equal(h.state.data.zones.length, 0);
  });
}

for (const [name, results] of [['create zone', [zones[0]]], ['initial location', []]]) {
  test(`${name} still publishes a complete ${results.length ? 'single-page' : 'empty'} response`, async () => {
    const operation = operations.find(operation => operation.name === name);
    const { h, completion, firstPage } = await startRefresh(operation);
    firstPage.resolve({ results, next: null });
    await completion;
    assert.deepEqual(Array.from(h.state.data.zones), results);
    assert.equal(h.requests.length, 2);
    assert.equal(h.state.recovery, null);
  });
}

for (const stage of ['before recovery', 'during recovery']) {
  test(`create probe cannot recover stale zones after Organization switch ${stage}`, async () => {
    const operation = operations.find(operation => operation.name === 'create probe');
    const { h, completion, firstPage } = await startRefresh(operation);
    firstPage.resolve({ results: zones.slice(0, 80), next: nextPath });
    await tick();
    h.requests.at(-1).reject(new Error('Later page failed'));
    await completion;
    const recovery = h.state.recovery;
    assert.ok(recovery);
    let rejected;
    if (stage === 'during recovery') {
      rejected = assert.rejects(recovery(), h.context.ApiResourceCancelledError);
      h.requests.at(-1).resolve({ results: zones.slice(0, 80), next: nextPath });
      await tick();
    }
    h.switchOrganization();
    const replacement = { boxes: [], boxDetails: {}, zones: [{ id: 201 }] };
    h.state.data = replacement;
    h.state.recovery = null;
    const requestCount = h.requests.length;
    if (stage === 'before recovery') rejected = assert.rejects(recovery(), h.context.ApiResourceCancelledError);
    else h.requests.at(-1).reject(new Error('Old Organization page failed'));
    await rejected;
    assert.equal(h.requests.length, requestCount);
    assert.equal(h.state.data, replacement);
    assert.equal(h.state.recovery, null);
  });
}

test('update zone guards queued publication after all pages have arrived', async () => {
  const operation = operations.find(operation => operation.name === 'update zone');
  const { h, completion, firstPage } = await startRefresh(operation);
  h.queueUpdates();
  firstPage.resolve({ results: zones.slice(0, 80), next: nextPath });
  await tick();
  h.requests.at(-1).resolve({ results: zones.slice(80), next: null });
  await completion;
  assert.equal(h.queuedUpdates.length, 1);
  h.switchOrganization();
  const replacement = { boxes: [], boxDetails: {}, zones: [{ id: 201 }] };
  assert.equal(h.queuedUpdates[0](replacement), replacement);
});

test('create zone rejects an unsafe next link through read-only recovery, never publishing a partial collection', async () => {
  const operation = operations.find(operation => operation.name === 'create zone');
  const { h, baseline, completion, firstPage } = await startRefresh(operation);
  firstPage.resolve({ results: zones.slice(0, 80), next: 'https://foreign.test/api/thermal-zones/?offset=80' });
  await completion;
  assert.equal(h.requests.length, 2);
  assert.equal(h.state.data.zones, baseline);
  const recovery = h.state.recovery();
  const rejected = assert.rejects(recovery, /Invalid pagination link/);
  h.requests.at(-1).resolve({ results: zones.slice(0, 80), next: '/api/boxes/?offset=80' });
  await rejected;
  assert.ok(h.state.recovery, 'keep GET-only recovery available when retry fails');
  assert.equal(h.state.data.zones, baseline);
  assert.equal(h.requests.filter(request => request.method !== 'apiGet').length, 1);
});

test('Organization bootstrap still waits for every zone page before returning scoped data', async () => {
  const h = appHarness();
  let organizationContext;
  h.context.setActiveOrganizationContext = id => { organizationContext = id; };
  for (const name of ['getSelectableOrganizations', 'getOrganizationById', 'setProfileActiveOrganization', 'fetchScopedData']) {
    h.evaluate(functionNode(name).getText());
  }
  const organization = { id: 1, name: 'Organization 1' };
  const profile = { organizations: [organization], memberships: [], active_organization: organization };
  let finished = false;
  const pending = h.context.fetchScopedData(profile, 1).then(data => { finished = true; return data; });
  assert.equal(organizationContext, 1);
  h.requests.find(request => request.url === '/api/dashboard/').resolve({ recent_accesses: [] });
  h.requests.find(request => request.url === zonePath).resolve({ results: zones.slice(0, 80), next: nextPath });
  await tick();
  assert.equal(finished, false);
  assert.equal(h.state.data.zones.length, 0);
  h.requests.at(-1).resolve({ results: zones.slice(80), next: null });
  const data = await pending;
  assert.equal(data.zones.length, 81);
  assert.equal(data.zones[80], zones[80]);
  assert.equal(data.profile.active_organization, organization);
  assert.ok(h.requests.every(request => request.method === 'apiGet' && !request.url.startsWith('/api/boxes/')));
});
