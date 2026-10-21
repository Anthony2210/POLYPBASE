import assert from 'node:assert/strict';
import test from 'node:test';
import { button, createRequestContextHarness, named, nodes, text } from './request-context-test-harness.mjs';

const t = (key) => key;
const inventoryBox = (id) => ({
  id, global_code: `BOX-${id}`, status: 'pending_review',
  species: { scientific_name: 'Aurelia aurita' }, thermal_zone: null, last_location: null,
  inventory_created_on: '2020-01-01',
  latest_measurement: { measured_on: '2020-01-01', polyp_count: 0, ephyrae_count: 0 },
});
const inventoryResponse = (ids, count = ids.length, extra = {}) => ({
  count, results: ids.map(inventoryBox), next: null, previous: null,
  summary: { pending_review_count: count, active_without_location_count: 0, pending_without_location_count: count },
  filter_options: { creation_years: [2020], reference_date: '2026-10-02' },
  selection: { eligible_count: count, max_batch_size: 500 }, ...extra,
});
function inventoryHarness() {
  const mutations = [];
  const props = {
    language: 'en', t, zones: [], onOpenBox() {}, onOpenZone() {},
    onBatchQualify: async (payload) => {
      mutations.push(payload);
      return { succeeded: payload.box_ids, failed: [] };
    },
    onAssignLocation: async (...args) => { mutations.push(args); },
    onDeactivate: async (...args) => { mutations.push(args); },
    onQualify: async (...args) => { mutations.push(args); },
    onReactivate: async (...args) => { mutations.push(args); },
  };
  const harness = createRequestContextHarness('BoxInventoryAdminSection', props);
  harness.flush();
  return { harness, mutations };
}
function changeInventoryFilter(harness, name, value) {
  const label = nodes(harness.tree, (node) => node.type === 'label' && text(node).includes(name))[0];
  const select = named(label, 'select')[0];
  assert.ok(select, `Missing filter ${name}`);
  select.props.onChange({ target: { value } });
}
const rows = (harness) => nodes(harness.tree, (node) => node.type === 'article');
function selectInventoryBox(harness, id) {
  const checkbox = nodes(harness.tree, (node) => node.type === 'input'
    && node.props['aria-label'] === `boxInventoryBatchSelectBox BOX-${id}`)[0];
  assert.ok(checkbox);
  assert.equal(checkbox.props.disabled, false);
  checkbox.props.onChange({ target: { checked: true } });
  harness.flush();
}
async function resolveInventory(harness, requestIndex, response) {
  harness.requests[requestIndex].resolve(response);
  await harness.settle();
}

const exportOptions = {
  organizations: [],
  species: [{ id: 1, scientific_name: 'Aurelia aurita', common_name: '' }],
  strains: [{ id: 1, code: 'AA', species_id: 1 }],
  zones: [{ id: 7, name: 'Zone 7' }, { id: 8, name: 'Zone 8' }],
  boxes: [{ id: 1, global_code: 'BOX-1', local_code: '', species_id: 1, strain_id: 1, thermal_zone_id: 7 }],
};
const eligible = (ids = [1]) => ({
  box_ids: ids, latest_measurement_on_by_box: { 1: '2020-01-01' }, measurement_count: ids.length,
});
function exportsHarness(options = exportOptions) {
  const harness = createRequestContextHarness('ExportsView', { options, language: 'en', isLoading: false });
  harness.flush();
  return harness;
}
const disclosure = (harness, title) => named(harness.tree, 'FilterDisclosure').find((node) => node.props.title === title);
function changeExportDate(harness, value) {
  const input = nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'date')[0];
  input.props.onChange({ target: { value } });
}
async function resolveEligibility(harness, requestIndex, data = eligible()) {
  harness.runTimers();
  harness.requests[requestIndex].resolve(data);
  await harness.settle();
}
function assertUnverifiedExports(harness) {
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, true);
  assert.equal(named(harness.tree, 'PreviewBoxPicker').length, 0);
  assert.equal(nodes(harness.tree, (node) => node.props?.className === 'export-review-count').length, 0);
  assert.equal(nodes(harness.tree, (node) => node.props?.className === 'export-no-result').length, 0);
  assert.equal(disclosure(harness, 'Boxes').props.disabled, true);
}

