import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import { appHarness } from './app-operation-test-harness.mjs';
import { createBoxEnvironment, createBoxHarness, plain } from './create-box-harness.mjs';

// Optional DNA evidence in "New box": the Box page URL rule, sent with the
// creation intent only when filled in, and kept in the recovery envelope.
const STORAGE = 'polypbase.automaticBox.7.1';
const REGISTER = '/api/boxes/automatic/intents/';
const EXECUTE = '/api/boxes/automatic/intents/execute/';
const URL_VALUE = 'https://lab.example.org/dna/report-42';
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const option = { id: 1, species: { id: 1, scientific_name: 'Aurelia aurita' }, code: 'AUR-ATL-1', name: '', active_box_count: 0,
  identity: { status: 'usable', prefix: 'AUR-ATL-1', species_code: 'AUR', origin_code: 'ATL', strain_number: '1' } };
const createdBox = (validation = null) => ({ id: 42, global_code: 'AUR-ATL-1.001', strain: { id: 1 },
  species: { scientific_name: 'Aurelia aurita' }, dna_validation: validation, can_edit_dna_validation: true });

const dnaInput = (h) => h.panel.field('create-box-dna-field');
const save = (h) => h.panel.byClass('create-box-submit');
const errors = (h) => h.panel.nodes.filter((node) => node.matches('.inline-error'));

async function ready(h, polyps = '5') {
  h.env.requests.findLast((r) => r.path === '/api/boxes/automatic/strains/').resolve([option]);
  await h.panel.settle();
  h.panel.click(h.panel.nodes.find((n) => n.props.role === 'option'));
  h.panel.change(h.panel.field('create-box-location-field'), '10');
  h.panel.change(h.panel.field('create-box-initial-polyps-field'), polyps);
  return h;
}
function typeDna(h, value) {
  h.panel.change(dnaInput(h), value);
}
function blurDna(h) {
  dnaInput(h).props.onBlur({ relatedTarget: null });
  h.panel.flush();
}
function integrated(env = createBoxEnvironment(), transport = appHarness()) {
  transport.context.ApiError = env.load('../src/api/client.ts').ApiError;
  const h = createBoxHarness({ onCreateBox: (intent) => transport.context.createBox(intent) }, { environment: env });
  return { h, env, transport };
}

test('DNA evidence field: optional, after the initial count, with the Box page input semantics', async () => {
  const h = await ready(createBoxHarness());
  const labels = h.panel.nodes.filter((node) => node.type === 'label').map((node) => node.props.className);
  assert.equal(labels.indexOf('create-box-dna-field'), labels.indexOf('create-box-initial-polyps-field') + 1);
  const label = h.panel.byClass('create-box-dna-field');
  assert.equal(label.children[0].type, 'span');
  assert.equal(label.children[0].props.children, 'createBoxDnaEvidence');
  const input = dnaInput(h);
  assert.equal(input.props.type, 'url');
  assert.equal(input.props.autoComplete, 'url');
  assert.equal(input.props.maxLength, 2000);
  assert.equal(input.props.placeholder, 'dnaEvidenceUrl');
  assert.equal(input.props.required, undefined, 'evidence is never required');
  assert.equal(input.props.value, '');
  assert.equal(input.props['aria-invalid'], false);
  assert.equal(save(h).disabled, false, 'an empty evidence field does not block creation');
  h.unmount();
});

test('creation without evidence sends exactly the previous payload, zero count included', async () => {
  const h = await ready(createBoxHarness(), '0');
  const pending = h.panel.submit();
  await h.panel.settle();
  const payload = plain(h.mutations[0].payload);
  assert.equal(payload.initial_polyp_count, 0, 'zero is a real initial count');
  assert.equal('dna_evidence_url' in payload, false);
  assert.deepEqual(Object.keys(payload).sort(), ['entered_on', 'initial_polyp_count', 'notes', 'request_id', 'resume_token',
    'strain', 'thermal_zone', 'volume_liters']);
  h.mutations[0].resolve(createdBox());
  await pending;
  h.unmount();
});

