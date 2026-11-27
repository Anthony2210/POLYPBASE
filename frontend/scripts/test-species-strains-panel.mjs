import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { loadI18n } from './i18n-runtime-test-helper.mjs';

// Keep the shared harness untouched. It has no portal boundary; provide one
// locally so the real menu module can load (menu props are tested as a leaf).
let createEnvironment;
function createSpeciesEnvironment() {
  if (createEnvironment) return createEnvironment();
  const url = new URL('./create-box-harness.mjs', import.meta.url);
  const source = readFileSync(url, 'utf8')
    .replaceAll('import.meta.url', JSON.stringify(url.href))
    .replace(
      "if (name === 'react') return hooks;",
      "if (name === 'react-dom') return { createPortal: node => node }; if (name === 'react') return hooks;",
    );
  const exports = {};
  vm.compileFunction(
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.CommonJS,
        target: ts.ScriptTarget.ES2022,
        esModuleInterop: true,
      },
    }).outputText,
    ['exports', 'require'],
  )(exports, createRequire(url));
  createEnvironment = exports.createBoxEnvironment;
  return createEnvironment();
}

const strain = (id, strain_number, extra = {}) => ({
  id,
  strain_number,
  code: `CAN-BRE-${id}`,
  name: '',
  active_box_count: 0,
  is_owned: true,
  is_box_usable: true,
  recorded_provenance_name: null,
  ...extra,
});
const box = (id, strainId, organizationId = 1, extra = {}) => ({
  id,
  global_code: `CAN-BRE-${strainId}.${id}`,
  organization: { id: organizationId },
  strain: { id: strainId, species: { id: 7 } },
  status: 'active',
  thermal_zone: null,
  latest_measurement: null,
  entered_on: null,
  ...extra,
});
function mount(extra = {}) {
  const env = createSpeciesEnvironment();
  env.document.documentElement = { lang: 'en' };
  const opened = [];
  const Panel = env.load('../src/components/SpeciesStrainsPanel.tsx').default;
  const view = env.instance(Panel, {
    strains: [strain(1, 1)],
    boxes: [],
    organizationId: 1,
    boxCollectionStatus: 'ready',
    sharedProvenance: null,
    speciesName: 'Cassiopea andromeda',
    openedStrainId: null,
    language: 'en',
    t: (key) => key,
    onRetryBoxCollection() {},
    onOpenBox: (...args) => opened.push(args),
    onOpenZone: (id) => opened.push(['zone', id]),
    ...extra,
  });
  const child = view.nodes.find((node) => node.type?.name === 'StrainItem');
  if (child) {
    env.renderNestedComponent(child.type);
    view.setProps({});
  }
  const row = view.nodes.find((node) => node.type?.name === 'SpeciesBoxRow');
  if (row) {
    env.renderNestedComponent(row.type);
    view.setProps({});
  }
  const all = (name) =>
    view.nodes.filter((node) =>
      (node.props.className ?? '').split(' ').includes(name),
    );
  return { env, view, opened, all };
}
const text = (node) => {
  if (node == null || typeof node === 'boolean') return '';
  if (Array.isArray(node)) return node.map(text).join(' ');
  return typeof node === 'object' ? text(node.props.children) : String(node);
};

test('authoritative X sorts numerically across collection codes without mutating input', () => {
  const { sortSpeciesStrains } = createSpeciesEnvironment().load(
    '../src/utils/speciesCatalog.ts',
  );
  const input = [
    strain(10, '10', { code: 'AAA-AAA-1' }),
    strain(2, 2, { code: 'ZZZ-ZZZ-999' }),
    strain(1, '01'),
    strain(0, 0),
  ];
  assert.deepEqual(
    Array.from(sortSpeciesStrains(input), (item) => item.id),
    [0, 1, 2, 10],
  );
  assert.deepEqual(
    input.map((item) => item.id),
    [10, 2, 1, 0],
  );
});