test('inventory A success -> filters B -> B failure removes old rows/actions and retries B', async () => {
  const { harness, mutations } = inventoryHarness();
  await resolveInventory(harness, 0, inventoryResponse([1]));
  assert.equal(rows(harness).length, 1);
  assert.match(text(harness.tree), /0 polyps/);
  button(harness.tree, 'boxInventoryEnterSelectionMode').props.onClick();
  harness.flush();
  selectInventoryBox(harness, 1);
  button(harness.tree, 'boxInventoryBatchMakeActive').props.onClick();
  harness.flush();
  assert.equal(named(harness.tree, 'BoxInventoryBatchModal').length, 1);

  changeInventoryFilter(harness, 'boxInventoryLocation', '7');
  harness.render(false);
  assert.equal(rows(harness).length, 0, 'A must disappear before the B effect');
  assert.equal(named(harness.tree, 'BoxInventoryBatchModal').length, 0);
  harness.flush();
  assert.match(text(harness.tree), /boxInventorySelectionClearedByFilters/);
  assert.match(harness.requests[1].url, /limit=24&offset=0.*location=7/);
  harness.requests[1].reject(new harness.ApiError('B failed'));
  await harness.settle();
  assert.equal(rows(harness).length, 0);
  assert.equal(named(harness.tree, 'BoxInventoryRowMenu').length, 0);
  assert.equal(named(harness.tree, 'BoxTrackingPreview').length, 0);
  assert.doesNotMatch(text(harness.tree), /boxInventoryEmptyTitle|boxInventoryBatchMakeActive|1 boxInventoryBoxes/);
  assert.match(text(harness.tree), /B failed/);
  assert.equal(mutations.length, 0);

  button(harness.tree, 'reloadAction').props.onClick();
  harness.flush();
  assert.equal(rows(harness).length, 0);
  assert.doesNotMatch(text(harness.tree), /B failed/);
  assert.equal(harness.requests[2].url, harness.requests[1].url);
  await resolveInventory(harness, 2, inventoryResponse([2]));
  assert.equal(rows(harness).length, 1);
  assert.match(rows(harness)[0].props['aria-label'], /BOX-2/);
  selectInventoryBox(harness, 2);
  button(harness.tree, 'boxInventoryBatchMakeActive').props.onClick();
  harness.flush();
  await named(harness.tree, 'BoxInventoryBatchModal')[0].props.onConfirm();
  assert.equal(mutations.length, 1);
  assert.deepEqual(Array.from(mutations[0].box_ids), [2]);
  assert.equal(mutations[0].target_status, 'active');
});

test('inventory pagination preserves cross-page selection but failed pages cannot run batch actions', async () => {
  const { harness, mutations } = inventoryHarness();
  await resolveInventory(harness, 0, inventoryResponse([1], 25, { next: '?offset=24' }));
  button(harness.tree, 'boxInventoryEnterSelectionMode').props.onClick();
  harness.flush();
  selectInventoryBox(harness, 1);
  button(harness.tree, 'adminNextPage').props.onClick();
  harness.render(false);
  assert.equal(rows(harness).length, 0);
  assert.equal(button(harness.tree, 'boxInventoryBatchMakeActive').props.disabled, true);
  harness.flush();
  assert.match(harness.requests[1].url, /offset=24/);
  harness.requests[1].reject(new harness.ApiError('Page failed'));
  await harness.settle();
  const action = button(harness.tree, 'boxInventoryBatchMakeActive');
  assert.equal(action.props.disabled, true);
  action.props.onClick();
  harness.flush();
  assert.equal(named(harness.tree, 'BoxInventoryBatchModal').length, 0);
  assert.match(text(harness.tree), /1 boxInventoryBatchSelectedOne/);
  assert.equal(mutations.length, 0);
  button(harness.tree, 'reloadAction').props.onClick();
  harness.flush();
  await resolveInventory(harness, 2, inventoryResponse([2], 25, { previous: '?offset=0' }));
  selectInventoryBox(harness, 2);
  button(harness.tree, 'boxInventoryBatchMakeInactive').props.onClick();
  harness.flush();
  const modal = named(harness.tree, 'BoxInventoryBatchModal')[0];
  assert.deepEqual(Array.from(modal.props.selectedBoxes, (box) => box.id), [1, 2]);
  await modal.props.onConfirm();
  assert.deepEqual(Array.from(mutations[0].box_ids), [1, 2]);
  assert.equal(mutations[0].reason_missing_from_history, true);
});

test('inventory urgent search hides A while useDeferredValue still contains A', async () => {
  const { harness } = inventoryHarness();
  await resolveInventory(harness, 0, inventoryResponse([1]));
  harness.holdDeferred(true);
  nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'search')[0]
    .props.onChange({ target: { value: 'B' } });
  harness.flush();
  assert.equal(rows(harness).length, 0);
  assert.equal(harness.requests.length, 1);
  harness.holdDeferred(false);
  harness.flush();
  assert.match(harness.requests[1].url, /q=B/);
  harness.requests[1].reject(new harness.ApiError('Search failed'));
  await harness.settle();
  assert.equal(rows(harness).length, 0);
  assert.match(text(harness.tree), /Search failed/);
});

