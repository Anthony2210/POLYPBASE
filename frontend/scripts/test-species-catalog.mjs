import assert from 'node:assert/strict';
import { readdirSync, readFileSync } from 'node:fs';
import test from 'node:test';

import vm from 'node:vm';
import ts from 'typescript';

import { createBoxEnvironment, deferred, text as elementText } from './create-box-harness.mjs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const utilSource = read('../src/utils/speciesCatalog.ts');
const { outputText } = ts.transpileModule(utilSource, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const helpers = {};
vm.runInNewContext(outputText, { exports: helpers });

const section = read('../src/components/SpeciesStrainsAdminSection.tsx');
const strainsPanel = read('../src/components/SpeciesStrainsPanel.tsx');
const adminView = read('../src/components/AdminView.tsx');
const app = read('../src/App.tsx');
const fr = read('../src/i18n/fr.ts');
const en = read('../src/i18n/en.ts');
const plain = (value) => JSON.parse(JSON.stringify(value));

const LIST_PATH = '/api/admin/institution-species/';
const WORMS_PATH = '/api/admin/species-catalog/worms-search/';

const profile = (id, name, extra = {}) => ({
  id, species_id: id + 100, scientific_name: name, worms_aphia_id: null, worms_url: null,
  species_code: null, revision: 1, strain_count: 0, ...extra,
});
const historical = (speciesId, name, extra = {}) => ({
  species_id: speciesId, shared_scientific_name: name, species_code: null, strain_count: 0,
  requires_historical_adoption: true, ...extra,
});
const listResponse = {
  institution_species: [
    profile(1, 'Cassiopea andromeda', { species_code: 'CAN', worms_aphia_id: 135306, worms_url: 'https://www.marinespecies.org/aphia.php?p=taxdetails&id=135306', strain_count: 2 }),
  ],
  historical_species: [historical(7, 'Aurelia aurita', { species_code: 'AUR', strain_count: 3 }), historical(8, 'Rhizostoma pulmo')],
};

function wormsRecord(aphiaId, name, extra = {}) {
  return {
    aphia_id: aphiaId, scientific_name: name, authority: '(Linnaeus, 1758)', status: 'accepted', unaccept_reason: '',
    rank: 'Species', rank_id: 220, is_species_level: true, valid_aphia_id: aphiaId, valid_name: name,
    valid_authority: '(Linnaeus, 1758)', kingdom: 'Animalia', phylum: 'Cnidaria', class_name: 'Scyphozoa',
    order: '', family: '', url: `https://www.marinespecies.org/aphia.php?p=taxdetails&id=${aphiaId}`,
    local_species: null, valid_local_species: null, ...extra,
  };
}

// Pure helpers ---------------------------------------------------------------

test('list entries combine own profiles and historical references, sorted, nothing else', () => {
  const entries = helpers.buildSpeciesEntries(listResponse);
  assert.deepEqual(plain(entries.map((entry) => [entry.scientificName, entry.target.kind, entry.target.id, entry.isHistorical])), [
    ['Aurelia aurita', 'historical', 7, true],
    ['Cassiopea andromeda', 'profile', 1, false],
    ['Rhizostoma pulmo', 'historical', 8, true],
  ]);
  // A profile's local name and AphiaID come from the institution, not the shared reference.
  assert.equal(entries[1].aphiaId, 135306);
  assert.deepEqual(plain(helpers.buildSpeciesEntries({ institution_species: [], historical_species: [] })), []);
});

test('search covers scientific name, code and WoRMS identifier, accent- and case-insensitively', () => {
  const entries = helpers.buildSpeciesEntries(listResponse);
  const names = (query) => plain(helpers.filterSpeciesEntries(entries, query).map((entry) => entry.scientificName));
  assert.deepEqual(names('  '), ['Aurelia aurita', 'Cassiopea andromeda', 'Rhizostoma pulmo']);
  assert.deepEqual(names('CASSIOPÉA'), ['Cassiopea andromeda']);
  assert.deepEqual(names('aur'), ['Aurelia aurita']);
  assert.deepEqual(names('can'), ['Cassiopea andromeda']);
  assert.deepEqual(names('135306'), ['Cassiopea andromeda']);
  assert.deepEqual(names('aurelia can'), []);
});

test('paths and targets distinguish institutional profiles from historical references', () => {
  assert.equal(helpers.adminSpeciesPath({ kind: 'profile', id: 4 }), '/administration/species-strains/4');
  assert.equal(helpers.adminSpeciesPath({ kind: 'historical', id: 9 }), '/administration/species-strains/historical/9');
  assert.deepEqual(plain(helpers.adminSpeciesTargetFor({ institution_species_id: 4, species_id: 104 })), { kind: 'profile', id: 4 });
  assert.deepEqual(plain(helpers.adminSpeciesTargetFor({ institution_species_id: null, species_id: 9 })), { kind: 'historical', id: 9 });
  assert.equal(helpers.sameSpeciesTarget({ kind: 'profile', id: 4 }, { kind: 'historical', id: 4 }), false);
});

test('payloads carry the name and optional AphiaID only; edits carry the read revision', () => {
  assert.deepEqual(plain(helpers.buildRegistrationPayload('  Medusa   aurita ', 5)), { scientific_name: 'Medusa aurita', worms_aphia_id: 5 });
  assert.deepEqual(plain(helpers.buildRegistrationPayload('Aurelia coerulea', null)), { scientific_name: 'Aurelia coerulea' });
  assert.deepEqual(plain(helpers.buildEditPayload(3, ' Aurelia  coerulea', null)), {
    expected_revision: 3, scientific_name: 'Aurelia coerulea', worms_aphia_id: null,
  });
});

test('a WoRMS reference stays compatible only with its exact name, never silently dropped', () => {
  const record = { kind: 'record', record: wormsRecord(5, 'Aurelia aurita') };
  const saved = { kind: 'saved', aphiaId: 6, url: null, name: 'Aurelia coerulea' };
  assert.equal(helpers.isReferenceCompatible(record, ' Aurelia  aurita '), true);
  assert.equal(helpers.isReferenceCompatible(record, 'aurelia aurita'), false);
  assert.equal(helpers.isReferenceCompatible(saved, 'Aurelia coerulea'), true);
  assert.equal(helpers.isReferenceCompatible(saved, 'Aurelia coerulea alba'), false);
  assert.equal(helpers.isReferenceCompatible(null, 'anything'), true);
  assert.equal(helpers.referenceAphiaId(record), 5);
  assert.equal(helpers.referenceAphiaId(saved), 6);
  assert.equal(helpers.referenceAphiaId(null), null);
});

test('WoRMS query rule, accepted names and service failures follow the server', () => {
  assert.equal(helpers.normalizeWormsQuery('  Aurelia   aurita '), 'Aurelia aurita');
  assert.equal(helpers.normalizeWormsQuery('Au'), null);
  assert.equal(helpers.getWormsQueryProblem('Aurelia sp. 2'), 'unsupported_characters');
  const synonym = wormsRecord(5, 'Medusa aurita', { status: 'unaccepted', valid_aphia_id: 6, valid_name: 'Aurelia aurita' });
  assert.equal(helpers.isWormsAccepted(synonym), false);
  assert.equal(helpers.hasDistinctAcceptedName(synonym), true);
  assert.deepEqual(plain(helpers.acceptedRecordFrom(synonym)).aphia_id, 6);
  assert.equal(helpers.acceptedRecordFrom(synonym).scientific_name, 'Aurelia aurita');
  assert.equal(helpers.getServiceFailure({ code: 'stale_revision', current_revision: 3 }).current_revision, 3);
  assert.equal(helpers.getServiceFailure({ detail: 'x' }), null);
  assert.equal(helpers.isWormsReferenceFailure('worms_reference_name_mismatch'), true);
  assert.equal(helpers.isWormsReferenceFailure('worms_reference_unavailable'), false);
});

// Source contracts ---------------------------------------------------------------

test('Administration keeps one desktop Species entry; disabled sections stay disabled', () => {
  assert.match(adminView, /\{ key: 'species', panelId: 'admin-species', label: 'adminTabSpecies', scope: 'aquarium' \}/);
  const disabled = adminView.match(/DISABLED_ADMIN_SECTIONS[^;]+;/s)[0];
  assert.doesNotMatch(disabled, /'species'/);
  for (const key of ['references', 'organizations']) assert.match(disabled, new RegExp(`'${key}'`));
  for (const key of ['environment', 'transfers']) assert.doesNotMatch(disabled, new RegExp(`'${key}'`));
  assert.match(adminView, /<LabConfigurationAdminSection\b/);
  assert.match(adminView, /displayedSection === 'transfers' && activeOrganizationId != null \? \(\s*<InstitutionalExchangesPage\b/);
  assert.doesNotMatch(adminView, /<(?:BoxSharingAdminSection|TransfersAdminSection)\b/);
  assert.match(adminView, /<SpeciesStrainsAdminSection\s+key=\{activeOrganizationId\}\s+target=\{speciesTarget\}/);
  assert.match(app, /species: '\/administration\/species-strains',/);
  assert.match(app, /if \(activeTab === 'admin' && !isDesktopApp\) \{\s*replaceRoute\(\{ tab: 'pilotage', boxCode: null, boxId: null \}, '\/', true\);\s*return;\s*\}/);
  assert.match(app, /canOpenAdmin=\{canUseAdmin && isDesktopApp\}/);
  // No legacy creation surface is mounted.
  assert.doesNotMatch(adminView + app, /<QuickStrainCreator\b/);
});

test('the workspace uses only institutional contracts and the server-side WoRMS search', () => {
  assert.match(section, /'\/api\/admin\/institution-species\/'/);
  assert.match(section, /`\/api\/admin\/institution-species\/\$\{id\}\/`/);
  assert.match(section, /`\/api\/admin\/institution-species\/historical\/\$\{speciesId\}\/`/);
  assert.match(section, /'\/api\/admin\/species-catalog\/worms-search\/'/);
  // The implicit resolve POST (profile/code persistence on opening a dialog) is retired.
  assert.doesNotMatch(section, /institution-species\/resolve/);
  assert.match(utilSource, /can_create_strain: boolean/);
  assert.match(utilSource, /strain_creation_issue: 'species_code_missing' \| 'species_code_unusable' \| 'species_code_inconsistent' \| null/);
  assert.match(utilSource, /InstitutionSpeciesDetail = InstitutionSpecies & SpeciesActionReadiness/);
  assert.match(utilSource, /HistoricalSpeciesDetail = Omit<HistoricalSpecies, 'requires_historical_adoption'> & SpeciesActionReadiness/);
  // Closed (410) creation and first-code endpoints and legacy taxonomy writes are gone.
  assert.doesNotMatch(section, /species-catalog\/species|species-catalog\/'|\/code\/|api\/taxonomy\//);
  assert.doesNotMatch(section, /speciesCodeGenerate|generateCode|Générer/);
  for (const file of readdirSync(new URL('../src/', import.meta.url), { recursive: true })) {
    if (!/\.(ts|tsx)$/.test(file)) continue;
    assert.doesNotMatch(read(`../src/${file.replaceAll('\\', '/')}`), /marinespecies\.org\/rest/, file);
  }
  // Strain creation, measurements and locations are out of scope here.
  assert.doesNotMatch(section, /\b(polyps?|ephyrae|polyp_count|ephyrae_count|thermal_zone|measured_on)\b/i);
});

test('the form is minimal: one scientific name, no code, no common names, no described flag', () => {
  // The Species dialog only; the Strain dialog follows it in the same file.
  const dialog = section.slice(section.indexOf('function SpeciesFormDialog'), section.indexOf('type LocationsState'));
  assert.equal((dialog.match(/<input\b/g) ?? []).length, 2);
  assert.doesNotMatch(section, /species_code: |setSpeciesCode|translations|is_described|type="checkbox"|common_name/);
});

test('all section keys exist in FR and EN, without middle dots or technical AAA terms', () => {
  const keys = new Set([...(section + strainsPanel).matchAll(/'((?:species|taxonomy|createBox)[A-Za-z]*)'/g)].map((match) => match[1])
    .filter((key) => /^(species[A-Z]|taxonomy[A-Z]|createBox[A-Z])/.test(key)));
  for (const key of ['adminTabSpecies', 'back', 'cancel', 'close', 'saving', 'loading']) keys.add(key);
  for (const key of keys) {
    for (const [name, catalogSource] of [['fr', fr], ['en', en]]) {
      const line = catalogSource.match(new RegExp(`^  ${key}: '(.*)',$`, 'm'));
      assert.ok(line, `${name}.${key}`);
      assert.doesNotMatch(line[1], /·|\bAAA\b|\bBBB\b/, `${name}.${key}`);
    }
  }
  assert.doesNotMatch(fr + en, /speciesCodeGenerate|speciesCatalogCreated/);
  assert.match(fr, /speciesCatalogForbidden: '.*Merci de contacter un administrateur\.'/);
  assert.match(en, /speciesCatalogForbidden: '.*Please contact your administrator\.'/);
  assert.match(fr, /speciesDetailEdit: 'Modifier l’espèce'/);
});

// Actual component through the harness, with transport replaced -----------------

const NESTED = new Set(['SpeciesRow', 'SpeciesDetail', 'SpeciesStrainsPanel', 'StrainItem', 'SpeciesFormDialog', 'StrainCreateDialog',
  'ConfirmActionModal',
  'SpeciesBoxRow', 'WormsRank', 'WormsStatus']);

function nodeText(node) {
  return [node, ...node.querySelectorAll('*')]
    .flatMap((item) => [].concat(item.props.children ?? []))
    .filter((child) => typeof child === 'string' || typeof child === 'number')
    .join(' ');
}

// Same text with the spaces between rendered fragments collapsed.
const flatText = (node) => nodeText(node).replace(/\s+/g, ' ').trim();

async function mount({
  target = null, t = (key) => key, boxes = [], boxCollectionStatus = 'loading', organizationId = 1, onCreateBox, language = 'fr',
} = {}) {
  const env = createBoxEnvironment();
  // Box status and date labels read the document language.
  env.document.documentElement ??= { lang: language };
  const client = env.load('../src/api/client.ts');
  const writes = [];
  for (const method of ['apiPost', 'apiPatch']) {
    client[method] = (path, payload) => {
      const request = { ...deferred(), method, path, payload };
      writes.push(request);
      return request.promise;
    };
  }
  const Section = env.load('../src/components/SpeciesStrainsAdminSection.tsx').default;
  const opened = [];
  const openedBoxes = [];
  let backs = 0;
  let collectionRetries = 0;
  const view = env.instance(Section, {
    target, t, organizationId, organizationName: 'Aquarium Test', language,
    boxes, boxCollectionStatus, onRetryBoxCollection: () => { collectionRetries++; },
    onOpenSpecies: (next) => opened.push(plain(next)), onBack: () => { backs++; },
    onOpenBox: (boxId, code) => openedBoxes.push([boxId, code]),
    ...(onCreateBox ? { onCreateBox } : {}),
  });
  const registered = new Set();
  let lastStrains = [];
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
  const api = {
    env, view, client, writes, opened, openedBoxes,
    get backs() { return backs; },
    get collectionRetries() { return collectionRetries; },
    all(name) { return view.nodes.filter((node) => (node.props.className ?? '').split(' ').includes(name)); },
    // Find the static heading by the Strain's displayed numeric order.
    strainHeading(code) {
      const { sortSpeciesStrains } = env.load('../src/utils/speciesCatalog.ts');
      const index = sortSpeciesStrains(lastStrains).findIndex((item) => item.code === code);
      return index < 0 ? undefined : api.all('species-strain-heading')[index];
    },
    async respond(path, payload, { error } = {}) {
      const request = [...env.requests].reverse().find((item) => item.path.startsWith(path) && !item.done);
      assert.ok(request, `Missing GET ${path}`);
      request.done = true;
      if (Array.isArray(payload?.strains)) lastStrains = payload.strains;
      if (error) request.reject(error); else request.resolve(payload);
      await view.settle();
      renderLeaves();
    },
    async answer(index, payload, { error } = {}) {
      const request = writes[index];
      if (error) request.reject(error); else request.resolve(payload);
      await view.settle();
      renderLeaves();
    },
    text() { return view.nodes.map(nodeText).join(' '); },
    button(label) { return view.nodes.find((node) => node.type === 'button'
      && (nodeText(node).includes(label) || node.props['aria-label'] === label)); },
    get input() { return view.nodes.find((node) => node.props.id === 'species-form-name' || node.props.id === 'species-form-search'); },
    get submitButton() { return view.byClass('species-form-submit'); },
    type(value) { view.change(api.input, value); renderLeaves(); },
    click(node) { view.click(node); renderLeaves(); },
    // A single text input also submits implicitly when there is no submit button.
    enter() {
      const defaultButton = view.form.querySelectorAll('*').find((node) => node.type === 'button' && node.props.type === 'submit');
      if (defaultButton?.disabled || (!defaultButton && !api.input)) return;
      let prevented = false;
      view.form.props.onSubmit({ preventDefault() { prevented = true; } });
      assert.equal(prevented, true, 'implicit submission must prevent native navigation');
      view.flush();
    },
    results() { return view.nodes.filter((node) => (node.props.className ?? '').split(' ').includes('species-worms-result')); },
    renderLeaves,
  };
  return api;
}

// Only detail responses carry readiness. Defaults model known fixture assignments;
// authority regressions below deliberately override them independently of labels.
const detailOf = (item, strains = []) => {
  const issue = item.species_code_issue === 'species_code_inconsistent' ? 'species_code_inconsistent'
    : !item.species_code ? 'species_code_missing'
      : /^[A-Z]{3}$/.test(item.species_code) ? null : 'species_code_unusable';
  return {
    can_create_strain: issue === null,
    strain_creation_issue: issue,
    ...('shared_scientific_name' in item ? {
      institution_species_id: null, scientific_name: item.shared_scientific_name,
      worms_aphia_id: null, worms_url: null,
    } : {}),
    ...item, strains,
  };
};
const strain = (id, code, extra = {}) => ({
  id, code, name: '', strain_number: null, active_box_count: 0, is_box_usable: true, is_owned: true,
  recorded_provenance_name: null, ...extra,
});

test('detail fixtures model server readiness without adding readiness fields to list responses', () => {
  for (const [code, issue] of [
    ['AFL', null], [null, 'species_code_missing'], ['', 'species_code_missing'],
    ['AB', 'species_code_unusable'], ['abC', 'species_code_unusable'],
  ]) {
    const item = profile(41, 'Aurelia aurita', { species_code: code });
    const detail = detailOf(item);
    assert.equal(detail.can_create_strain, issue === null);
    assert.equal(detail.strain_creation_issue, issue);
    assert.equal('can_create_strain' in item, false);
    assert.equal('strain_creation_issue' in item, false);
  }
  const inconsistent = detailOf(profile(41, 'Aurelia aurita', {
    species_code: 'AFL', species_code_issue: 'species_code_inconsistent',
  }));
  assert.equal(inconsistent.can_create_strain, false);
  assert.equal(inconsistent.strain_creation_issue, 'species_code_inconsistent');
});

test('list reads only the institutional API and shows quiet, compact rows', async () => {
  const h = await mount();
  assert.equal(h.env.requests[0].path, LIST_PATH);
  await h.respond(LIST_PATH, listResponse);
  const rows = h.all('species-list-row');
  assert.ok(rows.every((row) => row.type === 'li'));
  const links = rows.map((row) => row.querySelector('.species-list-name'));
  assert.ok(links.every((link) => link.type === 'a'));
  assert.deepEqual(links.map((link) => link.props.href), [
    '/administration/species-strains/historical/7', '/administration/species-strains/1', '/administration/species-strains/historical/8',
  ]);
  // Scientific names are italic; strain counts are real numbers, zero included; no code-generation action.
  assert.ok(links.every((link) => link.querySelector('*').type === 'em'));
  const strainCounts = rows.map((row) => row.querySelectorAll('.species-list-count')[0].props.children[0]);
  assert.deepEqual(strainCounts, [3, 2, 0]);
  assert.deepEqual(links.map(elementText), ['Aurelia aurita (AUR)', 'Cassiopea andromeda (CAN)', 'Rhizostoma pulmo']);
  assert.equal(h.all('species-list-worms').length, 0);
  assert.equal(h.all('species-list-code').length, 0);
  assert.equal(flatText(h.all('species-list-header')[0]), 'speciesLabel speciesDetailStrains speciesStrainActiveBoxes boxInventoryDates');
  assert.doesNotMatch(h.text(), /verified|pending|vérifié/i);
  assert.equal(h.all('species-list-kind').length, 0);
  assert.doesNotMatch(h.text(), /speciesCatalogHistorical|speciesDetailNoCode/);
  const menus = h.view.nodes.filter((node) => node.type?.name === 'RowActionMenu');
  assert.equal(menus.length, 3);
  assert.ok(menus.every((menu) => menu.children.length === 0), 'menu remains an opaque leaf');
  assert.deepEqual(menus.map((menu) => plain(menu.props.actions)), Array.from({ length: 3 }, () => [{ action: 'open', label: 'speciesCreateShowExisting' }]));
  menus[1].props.onAction('open');
  assert.deepEqual(h.opened, [{ kind: 'profile', id: 1 }]);
  const toolbarButtons = h.view.nodes.filter((node) => node.type === 'button');
  assert.deepEqual(toolbarButtons.map(nodeText), ['', 'speciesCatalogNewSpecies']);
  assert.equal(toolbarButtons[0].props['aria-label'], 'speciesCatalogRefresh');
  h.view.change(h.view.nodes.find((node) => node.type === 'input'), 'rhizo');
  assert.equal(h.all('species-list-name').length, 1);
  h.view.change(h.view.nodes.find((node) => node.type === 'input'), 'zzz');
  assert.match(h.text(), /speciesCatalogNoResults/);
});

test('species without a resolvable code share one compact alert; each stays openable', async () => {
  const h = await mount();
  await h.respond(LIST_PATH, { ...listResponse, historical_species: [...listResponse.historical_species,
    historical(9, 'Obelia sp.'), historical(10, 'Pennaria disticha')] });
  const alerts = h.view.nodes.filter((node) => node.props.role === 'alert');
  assert.equal(alerts.length, 1, 'one alert, not one banner per Species');
  assert.equal(alerts[0].props.className, 'species-code-issues');
  assert.equal(alerts.filter((node) => /speciesCodeDataIssue/.test(nodeText(node))).length, 1);
  const links = alerts[0].querySelectorAll('*').filter((node) => node.type === 'a');
  assert.deepEqual(links.map(elementText), ['Obelia sp.', 'Pennaria disticha', 'Rhizostoma pulmo']);
  assert.deepEqual(links.map((link) => link.props.href), ['/administration/species-strains/historical/9',
    '/administration/species-strains/historical/10', '/administration/species-strains/historical/8']);
  // Still listed while the catalog search hides those rows.
  h.view.change(h.view.nodes.find((node) => node.type === 'input'), 'zzz');
  assert.equal(h.view.nodes.filter((node) => node.props.role === 'alert')[0].querySelectorAll('*').filter((node) => node.type === 'a').length, 3);
  let prevented = false;
  links[1].props.onClick({ button: 0, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(h.opened, [{ kind: 'historical', id: 10 }]);
  assert.equal(h.writes.length, 0, 'the alert offers no code mutation');
});

test('opening a row navigates in-app; modified clicks keep the browser link', async () => {
  const h = await mount();
  await h.respond(LIST_PATH, listResponse);
  const row = h.view.nodes.find((node) => node.props.href === '/administration/species-strains/1');
  let prevented = false;
  row.props.onClick({ button: 0, preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  row.props.onClick({ button: 0, ctrlKey: true, preventDefault() { throw new Error('must not prevent'); } });
  assert.deepEqual(h.opened, [{ kind: 'profile', id: 1 }]);
});

test('empty, forbidden and error states are distinct', async () => {
  let h = await mount();
  await h.respond(LIST_PATH, { institution_species: [], historical_species: [] });
  assert.match(h.text(), /speciesCatalogEmpty/);
  h = await mount();
  await h.respond(LIST_PATH, null, { error: new h.client.ApiError(403, 'no') });
  assert.match(h.text(), /speciesCatalogForbidden/);
  assert.equal(h.button('speciesCatalogNewSpecies'), undefined);
  h = await mount();
  await h.respond(LIST_PATH, null, { error: new h.client.ApiError(500, 'no') });
  assert.match(h.text(), /speciesCatalogLoadError/);
  h.click(h.button('taxonomyRetry'));
  assert.equal(h.env.requests.at(-1).path, LIST_PATH);
});

test('profile detail is an entity page: name with its code, WoRMS link, strains, one identification action', async () => {
  const h = await mount({ target: { kind: 'profile', id: 1 } });
  assert.equal(h.env.requests[0].path, '/api/admin/institution-species/1/');
  await h.respond('/api/admin/institution-species/1/', detailOf(listResponse.institution_species[0], [
    strain(10, 'CAN-FRA-1', { strain_number: 1, name: 'Souche Paris', active_box_count: 0 }),
    strain(11, 'legacy', { is_owned: false, is_box_usable: false, active_box_count: 2 }),
  ]));
  const header = h.view.byClass('entity-header');
  assert.match(header.props.className, /entity-header--species/);
  assert.equal(nodeText(header.querySelector('.box-page-label')), 'speciesDetailLabel');
  // The institutional code sits beside the scientific name, in the title itself.
  const title = h.view.nodes.find((node) => node.type === 'h2');
  assert.equal(flatText(title), 'Cassiopea andromeda (CAN)');
  assert.equal(h.view.nodes.find((node) => node.type === 'a').props.href, listResponse.institution_species[0].worms_url);
  // No separate identification card, definition list or code row in the header.
  assert.equal(header.querySelectorAll('*').filter((node) => node.type === 'dl').length, 0);
  // Incomplete collections never publish a Box total.
  assert.doesNotMatch(h.text(), /createBoxActiveBoxes/);
  assert.doesNotMatch(nodeText(header), /speciesDetailBox/);
  // Only the operational box-usability note is shown, in the opened strain;
  // a missing owning institution is not reported on the institutional sheet.
  assert.match(h.text(), /speciesCatalogNotBoxUsable/);
  assert.doesNotMatch(h.text(), /speciesStrainUnowned|Unowned|propriétaire/);
  assert.equal(h.view.nodes.filter((node) => node.type === 'button' && nodeText(node).includes('speciesDetailIdentification')).length, 1);
  assert.equal(h.view.nodes.filter((node) => node.type === 'input').length, 0, 'no permanent form');
  const back = h.view.nodes.find((node) => node.type?.name === 'DetailBackButton');
  back.props.onBack();
  assert.equal(h.backs, 1);
});

test('a strain shows its recorded biological provenance only when the server provides it', async () => {
  const t = (key) => (key === 'speciesStrainRecordedProvenance' ? 'Provenance: {name}' : key);
  const h = await mount({ target: { kind: 'profile', id: 1 }, t });
  await h.respond('/api/admin/institution-species/1/', detailOf(listResponse.institution_species[0], [
    strain(10, 'CAN-WLD-1', { strain_number: 1, recorded_provenance_name: 'Recorded wild provenance' }),
    strain(11, 'CAN-FRA-2', { strain_number: 2 }),
  ]));
  const provenance = h.all('species-strain-provenance').map(nodeText);
  assert.deepEqual(provenance, ['adminCountry : speciesStrainNoProvenance', 'adminCountry : speciesStrainNoProvenance']);
  assert.match(h.text(), /speciesStrainLocation Recorded wild provenance/, 'legacy place is retained, never presented as a country');
  // Distinct locations are never merged into one origin in the header.
  assert.doesNotMatch(nodeText(h.view.byClass('entity-header')), /Provenance/);
  // Every section is already visible; an absent country stays explicitly unrecorded.
  assert.match(nodeText(h.strainHeading('CAN-FRA-2')), /speciesStrainNoProvenance/);
  // Viewing never offers a strain edition action: no write contract exists for it.
  assert.equal(h.view.nodes.filter((node) => node.type === 'button' && /Modifier|taxonomyEdit|speciesStrainEdit/.test(nodeText(node))).length, 0);
  assert.equal(h.view.nodes.filter((node) => node.type === 'input').length, 0);
});

test('strain creation and eligible heading actions do not depend on a WoRMS reference', async () => {
  for (const aphiaId of [null, 135306]) {
    const createdBoxes = [];
    const h = await mount({ target: { kind: 'profile', id: 1 }, onCreateBox: id => createdBoxes.push(id) });
    await h.respond('/api/admin/institution-species/1/', detailOf(profile(1, 'Cassiopea andromeda', {
      species_code: 'CAN', worms_aphia_id: aphiaId,
    }), [strain(42, 'CAN-FRA-1', { strain_number: 1 }), strain(43, 'legacy', { is_box_usable: false })]));
    assert.equal(h.button('speciesStrainNew').props.disabled, false);
    assert.equal(h.button('speciesDetailIdentification').props.disabled, false);
    h.click(h.button('speciesDetailIdentification'));
    assert.equal(h.input.props.value, 'Cassiopea andromeda');
    h.click(h.view.byClass('box-dialog-close'));
    const actions = h.view.nodes.filter(node => node.type === 'button' && nodeText(node).trim() === 'createBoxNew');
    assert.equal(actions.length, 1, 'usable strain without Boxes still offers creation');
    h.click(actions[0]);
    assert.deepEqual(createdBoxes, [42]);
    assert.equal(h.button('speciesCodeChangeOpen'), undefined);
    assert.ok(!h.view.nodes.some(node => node.type?.name === 'SpeciesCodeChangePreview'));
    h.click(h.button('speciesStrainNew'));
    assert.equal(h.env.requests.at(-1).path, '/api/admin/collection-locations/');
    assert.equal(h.env.requests.some(request => request.path.startsWith(WORMS_PATH)), false);
    assert.equal(h.writes.length, 0, 'ready profile actions open dialogs directly, without resolve');
  }
});

test('missing or malformed institutional AAA and forbidden detail block strain creation without writes', async () => {
  for (const code of [null, '', 'AB', 'abcd', 'abC']) {
    const h = await mount({ target: { kind: 'profile', id: 1 } });
    await h.respond('/api/admin/institution-species/1/', detailOf(profile(1, 'Uncoded species', { species_code: code })));
    assert.equal(h.button('speciesStrainNew').props.disabled, true);
    assert.equal(h.button('speciesStrainNew').props['aria-describedby'], 'species-code-issue');
    assert.equal(h.button('speciesDetailIdentification').props.disabled, false);
    assert.match(h.text(), code ? /speciesStrainSpeciesCodeUnusable/ : /speciesStrainSpeciesCodeMissing/);
    h.button('speciesStrainNew').props.onClick();
    await h.view.settle();
    assert.equal(strainDialog(h), undefined);
    h.click(h.button('speciesDetailIdentification'));
    assert.equal(h.input.props.value, 'Uncoded species');
    assert.equal(h.writes.length, 0, 'existing profiles edit directly even without a usable code');
  }
  const h = await mount({ target: { kind: 'profile', id: 1 }, onCreateBox() {} });
  await h.respond('/api/admin/institution-species/1/', null, { error: new h.client.ApiError(403, 'Forbidden') });
  assert.equal(h.button('speciesStrainNew'), undefined);
  assert.equal(h.button('createBoxNew'), undefined);
});

for (const [speciesId, extra, strains] of [
  [8, {}, []],
  [7, { species_code: 'AHA', strain_count: 1, can_create_strain: false, strain_creation_issue: 'species_code_missing' },
    [strain(42, 'AHA-ARG-1')]],
  [7, { species_code: 'AUR', can_create_strain: true, strain_creation_issue: null }, []],
]) {
  test(`historical ${speciesId}/${extra.strain_creation_issue}: read-only, actions never persist a profile or code`, async () => {
    const h = await mount({ target: { kind: 'historical', id: speciesId }, boxCollectionStatus: 'ready' });
    await h.respond(`/api/admin/institution-species/historical/${speciesId}/`, detailOf(
      historical(speciesId, 'Acromitus hardenbergi', extra), strains,
    ));
    for (const label of ['speciesStrainNew', 'speciesDetailIdentification']) {
      const button = h.button(label);
      assert.equal(button.props.disabled, true);
      assert.equal(button.props['aria-describedby'], 'species-historical-readonly');
      button.props.onClick();
      await h.view.settle();
    }
    assert.match(h.text(), /speciesDetailActionsUnavailable/);
    assert.doesNotMatch(h.text(), /speciesStrainSpeciesCodeMissing/);
    assert.equal(h.input, undefined, 'no edit dialog opens');
    assert.equal(strainDialog(h), undefined, 'no strain dialog opens');
    assert.equal(h.writes.length, 0, 'browsing and clicking a historical species never writes');
    assert.equal(h.env.requests.length, 1, 'only the read-only detail GET');
  });
}

test('a foreign or missing detail is not found, without data', async () => {
  const h = await mount({ target: { kind: 'profile', id: 99 } });
  await h.respond('/api/admin/institution-species/99/', null, { error: new h.client.ApiError(404, 'no') });
  assert.match(h.text(), /speciesDetailNotFound/);
  assert.equal(h.view.nodes.find((node) => node.type === 'h2'), undefined);
});

async function openCreate() {
  const h = await mount();
  await h.respond(LIST_PATH, listResponse);
  h.click(h.button('speciesCatalogNewSpecies'));
  return h;
}

test('empty search validates locally; manual entry is explicit and never saves on Enter', async () => {
  const h = await openCreate();
  const reads = h.env.requests.length;
  h.enter();
  assert.equal(h.env.requests.length, reads);
  assert.match(h.text(), /speciesCreateWormsInvalidQuery/);
  h.type('Aurelia sp. 2');
  assert.equal(h.submitButton, undefined);
  h.click(h.button('speciesSearchManual'));
  assert.equal(h.input.props.id, 'species-form-name');
  assert.equal(h.submitButton.disabled, false);
  assert.equal(h.writes.length, 0);
  h.enter();
  assert.equal(h.writes.length, 0);
  h.click(h.submitButton);
  assert.deepEqual(plain(h.writes[0].payload), { scientific_name: 'Aurelia sp. 2' });
});

test('manual final name has no side search; Enter is inert and returning to search preserves the name and focus', async () => {
  const h = await openCreate();
  h.type('Aurelia coerulea');
  h.click(h.button('speciesSearchManual'));
  assert.equal(h.env.document.activeElement, h.input);
  assert.equal(h.input.props.value, 'Aurelia coerulea');
  assert.equal(h.all('species-worms-button').length, 0);
  assert.equal(h.all('species-name-row')[0].props.className, 'species-name-row');
  assert.match(h.text(), /speciesSearchManualName/);
  assert.equal(h.button('speciesSearchChange'), undefined);
  const reads = h.env.requests.length;
  h.enter();
  assert.equal(h.env.requests.length, reads, 'Enter must not launch a hidden search');
  assert.equal(h.writes.length, 0);
  h.click(h.button('speciesCreateWormsSearch'));
  assert.equal(h.input.props.id, 'species-form-search');
  assert.equal(h.input.props.value, 'Aurelia coerulea');
  assert.equal(h.env.document.activeElement, h.input);
  assert.equal(h.submitButton, undefined);
  assert.equal(h.env.requests.length, reads, 'returning does not search automatically');
  h.enter();
  assert.equal(h.env.requests.length, reads + 1);
});

test('selected final name has no side search; Enter is inert and changing species keeps its reference explicit', async () => {
  const h = await openCreate();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: false, limit: 25, results: [wormsRecord(287206, 'Aurelia coerulea')] });
  h.click(h.results()[0]);
  assert.equal(h.env.document.activeElement, h.input);
  assert.equal(h.all('species-worms-button').length, 0);
  assert.equal(h.button('speciesCreateWormsSearch'), undefined);
  assert.match(h.text(), /speciesCreateWormsReference\s+287206/);
  const reads = h.env.requests.length;
  h.enter();
  assert.equal(h.env.requests.length, reads);
  assert.equal(h.writes.length, 0);
  h.type('Aurelia coerulea alba');
  assert.match(h.text(), /speciesWormsReferenceMismatch/);
  assert.equal(h.submitButton.disabled, true);
  h.enter();
  assert.equal(h.env.requests.length, reads);
  h.click(h.button('speciesSearchChangeSpecies'));
  assert.equal(h.input.props.value, 'Aurelia coerulea alba');
  assert.equal(h.env.document.activeElement, h.input);
  assert.equal(h.submitButton, undefined);
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia coerulea alba', results: [wormsRecord(9, 'Aurelia coerulea alba')] });
  h.click(h.results()[0]);
  assert.match(h.text(), /speciesCreateWormsReference\s+9/);
  assert.equal(h.submitButton.disabled, false);
});

test('exact species first, other ranks hidden, synonym accepted name requires a second choice', async () => {
  const h = await openCreate();
  h.type('Aurelia aurita');
  h.enter();
  assert.match(h.text(), /speciesCreateWormsSearching/);
  assert.equal(h.submitButton, undefined);
  await h.respond(WORMS_PATH, { query: 'Aurelia aurita', truncated: false, limit: 25, results: [
    wormsRecord(1, 'Aurelia', { rank: 'Genus', rank_id: 180, is_species_level: false }),
    wormsRecord(2, 'Aurelia aurita alba', { rank: 'Subspecies', rank_id: 230 }),
    wormsRecord(3, 'Aurelia aurita', { status: 'unaccepted', valid_aphia_id: 4, valid_name: 'Aurelia coerulea' }),
    wormsRecord(5, 'Aurelia aurita beta'),
  ] });
  assert.deepEqual(h.results().map((node) => nodeText(node).match(/Aurelia [a-z ]+/)[0].trim()), [
    'Aurelia aurita', 'Aurelia aurita beta', 'Aurelia aurita alba',
  ]);
  assert.equal(h.view.nodes.find((node) => node.type === 'details'), undefined);
  assert.doesNotMatch(h.text(), /speciesSearchOtherRanks|speciesCreateAboveSpeciesRank|speciesCreateRankGenus/);
  h.click(h.results()[0]);
  assert.equal(h.input.props.value, 'Aurelia aurita');
  assert.match(h.text(), /speciesSearchSelected/);
  assert.equal(h.writes.length, 0);
  h.click(h.button('speciesCreateUseAcceptedName'));
  assert.equal(h.input.props.value, 'Aurelia coerulea');
  assert.equal(h.writes.length, 0);
  h.click(h.submitButton);
  assert.deepEqual(plain(h.writes[0].payload), { scientific_name: 'Aurelia coerulea', worms_aphia_id: 4 });
});

test('non-species-only results stay hidden and the manual alternative remains in the footer', async () => {
  const h = await openCreate();
  h.type('Fade');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Fade', truncated: false, limit: 25, results: [
    wormsRecord(1, 'Fadeewella', { rank: 'Genus', rank_id: 180, is_species_level: false }),
    wormsRecord(2, 'Fadejewobdella', { rank: 'Genus', rank_id: 180, is_species_level: false }),
    wormsRecord(3, 'Fadyenia', { rank: 'Genus', rank_id: 180, is_species_level: false }),
  ] });
  assert.equal(h.results().length, 0);
  assert.doesNotMatch(h.text(), /Fadeewella|Fadejewobdella|Fadyenia|speciesSearchOtherRanks|speciesCreateAboveSpeciesRank/);
  assert.match(h.text(), /speciesCreateWormsEmpty/);
  const footer = h.view.nodes.find(node => node.type === 'footer');
  assert.match(nodeText(footer), /speciesSearchManual/);
  const dialog = h.view.byClass('species-form-dialog');
  assert.equal(dialog.querySelectorAll('*').filter(node => node.type === 'button' && node.props.className?.includes('primary-button')).length, 1);
  h.click(h.button('speciesSearchManual'));
  assert.equal(h.input.props.value, 'Fade');
  assert.equal(h.writes.length, 0);
});

test('creation dialog: focused empty name, Enter searches WoRMS and never creates', async () => {
  const h = await openCreate();
  assert.equal(h.input.props.value, '');
  assert.equal(h.input.props.placeholder, 'speciesCreateNamePlaceholder');
  assert.equal(h.env.document.activeElement, h.input);
  assert.equal(h.submitButton, undefined);
  assert.equal(nodeText(h.button('speciesCreateWormsSearch')).trim(), 'speciesCreateWormsSearch');
  h.type('Aurelia coerulea');
  h.enter();
  assert.equal(h.writes.length, 0);
  assert.equal(h.env.requests.at(-1).path, `${WORMS_PATH}?q=Aurelia+coerulea`);
  assert.equal(h.submitButton, undefined);
});

test('creation: explicit WoRMS selection, generated code shown on the new detail, list refreshed', async () => {
  const h = await openCreate();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: true, limit: 25, results: [
    wormsRecord(135263, 'Aurelia', { rank: 'Genus', rank_id: 180, is_species_level: false }),
    wormsRecord(287206, 'Aurelia coerulea'),
  ] });
  assert.match(h.text(), /speciesCreateWormsTruncated|25/);
  // Non-species ranks are not displayed or selectable.
  assert.equal(h.results().filter((node) => node.type === 'button').length, 1);
  assert.doesNotMatch(h.text(), /speciesCreateAboveSpeciesRank|speciesCreateRankGenus|speciesSearchOtherRanks/);
  h.click(h.results().find((node) => node.type === 'button'));
  assert.equal(h.input.props.value, 'Aurelia coerulea');
  assert.equal(h.writes.length, 0, 'selection never saves');
  h.click(h.submitButton);
  assert.deepEqual(plain({ method: h.writes[0].method, path: h.writes[0].path, payload: h.writes[0].payload }), { method: 'apiPost', path: LIST_PATH, payload: { scientific_name: 'Aurelia coerulea', worms_aphia_id: 287206 } });
  const listReads = h.env.requests.filter((item) => item.path === LIST_PATH).length;
  await h.answer(0, profile(5, 'Aurelia coerulea', { species_code: 'ACO', worms_aphia_id: 287206 }));
  assert.deepEqual(h.opened, [{ kind: 'profile', id: 5 }]);
  assert.equal(h.env.requests.filter((item) => item.path === LIST_PATH).length, listReads + 1);
  assert.equal(h.input, undefined, 'dialog closed');
  h.view.setProps({ target: { kind: 'profile', id: 5 } });
  h.renderLeaves();
  await h.respond('/api/admin/institution-species/5/', detailOf(profile(5, 'Aurelia coerulea', { species_code: 'ACO' })));
  assert.match(h.text(), /ACO/);
  assert.match(h.text(), /speciesDetailCreated/);
  assert.ok(h.view.nodes.some((node) => node.props.className === 'species-detail-message'));
});

