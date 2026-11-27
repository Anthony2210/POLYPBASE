import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(
  new URL('../src/components/AdminView.tsx', import.meta.url),
  'utf8',
);
const ast = ts.createSourceFile(
  'AdminView.tsx',
  source,
  ts.ScriptTarget.Latest,
  true,
  ts.ScriptKind.TSX,
);

function findNode(root, predicate) {
  let found;
  function visit(node) {
    if (predicate(node)) found = node;
    ts.forEachChild(node, visit);
  }
  visit(root);
  assert.ok(found, 'Missing AdminView implementation node');
  return found;
}

function functionNode(name) {
  return findNode(
    ast,
    (node) => ts.isFunctionDeclaration(node) && node.name?.text === name,
  );
}

function evaluate(code, context) {
  const { outputText } = ts.transpileModule(code, {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.None,
    },
  });
  return vm.runInNewContext(outputText, context);
}

function memberHarness(overrides = {}) {
  const calls = [];
  const context = {
    isAdding: false,
    organizationId: 7,
    data: { roles: [{ value: 'viewer' }] },
    form: {
      email: ' invitee@example.org ',
      first_name: 'Invitee',
      last_name: 'FIXTURE',
    },
    role: 'viewer',
    emptyMemberForm: {},
    isMountedRef: { current: true },
    formatFirstName: (value) => value,
    formatLastName: (value) => value,
    setIsAdding: (value) => calls.push(['adding', value]),
    setFormError: (value) => calls.push(['error', value]),
    setFormFieldErrors() {},
    setForm: (value) => calls.push(['form', value]),
    setRole: (value) => calls.push(['role', value]),
    setIsMemberFormOpen: (value) => calls.push(['open', value]),
    apiPost: async (url, payload) => {
      calls.push(['post', url, payload.organization_id]);
      return { membership_id: 12, invitation: { id: 12 } };
    },
    invitationsStoreRef: {
      current: { applyMutation: (row) => calls.push(['invitation', row.id]) },
    },
    loadInvitations: async () => calls.push(['refresh']),
    showMemberFeedback: (id, message) => calls.push(['feedback', id, message]),
    ...overrides,
  };
  evaluate(functionNode('handleAddMember').getText(ast), context);
  return {
    calls,
    submit: () => context.handleAddMember({ preventDefault() {} }),
  };
}

test('member submission does nothing before data or organization is available or while adding', async () => {
  for (const overrides of [
    { data: null },
    { organizationId: null },
    { isAdding: true },
  ]) {
    const h = memberHarness(overrides);
    await h.submit();
    assert.deepEqual(h.calls, []);
  }
});

test('loaded member submission preserves invitation refresh and role reset', async () => {
  const h = memberHarness();
  await h.submit();
  assert.deepEqual(h.calls, [
    ['adding', true],
    ['error', null],
    ['post', '/api/accounts/members/', 7],
    ['invitation', 12],
    ['refresh'],
    ['form', {}],
    ['role', 'viewer'],
    ['open', false],
    ['feedback', 12, 'created'],
    ['adding', false],
  ]);
});
