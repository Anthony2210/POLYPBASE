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

const dateFormat = loadModule('../src/utils/dateFormat.ts');
const audit = loadModuleWithRequire('../src/utils/auditPresentation.ts', {
  './dateFormat': dateFormat,
});
const personalActions = loadModuleWithRequire('../src/utils/personalActions.ts', {
  './auditPresentation': audit,
});

const fr = loadModule('../src/i18n/fr.ts').fr;
const en = loadModule('../src/i18n/en.ts').en;

function translator(catalog) {
  return (key) => catalog[key] ?? key;
}

const tFr = translator(fr);
const tEn = translator(en);

test('FR measurement creation is readable and distinct from a correction', () => {
  const created = audit.getAuditDescriptionLabel(
    { action: 'entry', description: 'Biological measurement for 2026-09-15' },
    tFr,
  );
  const corrected = audit.getAuditDescriptionLabel(
    { action: 'update', description: 'Biological measurement for 2026-09-15' },
    tFr,
  );

  assert.equal(created, 'Relevé biologique enregistré pour le 15/09/2026');
  assert.equal(corrected, 'Relevé biologique corrigé pour le 15/09/2026');
  assert.notEqual(created, corrected);
});

test('EN measurement creation is readable and distinct from a correction', () => {
  const created = audit.getAuditDescriptionLabel(
    { action: 'entry', description: 'Biological measurement for 2026-09-15' },
    tEn,
  );
  const corrected = audit.getAuditDescriptionLabel(
    { action: 'update', description: 'Biological measurement for 2026-09-15' },
    tEn,
  );

  assert.equal(created, 'Biological measurement recorded for 15/09/2026');
  assert.equal(corrected, 'Biological measurement corrected for 15/09/2026');
  assert.notEqual(created, corrected);
});

test('a legacy edited measurement keeps the correction wording', () => {
  const label = audit.getAuditDescriptionLabel(
    { action: 'update', description: 'Biological measurement edited for 2026-09-15' },
    tFr,
  );
  assert.equal(label, 'Relevé biologique corrigé pour le 15/09/2026');
});

test('the legacy measurement creation description is translated in FR', () => {
  const label = audit.getAuditDescriptionLabel(
    { action: 'entry', description: 'Biological measurement recorded' },
    tFr,
  );

  assert.equal(label, 'Relevé biologique enregistré');
  assert.equal(label.includes('Biological measurement recorded'), false);
  assert.notEqual(label, tFr('auditDescriptionMeasurementCorrected'));
});

test('the legacy measurement creation description is translated in EN', () => {
  const label = audit.getAuditDescriptionLabel(
    { action: 'entry', description: 'Biological measurement recorded' },
    tEn,
  );

  assert.equal(label, 'Biological measurement recorded');
  assert.notEqual(label, tEn('auditDescriptionMeasurementCorrected'));
});

test('the legacy measurement creation is treated as a creation, not a correction', () => {
  const legacy = { action: 'entry', description: 'Biological measurement recorded' };
  const modernCreation = { action: 'entry', description: 'Biological measurement for 2026-09-15' };
  const correction = { action: 'update', description: 'Biological measurement for 2026-09-15' };

  const legacyFr = audit.getAuditDescriptionLabel(legacy, tFr);
  const legacyEn = audit.getAuditDescriptionLabel(legacy, tEn);

  // Same creation family as the modern description, and never the correction wording.
  assert.equal(legacyFr.startsWith('Relevé biologique enregistré'), true);
  assert.equal(legacyEn.startsWith('Biological measurement recorded'), true);
  assert.notEqual(legacyFr, audit.getAuditDescriptionLabel(correction, tFr));
  assert.notEqual(legacyEn, audit.getAuditDescriptionLabel(correction, tEn));
  assert.notEqual(legacyFr, audit.getAuditDescriptionLabel(modernCreation, tFr));
});

