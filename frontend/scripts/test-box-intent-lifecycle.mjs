import assert from 'node:assert/strict';
import test from 'node:test';
import { appHarness } from './app-operation-test-harness.mjs';
import { createBoxEnvironment, createBoxHarness, createPilotageHarness, plain } from './create-box-harness.mjs';

const STORAGE = 'polypbase.automaticBox.7.1';
const KEY = `${STORAGE}.requestId`;
const REGISTER = '/api/boxes/automatic/intents/';
const EXECUTE = '/api/boxes/automatic/intents/execute/';
const box = { id: 42, global_code: 'AUR-ATL-1.001', strain: { id: 1 }, species: { scientific_name: 'Aurelia aurita' } };
const option = { id: 1, species: { id: 1, scientific_name: 'Aurelia aurita' }, code: 'AUR-ATL-1', name: '', active_box_count: 0,
  identity: { status: 'usable', prefix: 'AUR-ATL-1', species_code: 'AUR', origin_code: 'ATL', strain_number: '1' } };
const payload = () => ({ request_id: crypto.randomUUID(), resume_token: crypto.randomUUID(), strain: 1,
  thermal_zone: 10, entered_on: '2026-10-01', volume_liters: null, notes: '' });
const visible = h => h.panel.nodes.map(n => [].concat(n.props.children ?? []).filter(c => typeof c === 'string').join('')).join('');

function integrated(env = createBoxEnvironment(), transport = appHarness()) {
  transport.context.ApiError = env.load('../src/api/client.ts').ApiError;
  const h = createBoxHarness({ onCreateBox: intent => transport.context.createBox(intent) }, { environment: env });
  return { h, env, transport };
}
async function select(h) {
  h.env.requests.findLast(r => r.path === '/api/boxes/automatic/strains/').resolve([option]);
  await h.panel.settle();
  h.panel.click(h.panel.nodes.find(n => n.props.role === 'option'));
  h.panel.change(h.panel.field('create-box-location-field'), '10');
}
async function start(h) {
  const completion = h.panel.submit();
  await h.panel.settle();
  return { completion };
}
async function respond(h, request, data) {
  request.resolve(data);
  await h.panel.settle();
}

test('App registers immutable intent before executing only its key and token, then publishes confirmed Box', async () => {
  const { h, transport } = integrated();
  await select(h);
  const { completion } = await start(h);
  await h.panel.submit();
  assert.equal(transport.requests.length, 1, 'Duplicate click cannot start another registration');
  const registered = plain(transport.requests[0].payload);
  assert.equal(transport.requests[0].url, REGISTER);
  assert.equal(JSON.parse(h.env.window.sessionStorage.getItem(STORAGE)).request_id, registered.request_id);
  await respond(h, transport.requests[0], { status: 'pending' });
  assert.equal(transport.requests[1].url, EXECUTE);
  assert.deepEqual(plain(transport.requests[1].payload), { request_id: registered.request_id, resume_token: registered.resume_token });
  assert.equal(h.panel.byClass('create-box-open-created'), undefined, 'Pending is not success');
  await respond(h, transport.requests[1], { status: 'created', box });
  await completion;
  assert.ok(visible(h).includes(box.global_code));
  assert.equal(transport.state.data.boxDetails[42].global_code, box.global_code);
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), null);
  assert.equal(h.resolutions.length, 0);
  h.unmount();
});

for (const phase of ['registration never arrived', 'registration response lost', 'execution response lost']) {
  test(`${phase}: reconnect resumes the same intention without a replacement UUID`, async () => {
    const { h, transport } = integrated();
    await select(h);
    const { completion } = await start(h);
    const original = plain(transport.requests[0].payload);
    if (phase === 'execution response lost') await respond(h, transport.requests[0], { status: 'pending' });
    transport.requests.at(-1).reject(new TypeError('Network interrupted'));
    await completion;
    await h.panel.settle();
    assert.equal(h.panel.field('create-box-date-field').disabled, true);
    h.env.dispatch('online');
    await h.panel.settle();
    const retry = transport.requests.at(-1);
    assert.equal(retry.url, REGISTER);
    assert.deepEqual(plain(retry.payload), original);
    if (phase === 'execution response lost') await respond(h, retry, { status: 'created', box });
    else {
      await respond(h, retry, { status: 'pending' });
      await respond(h, transport.requests.at(-1), { status: 'created', box });
    }
    assert.ok(visible(h).includes(box.global_code));
    assert.equal(h.env.window.sessionStorage.getItem(KEY), null);
    assert.equal(h.resolutions.length, 0);
    h.unmount();
  });
}