test('inventory ignores superseded success, counter completion and failure', async () => {
  const { harness } = inventoryHarness();
  const withoutSummary = inventoryResponse([1]);
  delete withoutSummary.summary;
  harness.requests[0].resolve(withoutSummary);
  await harness.settle();
  assert.equal(harness.requests.length, 3, 'Legacy counter queries remain server-side');
  changeInventoryFilter(harness, 'boxInventoryStatus', 'pending_review');
  harness.flush();
  await resolveInventory(harness, 3, inventoryResponse([2]));
  harness.requests[1].resolve({ count: 99 });
  harness.requests[2].resolve({ count: 99 });
  await harness.settle();
  assert.match(rows(harness)[0].props['aria-label'], /BOX-2/);
  changeInventoryFilter(harness, 'boxInventoryLocation', '7');
  harness.flush();
  changeInventoryFilter(harness, 'boxInventoryLocation', '8');
  harness.flush();
  await resolveInventory(harness, 5, inventoryResponse([3]));
  harness.requests[4].reject(new harness.ApiError('Superseded failure'));
  await harness.settle();
  assert.match(rows(harness)[0].props['aria-label'], /BOX-3/);
  assert.doesNotMatch(text(harness.tree), /Superseded failure/);
});

test('inventory empty is shown only for successful current zero-count response', async () => {
  const { harness } = inventoryHarness();
  assert.doesNotMatch(text(harness.tree), /boxInventoryEmptyTitle/);
  await resolveInventory(harness, 0, inventoryResponse([]));
  assert.match(text(harness.tree), /boxInventoryEmptyTitle/);
  assert.match(text(harness.tree), /0 boxInventoryBoxes/);
  assert.equal(named(harness.tree, 'BoxInventoryRowMenu').length, 0);
});

test('inventory lifecycle dialog from A cannot submit after switching to B', async () => {
  const { harness, mutations } = inventoryHarness();
  await resolveInventory(harness, 0, inventoryResponse([1]));
  named(harness.tree, 'BoxInventoryRowMenu')[0].props.onAction('qualify');
  harness.flush();
  assert.equal(named(harness.tree, 'BoxLifecycleModal').length, 1);
  changeInventoryFilter(harness, 'boxInventoryStatus', 'active');
  harness.render(false);
  assert.equal(named(harness.tree, 'BoxLifecycleModal').length, 0);
  harness.flush();
  harness.requests[1].reject(new harness.ApiError('B failed'));
  await harness.settle();
  assert.equal(named(harness.tree, 'BoxLifecycleModal').length, 0);
  assert.equal(mutations.length, 0);
});

test('exports A success -> date filters B -> B failure cannot validate count/selection/download; retry recovers', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, false);
  assert.equal(named(harness.tree, 'PreviewBoxPicker')[0].props.options.length, 1);
  changeExportDate(harness, '2021-01-01');
  harness.render(false);
  assertUnverifiedExports(harness);
  harness.flush();
  harness.runTimers();
  assert.match(harness.requests[1].url, /date_from=2021-01-01/);
  harness.requests[1].reject(new Error('B eligibility failed'));
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.match(text(harness.tree), /B eligibility failed/);
  assert.doesNotMatch(text(harness.tree), /No box matches|stale|Fichier.*obsolète/i);
  button(harness.tree, 'Download CSV').props.onClick();
  assert.equal(harness.downloads.length, 0);
  button(harness.tree, 'Reload').props.onClick();
  harness.flush();
  assertUnverifiedExports(harness);
  assert.doesNotMatch(text(harness.tree), /B eligibility failed/);
  await resolveEligibility(harness, 2);
  assert.equal(harness.requests[2].url, harness.requests[1].url);
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, false);
  const download = button(harness.tree, 'Download CSV').props.onClick();
  assert.match(harness.downloads[0].url, /measurements.csv\?date_from=2021-01-01/);
  harness.downloads[0].resolve('measurements.csv');
  await download;
  await harness.settle();
  assert.match(text(harness.tree), /File downloaded : measurements.csv/);
});

test('exports zone and include-other-zones changes immediately invalidate eligibility and preserve backend query', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  disclosure(harness, 'Thermal zones').props.onToggle(7);
  harness.render(false);
  assertUnverifiedExports(harness);
  harness.flush();
  await resolveEligibility(harness, 1);
  assert.match(harness.requests[1].url, /zones=7/);
  disclosure(harness, 'Thermal zones').props.extraContent.props.children[0]
    .props.onChange({ target: { checked: true } });
  harness.render(false);
  assertUnverifiedExports(harness);
  harness.flush();
  harness.runTimers();
  assert.match(harness.requests[2].url, /zones=7&include_other_zones=true/);
  harness.requests[2].reject(new Error('Zone scope failed'));
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.match(text(harness.tree), /Zone scope failed/);
});

