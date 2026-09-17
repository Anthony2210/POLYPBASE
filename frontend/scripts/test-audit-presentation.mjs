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
  vm.runInNewContext(outputText, { exports, URLSearchParams });
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
  vm.runInNewContext(outputText, { exports, require, URLSearchParams });
  return exports;
}

const dateFormat = loadModule('../src/utils/dateFormat.ts');
const audit = loadModuleWithRequire('../src/utils/auditPresentation.ts', {
  './dateFormat': dateFormat,
});
const personalActions = loadModuleWithRequire('../src/utils/personalActions.ts', {
  './auditPresentation': audit,
});
const adminAudit = loadModuleWithRequire('../src/utils/adminAudit.ts', {
  './auditPresentation': audit,
});

function readSource(relativePath) {
  return readFileSync(new URL(relativePath, import.meta.url), 'utf8');
}

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

  assert.equal(created, 'Relevé biologique enregistré');
  assert.equal(corrected, 'Relevé biologique corrigé');
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

  assert.equal(created, 'Biological measurement recorded');
  assert.equal(corrected, 'Biological measurement corrected');
  assert.notEqual(created, corrected);
});

test('a legacy edited measurement keeps the correction wording', () => {
  const label = audit.getAuditDescriptionLabel(
    { action: 'update', description: 'Biological measurement edited for 2026-09-15' },
    tFr,
  );
  assert.equal(label, 'Relevé biologique corrigé');
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

  assert.equal(legacyFr, audit.getAuditDescriptionLabel(modernCreation, tFr));
  assert.equal(legacyEn, audit.getAuditDescriptionLabel(modernCreation, tEn));
  assert.notEqual(legacyFr, audit.getAuditDescriptionLabel(correction, tFr));
  assert.notEqual(legacyEn, audit.getAuditDescriptionLabel(correction, tEn));
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
  const keyLabel = audit.getAuditMetadataKeyLabel('is_responsable', tFr);
  assert.equal(keyLabel, 'Statut Responsable');
  assert.equal(keyLabel.includes('is_responsable'), false);
  assert.equal(audit.getAuditMetadataKeyLabel('is_responsable', tEn), 'Responsable status');
  assert.equal(audit.formatAuditMetadataValue(true, tFr), 'Oui');
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

test('rich inline summaries preserve box wording, changes, notes, and zero', () => {
  const recorded = { type: 'measurement', values: { polypes: 0, ephyrules: 3, salinite_psu: '2.00', note: 'Stable' } };
  const corrected = {
    type: 'measurement',
    changes: {
      polypes: { before: 0, after: 5 },
      ephyrules: { before: 3, after: 8 },
      salinite_psu: { before: '2.00', after: '2.80' },
      note: { before: '', after: 'À surveiller' },
    },
  };
  const movement = { type: 'box_movement', from_zone: 'Cabinet 15 C', to_zone: 'Cabinet 10 C' };

  assert.equal(JSON.stringify(audit.getAuditBoxSummaryParts({ action: 'entry', description: '', business_details: recorded }, tFr)), JSON.stringify(['Relevé ', ' effectué']));
  assert.equal(JSON.stringify(audit.getAuditBoxSummaryParts({ action: 'update', description: '', business_details: corrected }, tFr)), JSON.stringify(['Relevé ', ' corrigé']));
  assert.equal(JSON.stringify(audit.getAuditBoxSummaryParts({ action: 'update', description: '', business_details: movement }, tFr)), JSON.stringify(['', ' déplacée']));

  const recordedItems = audit.getAuditInlineBusinessItems(recorded, tFr);
  assert.equal(recordedItems[0].value, '0');
  assert.equal(recordedItems[2].unit, undefined);
  assert.equal(recordedItems[2].value, '2.00');
  const correctedItems = audit.getAuditInlineBusinessItems(corrected, tFr);
  assert.equal(JSON.stringify(correctedItems.map(({ key, before, after }) => ({ key, before, after }))), JSON.stringify([
    { key: 'polypes', before: '0', after: '5' },
    { key: 'ephyrules', before: '3', after: '8' },
    { key: 'salinite_psu', before: '2.00', after: '2.80' },
  ]));
  assert.equal(audit.getAuditBusinessNote(recorded), 'Stable');
  assert.equal(audit.getAuditBusinessNote(corrected), 'À surveiller');
  assert.equal(JSON.stringify(audit.getAuditInlineBusinessItems(movement, tFr)), JSON.stringify([{
    key: 'movement', label: 'Emplacement', before: 'Cabinet 15 C', after: 'Cabinet 10 C', showLabel: false,
  }]));
});

test('subculture summaries name all children and the parent in FR and EN', () => {
  const oneChild = {
    action: 'subculture',
    description: 'Subculture created from SF.001',
    business_details: {
      type: 'subculture',
      parent_global_code: 'SF.001',
      child_global_codes: ['SF.002'],
    },
  };
  const manyChildren = {
    ...oneChild,
    business_details: {
      ...oneChild.business_details,
      child_global_codes: ['SF.002', 'SF.003', 'SF.004'],
    },
  };

  assert.equal(audit.getAuditBusinessSummary(oneChild, tFr), 'SF.002 créée via repiquage de SF.001');
  assert.equal(audit.getAuditBusinessSummary(oneChild, tEn), 'SF.002 created by subculturing SF.001');
  assert.equal(
    audit.getAuditBusinessSummary(manyChildren, tFr),
    'SF.002, SF.003, SF.004 créées via repiquage de SF.001',
  );
  assert.equal(
    audit.getAuditBusinessSummary(manyChildren, tEn),
    'SF.002, SF.003, SF.004 created by subculturing SF.001',
  );
  // The rich child+parent summary replaces the parent presentation, so no
  // second parent reference is rendered.
  assert.equal(audit.hasAuditSubcultureSummary(oneChild.business_details), true);
  assert.equal(audit.hasAuditSubcultureSummary(manyChildren.business_details), true);
  assert.equal(audit.getAuditBoxSummaryParts(oneChild, tFr), null);
  assert.equal(audit.getAuditBoxSummaryParts(manyChildren, tFr), null);
});

test('a legacy subculture without usable children keeps the parent box visible', () => {
  const legacy = {
    action: 'subculture',
    description: 'Subculture created from SF.001',
    business_details: { type: 'subculture', parent_global_code: 'SF.001' },
  };

  // No usable child list: the rich summary must not mask the parent, so the
  // established relation presentation stays available.
  assert.equal(audit.hasAuditSubcultureSummary(legacy.business_details), false);
  assert.equal(
    JSON.stringify(audit.getAuditBoxSummaryParts(legacy, tFr)),
    JSON.stringify(['Repiquage depuis ', '']),
  );
  assert.equal(
    JSON.stringify(audit.getAuditBoxSummaryParts(legacy, tEn)),
    JSON.stringify(['Subculture from ', '']),
  );
});

test('a subculture without parent or children invents no relation', () => {
  const orphan = {
    action: 'subculture',
    description: 'Subculture created from ',
    business_details: { type: 'subculture' },
  };

  assert.equal(audit.hasAuditSubcultureSummary(orphan.business_details), false);
  assert.equal(audit.getAuditBusinessSummary(orphan, tFr), 'Repiquage créé');

  const timelineSource = readSource('../src/components/AuditTimeline.tsx');
  assert.match(timelineSource, /if \(!parentCode && !children\.length\) return null;/);
  assert.match(timelineSource, /hidePrimaryResource \|\| !parentCode \? null/);
});

test('initial polyp wording distinguishes zero, one, plural, and missing values', () => {
  assert.equal(audit.getAuditInitialPolypsLabel(0, tFr), '0 polypes initiaux');
  assert.equal(audit.getAuditInitialPolypsLabel(1, tFr), '1 polype initial');
  assert.equal(audit.getAuditInitialPolypsLabel(2, tFr), '2 polypes initiaux');
  assert.equal(audit.getAuditInitialPolypsLabel(0, tEn), '0 initial polyps');
  assert.equal(audit.getAuditInitialPolypsLabel(1, tEn), '1 initial polyp');
  assert.equal(audit.getAuditInitialPolypsLabel(2, tEn), '2 initial polyps');

  const timelineSource = readSource('../src/components/AuditTimeline.tsx');
  assert.match(timelineSource, /child\.initial_polyp_count !== null/);
  assert.match(timelineSource, /getAuditInitialPolypsLabel\(child\.initial_polyp_count, t\)/);
});

test('structured lifecycle transitions distinguish activation, reactivation, and deactivation', () => {
  const cases = [
    [{ from: 'pending_review', to: 'active' }, [' activée', ' activated']],
    [{ from: 'inactive', to: 'active' }, [' réactivée', ' reactivated']],
    [{ from: 'active', to: 'inactive' }, [' désactivée', ' deactivated']],
  ];
  for (const [transition, [frSuffix, enSuffix]] of cases) {
    const entry = {
      action: 'update',
      description: 'Legacy wording must not decide the transition',
      business_details: { type: 'box_status', transition },
    };
    assert.equal(JSON.stringify(audit.getAuditBoxSummaryParts(entry, tFr)), JSON.stringify(['', frSuffix]));
    assert.equal(JSON.stringify(audit.getAuditBoxSummaryParts(entry, tEn)), JSON.stringify(['', enSuffix]));
  }
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

test('administration account targets use normalized business details, never the internal id', () => {
  const entry = {
    action: 'update',
    description: 'Member access updated',
    object_type: 'account',
    object_id: 'internal_0123456789abcdef',
    business_details: {
      type: 'account',
      values: { nom: 'Camille DURAND', email: 'camille@example.org' },
    },
  };
  assert.equal(audit.getAuditTargetLabel(entry), 'Camille DURAND');

  const emailOnly = {
    ...entry,
    business_details: {
      type: 'account',
      values: { nom: 'internal_0123456789abcdef', email: 'camille@example.org' },
    },
  };
  assert.equal(audit.getAuditTargetLabel(emailOnly), 'camille@example.org');

  const noValues = { ...entry, business_details: { type: 'account' } };
  assert.equal(audit.getAuditTargetLabel(noValues), '');

  const boxEntry = {
    action: 'update',
    description: 'Box deactivated: ATL-AAU-1.001',
    object_type: 'box',
    object_id: 'ATL-AAU-1.001',
  };
  assert.equal(audit.getAuditTargetLabel(boxEntry), 'ATL-AAU-1.001');

  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  assert.match(adminSource, /getAccountDisplayLabel\(entry\.user_display\) \|\| '-'/);
  assert.doesNotMatch(adminSource, /\{entry\.user\}/);
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

test('raw audit object ids are never rendered as history targets', () => {
  // Alerts, species and strains store a primary key in object_id.
  const opaqueTargets = [
    { action: 'update', description: 'Alert resolved: Temperature too high', object_type: 'alert', object_id: '123' },
    { action: 'creation', description: 'Species created: Aurelia aurita', object_type: 'species', object_id: '42' },
    { action: 'creation', description: 'Strain created: AAU', object_type: 'strain', object_id: '7' },
    { action: 'update', description: 'Unknown event', object_type: 'widget', object_id: 'opaque-target' },
  ];
  for (const entry of opaqueTargets) {
    assert.equal(audit.getAuditTargetLabel(entry), '');
    assert.equal(
      audit.getPersonalResourceLabel({ type: entry.object_type, identifier: entry.object_id, label: entry.object_id }),
      '',
    );
  }

  // A box code, a zone name and a probe code stay readable secondary targets.
  const safeTargets = [
    { object_type: 'box', object_id: 'ATL-AAU-1.001' },
    { object_type: 'thermal_zone', object_id: 'Cabinet 15 C' },
    { object_type: 'probe', object_id: 'SONDE-15-01' },
  ];
  for (const target of safeTargets) {
    assert.equal(
      audit.getAuditTargetLabel({ action: 'update', description: 'Event', ...target }),
      target.object_id,
    );
  }

  // Defence in depth: an opaque id is refused even for an allowlisted type.
  assert.equal(
    audit.getAuditTargetLabel({ action: 'update', description: 'Box moved', object_type: 'box', object_id: '550e8400-e29b-41d4-a716-446655440000' }),
    '',
  );
  assert.equal(
    audit.getAuditTargetLabel({ action: 'update', description: 'Box moved', object_type: 'box', object_id: '12345' }),
    '',
  );

  // The inventory initialization writer stores the organization primary key, so
  // it is not a readable target type. Its description carries the context.
  assert.equal(
    audit.getAuditTargetLabel({
      action: 'import',
      description: 'Historical box inventory initialized for Aquarium de Paris',
      object_type: 'box_inventory_initialization',
      object_id: '3',
    }),
    '',
  );
  assert.equal(
    audit.getPersonalResourceLabel({ type: 'box_inventory_initialization', identifier: '3', label: '3' }),
    '',
  );

  const presentationSource = readSource('../src/utils/auditPresentation.ts');
  const allowlistStart = presentationSource.indexOf('const READABLE_TARGET_OBJECT_TYPES');
  const allowlistSource = presentationSource.slice(
    allowlistStart,
    presentationSource.indexOf(']);', allowlistStart),
  );
  assert.equal(allowlistSource.includes('box_inventory_initialization'), false);
  for (const readableType of ['box', 'measurements', 'organization', 'probe', 'thermal_zone']) {
    assert.equal(allowlistSource.includes(`'${readableType}'`), true);
  }

  // The safe summary keeps its business message, so nothing useful is lost.
  assert.equal(
    audit.getAuditDescriptionLabel({ action: 'update', description: 'Alert resolved: Temperature too high' }, tFr),
    'Alerte résolue : Temperature too high',
  );
  assert.equal(
    audit.getAuditDescriptionLabel({ action: 'creation', description: 'Species created: Aurelia aurita' }, tFr).includes('Aurelia aurita'),
    true,
  );
  assert.equal(
    audit.getPersonalResourceLabel({ type: 'alert', identifier: '123', label: '123' }),
    '',
  );

  // No component renders a placeholder target, and the box preview stays in use.
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  for (const source of [adminSource, profileSource]) {
    assert.doesNotMatch(source, /targetLabel \|\| '-'/);
    assert.match(source, /<BoxTrackingPreview/);
  }
});

test('action and object type labels are translated', () => {
  assert.equal(audit.getAuditActionLabel({ action: 'entry' }, tFr), 'Nouvelle donnée enregistrée');
  assert.equal(audit.getAuditActionLabel({ action: 'update' }, tEn), 'Change recorded');
  assert.equal(audit.getAuditActionLabel({ action: 'unknown', action_label: 'Custom' }, tFr), 'Custom');
  assert.equal(audit.getAuditObjectTypeLabel('account', tFr), 'Compte');
  assert.equal(audit.getAuditObjectTypeLabel('box', tEn), 'Box');
  assert.equal(audit.getAuditObjectTypeLabel('', tFr), '-');
});

test('corrected measurement changes render inline without a duplicate disclosure', () => {
  const details = personalActions.getPersonalActionDetails({
    type: 'measurement',
    values: {
      date: '2026-09-15',
      polypes: 0,
      ephyrules: 6,
      salinite_psu: '35.0',
      notes: 'Real note',
    },
    changes: { polypes: { before: 4, after: 0 } },
  });

  assert.equal(details.values, null);
  assert.equal(details.changes, null);
  assert.equal(personalActions.hasPersonalActionDetails({ type: 'unknown' }), false);
  assert.equal(personalActions.hasPersonalActionDetails(undefined), false);
  assert.equal(personalActions.hasPersonalActionDetails({ type: 'measurement', values: {} }), false);
  assert.equal(personalActions.hasPersonalActionDetails({ type: 'measurement', values: { polypes: 0 } }), false);
  assert.equal(
    personalActions.hasPersonalActionDetails({
      type: 'measurement',
      changes: { polypes: { before: 4, after: 0 } },
    }),
    false,
  );
});

test('recorded measurement date and inline values do not create a disclosure', () => {
  const details = audit.getAuditBusinessDetailContent({
    type: 'measurement',
    values: {
      date: '2026-09-15',
      polypes: 0,
      ephyrules: 3,
      salinite_psu: '35.0',
      notes: 'Shown separately',
      source: 'web_app',
      measurement_id: 42,
    },
  });

  assert.equal(
    JSON.stringify(details),
    JSON.stringify({ values: null, changes: null }),
  );
});

test('real normalized notes render without empty filler text', () => {
  assert.equal(
    audit.getAuditBusinessNote({ type: 'measurement', values: { notes: '  Healthy culture  ' } }),
    'Healthy culture',
  );
  assert.equal(audit.getAuditBusinessNote({ type: 'box_movement', note: '  Routine move  ' }), 'Routine move');
  assert.equal(audit.getAuditBusinessNote({ type: 'box_status', stop_reason: '  End of culture  ' }), 'End of culture');
  assert.equal(audit.getAuditBusinessNote({ type: 'measurement', values: { notes: '   ' } }), '');
  assert.equal(audit.getAuditBusinessNote({ type: 'box_movement', note: '' }), '');

  const timelineSource = readSource('../src/components/AuditTimeline.tsx');
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  assert.match(timelineSource, /return note \? <p className="audit-business-note">\{note\}<\/p> : null;/);
  assert.match(profileSource, /<AuditBusinessNote details=\{entry\.business_details\} \/>/);
  assert.match(adminSource, /<AuditBusinessNote details=\{entry\.business_details\} \/>/);
});

test('a first location assignment keeps its destination without inventing an origin', () => {
  const items = audit.getAuditInlineBusinessItems(
    { type: 'box_movement', to_zone: 'Nursery B' },
    tFr,
  );
  assert.equal(JSON.stringify(items), JSON.stringify([{
    key: 'movement', label: 'Emplacement', value: 'Nursery B',
  }]));
});

test('salinity rendering preserves zero, hides null, and never duplicates PSU', () => {
  const normal = audit.getAuditInlineBusinessItems(
    { type: 'measurement', values: { salinite_psu: '35.00 PSU' } },
    tFr,
  );
  const zero = audit.getAuditInlineBusinessItems(
    { type: 'measurement', values: { salinite_psu: 0 } },
    tFr,
  );
  const missing = audit.getAuditInlineBusinessItems(
    { type: 'measurement', values: { salinite_psu: null } },
    tFr,
  );
  const correction = audit.getAuditInlineBusinessItems(
    { type: 'measurement', changes: { salinite_psu: { before: null, after: '0 PSU' } } },
    tFr,
  );

  assert.equal(normal[0].label, 'Salinité (PSU)');
  assert.equal(normal[0].value, '35.00');
  assert.equal(normal[0].unit, undefined);
  assert.equal(zero[0].value, '0');
  assert.equal(JSON.stringify(missing), JSON.stringify([]));
  assert.equal(correction[0].before, '-');
  assert.equal(correction[0].after, '0');
  assert.equal(JSON.stringify([...normal, ...zero, ...correction]).includes('- PSU'), false);
});

test('movement summaries render the real transition inline without duplicate details', () => {
  const entry = {
    action: 'update',
    description: 'Box moved to legacy-description-zone',
    business_details: {
      type: 'box_movement',
      from_zone: 'Nursery A',
      to_zone: 'Cabinet B',
      moved_at: '2026-09-15',
      note: 'Routine move',
    },
  };

  assert.equal(audit.getAuditBusinessSummary(entry, tFr), 'Boîte déplacée vers Cabinet B');
  assert.equal(audit.getAuditBusinessSummary(entry, tEn), 'Box moved to Cabinet B');
  assert.equal(
    JSON.stringify(audit.getAuditBusinessDetailContent(entry.business_details)),
    JSON.stringify({ values: null, changes: null }),
  );
});

test('manual temperature details keep the business date and hide repeated changed values', () => {
  const temperature = audit.getAuditBusinessDetailContent({
    type: 'environment',
    values: { date: '2026-09-14', temperature_c: '12.5' },
  });
  assert.equal(
    JSON.stringify(temperature),
    JSON.stringify({ values: { date: '2026-09-14', temperature_c: '12.5' }, changes: null }),
  );

  const account = audit.getAuditBusinessDetailContent({
    type: 'account',
    values: { role: 'viewer', acces_actif: true, is_responsable: false },
    changes: { acces_actif: { before: false, after: true } },
  });
  assert.equal(
    JSON.stringify(account),
    JSON.stringify({
      values: { role: 'viewer', is_responsable: false },
      changes: { acces_actif: { before: false, after: true } },
    }),
  );
  assert.equal(audit.getAuditMetadataKeyLabel('active', tFr), 'Actif');
});

test('safe alert, species, and strain targets remain in final summaries without opaque ids', () => {
  const cases = [
    ['Alert resolved: Température haute', 'Alerte résolue : Température haute', 'Alert resolved: Température haute'],
    ['Species created: Aurelia aurita', 'Espèce créée : Aurelia aurita', 'Species created: Aurelia aurita'],
    ['Species updated: Aurelia aurita', 'Espèce modifiée : Aurelia aurita', 'Species updated: Aurelia aurita'],
    ['Strain created: 1-ATL', 'Souche créée : 1-ATL', 'Strain created: 1-ATL'],
    ['Strain updated: 1-ATL', 'Souche modifiée : 1-ATL', 'Strain updated: 1-ATL'],
  ];
  for (const [description, frLabel, enLabel] of cases) {
    const entry = { action: 'update', description, object_id: '123' };
    assert.equal(audit.getAuditBusinessSummary(entry, tFr), frLabel);
    assert.equal(audit.getAuditBusinessSummary(entry, tEn), enLabel);
    assert.equal(audit.getAuditBusinessSummary(entry, tFr).includes('123'), false);
  }
});

test('box status details hide transition history while preserving the stop reason note', () => {
  const details = {
    type: 'box_status',
    transition: { from: 'active', to: 'inactive' },
    stop_reason: 'Culture completed',
    stop_reason_missing_from_history: true,
    deactivated_on: '2026-09-15',
  };

  assert.equal(
    JSON.stringify(audit.getAuditBusinessDetailContent(details)),
    JSON.stringify({ values: null, changes: null }),
  );
  assert.equal(audit.getAuditBusinessNote(details), 'Culture completed');
  assert.equal(audit.hasAuditBusinessDetails(details), false);
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

test('all eight audit families use the central FR/EN presentation registry', () => {
  const expected = [
    ['measurements', 'Mesures', 'Measurements'],
    ['transfers', 'Transferts', 'Transfers'],
    ['subcultures', 'Repiquages', 'Subcultures'],
    ['boxes', 'Boîtes', 'Boxes'],
    ['exports', 'Exports', 'Exports'],
    ['environment', 'Emplacements & sondes', 'Locations & probes'],
    ['accounts', 'Utilisateurs & accès', 'Users & access'],
    ['references', 'Référentiels', 'References'],
  ];

  assert.equal(JSON.stringify(audit.AUDIT_FAMILIES), JSON.stringify(expected.map(([family]) => family)));
  assert.equal(JSON.stringify(Object.keys(audit.AUDIT_FAMILY_PRESENTATION)), JSON.stringify(audit.AUDIT_FAMILIES));
  for (const [family, frLabel, enLabel] of expected) {
    assert.equal(audit.getAuditFamilyLabel(family, tFr), frLabel);
    assert.equal(audit.getAuditFamilyLabel(family, tEn), enLabel);
    assert.equal(audit.AUDIT_FAMILY_PRESENTATION[family].icon, null);
  }
});

test('event family fallback uses the canonical plural taxonomy', () => {
  const cases = [
    [{ family: 'measurements', action: 'custom', description: '' }, 'measurements'],
    [{ action: 'transfer', object_type: 'box', description: '' }, 'transfers'],
    [{ action: 'subculture', object_type: 'box', description: '' }, 'subcultures'],
    [{ action: 'export', object_type: 'measurements', description: '' }, 'exports'],
    [{ action: 'update', object_type: 'account', description: '' }, 'accounts'],
    [{ action: 'update', object_type: 'probe', description: '' }, 'environment'],
    [{ action: 'creation', object_type: 'organization', description: '' }, 'references'],
    [{ action: 'custom', object_type: 'mystery', description: 'Unknown action' }, 'boxes'],
  ];

  for (const [entry, family] of cases) assert.equal(audit.getAuditEventFamily(entry), family);
});

test('field ordering prioritizes biological measurements without dropping unknown keys', () => {
  const ordered = audit.orderAuditFieldEntries([
    ['source', 'web_app'],
    ['notes', 'Long note'],
    ['salinite_psu', '35'],
    ['ephyrules', 0],
    ['polypes', 4],
    ['date', '2026-09-15'],
  ]);
  assert.equal(
    JSON.stringify(ordered.map(([key]) => key)),
    JSON.stringify(['polypes', 'ephyrules', 'salinite_psu', 'date', 'notes', 'source']),
  );
});

test('structured changes support French and English shapes and missing sides', () => {
  assert.equal(JSON.stringify(audit.getAuditValueChange({ avant: 0, apres: 0 })), JSON.stringify({ before: 0, after: 0 }));
  assert.equal(JSON.stringify(audit.getAuditValueChange({ before: 4, after: 0 })), JSON.stringify({ before: 4, after: 0 }));
  assert.equal(audit.getAuditValueChange({ value: 4 }), null);
  assert.equal(audit.formatAuditChange({ before: null, after: 0 }, tEn), '- -> 0');
  assert.equal(audit.formatAuditChange({ before: '', after: undefined }, tFr), '- -> -');
});

test('business metadata formatting keeps scalars readable and hides unknown structures', () => {
  assert.equal(audit.formatAuditMetadataValue([0, 4, 'active'], tEn), '0, 4, Active');
  assert.equal(audit.formatAuditMetadataValue({ before: 4, after: 0 }, tEn), '4 -> 0');
  assert.equal(audit.formatAuditMetadataValue({ transition: { from: 'active', to: 'inactive' } }, tEn), 'Information unavailable');
  assert.equal(audit.formatAuditMetadataValue('internal_0123456789abcdef', tEn), '-');
});

test('linked action popover displays the authorized actor supplied by the payload', () => {
  const source = readSource('../src/components/AuditLinkedActionsPopover.tsx');
  const popoverCss = readSource('../src/styles/components/popovers.css');
  assert.match(source, /getAccountDisplayLabel\(linkedEntry\.user_display\)/);
  assert.match(source, /auditLinkedActionAuthor/);
  assert.equal(tFr('auditLinkedActionAuthor'), 'par {name}');
  assert.equal(tEn('auditLinkedActionAuthor'), 'by {name}');
  // The author is secondary metadata, never louder than the action summary.
  assert.match(popoverCss, /\.audit-linked-action-author \{[^}]*color: var\(--color-muted\);[^}]*\}/s);
  assert.match(popoverCss, /\.audit-linked-action-author \{[^}]*font-size: \.7rem;[^}]*\}/s);
});

test('demo measurement actions are translated without mutating existing audit rows', () => {
  const source = readSource('../../backend/apps/cultures/management/commands/seed_demo_data.py');
  assert.match(source, /AuditLog\.objects\.get_or_create/);
  assert.doesNotMatch(source, /AuditLog\.objects\.update_or_create/);
  assert.match(source, /"description": "Biological measurement recorded"/);
  assert.doesNotMatch(source, /"description": "Demo biological measurement entry\."/);
  assert.equal(
    audit.getAuditDescriptionLabel(
      { action: 'entry', description: 'Demo biological measurement entry.' },
      tFr,
    ),
    'Relevé biologique enregistré',
  );
});

test('new timeline labels exist in both languages', () => {
  const expected = {
    auditPrevious: ['Avant', 'Previous'],
    auditNew: ['Après', 'New'],
    auditValueUnavailable: ['Information non disponible', 'Information unavailable'],
  };
  for (const [key, [frLabel, enLabel]] of Object.entries(expected)) {
    assert.equal(tFr(key), frLabel);
    assert.equal(tEn(key), enLabel);
  }
});

test('day grouping uses the caller-selected timestamp', () => {
  const entries = [
    { id: 1, created_at: '2026-09-14T23:30:00Z', effective_at: '2026-09-15T10:00:00Z' },
    { id: 2, created_at: '2026-09-15T11:00:00Z', effective_at: '2026-09-15T11:00:00Z' },
  ];
  const profileGroups = audit.groupAuditEntriesByDay(entries, (entry) => entry.created_at);
  const adminGroups = audit.groupAuditEntriesByDay(entries, (entry) => entry.effective_at);
  assert.equal(profileGroups.flatMap((group) => group.entries).length, 2);
  assert.equal(adminGroups.flatMap((group) => group.entries).length, 2);
  assert.equal(adminGroups[0].entries[0].id, 1);
  assert.equal(adminGroups[0].entries[0].effective_at, '2026-09-15T10:00:00Z');
});

test('administration family and date reloads invalidate pagination state', () => {
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  const invalidationSource = sectionSource.slice(
    sectionSource.indexOf('function invalidateAuditRequests'),
    sectionSource.indexOf('useEffect(() =>'),
  );

  assert.match(invalidationSource, /requestGeneration\.current \+= 1/);
  assert.match(invalidationSource, /setIsLoadingMore\(false\)/);
  assert.match(invalidationSource, /setExpandedEntryId\(null\)/);
  // One declaration plus the family and date filter call sites.
  assert.equal((sectionSource.match(/invalidateAuditRequests\(\)/g) ?? []).length, 3);
  assert.match(sectionSource, /\[activeOrganizationId, dateFilter, familyFilter\]/);
  assert.match(sectionSource, /if \(!isActive \|\| requestGeneration\.current !== generation\) return;/);
  assert.match(sectionSource, /if \(requestGeneration\.current !== generation\) return;/);
});

test('administration pagination failure preserves entries, filters, total, and retry offset', () => {
  const loaded = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(7), {
    kind: 'initial-page',
    organizationId: 7,
    page: { results: [{ id: 3 }, { id: 2 }], has_more: true, next_offset: 40, total_count: 47 },
  });
  const failed = adminAudit.applyAdminAuditOutcome(loaded, {
    kind: 'load-more-error',
    organizationId: 7,
    message: 'Network error',
  });
  assert.equal(JSON.stringify(failed.entries.map((entry) => entry.id)), JSON.stringify([3, 2]));
  assert.equal(failed.nextOffset, 40);
  assert.equal(failed.totalCount, 47);
  assert.equal(failed.loadMoreError, 'Network error');
  assert.equal(
    adminAudit.buildAdminAuditQuery({
      family: 'measurements',
      date: '2026-09-15',
      includeOptions: false,
      limit: 40,
      offset: failed.nextOffset,
    }),
    'limit=40&offset=40&family=measurements&date=2026-09-15',
  );
});

test('administration retry merges safely while preserving the filtered total', () => {
  const loaded = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(7), {
    kind: 'initial-page',
    organizationId: 7,
    page: { results: [{ id: 3 }, { id: 2 }], has_more: true, next_offset: 40, total_count: 47 },
  });
  const failed = adminAudit.applyAdminAuditOutcome(loaded, {
    kind: 'load-more-error',
    organizationId: 7,
    message: 'Network error',
  });
  const retried = adminAudit.applyAdminAuditOutcome(failed, {
    kind: 'load-more-page',
    organizationId: 7,
    page: { results: [{ id: 2 }, { id: 1 }], has_more: false, next_offset: null },
  });
  assert.equal(JSON.stringify(retried.entries.map((entry) => entry.id)), JSON.stringify([3, 2, 1]));
  assert.equal(retried.loadMoreError, null);
  assert.equal(retried.nextOffset, null);
  assert.equal(retried.totalCount, 47);
});

test('administration queries preserve family, date, pagination, and empty-filter semantics', () => {
  assert.equal(
    adminAudit.buildAdminAuditQuery({
      family: 'transfers',
      date: '2026-09-15',
      includeOptions: true,
      includeTotal: true,
      limit: 40,
      offset: 80,
    }),
    'limit=40&offset=80&include_options=1&include_total=1&family=transfers&date=2026-09-15',
  );
  assert.equal(
    adminAudit.buildAdminAuditQuery({ family: '', date: '', includeOptions: true, limit: 40, offset: 0 }),
    'limit=40&offset=0&include_options=1',
  );

  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  assert.match(sectionSource, /family: familyFilter,/);
  assert.match(sectionSource, /date: dateFilter,/);
  assert.match(sectionSource, /includeOptions: true,\s*includeTotal: true,/);
  assert.match(sectionSource, /includeOptions: false,\s*includeTotal: false,/);
  assert.match(sectionSource, /offset: requestedOffset,/);
});

test('administration count uses the exact filtered backend total', () => {
  const unfiltered = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(7), {
    kind: 'initial-page',
    organizationId: 7,
    page: { results: [{ id: 3 }, { id: 2 }], has_more: true, next_offset: 40, total_count: 10 },
  });
  const measurements = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(7), {
    kind: 'initial-page',
    organizationId: 7,
    page: { results: [{ id: 3 }], has_more: false, next_offset: null, total_count: 6 },
  });
  const unavailable = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(7), {
    kind: 'initial-page',
    organizationId: 7,
    page: { results: [{ id: 3 }], has_more: true, next_offset: 40 },
  });

  assert.equal(unfiltered.totalCount, 10);
  assert.equal(measurements.totalCount, 6);
  assert.equal(unavailable.totalCount, null);
});