test('Responsable events render readable FR and EN wording', () => {
  const cases = [
    [
      'Institution Responsable granted by platform',
      'Statut Responsable accordé par la plateforme',
      'Responsable status granted by the platform',
    ],
    [
      'Institution Responsable revoked by platform',
      'Statut Responsable retiré par la plateforme',
      'Responsable status revoked by the platform',
    ],
    [
      'Institution Responsable relinquished',
      'Statut Responsable abandonné',
      'Responsable status relinquished',
    ],
  ];

  for (const [description, frLabel, enLabel] of cases) {
    const entry = { action: 'update', description };
    assert.equal(audit.getAuditDescriptionLabel(entry, tFr), frLabel);
    assert.equal(audit.getAuditDescriptionLabel(entry, tEn), enLabel);
  }
});

test('box deactivation and reactivation are readable in both languages', () => {
  const deactivated = { action: 'update', description: 'Box deactivated: ATL-AAU-1.001' };
  const reactivated = { action: 'update', description: 'Box reactivated: ATL-AAU-1.001' };

  assert.equal(audit.getAuditDescriptionLabel(deactivated, tFr), 'Boîte désactivée : ATL-AAU-1.001');
  assert.equal(audit.getAuditDescriptionLabel(reactivated, tFr), 'Boîte réactivée : ATL-AAU-1.001');
  assert.equal(audit.getAuditDescriptionLabel(deactivated, tEn), 'Box deactivated: ATL-AAU-1.001');
  assert.equal(audit.getAuditDescriptionLabel(reactivated, tEn), 'Box reactivated: ATL-AAU-1.001');
});

test('member access descriptions are readable and never raw English in FR', () => {
  const cases = [
    ['Member access created', 'Accès utilisateur créé'],
    ['Member access restored', 'Accès utilisateur réactivé'],
    ['Member access updated', 'Accès utilisateur modifié'],
  ];

  for (const [description, frLabel] of cases) {
    assert.equal(audit.getAuditDescriptionLabel({ action: 'update', description }, tFr), frLabel);
  }
});

test('member role and activation changes are readable', () => {
  assert.equal(audit.getAuditMetadataKeyLabel('role', tFr), 'Rôle');
  assert.equal(audit.getAuditMetadataKeyLabel('acces_actif', tFr), 'Accès actif');
  assert.equal(audit.getAuditMetadataKeyLabel('role', tEn), 'Role');
  assert.equal(audit.getAuditMetadataKeyLabel('acces_actif', tEn), 'Access active');
  assert.equal(audit.getAuditValueLabel('lab_technician', tFr), 'Technicien');
  assert.equal(audit.getAuditValueLabel('viewer', tEn), 'Viewer');
  assert.equal(audit.formatAuditMetadataValue(true, tFr), 'Oui');
  assert.equal(audit.formatAuditMetadataValue(false, tEn), 'No');
});

test('raw is_responsable wording never reaches the interface', () => {
  const entry = {
    action: 'update',
    description: 'Member access updated',
    metadata: { valeurs: { is_responsable: true, role: 'admin' } },
  };

  const keyLabel = audit.getAuditMetadataKeyLabel('is_responsable', tFr);
  assert.equal(keyLabel, 'Statut Responsable');
  assert.equal(keyLabel.includes('is_responsable'), false);
  assert.equal(audit.getAuditMetadataKeyLabel('is_responsable', tEn), 'Responsable status');
  assert.equal(audit.getAuditDescriptionLabel(entry, tFr).includes('is_responsable'), false);
  assert.equal(audit.formatAuditMetadataValue(entry.metadata.valeurs.is_responsable, tFr), 'Oui');
});