test('exports ignores late success and failure even if transport does not honor abort', async () => {
  const harness = exportsHarness();
  harness.runTimers();
  changeExportDate(harness, '2021-01-01');
  harness.flush();
  harness.runTimers();
  assert.equal(harness.requests[0].config.signal.aborted, true);
  harness.requests[1].reject(new Error('Current failure'));
  await harness.settle();
  harness.requests[0].resolve(eligible());
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.match(text(harness.tree), /Current failure/);
  button(harness.tree, 'Reload').props.onClick();
  harness.flush();
  harness.runTimers();
  changeExportDate(harness, '2022-01-01');
  harness.flush();
  await resolveEligibility(harness, 3);
  harness.requests[2].reject(new Error('Superseded failure'));
  await harness.settle();
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, false);
  assert.doesNotMatch(text(harness.tree), /Superseded failure/);
});

test('exports initial eligibility failure is retryable, not an endless page loader or empty result', async () => {
  const harness = exportsHarness();
  assertUnverifiedExports(harness);
  harness.runTimers();
  harness.requests[0].reject(new Error('Initial failure'));
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.equal(named(harness.tree, 'PageLoader').length, 0);
  button(harness.tree, 'Reload').props.onClick();
  harness.flush();
  await resolveEligibility(harness, 1, eligible([]));
  assert.match(text(harness.tree), /No box matches these filters/);
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, true);
});

test('exports new options identity requires fresh eligibility before counts or download', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  harness.setProps({ options: { ...exportOptions } });
  harness.render(false);
  assertUnverifiedExports(harness);
  harness.flush();
  await resolveEligibility(harness, 1);
  assert.equal(button(harness.tree, 'Download CSV').props.disabled, false);
});

test('exports local species/strain/box filtering still uses current eligibility and cumulative CSV filters', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  for (const title of ['Species', 'Strains', 'Boxes']) {
    disclosure(harness, title).props.onToggle(1);
    harness.flush();
  }
  assert.equal(harness.requests.length, 1, 'Local taxonomy filters do not change the server eligibility scope');
  const download = button(harness.tree, 'Download CSV').props.onClick();
  assert.match(harness.downloads[0].url, /species=1&strains=1&boxes=1/);
  changeExportDate(harness, '2021-01-01');
  harness.flush();
  harness.downloads[0].resolve('A.csv');
  await download;
  await harness.settle();
  assert.doesNotMatch(text(harness.tree), /File downloaded/);
  assertUnverifiedExports(harness);
});

test('exports late preview failure cannot render under failed B eligibility', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
  harness.flush();
  assert.match(harness.requests[1].url, /boxes\/1\/trend/);
  changeExportDate(harness, '2021-01-01');
  harness.flush();
  assert.equal(harness.requests[1].config.signal.aborted, true);
  harness.runTimers();
  harness.requests[2].reject(new Error('B failed'));
  await harness.settle();
  harness.requests[1].reject(new Error('Old preview failure'));
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.doesNotMatch(text(harness.tree), /Old preview failure/);
  assert.equal(named(harness.tree, 'BiologicalTrendChart').length, 0);
});

test('exports cached A chart is hidden immediately and stays hidden after B eligibility fails', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
  harness.flush();
  harness.requests[1].resolve({
    id: 1, locations: [], movements: [],
    biological_measurements: [{ id: 1, measured_on: '2020-01-01', polyp_count: 0, ephyrae_count: 0 }],
  });
  await harness.settle();
  assert.equal(named(harness.tree, 'BiologicalTrendChart').length, 1);
  assert.equal(named(harness.tree, 'BiologicalTrendChart')[0].props.measurements[0].polypCount, 0);
  disclosure(harness, 'Thermal zones').props.onToggle(7);
  harness.render(false);
  assert.equal(named(harness.tree, 'BiologicalTrendChart').length, 0);
  harness.flush();
  harness.runTimers();
  harness.requests.findLast((request) => request.url.includes('/eligible-boxes/')).reject(new Error('B failed'));
  await harness.settle();
  assertUnverifiedExports(harness);
  assert.equal(named(harness.tree, 'BiologicalTrendChart').length, 0);
});