test('normalized business detail forms are rendered without raw metadata fallback', () => {
  const cases = [
    [
      { type: 'measurement', values: { polypes: 0 }, changes: { ephyrules: { before: 2, after: 0 } } },
      { values: null, changes: null },
    ],
    [{ type: 'box', values: { notes: 'Stable' } }, { values: null, changes: null }],
    [{ type: 'environment', changes: { temperature_c: { before: 12, after: 0 } } }, { values: null, changes: { temperature_c: { before: 12, after: 0 } } }],
    [{ type: 'account', values: { role: 'viewer' } }, { values: { role: 'viewer' }, changes: null }],
    [{ type: 'reference', values: { nom: 'Aurelia' } }, { values: { nom: 'Aurelia' }, changes: null }],
    [
      { type: 'transfer_out', destination_organization: 'Ocean Lab', date: '2026-09-15', polyp_count: 0, note: '' },
      { values: null, changes: null },
    ],
    [
      { type: 'transfer_import', source_global_code: 'ATL-AAU-1.001', source_organization: 'Aquarium' },
      { values: null, changes: null },
    ],
    [
      { type: 'box_movement', from_zone: 'A', to_zone: 'B', moved_at: '2026-09-15', note: 'Routine' },
      { values: null, changes: null },
    ],
    [
      {
        type: 'box_status',
        transition: { from: 'active', to: 'inactive' },
        stop_reason: 'End',
        stop_reason_missing_from_history: true,
        deactivated_on: '2026-09-15',
      },
      { values: null, changes: null },
    ],
    [{ type: 'box_inventory_initialization', box_count: 0, target_status: 'active' }, { values: { box_count: 0, statut: 'active' }, changes: null }],
    [
      { type: 'export', box_count: 0, measurement_count: 0, week_count: 0, filters: { include_other_zones: false } },
      { values: { box_count: 0, measurement_count: 0, week_count: 0, include_other_zones: false }, changes: null },
    ],
  ];

  for (const [details, expected] of cases) {
    assert.equal(JSON.stringify(audit.getAuditBusinessDetailContent(details)), JSON.stringify(expected));
  }
  for (const details of [null, undefined, { type: 'subculture' }, { type: 'unknown' }, { type: 'future' }]) {
    assert.equal(
      JSON.stringify(audit.getAuditBusinessDetailContent(details)),
      JSON.stringify({ values: null, changes: null }),
    );
  }

  assert.equal(adminAudit.hasAdminAuditBusinessDetails({ business_details: { type: 'unknown' }, edited_at: null }), false);
  assert.equal(adminAudit.hasAdminAuditBusinessDetails({ business_details: { type: 'measurement', values: { polypes: 0 } }, edited_at: null }), false);
  assert.equal(adminAudit.hasAdminAuditBusinessDetails({ business_details: { type: 'measurement', values: { date: '2026-09-15', polypes: 0 } }, edited_at: null }), false);
  assert.equal(adminAudit.hasAdminAuditBusinessDetails({ business_details: { type: 'unknown' }, edited_at: '2026-09-15T12:00:00Z' }), true);
});

