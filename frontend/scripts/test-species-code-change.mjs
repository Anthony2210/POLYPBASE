import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

import { createBoxEnvironment } from './create-box-harness.mjs';

// Read-only Species code-change preview (Administration > Species and strains).
// The real components run in the hook harness; only transport and the rich Box
// preview leaf are replaced.

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(read('../src/utils/speciesCodeChange.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const helpers = {};
vm.runInNewContext(outputText, { exports: helpers, encodeURIComponent });

const previewSource = read('../src/components/SpeciesCodeChangePreview.tsx');
const utilSource = read('../src/utils/speciesCodeChange.ts');
const section = read('../src/components/SpeciesStrainsAdminSection.tsx');
const adminView = read('../src/components/AdminView.tsx');
const app = read('../src/App.tsx');
const frSource = read('../src/i18n/fr.ts');
const enSource = read('../src/i18n/en.ts');
const plain = (value) => JSON.parse(JSON.stringify(value));

const DETAIL_PATH = '/api/admin/institution-species/1/';
const previewPath = (code, id = 1) => `/api/admin/institution-species/${id}/code-change-preview/?proposed_code=${code}`;
const NESTED = new Set(['SpeciesDetail', 'SpeciesStrainsPanel', 'StrainItem', 'SpeciesCodeChangePreview', 'SpeciesCodeChangeImpact',
  'ProposedIdentifier', 'IssueList']);

const profile = {
  id: 1, species_id: 101, scientific_name: 'Cassiopea andromeda', worms_aphia_id: null, worms_url: null,
  species_code: 'CAN', revision: 3, strain_count: 2,
};
const detailStrain = (id, code, extra = {}) => ({
  id, code, name: '', strain_number: null, active_box_count: 0, is_box_usable: true, is_owned: true,
  recorded_provenance_name: null, ...extra,
});
const detail = {
  ...profile,
  strains: [
    detailStrain(10, 'CAN-FGU-2', { strain_number: 2, recorded_provenance_name: 'Guyane française' }),
    detailStrain(11, 'CAN-BRE-1', { strain_number: 1 }),
  ],
};

const planStrain = (id, current, proposed, extra = {}) => ({
  id, current_code: current, proposed_code: proposed, ownership: 'institution_owned', action: 'rename',
  reasons: [], box_count: 0, ...extra,
});
const planBox = (id, strainId, current, proposed, extra = {}) => ({
  id, strain_id: strainId, status: 'active', current_local_id: current, proposed_local_id: proposed,
  previous_local_id: null, global_id: `00000000-0000-4000-8000-${String(id).padStart(12, '0')}`,
  is_merged_alias: false, action: 'rename', reasons: [], ...extra,
});
const dependencies = (labels = 0) => [
  { kind: 'audit_entries_by_local_id', effect: 'requires_alias', count: 7 },
  { kind: 'historical_alias_records', effect: 'keeps_old_identifier', count: 0 },
  { kind: 'boxes_with_previous_local_id', effect: 'requires_alias', count: 1 },
  { kind: 'qr_tags', effect: 'unaffected', count: 2 },
  { kind: 'physical_labels', effect: 'requires_review', count: labels },
  { kind: 'lineage_links', effect: 'unaffected', count: 0 },
  { kind: 'box_code_namespaces', effect: 'requires_carry_over', count: 2 },
];

function plan(overrides = {}) {
  const strains = overrides.strains ?? [
    planStrain(10, 'CAN-FGU-2', 'DEF-FGU-2', { box_count: 2 }),
    planStrain(11, 'CAN-BRE-1', 'DEF-BRE-1', { box_count: 1 }),
  ];
  const boxes = overrides.boxes ?? [
    planBox(501, 10, 'CAN-FGU-2.001', 'DEF-FGU-2.001'),
    planBox(502, 10, 'CAN-FGU-2.002', 'DEF-FGU-2.002', { status: 'inactive' }),
    planBox(503, 11, 'CAN-BRE-1.001', 'DEF-BRE-1.001', { status: 'pending_review' }),
  ];
  const blockers = overrides.blockers ?? [];
  const count = (items, action) => items.filter((item) => item.action === action).length;
  return {
    format: 'polypbase.species_code_change.preview.v1',
    organization_id: 1,
    institution_species: { id: 1, species_id: 101, scientific_name: 'Cassiopea andromeda', revision: 3 },
    code_assignment_id: 9,
    current_code: 'CAN',
    proposed_code: 'DEF',
    feasible: blockers.length === 0,
    blockers,
    warnings: [],
    strains,
    boxes,
    summary: {
      strains: strains.length, strains_to_rename: count(strains, 'rename'), strains_blocked: count(strains, 'blocked'),
      strains_unchanged: count(strains, 'unchanged'), boxes: boxes.length, boxes_to_rename: count(boxes, 'rename'),
      boxes_blocked: count(boxes, 'blocked'), boxes_unchanged: count(boxes, 'unchanged'),
      boxes_missing_global_id: 0, blockers: blockers.length,
    },
    dependencies: dependencies(boxes.filter((item) => item.status !== 'inactive').length),
    code_based_lookups: [],
    plan_fingerprint: 'a'.repeat(64),
    advisory_only: true,
    revalidation_required: true,
    mutation_available: false,
    ...overrides,
  };
}

// Text of a rendered element in document order. Every element child, including
// the single root of a nested component, is the next reconciled child.
function text(element) {
  let index = 0;
  const walk = (node) => {
    if (Array.isArray(node)) return node.map(walk).join('');
    if (node == null || typeof node === 'boolean') return '';
    if (typeof node !== 'object') return String(node);
    const child = element.children[index++];
    return child ? text(child) : '';
  };
  return walk(element.props.children);
}

function nodeText(node) {
  return [node, ...node.querySelectorAll('*')]
    .flatMap((item) => [].concat(item.props.children ?? []))
    .filter((child) => typeof child === 'string' || typeof child === 'number')
    .join(' ');
}

function mount({ target = { kind: 'profile', id: 1 }, t = (key) => key, language = 'fr', previewOnly = false } = {}) {
  const env = createBoxEnvironment();
  const client = env.load('../src/api/client.ts');
  const writes = [];
  for (const method of ['apiPost', 'apiPatch', 'apiPut', 'apiDelete']) {
    client[method] = (path, payload) => { writes.push({ method, path, payload }); return new Promise(() => {}); };
  }
  const Section = env.load(previewOnly
    ? '../src/components/SpeciesCodeChangePreview.tsx'
    : '../src/components/SpeciesStrainsAdminSection.tsx').default;
  const openedBoxes = [];
  // Only the standalone test host owns visibility; Species detail exposes no entry point.
  const PreviewHost = ({ isOpen, ...props }) => isOpen ? { type: Section, props } : null;
  const view = env.instance(previewOnly ? PreviewHost : Section, {
    target, t, language, organizationId: 1, organizationName: 'Aquarium Test',
    boxes: [], boxCollectionStatus: 'ready', onRetryBoxCollection() {},
    onOpenSpecies() {}, onBack() {}, onOpenBox: (boxId, code) => openedBoxes.push([boxId, code]),
    ...(previewOnly ? {
      isOpen: true, profileId: 1, scientificName: profile.scientific_name, currentCode: 'CAN', strains: detail.strains,
      onClose() { view.setProps({ isOpen: false }); },
    } : {}),
  });
  const registered = new Set();
  function renderLeaves() {
    for (let found = true; found;) {
      found = false;
      for (const node of view.nodes) {
        if (typeof node.type === 'function' && NESTED.has(node.type.name) && !registered.has(node.type)) {
          registered.add(node.type);
          env.renderNestedComponent(node.type);
          found = true;
        }
      }
      if (found) view.setProps({});
    }
  }
  renderLeaves();
  const h = {
    env, view, client, writes, openedBoxes, renderLeaves,
    async respond(path, payload, { error } = {}) {
      const request = [...env.requests].reverse().find((item) => item.path === path && !item.done);
      assert.ok(request, `Missing GET ${path}`);
      request.done = true;
      if (error) request.reject(error); else request.resolve(payload);
      await view.settle();
      renderLeaves();
      return request;
    },
    text() { return view.nodes.map(nodeText).join(' '); },
    byClass(name) { return view.nodes.filter((node) => (node.props.className ?? '').split(' ').includes(name)); },
    button(label) { return view.nodes.find((node) => node.type === 'button' && nodeText(node).includes(label)); },
    get trigger() { return h.byClass('species-code-change-trigger')[0]; },
    get panel() { return h.byClass('code-change-panel')[0]; },
    get input() { return view.nodes.find((node) => node.props.id === 'species-code-change-input'); },
    get previews() { return env.requests.filter((item) => item.path.includes('/code-change-preview/')); },
    click(node) { view.click(node); renderLeaves(); },
    type(value) { view.change(h.input, value); renderLeaves(); },
    submit() {
      const form = h.byClass('code-change-form')[0];
      assert.ok(form, 'Missing code-change form');
      let prevented = false;
      form.props.onSubmit({ preventDefault() { prevented = true; } });
      assert.equal(prevented, true);
      view.flush();
      renderLeaves();
    },
    rows(sectionTitleId) {
      const owner = view.nodes.find((node) => node.props['aria-labelledby'] === sectionTitleId);
      return owner ? owner.querySelectorAll('*').filter((node) => node.type === 'tr' && node.parent.type === 'tbody') : [];
    },
  };
  return h;
}

async function openPanel(options = {}) {
  const h = mount({ ...options, previewOnly: true });
  return h;
}

async function previewOf(response, options = {}) {
  const h = await openPanel(options);
  h.type('def');
  h.submit();
  await h.respond(previewPath('DEF'), response);
  return h;
}

const cells = (row) => row.children.map((cell) => text(cell));

// Pure helpers ------------------------------------------------------------------

test('helpers: request path, normalization and the server input rule', () => {
  assert.equal(helpers.codeChangePreviewPath(4, 'DEF'), '/api/admin/institution-species/4/code-change-preview/?proposed_code=DEF');
  assert.equal(helpers.normalizeSpeciesCode(' d e f g '), 'DEF');
  assert.equal(helpers.getProposedCodeProblem('', 'CAN'), 'required');
  assert.equal(helpers.getProposedCodeProblem('   ', 'CAN'), 'required');
  assert.equal(helpers.getProposedCodeProblem('de', 'CAN'), 'invalid_format');
  assert.equal(helpers.getProposedCodeProblem('d3f', 'CAN'), 'invalid_format');
  assert.equal(helpers.getProposedCodeProblem('can', 'CAN'), 'unchanged');
  assert.equal(helpers.getProposedCodeProblem('def', 'CAN'), null);
  assert.equal(helpers.getProposedCodeRefusal({ proposed_code: ['invalid_format'] }), 'invalid_format');
  assert.equal(helpers.getProposedCodeRefusal({ proposed_code: ['required'] }), 'required');
  assert.equal(helpers.getProposedCodeRefusal({ code: 'not_found' }), null);
  assert.equal(helpers.getProposedCodeRefusal(null), null);
});

test('helpers: a plan belongs to one institution, Species and proposed code', () => {
  const scope = { organizationId: 1, profileId: 1, proposedCode: 'DEF' };
  assert.equal(helpers.isPlanFor(plan(), scope), true);
  assert.equal(helpers.isPlanFor(plan({ organization_id: 2 }), scope), false);
  assert.equal(helpers.isPlanFor(plan({ institution_species: { id: 2 } }), scope), false);
  assert.equal(helpers.isPlanFor(plan({ proposed_code: 'GHI' }), scope), false);
  assert.equal(helpers.isPlanFor(null, scope), false);
  assert.equal(helpers.isPlanFor({}, scope), false);
});

test('helpers: reasons are grouped with their own records; absent counts differ from zero', () => {
  const groups = plain(helpers.groupCodeChangeIssues([
    { code: 'box_local_id_collision', box_id: 501, identifier: 'DEF-FGU-2.001', scope: 'outside_institution' },
    { code: 'box_local_id_collision', box_id: 502, identifier: 'DEF-FGU-2.002', scope: 'this_institution', conflicting_box_id: 9 },
    { code: 'box_local_id_collision', box_id: 502, identifier: 'DEF-FGU-2.002', scope: 'this_institution' },
    { code: 'strain_without_owner', strain_id: 10, identifier: 'CAN-FGU-2' },
    { code: 'code_assigned_in_institution', species_id: 5, scientific_name: 'Aurelia aurita' },
    { code: 'assignment_used_by_unplanned_strain', count: 2 },
    { code: 'current_code_on_other_species_boxes', box_ids: [900, 901, 902] },
  ], plan()));
  assert.deepEqual(groups.map((group) => [group.code, group.recordCount, group.identifiers]), [
    // The concerned record is named by its current identifier, once.
    ['box_local_id_collision', 2, ['CAN-FGU-2.001', 'CAN-FGU-2.002']],
    ['strain_without_owner', 1, ['CAN-FGU-2']],
    ['code_assigned_in_institution', 0, []],
    ['assignment_used_by_unplanned_strain', 2, []],
    // Boxes the plan does not enumerate are counted, never named.
    ['current_code_on_other_species_boxes', 3, []],
  ]);
  assert.equal(groups[0].isOutsideInstitution, true);
  assert.equal(groups[1].isOutsideInstitution, false);
  assert.equal(groups[2].scientificName, 'Aurelia aurita');
  assert.deepEqual(plain(helpers.groupCodeChangeIssues(undefined, {})), []);
  assert.equal(helpers.dependencyCount({ dependencies: dependencies(0) }, 'physical_labels'), 0);
  assert.equal(helpers.dependencyCount({ dependencies: [] }, 'physical_labels'), null);
  assert.equal(helpers.dependencyCount({}, 'physical_labels'), null);
  assert.deepEqual(plain(helpers.splitSpeciesCodePrefix('DEF-FGU-2.001', 'DEF')), { code: 'DEF', rest: '-FGU-2.001' });
  assert.deepEqual(plain(helpers.splitSpeciesCodePrefix('legacy', 'DEF')), { code: '', rest: 'legacy' });
});

// Entry point and code entry -----------------------------------------------------

test('Species detail hides the code-change action and mounts no preview', async () => {
  const h = mount();
  await h.respond(DETAIL_PATH, detail);
  assert.equal(h.trigger, undefined);
  assert.equal(h.button('speciesCodeChangeOpen'), undefined);
  assert.equal(h.panel, undefined);
  assert.equal(h.input, undefined);
  assert.equal(h.view.nodes.some((node) => node.type?.name === 'SpeciesCodeChangePreview'), false);
  assert.equal(h.previews.length, 0);
  assert.equal(h.byClass('species-strain').length, 2);
});

test('the preserved standalone component opens a compact preview panel', async () => {
  const h = await openPanel();
  assert.equal(h.panel.props.id, 'species-code-change');
  assert.equal(h.panel.props['aria-labelledby'], 'species-code-change-title');
  const panelText = nodeText(h.panel);
  assert.match(panelText, /speciesCodeChangeTitle/);
  assert.equal(h.panel.querySelectorAll('*').filter((node) => /^h[1-6]$/.test(node.type)).length, 1);
  assert.doesNotMatch(panelText, /speciesCodeChangeSimulation|speciesCodeChangeHint/);
  assert.equal(h.input.props['aria-describedby'], undefined);
  assert.match(panelText, /speciesCodeChangeCurrent CAN/);
  assert.equal(h.env.document.activeElement, h.input, 'the proposed code is focused');
  assert.equal(h.previews.length, 0, 'no request before an explicit preview');
  h.click(h.panel.querySelectorAll('*').find((node) => node.type === 'button' && node.props['aria-label'] === 'close'));
  assert.equal(h.panel, undefined);
});

test('a historical Species or a Species without code offers no code-change preview', async () => {
  let h = mount({ target: { kind: 'historical', id: 7 } });
  await h.respond('/api/admin/institution-species/historical/7/', {
    species_id: 7, shared_scientific_name: 'Aurelia aurita', species_code: 'AUR', strain_count: 0,
    requires_historical_adoption: true, strains: [],
  });
  assert.equal(h.trigger, undefined);
  h = mount();
  await h.respond(DETAIL_PATH, { ...detail, species_code: null });
  assert.equal(h.trigger, undefined);
});

test('the proposed code is validated like the server rule, without any request', async () => {
  const h = await openPanel();
  const submitButton = h.byClass('code-change-submit')[0];
  assert.equal(submitButton.props.type, 'submit');
  assert.equal(nodeText(submitButton), 'speciesCodeChangeSubmit');
  assert.equal(h.input.props.maxLength, 3);
  assert.equal(h.input.props['aria-invalid'], false);

  h.submit();
  assert.match(nodeText(h.panel), /speciesCodeChangeCodeRequired/);
  assert.equal(h.input.props['aria-invalid'], true);
  assert.equal(h.input.props['aria-describedby'], 'species-code-change-problem');
  assert.equal(h.byClass('code-change-problem')[0].props.role, 'alert');

  h.type('d1');
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeCodeRequired/, 'typing clears the previous problem');
  h.submit();
  assert.match(nodeText(h.panel), /speciesCodeChangeCodeInvalid/);

  h.type(' can ');
  assert.equal(h.input.props.value, 'CAN', 'normalized like the server');
  h.submit();
  assert.match(nodeText(h.panel), /speciesCodeChangeReasonUnchanged/);
  assert.equal(h.previews.length, 0);
  assert.equal(h.byClass('code-change-impact').length, 0);
});

// Request and impact -----------------------------------------------------------

test('previewing issues one institutional GET and shows the factual summary', async () => {
  const h = await openPanel();
  h.type('def');
  h.submit();
  assert.equal(h.previews.length, 1);
  assert.equal(h.previews[0].path, previewPath('DEF'));
  assert.ok(h.previews[0].options.signal, 'cancellable');
  // Pending: announced, button busy, nothing shown yet.
  const live = h.byClass('code-change-live')[0];
  assert.equal(live.props.role, 'status');
  assert.match(nodeText(live), /speciesCodeChangeLoading/);
  assert.equal(h.byClass('code-change-submit')[0].props.disabled, true);
  assert.equal(h.byClass('code-change-impact').length, 0);

  await h.respond(previewPath('DEF'), plan());
  assert.equal(nodeText(h.byClass('code-change-live')[0]), '');
  assert.equal(h.byClass('code-change-verdict').length, 0);
  assert.equal((nodeText(h.panel).match(/→/g) ?? []).length, 1, 'the form shows before → after only once');
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeReady|speciesCodeChangeFeasible|speciesCodeChangeNothingApplied|speciesCodeChangeInvariant|speciesCodeChangeBreakdown/);
  const facts = Object.fromEntries(h.byClass('code-change-facts')[0].querySelectorAll('*').filter((node) => node.type === 'dt')
    .map((term) => [term.props.children, text(term.parent.children[1].children[0])]));
  assert.deepEqual(facts, {
    speciesCodeChangeStrainsConcerned: '2',
    speciesCodeChangeBoxesConcerned: '3',

  });
  assert.equal(h.byClass('code-change-issues').length, 0);
  // Detail context comes from props; only the preview is requested. Nothing is written or selected.
  assert.deepEqual(h.env.requests.map((item) => item.path), [previewPath('DEF')]);
  assert.deepEqual(h.writes, []);
});