test('exports preview failure retries the same box and filters without reloading eligibility and preserves zeros', async () => {
  const harness = exportsHarness();
  changeExportDate(harness, '2019-01-01');
  disclosure(harness, 'Thermal zones').props.onToggle(7);
  harness.flush();
  await resolveEligibility(harness, 0);
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
  harness.flush();
  const firstPreviewRequest = harness.requests[1];
  assert.match(firstPreviewRequest.url, /boxes\/1\/trend/);
  assert.match(firstPreviewRequest.url, /date_from=2019-01-01/);
  assert.match(firstPreviewRequest.url, /zones=7/);
  firstPreviewRequest.reject(new Error('Preview failed'));
  await harness.settle();
  assert.match(text(harness.tree), /Preview failed/);
  assert.equal(nodes(harness.tree, (node) => node.props?.['aria-busy'] === 'true').length, 0);

  button(harness.tree, 'Reload').props.onClick();
  harness.render(false);
  assert.doesNotMatch(text(harness.tree), /Preview failed/);
  assert.match(text(harness.tree), /Loading measurements/);
  assert.equal(nodes(harness.tree, (node) => node.props?.className === 'export-chart-state is-error').length, 0);
  assert.equal(named(harness.tree, 'PreviewBoxPicker')[0].props.selectedId, 1);
  assert.equal(nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'date')[0].props.value, '2019-01-01');
  assert.deepEqual(Array.from(disclosure(harness, 'Thermal zones').props.selectedIds), [7]);
  harness.flush();
  assert.equal(harness.requests[2].url, firstPreviewRequest.url);
  assert.equal(harness.requests.filter((request) => request.url.includes('/eligible-boxes/')).length, 1);
  harness.requests[2].resolve({
    id: 1, locations: [], movements: [],
    biological_measurements: [{ id: 1, measured_on: '2020-01-01', polyp_count: 0, ephyrae_count: 0 }],
  });
  await harness.settle();
  const chart = named(harness.tree, 'BiologicalTrendChart')[0];
  assert.ok(chart);
  assert.equal(chart.props.measurements[0].polypCount, 0);
  assert.equal(chart.props.measurements[0].ephyraeCount, 0);
  assert.doesNotMatch(text(harness.tree), /Preview failed|Loading measurements/);
  assert.equal(named(harness.tree, 'PreviewBoxPicker')[0].props.selectedId, 1);
  harness.runTimers();
  assert.equal(harness.requests.filter((request) => request.url.includes('/trend/')).length, 2,
    'Retry must issue only one new trend request');
});

test('exports successful current empty eligibility shows no-boxes text in the preview in both languages', async () => {
  for (const [language, expected] of [
    ['en', 'No box matches these filters.'],
    ['fr', 'Aucune boîte ne correspond à ces filtres.'],
  ]) {
    const harness = createRequestContextHarness('ExportsView', { options: exportOptions, language, isLoading: false });
    harness.flush();
    assert.equal(nodes(harness.tree, (node) => node.props?.className === 'export-chart-state is-empty').length, 0);
    await resolveEligibility(harness, 0, eligible([]));
    const emptyState = nodes(harness.tree, (node) => node.props?.className === 'export-chart-state is-empty');
    assert.equal(emptyState.length, 1);
    assert.equal(text(emptyState[0]).trim(), expected);
    assert.equal(named(harness.tree, 'PreviewBoxPicker')[0].props.options.length, 0);
    assert.equal(named(harness.tree, 'BiologicalTrendChart').length, 0);
  }
});

test('exports forwards selected calendar bounds and complete movement timestamps to the chart', async () => {
  const harness = exportsHarness();
  const dates = nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'date');
  dates[0].props.onChange({ target: { value: '2026-10-02' } });
  dates[1].props.onChange({ target: { value: '2026-10-03' } });
  harness.flush();
  await resolveEligibility(harness, 0, { ...eligible(), latest_measurement_on_by_box: { 1: '2026-10-03' } });
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
  harness.flush();
  const timestamps = ['2026-10-03T12:00:00Z', '2026-10-04T00:59:59.999+03:00'];
  harness.requests[1].resolve({
    id: 1, locations: [],
    movements: timestamps.map((moved_at, index) => ({ id: index + 1, moved_at, from_thermal_zone: { name: 'A' }, to_thermal_zone: { name: 'B' } })),
    biological_measurements: [{ id: 1, measured_on: '2026-10-03', polyp_count: 0, ephyrae_count: 0 }],
  });
  await harness.settle();
  const chart = named(harness.tree, 'BiologicalTrendChart')[0];
  assert.ok(chart);
  assert.equal(chart.props.startDate, '2026-10-02');
  assert.equal(chart.props.endDate, '2026-10-03');
  assert.deepEqual(Array.from(chart.props.events, (event) => event.date), timestamps);
  assert.deepEqual(Array.from(chart.props.events, (event) => event.instant), timestamps);
  assert.deepEqual(Array.from(chart.props.events, (event) => event.id), ['movement-1', 'movement-2']);
});