test('organization switches clear administration rows and ignore late responses', () => {
  const organizationOne = adminAudit.applyAdminAuditOutcome(adminAudit.createAdminAuditState(1), {
    kind: 'initial-page',
    organizationId: 1,
    page: { results: [{ id: 10 }], has_more: false, next_offset: null },
  });
  assert.equal(organizationOne.entries.length, 1);

  const organizationTwo = adminAudit.createAdminAuditState(2);
  assert.equal(organizationTwo.entries.length, 0);
  const afterLateResponse = adminAudit.applyAdminAuditOutcome(organizationTwo, {
    kind: 'initial-page',
    organizationId: 1,
    page: { results: [{ id: 11 }], has_more: false, next_offset: null },
  });
  assert.equal(afterLateResponse, organizationTwo);
  assert.equal(afterLateResponse.organizationId, 2);
  assert.equal(afterLateResponse.entries.length, 0);
});

test('family options render eight choices in laboratory-first presentation order', () => {
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  const orderSource = sectionSource.slice(
    sectionSource.indexOf('const ADMIN_AUDIT_FAMILY_ORDER'),
    sectionSource.indexOf('export default function AdminAuditSection'),
  );
  const expectedOrder = [
    'measurements',
    'boxes',
    'subcultures',
    'transfers',
    'environment',
    'exports',
    'accounts',
  ];

  // The references family keeps its backend key and its audit rows, but it has
  // no filter pill until the product decision is taken again.

  assert.match(sectionSource, /family_options\?: AdminAuditFamilyOption\[\]/);
  assert.match(sectionSource, /setFamilyOptions\(response\.family_options \?\? \[\]\)/);
  assert.match(sectionSource, /const counts = new Map\(familyOptions\?\.map/);
  assert.match(sectionSource, /const allCount = familyOptions\?\.reduce/);
  assert.equal(JSON.stringify([...orderSource.matchAll(/'([^']+)'/g)].map((match) => match[1])), JSON.stringify(expectedOrder));
  assert.match(sectionSource, /onSelect=\{\(\) => selectFamily\(''\)\}/);
  assert.match(sectionSource, /\{ADMIN_AUDIT_FAMILY_ORDER\.map\(\(family\) => \(/);
  assert.match(sectionSource, /count=\{counts\.get\(family\)\}/);
  assert.match(sectionSource, /label=\{getAuditFamilyLabel\(family, t\)\}/);
  assert.match(sectionSource, /aria-pressed=\{isSelected\}/);
});

test('administration header stacks the date filter below the family filters', () => {
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  const inventorySource = readSource('../src/components/BoxInventoryAdminSection.tsx');
  const administrationCss = readSource('../src/styles/pages/administration.css');
  const frSource = readSource('../src/i18n/fr.ts');
  const enSource = readSource('../src/i18n/en.ts');
  const headingStart = sectionSource.indexOf('<header className="box-inventory-heading">');
  const headingEnd = sectionSource.indexOf('</header>', headingStart);
  const headingSource = sectionSource.slice(headingStart, headingEnd);
  const filterBarStart = sectionSource.indexOf('<div className="admin-audit-filter-bar">');
  const filterBarEnd = sectionSource.indexOf('<div className="admin-audit-page-body">', filterBarStart);
  const filterBarSource = sectionSource.slice(filterBarStart, filterBarEnd);
  const familyFiltersStart = sectionSource.indexOf('<div className="admin-audit-family-filters"');
  const dateFilterStart = sectionSource.indexOf('<label className="admin-audit-date-filter">');


  assert.match(inventorySource, /<header className="box-inventory-heading">/);
  assert.equal(headingStart >= 0, true);
  assert.doesNotMatch(headingSource, /admin-audit-date-filter|admin-audit-result-count|<p>/);
  assert.doesNotMatch(sectionSource, /admin-audit-result-count|adminAuditCount|admin-audit-filter-meta/);
  assert.equal(filterBarStart > headingEnd, true);
  assert.equal(familyFiltersStart > filterBarStart && familyFiltersStart < filterBarEnd, true);
  assert.equal(dateFilterStart > familyFiltersStart && dateFilterStart < filterBarEnd, true);
  assert.match(sectionSource, /<span className="sr-only">\{t\('adminAuditFilterDate'\)\}<\/span>/);
  assert.doesNotMatch(filterBarSource, /state\.totalCount|state\.entries\.length/);
  assert.match(administrationCss, /\.admin-audit-filter-bar \{[^}]*display: grid;/s);
  assert.match(administrationCss, /\.admin-audit-family-filters \{[^}]*display: flex;[^}]*flex-wrap: wrap;/s);
  assert.match(administrationCss, /\.admin-audit-date-filter \{[^}]*width: max-content;/s);
  assert.match(administrationCss, /\.admin-audit-date-filter input \{[^}]*width: 164px;[^}]*min-height: 36px;/s);
  assert.doesNotMatch(administrationCss, /\.admin-audit-filter-meta/);
  assert.doesNotMatch(administrationCss, /\.admin-audit-result-count/);
  assert.match(administrationCss, /\.admin-audit-family-filters button \{[^}]*min-height: 36px;[^}]*padding: 0 var\(--space-3\);[^}]*font-size: .76rem;/s);
  assert.match(administrationCss, /\.admin-audit-family-filters small \{[^}]*font-size: .66rem;/s);
  assert.match(sectionSource, /onSelect=\{\(\) => selectFamily\(''\)\}/);
  assert.match(sectionSource, /setDateFilter\(event\.target\.value\)/);
  for (const source of [sectionSource, administrationCss, frSource, enSource]) {
    assert.equal(source.includes('adminAuditText'), false);
    assert.equal(source.includes('adminAuditClearFilters'), false);
    assert.equal(source.includes('admin-audit-clear'), false);
    assert.equal(source.includes('admin-audit-controls'), false);
  }
});

test('family filter pills are ergonomic controls with uniform hit areas', () => {
  const css = readSource('../src/styles/pages/administration.css');
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  const filtersStart = css.indexOf('.admin-audit-family-filters {');
  const filtersCss = css.slice(filtersStart, css.indexOf('.admin-audit-page-body {'));
  const buttonCss = filtersCss.slice(
    filtersCss.indexOf('.admin-audit-family-filters button {'),
    filtersCss.indexOf('.admin-audit-family-filters button:is('),
  );

  // Uniform hit area that matches the other Administration controls.
  assert.match(buttonCss, /min-height: 36px;/);
  assert.match(buttonCss, /padding: 0 var\(--space-3\);/);
  assert.match(buttonCss, /cursor: pointer;/);
  assert.match(buttonCss, /user-select: none;/);

  // A label or count never wraps inside a pill, so every pill keeps one height.
  assert.match(buttonCss, /white-space: nowrap;/);
  assert.match(filtersCss, /\.admin-audit-family-filters small \{[^}]*white-space: nowrap;/s);

  // Hover, press and selected states stay distinct.
  assert.match(filtersCss, /\.admin-audit-family-filters button:is\(:hover, :focus-visible\) \{[^}]*background: var\(--color-surface-subtle\);/s);
  assert.match(filtersCss, /\.admin-audit-family-filters button:active \{[^}]*background: var\(--color-surface-info\);/s);
  assert.match(filtersCss, /\.admin-audit-family-filters button\[aria-pressed='true'\] \{[^}]*background: var\(--color-primary-faint\);/s);
  assert.match(
    filtersCss,
    /\.admin-audit-family-filters button\[aria-pressed='true'\]:is\(:hover, :focus-visible\) \{[^}]*background: var\(--color-primary-soft\);/s,
  );

  // Keyboard and screen-reader affordances stay intact.
  assert.match(filtersCss, /\.admin-audit-family-filters button:focus-visible,[^}]*outline: 2px solid var\(--color-primary\);/s);
  assert.match(sectionSource, /role="group" aria-label=\{t\('adminAuditFilterFamily'\)\}/);
  assert.match(sectionSource, /aria-pressed=\{isSelected\}/);
});

test('history headings omit filler text and action counts while using the application body font', () => {
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  const adminCss = readSource('../src/styles/pages/administration.css');
  const profileCss = readSource('../src/styles/pages/profile.css');
  const timelineCss = readSource('../src/styles/components/audit-timeline.css');
  const frSource = readSource('../src/i18n/fr.ts');
  const enSource = readSource('../src/i18n/en.ts');

  assert.doesNotMatch(adminSource, /adminAuditCount|admin-audit-result-count/);
  assert.doesNotMatch(profileSource, /profileActionsText|profileActionsCount|profile-actions-count/);
  assert.match(adminCss, /\.admin-audit-stream \{[^}]*font-family: var\(--font-body\);/s);
  assert.match(profileCss, /\.profile-actions-stream \{[^}]*font-family: var\(--font-body\);/s);
  assert.match(timelineCss, /\.audit-day-heading \{[^}]*font-family: var\(--font-body\);/s);
  for (const source of [frSource, enSource]) {
    assert.doesNotMatch(source, /adminAuditCount|profileActionsText|profileActionsCount/);
  }
});

test('journal identities reuse the Team and Inventory name and box-code treatment', () => {
  const adminCss = readSource('../src/styles/pages/administration.css');
  const timelineCss = readSource('../src/styles/components/audit-timeline.css');
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');

  const memberNameCss = adminCss.slice(
    adminCss.indexOf('.member-identity strong {'),
    adminCss.indexOf('.member-identity small {'),
  );
  const authorCss = adminCss.slice(
    adminCss.indexOf('.admin-audit-author {'),
    adminCss.indexOf('.admin-audit-main {'),
  );
  const boxCodeCss = adminCss.slice(
    adminCss.indexOf('.box-inventory-identity a {'),
    adminCss.indexOf('.box-inventory-cell a {'),
  );
  const auditBoxLinkStart = timelineCss.indexOf(':is(.profile-action-summary');
  const auditBoxLinkCss = timelineCss.slice(
    auditBoxLinkStart,
    timelineCss.indexOf(':is(.profile-action-summary', auditBoxLinkStart + 1),
  );

  // The author is a real emphasis element, like the member name column.
  assert.match(sectionSource, /<strong className="admin-audit-author">/);
  assert.match(authorCss, /color: var\(--color-ink\);/);
  assert.match(authorCss, /font-size: .82rem;/);
  assert.match(memberNameCss, /color: var\(--color-ink\);/);
  assert.match(memberNameCss, /font-size: .82rem;/);

  // The box code keeps the Inventory identity link signature.
  assert.match(boxCodeCss, /color: var\(--color-primary-hover\);/);
  assert.match(boxCodeCss, /font-style: italic;/);
  assert.match(boxCodeCss, /font-weight: 800;/);
  assert.match(auditBoxLinkCss, /color: var\(--color-primary-hover\);/);
  assert.match(auditBoxLinkCss, /font-style: italic;/);
  assert.match(auditBoxLinkCss, /font-weight: 800;/);
  assert.match(auditBoxLinkCss, /text-decoration-line: none;/);
});

test('action titles and inline values reuse the Polypbase display typography', () => {
  const adminCss = readSource('../src/styles/pages/administration.css');
  const profileCss = readSource('../src/styles/pages/profile.css');
  const timelineCss = readSource('../src/styles/components/audit-timeline.css');
  const timelineSource = readSource('../src/components/AuditTimeline.tsx');

  assert.match(adminCss, /\.admin-audit-summary \{[^}]*font-family: var\(--font-display\);/s);
  assert.match(profileCss, /\.profile-action-summary \{[^}]*font-family: var\(--font-display\);/s);
  assert.match(timelineCss, /\.audit-inline-business-summary \{[^}]*font-family: var\(--font-display\);[^}]*font-variant-numeric: tabular-nums;/s);

  // Quiet labels, readable values: the Inventory count pattern.
  assert.match(timelineCss, /\.audit-inline-label \{ color: var\(--color-muted\); \}/);
  assert.match(timelineCss, /\.audit-inline-value \{ color: var\(--color-ink\); font-weight: 600; \}/);
  assert.match(timelineSource, /className="audit-inline-label"/);
  assert.match(timelineSource, /className="audit-inline-value"/);

  // The box code keeps its own identity treatment and is never wrapped in the
  // inline value styling.
  assert.doesNotMatch(timelineSource, /audit-inline-value[^>]*box-tracking-preview/);
});

