import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

function loadModule(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const accountMembers = loadModule('../src/utils/accountMembers.ts');
const memberMutationLock = loadModule('../src/utils/memberMutationLock.ts');
const memberFeedback = loadModule('../src/utils/memberFeedback.ts');

function actionsFor(member) {
  return accountMembers.getMemberRowActions(member).map((item) => item.action).join(',');
}

test('a viewer can be promoted to lab technician', () => {
  assert.equal(actionsFor({ role: 'viewer', is_active: true, is_self: false }), 'promote,deactivate');
});

test('a lab technician can be demoted to viewer', () => {
  assert.equal(
    actionsFor({ role: 'lab_technician', is_active: true, is_self: false }),
    'demote,deactivate',
  );
});

test('an administrator exposes no editable role action', () => {
  assert.equal(actionsFor({ role: 'admin', is_active: true, is_self: false }), 'deactivate');
});

test('an inactive member is reactivated instead of deactivated', () => {
  assert.equal(
    actionsFor({ role: 'viewer', is_active: false, is_self: false }),
    'promote,reactivate',
  );
});

test('self-protection leaves no access-toggle action available', () => {
  assert.equal(actionsFor({ role: 'admin', is_active: true, is_self: true }), '');
  assert.equal(actionsFor({ role: 'viewer', is_active: false, is_self: true }), 'promote');
});

test('destructive role and access actions are marked as danger', () => {
  const actions = accountMembers.getMemberRowActions({
    role: 'lab_technician',
    is_active: true,
    is_self: false,
  });
  const danger = Object.fromEntries(actions.map((item) => [item.action, item.danger]));
  assert.equal(danger.demote, true);
  assert.equal(danger.deactivate, true);
  const promotion = accountMembers.getMemberRowActions({
    role: 'viewer',
    is_active: false,
    is_self: false,
  });
  assert.equal(promotion[0].danger, undefined);
  assert.equal(promotion[1].danger, undefined);
});

test('member mutation feedback encodes the direction of the action', () => {
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.promote.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.reactivate.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.created.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.demote.tone, 'negative');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.deactivate.tone, 'negative');
});

test('member row class reflects inactive and feedback states', () => {
  assert.equal(memberFeedback.getMemberRowClassName(true, null), '');
  assert.equal(memberFeedback.getMemberRowClassName(false, null), 'is-inactive');
  assert.equal(memberFeedback.getMemberRowClassName(true, 'positive'), 'is-updated-positive');
  assert.equal(memberFeedback.getMemberRowClassName(true, 'negative'), 'is-updated-negative');
  assert.equal(
    memberFeedback.getMemberRowClassName(false, 'negative'),
    'is-inactive is-updated-negative',
  );
});

test('a membership cannot start a duplicate mutation while busy', () => {
  const busy = new Set();
  assert.equal(memberMutationLock.beginMemberMutation(busy, 1), true);
  assert.equal(memberMutationLock.beginMemberMutation(busy, 1), false);
});

test('starting another membership keeps the first one locked', () => {
  const busy = new Set();
  assert.equal(memberMutationLock.beginMemberMutation(busy, 1), true);
  assert.equal(memberMutationLock.beginMemberMutation(busy, 2), true);
  assert.equal(busy.has(1), true);
  assert.equal(busy.has(2), true);
});

test('ending a mutation releases only that membership', () => {
  const busy = new Set();
  memberMutationLock.beginMemberMutation(busy, 1);
  memberMutationLock.beginMemberMutation(busy, 2);
  memberMutationLock.endMemberMutation(busy, 1);
  assert.equal(busy.has(1), false);
  assert.equal(busy.has(2), true);
  assert.equal(memberMutationLock.beginMemberMutation(busy, 1), true);
});