test('blank evidence is absent, and valid evidence is trimmed and sent with the intent', async () => {
  for (const [value, expected] of [['   ', undefined], [`  ${URL_VALUE}  `, URL_VALUE]]) {
    const h = await ready(createBoxHarness());
    typeDna(h, value);
    blurDna(h);
    assert.equal(errors(h).length, 0);
    const pending = h.panel.submit();
    await h.panel.settle();
    assert.equal(plain(h.mutations[0].payload).dna_evidence_url, expected);
    h.mutations[0].resolve(createdBox());
    await pending;
    h.unmount();
  }
});

test('invalid evidence follows the Box page rule: error on blur of a modified field, cleared on input, no write', async () => {
  for (const value of ['not a url', 'ftp://lab.example.org/x', 'javascript:alert(1)', `https://lab.example.org/${'a'.repeat(2000)}`]) {
    const h = await ready(createBoxHarness());
    blurDna(h);
    assert.equal(errors(h).length, 0, 'no error before any input');
    typeDna(h, value);
    assert.equal(errors(h).length, 0, 'no error while typing');
    assert.equal(save(h).disabled, true);
    blurDna(h);
    const error = errors(h)[0];
    assert.equal(error.props.children, 'dnaInvalidUrl');
    assert.equal(error.props.id, 'create-box-dna-error');
    assert.equal(dnaInput(h).props['aria-invalid'], true);
    assert.equal(dnaInput(h).props['aria-describedby'], 'create-box-dna-error');
    await h.panel.submit();
    await h.panel.settle();
    assert.equal(h.mutations.length, 0, 'invalid evidence never starts a creation');
    typeDna(h, URL_VALUE);
    assert.equal(errors(h).length, 0);
    assert.equal(save(h).disabled, false);
    h.unmount();
  }
});

test('the integrated flow registers the evidence, stores it for recovery and publishes the evidence-bearing Box', async () => {
  const { h, transport } = integrated();
  await ready(h);
  typeDna(h, URL_VALUE);
  const completion = h.panel.submit();
  await h.panel.settle();
  assert.equal(transport.requests[0].url, REGISTER);
  assert.equal(plain(transport.requests[0].payload).dna_evidence_url, URL_VALUE);
  assert.equal(JSON.parse(h.env.window.sessionStorage.getItem(STORAGE)).dna_evidence_url, URL_VALUE);
  transport.requests[0].resolve({ status: 'pending' });
  await h.panel.settle();
  assert.equal(transport.requests[1].url, EXECUTE);
  assert.deepEqual(Object.keys(plain(transport.requests[1].payload)).sort(), ['request_id', 'resume_token']);
  const validation = { id: 7, evidence_url: URL_VALUE, created_by: 'Tech', created_at: '2026-11-18T10:00:00Z', revision: 1 };
  transport.requests[1].resolve({ status: 'created', box: createdBox(validation) });
  await h.panel.settle();
  await completion;
  // The Box page reads this detail: its DNA badge shows the recorded evidence.
  assert.deepEqual(plain(transport.state.data.boxDetails[42].dna_validation), validation);
  assert.ok(h.panel.byClass('create-box-open-created'));
  h.unmount();
});

test('a server refusal of the evidence unlocks the draft with its explicit field error, never a success', async () => {
  const { h, transport } = integrated();
  await ready(h);
  typeDna(h, URL_VALUE);
  const completion = h.panel.submit();
  await h.panel.settle();
  transport.requests[0].resolve({ status: 'rejected', errors: { dna_evidence_url: ['Saisissez une URL valide.'] } });
  await h.panel.settle();
  await completion;
  assert.equal(h.panel.byClass('create-box-open-created'), undefined);
  assert.ok(errors(h).some((node) => String(node.props.children).includes('Saisissez une URL valide.')));
  assert.equal(dnaInput(h).props.value, URL_VALUE, 'the entered evidence is kept for correction');
  assert.equal(dnaInput(h).props.disabled, false);
  h.unmount();
});