test('administration rows use distinct hour, author, action, and controls columns', () => {
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');
  const cssSource = readSource('../src/styles/pages/administration.css');
  const rowStart = sectionSource.indexOf('<div className="admin-audit-row">');
  const rowEnd = sectionSource.indexOf('{hasDetails && isExpanded', rowStart);
  const rowSource = sectionSource.slice(rowStart, rowEnd);

  const timeStart = rowSource.indexOf('<time className="admin-audit-time"');
  const authorStart = rowSource.indexOf('<strong className="admin-audit-author">');
  const actionStart = rowSource.indexOf('<div className="admin-audit-main">');
  const controlsStart = rowSource.indexOf('<div className="admin-audit-row-actions">');
  assert.equal(timeStart >= 0, true);
  assert.equal(authorStart > timeStart, true);
  assert.equal(actionStart > authorStart, true);
  assert.equal(controlsStart > actionStart, true);
  assert.doesNotMatch(rowSource, /admin-audit-anchor/);
  assert.match(cssSource, /\.admin-audit-row \{[^}]*grid-template-columns: 56px minmax\(120px, 160px\) minmax\(0, 1fr\) auto;/s);
});

test('administration audit is extracted and remounts for each organization', () => {
  const adminViewSource = readSource('../src/components/AdminView.tsx');
  const sectionSource = readSource('../src/components/AdminAuditSection.tsx');

  assert.match(adminViewSource, /import AdminAuditSection from '\.\/AdminAuditSection';/);
  assert.equal(adminViewSource.includes('function AdminAuditLogSection'), false);
  assert.equal(adminViewSource.includes('function AdminAuditRow'), false);
  assert.match(
    adminViewSource,
    /<AdminAuditSection\s+activeOrganizationId=\{activeOrganizationId\}\s+key=\{activeOrganizationId\}/,
  );
  assert.match(sectionSource, /setState\(createAdminAuditState\(activeOrganizationId\)\)/);
  assert.match(sectionSource, /requestGeneration\.current = generation/);
  assert.match(sectionSource, /setExpandedEntryId\(null\)/);
  assert.match(sectionSource, /setIsLoadingMore\(false\)/);
});