test('counts come from the server summary only; an absent count is omitted, never zero', async () => {
  const response = plan({ summary: { strains: 2 }, dependencies: undefined });
  const h = await previewOf(response);
  const terms = h.byClass('code-change-facts')[0].querySelectorAll('*').filter((node) => node.type === 'dt')
    .map((term) => term.props.children);
  assert.deepEqual(terms, ['speciesCodeChangeStrainsConcerned']);
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeBreakdown/);
  assert.equal(h.byClass('code-change-dependencies').length, 0);
  assert.doesNotMatch(h.text(), /speciesCodeChangeLabelsNote/);
});

test('strains show before and after identifiers with their collection location context', async () => {
  const t = (key) => (key === 'speciesStrainRecordedProvenance' ? 'Lieu de collecte : {name}' : key);
  const h = await previewOf(plan(), { t });
  const rows = h.rows('code-change-strains-title');
  assert.deepEqual(rows.map(cells), [
    ['createBoxStrainLabel 2Lieu de collecte : Guyane française', 'CAN-FGU-2', 'DEF-FGU-2'],
    // No recorded location: nothing is filled in.
    ['createBoxStrainLabel 1', 'CAN-BRE-1', 'DEF-BRE-1'],
  ]);
  // Only the Species code segment is emphasized; location and number are unchanged text.
  const proposed = rows[0].querySelector('.code-change-proposed');
  assert.equal(proposed.children[0].type, 'strong');
  assert.equal(text(proposed.children[0]), 'DEF');
});