test('a permission refusal is reported and never shown as a created Box', async () => {
  const h = await ready(createBoxHarness());
  typeDna(h, URL_VALUE);
  const pending = h.panel.submit();
  await h.panel.settle();
  h.mutations[0].reject(new h.api.ApiError(403, 'Forbidden'));
  await pending;
  h.panel.flush();
  assert.equal(h.panel.byClass('create-box-open-created'), undefined);
  assert.equal(errors(h).length, 1);
  h.unmount();
});

test('another box starts without the previous evidence', async () => {
  const h = await ready(createBoxHarness());
  typeDna(h, URL_VALUE);
  const pending = h.panel.submit();
  await h.panel.settle();
  h.mutations[0].resolve(createdBox());
  await pending;
  await h.panel.settle();
  h.panel.click(h.panel.byClass('create-box-another'));
  assert.equal(dnaInput(h).props.value, '');
  h.unmount();
});

test('recovery envelope restores evidence and keeps digests of envelopes without evidence', async () => {
  const env = createBoxEnvironment();
  const recovery = env.load('../src/utils/boxCreationRecovery.ts');
  const base = { request_id: crypto.randomUUID(), resume_token: crypto.randomUUID(), strain: 1, thermal_zone: 10,
    entered_on: '2026-10-01', volume_liters: null, notes: '', initial_polyp_count: 0 };
  const withEvidence = { ...base, dna_evidence_url: URL_VALUE };
  assert.notEqual(await recovery.creationIntentDigest(STORAGE, base), await recovery.creationIntentDigest(STORAGE, withEvidence));
  await recovery.storeCreationIntent(STORAGE, withEvidence);
  const restored = recovery.readStoredCreationIntent(STORAGE);
  assert.equal(restored.canResume, true);
  assert.equal(restored.intent.dna_evidence_url, URL_VALUE);
  assert.equal(restored.intent.initial_polyp_count, 0);
  assert.equal(restored.digest, await recovery.creationIntentDigest(STORAGE, plain(restored.intent)));
  await recovery.storeCreationIntent(STORAGE, base);
  const restoredBase = recovery.readStoredCreationIntent(STORAGE);
  assert.equal('dna_evidence_url' in restoredBase.intent, false);
  assert.equal(restoredBase.digest, await recovery.creationIntentDigest(STORAGE, plain(restoredBase.intent)));
  // A damaged evidence value is never replayed.
  const envelope = JSON.parse(env.window.sessionStorage.getItem(STORAGE));
  env.window.sessionStorage.setItem(STORAGE, JSON.stringify({ ...envelope, dna_evidence_url: 42 }));
  assert.equal(recovery.readStoredCreationIntent(STORAGE).canResume, false);
});

test('layout: DNA evidence sits right of the initial count and stacks in the single-column layout', () => {
  const css = read('../src/styles/pages/pilotage.css');
  assert.match(css, /\.create-box-dna-field \{ grid-area: dna; \}/);
  assert.equal((css.match(/"initial dna"/g) ?? []).length, 3);
  assert.doesNotMatch(css, /"initial \."/);
  assert.match(css, /"initial"\s+"dna"\s+"notes"/);
});

test('FR/EN wording follows the optional-label convention', () => {
  const fr = read('../src/i18n/fr.ts');
  const en = read('../src/i18n/en.ts');
  assert.match(fr, /^ {2}createBoxDnaEvidence: 'Preuve ADN \(facultatif\)',/m);
  assert.match(en, /^ {2}createBoxDnaEvidence: 'DNA evidence \(optional\)',/m);
  assert.match(fr, /^ {2}dnaEvidenceUrl: 'Lien vers la preuve ADN',/m);
  assert.match(en, /^ {2}dnaEvidenceUrl: 'DNA evidence link',/m);
});