test('timeline changes expose accessible before and after labels', () => {
  const timelineSource = readSource('../src/components/AuditTimeline.tsx');

  assert.match(timelineSource, /<span className="sr-only">\{t\('auditPrevious'\)\}: <\/span>/);
  assert.match(timelineSource, /<span className="sr-only">\{t\('auditNew'\)\}: <\/span>/);
  assert.match(timelineSource, /className="audit-change-arrow" aria-hidden="true"/);
  assert.match(timelineSource, /aria-controls=\{controls\}/);
  assert.match(timelineSource, /aria-expanded=\{isExpanded\}/);
  assert.match(timelineSource, /aria-label=\{isExpanded \? t\('profileActionsHideDetails'\) : t\('profileActionsDetails'\)\}/);
});

test('Profile and Admin use typed box references with a plain safe fallback', () => {
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');

  for (const [source, label] of [
    [profileSource, /\{targetLabel\}/],
    [adminSource, /\{targetLabel\}/],
  ]) {
    assert.match(source, /entry\.box_reference \? \(/);
    assert.match(source, /boxId=\{entry\.box_reference\.id\}/);
    assert.match(source, /code=\{entry\.box_reference\.global_code\}/);
    assert.match(source, /speciesName=\{entry\.box_reference\.species_scientific_name\}/);
    assert.match(source, label);
    // A target line only exists for the typed box reference or a safe label.
    assert.doesNotMatch(source, /\|\| '-'\}<\/span>\}/);
    assert.equal(/metadata(?:\?\.|\.)box_id/.test(source), false);

    const rowSource = source.slice(source.indexOf('function ' + (source === profileSource ? 'ProfileActionRow' : 'AdminAuditRow')));
    assert.equal(rowSource.indexOf('<BoxTrackingPreview') < rowSource.indexOf('<AuditDisclosureButton'), true);
    assert.doesNotMatch(rowSource, /<AuditDisclosureButton[\s\S]*?<BoxTrackingPreview/);
  }
});

