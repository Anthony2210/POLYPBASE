import assert from 'node:assert/strict';

import { describe, test } from 'node:test';
import { createBoxEnvironment, createBoxHarness, createPilotageHarness, FOCUSABLE_SELECTOR, plain, submitEvent, text } from './create-box-harness.mjs';

const references = (id = 1, code = 'AUR', species = 'Aurelia aurita') => ({
  strains: [{ id, code, species: 100 + id, species_scientific_name: species }],
});
const box = (code, organization = 1, strain = 1) => ({ global_code: code, organization: { id: organization }, strain: { id: strain } });
const field = (h, name) => h.panel.field(`create-box-${name}-field`);
const save = h => h.panel.nodes.find(node => node.type === 'button' && node.props.type === 'submit');
const errors = h => h.panel.nodes.filter(node => node.matches('.inline-error')).map(text);
async function ready(h = createBoxHarness()) {
  h.env.requests.at(-1).resolve(references());
  await h.panel.settle();
  h.panel.change(field(h, 'location'), '10');
  assert.equal(save(h).disabled, false);
  return h;
}
function edit(h, name, value) {
  h.panel.change(name === 'notes' ? h.panel.nodes.find(node => node.type === 'textarea') : field(h, name), value);
}
async function beginMutation(h) {
  const pending = h.panel.submit();
  h.confirmations.at(-1).resolve(true);
  await h.panel.settle();
  assert.equal(h.mutations.length, 1);
  return { pending };
}
describe('Actual extracted CreateBoxPanel', () => {
  test('inline starts closed, requests options/list only on open, and ignores controlled isOpen', async () => {
    const h = createBoxHarness({ presentation: 'inline', isOpen: true, exportOptions: null });
    assert.equal(h.panel.form, undefined);
    assert.equal(h.env.requests.length, 0);
    assert.equal(h.optionsRequests, 0);
    assert.equal(h.boxCollectionRequests, 0);
    h.panel.click(h.panel.byClass('create-box-toggle'));
    assert.ok(h.panel.form);
    assert.equal(h.optionsRequests, 1);
    assert.equal(h.boxCollectionRequests, 1);
    assert.equal(h.env.requests[0].path, '/api/taxonomy/references/');
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
    assert.equal(h.boxCollectionRequests, 1);
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

  test('no organization means no taxonomy request and no submission', async () => {
    const h = createBoxHarness({ profile: null });
    assert.equal(h.env.requests.length, 0);
    assert.equal(save(h).disabled, true);
    const event = submitEvent();
    await h.panel.submit(event);
    assert.equal(event.defaultPrevented, true);
    assert.equal(h.confirmations.length, 0);
    h.panel.unmount();
  });

  test('deferred taxonomy displays loading, disables save, then exposes the empty state', async () => {
    const h = createBoxHarness();
    assert.ok(text(h.panel.value).includes('loading'));
    assert.equal(save(h).disabled, true);
    h.env.requests[0].resolve({ strains: [] });
    await h.panel.settle();
    assert.ok(text(h.panel.value).includes('createBoxNoOptions'));
    assert.equal(text(h.panel.value).includes('loading'), false);
    assert.equal(save(h).disabled, true);
    h.panel.unmount();
  });

  for (const kind of ['api', 'generic']) test(`taxonomy ${kind} failure shows real error mapping and retries after reopen`, async () => {
    const h = createBoxHarness();
    h.env.requests[0].reject(kind === 'api' ? new h.api.ApiError(500, 'Taxonomy unavailable') : new Error('private error'));
    await h.panel.settle();
    assert.deepEqual(errors(h), [kind === 'api' ? 'Taxonomy unavailable' : 'taxonomyLoadError']);
    assert.equal(save(h).disabled, true);
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
    assert.deepEqual(errors(h), []);
    h.env.requests[1].resolve(references());
    await h.panel.settle();
    assert.equal(field(h, 'code').props.value, 'AUR.001');
    h.panel.unmount();
  });

  for (const completion of ['resolve', 'reject']) test(`taxonomy stale ${completion} cannot replace newer organization data or clear loading`, async () => {
    const h = createBoxHarness();
    const old = h.env.requests[0];
    h.panel.setProps({ profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' } } });
    assert.equal(h.env.requests.length, 2);
    old[completion](completion === 'resolve' ? references(9, 'OLD') : new h.api.ApiError(500, 'Old failure'));
    await h.panel.settle();
    assert.deepEqual(errors(h), []);
    assert.ok(text(h.panel.value).includes('loading'));
    assert.equal(field(h, 'code').props.value, '');
    h.env.requests[1].resolve(references(2, 'NEW'));
    await h.panel.settle();
    assert.equal(field(h, 'code').props.value, 'NEW.001');
    assert.equal(text(h.panel.value).includes('OLD'), false);
    h.panel.unmount();
  });

  test('rapid A -> B -> A taxonomy generations ignore both older requests after latest success', async () => {
    const h = createBoxHarness();
    h.panel.setProps({ profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' } } });
    h.panel.setProps({ profile: h.props.profile });
    assert.equal(h.env.requests.length, 3);
    h.env.requests[2].resolve(references(3, 'LATEST'));
    await h.panel.settle();
    h.env.requests[0].resolve(references(1, 'OLD'));
    h.env.requests[1].reject(new h.api.ApiError(500, 'Stale B failure'));
    await h.panel.settle();
    assert.deepEqual(errors(h), []);
    assert.equal(field(h, 'code').props.value, 'LATEST.001');
    assert.equal(text(h.panel.value).includes('loading'), false);
    assert.equal(text(h.panel.value).includes('OLD'), false);
    h.panel.unmount();
  });

  for (const completion of ['resolve', 'reject']) test(`taxonomy ${completion} after unmount performs no state updates`, async () => {
    const h = createBoxHarness();
    h.panel.unmount();
    h.env.requests[0][completion](completion === 'resolve' ? references() : new Error('Late'));
    await h.panel.settle();
    assert.equal(h.panel.updatesAfterUnmount, 0);
    assert.equal(h.env.listenerCount(), 0);
  });

  test('code suggestion waits for the complete list, scopes organization/strain, and preserves manual input', async () => {
    const h = createBoxHarness({ isBoxCollectionReady: false });
    h.env.requests[0].resolve(references());
    await h.panel.settle();
    assert.equal(field(h, 'code').props.value, '');
    assert.equal(field(h, 'number').props.value, '');
    h.panel.setProps({ boxes: [box('AUR.0009'), box('AUR.999', 2), box('OTHER.888', 1, 2)], isBoxCollectionReady: true });
    assert.equal(field(h, 'code').props.value, 'AUR.0010');
    assert.equal(field(h, 'number').props.value, '0010');
    edit(h, 'code', ' MANUAL.077 ');
    edit(h, 'number', ' 077 ');
    h.panel.setProps({ boxes: [box('AUR.500')] });
    assert.equal(field(h, 'code').props.value, ' MANUAL.077 ');
    assert.equal(field(h, 'number').props.value, ' 077 ');
    h.panel.unmount();
  });

  test('zones are organization scoped and removed zone selection is cleared', async () => {
    const h = await ready(createBoxHarness({ exportOptions: { zones: [
      { id: 10, organization_id: 1, name: 'Own' }, { id: 20, organization_id: 2, name: 'Foreign' },
    ] } }));
    assert.equal(text(field(h, 'location')).includes('Foreign'), false);
    h.panel.setProps({ exportOptions: { zones: [] } });
    assert.equal(field(h, 'location').props.value, '');
    assert.equal(save(h).disabled, true);
    h.panel.unmount();
  });

  test('strain search retains selected strain, changing strain clears and regenerates code', async () => {
    const h = createBoxHarness();
    h.env.requests[0].resolve({ strains: [...references().strains, ...references(2, 'CY', 'Cyanea').strains] });
    await h.panel.settle();
    h.panel.change(h.panel.byClass('create-box-strain-search').querySelectorAll(FOCUSABLE_SELECTOR)[0], '  cyanea  ');
    const select = h.panel.nodes.find(node => node.type === 'select' && node.props['aria-label'] === 'createBoxStrain');
    assert.equal(select.props.children.length, 2, 'Selected strain remains visible despite filtering');
    edit(h, 'code', 'MANUAL.099');
    h.panel.change(select, '2');
    assert.equal(field(h, 'code').props.value, 'CY.001');
    assert.equal(field(h, 'number').props.value, '001');
    h.panel.unmount();
  });

  test('number mismatch blocks confirmation; cancellation preserves draft', async () => {
    const h = await ready();
    edit(h, 'number', '002');
    await h.panel.submit();
    h.panel.flush();
    assert.deepEqual(errors(h), ['createBoxNumberMismatch']);
    assert.equal(h.confirmations.length, 0);
    edit(h, 'number', '001');
    edit(h, 'notes', ' draft ');
    const pending = h.panel.submit();
    assert.equal(h.confirmations.length, 1);
    const details = plain(h.confirmations[0].details);
    assert.deepEqual(details, {
      title: 'confirmCreateBoxTitle', message: 'confirmCreateBoxMessage', confirmLabel: 'confirmCreateBoxAction', cancelLabel: 'confirmCancel',
      details: [
        { label: 'confirmDetailBox', value: 'AUR.001' }, { label: 'confirmDetailSpecies', value: 'Aurelia aurita' },
        { label: 'confirmDetailStrain', value: 'AUR' }, { label: 'confirmDetailOrganization', value: 'Laboratory A' },
        { label: 'confirmDetailLocation', value: 'Zone A' },
      ],
    });
    h.confirmations[0].resolve(false);
    await pending;
    h.panel.flush();
    assert.equal(h.mutations.length, 0);

    assert.equal(h.panel.nodes.find(node => node.type === 'textarea').props.value, ' draft ');
    h.panel.unmount();
  });

  for (const boundary of ['generation', 'unmount']) test(`${boundary} during confirmation prevents mutation`, async () => {
    const h = await ready();
    const pending = h.panel.submit();
    if (boundary === 'generation') h.invalidateOperation();
    else h.panel.unmount();
    h.confirmations[0].resolve(true);
    await pending;
    await h.panel.settle();
    assert.equal(h.mutations.length, 0);
    assert.deepEqual(h.navigations, []);
    assert.equal(h.panel.updatesAfterUnmount, 0);
    if (boundary === 'generation') h.panel.unmount();
  });

  test('saving blocks a new rendered submission; success trims payload, resets draft, navigates and defers page focus', async () => {
    const h = await ready();
    edit(h, 'code', ' AUR.001 ');
    edit(h, 'number', ' 001 ');
    edit(h, 'date', '2026-04-09');
    h.panel.change(h.panel.nodes.find(node => node.type === 'textarea'), ' notes ');
    const target = h.env.external('section', { 'data-box-page-focus-target': true, tabIndex: -1 });
    const { pending } = await beginMutation(h);
    assert.equal(save(h).disabled, true);
    assert.equal(text(save(h)), 'saving');
    await h.panel.submit();
    assert.equal(h.confirmations.length, 1);
    assert.equal(h.mutations.length, 1);
    assert.deepEqual(plain(h.mutations[0].payload), {
      strain: 1, thermal_zone: 10, global_code: 'AUR.001', local_code: '', box_number: '001',
      entered_on: '2026-04-09', volume_liters: null, notes: 'notes',
    });
    h.mutations[0].resolve({ id: 42 });
    await pending;
    h.panel.flush();
    assert.deepEqual(h.navigations, [42]);
    assert.equal(field(h, 'code').props.value, '');
    assert.equal(field(h, 'number').props.value, '');
    assert.equal(h.panel.nodes.find(node => node.type === 'textarea').props.value, '');
    assert.equal(field(h, 'date').props.value, '2026-04-09');
    assert.equal(field(h, 'location').props.value, 10);
    assert.equal(h.env.frameCount, 1);
    assert.notEqual(h.env.document.activeElement, target);
    h.env.flushFrames();
    assert.equal(h.env.document.activeElement, target);
    assert.deepEqual(plain(target.focusCalls.at(-1)), { preventScroll: true });
    h.panel.unmount();
  });

  test('inline success navigates without scheduling modal page focus', async () => {
    const h = createBoxHarness({ presentation: 'inline' });
    h.panel.click(h.panel.byClass('create-box-toggle'));
    await ready(h);
    const { pending } = await beginMutation(h);
    h.mutations[0].resolve({ id: 43 });
    await pending;
    h.panel.flush();
    assert.deepEqual(h.navigations, [43]);
    assert.equal(h.env.frameCount, 0);
    assert.ok(h.panel.form);
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
      assert.equal(h.panel.updatesAfterUnmount, 0);
      if (boundary === 'generation') {
        assert.deepEqual(errors(h), []);
        assert.equal(field(h, 'code').props.value, 'AUR.001');
        h.panel.unmount();
      }
    });
  }

  for (const kind of ['403', 'api', 'generic', 'cancelled']) test(`${kind} mutation failure uses real classes, preserves draft and allows retry`, async () => {
    const h = await ready();
    const { pending } = await beginMutation(h);
    const failure = kind === '403' ? new h.api.ApiError(403, 'Raw refusal')
      : kind === 'api' ? new h.api.ApiError(400, 'Invalid box')
        : kind === 'cancelled' ? new h.api.ApiResourceCancelledError() : new Error('Private details');
    h.mutations[0].reject(failure);
    await pending;
    h.panel.flush();
    assert.deepEqual(errors(h), kind === 'cancelled' ? [] : [kind === '403' ? 'createBoxForbidden' : kind === 'api' ? 'Invalid box' : 'Impossible de joindre l API Django.']);
    assert.equal(save(h).disabled, false);
    assert.equal(field(h, 'code').props.value, 'AUR.001');
    assert.deepEqual(h.navigations, []);
    const retry = h.panel.submit();
    h.confirmations[1].resolve(false);
    await retry;
    h.panel.unmount();
  });

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

  test('unmount does not try restoring a disconnected opener', () => {
    const h = createBoxHarness();
    h.env.opener.isConnected = false;
    const calls = h.env.opener.focusCalls.length;
    h.panel.unmount();
    assert.equal(h.env.opener.focusCalls.length, calls);
    assert.equal(h.env.listenerCount(), 0);
  });

  test('quick-strain child suppresses underlying keyboard handling and created strain regenerates code', async () => {
    const h = await ready();
    h.panel.click(h.panel.byClass('create-box-reference-action'));
    const child = h.panel.nodes.find(node => node.type === 'QuickStrainCreator');
    assert.ok(child);
    const nested = h.env.external('button');
    nested.focus();
    assert.equal(h.env.key('Escape').defaultPrevented, false);
    assert.deepEqual(h.openChanges, []);
    assert.equal(h.env.key('Tab').defaultPrevented, false);
    child.props.onCreated({ id: 7, code: 'NEW', species_id: 107, species_name: 'New species' });
    h.panel.flush();
    assert.equal(h.panel.nodes.some(node => node.type === 'QuickStrainCreator'), false);
    assert.equal(field(h, 'code').props.value, 'NEW.001');
    assert.equal(h.env.key('Escape').defaultPrevented, true);
    h.panel.unmount();
  });

  test('non-admin active organization hides quick-strain action even with an admin membership elsewhere', () => {
    const h = createBoxHarness({ profile: { active_organization: { id: 2 }, memberships: [{ organization: { id: 1 }, role: 'admin' }] } });
    assert.equal(h.panel.byClass('create-box-reference-action'), undefined);
    h.panel.unmount();
  });

  test('confirmation guard suppresses underlying Escape/Tab and resets after cancel or rejection', async () => {
    const h = await ready();
    for (const reject of [false, true]) {
      const pending = h.panel.submit();
      const checked = reject ? assert.rejects(pending, /Confirmation failure/) : pending;
      const nested = h.env.external('button');
      nested.focus();
      assert.equal(h.env.key('Escape').defaultPrevented, false);
      assert.equal(h.env.key('Tab').defaultPrevented, false);
      assert.deepEqual(h.openChanges, []);
      if (reject) h.confirmations.at(-1).reject(new Error('Confirmation failure'));
      else h.confirmations.at(-1).resolve(false);
      await checked;
      assert.equal(h.env.key('Escape').defaultPrevented, true);
      h.openChanges.length = 0;
    }
    assert.equal(h.mutations.length, 0);
    h.panel.unmount();
  });

  test('StrictMode effect replay abandons first taxonomy request, keeps one listener and restores operation lifetime', async () => {
    const h = createBoxHarness({}, { strictMode: true });
    assert.equal(h.env.requests.length, 2);
    assert.equal(h.env.listenerCount(), 1);
    h.env.requests[0].resolve(references(9, 'STALE'));
    await h.panel.settle();
    assert.equal(field(h, 'code').props.value, '');
    assert.ok(text(h.panel.value).includes('loading'));
    h.env.requests[1].resolve(references());
    await h.panel.settle();
    edit(h, 'location', '10');
    const { pending } = await beginMutation(h);
    h.mutations[0].resolve({ id: 77 });
    await pending;
    h.panel.flush();
    assert.deepEqual(h.navigations, [77]);
    h.panel.unmount();
    assert.equal(h.env.listenerCount(), 0);
    assert.equal(h.env.document.activeElement, h.env.opener);
  });

  test('actual useConfirmAction declines concurrent submissions without duplicate mutations', async () => {
    const h = await ready(createBoxHarness({}, { realConfirmation: true }));
    const first = h.panel.submit();
    const modal = h.confirmationHost.modal;
    assert.ok(modal.dialog);
    const second = h.panel.submit();
    await second;
    h.confirmationHost.flush();
    assert.equal(h.confirmationHost.modal, modal, 'The first confirmation remains owned and mounted');
    assert.equal(h.mutations.length, 0);
    modal.click(modal.byClass('confirm-action-submit'));
    h.confirmationHost.flush();
    await h.panel.settle();
    assert.equal(h.mutations.length, 1);
    assert.equal(h.confirmationHost.modal, null);
    await h.panel.submit();
    assert.equal(h.mutations.length, 1, 'Saving also blocks a subsequent submission');
    h.mutations[0].resolve({ id: 88 });
    await first;
    h.panel.flush();
    assert.deepEqual(h.navigations, [88]);
    h.unmount();
  });

  test('real nested confirmation traps Tab/ShiftTab, cancels on Escape and restores its opener', async () => {
    const h = await ready(createBoxHarness({}, { realConfirmation: true }));
    const opener = save(h);
    opener.focus();
    const pending = h.panel.submit();
    const modal = h.confirmationHost.modal;
    assert.equal(h.env.listenerCount(), 2);
    const controls = modal.dialog.querySelectorAll(FOCUSABLE_SELECTOR);
    assert.equal(h.env.document.activeElement, controls[0]);
    controls.at(-1).focus();
    assert.equal(h.env.key('Tab').defaultPrevented, true);
    assert.equal(h.env.document.activeElement, controls[0]);
    assert.equal(h.env.key('Tab', true).defaultPrevented, true);
    assert.equal(h.env.document.activeElement, controls.at(-1));
    assert.deepEqual(h.openChanges, []);
    h.env.key('Escape');
    h.confirmationHost.flush();
    await pending;
    h.panel.flush();
    assert.equal(h.confirmationHost.modal, null);
    assert.equal(h.env.document.activeElement, opener);
    assert.deepEqual(h.openChanges, [], 'Escape cancelled only the nested dialog');
    assert.equal(h.mutations.length, 0);
    assert.equal(h.env.listenerCount(), 1);
    h.env.key('Escape');
    assert.deepEqual(h.openChanges, [false]);
    h.unmount();
  });

  test('actual confirmation hook unmount cancels its pending request and cannot start a mutation', async () => {
    const h = await ready(createBoxHarness({}, { realConfirmation: true }));
    const pending = h.panel.submit();
    h.confirmationHost.unmount();
    await pending;
    h.panel.flush();
    assert.equal(h.mutations.length, 0);
    assert.deepEqual(h.navigations, []);
    h.panel.unmount();
  });

  test('preserved navigation flow: success RAF focuses the box page after panel unmount', async () => {
    const h = await ready();
    const target = h.env.external('section', { 'data-box-page-focus-target': true, tabIndex: -1 });
    const { pending } = await beginMutation(h);
    h.mutations[0].resolve({ id: 89 });
    await pending;
    h.panel.unmount();
    assert.equal(h.env.document.activeElement, h.env.opener);
    assert.equal(h.env.frameCount, 1);
    h.env.flushFrames();
    assert.equal(h.env.document.activeElement, target);
  });

  for (const phase of ['confirmation', 'mutation']) test(`actual PilotageView organization key remounts/reset panel and guards old ${phase}`, async () => {
    const h = await ready(createPilotageHarness());
    const old = h.panel;
    edit(h, 'code', 'AUR.077');
    edit(h, 'number', '077');
    edit(h, 'notes', 'Old notes');
    const pending = old.submit();
    if (phase === 'mutation') {
      h.confirmations[0].resolve(true);
      await old.settle();
      assert.equal(h.mutations.length, 1);
    }
    const node = h.parent.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
    assert.equal(node.key, 1);
    h.setParentProps({ isOptionsLoading: true });
    assert.equal(h.panel, old, 'Same parent key preserves the child instance');
    h.setParentProps({
      activeOrganizationId: 2,
      profile: { ...h.props.profile, active_organization: { id: 2, name: 'B' }, memberships: [{ organization: { id: 2 }, role: 'admin' }] },
      exportOptions: { zones: [{ id: 20, organization_id: 2, name: 'Zone B' }] },
      isOptionsLoading: false,
    });
    const replacementNode = h.parent.nodes.find(candidate => candidate.type === node.type);
    assert.equal(replacementNode.key, 2);
    assert.notEqual(h.panel, old);
    assert.equal(field(h, 'code').props.value, '');
    assert.equal(field(h, 'number').props.value, '');
    assert.equal(field(h, 'location').props.value, '');
    assert.equal(h.panel.nodes.find(node => node.type === 'textarea').props.value, '');
    if (phase === 'confirmation') h.confirmations[0].resolve(true);
    else h.mutations[0].resolve({ id: 99 });
    await pending;
    assert.equal(old.updatesAfterUnmount, 0);
    assert.equal(h.mutations.length, phase === 'confirmation' ? 0 : 1);
    assert.deepEqual(h.navigations, []);
    assert.equal(h.env.frameCount, 0);
    h.env.requests.at(-1).resolve(references(2, 'B'));
    await h.panel.settle();
    assert.equal(field(h, 'code').props.value, 'B.001');
    assert.equal(field(h, 'number').props.value, '001');
    assert.equal(h.env.listenerCount(), 1);
    h.unmount();
  });

  test('actual PilotageView uses the none key without an active organization', () => {
    const h = createPilotageHarness({ profile: { active_organization: null, is_superuser: true, memberships: [] } });
    const node = h.parent.nodes.find(node => node.type === h.env.load('../src/components/CreateBoxPanel.tsx').default);
    assert.equal(node.key, 'none');
    assert.equal(h.env.requests.length, 0);
    h.unmount();
  });

  for (const language of ['fr', 'en']) test(`actual ${language} translator renders labels and confirmation/error copy`, async () => {
    const env = createBoxEnvironment();
    const t = env.load('../src/i18n/index.ts').createTranslator(language);
    const h = createBoxHarness({ t }, { environment: env });
    assert.equal(text(h.panel.nodes.find(node => node.type === 'h2')), language === 'fr' ? 'Créer une boîte' : 'Create a box');
    assert.equal(text(save(h)), language === 'fr' ? 'Créer' : 'Create');
    h.env.requests[0].reject(new Error('Private taxonomy error'));
    await h.panel.settle();
    assert.deepEqual(errors(h), [t('taxonomyLoadError')]);
    h.panel.setProps({ isOpen: false });
    h.panel.setProps({ isOpen: true });
    await ready(h);
    edit(h, 'number', '002');
    await h.panel.submit();
    h.panel.flush();
    assert.deepEqual(errors(h), [t('createBoxNumberMismatch')]);
    edit(h, 'number', '001');
    const { pending } = await beginMutation(h);
    assert.equal(h.confirmations[0].details.title, language === 'fr' ? 'Créer cette boîte' : 'Create this box');
    assert.equal(h.confirmations[0].details.confirmLabel, t('confirmCreateBoxAction'));
    h.mutations[0].reject(new h.api.ApiError(403, 'Raw refusal'));
    await pending;
    h.panel.flush();
    assert.deepEqual(errors(h), [language === 'fr' ? 'Ce compte ne peut pas créer de boîte.' : 'This account cannot create boxes.']);
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
