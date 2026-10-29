import assert from 'node:assert/strict';

import { describe, test } from 'node:test';
import { createBoxEnvironment, createBoxHarness, createPilotageHarness, FOCUSABLE_SELECTOR, plain, submitEvent, text } from './create-box-harness.mjs';

const complete = (prefix, number = '1', origin = 'ATL', provenanceName = null) => ({
  status: 'usable', prefix, species_code: prefix.slice(0, 3), origin_code: origin, strain_number: number, recorded_provenance_name: provenanceName,
});
const strainOption = (id = 1, overrides = {}) => ({
  id, species: { id: 101, scientific_name: 'Aurelia aurita' }, name: '', code: 'AUR-ATL-1',
  active_box_count: 0, identity: complete('AUR-ATL-1'), ...overrides,
});
const references = (id = 1, code = 'AUR-ATL-1', species = 'Aurelia aurita') => [
  strainOption(id, { code, species: { id: 100 + id, scientific_name: species }, identity: complete(code) }),
];
const box = (code, organization = 1, strain = 1) => ({ global_code: code, organization: { id: organization }, strain: { id: strain } });
const field = (h, name) => h.panel.field(`create-box-${name}-field`);
const options = h => h.panel.nodes.filter(node => node.props.role === 'option');
const option = (h, id) => options(h).find(node => node.props.id === `create-box-strain-option-${id}`);
const selectedStrain = h => {
  const selected = options(h).find(node => node.props['aria-selected'] === true);
  return selected ? Number(selected.props.id.split('-').at(-1)) : null;
};
const search = h => h.panel.nodes.find(node => node.props.role === 'combobox');
const save = h => h.panel.byClass('create-box-submit');