const temperatureRequests = (harness) => harness.requests.filter((request) => /\/(?:historical-)?temperatures\//.test(request.url));
const dailyRequest = (harness) => harness.requests.findLast((request) => /\/temperatures\//.test(request.url));
const weeklyRequest = (harness) => harness.requests.findLast((request) => /\/historical-temperatures\//.test(request.url));
const chartProps = (harness) => named(harness.tree, 'BiologicalTrendChart')[0]?.props;
const temperaturePage = (results) => ({ count: results.length, results, next: null, previous: null });
const dailyPoint = (zone_id = 7, value = 0) => ({
  date: '2020-01-01', zone_id, zone_name: `Zone ${zone_id}`, average_temperature_c: value,
  min_temperature_c: value, max_temperature_c: value,
});
const weeklyPoint = (value = '0') => ({
  id: 91, box_id: 1, biological_measurement_id: 31, iso_year: 2020, iso_week: 1,
  interval_start: '2019-12-30', interval_end: '2020-01-05', temperature_c: value,
  temporal_precision: 'iso_week', association_status: 'confirmed',
});
async function selectExportPreview(harness, id = 1, measurements = []) {
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(id);
  harness.flush();
  const request = harness.requests.findLast((entry) => entry.url.includes(`/boxes/${id}/trend/`));
  assert.ok(request);
  request.resolve({ id, locations: [], movements: [], biological_measurements: measurements });
  await harness.settle();
}
function temperatureStatus(harness, name) {
  return named(chartProps(harness)?.temperatureStatus, name)[0]?.props;
}

for (const series of ['daily', 'weekly']) {
  test(`exports ${series} temperature-only preview renders real zero without changing eligibility or CSV`, async () => {
    const harness = exportsHarness();
    await resolveEligibility(harness, 0);
    await selectExportPreview(harness);
    assert.equal(chartProps(harness).measurements.length, 0);
    assert.equal(temperatureStatus(harness, 'TemperatureHistoryStatus').loading, true);
    assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').loading, true);
    dailyRequest(harness).resolve(temperaturePage(series === 'daily' ? [dailyPoint()] : []));
    weeklyRequest(harness).resolve(temperaturePage(series === 'weekly' ? [weeklyPoint()] : []));
    await harness.settle();
    const chart = chartProps(harness);
    assert.ok(chart);
    assert.equal(chart.measurements.length, 0);
    assert.equal(chart.temperatures.length, 1);
    assert.equal(chart.temperatures[0].average_temperature_c, 0);
    assert.equal(Boolean(chart.temperatures[0].week), series === 'weekly');
    assert.equal(chart.labels.temperature, 'Temperature');
    assert.equal(chart.labels.historicalTemperature, 'Temperature');
    assert.equal(chart.labels.historicalWeek, 'ISO week');
    assert.equal(chart.labels.historicalPeriod, 'Period');
    assert.equal(chart.labels.temperatureComparable, undefined);
    assert.equal(chart.labels.temperatureUncertain, undefined);
    assert.equal(temperatureStatus(harness, 'TemperatureHistoryStatus').loading, false);
    assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').loading, false);
    const review = nodes(harness.tree, (node) => node.props?.className === 'export-review-count')[0];
    assert.match(text(review), /1/);
    const download = button(harness.tree, 'Download CSV').props.onClick();
    assert.equal(harness.downloads[0].url, '/api/exports/measurements.csv');
    harness.downloads[0].resolve('measurements.csv');
    await download;
  });
}

test('exports temperature loading and failures never hide valid biological zero readings or the other series', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  await selectExportPreview(harness, 1, [
    { id: 31, measured_on: '2020-01-01', polyp_count: 0, ephyrae_count: 0 },
  ]);
  assert.equal(chartProps(harness).measurements[0].polypCount, 0);
  dailyRequest(harness).reject(new Error('Daily failed'));
  await harness.settle();
  assert.equal(temperatureStatus(harness, 'TemperatureHistoryStatus').failed, true);
  assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').loading, true);
  assert.equal(chartProps(harness).measurements[0].ephyraeCount, 0);
  weeklyRequest(harness).resolve(temperaturePage([weeklyPoint('18.5')]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures[0].average_temperature_c, 18.5);
  temperatureStatus(harness, 'TemperatureHistoryStatus').retry();
  harness.flush();
  dailyRequest(harness).resolve(temperaturePage([dailyPoint(7, 19)]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures.length, 2);
  assert.equal(temperatureStatus(harness, 'TemperatureHistoryStatus').failed, false);
  assert.equal(chartProps(harness).measurements[0].polypCount, 0);
});

test('exports historical failure and retry leave daily temperatures and biological readings intact', async () => {
  const harness = exportsHarness();
  await resolveEligibility(harness, 0);
  await selectExportPreview(harness, 1, [
    { id: 31, measured_on: '2020-01-01', polyp_count: 0, ephyrae_count: 0 },
  ]);
  dailyRequest(harness).resolve(temperaturePage([dailyPoint(7, 17)]));
  weeklyRequest(harness).reject(new Error('Weekly failed'));
  await harness.settle();
  assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').failed, true);
  assert.equal(chartProps(harness).temperatures[0].average_temperature_c, 17);
  assert.equal(chartProps(harness).measurements[0].polypCount, 0);
  const dailyCount = harness.requests.filter((request) => /\/temperatures\//.test(request.url)).length;
  temperatureStatus(harness, 'HistoricalTemperatureStatus').retry();
  harness.flush();
  assert.equal(chartProps(harness).temperatures[0].average_temperature_c, 17);
  weeklyRequest(harness).resolve(temperaturePage([weeklyPoint('0')]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures.length, 2);
  assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').failed, false);
  assert.equal(harness.requests.filter((request) => /\/temperatures\//.test(request.url)).length, dailyCount);
});

test('exports institution abort before eligibility debounce cannot start an obsolete request', async () => {
  const harness = exportsHarness();
  harness.switchOrganization();
  harness.runTimers();
  assert.equal(harness.requests.length, 0);
  harness.flush();
  await resolveEligibility(harness, 0);
  assert.equal(temperatureRequests(harness).length, 0);
});

test('exports requests both temperature series only within the selected preview calendar window', async () => {
  const harness = exportsHarness();
  const dates = nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'date');
  dates[0].props.onChange({ target: { value: '2020-01-01' } });
  dates[1].props.onChange({ target: { value: '2020-01-02' } });
  harness.flush();
  await resolveEligibility(harness, 0);
  assert.equal(temperatureRequests(harness).length, 0);
  await selectExportPreview(harness);
  for (const request of temperatureRequests(harness)) {
    const query = new URL(request.url, 'https://local.test').searchParams;
    assert.equal(query.get('start'), '2020-01-01');
    assert.equal(query.get('end'), '2020-01-02');
    assert.equal(request.config.cache, 'no-store');
  }
  assert.equal(chartProps(harness).startDate, '2020-01-01');
  assert.equal(chartProps(harness).endDate, '2020-01-02');
});

test('exports restricted zones filter daily points and do not fetch or fabricate historical week zones', async () => {
  const harness = exportsHarness();
  disclosure(harness, 'Thermal zones').props.onToggle(7);
  harness.flush();
  await resolveEligibility(harness, 0);
  await selectExportPreview(harness);
  assert.equal(weeklyRequest(harness), undefined);
  dailyRequest(harness).resolve(temperaturePage([dailyPoint(7, 0), dailyPoint(8, 25)]));
  await harness.settle();
  assert.deepEqual(Array.from(chartProps(harness).temperatures, (point) => point.zone_id), [7]);
  assert.equal(chartProps(harness).temperatures[0].average_temperature_c, 0);
  assert.equal(temperatureStatus(harness, 'HistoricalTemperatureStatus').loading, false);
  disclosure(harness, 'Thermal zones').props.extraContent.props.children[0]
    .props.onChange({ target: { checked: true } });
  harness.render(false);
  assert.equal(chartProps(harness), undefined);
  harness.flush();
  await resolveEligibility(harness, harness.requests.length);
  await selectExportPreview(harness);
  assert.ok(weeklyRequest(harness));
  dailyRequest(harness).resolve(temperaturePage([dailyPoint(7, 0), dailyPoint(8, 25)]));
  weeklyRequest(harness).resolve(temperaturePage([weeklyPoint('20')]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures.length, 3);
  assert.equal(chartProps(harness).temperatures[2].zone_id, undefined);
  assert.ok(chartProps(harness).temperatures[2].week);
});

test('exports never starts temperatures without a current eligible successful preview', async () => {
  const harness = exportsHarness();
  assert.equal(temperatureRequests(harness).length, 0);
  await resolveEligibility(harness, 0);
  assert.equal(temperatureRequests(harness).length, 0);
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
  harness.flush();
  assert.equal(temperatureRequests(harness).length, 0, 'Wait for successful current preview');
  harness.requests[1].reject(new Error('Preview failed'));
  await harness.settle();
  assert.equal(temperatureRequests(harness).length, 0);
  changeExportDate(harness, '2021-01-01');
  harness.flush();
  await resolveEligibility(harness, 2, eligible([]));
  assert.equal(temperatureRequests(harness).length, 0);
  assert.equal(chartProps(harness), undefined);
});

for (const gate of ['date', 'zone', 'species', 'strain', 'box', 'invalid-period', 'loading', 'options', 'institution']) {
  test(`exports ${gate} change hides temperatures immediately and ignores obsolete completions`, async () => {
    const harness = exportsHarness();
    await resolveEligibility(harness, 0);
    await selectExportPreview(harness);
    const oldDaily = dailyRequest(harness);
    const oldWeekly = weeklyRequest(harness);
    const before = temperatureRequests(harness).length;
    if (gate === 'date') changeExportDate(harness, '2021-01-01');
    if (gate === 'zone') disclosure(harness, 'Thermal zones').props.onToggle(7);
    if (['species', 'strain', 'box'].includes(gate)) {
      const title = { species: 'Species', strain: 'Strains', box: 'Boxes' }[gate];
      disclosure(harness, title).props.onToggle(999);
    }
    if (gate === 'invalid-period') {
      const dates = nodes(harness.tree, (node) => node.type === 'input' && node.props.type === 'date');
      dates[0].props.onChange({ target: { value: '2021-01-01' } });
      dates[1].props.onChange({ target: { value: '2020-01-01' } });
    }
    if (gate === 'loading') harness.setProps({ isLoading: true });
    if (gate === 'options') harness.setProps({ options: { ...exportOptions } });
    if (gate === 'institution') harness.switchOrganization();
    harness.render(false);
    assert.equal(chartProps(harness), undefined, 'Do not wait for effect cleanup to hide old context');
    harness.flush();
    assert.equal(oldDaily.config.signal.aborted, true);
    assert.equal(oldWeekly.config.signal.aborted, true);
    oldDaily.resolve(temperaturePage([dailyPoint(7, 99)]));
    oldWeekly.reject(new Error('Obsolete week failure'));
    await harness.settle();
    assert.equal(chartProps(harness), undefined);
    assert.equal(temperatureRequests(harness).length, before, 'No fetch in unverified or excluded context');
    assert.doesNotMatch(text(harness.tree), /Obsolete week failure/);
  });
}

test('exports switching boxes discards pending temperatures before effects and cannot associate late readings', async () => {
  const options = { ...exportOptions, boxes: [exportOptions.boxes[0], { ...exportOptions.boxes[0], id: 2, global_code: 'BOX-2' }] };
  const harness = exportsHarness(options);
  await resolveEligibility(harness, 0, { ...eligible([1, 2]), latest_measurement_on_by_box: { 1: '2020-01-01', 2: '2020-01-01' } });
  await selectExportPreview(harness);
  const oldDaily = dailyRequest(harness);
  const oldWeekly = weeklyRequest(harness);
  named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(2);
  harness.render(false);
  assert.equal(chartProps(harness), undefined);
  harness.flush();
  harness.requests.findLast((request) => request.url.includes('/boxes/2/trend/')).resolve({
    id: 2, locations: [], movements: [], biological_measurements: [],
  });
  await harness.settle();
  assert.equal(chartProps(harness).selectionScope, 2);
  assert.equal(chartProps(harness).temperatures.length, 0);
  assert.equal(oldDaily.config.signal.aborted, true);
  assert.equal(oldWeekly.config.signal.aborted, true);
  oldDaily.resolve(temperaturePage([dailyPoint(7, 99)]));
  oldWeekly.resolve(temperaturePage([weeklyPoint('99')]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures.length, 0);
  dailyRequest(harness).resolve(temperaturePage([dailyPoint(8, 12)]));
  weeklyRequest(harness).resolve(temperaturePage([]));
  await harness.settle();
  assert.equal(chartProps(harness).temperatures[0].average_temperature_c, 12);
});

for (const gate of ['options', 'institution']) {
  test(`exports ${gate} change cannot reuse cached biological preview or temperatures for the same box`, async () => {
    const harness = exportsHarness();
    await resolveEligibility(harness, 0);
    await selectExportPreview(harness);
    dailyRequest(harness).resolve(temperaturePage([dailyPoint(7, 99)]));
    weeklyRequest(harness).resolve(temperaturePage([weeklyPoint('99')]));
    await harness.settle();
    assert.equal(chartProps(harness).temperatures.length, 2);
    if (gate === 'options') harness.setProps({ options: { ...exportOptions } });
    else harness.switchOrganization();
    harness.flush();
    await resolveEligibility(harness, harness.requests.length);
    named(harness.tree, 'PreviewBoxPicker')[0].props.onSelect(1);
    harness.flush();
    assert.equal(chartProps(harness), undefined, 'Same ids require a new preview in the new context');
    const request = harness.requests.findLast((entry) => entry.url.includes('/trend/'));
    request.resolve({ id: 1, locations: [], movements: [], biological_measurements: [] });
    await harness.settle();
    assert.equal(chartProps(harness).temperatures.length, 0);
    assert.equal(temperatureStatus(harness, 'TemperatureHistoryStatus').loading, true);
  });
}