test('Profile and Admin share inline summaries without duplicating resolved box targets', () => {
  const timelineSource = readSource('../src/components/AuditTimeline.tsx');
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');

  assert.match(timelineSource, /export function AuditPrimarySummary/);
  assert.match(timelineSource, /getAuditBoxSummaryParts\(entry, t\)/);
  assert.match(timelineSource, /<BoxTrackingPreview/);
  assert.match(timelineSource, /export function AuditInlineBusinessSummary/);
  for (const source of [profileSource, adminSource]) {
    assert.match(source, /<AuditPrimarySummary/);
    assert.match(source, /<AuditInlineBusinessSummary details=\{entry\.business_details\}/);
    assert.match(source, /hasInlineBoxSummary \|\| hasSubcultureSummary \? null/);
    assert.match(source, /hidePrimaryResource=\{hasInlineBoxSummary \|\| hasSubcultureSummary\}/);
    // Masking the parent is conditional on the rich child+parent summary.
    assert.match(source, /const hasSubcultureSummary = hasAuditSubcultureSummary\(entry\.business_details\);/);
  }
});

test('disclosures remain only for normalized details not already rendered inline', () => {
  assert.equal(audit.hasAuditBusinessDetails({ type: 'measurement', changes: { polypes: { before: 0, after: 5 } } }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'measurement', values: { date: '2026-09-15', polypes: 0 } }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'box_movement', from_zone: 'A', to_zone: 'B' }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'box_status', transition: { from: 'active', to: 'inactive' }, stop_reason: 'End' }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'transfer_import', source_organization: 'Ocean Lab' }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'transfer_out', destination_organization: 'Ocean Lab', date: '2026-09-15' }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'box', values: { date: '2026-09-15' } }), false);
  assert.equal(audit.hasAuditBusinessDetails({ type: 'account', changes: { role: { before: 'viewer', after: 'admin' } } }), true);
});