test('an adjusted name cannot keep the selected reference; removal is explicit', async () => {
  const h = await openCreate();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: false, limit: 25, results: [wormsRecord(287206, 'Aurelia coerulea')] });
  h.click(h.results()[0]);
  h.type('Aurelia coerulea alba');
  assert.match(h.text(), /speciesWormsReferenceMismatch/);
  assert.equal(h.submitButton.disabled, true);
  h.click(h.button('speciesCreateRemoveReference'));
  assert.equal(h.submitButton.disabled, false);
  h.click(h.submitButton);
  assert.deepEqual(plain(h.writes[0].payload), { scientific_name: 'Aurelia coerulea alba' });
});

test('stale WoRMS responses are ignored after the name changes', async () => {
  const h = await openCreate();
  h.type('Aurelia');
  h.enter();
  const pending = h.env.requests.at(-1);
  h.type('Cassiopea');
  pending.resolve({ query: 'Aurelia', truncated: false, limit: 25, results: [wormsRecord(287206, 'Aurelia coerulea')] });
  await h.view.settle();
  assert.equal(h.results().length, 0);
});

test('results already in this institution open that species instead of being selected', async () => {
  const h = await openCreate();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: false, limit: 25, results: [wormsRecord(135306, 'Aurelia aurita', {
    local_species: { institution_species_id: null, species_id: 7, scientific_name: 'Aurelia aurita', requires_historical_adoption: true },
  })] });
  assert.match(h.text(), /speciesCreateAlreadyRecorded/);
  h.click(h.results()[0]);
  assert.equal(h.input.props.value, 'Aurelia');
  assert.match(h.text(), /speciesCreateHistoricalAdoption/);
  h.click(h.button('speciesCreateShowExisting'));
  assert.deepEqual(h.opened, [{ kind: 'historical', id: 7 }]);
  assert.equal(h.writes.length, 0);
});