test('boxes show only Box, current and proposed Local IDs with the real tracking preview', async () => {
  const h = await previewOf(plan());
  const rows = h.rows('code-change-boxes-title');
  assert.equal(rows.length, 3);
  assert.deepEqual(rows.map((row) => cells(row).slice(1)), [
    ['CAN-FGU-2.001', 'DEF-FGU-2.001'],
    ['CAN-FGU-2.002', 'DEF-FGU-2.002'],
    ['CAN-BRE-1.001', 'DEF-BRE-1.001'],
  ]);
  // The existing rich preview is reused as is, on the real Box and its current identifier.
  const previews = h.view.nodes.filter((node) => node.type === 'BoxTrackingPreview');
  assert.deepEqual(previews.map((node) => [node.props.boxId, node.props.code]), [
    [501, 'CAN-FGU-2.001'], [502, 'CAN-FGU-2.002'], [503, 'CAN-BRE-1.001'],
  ]);
  assert.ok(previews.every((node) => node.props.language === 'fr' && node.props.speciesName === 'Cassiopea andromeda'));
  assert.equal(previews[0].parent.type, 'th');
  previews[0].props.onOpenBox(501, 'CAN-FGU-2.001');
  assert.deepEqual(h.openedBoxes, [[501, 'CAN-FGU-2.001']], 'normal navigation to the Box detail');
  // Tracking data stays lazy: no Box request and no Box catalog for the list.
  assert.ok(h.env.requests.every((item) => !item.path.startsWith('/api/boxes')));
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeBoxPreviewHint|speciesCodeChangeConsequence|boxInventoryStatus/);
});