test('a scientific zero stays a real value', () => {
  assert.equal(audit.formatAuditMetadataValue(0, tFr), '0');
  assert.equal(audit.formatAuditMetadataValue(0, tEn), '0');
  assert.equal(audit.formatAuditChange({ avant: 4, apres: 0 }, tFr), '4 -> 0');
  assert.equal(audit.formatAuditChange({ before: 0, after: 0 }, tEn), '0 -> 0');
  assert.equal(audit.formatAuditMetadataValue(null, tFr), '-');
  assert.equal(audit.formatAuditMetadataValue('', tFr), '-');
  assert.equal(audit.formatAuditMetadataValue(undefined, tFr), '-');
});

test('known business events never fall through to raw English in FR', () => {
  const descriptions = [
    'Biological measurement for 2026-09-15',
    'Biological measurement edited for 2026-09-15',
    'Biological measurement recorded',
    'Box created manually: ATL-AAU-1.001',
    'Box opened: ATL-AAU-1.001',
    'QR scan of ATL-AAU-1.001',
    'Box archived: ATL-AAU-1.001',
    'Box activated: ATL-AAU-1.001',
    'Box deactivated: ATL-AAU-1.001',
    'Box reactivated: ATL-AAU-1.001',
    'Box moved to Cabinet-15',
    'Subculture created from ATL-AAU-1.001',
    'Manual temperature recorded: Cabinet-15',
    'Thermal zone created: Cabinet-15',
    'Thermal zone updated: Cabinet-15',
    'Probe created: SONDE-15-01',
    'Box transfer prepared: ATL-AAU-1.001',
    'Transfer imported from Aquarium de Paris',
    'Alert resolved: Polyp drop detected',
    'Historical box inventory initialized for Aquarium de Paris',
    'Historical box qualified as active: ATL-AAU-1.001',
    'Species created: Aurelia aurita',
    'Species updated: Aurelia aurita',
    'Strain created: 1-ATL',
    'Strain updated: 1-ATL',
    'Member access created',
    'Member access restored',
    'Member access updated',
    'Weekly biological measurement CSV export',
    'Organization created',
    'Organization updated',
    'Organization deleted',
    'Institution Responsable granted by platform',
    'Institution Responsable revoked by platform',
    'Institution Responsable relinquished',
    'Password reset from the login page',
  ];

  for (const description of descriptions) {
    const label = audit.getAuditDescriptionLabel({ action: 'update', description }, tFr);
    assert.notEqual(label, description, `unmapped description: ${description}`);
  }
});

test('the qualified historical box status is translated', () => {
  const label = audit.getAuditDescriptionLabel(
    { action: 'update', description: 'Historical box qualified as inactive: ATL-AAU-1.001' },
    tFr,
  );
  assert.equal(label, 'Boîte historique qualifiée (Inactive) : ATL-AAU-1.001');
});

test('readable account labels never expose an internal username', () => {
  assert.equal(audit.getAccountDisplayLabel('internal_0123456789abcdef'), '');
  assert.equal(audit.getAccountDisplayLabel('Camille DURAND'), 'Camille DURAND');
  assert.equal(audit.getAccountDisplayLabel('camille@example.org'), 'camille@example.org');
  assert.equal(audit.getAccountDisplayLabel(null), '');
  assert.equal(audit.getAccountDisplayLabel(''), '');
  assert.equal(audit.getAccountDisplayLabel('  '), '');
});

test('administration account targets use trusted values, never the internal id', () => {
  const entry = {
    action: 'update',
    description: 'Member access updated',
    object_type: 'account',
    object_id: 'internal_0123456789abcdef',
    metadata: { valeurs: { nom: 'Camille DURAND', email: 'camille@example.org' } },
  };
  assert.equal(audit.getAuditTargetLabel(entry), 'Camille DURAND');

  const emailOnly = {
    ...entry,
    metadata: { valeurs: { nom: 'internal_0123456789abcdef', email: 'camille@example.org' } },
  };
  assert.equal(audit.getAuditTargetLabel(emailOnly), 'camille@example.org');

  const noValues = { ...entry, metadata: {} };
  assert.equal(audit.getAuditTargetLabel(noValues), '');

  const boxEntry = {
    action: 'update',
    description: 'Box deactivated: ATL-AAU-1.001',
    object_type: 'box',
    object_id: 'ATL-AAU-1.001',
  };
  assert.equal(audit.getAuditTargetLabel(boxEntry), 'ATL-AAU-1.001');
});