test('equal and malformed X use deterministic exact code and ID fallbacks', () => {
  const { sortSpeciesStrains } = createSpeciesEnvironment().load(
    '../src/utils/speciesCatalog.ts',
  );
  const input = [
    strain(9, null, { code: 'B' }),
    strain(8, '2x', { code: 'A' }),
    strain(7, '', { code: 'A' }),
    strain(6, -1, { code: 'A' }),
    strain(5, 1.5, { code: 'A' }),
    strain(4, '2', { code: 'B' }),
    strain(3, '02', { code: 'A' }),
    strain(2, '100000000000000000000'),
    strain(1, '99999999999999999999'),
  ];
  const expected = [3, 4, 1, 2, 5, 6, 7, 8, 9];
  assert.deepEqual(
    Array.from(sortSpeciesStrains(input), (item) => item.id),
    expected,
  );
  assert.deepEqual(
    Array.from(sortSpeciesStrains([...input].reverse()), (item) => item.id),
    expected,
  );
});

test('grouping retains only listed strains in the active institution and preserves box order', () => {
  const { groupBoxesByStrain } = createSpeciesEnvironment().load(
    '../src/utils/speciesCatalog.ts',
  );
  const groups = groupBoxesByStrain(
    [box(3, 1), box(2, 1, 2), box(1, 1), box(4, 99)],
    [1, 2],
    1,
  );
  assert.deepEqual(
    Array.from(groups.get(1), (item) => item.id),
    [3, 1],
  );
  assert.equal(groups.get(2).length, 0);
  assert.equal(groups.has(99), false);
});

test('panel keeps every strain visible in numeric order', () => {
  const h = mount({
    strains: [strain(10, 10), strain(2, 2), strain(1, 1)],
    openedStrainId: 2,
  });
  assert.deepEqual(
    h
      .all('species-strain-heading')
      .map((node) => text(node.querySelector('.species-strain-name'))),
    [
      'createBoxStrainLabel 1 (CAN-BRE-1)',
      'createBoxStrainLabel 2 (CAN-BRE-2)',
      'createBoxStrainLabel 10 (CAN-BRE-10)',
    ],
  );
  assert.equal(h.all('species-strain-body').length, 3);
  assert.equal(h.all('species-strain-toggle').length, 0);
});