test('the rich Box preview component is reused unchanged, with its lazy detail loading', () => {
  assert.match(previewSource, /import BoxTrackingPreview from '\.\/BoxTrackingPreview';/);
  assert.match(previewSource, /<BoxTrackingPreview\s+boxId=\{item\.id\} code=\{item\.current_local_id\}/);
  const tracking = read('../src/components/BoxTrackingPreview.tsx');
  // Data is requested by the popover content only, once it is opened.
  assert.match(tracking, /\{isOpen \? createPortal\(/);
  assert.match(tracking, /apiGet<TrackingBoxDetail>\(`\/api\/boxes\/\$\{boxId\}\/`, \{ signal: controller\.signal \}\)/);
  assert.match(tracking, /onFocus=\{\(\) => \{ if \(!suppressFocus\.current\) \{ clearTimers\(\); setIsOpen\(true\); \} \}\}/);
  // The identity preview reads no Box detail and no biological data of its own.
  assert.doesNotMatch(previewSource + utilSource, /api\/boxes|biological_measurements|\bpolyps?\b|polyp_count|ephyra/i);
});

test('many boxes stay scannable: first rows, an explicit complete list, filters by consequence', async () => {
  const boxes = Array.from({ length: 120 }, (_, index) => {
    const number = String(index + 1).padStart(3, '0');
    return index % 40 === 0
      ? planBox(1000 + index, 10, `legacy-${number}`, null, { action: 'unchanged', reasons: ['local_id_without_current_code'] })
      : planBox(1000 + index, 10, `CAN-FGU-2.${number}`, `DEF-FGU-2.${number}`);
  });
  const h = await previewOf(plan({ boxes }));
  assert.equal(h.rows('code-change-boxes-title').length, 50);
  const more = h.byClass('code-change-more')[0];
  assert.equal(nodeText(more), 'speciesCodeChangeShowAllBoxes');
  h.click(more);
  assert.equal(h.rows('code-change-boxes-title').length, 120);
  assert.equal(h.byClass('code-change-more').length, 0);

  const filters = h.byClass('code-change-filters')[0];
  assert.equal(filters.props.role, 'group');
  // Counts are the server's; only consequences present in the plan are offered.
  assert.deepEqual(filters.children.map((node) => [text(node), node.props['aria-pressed']]), [
    ['speciesCodeChangeFilterAll120', true], ['speciesCodeChangeActionRename117', false],
    ['speciesCodeChangeActionUnchanged3', false],
  ]);
  h.click(filters.children[2]);
  const unchanged = h.rows('code-change-boxes-title');
  assert.equal(unchanged.length, 3);
  assert.deepEqual(cells(unchanged[0]).slice(1), ['legacy-001', 'speciesCodeChangeKept']);
  assert.equal(cells(unchanged[0]).length, 3);
  assert.equal(h.byClass('code-change-filters')[0].children[2].props['aria-pressed'], true);
});

// Blockers and warnings ------------------------------------------------------------

const blockedPlan = () => plan({
  strains: [
    planStrain(10, 'CAN-FGU-2', 'DEF-FGU-2', { box_count: 2, action: 'blocked', ownership: 'shared', reasons: ['strain_used_by_another_institution'] }),
    planStrain(11, 'CAN-BRE-1', 'DEF-BRE-1', { box_count: 1 }),
    planStrain(12, 'old strain', null, { action: 'unchanged', ownership: 'legacy_unowned', reasons: ['code_format'] }),
  ],
  boxes: [
    planBox(501, 10, 'CAN-FGU-2.001', 'DEF-FGU-2.001', { action: 'blocked', reasons: ['strain_blocked'] }),
    planBox(502, 10, 'CAN-FGU-2.002', 'DEF-FGU-2.002', { action: 'blocked', reasons: ['strain_blocked', 'local_id_history_unresolved'] }),
    planBox(503, 11, 'CAN-BRE-1.001', 'DEF-BRE-1.001', { action: 'blocked', reasons: ['box_local_id_collision'] }),
    planBox(504, 11, 'CAN-BRE-1.002', null, { action: 'unchanged', is_merged_alias: true, reasons: ['merged_alias_retained'] }),
  ],
  blockers: [
    { code: 'code_assigned_in_institution', category: 'collision', species_id: 5, scientific_name: 'Aurelia aurita' },
    { code: 'strain_used_by_another_institution', category: 'shared_data', strain_id: 10, identifier: 'CAN-FGU-2' },
    { code: 'local_id_history_unresolved', category: 'historical_identity', box_id: 502 },
    { code: 'box_local_id_collision', category: 'collision', box_id: 503, identifier: 'DEF-BRE-1.001', scope: 'outside_institution' },
    { code: 'future_unknown_reason', category: 'consistency' },
  ],
  warnings: [
    { code: 'noncanonical_strain_unchanged', strain_id: 12 },
    { code: 'missing_global_id', box_ids: [503] },
  ],
});

test('a blocked plan is announced as impossible, with readable reasons and blocked records', async () => {
  const h = await previewOf(blockedPlan());
  const refusal = h.byClass('code-change-problem')[0];
  assert.equal(refusal.props.role, 'alert');
  assert.equal(nodeText(refusal), 'speciesCodeChangeImpossible');
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeImpossibleNote/);
  // A blocked recoding is never summarized as identifiers that will change.
  assert.doesNotMatch(h.text(), /speciesCodeChangeIdentifiersToChange|speciesCodeChangeFeasible|speciesCodeChangeNothingApplied/);

  const [blockers, warnings] = h.byClass('code-change-issues');
  assert.equal(blockers.props.className, 'code-change-issues is-blocker');
  assert.equal(text(blockers.children[0]), 'speciesCodeChangeBlockers5');
  const items = blockers.querySelectorAll('*').filter((node) => node.type === 'li');
  assert.deepEqual(items.map((item) => text(item.children[0])), [
    'speciesCodeChangeReasonAssigned', 'speciesCodeChangeReasonStrainShared', 'speciesCodeChangeReasonHistoryUnresolved',
    'speciesCodeChangeReasonBoxCollision', 'speciesCodeChangeReasonOther',
  ]);
  // Own-institution Species named by the server; concerned records by current identifier.
  assert.match(text(items[0]), /Aurelia aurita/);
  assert.match(text(items[1]), /speciesCodeChangeRecordCAN-FGU-2$/);
  assert.match(text(items[2]), /CAN-FGU-2\.002/);
  // A conflict outside the institution: a generic sentence, no foreign detail.
  assert.match(text(items[3]), /speciesCodeChangeOutsideInstitution/);
  assert.doesNotMatch(text(items[0]) + text(items[1]) + text(items[2]), /speciesCodeChangeOutsideInstitution/);

  // Warnings stay a separate, differently marked list.
  assert.equal(warnings.props.className, 'code-change-issues is-warning');
  assert.deepEqual(warnings.querySelectorAll('*').filter((node) => node.type === 'li').map((item) => text(item.children[0])), [
    'speciesCodeChangeReasonStrainFormat', 'speciesCodeChangeReasonMissingGlobalId',
  ]);

  const strainRows = h.rows('code-change-strains-title');
  assert.deepEqual(strainRows.map((row) => cells(row).slice(1)), [
    ['CAN-FGU-2', 'DEF-FGU-2'],
    ['CAN-BRE-1', 'DEF-BRE-1'],
    ['old strain', 'speciesCodeChangeKept'],
  ]);
  assert.equal(strainRows[0].querySelector('.code-change-proposed').props.className, 'code-change-proposed is-blocked');
  // A strain the detail does not list is identified by its own identifier.
  assert.equal(cells(strainRows[2])[0], 'old strain');
  const boxRows = h.rows('code-change-boxes-title');
  assert.ok(boxRows.every((row) => cells(row).length === 3));
  assert.equal(boxRows[0].querySelector('.code-change-proposed').props.className, 'code-change-proposed is-blocked');
  assert.equal(cells(boxRows[3])[2], 'speciesCodeChangeKept');
  // No server code, internal field or foreign detail reaches the screen.
  const shown = h.text();
  for (const hidden of ['strain_used_by_another_institution', 'local_id_history_unresolved', 'future_unknown_reason',
    'outside_institution', 'shared_data', 'legacy_unowned', 'aaaaaaaa', 'collision', 'namespace']) {
    assert.ok(!shown.includes(hidden), hidden);
  }
});

test('warnings alone never make a plan impossible', async () => {
  const h = await previewOf(plan({
    warnings: [
      { code: 'proposed_code_used_outside_institution' },
      { code: 'current_code_on_other_species_boxes', box_ids: [900, 901] },
    ],
  }));
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeFeasible/);
  assert.doesNotMatch(h.text(), /speciesCodeChangeImpossible/);
  const issues = h.byClass('code-change-issues');
  assert.equal(issues.length, 1);
  assert.equal(issues[0].props.className, 'code-change-issues is-warning');
  const items = issues[0].querySelectorAll('*').filter((node) => node.type === 'li');
  assert.deepEqual(items.map((item) => text(item)), [
    'speciesCodeChangeReasonUsedOutside',
    // Boxes outside the plan are counted from the server list, never named.
    'speciesCodeChangeReasonCurrentOnOtherSpeciesspeciesCodeChangeRecords',
  ]);
});