test('creation refusals: adoption required, existing profile, unavailable WoRMS fallback, permission', async () => {
  let h = await openCreate();
  h.type('Aurelia aurita');
  h.click(h.button('speciesSearchManual'));
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', {
    code: 'historical_adoption_required', species_id: 7, shared_scientific_name: 'Aurelia aurita', species_code: 'AUR',
  }) });
  assert.match(h.text(), /speciesCreateHistoricalAdoption/);
  assert.doesNotMatch(h.text(), /speciesCodeGenerate/);
  h.click(h.button('speciesCreateShowExisting'));
  assert.deepEqual(h.opened, [{ kind: 'historical', id: 7 }]);

  h = await openCreate();
  h.type('Cassiopea andromeda');
  h.click(h.button('speciesSearchManual'));
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', {
    code: 'institution_species_exists', institution_species_id: 1, species_id: 101, scientific_name: 'Cassiopea andromeda', species_code: 'CAN',
  }) });
  assert.match(h.text(), /speciesCreateExists/);

  h = await openCreate();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: false, limit: 25, results: [wormsRecord(287206, 'Aurelia coerulea')] });
  h.click(h.results()[0]);
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(503, 'x', { code: 'worms_reference_unavailable' }) });
  h.click(h.button('speciesCreateWithoutReference'));
  assert.deepEqual(plain(h.writes[1].payload), { scientific_name: 'Aurelia coerulea' });

  h = await openCreate();
  h.type('Aurelia coerulea');
  h.click(h.button('speciesSearchManual'));
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(403, 'x', { detail: 'no' }) });
  assert.match(h.text(), /speciesCatalogActionForbidden/);
});