for (const phase of ['registration', 'execution']) test(`reload during ${phase} resumes registration safely and ignores the old operation`, async () => {
  const first = integrated();
  await select(first.h);
  const { completion } = await start(first.h);
  const original = plain(first.transport.requests[0].payload);
  if (phase === 'execution') await respond(first.h, first.transport.requests[0], { status: 'pending' });
  const abandoned = first.transport.requests.at(-1);
  first.h.unmount();
  const restored = integrated(first.env);
  await restored.h.panel.settle();
  assert.equal(restored.transport.requests[0].url, REGISTER);
  assert.deepEqual(plain(restored.transport.requests[0].payload), original);
  abandoned.reject(new TypeError('Abandoned response'));
  await completion;
  assert.equal(first.h.panel.updatesAfterUnmount, 0);
  await respond(restored.h, restored.transport.requests[0], { status: 'pending' });
  await respond(restored.h, restored.transport.requests[1], { status: 'created', box });
  assert.ok(visible(restored.h).includes(box.global_code));
  restored.h.unmount();
});

for (const stage of ['registration', 'execution']) test(`durable ${stage} rejection releases the draft without cancellation or replacement`, async () => {
  const { h, transport } = integrated();
  await select(h);
  const { completion } = await start(h);
  if (stage === 'execution') await respond(h, transport.requests[0], { status: 'pending' });
  await respond(h, transport.requests.at(-1), { status: 'rejected', errors: { strain: ['Unavailable strain'] } });
  await completion;
  assert.equal(h.panel.field('create-box-date-field').disabled, false);
  assert.ok(visible(h).includes('Unavailable strain'));
  assert.equal(h.env.window.sessionStorage.getItem(KEY), null);
  assert.ok(transport.requests.every(r => r.url === REGISTER || r.url === EXECUTE));
  assert.equal(h.resolutions.length, 0);
  h.unmount();
});

test('duplicated intact v2 tabs resume exactly the same key, secret and immutable payload', async () => {
  const original = integrated();
  await select(original.h);
  const { completion } = await start(original.h);
  const env = createBoxEnvironment();
  for (const key of [KEY, STORAGE]) env.window.sessionStorage.setItem(key, original.env.window.sessionStorage.getItem(key));
  const duplicate = integrated(env);
  await duplicate.h.panel.settle();
  assert.deepEqual(plain(duplicate.transport.requests[0].payload), plain(original.transport.requests[0].payload));
  await respond(original.h, original.transport.requests[0], { status: 'pending' });
  await respond(original.h, original.transport.requests[1], { status: 'created', box });
  await completion;
  await respond(duplicate.h, duplicate.transport.requests[0], { status: 'created', box });
  assert.equal(duplicate.transport.requests.length, 1, 'Committed registration never executes a second time');
  assert.ok(visible(duplicate.h).includes(box.global_code));
  original.h.unmount();
  duplicate.h.unmount();
});

for (const damage of ['uuid', 'payload', 'scope', 'divergence']) test(`${damage} damage never authorizes registration, execution or cancellation`, async () => {
  const env = createBoxEnvironment();
  const intent = payload();
  await env.load('../src/utils/boxCreationRecovery.ts').storeCreationIntent(STORAGE, intent);
  const envelope = JSON.parse(env.window.sessionStorage.getItem(STORAGE));
  if (damage === 'uuid') {
    envelope.request_id = crypto.randomUUID();
    env.window.sessionStorage.removeItem(KEY);
  }
  if (damage === 'payload') envelope.strain = 2;
  if (damage === 'scope') envelope.scope = 'polypbase.automaticBox.8.1';
  if (damage === 'divergence') env.window.sessionStorage.setItem(KEY, crypto.randomUUID());
  env.window.sessionStorage.setItem(STORAGE, JSON.stringify(envelope));
  const { h, transport } = integrated(env);
  await h.panel.settle();
  assert.equal(transport.requests.length, 0);
  assert.equal(h.resolutions.length, 0);
  assert.ok(env.requests.some(r => r.path.startsWith('/api/boxes/automatic/resolve/?')));
  assert.ok(env.window.sessionStorage.getItem(STORAGE));
  h.unmount();
});

test('actor replacement in the same institution remounts and cannot resume the old actor intention', async () => {
  const h = createPilotageHarness();
  await select(h);
  const { completion } = await start(h);
  const old = h.panel;
  const original = h.env.window.sessionStorage.getItem(STORAGE);
  h.setParentProps({ profile: { ...h.props.profile, id: 8 } });
  assert.notEqual(h.panel, old);
  await h.panel.settle();
  assert.equal(h.mutations.length, 1, 'The new actor does not resume the old in-memory or stored intention');
  h.mutations[0].reject(new TypeError('Old actor response lost'));
  await completion;
  assert.equal(old.updatesAfterUnmount, 0);
  assert.equal(h.env.window.sessionStorage.getItem(STORAGE), original);
  assert.equal(h.env.window.sessionStorage.getItem('polypbase.automaticBox.8.1'), null);
  assert.equal(h.panel.field('create-box-date-field').disabled, false);
  h.unmount();
});

test('institution replacement between registration and execution cannot dispatch the second stage', async () => {
  const h = appHarness();
  const pending = h.context.createBox(payload());
  const rejected = assert.rejects(pending, h.context.ApiResourceCancelledError);
  h.switchOrganization();
  h.requests[0].resolve({ status: 'pending' });
  await rejected;
  assert.equal(h.requests.length, 1);
  assert.equal(h.state.data.boxes.length, 0);
});