test('a Species without strain or box has an explicit empty impact, distinct from an error', async () => {
  const h = await previewOf(plan({ strains: [], boxes: [] }));
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeNoImpact|speciesCodeChangeFeasible/);
  assert.match(h.text(), /speciesCodeChangeEmpty/);
  assert.equal(h.view.nodes.filter((node) => node.type === 'table').length, 0);
  assert.equal(h.view.nodes.filter((node) => node.props.role === 'alert').length, 0);
  // Reported zeros stay visible as zeros.
  const facts = h.byClass('code-change-facts')[0];
  const strains = facts.querySelectorAll('*').find((node) => node.type === 'dt' && node.props.children === 'speciesCodeChangeStrainsConcerned');
  assert.equal(text(strains.parent.children[1].children[0]), '0');
});

test('history, labels, secondary counters and prose are absent from the preview', async () => {
  const h = await previewOf(plan());
  assert.equal(h.byClass('code-change-history').length, 0);
  assert.equal(h.byClass('code-change-dependencies').length, 0);
  assert.equal(h.panel.querySelectorAll('*').filter((node) => node.type === 'details').length, 0);
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChange(?:History|Labels|Dependency|MissingGlobalId|IdentifiersToChange|Simulation|Invariant|Hint)/);
  assert.doesNotMatch(previewSource + utilSource, /label-selection|labelSelection|QrLabel|print\(|LabelsView/);
});

