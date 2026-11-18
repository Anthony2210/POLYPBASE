import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

function source(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

// Strains are created only from an institutional Species (server-built AAA-BBB-X);
// POST /api/taxonomy/strains/ answers 410 and PATCH rejects identity changes (409).

test('no legacy surface advertises or sends direct Strain creation', () => {
  assert.equal(existsSync(new URL('../src/components/QuickStrainCreator.tsx', import.meta.url)), false);
  const taxonomy = source('../src/components/TaxonomyAdminSection.tsx');
  assert.doesNotMatch(taxonomy, /apiPost<StrainReference>/);
  assert.doesNotMatch(taxonomy, /'\/api\/taxonomy\/strains\/'/);
  assert.doesNotMatch(taxonomy, /taxonomyNewStrain|taxonomyStrainMissingAAA|isMissingStrainSpeciesCode/);
  assert.doesNotMatch(taxonomy, /kind: activeTab/);
  // The add action exists only on the Species tab; the Strains tab points to the Species workspace.
  assert.match(taxonomy, /\{activeTab === 'species' \? \(\s*<button\s+className="primary-button taxonomy-add-button"[\s\S]*?onClick=\{\(\) => setFormState\(\{ kind: 'species' \}\)\}/);
  assert.match(taxonomy, /activeTab === 'strains' \? \(\s*<p role="note">\s*\{t\('taxonomyStrainCreationMoved'\)\}/);
  assert.match(taxonomy, /onOpenSpeciesWorkspace \? \([\s\S]*?\{t\('taxonomyOpenSpeciesStrains'\)\}/);
  const admin = source('../src/components/AdminView.tsx');
  assert.match(admin, /<TaxonomyAdminSection[\s\S]*?onOpenSpeciesWorkspace=\{\(\) => selectAdminSection\('species'\)\}/);
});

test('the legacy strain form is edit-only and never sends identity fields', () => {
  const taxonomy = source('../src/components/TaxonomyAdminSection.tsx');
  const form = taxonomy.slice(taxonomy.indexOf('function StrainForm('), taxonomy.indexOf('function LocalizedFields('));
  assert.match(form, /initialStrain: StrainReference;/);
  assert.match(form, /apiPatch<StrainReference>\(`\/api\/taxonomy\/strains\/\$\{initialStrain\.id\}\/`, payload\)/);
  const payload = form.match(/const payload: StrainReferencePayload = \{([\s\S]*?)\n\s*\};/);
  assert.ok(payload);
  assert.doesNotMatch(payload[1], /\b(code|number|origin_code|organization|assignment)\b/);
  assert.match(payload[1], /species: speciesId/);
  for (const value of ['initialStrain.code', "initialStrain.number ?? ''", 'initialStrain.origin_code']) {
    assert.ok(form.includes(`<input readOnly value={${value}} />`), value);
  }
  assert.doesNotMatch(form, /setCode|setNumber|setOriginCode/);
  assert.match(form, /taxonomyStrainIdentityReadOnly/);
  const types = source('../src/types/admin.ts');
  const type = types.match(/export type StrainReferencePayload = \{([\s\S]*?)\};/);
  assert.ok(type);
  assert.doesNotMatch(type[1], /\b(code|number|origin_code)\??:/);
});

test('organization switching remounts code availability and shared-reference navigation uses existing route', () => {
  const app = source('../src/App.tsx');
  const pilotage = source('../src/components/PilotageView.tsx');
  const admin = source('../src/components/AdminView.tsx');
  const taxonomy = source('../src/components/TaxonomyAdminSection.tsx');
  assert.match(pilotage, /<CreateBoxPanel\s+key=\{`\$\{profile\?\.id \?\? 'none'\}:\$\{activeOrganizationId \?\? 'none'\}`\}/);
  assert.match(admin, /key=\{activeOrganizationId\}/);
  assert.match(app, /references: '\/administration\/reference-data'/);
  assert.match(taxonomy, /onEditCode=\{\(\) => setCodeForm\(species\)\}/);
});