test('collapsed context keeps only useful subculture and transfer relations', () => {
  const timelineSource = readSource('../src/components/AuditTimeline.tsx');

  assert.match(timelineSource, /if \(context\.subculture\)/);
  assert.match(timelineSource, /if \(context\.transfer\)/);
  assert.equal(timelineSource.includes('context.movement'), false);
  assert.equal(timelineSource.includes('context.measurement'), false);
  assert.equal(timelineSource.includes('auditRelationMeasurementCorrection'), false);
  assert.equal(timelineSource.includes('description'), false);
  assert.equal(timelineSource.includes('deactivated'), false);
  assert.equal(timelineSource.includes('reactivated'), false);
});

test('details use normalized business data and never expose raw metadata', () => {
  const sources = [
    readSource('../src/components/ProfileActionsSection.tsx'),
    readSource('../src/components/AdminAuditSection.tsx'),
    readSource('../src/components/AuditTimeline.tsx'),
    readSource('../src/components/AuditLinkedActionsPopover.tsx'),
    readSource('../src/utils/adminAudit.ts'),
    readSource('../src/utils/personalActions.ts'),
  ];
  const presentationSource = readSource('../src/utils/auditPresentation.ts');

  for (const source of sources) {
    assert.equal(source.includes('entry.metadata'), false);
    assert.equal(source.includes('details.technical'), false);
    assert.equal(source.includes('AuditTechnicalDetails'), false);
    assert.equal(/metadata(?:\?\.|\.)box_id/.test(source), false);
  }
  assert.match(sources[0], /hasPersonalActionDetails\(entry\.business_details\)/);
  assert.match(sources[0], /<AuditBusinessDetail details=\{entry\.business_details\}/);
  assert.match(sources[1], /<AuditBusinessDetail details=\{entry\.business_details\}/);
  assert.doesNotMatch(presentationSource, /entry\.metadata\?\.valeurs/);
});