// Direct component coverage does not depend on the rest of the Species page.
test('standalone preview stays compact with only two totals and three-column tables', async () => {
  const h = mount({ previewOnly: true });
  assert.equal(h.input.props['aria-describedby'], undefined);
  h.type('def');
  h.submit();
  await h.respond(previewPath('DEF'), plan());
  const shown = nodeText(h.panel);
  assert.equal((shown.match(/→/g) ?? []).length, 1);
  assert.doesNotMatch(shown, /speciesCodeChange(?:Simulation|Hint|Ready|Feasible|NothingApplied|Invariant|History|Labels|Dependency|Consequence|Breakdown|IdentifiersToChange|MissingGlobalId|Blockers|Warnings)/);
  const facts = h.byClass('code-change-facts')[0];
  assert.deepEqual(facts.children.map((row) => [text(row.children[0]), text(row.children[1])]), [
    ['speciesCodeChangeStrainsConcerned', '2'], ['speciesCodeChangeBoxesConcerned', '3'],
  ]);
  const tables = h.panel.querySelectorAll('*').filter((node) => node.type === 'table');
  assert.deepEqual(tables.map((table) => table.querySelectorAll('*').filter((node) => node.type === 'tr' && node.parent.type === 'thead').map(cells)), [
    [['createBoxStrainLabel', 'speciesCodeChangeBefore', 'speciesCodeChangeAfter']],
    [['boxInventoryBox', 'speciesCodeChangeBefore', 'speciesCodeChangeAfter']],
  ]);
  assert.ok([...h.rows('code-change-strains-title'), ...h.rows('code-change-boxes-title')].every((row) => cells(row).length === 3));
  assert.equal(h.view.nodes.filter((node) => node.type === 'BoxTrackingPreview').length, 3);
  assert.deepEqual(h.env.requests.map((request) => request.path), [previewPath('DEF')]);
  assert.deepEqual(h.writes, []);
});

test('standalone preview preserves actual blockers and warnings without a success or history section', async () => {
  const h = mount({ previewOnly: true });
  h.type('DEF');
  h.submit();
  await h.respond(previewPath('DEF'), blockedPlan());
  assert.equal(h.byClass('code-change-problem')[0].props.role, 'alert');
  assert.match(nodeText(h.panel), /speciesCodeChangeImpossible/);
  const [blockers, warnings] = h.byClass('code-change-issues');
  assert.equal(blockers.props.className, 'code-change-issues is-blocker');
  assert.equal(warnings.props.className, 'code-change-issues is-warning');
  assert.match(nodeText(blockers), /speciesCodeChangeReasonStrainShared|speciesCodeChangeReasonBoxCollision/);
  assert.match(nodeText(warnings), /speciesCodeChangeReasonMissingGlobalId/);
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeFeasible|speciesCodeChangeHistory/);
  assert.deepEqual(h.writes, []);
});

test('standalone preview keeps validation, cancellation and permission errors', async () => {
  const h = mount({ previewOnly: true });
  h.submit();
  assert.equal(h.input.props['aria-describedby'], 'species-code-change-problem');
  assert.equal(h.previews.length, 0);
  h.type('DEF');
  h.submit();
  const pending = h.previews[0];
  h.type('GHI');
  assert.equal(pending.options.signal.aborted, true);
  await h.respond(previewPath('DEF'), plan());
  assert.equal(h.byClass('code-change-impact').length, 0);
  h.submit();
  await h.respond(previewPath('GHI'), null, { error: new h.client.ApiError(403, 'Forbidden') });
  assert.match(nodeText(h.panel), /speciesCodeChangeForbidden/);
  assert.equal(h.button('taxonomyRetry'), undefined);
  assert.deepEqual(h.writes, []);
});

// Failures ---------------------------------------------------------------------

test('a network failure is an error with an explicit retry of the same code', async () => {
  const h = await openPanel();
  h.type('def');
  h.submit();
  await h.respond(previewPath('DEF'), null, { error: new Error('offline') });
  const notice = h.view.nodes.find((node) => node.props.role === 'alert');
  assert.match(nodeText(notice), /speciesCodeChangeLoadError/);
  assert.equal(h.byClass('code-change-impact').length, 0);
  assert.equal(h.byClass('code-change-submit')[0].props.disabled, false);
  h.click(h.button('taxonomyRetry'));
  assert.equal(h.previews.length, 2);
  await h.respond(previewPath('DEF'), plan());
  assert.equal(h.byClass('code-change-impact').length, 1);
  assert.equal(h.view.nodes.filter((node) => node.props.role === 'alert').length, 0);
});

test('permission, missing Species and server validation refusals are distinct and not retried', async () => {
  for (const [error, message] of [
    [(client) => new client.ApiError(403, 'no'), 'speciesCodeChangeForbidden'],
    [(client) => new client.ApiError(404, 'no', { code: 'not_found' }), 'speciesDetailNotFound'],
    [(client) => new client.ApiError(400, 'no', { proposed_code: ['invalid_format'] }), 'speciesCodeChangeCodeInvalid'],
    [(client) => new client.ApiError(500, 'no'), 'speciesCodeChangeLoadError'],
  ]) {
    const h = await openPanel();
    h.type('def');
    h.submit();
    await h.respond(previewPath('DEF'), null, { error: error(h.client) });
    const notice = h.view.nodes.find((node) => node.props.role === 'alert');
    assert.match(nodeText(notice), new RegExp(message));
    assert.equal(Boolean(h.button('taxonomyRetry')), message === 'speciesCodeChangeLoadError', message);
    assert.equal(h.byClass('code-change-impact').length, 0);
    assert.equal(h.previews.length, 1);
  }
});

// Freshness ----------------------------------------------------------------------