test('personal resource labels never expose an internal identifier', () => {
  assert.equal(
    audit.getPersonalResourceLabel({
      type: 'account',
      identifier: 'internal_0123456789abcdef',
      label: 'Camille DURAND',
    }),
    'Camille DURAND',
  );
  assert.equal(
    audit.getPersonalResourceLabel({
      type: 'account',
      identifier: 'internal_0123456789abcdef',
      label: null,
    }),
    '',
  );
  assert.equal(
    audit.getPersonalResourceLabel({
      type: 'account',
      identifier: 'internal_0123456789abcdef',
      label: 'internal_0123456789abcdef',
    }),
    '',
  );
  assert.equal(
    audit.getPersonalResourceLabel({
      type: 'box',
      identifier: 'ATL-AAU-1.001',
      label: 'ATL-AAU-1.001',
    }),
    'ATL-AAU-1.001',
  );
});

test('action and object type labels are translated', () => {
  assert.equal(audit.getAuditActionLabel({ action: 'entry' }, tFr), 'Nouvelle donnée enregistrée');
  assert.equal(audit.getAuditActionLabel({ action: 'update' }, tEn), 'Change recorded');
  assert.equal(audit.getAuditActionLabel({ action: 'unknown', action_label: 'Custom' }, tFr), 'Custom');
  assert.equal(audit.getAuditObjectTypeLabel('account', tFr), 'Compte');
  assert.equal(audit.getAuditObjectTypeLabel('box', tEn), 'Box');
  assert.equal(audit.getAuditObjectTypeLabel('', tFr), '-');
});

test('personal action details keep the allowlisted shape and hide empty details', () => {
  const details = personalActions.getPersonalActionDetails({
    values: { polypes: 0, ephyrules: 0 },
    changes: { polypes: { before: 4, after: 0 } },
  });
  assert.equal(JSON.stringify(details.values), JSON.stringify({ polypes: 0, ephyrules: 0 }));
  assert.equal(
    JSON.stringify(details.changes),
    JSON.stringify({ polypes: { before: 4, after: 0 } }),
  );

  assert.equal(personalActions.hasPersonalActionDetails({}), false);
  assert.equal(personalActions.hasPersonalActionDetails(undefined), false);
  assert.equal(personalActions.hasPersonalActionDetails({ values: {} }), false);
  assert.equal(personalActions.hasPersonalActionDetails({ values: { polypes: 0 } }), true);
  assert.equal(personalActions.hasPersonalActionDetails({ changes: { polypes: { before: 0, after: 0 } } }), true);
});

test('personal action details drop hidden technical keys', () => {
  const details = personalActions.getPersonalActionDetails({
    values: { polypes: 3, strobiles: 1, statut_culture: 'good', a_verifier: false },
  });
  assert.equal(JSON.stringify(details.values), JSON.stringify({ polypes: 3 }));
});

test('loading another personal action page appends only unknown actions', () => {
  const current = [{ id: 3 }, { id: 2 }];
  const incoming = [{ id: 2 }, { id: 1 }];
  assert.equal(
    JSON.stringify(personalActions.mergePersonalActionPage(current, incoming).map((entry) => entry.id)),
    JSON.stringify([3, 2, 1]),
  );
  assert.equal(JSON.stringify(personalActions.mergePersonalActionPage([], [])), JSON.stringify([]));
});

function loadedPersonalActionsState() {
  return personalActions.applyPersonalActionsOutcome(
    personalActions.EMPTY_PERSONAL_ACTIONS_STATE,
    {
      kind: 'initial-page',
      page: { results: [{ id: 3 }, { id: 2 }], has_more: true, next_offset: 2 },
    },
  );
}