test('WoRMS unavailable during search keeps an explicit manual path', async () => {
  const h = await openCreate();
  h.type('Aurelia coerulea');
  h.enter();
  await h.respond(WORMS_PATH, null, { error: new h.client.ApiError(503, 'x', { code: 'worms_unavailable' }) });
  assert.match(h.text(), /speciesCreateWormsUnavailable/);
  assert.equal(h.submitButton, undefined);
  h.click(h.button('speciesSearchManual'));
  assert.equal(h.submitButton.disabled, false);
});

test('Escape closes the dialog without saving', async () => {
  const h = await openCreate();
  h.type('Aurelia coerulea');
  h.env.key('Escape');
  h.view.flush();
  assert.equal(h.input, undefined);
  assert.equal(h.writes.length, 0);
});

for (const outcome of ['success', 'error']) {
  test(`late registration ${outcome} after unmount cannot navigate, refresh or publish feedback`, async () => {
    const h = await openCreate();
    h.type('Aurelia coerulea');
    h.click(h.button('speciesSearchManual'));
    h.click(h.submitButton);
    assert.equal(h.writes.length, 1);
    const reads = h.env.requests.length;
    h.view.unmount();
    if (outcome === 'success') h.writes[0].resolve(profile(5, 'Aurelia coerulea', { species_code: 'ACO' }));
    else h.writes[0].reject(new h.client.ApiError(403, 'late refusal'));
    await h.view.settle();
    assert.deepEqual(h.opened, [], 'a completed registration must not navigate from an abandoned page');
    assert.equal(h.env.requests.length, reads, 'no late list refresh');
    // The shared mutation hook still resets its busy flag in finally; no business state may publish in addition.
    assert.equal(h.view.updatesAfterUnmount, 1, 'only the shared busy reset, no saved callback or error feedback');
  });
}

async function openEdit(item = profile(1, 'Aurelia aurita', { worms_aphia_id: 135306, worms_url: 'https://example.invalid/135306', species_code: 'AUR', revision: 4 })) {
  const h = await mount({ target: { kind: 'profile', id: item.id } });
  await h.respond(`/api/admin/institution-species/${item.id}/`, detailOf(item, [strain(10, 'AUR-FRA-1')]));
  h.click(h.button('speciesDetailIdentification'));
  return h;
}

test('edit: prefilled, unchanged cannot be saved, manual correction requires removing the old reference', async () => {
  const h = await openEdit();
  assert.equal(h.button('speciesCodeChangeOpen'), undefined);
  assert.equal(h.input.props.value, 'Aurelia aurita');
  assert.match(h.text(), /135306/);
  assert.equal(h.submitButton.disabled, true);
  assert.ok(h.button('speciesEditWormsCheck'));
  h.type('Aurelia coerulea');
  assert.match(h.text(), /speciesWormsReferenceMismatch/);
  assert.equal(h.submitButton.disabled, true);
  h.click(h.button('speciesCreateRemoveReference'));
  h.click(h.submitButton);
  assert.deepEqual(plain({ method: h.writes[0].method, path: h.writes[0].path, payload: h.writes[0].payload }), {
    method: 'apiPatch', path: '/api/admin/institution-species/1/',
    payload: { expected_revision: 4, scientific_name: 'Aurelia coerulea', worms_aphia_id: null },
  });
  await h.answer(0, profile(1, 'Aurelia coerulea', { species_code: 'AUR', revision: 5 }));
  assert.equal(flatText(h.view.nodes.find((node) => node.type === 'h2')), 'Aurelia coerulea (AUR)');
  assert.doesNotMatch(h.text(), /speciesDetailUpdated/);
  assert.equal(h.button('speciesCodeChangeOpen'), undefined);
  assert.ok(!h.view.nodes.some((node) => node.type?.name === 'SpeciesCodeChangePreview'), 'preview remains unmounted');
  // The code is unchanged and strains are kept.
  assert.match(h.text(), /AUR-FRA-1/);
});

test('edit: adopting a WoRMS proposal for the same profile sends its AphiaID', async () => {
  const h = await openEdit();
  h.type('Aurelia');
  h.enter();
  await h.respond(WORMS_PATH, { query: 'Aurelia', truncated: false, limit: 25, results: [
    wormsRecord(287206, 'Aurelia coerulea', { local_species: { institution_species_id: 1, species_id: 101, scientific_name: 'Aurelia aurita', requires_historical_adoption: false } }),
  ] });
  assert.doesNotMatch(h.text(), /speciesCreateAlreadyRecorded/, 'not recorded against itself');
  h.click(h.results()[0]);
  assert.equal(h.input.props.value, 'Aurelia coerulea');
  h.click(h.submitButton);
  assert.deepEqual(plain(h.writes[0].payload), { expected_revision: 4, scientific_name: 'Aurelia coerulea', worms_aphia_id: 287206 });
});

test('edit: a stale revision is a conflict requiring an explicit reload', async () => {
  const h = await openEdit();
  h.click(h.button('speciesCreateRemoveReference'));
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', { code: 'stale_revision', current_revision: 5 }) });
  assert.match(h.text(), /speciesEditStale/);
  const reads = h.env.requests.length;
  h.click(h.button('speciesEditReload'));
  assert.equal(h.input, undefined);
  assert.equal(h.env.requests.length, reads + 1);
  assert.equal(h.env.requests.at(-1).path, '/api/admin/institution-species/1/');
  assert.equal(h.writes.length, 1, 'never retried silently');
});

test('edit: name collisions are reported, never overwritten', async () => {
  const h = await openEdit();
  h.type('Cassiopea andromeda');
  h.click(h.button('speciesCreateRemoveReference'));
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', { code: 'institution_species_name_exists', institution_species_id: 2, scientific_name: 'Cassiopea andromeda' }) });
  assert.match(h.text(), /speciesEditNameExists/);
  assert.ok(h.input, 'values kept after a recoverable error');
});

// Compatibility actions use the existing dialogs, without changing the URL ----

async function openCompatibility(extra = {}, { speciesId = 7, strains = [strain(10, 'AUR-FRA-1')], ...options } = {}) {
  const h = await mount({ ...options, target: { kind: 'historical', id: speciesId } });
  await h.respond(`${LIST_PATH}historical/${speciesId}/`, detailOf(historical(speciesId, 'Aurelia aurita', {
    species_code: 'AUR', ...extra,
  }), strains));
  return h;
}

const resolvedProfile = (extra = {}) => detailOf(profile(41, 'Aurelia aurita', {
  species_id: 7, species_code: 'AUR', revision: 6, ...extra,
}), [strain(10, 'AUR-FRA-1')]);