test('a response for an earlier proposed code is rejected', async () => {
  const h = await openPanel();
  h.type('def');
  h.submit();
  const first = h.previews[0];
  h.type('ghi');
  assert.equal(first.options.signal.aborted, true, 'typing cancels the pending preview');
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeLoading/);
  h.submit();
  assert.equal(h.previews.length, 2);
  // The earlier answer arrives late: it must not be shown for GHI.
  await h.respond(previewPath('DEF'), plan());
  assert.equal(h.byClass('code-change-impact').length, 0);
  assert.match(nodeText(h.byClass('code-change-live')[0]), /speciesCodeChangeLoading/);
  await h.respond(previewPath('GHI'), plan({ proposed_code: 'GHI' }));
  assert.equal(h.input.props.value, 'GHI');
  assert.equal(h.byClass('code-change-impact').length, 1);

  // Editing the code afterwards removes the result that no longer matches the field.
  h.type('gh');
  assert.equal(h.byClass('code-change-impact').length, 0);
  assert.doesNotMatch(nodeText(h.panel), /speciesCodeChangeReady/);
});

test('repeated previews of one code do not stack requests or results', async () => {
  const h = await openPanel();
  h.type('def');
  h.submit();
  assert.equal(h.byClass('code-change-submit')[0].props.disabled, true);
  await h.respond(previewPath('DEF'), plan());
  h.submit();
  assert.equal(h.previews.length, 2, 'an explicit new preview recomputes the plan');
  assert.equal(h.byClass('code-change-impact').length, 0, 'the previous plan is not shown as current meanwhile');
  await h.respond(previewPath('DEF'), plan({ strains: [], boxes: [] }));
  assert.equal(h.byClass('code-change-impact').length, 1);
  assert.match(h.text(), /speciesCodeChangeEmpty/);
});

test('a plan computed for another institution, Species or code is never displayed', async () => {
  for (const foreign of [
    plan({ organization_id: 2 }),
    plan({ institution_species: { id: 2, species_id: 102, scientific_name: 'Other species', revision: 1 } }),
    plan({ proposed_code: 'XYZ' }),
  ]) {
    const h = await previewOf(foreign);
    assert.equal(h.byClass('code-change-impact').length, 0);
    assert.match(nodeText(h.view.nodes.find((node) => node.props.role === 'alert')), /speciesCodeChangeLoadError/);
    assert.doesNotMatch(h.text(), /Other species|XYZ/);
  }
});

test('changing standalone Species or institution resets the simulation and ignores its pending answer', async () => {
  for (const change of [{ profileId: 2 }, { organizationId: 2, organizationName: 'Other institution' }]) {
    const h = await openPanel();
    h.type('def');
    h.submit();
    const pending = h.previews[0];
    h.view.setProps(change);
    h.renderLeaves();
    assert.equal(pending.options.signal.aborted, true);
    assert.equal(h.input.props.value, '');
    pending.resolve(plan());
    await h.view.settle();
    h.renderLeaves();
    assert.equal(h.byClass('code-change-impact').length, 0);
    assert.equal(h.input.props.value, '');
  }
});

test('a displayed plan does not survive a change of Species or institution', async () => {
  for (const change of [{ profileId: 2 }, { organizationId: 2, organizationName: 'Other institution' }]) {
    const h = await previewOf(plan());
    assert.equal(h.byClass('code-change-impact').length, 1);
    h.view.setProps(change);
    h.renderLeaves();
    assert.equal(h.byClass('code-change-impact').length, 0);
    assert.equal(h.input.props.value, '');
  }
});

test('closing or leaving the page cancels the pending preview without late updates', async () => {
  let h = await openPanel();
  h.type('def');
  h.submit();
  h.click(h.panel.querySelectorAll('*').find((node) => node.type === 'button' && node.props['aria-label'] === 'close'));
  assert.equal(h.panel, undefined);
  assert.equal(h.previews[0].options.signal.aborted, true);
  h.previews[0].resolve(plan());
  await h.view.settle();
  h.renderLeaves();
  assert.equal(h.byClass('code-change-impact').length, 0);
  // Reopening starts from an empty entry, never from the cancelled answer.
  h.view.setProps({ isOpen: true });
  h.renderLeaves();
  assert.equal(h.input.props.value, '');
  assert.equal(h.byClass('code-change-impact').length, 0);

  h = await openPanel();
  h.type('def');
  h.submit();
  h.view.unmount();
  assert.equal(h.previews[0].options.signal.aborted, true);
  h.previews[0].resolve(plan());
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(h.view.updatesAfterUnmount, 0);
});

// Read-only boundary -----------------------------------------------------------

