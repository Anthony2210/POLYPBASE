import assert from 'node:assert/strict';
import test from 'node:test';
import { setup } from './perf-sweep.mjs';

test('the sweep authenticates before verifying the active profile', async () => {
  const base = new URL('http://127.0.0.1:18173');
  const events = [];
  let finishLogin;
  const loggedIn = new Promise(resolve => { finishLogin = resolve; });
  const page = {
    goto(url) { events.push('open'); assert.equal(url, new URL('/login?next=%2F', base).href); },
    locator(selector) {
      if (selector === '.login-screen') return { locator: control => ({
        fill() {},
        click() { assert.equal(control, 'button[type=submit]'); events.push('submit'); },
      }) };
      assert.equal(selector, '.pilotage-flow, .organization-choice-page');
      return { first: () => ({ waitFor() { events.push('app-ready'); } }) };
    },
    waitForResponse(predicate) {
      const response = (path, method) => ({
        url: () => new URL(path, base).href,
        request: () => ({ method: () => method }),
      });
      assert.ok(!predicate(response('/api/auth/session/', 'GET')));
      assert.ok(!predicate(response('/api/profile/', 'GET')));
      assert.ok(predicate(response('/api/auth/session/', 'POST')));
      return loggedIn;
    },
    evaluate(_callback, params) {
      if (!params) return '7';
      assert.equal(params.path, '/api/profile/');
      events.push('verify-profile');
      return { memberships: [{ role: 'lab_technician', organization: { id: 7, slug: 'demo' } }] };
    },
  };
  const result = setup(page, { base, orgSlug: 'demo', credentials: { lab: { email: 'lab@example.test', password: 'fixture' } } }, 'lab', []);
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(events, ['open', 'submit']);
  finishLogin({ ok: () => true });
  assert.equal((await result).orgId, 7);
  assert.deepEqual(events, ['open', 'submit', 'app-ready', 'verify-profile']);
});