for (const [code, allowed, issue] of [
  ['AUR', false, 'species_code_missing'],
  ['AUR', false, 'species_code_inconsistent'],
  ['AUR', false, 'species_code_unusable'],
  ['ab', false, 'species_code_unusable'],
  [null, false, 'species_code_missing'],
  ['AUR', false, null],
  [null, true, null],
  ['ab', true, null],
]) {
  test(`profile: server readiness ${allowed}/${issue} alone controls strain action despite display ${code}`, async () => {
    const extra = { species_code: code, can_create_strain: allowed, strain_creation_issue: issue };
    const h = await mount({ target: { kind: 'profile', id: 41 } });
    await h.respond(`${LIST_PATH}41/`, resolvedProfile(extra));
    assert.equal(h.button('speciesStrainNew').props.disabled, !allowed);
    assert.equal(h.button('speciesDetailIdentification').props.disabled, false);
    if (!allowed) {
      h.button('speciesStrainNew').props.onClick();
      await h.view.settle();
      assert.equal(strainDialog(h), undefined, 'the handler also blocks a non-ready profile');
    }
    h.click(h.button('speciesDetailIdentification'));
    assert.equal(h.input.props.value, 'Aurelia aurita', 'editing an established profile stays available');
    h.click(h.view.byClass('box-dialog-close'));
    assert.equal(h.input, undefined);
    assert.equal(h.writes.length, 0, 'opening and cancelling the edit dialog writes nothing');
  });
}

test('established profile: open then cancel the strain dialog writes nothing', async () => {
  const h = await mount({ target: { kind: 'profile', id: 41 } });
  await h.respond(`${LIST_PATH}41/`, resolvedProfile());
  h.click(h.button('speciesStrainNew'));
  assert.ok(strainDialog(h));
  await h.respond(LOCATIONS_PATH, { collection_locations: [], countries: COUNTRIES });
  h.click(h.view.byClass('box-dialog-close'));
  assert.equal(strainDialog(h), undefined);
  assert.equal(h.writes.length, 0);
});

test('established profile strain dialog sends the actual creation payload to the profile', async () => {
  const h = await mount({ target: { kind: 'profile', id: 41 } });
  await h.respond(`${LIST_PATH}41/`, resolvedProfile());
  h.click(h.button('speciesStrainNew'));
  await h.respond(LOCATIONS_PATH, { collection_locations: [location(3, 'FRA', 'France', 'FR')], countries: COUNTRIES });
  set(h, 'adminCountry', 'FR');
  h.click(h.submitButton);
  assert.equal(h.writes.length, 1);
  assert.equal(h.writes[0].method, 'apiPost');
  assert.equal(h.writes[0].path, `${LIST_PATH}41/strains/`);
  assert.deepEqual(plain(withoutKey(h.writes[0].payload)), { country: 'FR' });
});

for (const [language, source, notice] of [
  ['fr', fr, /lecture seule/],
  ['en', en, /read-only/],
]) {
  test(`${language}: historical sheet shows a translated read-only notice`, async () => {
    const exports = {};
    vm.runInNewContext(ts.transpileModule(source, {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    }).outputText, { exports });
    const catalog = exports[language];
    const t = key => {
      assert.equal(typeof catalog[key], 'string', `${language}.${key}`);
      return catalog[key];
    };
    const h = await openCompatibility({}, { language, t });
    assert.match(h.text(), notice);
    assert.equal(h.writes.length, 0);
  });
}

test('old URL reload fetches the linked profile before showing its local name and revision-protected editor', async () => {
  const h = await openCompatibility({ institution_species_id: 41, scientific_name: 'Alias name is not authoritative' });
  assert.equal(h.env.requests.at(-1).path, `${LIST_PATH}41/`);
  assert.equal(h.input, undefined);
  assert.equal(h.button('speciesDetailIdentification'), undefined);
  assert.equal(h.view.nodes.find(node => node.type === 'h2'), undefined);
  await h.respond(`${LIST_PATH}41/`, resolvedProfile({ scientific_name: 'Aurelia localis', revision: 12 }));
  assert.equal(flatText(h.view.nodes.find(node => node.type === 'h2')), 'Aurelia localis (AUR)');
  assert.equal(h.writes.length, 0, 'following an alias is read-only');
  h.click(h.button('speciesDetailIdentification'));
  assert.equal(h.input.props.value, 'Aurelia localis');
  h.type('Aurelia coerulea');
  h.click(h.submitButton);
  assert.equal(h.writes[0].path, `${LIST_PATH}41/`);
  assert.deepEqual(plain(h.writes[0].payload), {
    expected_revision: 12, scientific_name: 'Aurelia coerulea', worms_aphia_id: null,
  });
  assert.deepEqual(h.opened, []);
});

// Strain creation from the Species detail ------------------------------------