test('the preview exposes no mutation: no write request, no apply action, even when feasible', async () => {
  // Even a server that would announce an available mutation gets no apply control here.
  const h = await previewOf(plan({ mutation_available: true }));
  assert.deepEqual(h.writes, []);
  const buttons = h.panel.querySelectorAll('*').filter((node) => node.type === 'button').map(nodeText);
  assert.deepEqual(buttons, ['', 'speciesCodeChangeSubmit']);
  assert.ok(h.env.requests.every((item) => /^\/api\/admin\/institution-species\/1\/(code-change-preview\/\?proposed_code=[A-Z]{3})?$/.test(item.path)));

  for (const source of [previewSource, utilSource]) {
    assert.doesNotMatch(source, /apiPost|apiPatch|apiPut|apiDelete|apiRequest|fetch\(/);
    // The flag is typed but never read: nothing can be unlocked from the client.
    assert.doesNotMatch(source, /apply_species_code_change|species-codes|code-change\/apply|\.mutation_available/);
    assert.doesNotMatch(source, /localStorage|sessionStorage/);
  }
  assert.equal((previewSource.match(/apiGet</g) ?? []).length, 1);
  assert.match(previewSource, /apiGet<SpeciesCodeChangePlan>\(codeChangePreviewPath\(profileId, proposedCode\), \{ signal: controller\.signal \}\)/);
  // The Species workspace itself gained no write path for codes.
  assert.doesNotMatch(section, /code-change-preview|species-codes/);
  assert.doesNotMatch(section, /SpeciesCodeChangePreview|species-code-change-trigger|speciesCodeChangeOpen|isCodeChangeOpen/);
});

test('Species detail receives the active institution and keeps the preview hidden in desktop-only Administration', () => {
  assert.match(adminView, /<SpeciesStrainsAdminSection\s+key=\{activeOrganizationId\}\s+target=\{speciesTarget\}\s+organizationId=\{activeOrganizationId\}/);
  assert.match(adminView, /organizationName=\{profile\?\.active_organization\?\.name \?\? ''\}\s+boxes=\{boxes\}\s+boxCollectionStatus=\{boxCollectionStatus\}\s+onRetryBoxCollection=\{onRetryBoxCollection\}\s+language=\{language\}\s+onOpenSpecies=\{onOpenSpecies\}\s+onOpenBox=\{onOpenBox\}/);
  assert.doesNotMatch(section, /SpeciesCodeChangePreview|setIsCodeChangeOpen/);
  // The preserved preview has no route or mount in the desktop-only Species detail.
  assert.doesNotMatch(app, /code-change/);
  assert.match(app, /if \(activeTab === 'admin' && !isDesktopApp\) \{\s*replaceRoute\(\{ tab: 'pilotage', boxCode: null, boxId: null \}, '\/', true\);\s*return;\s*\}/);
  assert.match(app, /canOpenAdmin=\{canUseAdmin && isDesktopApp\}/);
  assert.doesNotMatch(read('../src/styles/responsive/tablet.css') + read('../src/styles/responsive/phone.css'), /code-change/);
});

// Translations ---------------------------------------------------------------------

function catalog(source) {
  return Object.fromEntries([...source.matchAll(/^  ([A-Za-z0-9]+): '(.*)',$/gm)].map((match) => [match[1], match[2]]));
}
const fr = catalog(frSource);
const en = catalog(enSource);

test('every preview text exists in FR and EN with the product vocabulary', () => {
  const keys = new Set([...(previewSource + section).matchAll(/'(speciesCodeChange[A-Za-z]+)'/g)].map((match) => match[1]));
  assert.ok(keys.size > 40);
  for (const key of keys) {
    for (const [name, values] of [['fr', fr], ['en', en]]) {
      assert.ok(values[key], `${name}.${key}`);
      // Natural wording: no storage or planner vocabulary, no AAA/BBB field names, no middle dot.
      assert.doesNotMatch(values[key], /·|\bAAA\b|\bBBB\b|OrganizationSpeciesCode|fingerprint|namespace|legacy|blocker|bloqueur|alias|SQL/i, `${name}.${key}`);
      // A simulation never borrows the vocabulary of a saved change.
      assert.doesNotMatch(values[key], /Enregistrer|Appliquer|Confirmer|\bSave\b|\bApply\b|\bConfirm\b/, `${name}.${key}`);
    }
  }
  // Removed UI copy may stay in the catalogs, which are owned by another task.
  for (const key of ['speciesCodeChangeSimulation', 'speciesCodeChangeHint', 'speciesCodeChangeReady',
    'speciesCodeChangeHistoryTitle', 'speciesCodeChangeLabelsNote', 'speciesCodeChangeConsequence']) {
    assert.ok(!previewSource.includes(`'${key}'`), key);
  }
  assert.equal(fr.speciesCodeChangeSubmit, 'Prévisualiser les conséquences');
  assert.equal(fr.speciesCodeChangeTitle, 'Prévisualisation');
  assert.equal(fr.speciesCodeChangeCurrent, 'Actuel');
  assert.equal(fr.speciesCodeChangeProposed, 'Proposé');
  assert.equal(en.speciesCodeChangeTitle, 'Preview');
  assert.equal(en.speciesCodeChangeCurrent, 'Current');
  assert.equal(en.speciesCodeChangeProposed, 'Proposed');
  assert.equal(fr.speciesCodeChangeImpossible, 'Modification impossible');
  assert.equal(fr.speciesCodeChangeBefore, 'Avant');
  assert.equal(fr.speciesCodeChangeAfter, 'Après');
  assert.match(fr.speciesCodeChangeInvariant, /lieu de collecte.*numéro de souche.*numéro de boîte.*identifiant global/);
  assert.match(fr.speciesCodeChangeForbidden, /Merci de contacter un administrateur\.$/);
  assert.match(en.speciesCodeChangeForbidden, /Please contact your administrator\.$/);
  // The collection location is never presented as an institution or a provider.
  assert.doesNotMatch(fr.speciesCodeChangeInvariant + en.speciesCodeChangeInvariant, /fournisseur|provider|origine|origin/i);
  // Every reason the planner can return has its own readable text.
  for (const code of ['species_code_missing', 'current_code_unusable', 'code_unchanged', 'code_assigned_in_institution',
    'proposed_code_used_by_other_species', 'strain_code_collision', 'box_namespace_reserved', 'box_namespace_in_use',
    'box_local_id_collision', 'box_local_id_historically_reserved', 'strain_code_collision_in_batch',
    'box_local_id_collision_in_batch', 'strain_used_by_another_institution', 'strain_without_owner',
    'strain_owned_by_another_institution', 'local_id_history_unresolved', 'reviewed_identity_protected',
    'strain_prefix_mismatch', 'box_local_id_strain_mismatch', 'local_identity_mismatch',
    'assignment_used_by_unplanned_strain', 'missing_global_id', 'noncanonical_strain_unchanged',
    'box_without_current_code_unchanged', 'current_code_on_other_species_boxes',
    'proposed_code_used_outside_institution', 'merged_alias_retained', 'strain_blocked', 'code_format',
    'prefix_mismatch', 'local_id_strain_mismatch', 'local_id_without_current_code']) {
    assert.match(previewSource, new RegExp(`^  ${code}: 'speciesCodeChangeReason[A-Za-z]+',$`, 'm'), code);
  }
});

test('the blocked plan reads naturally in French and in English', async () => {
  for (const [language, values, expected] of [
    ['fr', fr, [/Actuel CAN → Proposé/, /Modification impossible/,
      /Souches concernées 3/, /Boîtes concernées 4/, /Souche également utilisée par des boîtes d’une autre structure\./,
      /Lieu de collecte : Guyane française/, /Avant/, /Après/, /Avertissements/]],
    ['en', en, [/Current CAN → Proposed/, /Change not possible/,
      /Strains affected 3/, /Boxes affected 4/, /Strain also used by boxes of another institution\./,
      /Collection location: Guyane française/, /Before/, /After/, /Warnings/]],
  ]) {
    const h = await previewOf(blockedPlan(), { t: (key) => values[key] ?? `MISSING:${key}`, language });
    const shown = h.text();
    assert.equal(h.input.props.value, 'DEF');
    assert.doesNotMatch(shown, /MISSING:/);
    for (const pattern of expected) assert.match(shown, pattern);
    assert.doesNotMatch(shown, /\{[a-z]+\}/, 'every placeholder is filled');
    assert.equal(h.view.nodes.filter((node) => node.type === 'BoxTrackingPreview')[0].props.language, language);
  }
});