const created = (code = 'AUR-ATL-1.007') => ({ id: 42, global_code: code, strain: { id: 1 } });
const errors = h => h.panel.nodes.filter(node => node.matches('.inline-error')).map(text);
const panelText = h => h.panel.nodes.map(n => [].concat(n.props.children ?? []).filter(c => typeof c === 'string' || typeof c === 'number').join('')).join(' ');
async function ready(h = createBoxHarness(), strains = references()) {
  h.env.requests.at(-1).resolve(strains);
  await h.panel.settle();
  h.panel.click(options(h)[0]);
  h.panel.change(field(h, 'location'), '10');
  assert.equal(save(h).disabled, false);
  return h;
}
function edit(h, name, value) {
  h.panel.change(name === 'notes' ? h.panel.nodes.find(node => node.type === 'textarea') : field(h, name), value);
}
async function beginMutation(h) {
  const pending = h.panel.submit();
  await h.panel.settle();
  assert.equal(h.mutations.length, 1);
  return { pending };
}
function key(h, name) {
  const event = { key: name, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
  search(h).props.onKeyDown(event);
  h.panel.flush();
  return event;
}
describe('Actual extracted CreateBoxPanel', () => {
  test('inline starts closed, requests options/strains only on open, and ignores controlled isOpen', async () => {
    const h = createBoxHarness({ presentation: 'inline', isOpen: true, exportOptions: null });
    assert.equal(h.panel.form, undefined);
    assert.equal(h.env.requests.length, 0);
    assert.equal(h.optionsRequests, 0);
    assert.equal(h.boxCollectionRequests, 0);
    h.panel.click(h.panel.byClass('create-box-toggle'));
    assert.ok(h.panel.form);
    assert.equal(h.optionsRequests, 1);
    assert.equal(h.boxCollectionRequests, 0);
    assert.equal(h.env.requests[0].path, '/api/boxes/automatic/strains/');
    h.panel.click(h.panel.byClass('create-box-toggle'));
    h.env.requests[0].resolve(references());
    await h.panel.settle();
    assert.equal(h.panel.form, undefined);
    assert.equal(h.env.listenerCount(), 0);
    h.panel.unmount();
  });

  test('controlled modal renders nothing closed, fetches lazily, and uses onOpenChange', () => {
    const h = createBoxHarness({ isOpen: false, exportOptions: null });
    assert.equal(h.panel.value, null);
    assert.equal(h.env.requests.length, 0);
    h.panel.setProps({ isOpen: true });
    assert.equal(h.optionsRequests, 1);
    assert.equal(h.boxCollectionRequests, 0);
    assert.equal(h.panel.dialog.props['aria-modal'], 'true');
    assert.equal(h.panel.dialog.props['aria-labelledby'], 'create-box-modal-title');
    assert.equal(h.env.document.activeElement, h.panel.byClass('modal-close-button'));
    h.panel.click(h.panel.byClass('modal-close-button'));
    assert.deepEqual(h.openChanges, [false]);
    assert.ok(h.panel.dialog, 'The parent, not the panel, owns controlled visibility');
    let stopped = false;
    h.panel.dialog.props.onMouseDown({ stopPropagation() { stopped = true; } });
    assert.equal(stopped, true);
    h.panel.byClass('modal-backdrop').props.onMouseDown();
    assert.deepEqual(h.openChanges, [false, false]);
    h.panel.unmount();
  });

  test('no organization means no strain request and no submission', async () => {
    const h = createBoxHarness({ profile: null });
    assert.equal(h.env.requests.length, 0);
    assert.equal(save(h).disabled, true);
    const event = submitEvent();
    await h.panel.submit(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(h.mutations.length, 0);
    h.panel.unmount();
  });

  test('deferred strains display loading, disable save, then expose the empty state', async () => {
    const h = createBoxHarness();
    assert.ok(panelText(h).includes('loading'));
    assert.equal(save(h).disabled, true);
    h.env.requests[0].resolve([]);
    await h.panel.settle();
    assert.ok(panelText(h).includes('createBoxNoOptions'));
    assert.equal(panelText(h).includes('loading'), false);
    assert.equal(save(h).disabled, true);
    h.panel.unmount();
  });

  for (const kind of ['api', 'generic']) test(`strain ${kind} failure shows real error mapping and retries after reopen`, async () => {
    const h = createBoxHarness();
    h.env.requests[0].reject(kind === 'api' ? new h.api.ApiError(500, 'Strains unavailable') : new Error('private error'));
    await h.panel.settle();
    assert.deepEqual(errors(h), [(kind === 'api' ? 'Strains unavailable' : 'taxonomyLoadError') + ' createBoxRetry']);
    assert.equal(save(h).disabled, true);
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
    assert.deepEqual(errors(h), []);
    h.env.requests[1].resolve(references());
    await h.panel.settle();
    assert.equal(options(h).length, 1);
    h.panel.unmount();
  });

  for (const completion of ['resolve', 'reject']) test(`strain stale ${completion} cannot replace newer organization data or clear loading`, async () => {
    const h = createBoxHarness();
    const old = h.env.requests[0];
    h.panel.setProps({ profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' } } });
    assert.equal(h.env.requests.length, 2);
    old[completion](completion === 'resolve' ? references(9, 'OLD-OLD-9') : new h.api.ApiError(500, 'Old failure'));
    await h.panel.settle();
    assert.deepEqual(errors(h), []);
    assert.ok(panelText(h).includes('loading'));
    h.env.requests[1].resolve(references(2, 'NEW-NEW-2'));
    await h.panel.settle();
    assert.ok(option(h, 2));
    assert.equal(panelText(h).includes('OLD-OLD-9'), false);
    h.panel.unmount();
  });

  test('rapid A -> B -> A strain generations ignore both older requests after latest success', async () => {
    const h = createBoxHarness();
    h.panel.setProps({ profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' } } });
    h.panel.setProps({ profile: h.props.profile });
    assert.equal(h.env.requests.length, 3);
    h.env.requests[2].resolve(references(3, 'LAT-EST-3'));
    await h.panel.settle();
    h.env.requests[0].resolve(references(1, 'OLD-OLD-1'));
    h.env.requests[1].reject(new h.api.ApiError(500, 'Stale B failure'));
    await h.panel.settle();
    assert.deepEqual(errors(h), []);
    assert.equal(panelText(h).includes('loading'), false);
    assert.deepEqual(options(h).map(node => node.props.id), ['create-box-strain-option-3']);
    h.panel.unmount();
  });

  for (const completion of ['resolve', 'reject']) test(`strain ${completion} after unmount performs no state updates`, async () => {
    const h = createBoxHarness();
    h.panel.unmount();
    h.env.requests[0][completion](completion === 'resolve' ? references() : new Error('Late'));
    await h.panel.settle();
    assert.equal(h.panel.updatesAfterUnmount, 0);
    assert.equal(h.env.listenerCount(), 0);
  });

  test('zones are organization scoped and removed zone selection is cleared', async () => {
    const h = await ready(createBoxHarness({ exportOptions: { zones: [
      { id: 10, organization_id: 1, name: 'Own' }, { id: 20, organization_id: 2, name: 'Foreign' },
    ] } }));
    assert.equal(text(field(h, 'location')).includes('Foreign'), false);
    h.panel.setProps({ exportOptions: { zones: [] } });
    assert.equal(field(h, 'location').props.value, '');
    assert.equal(save(h).disabled, true);
    assert.ok(panelText(h).includes('createBoxNoLocations'));
    h.panel.unmount();
  });

  test('strains are grouped under their species; multiple strains of one species stay distinguishable', async () => {
    const h = createBoxHarness();
    h.env.requests[0].resolve([
      strainOption(1, { active_box_count: 3 }),
      strainOption(2, { code: 'AUR-MED-2', identity: complete('AUR-MED-2', '2', 'MED', 'Mediterranean population'), name: 'Lagoon line' }),
      strainOption(3, { code: '1-LAB', identity: { status: 'unusable', reason: 'code_format' } }),
      strainOption(4, { species: { id: 202, scientific_name: 'Cassiopea andromeda' }, code: 'CAN-LAB-1', identity: complete('CAN-LAB-1', '1', 'LAB') }),
    ]);
    await h.panel.settle();
    const headings = h.panel.nodes.filter(node => node.matches('.strain-picker-species')).map(text);
    assert.deepEqual(headings, ['Aurelia aurita', 'Cassiopea andromeda']);
    assert.deepEqual(options(h).map(node => node.props.id.split('-').at(-1)), ['1', '2', '4'], 'Only usable identities can be selected');
    assert.ok(text(option(h, 1)).includes('createBoxStrainLabel 1'));
    assert.ok(text(option(h, 1)).includes('AUR-ATL-1'));
    assert.ok(text(option(h, 1)).includes('3 createBoxActiveBoxes'));
    assert.ok(text(option(h, 2)).includes('createBoxStrainLabel 2'));
    assert.ok(text(option(h, 2)).includes('AUR-MED-2'));
    assert.ok(text(option(h, 2)).includes('0 createBoxActiveBoxes'));
    assert.equal(option(h, 3), undefined);
    assert.equal(panelText(h).includes('1-LAB'), false);
    h.panel.unmount();
  });

  test('search filters by species, origin, strain number or code, accent and case insensitive', async () => {
    const h = createBoxHarness();
    h.env.requests[0].resolve([
      strainOption(1),
      strainOption(2, { species: { id: 202, scientific_name: 'Cyanea capillata' }, code: 'CYA-NOR-2', identity: complete('CYA-NOR-2', '2', 'NOR', 'Norvège') }),
    ]);
    await h.panel.settle();
    h.panel.change(search(h), '  CYANEA ');
    assert.deepEqual(options(h).map(node => node.props.id), ['create-box-strain-option-2']);
    h.panel.change(search(h), 'norvege');
    assert.deepEqual(options(h).map(node => node.props.id), ['create-box-strain-option-2']);
    h.panel.change(search(h), 'aurelia atl');
    assert.deepEqual(options(h).map(node => node.props.id), ['create-box-strain-option-1']);
    h.panel.change(search(h), 'nothing');
    assert.equal(options(h).length, 0);
    assert.ok(panelText(h).includes('createBoxStrainNoMatch'));
    h.panel.change(search(h), '');
    h.panel.click(option(h, 2));
    assert.equal(selectedStrain(h), 2);
    h.panel.unmount();
  });

  test('keyboard: arrows move the active option, Enter selects without submitting', async () => {
    const h = createBoxHarness();
    h.env.requests[0].resolve([strainOption(1), strainOption(2, { code: 'AUR-ATL-2', identity: complete('AUR-ATL-2', '2') })]);
    await h.panel.settle();
    h.panel.change(field(h, 'location'), '10');
    assert.equal(search(h).props['aria-controls'], 'create-box-strain-listbox');
    assert.equal(key(h, 'ArrowDown').defaultPrevented, true);
    assert.equal(search(h).props['aria-activedescendant'], 'create-box-strain-option-1');
    key(h, 'ArrowDown');
    key(h, 'ArrowDown');
    assert.equal(search(h).props['aria-activedescendant'], 'create-box-strain-option-2');
    assert.equal(key(h, 'Enter').defaultPrevented, true);
    assert.equal(selectedStrain(h), 2);
    assert.equal(h.mutations.length, 0);
    key(h, 'ArrowUp');
    key(h, 'Enter');
    assert.equal(selectedStrain(h), 1);
    h.panel.unmount();
  });

  test('draft creation no longer requests or renders a next identifier', async () => {
    const h = await ready();
    assert.equal(h.panel.byClass('created-box-identifier'), undefined);
    assert.equal(h.env.requests.length, 1, 'Only the Strain list is fetched');

    h.unmount();
  });

  test('successful creation still shows only the server-confirmed Box code', async () => {
    const h = await ready();
    assert.equal(h.panel.byClass('created-box-identifier'), undefined);
    const { pending } = await beginMutation(h);
    h.mutations[0].resolve(created('AUR-ATL-1.009'));
    await pending;
    h.panel.flush();
    assert.equal(text(h.panel.byClass('created-box-identifier-code')), 'AUR-ATL-1.009');

    assert.equal(h.panel.byClass('create-box-open-created').disabled, false);
    h.unmount();
  });

  test('no strain-creation action is offered from box creation', async () => {
    const h = await ready();
    assert.equal(h.panel.byClass('create-box-reference-action'), undefined);
    assert.equal(h.panel.nodes.some(node => node.type === 'QuickStrainCreator'), false);
    assert.equal(panelText(h).includes('quickStrainAdd'), false);
    h.panel.unmount();
  });

  for (const boundary of ['generation', 'unmount']) for (const completion of ['resolve', 'reject']) {
    test(`${boundary} during mutation ignores late ${completion} and never navigates`, async () => {
      const h = await ready();
      const { pending } = await beginMutation(h);
      if (boundary === 'generation') h.invalidateOperation();
      else h.panel.unmount();
      h.mutations[0][completion](completion === 'resolve' ? { id: 99 } : new h.api.ApiError(403, 'Late forbidden'));
      await pending;
      await h.panel.settle();
      assert.deepEqual(h.navigations, []);
      assert.equal(h.env.frameCount, 0);
      assert.equal(h.resolutions.length, 0);
      assert.equal(h.panel.updatesAfterUnmount, 0);
      if (boundary === 'generation') {
        assert.deepEqual(errors(h), []);
        h.panel.unmount();
      }
    });
  }

  test('modal traps Tab/ShiftTab, contains outside focus, Escape closes, and restores connected opener', () => {
    const h = createBoxHarness();
    const controls = h.panel.dialog.querySelectorAll(FOCUSABLE_SELECTOR);
    const first = controls[0], last = controls.at(-1);
    last.focus();
    assert.equal(h.env.key('Tab').defaultPrevented, true);
    assert.equal(h.env.document.activeElement, first);
    assert.equal(h.env.key('Tab', true).defaultPrevented, true);
    assert.equal(h.env.document.activeElement, last);
    h.env.opener.focus();
    h.env.key('Tab');
    assert.equal(h.env.document.activeElement, first);
    h.env.opener.focus();
    h.env.key('Tab', true);
    assert.equal(h.env.document.activeElement, last);
    first.focus();
    assert.equal(h.env.key('Tab').defaultPrevented, false);
    assert.equal(h.env.document.activeElement, controls[1]);
    assert.equal(h.env.key('Escape').defaultPrevented, true);
    assert.deepEqual(h.openChanges, [false]);
    h.panel.setProps({ isOpen: false });
    assert.equal(h.env.listenerCount(), 0);
    assert.equal(h.env.document.activeElement, h.env.opener);
    assert.deepEqual(plain(h.env.opener.focusCalls.at(-1)), { preventScroll: true });
    h.panel.unmount();
  });

  test('closing during creation keeps it running; reopening shows the result', async () => {
    const h = await ready();
    const { pending } = await beginMutation(h);
    h.panel.click(h.panel.byClass('modal-close-button'));
    h.panel.setProps({ isOpen: false });
    h.mutations[0].resolve({ id: 5, global_code: 'AUR-ATL-1.005', box_number: '005', strain: { id: 1 }, species: { scientific_name: 'Aurelia aurita' } });
    await pending;
    h.panel.setProps({ isOpen: true });
    assert.ok(panelText(h).includes('AUR-ATL-1.005'));
    h.panel.unmount();
  });

  test('unmount does not try restoring a disconnected opener', () => {
    const h = createBoxHarness();
    h.env.opener.isConnected = false;
    const calls = h.env.opener.focusCalls.length;
    h.panel.unmount();
    assert.equal(h.env.opener.focusCalls.length, calls);
    assert.equal(h.env.listenerCount(), 0);
    assert.equal(h.env.windowListenerCount('online'), 0);
  });

  test('StrictMode effect replay abandons first strain request, keeps one listener and restores operation lifetime', async () => {
    const h = createBoxHarness({}, { strictMode: true });
    assert.equal(h.env.requests.length, 2);
    assert.equal(h.env.listenerCount(), 1);
    assert.equal(h.env.windowListenerCount('online'), 1);
    h.env.requests[0].resolve(references(9, 'STA-LEE-9'));
    await h.panel.settle();
    assert.ok(panelText(h).includes('loading'));
    h.env.requests[1].resolve(references());
    await h.panel.settle();
    h.panel.click(options(h)[0]);
    edit(h, 'location', '10');
    const { pending } = await beginMutation(h);
    h.mutations[0].resolve({ id: 77, global_code: 'AUR-ATL-1.001', box_number: '001', strain: { id: 1 }, species: { scientific_name: 'Aurelia aurita' } });
    await pending;
    h.panel.flush();
    assert.deepEqual(h.navigations, []);
    h.panel.unmount();
    assert.equal(h.env.listenerCount(), 0);
    assert.equal(h.env.document.activeElement, h.env.opener);
  });

  for (const phase of ['draft', 'mutation']) test(`actual PilotageView organization key remounts/reset panel and guards old ${phase}`, async () => {
    const h = await ready(createPilotageHarness());
    const old = h.panel;
    edit(h, 'notes', 'Old notes');
    let pending = Promise.resolve();
    if (phase === 'mutation') {
      pending = old.submit();
      await old.settle();
      assert.equal(h.mutations.length, 1);
    }
    const node = h.parent.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
    assert.equal(node.key, '7:1');
    h.setParentProps({ isOptionsLoading: true });
    assert.equal(h.panel, old, 'Same parent key preserves the child instance');
    h.setParentProps({
      activeOrganizationId: 2,
      profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' }, memberships: [{ organization: { id: 2 }, role: 'admin' }] },
      exportOptions: { zones: [{ id: 20, organization_id: 2, name: 'Zone B' }] },
      isOptionsLoading: false,
    });
    const replacementNode = h.parent.nodes.find(candidate => candidate.type === node.type);
    assert.equal(replacementNode.key, '7:2');
    assert.notEqual(h.panel, old);
    assert.equal(field(h, 'location').props.value, '');
    assert.equal(h.panel.nodes.find(node => node.type === 'textarea').props.value, '');
    if (phase === 'mutation') h.mutations[0].resolve({ id: 99 });
    await pending;
    assert.equal(old.updatesAfterUnmount, 0);
    assert.equal(h.mutations.length, phase === 'draft' ? 0 : 1);
    assert.deepEqual(h.navigations, []);
    assert.equal(h.env.frameCount, 0);
    h.env.requests.at(-1).resolve(references(2, 'BBB-BBB-2'));
    await h.panel.settle();
    assert.ok(option(h, 2));
    assert.equal(selectedStrain(h), null, 'No strain is chosen on behalf of the researcher');
    assert.equal(h.env.listenerCount(), 1);
    h.unmount();
  });

  test('actual PilotageView uses the none key without an active organization', () => {
    const h = createPilotageHarness({ profile: { active_organization: null, is_superuser: true, memberships: [] } });
    const node = h.parent.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
    assert.equal(node.key, 'none:none');
    assert.equal(h.env.requests.length, 0);
    h.unmount();
  });

  for (const language of ['fr', 'en']) test(`actual ${language} translator renders labels, picker and error copy`, async () => {
    const env = createBoxEnvironment();
    const t = env.load('../src/i18n/index.ts').createTranslator(language);
    const h = createBoxHarness({ t }, { environment: env });
    assert.equal(text(h.panel.nodes.find(node => node.type === 'h2')), language === 'fr' ? 'Nouvelle boîte' : 'New box');
    assert.equal(text(save(h)), language === 'fr' ? 'Créer la boîte' : 'Create box');
    h.env.requests[0].reject(new Error('Private strain error'));
    await h.panel.settle();
    assert.deepEqual(errors(h), [t('taxonomyLoadError') + ' ' + t('createBoxRetry')]);
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
    await ready(h);
    assert.equal(search(h).props.placeholder, language === 'fr' ? 'Rechercher une espèce, une souche ou un code' : 'Search a species, strain or code');
    assert.ok(text(options(h)[0]).includes(language === 'fr' ? 'Souche 1' : 'Strain 1'));
    const { pending } = await beginMutation(h);
    assert.equal(h.confirmations.length, 0);
    h.mutations[0].reject(new h.api.ApiError(403, 'Raw refusal'));
    await pending;
    h.panel.flush();
    assert.deepEqual(errors(h), [language === 'fr'
      ? 'Ce compte ne peut pas créer de boîte. Merci de contacter un administrateur.'
      : 'This account cannot create boxes. Please contact your administrator.']);
    h.unmount();
  });
});

describe('Actual extracted code/profile utilities', () => {
  test('next code scopes boxes, ignores nonnumeric codes and preserves highest-number padding', () => {
    const { buildNextBoxCode } = createBoxEnvironment().load('../src/utils/boxCode.ts');
    assert.deepEqual(plain(buildNextBoxCode([], { id: 1, code: 'AUR' }, 1)), { globalCode: 'AUR.001', boxNumber: '001' });
    assert.deepEqual(plain(buildNextBoxCode([
      box('AUR.0009'), box('AUR.002'), box('AUR.bad'), box('AUR.999', 2), box('OTHER.999', 1, 2),
    ], { id: 1, code: 'AUR' }, 1)), { globalCode: 'AUR.0010', boxNumber: '0010' });
    assert.deepEqual(plain(buildNextBoxCode([box('AUR.999')], { id: 1, code: 'AUR' }, 1)), { globalCode: 'AUR.1000', boxNumber: '1000' });
  });
  test('box-number matching normalizes zeros/whitespace and preserves permissive nonnumeric-code behavior', () => {
    const { boxCodeMatchesBoxNumber: matches } = createBoxEnvironment().load('../src/utils/boxCode.ts');
    for (const [code, number, expected] of [
      [' AUR.001 ', ' 1 ', true], ['AUR.000', '0', true], ['AUR.002', '001', false],
      ['AUR.001suffix', '1', true], ['AUR', 'anything', true], ['AUR.001', '', false],
    ]) assert.equal(matches(code, number), expected, `${code} / ${number}`);
  });
  test('extract and normalize preserve zero, distinguish missing numbers and trim nonnumeric values', () => {
    const { extractBoxNumberFromCode: extract, normalizeBoxNumber: normalize } = createBoxEnvironment().load('../src/utils/boxCode.ts');
    for (const [code, expected] of [[' AUR.000 ', '000'], ['AUR.001suffix', '001'], ['AUR', null], ['AUR.bad', null], ['', null]]) {
      assert.equal(extract(code), expected, code);
    }
    for (const [number, expected] of [['000', '0'], [' 0 ', '0'], [' 001 ', '1'], ['', ''], ['  ', ''], [' custom ', 'custom']]) {
      assert.equal(normalize(number), expected, number);
    }
    assert.notEqual(normalize('000'), normalize(''));
  });

  test('admin role is active-organization scoped; superuser and explicit organization override are preserved', () => {
    const { userHasAdminRole } = createBoxEnvironment().load('../src/utils/profilePermissions.ts');
    const profile = { active_organization: { id: 1 }, memberships: [
      { organization: { id: 1 }, role: 'lab_technician' }, { organization: { id: 2 }, role: 'admin' },
    ] };
    assert.equal(userHasAdminRole(null), false);
    assert.equal(userHasAdminRole(profile), false);
    assert.equal(userHasAdminRole(profile, 2), true);
    assert.equal(userHasAdminRole({ ...profile, active_organization: null }), false);
    assert.equal(userHasAdminRole({ ...profile, is_superuser: true }), true);
  });
});