const LOCATIONS_PATH = '/api/admin/collection-locations/';
const STRAINS_PATH = '/api/admin/institution-species/1/strains/';
const DETAIL_PATH = '/api/admin/institution-species/1/';
const location = (id, code, name, country = null) => ({ id, code, name, country });
const COUNTRIES = ['FR', 'JP', 'NC'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
// The idempotency key is generated per intent, so assertions compare the rest.
const withoutKey = ({ request_id, ...rest }) => rest;

async function openStrainCreate({
  locations = [location(3, 'FRA', 'France', 'FR')], strains = [], t, onCreateBox, countries = COUNTRIES,
} = {}) {
  const h = await mount({ target: { kind: 'profile', id: 1 }, ...(t ? { t } : {}), ...(onCreateBox ? { onCreateBox } : {}) });
  await h.respond(DETAIL_PATH, detailOf(listResponse.institution_species[0], strains));
  h.click(h.button('speciesStrainNew'));
  assert.equal(h.env.requests.at(-1).path, LOCATIONS_PATH);
  if (locations !== null) await h.respond(LOCATIONS_PATH, { collection_locations: locations, countries });
  return h;
}

const strainDialog = (h) => h.view.byClass('strain-create-dialog');
const dialogControls = (h) => strainDialog(h).querySelectorAll('*').filter((node) => ['input', 'select'].includes(node.type));
const fieldIn = (h, label) => {
  const wrapper = strainDialog(h).querySelectorAll('*').filter((node) => node.type === 'label').find((node) => nodeText(node).includes(label));
  return wrapper?.querySelectorAll('*').find((node) => ['input', 'select'].includes(node.type));
};
const countrySelect = (h) => fieldIn(h, 'adminCountry');
const codeInput = (h) => fieldIn(h, 'strainCountryCodeLabel');
function set(h, label, value) {
  h.view.change(fieldIn(h, label), value);
  h.renderLeaves();
}
const confirmModal = (h) => h.view.byClass('confirm-action-modal');

test('strain form order: country, acquisition, donor (donation only), GPS, then the optional name', async () => {
  const h = await openStrainCreate();
  const dialog = strainDialog(h);
  assert.equal(dialog.props.role, 'dialog');
  assert.equal(dialog.props['aria-modal'], 'true');
  const labels = () => strainDialog(h).querySelectorAll('*').filter((node) => node.type === 'label').map((node) => flatText(node).split(' ')[0]);
  assert.deepEqual(labels(), ['adminCountry', 'strainAcquisitionType', 'strainGpsLatitude', 'strainGpsLongitude', 'strainName']);
  set(h, 'strainAcquisitionType', 'donation');
  assert.deepEqual(labels(), ['adminCountry', 'strainAcquisitionType', 'strainDonorInstitution', 'strainGpsLatitude', 'strainGpsLongitude', 'strainName']);
  // The four approved acquisition types, plus "not recorded".
  const acquisition = fieldIn(h, 'strainAcquisitionType');
  assert.deepEqual(acquisition.querySelectorAll('*').filter((node) => node.type === 'option').map((node) => node.props.value),
    ['', 'donation', 'field_collection', 'artificial_fertilization_wild', 'artificial_fertilization_captive']);
  set(h, 'strainAcquisitionType', 'field_collection');
  assert.equal(fieldIn(h, 'strainDonorInstitution'), undefined, 'donor hidden outside a donation');
  // No manual X, Strain code or initial measurement field.
  assert.doesNotMatch(nodeText(strainDialog(h)), /createBoxInitialPolyps|speciesStrainNumberAutomatic|CAN-…/);
  assert.ok(countrySelect(h).props.required);
});

test('country is required; a coded country reuses its code, a new country asks for one', async () => {
  const h = await openStrainCreate({ t: (key) => (key === 'strainCountryCodeRecorded' ? 'Code {code}' : key) });
  // Countries are localized by the client from the server identifiers.
  assert.deepEqual(countrySelect(h).querySelectorAll('*').filter((node) => node.type === 'option').map((node) => node.props.value), ['', 'FR', 'JP', 'NC']);
  h.click(h.submitButton);
  assert.equal(h.writes.length, 0);
  assert.match(h.text(), /strainCountryRequired/);
  assert.equal(countrySelect(h).props['aria-invalid'], true);
  set(h, 'adminCountry', 'FR');
  assert.match(flatText(h.view.byClass('strain-country-code')), /Code FRA/);
  assert.equal(codeInput(h), undefined, 'a recorded country never gets a second code');
  h.click(h.submitButton);
  assert.equal(h.writes[0].path, STRAINS_PATH);
  assert.deepEqual(plain(withoutKey(h.writes[0].payload)), { country: 'FR' });
  assert.match(h.writes[0].payload.request_id, UUID);

  const other = await openStrainCreate();
  set(other, 'adminCountry', 'JP');
  assert.ok(codeInput(other));
  other.click(other.submitButton);
  assert.equal(other.writes.length, 0, 'a new country needs its three-letter code');
  assert.match(other.text(), /strainCountryCodeInvalid/);
  set(other, 'strainCountryCodeLabel', 'j p n x');
  assert.equal(codeInput(other).props.value, 'JPN');
  other.click(other.submitButton);
  const payload = plain(withoutKey(other.writes[0].payload));
  assert.equal(payload.country, 'JP');
  assert.equal(payload.bbb_code, 'JPN');
  assert.equal(typeof payload.country_name, 'string');
  assert.ok(payload.country_name.length > 0);
});

test('optional values are omitted when empty; donor, GPS (including 0) and name are sent normalized', async () => {
  const h = await openStrainCreate();
  set(h, 'adminCountry', 'FR');
  set(h, 'strainAcquisitionType', 'donation');
  set(h, 'strainDonorInstitution', '  Aquarium   externe ');
  set(h, 'strainGpsLatitude', '0');
  set(h, 'strainGpsLongitude', '-4,486');
  set(h, 'strainName', '  Lignée  Brest ');
  h.click(h.submitButton);
  assert.deepEqual(plain(withoutKey(h.writes[0].payload)), {
    country: 'FR', acquisition_type: 'donation', donor_institution: 'Aquarium externe',
    latitude: '0', longitude: '-4.486', name: 'Lignée Brest', name_language: 'fr',
  });
});

test('GPS is validated locally: pair, range and decimal precision', async () => {
  const h = await openStrainCreate();
  set(h, 'adminCountry', 'FR');
  for (const [latitude, longitude, expected] of [
    ['48.1', '', /strainGpsPair/], ['91', '0', /strainGpsOutOfRange/], ['0', '-181', /strainGpsOutOfRange/],
    ['1.1234567', '0', /strainGpsInvalid/], ['nord', '0', /strainGpsInvalid/],
  ]) {
    set(h, 'strainGpsLatitude', latitude);
    set(h, 'strainGpsLongitude', longitude);
    h.click(h.submitButton);
    assert.equal(h.writes.length, 0, `${latitude}/${longitude}`);
    assert.match(h.text(), expected);
  }
});

test('a legacy location without country is linked only by an explicit choice', async () => {
  const legacy = location(4, 'JPN', 'Japon');
  const h = await openStrainCreate({ locations: [location(3, 'FRA', 'France', 'FR'), legacy] });
  set(h, 'adminCountry', 'JP');
  set(h, 'strainCountryCodeLabel', 'JPN');
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', { code: 'collection_location_code_taken', collection_location: legacy }) });
  assert.match(h.text(), /strainCountryCodeTaken/);
  assert.ok(strainDialog(h), 'values kept after a recoverable conflict');
  h.click(h.button('strainCountryLinkOffer'));
  assert.match(h.text(), /strainCountryLinked/);
  assert.equal(codeInput(h), undefined);
  h.click(h.submitButton);
  assert.deepEqual(plain(withoutKey(h.writes[1].payload)), { country: 'JP', link_collection_location: 4 });
  assert.notEqual(h.writes[1].payload.request_id, h.writes[0].payload.request_id);
  // Undoing the link returns to an ordinary code entry.
  h.click(h.button('strainCountryLinkCancel'));
  assert.ok(codeInput(h));
});

test('a code that already represents another country is never offered for linking', async () => {
  const h = await openStrainCreate();
  set(h, 'adminCountry', 'JP');
  set(h, 'strainCountryCodeLabel', 'FRA');
  h.click(h.submitButton);
  await h.answer(0, null, { error: new h.client.ApiError(409, 'x', {
    code: 'collection_location_code_taken', collection_location: location(3, 'FRA', 'France', 'FR') }) });
  assert.match(h.text(), /strainCountryCodeTaken/);
  assert.equal(h.button('strainCountryLinkOffer'), undefined);
  // A country coded meanwhile reloads the locations.
  const reads = h.env.requests.filter((item) => item.path === LOCATIONS_PATH).length;
  h.click(h.submitButton);
  await h.answer(1, null, { error: new h.client.ApiError(409, 'x', {
    code: 'country_already_coded', collection_location: location(9, 'JAP', 'Japon', 'JP') }) });
  assert.match(h.text(), /strainCountryAlreadyCoded/);
  assert.equal(h.env.requests.filter((item) => item.path === LOCATIONS_PATH).length, reads + 1);
});

test('a retry of the same intent reuses its key; a changed intent or reopened dialog starts a new one', async () => {
  const h = await openStrainCreate();
  set(h, 'adminCountry', 'FR');
  h.click(h.submitButton);
  const firstKey = h.writes[0].payload.request_id;
  await h.answer(0, null, { error: new Error('network') });
  assert.match(h.text(), /speciesStrainCreateError/);
  h.click(h.submitButton);
  assert.equal(h.writes[1].payload.request_id, firstKey, 'the same intent reuses its key');
  await h.answer(1, null, { error: new Error('network') });
  set(h, 'strainName', 'Autre');
  h.click(h.submitButton);
  assert.notEqual(h.writes[2].payload.request_id, firstKey, 'a new intent starts a new key');
  await h.answer(2, null, { error: new Error('network') });
  h.click(h.view.byClass('box-dialog-close'));
  assert.equal(strainDialog(h), undefined);
  h.click(h.button('speciesStrainNew'));
  await h.respond(LOCATIONS_PATH, { collection_locations: [location(3, 'FRA', 'France', 'FR')], countries: COUNTRIES });
  set(h, 'adminCountry', 'FR');
  h.click(h.submitButton);
  assert.notEqual(h.writes[3].payload.request_id, firstKey, 'a reopened dialog starts a new key');
});

test('after creation the new strain is shown in context, then "create a box?" opens the Box form or stays', async () => {
  for (const answer of ['yes', 'no']) {
    const boxes = [];
    const h = await openStrainCreate({ onCreateBox: (strainId) => boxes.push(strainId) });
    set(h, 'adminCountry', 'FR');
    h.click(h.submitButton);
    const created = strain(11, 'CAN-FRA-2', { strain_number: 2, recorded_provenance_name: 'France', recorded_country_code: 'FR' });
    // A successful replay (200) has the same shape and is handled identically.
    await h.answer(0, { institution_species_id: 1, strain: created, strains: [created] });
    assert.equal(strainDialog(h), undefined, 'form closed');
    assert.equal(confirmModal(h), undefined, 'asked only once the new strain is visible');
    const reread = h.env.requests.at(-1);
    assert.equal(reread.path, DETAIL_PATH);
    assert.equal(reread.options.cache, 'no-store');
    await h.respond(DETAIL_PATH, detailOf(listResponse.institution_species[0], [created]));
    assert.ok(h.strainHeading('CAN-FRA-2'), 'the new strain is visible on the Species page');
    assert.ok(confirmModal(h));
    assert.match(nodeText(confirmModal(h)), /strainOfferBoxTitle/);
    h.click(h.view.byClass(answer === 'yes' ? 'confirm-action-submit' : 'confirm-action-cancel'));
    await h.view.settle();
    h.renderLeaves();
    assert.equal(confirmModal(h), undefined);
    assert.deepEqual(boxes, answer === 'yes' ? [11] : []);
    assert.deepEqual(h.opened, [], 'no navigation away from the Species page');
    assert.equal(h.writes.length, 1, 'no duplicate write');
  }
});

for (const outcome of ['success', 'error']) {
  test(`late strain creation ${outcome} after unmount cannot refresh or publish feedback`, async () => {
    const h = await openStrainCreate();
    set(h, 'adminCountry', 'FR');
    h.click(h.submitButton);
    assert.equal(h.writes.length, 1);
    const reads = h.env.requests.length;
    h.view.unmount();
    if (outcome === 'success') {
      const created = strain(11, 'CAN-FRA-2', { strain_number: 2 });
      h.writes[0].resolve({ institution_species_id: 1, strain: created, strains: [created] });
    } else h.writes[0].reject(new h.client.ApiError(403, 'late refusal'));
    await h.view.settle();
    assert.equal(h.env.requests.length, reads, 'no detail reread from an abandoned page');
    assert.deepEqual(h.opened, []);
    // The shared mutation hook still resets its busy flag in finally; no business state may publish in addition.
    assert.equal(h.view.updatesAfterUnmount, 1, 'only the shared busy reset, no created callback or error feedback');
  });
}

test('server refusals are explained and never silently retried', async () => {
  const cases = [
    [new Error('network'), /speciesStrainCreateError/],
    ['403', /speciesCatalogActionForbidden/],
    ['404', /speciesDetailNotFound/],
    ['species_code_missing', /speciesStrainSpeciesCodeMissing/],
    ['species_code_unusable', /speciesStrainSpeciesCodeUnusable/],
    ['collection_location_code_unusable', /speciesStrainLocationCodeUnusable/],
    ['strain_number_unavailable', /speciesStrainNumberUnavailable/],
    ['strain_code_conflict', /speciesStrainConflict/],
    ['strain_creation_request_conflict', /speciesStrainRequestConflict/],
    ['collection_location_country_conflict', /strainCountryLocationConflict/],
  ];
  for (const [failure, expected] of cases) {
    const h = await openStrainCreate();
    set(h, 'adminCountry', 'FR');
    h.click(h.submitButton);
    const { ApiError } = h.client;
    const error = failure instanceof Error ? failure
      : failure === '403' ? new ApiError(403, 'x', { detail: 'no' })
        : failure === '404' ? new ApiError(404, 'x', { code: 'not_found' })
          : new ApiError(409, 'x', { code: failure });
    await h.answer(0, null, { error });
    assert.match(h.text(), expected, String(failure));
    assert.ok(strainDialog(h), 'the dialog stays open');
    if (failure === 'collection_location_country_conflict') {
      // The recorded locations are reread before another attempt.
      assert.equal(h.env.requests.at(-1).path, LOCATIONS_PATH);
      await h.respond(LOCATIONS_PATH, { collection_locations: [location(3, 'FRA', 'France', 'FR')], countries: COUNTRIES });
    }
    assert.equal(h.submitButton.props.disabled, false);
    assert.equal(h.writes.length, 1, 'never retried silently');
  }
});

test('locations failure is retryable; historical and uncoded species cannot open strain creation', async () => {
  const h = await openStrainCreate({ locations: null });
  await h.respond(LOCATIONS_PATH, null, { error: new h.client.ApiError(500, 'x', {}) });
  assert.match(h.text(), /speciesStrainLocationsError/);
  assert.equal(h.submitButton.props.disabled, true);
  h.click(h.button('taxonomyRetry'));
  await h.respond(LOCATIONS_PATH, { collection_locations: [], countries: COUNTRIES });
  assert.equal(h.submitButton.props.disabled, false);

  const historicalView = await mount({ target: { kind: 'historical', id: 7 } });
  await historicalView.respond('/api/admin/institution-species/historical/7/', detailOf({
    ...listResponse.historical_species[0], can_create_strain: false, strain_creation_issue: 'species_code_missing',
  }));
  assert.equal(historicalView.button('speciesStrainNew').props.disabled, true);
  historicalView.button('speciesStrainNew').props.onClick();
  await historicalView.view.settle();
  assert.equal(historicalView.writes.length, 0);
  assert.equal(strainDialog(historicalView), undefined);

  const uncoded = await mount({ target: { kind: 'profile', id: 2 } });
  await uncoded.respond('/api/admin/institution-species/2/', detailOf(profile(2, 'No code species')));
  assert.equal(uncoded.button('speciesStrainNew').props.disabled, true);
  uncoded.button('speciesStrainNew').props.onClick();
  await uncoded.view.settle();
  assert.equal(uncoded.writes.length, 0);
  assert.equal(strainDialog(uncoded), undefined);
});

test('species creation asks once "add a strain?": yes opens the form with the species, no stays on its page', async () => {
  for (const answer of ['yes', 'no']) {
    const h = await openCreate();
    h.type('Aurelia coerulea');
    h.click(h.button('speciesSearchManual'));
    h.click(h.submitButton);
    await h.answer(0, profile(5, 'Aurelia coerulea', { species_code: 'ACO' }));
    assert.deepEqual(h.opened, [{ kind: 'profile', id: 5 }]);
    h.view.setProps({ target: { kind: 'profile', id: 5 } });
    h.renderLeaves();
    await h.respond('/api/admin/institution-species/5/', detailOf(profile(5, 'Aurelia coerulea', { species_code: 'ACO' })));
    assert.ok(confirmModal(h));
    assert.match(nodeText(confirmModal(h)), /speciesOfferStrainTitle/);
    h.click(h.view.byClass(answer === 'yes' ? 'confirm-action-submit' : 'confirm-action-cancel'));
    await h.view.settle();
    h.renderLeaves();
    assert.equal(confirmModal(h), undefined);
    if (answer === 'yes') {
      assert.ok(strainDialog(h), 'the New Strain form opens for this Species');
      assert.match(nodeText(strainDialog(h)), /Aurelia coerulea/);
      assert.equal(h.env.requests.at(-1).path, LOCATIONS_PATH);
    } else {
      assert.equal(strainDialog(h), undefined);
      assert.ok(h.view.nodes.some((node) => node.props.id === 'species-detail-title'), 'the Species page stays open');
    }
    assert.equal(h.writes.length, 1, 'no Strain is created implicitly');
    // Reopening the page never asks again.
    h.view.setProps({ target: null });
    h.renderLeaves();
    h.view.setProps({ target: { kind: 'profile', id: 5 } });
    h.renderLeaves();
    await h.respond('/api/admin/institution-species/5/', detailOf(profile(5, 'Aurelia coerulea', { species_code: 'ACO' })));
    assert.equal(confirmModal(h), undefined);
  }
});

test('the Box continuation reuses the New Box form, desktop Administration only, reset on institution change', () => {
  assert.match(app, /\{activeTab === 'admin' && canCreateBox && isDesktopApp && adminBoxStrainId != null \? \(\s*<CreateBoxPanel/);
  assert.match(app, /initialStrainId=\{adminBoxStrainId\}\s+lockStrain/);
  assert.match(app, /key=\{`admin:\$\{data\.profile\?\.id \?\? 'none'\}:\$\{activeOrganizationId \?\? 'none'\}:\$\{adminBoxStrainId\}`\}/);
  assert.match(app, /isOperationCurrent=\{\(\) => operationGeneration === organizationRequestGenerationRef\.current\}/);
  assert.match(app, /if \(activeTab !== 'admin' \|\| isBoxRoute \|\| !isDesktopApp\) setAdminBoxStrainId\(null\);/);
  assert.match(app, /setIsCreateBoxOpen\(false\);\s*setAdminBoxStrainId\(null\);/);
  assert.match(app, /onCreateBoxForStrain=\{canCreateBox \? setAdminBoxStrainId : undefined\}/);
  assert.match(adminView, /onCreateBox=\{onCreateBoxForStrain\}/);
});

test('strain creation keys exist in FR and EN; the country label stays simple', () => {
  for (const [catalog, language] of [[fr, 'fr'], [en, 'en']]) {
    for (const key of ['speciesStrainNew', 'speciesStrainCreateSubmit', 'speciesStrainRequestConflict',
      'speciesOfferStrainTitle', 'strainOfferBoxTitle', 'strainAcquisitionType', 'strainDonorInstitution',
      'strainGps', 'strainName', 'strainCountryCodeLabel', 'historyInitialMeasurement', 'historyInitialObservedBy',
      'createBoxInitialPolyps']) {
      assert.match(catalog, new RegExp(`^  ${key}: '`, 'm'), `${language}.${key}`);
    }
  }
  assert.match(fr, /adminCountry: 'Pays',/);
  assert.match(fr, /speciesOfferStrainTitle: 'Souhaitez-vous ajouter une souche \?',/);
  assert.match(fr, /strainOfferBoxTitle: 'Souhaitez-vous créer une boîte \?',/);
  assert.match(fr, /strainAcquisitionDonation: 'Don d’une autre institution',/);
  assert.match(fr, /strainAcquisitionFieldCollection: 'Prélèvement dans le milieu naturel',/);
  assert.match(fr, /strainAcquisitionFertilizationWild: 'Fécondation artificielle depuis des individus sauvages',/);
  assert.match(fr, /strainAcquisitionFertilizationCaptive: 'Fécondation artificielle depuis des individus captifs',/);
  assert.match(fr, /historyInitialMeasurement: 'Départ',/);
  assert.match(fr, /historyInitialObservedBy: 'Nombre initial de polypes observé par',/);
  assert.match(fr, /createBoxInitialPolyps: 'Nombre initial de polypes',/);
  assert.doesNotMatch(fr + en, /·/);
});

// Species catalog and page: boxes, strains and layout ------------------------------

const box = (id, code, strainId, extra = {}) => ({
  id, global_code: code, status: 'active', organization: { id: 1 },
  strain: { id: strainId, species: { id: 101 } }, thermal_zone: { id: 4, name: 'Zone 25.0°C' },
  latest_measurement: { measured_on: '2026-11-13', polyp_count: 0, ephyrae_count: 0 }, ...extra,
});

test('box grouping helpers use only the active institution and never infer a shared origin', () => {
  const boxes = [box(1, 'CAN-BRE-1.001', 10), box(2, 'CAN-BRE-1.002', 10), box(3, 'CAN-FRA-2.001', 11),
    box(4, 'XXX-YYY-1.001', 10, { organization: { id: 2 } }), box(5, 'AUR-BRE-1.001', 50, { strain: { id: 50, species: { id: 107 } } })];
  assert.deepEqual(plain([...helpers.countBoxesBySpecies(boxes, 1)]), [[101, 3], [107, 1]]);
  const groups = helpers.groupBoxesByStrain(boxes, [10, 11, 12], 1);
  assert.deepEqual(plain([...groups].map(([id, items]) => [id, items.map((item) => item.id)])), [[10, [1, 2]], [11, [3]], [12, []]]);
  assert.equal(helpers.commonRecordedProvenance([]), null);
  assert.equal(helpers.commonRecordedProvenance([{ recorded_provenance_name: 'Brest' }, { recorded_provenance_name: 'Brest' }]), 'Brest');
  assert.equal(helpers.commonRecordedProvenance([{ recorded_provenance_name: 'Brest' }, { recorded_provenance_name: 'Nice' }]), null);
  assert.equal(helpers.commonRecordedProvenance([{ recorded_provenance_name: 'Brest' }, { recorded_provenance_name: null }]), null);
  assert.equal(helpers.strainTitle({ name: 'Souche Paris', strain_number: 1, code: 'CAN-FRA-1' }, 'Strain'), 'Souche Paris');
  assert.equal(helpers.strainTitle({ name: '', strain_number: 0, code: 'CAN-FRA-0' }, 'Strain'), 'Strain 0', 'zero is a real number');
  assert.equal(helpers.strainTitle({ name: '', strain_number: null, code: 'legacy' }, 'Strain'), 'legacy');
});

test('catalog box totals come only from the complete collection, for this institution', async () => {
  const boxes = [box(1, 'CAN-BRE-1.001', 10), box(2, 'CAN-BRE-1.002', 10), box(9, 'CAN-BRE-1.009', 10, { organization: { id: 2 } })];
  const counts = (h) => h.all('species-list-row')
    .map((row) => flatText(row.querySelectorAll('.species-list-count')[1]));
  // A partial list is never counted: loading shows a placeholder, not a number.
  let h = await mount({ boxes, boxCollectionStatus: 'loading' });
  await h.respond(LIST_PATH, listResponse);
  assert.deepEqual(counts(h), ['…', '…', '…']);
  h = await mount({ boxes, boxCollectionStatus: 'ready' });
  await h.respond(LIST_PATH, listResponse);
  assert.deepEqual(counts(h), ['0 / 0', '2 / 2', '0 / 0']);
  // A failed collection is reported and retryable; no total is invented.
  h = await mount({ boxes, boxCollectionStatus: 'error' });
  await h.respond(LIST_PATH, listResponse);
  assert.deepEqual(counts(h), ['', '', '']);
  h.click(h.button('boxCollectionRetry'));
  assert.equal(h.collectionRetries, 1);
});

test('scientific Species summaries use per-Box bounds across strains, including inactive Boxes', () => {
  const boxes = [
    box(1, 'CAN-BRE-1.001', 10, { first_measurement_on: '2020-01-05', last_measurement_on: '2025-11-01' }),
    box(2, 'CAN-FRA-2.001', 11, { status: 'inactive', first_measurement_on: '2010-01-05', last_measurement_on: '2026-11-13' }),
    box(3, 'CAN-FRA-2.002', 11, { status: 'pending_review', first_measurement_on: null, last_measurement_on: null, latest_measurement: null }),
    box(4, 'CAN-BRE-1.002', 10, { created_on: '1900-01-01', entered_on: '1901-01-01', latest_measurement: { measured_on: '1970-01-01', polyp_count: 0 } }),
    box(5, 'CAN-BRE-1.003', 10, { organization: { id: 2 }, first_measurement_on: '1800-01-01', last_measurement_on: '2099-01-01' }),
    box(6, 'AUR-BRE-1.001', 50, { strain: { id: 50, species: { id: 7 } }, first_measurement_on: '2015-01-01', last_measurement_on: '2018-01-01' }),
  ];
  const summaries = helpers.summarizeBoxesBySpecies(boxes, 1);
  assert.deepEqual(plain(summaries.get(101)), { boxCount: 4, activeBoxCount: 2, firstMeasurementOn: '2010-01-05', lastMeasurementOn: '2026-11-13' });
  assert.deepEqual(plain(summaries.get(7)), { boxCount: 1, activeBoxCount: 1, firstMeasurementOn: '2015-01-01', lastMeasurementOn: '2018-01-01' });
  assert.deepEqual(plain(helpers.summarizeBoxesBySpecies(boxes, 2).get(101)), { boxCount: 1, activeBoxCount: 1, firstMeasurementOn: '1800-01-01', lastMeasurementOn: '2099-01-01' });
});

test('catalog has one Inventory Dates cell with scientific bounds, active totals and no persistent selection', async () => {
  const boxes = [
    box(1, 'CAN-BRE-1.001', 10, { first_measurement_on: '2020-01-05', last_measurement_on: '2025-11-01', created_on: '2026-01-01' }),
    box(2, 'CAN-FRA-2.001', 11, { status: 'inactive', first_measurement_on: '2010-01-05', last_measurement_on: '2026-11-13' }),
    box(3, 'CAN-FRA-2.002', 11, { status: 'pending_review', first_measurement_on: null, last_measurement_on: null, latest_measurement: null }),
    box(4, 'CAN-BRE-1.002', 10, { organization: { id: 2 }, first_measurement_on: '1800-01-01', last_measurement_on: '2099-01-01' }),
  ];
  const h = await mount({ boxes, boxCollectionStatus: 'ready' });
  await h.respond(LIST_PATH, listResponse);
  const row = h.all('species-list-row')[1];
  const counts = row.querySelectorAll('.species-list-count').map(flatText);
  assert.deepEqual(counts, ['2 speciesCatalogStrains', '1 / 3']);
  assert.equal(h.all('species-list-header')[0].querySelectorAll('*').filter(node => node.type === 'span').length, 5);
  const dates = row.querySelectorAll('.box-inventory-dates');
  assert.equal(dates.length, 1);
  const { formatDisplayDate } = h.env.load('../src/utils/dateFormat.ts');
  const labels = dates[0].querySelectorAll('*').filter(node => node.type === 'small').map(elementText);
  const values = dates[0].querySelectorAll('*').filter(node => node.type === 'strong').map(elementText);
  assert.deepEqual(labels, ['firstMeasurementOn', 'boxInventoryLastMeasurement']);
  assert.deepEqual(values, [formatDisplayDate('2010-01-05'), formatDisplayDate('2026-11-13')]);
  assert.equal(h.all('species-list-row')[0].querySelectorAll('.box-inventory-dates')[0].querySelectorAll('*').filter(node => node.type === 'strong').map(elementText).join('|'), 'boxInventoryNoData|boxInventoryNoData');
  const link = row.querySelector('.species-list-name');
  assert.match(link.parent.props.className, /box-inventory-identity/);
  h.click(link);
  assert.ok(h.all('species-list-row').every(node => !node.props.className.includes('is-selected')));
  assert.equal(link.props['aria-current'], undefined);
  assert.deepEqual(h.env.requests.map(request => request.path), [LIST_PATH], 'no eager scientific histories or per-Box requests');
  for (const status of ['loading', 'error']) {
    h.view.setProps({ boxCollectionStatus: status });
    h.renderLeaves();
    assert.doesNotMatch(flatText(h.all('species-list-row')[1]), /2010|2026/, 'partial/error cache cannot publish dates');
  }
});

test('Species styling reuses Inventory reference links, dates and hover without selection or warning stripes', () => {
  const css = read('../src/styles/pages/administration.css');
  assert.match(section, /className="box-inventory-cell box-inventory-identity"/);
  assert.match(section, /className="box-inventory-cell box-inventory-dates"/);
  assert.match(css, /\.box-inventory-row:hover,\s*\.species-list-row:hover\s*\{\s*background: var\(--color-surface-subtle\)/);
  assert.doesNotMatch(css, /\.species-list-row\.is-selected/);
  const warning = css.match(/\.species-code-issues\s*\{([^}]+)\}/)[1];
  assert.match(warning, /background: var\(--color-danger-soft\)/);
  assert.doesNotMatch(warning, /border-left|border-inline-start|box-shadow/);
  assert.match(css, /\.species-admin-section :is\(a, button, input\):focus-visible/);
  assert.doesNotMatch(strainsPanel, /box\.created_on|box\.entered_on|formatDisplayDate\(measurement\.measured_on\)/);
});

test('refreshing the catalog rereads POLYPBASE data only and never calls WoRMS', async () => {
  const h = await mount();
  await h.respond(LIST_PATH, listResponse);
  const action = h.button('speciesCatalogRefresh');
  assert.equal(action.props.className, 'icon-button');
  assert.equal(nodeText(action), '', 'refresh is icon-only with an accessible name');
  h.click(action);
  assert.equal(h.button('loading').props.disabled, true);
  assert.equal(h.button('loading').props['aria-busy'], true);
  assert.deepEqual(h.env.requests.map((item) => item.path), [LIST_PATH, LIST_PATH]);
  await h.respond(LIST_PATH, { institution_species: [], historical_species: [] });
  assert.match(h.text(), /speciesCatalogEmpty/);
  assert.equal(h.writes.length, 0);
});

test('refresh uses an independent no-store read, replaces server names and strain counts, and retries boxes', async () => {
  const h = await mount({ boxes: [box(1, 'CAN-BRE-1.001', 10)], boxCollectionStatus: 'ready' });
  await h.respond(LIST_PATH, listResponse);
  const first = h.env.requests[0];
  assert.equal(first.options.cache, 'no-store');
  assert.ok(first.options.signal instanceof AbortSignal);
  h.click(h.button('speciesCatalogRefresh'));
  const refresh = h.env.requests.at(-1);
  assert.notEqual(refresh, first);
  assert.notEqual(refresh.options.signal, first.options.signal);
  assert.equal(first.options.signal.aborted, true);
  assert.equal(refresh.options.cache, 'no-store');
  assert.ok(refresh.options.signal instanceof AbortSignal);
  assert.equal(h.collectionRetries, 1);
  assert.equal(h.button('loading').props.disabled, true);
  assert.match(h.text(), /Cassiopea andromeda/, 'existing rows remain visible during refresh');
  await h.respond(LIST_PATH, {
    institution_species: [profile(1, 'Cassiopea updated', { strain_count: 9, species_code: 'NEW' })],
    historical_species: [],
  });
  assert.deepEqual(h.all('species-list-name').map(elementText), ['Cassiopea updated (NEW)']);
  assert.equal(h.all('species-list-count')[0].props.children[0], 9);
  assert.doesNotMatch(h.text(), /Cassiopea andromeda|Aurelia aurita/);
  assert.equal(h.button('speciesCatalogRefresh').props.disabled, false);
  // Collection counts are supplied by App; refresh must not retain an old memoized total.
  h.view.setProps({ boxes: [box(1, 'NEW-BRE-1.001', 10), box(2, 'NEW-BRE-1.002', 10)], boxCollectionStatus: 'ready' });
  assert.equal(flatText(h.all('species-list-count')[1]), '2 / 2');
  assert.deepEqual(h.env.requests.map((request) => request.path), [LIST_PATH, LIST_PATH]);
  assert.deepEqual(h.writes, []);
});

test('a failed refresh keeps existing rows with an error; a permission refusal removes them', async () => {
  const h = await mount();
  await h.respond(LIST_PATH, listResponse);
  h.click(h.button('speciesCatalogRefresh'));
  await h.respond(LIST_PATH, null, { error: new h.client.ApiError(500, 'unavailable') });
  assert.match(h.text(), /speciesCatalogLoadError/);
  assert.equal(h.all('species-list-row').length, 3);
  assert.equal(h.button('speciesCatalogRefresh').props.disabled, false);
  h.click(h.button('speciesCatalogRefresh'));
  assert.doesNotMatch(h.text(), /speciesCatalogLoadError/);
  await h.respond(LIST_PATH, null, { error: new h.client.ApiError(403, 'forbidden') });
  assert.match(h.text(), /speciesCatalogForbidden/);
  assert.equal(h.all('species-list-row').length, 0);
  assert.equal(h.button('speciesCatalogNewSpecies'), undefined);
  assert.equal(h.collectionRetries, 2);
});

test('changing the active organization aborts the old list read and scopes box totals to the new prop', async () => {
  const h = await mount({ boxCollectionStatus: 'ready', boxes: [
    box(1, 'CAN-BRE-1.001', 10),
    box(2, 'CAN-BRE-1.002', 10, { organization: { id: 2 } }),
    box(3, 'CAN-BRE-1.003', 10, { organization: { id: 2 } }),
  ] });
  const old = h.env.requests[0];
  h.view.setProps({ organizationId: 2, organizationName: 'Second institution' });
  const current = h.env.requests.at(-1);
  assert.notEqual(old, current);
  assert.equal(old.options.signal.aborted, true);
  assert.equal(current.options.cache, 'no-store');
  assert.ok(current.options.signal instanceof AbortSignal);
  await h.respond(LIST_PATH, { institution_species: [profile(1, 'Second institution species', { species_code: 'CAN' })], historical_species: [] });
  old.resolve(listResponse);
  await h.view.settle();
  h.renderLeaves();
  assert.deepEqual(h.all('species-list-name').map(elementText), ['Second institution species (CAN)']);
  assert.equal(flatText(h.all('species-list-count')[1]), '2 / 2');
  assert.doesNotMatch(h.text(), /Cassiopea andromeda|Aurelia aurita/);
});

test('late errors from an old organization or an unmounted catalog cannot publish', async () => {
  const h = await mount();
  const old = h.env.requests[0];
  h.view.setProps({ organizationId: 2 });
  await h.respond(LIST_PATH, listResponse);
  old.reject(new h.client.ApiError(403, 'old context'));
  await h.view.settle();
  assert.doesNotMatch(h.text(), /speciesCatalogForbidden|speciesCatalogLoadError/);
  assert.equal(h.all('species-list-row').length, 3);
  h.click(h.button('speciesCatalogRefresh'));
  const pending = h.env.requests.at(-1);
  h.view.unmount();
  assert.equal(pending.options.signal.aborted, true);
  pending.resolve({ institution_species: [], historical_species: [] });
  await h.view.settle();
  assert.equal(h.view.updatesAfterUnmount, 0);
});

test('App refresh forces the complete Box collection for Species even when already ready', () => {
  assert.match(app, /onRetryBoxCollection=\{\(\) => void requestBoxCollection\(\{ retry: true, force: route\.adminSection === 'species' \}\)\}/);
  const start = app.indexOf('  function requestBoxCollection(');
  const collection = app.slice(start, app.indexOf('\n  }', start) + 4);
  assert.match(collection, /if \(!options\.force && pending && pending\.generation === generation\) return pending\.promise;/);
  assert.match(collection, /if \(!options\.force && !isRetry && !shouldLoadBoxCollection\(current, Date\.now\(\)\)\) return Promise\.resolve\(\);/);
  assert.match(collection, /if \(options\.force \|\| current\.status !== 'ready'\) writeBoxCollection\(\{ status: 'loading', loadedAt: null \}\);/);
  assert.match(collection, /fetchAllPages<BoxItem>\(`\/api\/boxes\/\?limit=\$\{BOX_LIST_LIMIT\}`, undefined, options\.force\)/);
});

test('forced App collection refresh bypasses a ready cache and supersedes an older pending read', async () => {
  const start = app.indexOf('  function requestBoxCollection(');
  assert.ok(start > 0);
  const implementation = app.slice(start, app.indexOf('\n  }', start) + 4);
  const initial = box(1, 'OLD-BRE-1.001', 10);
  const state = { boxes: [initial] };
  const requests = [];
  const context = {
    Date, BOX_LIST_LIMIT: 100,
    data: state,
    organizationRequestGenerationRef: { current: 1 },
    boxCollectionRequestRef: { current: null },
    boxCollectionRevisionRef: { current: 0 },
    boxCollectionRef: { current: { status: 'ready', loadedAt: Date.now() } },
    shouldLoadBoxCollection: () => false,
    getOperationRequests: () => ({ setData(update) { state.boxes = update(state).boxes; } }),
    writeBoxCollection(next) { context.boxCollectionRef.current = next; },
    mergeLoadedBoxes: createBoxEnvironment().load('../src/utils/boxCollection.ts').mergeLoadedBoxes,
    fetchAllPages(path, until, force) {
      const request = { ...deferred(), path, until, force };
      requests.push(request);
      return request.promise;
    },
  };
  vm.createContext(context);
  vm.runInContext(ts.transpileModule(implementation, {
    compilerOptions: { target: ts.ScriptTarget.ES2022 },
  }).outputText, context);
  await context.requestBoxCollection();
  assert.equal(requests.length, 0, 'a fresh ready list normally needs no read');
  const earlier = context.requestBoxCollection({ retry: true, force: true });
  const latest = context.requestBoxCollection({ retry: true, force: true });
  assert.equal(requests.length, 2, 'force does not join the earlier pending read');
  assert.ok(requests.every((request) => request.path === '/api/boxes/?limit=100' && request.force === true));
  assert.equal(context.boxCollectionRef.current.status, 'loading');
  requests[0].resolve([box(2, 'STALE-BRE-1.002', 10)]);
  await earlier;
  assert.deepEqual(state.boxes, [initial]);
  assert.equal(context.boxCollectionRef.current.status, 'loading');
  assert.equal(context.boxCollectionRequestRef.current.promise, latest, 'old completion cannot clear the current request');
  requests[1].resolve([box(3, 'NEW-BRE-1.003', 10), box(4, 'NEW-BRE-1.004', 10)]);
  await latest;
  assert.deepEqual(Array.from(state.boxes, (item) => item.id), [3, 4]);
  assert.equal(context.boxCollectionRef.current.status, 'ready');
  assert.equal(context.boxCollectionRequestRef.current, null);
});

async function openPage({ strains, boxes = [], boxCollectionStatus = 'ready', t } = {}) {
  const h = await mount({ target: { kind: 'profile', id: 1 }, boxes, boxCollectionStatus, ...(t ? { t } : {}) });
  await h.respond('/api/admin/institution-species/1/', detailOf(listResponse.institution_species[0], strains));
  return h;
}

test('the header keeps counts but never promotes shared strain provenance to the species', async () => {
  const t = (key) => (key === 'speciesStrainRecordedProvenance' ? 'Origin: {name}' : key);
  const h = await openPage({
    t,
    strains: [strain(10, 'CAN-BRE-1', { strain_number: 1, recorded_provenance_name: 'Rade de Brest' }),
      strain(11, 'CAN-BRE-2', { strain_number: 2, recorded_provenance_name: 'Rade de Brest' })],
    boxes: [box(1, 'CAN-BRE-1.001', 10), box(2, 'CAN-BRE-2.001', 11), box(3, 'CAN-BRE-2.002', 11)],
  });
  const context = flatText(h.view.byClass('species-sheet-context'));
  assert.match(context, /2 speciesCatalogStrains/);
  assert.match(context, /3 speciesDetailBoxes/);
  assert.doesNotMatch(context, /Origin:|Rade de Brest|speciesStrainRecordedProvenance/);
  assert.match(h.text(), /speciesStrainLocation Rade de Brest/, 'strain-level legacy metadata is retained');
  // Each table keeps its own self-contained country subtitle.
  assert.deepEqual(h.all('species-strain-provenance').map(nodeText), ['adminCountry : speciesStrainNoProvenance', 'adminCountry : speciesStrainNoProvenance']);
  // Header actions: one primary creation, then identification; preview is temporarily hidden.
  const actions = h.view.byClass('entity-header__actions').querySelectorAll('*').filter((node) => node.type === 'button');
  assert.deepEqual(actions.map(nodeText), ['speciesStrainNew', 'speciesDetailIdentification']);
  assert.deepEqual(actions.map((node) => node.props.className.split(' ')[0]), ['primary-button', 'secondary-button']);
});

test('strains stay visible and list their real boxes with the tracking preview', async () => {
  const h = await openPage({
    strains: [strain(10, 'CAN-BRE-1', { strain_number: 1, name: 'Souche Brest', active_box_count: 1 }),
      strain(11, 'CAN-FRA-2', { strain_number: 2 })],
    boxes: [box(1, 'CAN-BRE-1.001', 10), box(2, 'CAN-BRE-1.002', 10, { status: 'inactive', thermal_zone: null, latest_measurement: null }),
      box(3, 'CAN-FRA-9.001', 99)],
  });
  assert.equal(h.all('species-strain-toggle').length, 0);
  assert.equal(h.all('species-strain-body').length, 2);
  // The title and official code share one summary, followed by the recorded country.
  assert.equal(nodeText(h.strainHeading('CAN-BRE-1').querySelector('.species-strain-name')), 'Souche Brest (CAN-BRE-1)');
  assert.equal(nodeText(h.strainHeading('CAN-FRA-2').querySelector('.species-strain-name')), 'createBoxStrainLabel 2 (CAN-FRA-2)');
  assert.equal(h.all('species-strain-code').length, 0);
  assert.match(flatText(h.strainHeading('CAN-BRE-1')), /adminCountry : speciesStrainNoProvenance/);
  assert.equal(h.all('species-strain-boxes').length, 0, 'no per-strain counter');

  const heading = h.strainHeading('CAN-BRE-1');
  assert.equal(heading.type, 'h4');
  assert.equal(heading.props.onClick, undefined);
  const previews = h.view.nodes.filter((node) => node.type === 'BoxTrackingPreview');
  assert.deepEqual(previews.map((node) => [node.props.boxId, node.props.code]), [[1, 'CAN-BRE-1.001'], [2, 'CAN-BRE-1.002']]);
  assert.ok(previews.every((node) => node.props.speciesName === 'Cassiopea andromeda'));
  previews[0].props.onOpenBox(1, 'CAN-BRE-1.001');
  assert.deepEqual(h.openedBoxes, [[1, 'CAN-BRE-1.001']]);
  // The official code stays in the summary, not duplicated in the opened facts.
  assert.match(nodeText(heading.querySelector('.species-strain-name')), /CAN-BRE-1/);
  assert.doesNotMatch(nodeText(h.view.byClass('species-strain-facts')), /CAN-BRE-1|speciesStrainActiveBoxes/);
  // Inventory rows: location, dates and latest counts; missing data is said, never filled in.
  const rows = h.all('box-inventory-row').map(nodeText);
  assert.match(rows[0], /Zone 25\.0°C/);
  assert.match(rows[1], /boxInventoryWithoutLocation/);
  assert.match(rows[1], /boxInventoryNoMeasurement/);
  // No measurement is loaded eagerly: the preview fetches a box only when opened.
  assert.deepEqual(h.env.requests.map((item) => item.path), ['/api/admin/institution-species/1/']);
  assert.equal(h.writes.length, 0);

  assert.match(h.text(), /speciesStrainNoBoxes/);
  assert.equal(h.all('species-strain-body').length, 2);
});

test('a strain stays visible while many boxes are disclosed progressively', async () => {
  const boxes = Array.from({ length: 53 }, (_, index) => box(index + 1, `CAN-BRE-1.${String(index + 1).padStart(3, '0')}`, 10));
  const h = await openPage({ strains: [strain(10, 'CAN-BRE-1', { strain_number: 1 })], boxes });
  assert.equal(h.all('species-strain-body').length, 1);
  const rowCount = () => h.all('box-inventory-row').length;
  assert.equal(rowCount(), 50);
  h.click(h.view.byClass('code-change-more'));
  assert.equal(rowCount(), 53);
});

test('boxes are never listed from an incomplete collection; a failure is retryable', async () => {
  const boxes = [box(1, 'CAN-BRE-1.001', 10)];
  let h = await openPage({ strains: [strain(10, 'CAN-BRE-1', { strain_number: 1, active_box_count: 4 })], boxes, boxCollectionStatus: 'loading' });
  assert.match(h.text(), /speciesStrainBoxesLoading/);
  assert.doesNotMatch(h.text(), /4 createBoxActiveBoxes/);
  assert.equal(h.all('box-inventory-row').length, 0);
  assert.doesNotMatch(nodeText(h.view.byClass('species-sheet-context')), /speciesDetailBox/);
  h = await openPage({ strains: [strain(10, 'CAN-BRE-1', { strain_number: 1 })], boxes, boxCollectionStatus: 'error' });
  assert.equal(h.all('box-inventory-row').length, 0);
  h.click(h.button('boxCollectionRetry'));
  assert.equal(h.collectionRetries, 1);
});

test('the Species page offers no unsupported control and no mutation of strains or boxes', () => {
  // The strain panel is read-only: no request helper at all.
  assert.doesNotMatch(strainsPanel, /apiGet|apiPost|apiPatch|apiPut|apiDelete|fetch\(/);
  assert.match(strainsPanel, /<BoxTrackingPreview\b/);
  // Recorded origin/GPS are read-only metadata; no edit, DNA or invented counts.
  assert.doesNotMatch(section + strainsPanel, /initial_polyp|createBoxInitialPolyps|\bdna\b|\bDNA\b|\bADN\b/);
  assert.doesNotMatch(strainsPanel, /<input|<textarea|<select/);
  assert.doesNotMatch(section + strainsPanel, /api\/taxonomy\//);
  // No WoRMS status is claimed, and no code-change apply exists.
  assert.doesNotMatch(section + strainsPanel, /mutation_available|code-change\/apply|species_code_change_apply/);
  // The page is a full entity page: the Administration section list is not kept beside it.
  assert.match(adminView, /const isSpeciesPage = displayedSection === 'species' && speciesTarget !== null;/);
  assert.match(adminView, /\{isSpeciesPage \? null : \(\s*<AdminFlowNav/);
  assert.match(fr, /adminTabSpecies: 'Espèces',/);
  assert.match(en, /adminTabSpecies: 'Species',/);
  assert.match(fr, /speciesDetailLabel: 'Fiche de l’espèce',/);
  assert.match(fr, /speciesDetailStrainsAndBoxes: 'Souches et boîtes',/);
  assert.doesNotMatch(fr + en, /adminTabSpeciesStrains/);
});
