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

function loadModuleWithRequire(relativePath, requireMap) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  const require = (specifier) => {
    if (specifier in requireMap) return requireMap[specifier];
    throw new Error(`Unexpected require: ${specifier}`);
  };
  vm.runInNewContext(outputText, { exports, require });
  return exports;
}

class FakeApiError extends Error {
  constructor(message, data) {
    super(message);
    this.data = data;
  }
}

const accountMembers = loadModule('../src/utils/accountMembers.ts');
const memberMutationLock = loadModule('../src/utils/memberMutationLock.ts');
const memberFeedback = loadModule('../src/utils/memberFeedback.ts');
const errors = loadModuleWithRequire('../src/utils/errors.ts', {
  '../api/client': { ApiError: FakeApiError },
});

const regularAdminContext = {
  canManageAdminMemberships: false,
  canRelinquishResponsable: false,
};
const responsableContext = {
  canManageAdminMemberships: true,
  canRelinquishResponsable: true,
};

function buildMember(overrides = {}) {
  return {
    role: 'viewer',
    role_label: 'Lecteur',
    is_responsable: false,
    is_active: true,
    is_self: false,
    ...overrides,
  };
}

function actionsFor(member, context = regularAdminContext) {
  return accountMembers.getMemberRowActions(buildMember(member), context)
    .map((item) => item.action)
    .join(',');
}

test('a Responsable admin uses the translated Responsable label', () => {
  assert.equal(
    accountMembers.getAccountMemberRoleLabel(
      buildMember({ role: 'admin', role_label: 'Administrateur', is_responsable: true }),
      'Responsable',
    ),
    'Responsable',
  );
});

test('an ordinary admin keeps the API role label', () => {
  assert.equal(
    accountMembers.getAccountMemberRoleLabel(
      buildMember({ role: 'admin', role_label: 'Administrateur' }),
      'Responsable',
    ),
    'Administrateur',
  );
});

test('a lower role with an invalid Responsable flag keeps the API role label', () => {
  assert.equal(
    accountMembers.getAccountMemberRoleLabel(
      buildMember({ role: 'lab_technician', role_label: 'Technicien', is_responsable: true }),
      'Responsable',
    ),
    'Technicien',
  );
});

test('account refusal codes are translated instead of showing raw API prose', () => {
  const t = (key) => `translated:${key}`;
  const cases = {
    membership_admin_required: 'manageErrorMembershipAdminRequired',
    responsable_required: 'manageErrorResponsableRequired',
    responsable_membership_protected: 'manageErrorResponsableProtected',
    active_responsable_required: 'manageErrorActiveResponsableRequired',
    last_active_responsable: 'manageErrorLastActiveResponsable',
  };

  for (const [code, key] of Object.entries(cases)) {
    const error = new FakeApiError('Raw backend prose', { detail: 'Raw backend prose', code });
    assert.equal(errors.getAccountErrorMessage(error, t), `translated:${key}`);
  }
});

test('an unknown account error keeps the API message and non-API errors use the fallback', () => {
  const t = (key) => `translated:${key}`;
  const unknownCode = new FakeApiError('Raw backend prose', { detail: 'Raw backend prose' });
  assert.equal(errors.getAccountErrorMessage(unknownCode, t), 'Raw backend prose');
  assert.equal(errors.getAccountErrorMessage(new Error('boom'), t), 'Impossible de joindre l API Django.');
});

test('Admin filtering and counts include Responsables without double counting', () => {
  const members = [
    buildMember({ role: 'admin', is_responsable: true }),
    buildMember({ role: 'admin' }),
    buildMember({ role: 'lab_technician' }),
    buildMember({ role: 'viewer' }),
  ];
  const counts = accountMembers.getMemberRoleCounts(members);
  const admins = accountMembers.filterMembersByRole(members, 'admin');

  assert.equal(admins.length, 2);
  assert.equal(admins.filter((member) => member.is_responsable).length, 1);
  assert.equal(counts.admin, 2);
  assert.equal(counts.all, 4);
  assert.equal(counts.admin + counts.lab_technician + counts.viewer, counts.all);
});

test('a regular admin keeps viewer and lab technician transitions', () => {
  assert.equal(actionsFor({ role: 'viewer' }), 'promote,deactivate');
  assert.equal(actionsFor({ role: 'lab_technician' }), 'demote,deactivate');
  assert.equal(actionsFor({ role: 'viewer', is_active: false }), 'promote,reactivate');
  assert.equal(actionsFor({ role: 'lab_technician', is_active: false }), 'demote,reactivate');
});

test('a regular admin cannot mutate another ordinary admin', () => {
  assert.equal(actionsFor({ role: 'admin' }), '');
  assert.equal(actionsFor({ role: 'admin', is_active: false }), '');
});

test('a regular admin cannot promote lower roles to admin', () => {
  assert.equal(actionsFor({ role: 'viewer' }).includes('promote_to_admin'), false);
  assert.equal(actionsFor({ role: 'lab_technician' }).includes('promote_to_admin'), false);
});

test('a Responsable can manage ordinary admins', () => {
  assert.equal(
    actionsFor({ role: 'admin' }, responsableContext),
    'demote_to_technician,deactivate',
  );
  assert.equal(
    actionsFor({ role: 'admin', is_active: false }, responsableContext),
    'demote_to_technician,reactivate',
  );
});

test('a Responsable preserves lower transitions and can promote lower roles to admin', () => {
  assert.equal(
    actionsFor({ role: 'viewer' }, responsableContext),
    'promote,promote_to_admin,deactivate',
  );
  assert.equal(
    actionsFor({ role: 'lab_technician' }, responsableContext),
    'demote,promote_to_admin,deactivate',
  );
});

test('no generic action is offered against another Responsable', () => {
  assert.equal(
    actionsFor({ role: 'admin', is_responsable: true }, responsableContext),
    '',
  );
  assert.equal(
    actionsFor({ role: 'admin', is_responsable: true, is_active: false }, responsableContext),
    '',
  );
});

test('self Responsable can relinquish only when the backend allows it', () => {
  const selfResponsable = { role: 'admin', is_responsable: true, is_self: true };
  assert.equal(actionsFor(selfResponsable, responsableContext), 'relinquish_responsable');
  assert.equal(
    actionsFor(selfResponsable, {
      canManageAdminMemberships: true,
      canRelinquishResponsable: false,
    }),
    '',
  );
});

test('destructive role and access actions are marked as danger', () => {
  const actions = accountMembers.getMemberRowActions(
    buildMember({ role: 'admin' }),
    responsableContext,
  );
  const danger = Object.fromEntries(actions.map((item) => [item.action, item.danger]));
  assert.equal(danger.demote_to_technician, true);
  assert.equal(danger.deactivate, true);

  const relinquish = accountMembers.getMemberRowActions(
    buildMember({ role: 'admin', is_responsable: true, is_self: true }),
    responsableContext,
  );
  assert.equal(relinquish[0].danger, true);
});

test('member mutation feedback encodes the direction of the action', () => {
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.promote.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.promote_to_admin.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.reactivate.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.created.tone, 'positive');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.demote.tone, 'negative');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.demote_to_technician.tone, 'negative');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.deactivate.tone, 'negative');
  assert.equal(memberFeedback.MEMBER_MUTATION_FEEDBACK.relinquish_responsable.tone, 'negative');
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