test('box preview is the unchanged inventory component and menu offers only open detail', () => {
  const h = mount({ boxes: [box(3, 1), box(4, 1, 2), box(5, 99)] });
  const previews = h.view.nodes.filter(
    (node) => node.type === 'BoxTrackingPreview',
  );
  assert.equal(previews.length, 1);
  assert.equal(previews[0].props.boxId, 3);
  assert.equal(previews[0].props.speciesName, 'Cassiopea andromeda');
  const menu = h.view.nodes.find((node) => node.type?.name === 'RowActionMenu');
  assert.deepEqual(
    Array.from(menu.props.actions, (item) => [item.action, item.label]),
    [['open', 'createBoxOpenCreated']],
  );
  assert.match(menu.props.ariaLabel, /CAN-BRE-1\.3/);
  menu.props.onAction('open');
  previews[0].props.onOpenBox(3, previews[0].props.code);
  assert.deepEqual(h.opened, [
    [3, 'CAN-BRE-1.3'],
    [3, 'CAN-BRE-1.3'],
  ]);
  assert.equal(
    h.env.requests.length,
    0,
    'no eager histories or per-box fetches',
  );
  const previewSource = readFileSync(
    new URL('../src/components/BoxTrackingPreview.tsx', import.meta.url),
    'utf8',
  );
  assert.match(previewSource, /className="box-tracking-preview-link"/);
  assert.match(previewSource, /if \(event.button !== 0 \|\| event.ctrlKey/);
});

test('header shows code and country; expanded facts keep recorded origin, including zero GPS', () => {
  const t = localizedTranslator('en');
  const h = mount({
    t,
    strains: [
      strain(1, 1, {
        name: 'Recorded fallback',
        recorded_country_code: 'FR',
        recorded_provenance_name: 'Brest',
        translations: {
          en: {
            name: 'Recorded English name',
            description: 'Recorded description',
          },
        },
        origin: {
          source_type: 'field_collection',
          event_date: '2026-01-02',
          description: 'Recorded origin',
          origin_institution_name: 'Source institution',
          latitude: '0',
          longitude: null,
          technique: 'Recorded technique',
        },
      }),
    ],
  });
  const facts = text(h.all('species-strain-facts')[0]);
  assert.match(
    text(h.all('species-strain-heading')[0]),
    /Recorded English name \(CAN-BRE-1\).*Country : France/,
  );
  for (const key of [
    'speciesStrainLocation',
    'speciesStrainActiveBoxes',
    'speciesStrainOriginType',
    'strainAcquisitionFieldCollection',
  ]) {
    assert.ok(!facts.includes(t(key)), key);
  }
  assert.equal(
    text(h.all('species-strain-provenance')[0]),
    `${t('adminCountry')} : France , via ${t('strainAcquisitionFieldCollection')}`,
  );
  for (const value of [
    'Recorded English name',
    'Recorded description',
    'Source institution',
    'Recorded origin',
    'Recorded technique',
  ])
    assert.ok(facts.includes(value), value);
  assert.ok(facts.includes(`${t('speciesStrainLatitude')} 0`));
  assert.ok(!facts.includes(t('speciesStrainLongitude')));
  assert.equal(h.env.requests.length, 0);
});

test('absent optional metadata is not inferred; complete empty boxes stay zero', () => {
  const h = mount();
  assert.match(
    text(h.all('species-strain-heading')[0]),
    /adminCountry : speciesStrainNoProvenance/,
  );
  assert.equal(h.all('species-strain-facts').length, 0, 'no empty facts block');
  assert.equal(h.all('species-strain-boxes').length, 0);
  const loading = mount({ boxCollectionStatus: 'loading', boxes: [box(1, 1)] });
  assert.equal(
    loading.view.nodes.filter((node) => node.type === 'BoxTrackingPreview')
      .length,
    0,
  );
  assert.match(
    text(loading.all('species-detail-empty')[0]),
    /speciesStrainBoxesLoading/,
  );
});

test('eligible strains offer heading creation with the exact ID, including zero Boxes and no WoRMS data', () => {
  const created = [];
  const h = mount({
    strains: [
      strain(42, 1),
      strain(7, 2, { is_owned: false }),
      strain(99, 3, { is_box_usable: false }),
    ],
    onCreateBox: (id) => created.push(id),
  });
  const headings = h.all('species-strain-heading');
  const actions = headings.map((heading) =>
    heading.querySelector('.primary-button'),
  );
  assert.equal(h.all('box-inventory-row').length, 0);
  assert.equal(actions[2], null, 'ineligible strain has no creation action');
  for (const [index, action] of actions.entries()) {
    if (index === 2) continue;
    assert.equal(action.props.type, 'button');
    assert.equal(action.props.className, 'primary-button button-icon-label');
    assert.equal(text(action).trim(), 'createBoxNew');
    assert.ok(
      action
        .querySelectorAll('*')
        .some(
          (node) =>
            node.props.name === 'plus' && node.props['aria-hidden'] === 'true',
        ),
    );
    assert.equal(
      headings[index].props.children.at(-1).props.onClick,
      action.props.onClick,
      'action follows the summary at the far right',
    );
    action.props.onClick();
  }
  assert.deepEqual(
    created,
    [42, 7],
    'server eligibility, not ownership or box count, controls the action',
  );
  assert.equal(
    h.env.requests.length,
    0,
    'creation is delegated without eager requests',
  );
});

test('creation requires the parent permission callback and stays available independently of collection state', () => {
  for (const boxCollectionStatus of ['ready', 'loading', 'error']) {
    for (const boxes of [[], [box(3, 1)]]) {
      const props = { boxCollectionStatus, boxes };
      const readOnly = mount(props);
      assert.equal(
        readOnly
          .all('species-strain-heading')[0]
          .querySelector('.primary-button'),
        null,
      );
      const writable = mount({ ...props, onCreateBox() {} });
      assert.equal(
        text(
          writable
            .all('species-strain-heading')[0]
            .querySelector('.primary-button'),
        ).trim(),
        'createBoxNew',
      );
    }
  }
});

function localizedTranslator(language) {
  const catalog = loadI18n().translations[language];
  return (key) => {
    assert.equal(typeof catalog[key], 'string', `${language}.${key}`);
    return catalog[key];
  };
}

for (const language of ['fr', 'en']) {
  test(`${language}: recorded acquisition follows country inline, retaining title and all other metadata`, () => {
    const t = localizedTranslator(language);
    const h = mount({
      language,
      t,
      onCreateBox() {},
      strains: [
        strain(42, 2, {
          name: 'Descriptive strain',
          recorded_country_code: 'FR',
          recorded_provenance_name: 'Brest',
          origin: {
            source_type: 'donation',
            event_date: '2026-01-02',
            origin_institution_name: 'Recorded donor',
            description: 'Recorded origin',
            latitude: '0',
            longitude: '0',
            technique: 'Recorded technique',
          },
          translations: {
            [language]: {
              name: 'Localized strain',
              description: 'Recorded description',
            },
          },
        }),
      ],
    });
    const heading = h.all('species-strain-heading')[0];
    assert.equal(
      text(heading.querySelector('.species-strain-name')),
      'Localized strain (CAN-BRE-42)',
    );
    assert.equal(
      text(heading.querySelector('.species-strain-provenance')),
      `${t('adminCountry')} : France , via ${t('strainAcquisitionDonation')}`,
    );
    assert.equal(
      text(heading.querySelector('.primary-button')).trim(),
      language === 'fr' ? 'Nouvelle boîte' : 'New box',
    );
    const facts = text(h.all('species-strain-facts')[0]);
    for (const value of [
      'Localized strain',
      'Recorded description',
      'Recorded donor',
      'Recorded origin',
      'Recorded technique',
      t('speciesStrainOriginDate'),
      t('speciesStrainLatitude'),
      t('speciesStrainLongitude'),
    ])
      assert.ok(facts.includes(value), value);
    assert.equal(
      h
        .all('species-strain-facts')[0]
        .querySelectorAll('*')
        .filter((node) => node.type === 'dd' && text(node) === '0').length,
      2,
    );
    assert.doesNotMatch(facts, new RegExp(t('speciesStrainOriginType')));
    assert.ok(
      !facts.includes(t('strainAcquisitionDonation')),
      'no standalone acquisition fact',
    );
    assert.ok(
      !facts.includes(t('speciesStrainLocation')),
      'known country needs no legacy location fallback',
    );
  });

  test(`${language}: legacy missing acquisition never invents a type or country`, () => {
    const t = localizedTranslator(language);
    for (const recorded_country_code of [undefined, null, '']) {
      for (const origin of [
        undefined,
        null,
        {},
        { source_type: null },
        { source_type: '' },
        { source_type: '   ' },
        { source_type: 'unrecognized' },
        { source_type: 'toString' },
      ]) {
        const h = mount({
          language,
          t,
          strains: [
            strain(1, 1, {
              origin,
              recorded_country_code,
              recorded_provenance_name: 'Brest',
            }),
          ],
        });
        const provenance = text(h.all('species-strain-provenance')[0]).trim();
        assert.equal(
          provenance,
          `${t('adminCountry')} : ${t('speciesStrainNoProvenance')}`,
        );
        assert.ok(
          !provenance.includes('Brest'),
          'a collection location is not a recorded country',
        );
        const facts = text(h.all('species-strain-facts')[0]);
        assert.ok(
          facts.includes(`${t('speciesStrainLocation')} Brest`),
          'legacy location remains recorded metadata, even without origin',
        );
        assert.ok(!facts.includes(t('speciesStrainOriginType')));
        assert.equal(
          text(
            h
              .all('species-strain-heading')[0]
              .querySelector('.species-strain-name'),
          ),
          `${t('createBoxStrainLabel')} 1 (CAN-BRE-1)`,
        );
      }
    }
    for (const [source_type, key] of Object.entries({
      field_collection: 'strainAcquisitionFieldCollection',
      artificial_fertilization_wild: 'strainAcquisitionFertilizationWild',
      artificial_fertilization_captive: 'strainAcquisitionFertilizationCaptive',
      reproduction: 'speciesStrainOriginReproduction',
      donation: 'strainAcquisitionDonation',
      exchange: 'speciesStrainOriginExchange',
      unknown: 'speciesStrainOriginUnknown',
    })) {
      const h = mount({
        language,
        t,
        strains: [strain(1, 1, { origin: { source_type } })],
      });
      assert.equal(
        text(h.all('species-strain-provenance')[0]),
        `${t('adminCountry')} : ${t('speciesStrainNoProvenance')} , via ${t(key)}`,
      );
    }
  });
}

test('species and collection code helpers share normalization without accepting malformed saved codes', () => {
  const helpers = createSpeciesEnvironment().load(
    '../src/utils/speciesCatalog.ts',
  );
  assert.equal(helpers.normalizeSpeciesCode(' c a n '), 'CAN');
  assert.equal(helpers.normalizeLocationCode(' b r e '), 'BRE');
  for (const value of [null, undefined, '', 'can', ' CA', 'CANN'])
    assert.equal(helpers.isUsableSpeciesCode(value), false);
  assert.equal(helpers.isUsableSpeciesCode('CAN'), true);
});

// Box rows reuse the Inventory's row layout ------------------------------------

const rowsOf = (h) => h.all('box-inventory-row');
const cell = (row, name) => row.querySelector(`.${name}`);

test('box rows use the Inventory layout: unlabeled menu column and no table', () => {
  const h = mount({ boxes: [box(3, 1)] });
  assert.ok(h.all('box-inventory-list')[0], 'Inventory list container');
  assert.equal(
    h.view.nodes.filter((node) =>
      ['table', 'thead', 'th', 'tr'].includes(node.type),
    ).length,
    0,
  );
  const head = h.all('box-inventory-table-head')[0];
  assert.equal(head.props['aria-hidden'], 'true');
  const headings = head
    .querySelectorAll('*')
    .filter((node) => node.type === 'span')
    .map(text);
  assert.deepEqual(headings, [
    'boxInventoryBox',
    'boxInventoryLocation',
    'boxInventoryDates',
    'boxInventoryLatestCounts',
    '',
  ]);
  assert.doesNotMatch(
    text(head),
    /boxInventoryActions/,
    'no visible Actions heading',
  );
  const [row] = rowsOf(h);
  assert.equal(row.type, 'article');
  assert.equal(row.props['aria-label'], 'CAN-BRE-1.3, Active');
  assert.match(row.props.className, /\bis-alive\b/);
  // The Box reference is the Inventory's preview component, inside the identity cell.
  assert.equal(
    cell(row, 'box-inventory-identity').querySelector('*').type,
    'BoxTrackingPreview',
  );
  const menu = cell(row, 'box-inventory-actions').querySelector('*');
  assert.equal(menu.type.name, 'RowActionMenu');
  assert.equal(
    menu.props.ariaLabel,
    'boxInventoryActions CAN-BRE-1.3',
    'accessible name kept',
  );
});

test('dates and latest counts: real zero shown, missing data never becomes 0', () => {
  const h = mount({
    boxes: [
      box(3, 1, 1, {
        first_measurement_on: '2024-01-05',
        last_measurement_on: '2026-11-13',
        created_on: '2026-01-05',
        entered_on: '2025-12-01',
        latest_measurement: {
          measured_on: '2026-11-01',
          polyp_count: 0,
          ephyrae_count: 0,
        },
      }),
      box(4, 1, 1, {
        created_on: '2026-01-06',
        entered_on: '2025-12-02',
        first_measurement_on: null,
        last_measurement_on: null,
        latest_measurement: null,
      }),
      box(5, 1, 1, {
        status: 'inactive',
        created_on: '2026-02-01',
        first_measurement_on: '2020-03-01',
        last_measurement_on: '2026-10-01',
        latest_measurement: {
          measured_on: '2026-10-01',
          polyp_count: 12,
          ephyrae_count: 3,
        },
      }),
    ],
  });
  const [zero, empty, counted] = rowsOf(h);
  const dates = text(cell(zero, 'box-inventory-dates'));
  assert.match(dates, /boxInventoryCreatedOn\s*5 Jan 2024/);
  assert.doesNotMatch(
    dates,
    /5 Jan 2026|Dec 2025|1 Nov 2026/,
    'technical dates and latest summary are not scientific bounds',
  );
  assert.equal(
    cell(zero, 'box-inventory-dates').props.children.length,
    2,
    'one Dates cell with exactly two lines',
  );
  assert.match(dates, /boxInventoryLastMeasurement\s*13 Nov 2026/);
  assert.match(
    text(cell(zero, 'box-inventory-counts')),
    /^\s*0\s*polyps\s*0\s*ephyraeFull\s*$/,
  );
  assert.match(
    text(cell(empty, 'box-inventory-dates')),
    /boxInventoryCreatedOn\s*boxInventoryNoData\s*boxInventoryLastMeasurement\s*boxInventoryNoData/,
  );
  assert.equal(
    text(cell(empty, 'box-inventory-counts')).trim(),
    'boxInventoryNoMeasurement',
  );
  assert.match(
    text(cell(counted, 'box-inventory-counts')),
    /^\s*12\s*polyps\s*3\s*ephyraeFull\s*$/,
  );
  assert.match(
    text(cell(counted, 'box-inventory-dates')),
    /boxInventoryCreatedOn\s*1 Mar 2020/,
  );
  assert.equal(h.env.requests.length, 0, 'no per-box request');
});

test('location links to the zone in-app; inactive boxes keep their latest reading and show no current location', () => {
  const zone = { id: 9, name: 'Zone 25.0°C' };
  const h = mount({
    boxes: [
      box(3, 1, 1, { thermal_zone: zone }),
      box(4, 1, 1, {
        status: 'inactive',
        thermal_zone: zone,
        latest_measurement: {
          measured_on: '2026-03-02',
          polyp_count: 0,
          ephyrae_count: 1,
        },
      }),
      box(5, 1, 1, { status: 'pending_review' }),
    ],
  });
  const [active, inactive, pending] = rowsOf(h);
  const link = cell(active, 'box-inventory-location').querySelector('*');
  assert.equal(link.type, 'a');
  assert.equal(link.props.href, '/zones/9');
  let prevented = false;
  link.props.onClick({
    button: 0,
    preventDefault() {
      prevented = true;
    },
  });
  assert.equal(prevented, true);
  link.props.onClick({
    button: 0,
    ctrlKey: true,
    preventDefault() {
      throw new Error('modified click must stay native');
    },
  });
  assert.deepEqual(h.opened, [['zone', 9]]);
  assert.match(inactive.props.className, /\bis-dead\b/);
  assert.equal(
    text(inactive.querySelector('.box-inventory-row-status')),
    'Inactive',
  );
  assert.equal(
    text(cell(inactive, 'box-inventory-location')),
    'boxInventoryWithoutLocation',
  );
  assert.match(
    text(cell(inactive, 'box-inventory-counts')),
    /^\s*0\s*polyps\s*1\s*ephyraeFull\s*$/,
  );
  assert.equal(
    text(pending.querySelector('.box-inventory-row-status')),
    'boxInventoryReviewMarker',
  );
  // No mutation is offered: no assignment button, only the open-detail menu entry.
  assert.equal(
    h.view.nodes.filter(
      (node) => node.type === 'button' && /assign/i.test(text(node)),
    ).length,
    0,
  );
  const menus = h.view.nodes.filter(
    (node) => node.type?.name === 'RowActionMenu',
  );
  assert.equal(menus.length, 3);
  assert.ok(
    menus.every(
      (menu) =>
        menu.props.actions.length === 1 &&
        menu.props.actions[0].action === 'open',
    ),
  );
});

test('strain header groups title, code and country without duplicate facts', () => {
  const h = mount({
    strains: [
      strain(1, 1, {
        name: 'Brest wild',
        recorded_country_code: 'FR',
        recorded_provenance_name: 'Brest',
        active_box_count: 2,
      }),
      strain(2, 2),
    ],
    boxes: [box(3, 1)],
    sharedProvenance: 'Brest',
  });
  const toggles = () => h.all('species-strain-heading');
  assert.deepEqual(
    toggles().map((node) => text(node.querySelector('.species-strain-name'))),
    ['Brest wild (CAN-BRE-1)', 'createBoxStrainLabel 2 (CAN-BRE-2)'],
  );
  const summary = text(toggles()[0].querySelector('.species-strain-summary'));
  assert.match(summary, /Brest wild \(CAN-BRE-1\).*adminCountry : France/);
  assert.match(text(toggles()[1]), /adminCountry : speciesStrainNoProvenance/);
  assert.doesNotMatch(
    text(toggles()[0]),
    /speciesDetailBox|speciesStrainActiveBoxes/,
  );

  const facts = text(h.all('species-strain-facts')[0]);
  assert.doesNotMatch(
    facts,
    /taxonomyStrainCode|speciesStrainLocation|speciesStrainActiveBoxes|CAN-BRE-1|Brest wild \(CAN-BRE-1\)/,
  );
});

test('strain sections are permanently visible with noninteractive headings', () => {
  const h = mount({
    strains: [strain(1, 1), strain(2, 2)],
    boxes: [box(3, 1)],
  });
  assert.equal(h.all('species-strain-body').length, 2);
  assert.equal(h.all('species-strain-toggle').length, 0);
  for (const heading of h.all('species-strain-heading')) {
    assert.equal(heading.type, 'h4');
    for (const prop of [
      'onClick',
      'onMouseEnter',
      'onFocus',
      'onKeyDown',
      'aria-expanded',
      'tabIndex',
    ])
      assert.equal(heading.props[prop], undefined, prop);
    assert.equal(
      heading
        .querySelectorAll('*')
        .filter((node) => node.type === 'button' || node.type === 'svg').length,
      0,
    );
  }
  assert.equal(
    h.view.nodes.filter((node) => node.type === 'BoxTrackingPreview').length,
    1,
  );
});

test('strain disclosure styles do not animate or resize the row', () => {
  const css = readFileSync(
    new URL('../src/styles/pages/administration.css', import.meta.url),
    'utf8',
  );
  assert.match(
    css,
    /\.species-strain-summary\s*\{[^}]*display: grid;[^}]*gap: var\(--space-1\);/,
  );
  assert.match(
    css,
    /\.species-strain-heading\s*\{[^}]*justify-content: space-between;/,
  );
  assert.match(
    css,
    /\.species-strain-heading > \.primary-button\s*\{[^}]*min-height: 36px;/,
  );
  const rules = [...css.matchAll(/([^{}]*\.species-strain[^{}]*)\{([^}]*)\}/g)];
  assert.ok(rules.length > 0);
  for (const [, selector, body] of rules) {
    assert.doesNotMatch(body, /transition|animation/, selector.trim());
    if (/is-open/.test(selector))
      assert.doesNotMatch(
        body,
        /margin|font-size|font-weight|font-style|border:|padding/,
        selector.trim(),
      );
  }
  assert.doesNotMatch(css, /\.species-strain-code|\.species-box-table/);
});