test('a load-more failure keeps the displayed actions and the next offset', () => {
  const failed = personalActions.applyPersonalActionsOutcome(loadedPersonalActionsState(), {
    kind: 'load-more-error',
    message: 'Network error',
  });

  assert.equal(JSON.stringify(failed.entries.map((entry) => entry.id)), JSON.stringify([3, 2]));
  assert.equal(failed.hasMore, true);
  assert.equal(failed.nextOffset, 2);
  assert.equal(failed.loadMoreError, 'Network error');
  assert.equal(failed.error, null);
});

test('a retry after a load-more failure requests the same offset', () => {
  const loaded = loadedPersonalActionsState();
  const failed = personalActions.applyPersonalActionsOutcome(loaded, {
    kind: 'load-more-error',
    message: 'Network error',
  });

  // The retry reads nextOffset from the failed state, so it must be unchanged.
  assert.equal(failed.nextOffset, loaded.nextOffset);
  assert.equal(failed.nextOffset, 2);
});

test('a successful retry merges without duplicates and clears the pagination error', () => {
  const failed = personalActions.applyPersonalActionsOutcome(loadedPersonalActionsState(), {
    kind: 'load-more-error',
    message: 'Network error',
  });
  const retried = personalActions.applyPersonalActionsOutcome(failed, {
    kind: 'load-more-page',
    page: { results: [{ id: 2 }, { id: 1 }], has_more: false, next_offset: null },
  });

  assert.equal(JSON.stringify(retried.entries.map((entry) => entry.id)), JSON.stringify([3, 2, 1]));
  assert.equal(retried.loadMoreError, null);
  assert.equal(retried.hasMore, false);
  assert.equal(retried.nextOffset, null);
});

test('an initial-load error stays distinct from a pagination error', () => {
  const initialFailure = personalActions.applyPersonalActionsOutcome(
    personalActions.EMPTY_PERSONAL_ACTIONS_STATE,
    { kind: 'initial-error', message: 'Initial failure' },
  );

  assert.equal(initialFailure.error, 'Initial failure');
  assert.equal(initialFailure.loadMoreError, null);
  assert.equal(JSON.stringify(initialFailure.entries), JSON.stringify([]));

  const paginationFailure = personalActions.applyPersonalActionsOutcome(
    loadedPersonalActionsState(),
    { kind: 'load-more-error', message: 'Pagination failure' },
  );

  assert.equal(paginationFailure.error, null);
  assert.equal(paginationFailure.loadMoreError, 'Pagination failure');
  assert.equal(paginationFailure.entries.length, 2);
});

test('a fresh initial page clears both error states', () => {
  const withErrors = personalActions.applyPersonalActionsOutcome(
    personalActions.applyPersonalActionsOutcome(loadedPersonalActionsState(), {
      kind: 'load-more-error',
      message: 'Pagination failure',
    }),
    { kind: 'initial-error', message: 'Initial failure' },
  );
  assert.equal(withErrors.error, 'Initial failure');
  assert.equal(withErrors.loadMoreError, 'Pagination failure');

  const reloaded = personalActions.applyPersonalActionsOutcome(withErrors, {
    kind: 'initial-page',
    page: { results: [{ id: 9 }], has_more: false, next_offset: null },
  });

  assert.equal(reloaded.error, null);
  assert.equal(reloaded.loadMoreError, null);
  assert.equal(JSON.stringify(reloaded.entries.map((entry) => entry.id)), JSON.stringify([9]));
});

test('the empty personal actions state carries no error', () => {
  const empty = personalActions.EMPTY_PERSONAL_ACTIONS_STATE;
  assert.equal(empty.error, null);
  assert.equal(empty.loadMoreError, null);
  assert.equal(empty.hasMore, false);
  assert.equal(empty.nextOffset, null);
  assert.equal(JSON.stringify(empty.entries), JSON.stringify([]));
});
