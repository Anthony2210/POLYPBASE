import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { createBoxEnvironment } from './create-box-harness.mjs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const section = read('../src/components/SpeciesStrainsAdminSection.tsx');
const adminView = read('../src/components/AdminView.tsx');
const app = read('../src/App.tsx');
const DETAIL_PATH = '/api/admin/institution-species/1/';
const NESTED = new Set(['SpeciesDetail', 'SpeciesStrainsPanel', 'StrainItem']);

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

function nodeText(node) {
  return [node, ...node.querySelectorAll('*')]
    .flatMap((item) => [].concat(item.props.children ?? []))
    .filter((child) => typeof child === 'string' || typeof child === 'number')
    .join(' ');
}

function mount({ target = { kind: 'profile', id: 1 }, t = (key) => key, language = 'fr' } = {}) {
  const env = createBoxEnvironment();
  const Section = env.load('../src/components/SpeciesStrainsAdminSection.tsx').default;
  const view = env.instance(Section, {
    target, t, language, organizationId: 1, organizationName: 'Aquarium Test',
    boxes: [], boxCollectionStatus: 'ready', onRetryBoxCollection() {},
    onOpenSpecies() {}, onBack() {}, onOpenBox() {},
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
    env, view,
    async respond(path, payload, { error } = {}) {
      const request = [...env.requests].reverse().find((item) => item.path === path && !item.done);
      assert.ok(request, `Missing GET ${path}`);
      request.done = true;
      if (error) request.reject(error); else request.resolve(payload);
      await view.settle();
      renderLeaves();
      return request;
    },
    byClass(name) { return view.nodes.filter((node) => (node.props.className ?? '').split(' ').includes(name)); },
    button(label) { return view.nodes.find((node) => node.type === 'button' && nodeText(node).includes(label)); },
    get trigger() { return h.byClass('species-code-change-trigger')[0]; },
    get panel() { return h.byClass('code-change-panel')[0]; },
    get input() { return view.nodes.find((node) => node.props.id === 'species-code-change-input'); },
    get previews() { return env.requests.filter((item) => item.path.includes('/code-change-preview/')); },
  };
  return h;
}


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

test('the tracking preview loads Box detail only when opened', () => {
  const tracking = read('../src/components/BoxTrackingPreview.tsx');
  // Data is requested by the popover content only, once it is opened.
  assert.match(tracking, /\{isOpen \? createPortal\(/);
  assert.match(tracking, /apiGet<TrackingBoxDetail>\(`\/api\/boxes\/\$\{boxId\}\/`, \{ signal: controller\.signal \}\)/);
  assert.match(tracking, /onFocus=\{\(\) => \{ if \(!suppressFocus\.current\) \{ clearTimers\(\); setIsOpen\(true\); \} \}\}/);
});

test('Species detail receives the active institution and keeps the preview hidden in desktop-only Administration', () => {
  assert.match(adminView, /<SpeciesStrainsAdminSection\s+key=\{activeOrganizationId\}\s+target=\{speciesTarget\}\s+organizationId=\{activeOrganizationId\}/);
  assert.match(adminView, /organizationName=\{profile\?\.active_organization\?\.name \?\? ''\}\s+boxes=\{boxes\}\s+boxCollectionStatus=\{boxCollectionStatus\}\s+onRetryBoxCollection=\{onRetryBoxCollection\}\s+language=\{language\}\s+onOpenSpecies=\{onOpenSpecies\}\s+onOpenBox=\{onOpenBox\}/);
  assert.doesNotMatch(section, /SpeciesCodeChangePreview|setIsCodeChangeOpen/);
  // The retired preview has no route or mount in the desktop-only Species detail.
  assert.doesNotMatch(app, /code-change/);
  assert.match(app, /if \(activeTab === 'admin' && !isDesktopApp\) \{\s*replaceRoute\(\{ tab: 'pilotage', boxCode: null, boxId: null \}, '\/', true\);\s*return;\s*\}/);
  assert.match(app, /canOpenAdmin=\{canUseAdmin && isDesktopApp\}/);
  assert.doesNotMatch(read('../src/styles/responsive/tablet.css') + read('../src/styles/responsive/phone.css'), /code-change/);
});