test('audit measurement actions reuse RowActionMenu and remain capability-driven', () => {
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  const actionsSource = readSource('../src/components/AuditLinkedActionsPopover.tsx');
  const rowMenuSource = readSource('../src/components/RowActionMenu.tsx');

  assert.match(adminSource, /<AuditLinkedActionsPopover/);
  assert.match(actionsSource, /<RowActionMenu/);
  assert.match(actionsSource, /if \(entry\.editable_measurement\)/);
  assert.match(actionsSource, /onEditMeasurement\(entry\.editable_measurement\)/);
  assert.match(actionsSource, /if \(entry\.related_action_count > 0\)/);
  assert.match(actionsSource, /action: 'view-linked-actions'/);
  assert.match(actionsSource, /if \(!actions\.length\) return null/);
  assert.match(actionsSource, /triggerRef=\{linkedAnchorRef\}/);
  assert.match(rowMenuSource, /close\(true\);\s*onAction\(item\.action\)/);
  assert.equal(actionsSource.includes('auditOpenBox'), false);
  assert.equal(actionsSource.includes('Voir la boîte'), false);
  assert.equal(adminSource.includes('admin-audit-correct-button'), false);
  assert.equal(adminSource.includes('onDelete'), false);
  assert.equal(adminSource.includes('apiDelete'), false);
});

test('linked measurement popup is lazy, anchored, retryable, and chronological', () => {
  const source = readSource('../src/components/AuditLinkedActionsPopover.tsx');
  const hookSource = readSource('../src/hooks/useAnchoredPopover.ts');

  assert.match(source, /useAnchoredPopover<HTMLButtonElement>\(isLinkedOpen, closeLinked, 'end'\)/);
  assert.match(source, /role="dialog"/);
  assert.match(source, /apiGet<LinkedAuditResponse>\(`\/api\/accounts\/audit-log\/\$\{entryId\}\/linked\/`/);
  assert.match(source, /if \(!controller\.signal\.aborted\) setEntries\(response\.results\)/);
  assert.match(source, /setRetry\(\(current\) => current \+ 1\)/);
  assert.match(source, /<time dateTime=\{linkedEntry\.effective_at\}>/);
  assert.match(source, /<AuditInlineBusinessSummary details=\{linkedEntry\.business_details\}/);
  assert.match(source, /<AuditBusinessNote details=\{linkedEntry\.business_details\}/);
  assert.match(source, /linkedAnchorRef\.current\?\.focus\(\{ preventScroll: true \}\)/);
  assert.match(hookSource, /event\.key !== 'Escape'/);
  assert.match(hookSource, /document\.dispatchEvent\(new CustomEvent\(OPEN_EVENT/);
});

test('audit presentation avoids technical UI, rigid rails, and final family icons', () => {
  const profileSource = readSource('../src/components/ProfileActionsSection.tsx');
  const adminSource = readSource('../src/components/AdminAuditSection.tsx');
  const timelineSource = readSource('../src/components/AuditTimeline.tsx');
  const linkedSource = readSource('../src/components/AuditLinkedActionsPopover.tsx');
  const presentationSource = readSource('../src/utils/auditPresentation.ts');
  const frSource = readSource('../src/i18n/fr.ts');
  const enSource = readSource('../src/i18n/en.ts');
  const adminCss = readSource('../src/styles/pages/administration.css');
  const timelineCss = readSource('../src/styles/components/audit-timeline.css');
  const middleDot = String.fromCodePoint(0xb7);

  for (const source of [profileSource, adminSource, timelineSource, linkedSource, presentationSource, frSource, enSource]) {
    assert.equal(source.includes(middleDot), false);
    assert.equal(source.includes('auditDetailTechnical'), false);
  }
  assert.equal(adminSource.includes('PolypbaseIcon'), false);
  assert.equal(adminSource.includes('FAMILY_ICONS'), false);
  assert.equal(adminCss.includes('.admin-audit-grid'), false);
  assert.equal(adminCss.includes('.admin-audit-rail'), false);
  assert.equal(adminCss.includes('.admin-audit-table'), false);
  assert.equal(adminCss.includes('.admin-audit-timeline'), false);
  const familyFilterCss = adminCss.slice(
    adminCss.indexOf('.admin-audit-family-filters {'),
    adminCss.indexOf('.admin-audit-family-filters button'),
  );
  assert.match(familyFilterCss, /display: flex;/);
  assert.match(familyFilterCss, /flex-wrap: wrap;/);
  assert.match(timelineCss, /\.audit-detail-region/);
  for (const family of audit.AUDIT_FAMILIES) {
    assert.equal(audit.AUDIT_FAMILY_PRESENTATION[family].icon, null);
  }
});
