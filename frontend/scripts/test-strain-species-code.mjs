import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function source(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

class ApiError extends Error {
  constructor(status, data) {
    super('Backend error');
    this.status = status;
    this.data = data;
  }
}

const exports = {};
vm.runInNewContext(ts.transpileModule(source('../src/utils/strainSpeciesCode.ts'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText, { exports, require: () => ({ ApiError }) });
const { hasSpeciesCode, isMissingStrainSpeciesCode } = exports;

test('only scoped AAA assignments determine creation eligibility, not the shared species code', () => {
  const species = { id: 10, genus_species_code: 'ABC' };
  assert.equal(hasSpeciesCode(null, species.id), null);
  assert.equal(hasSpeciesCode([{ id: 2, species: 11, code: 'ABC' }], species.id), false);
  assert.equal(hasSpeciesCode([{ id: 3, species: 10, code: 'DEF' }], species.id), true);
  assert.equal(hasSpeciesCode([], species.id), false);
});

test('only the backend missing-AAA species validation triggers the localized recovery path', () => {
  assert.equal(isMissingStrainSpeciesCode(new ApiError(400, {
    species: ['Assign an AAA code to this species in the active institution in Administration before creating a strain.'],
  })), true);
  assert.equal(isMissingStrainSpeciesCode(new ApiError(400, { species: ['Invalid species.'] })), false);
  assert.equal(isMissingStrainSpeciesCode(new ApiError(403, { species: ['Assign an AAA code'] })), false);
});

test('both normal strain forms use scoped AAA, preserve normal POST payloads and recover from backend errors', () => {
  for (const path of ['../src/components/QuickStrainCreator.tsx', '../src/components/TaxonomyAdminSection.tsx']) {
    const component = source(path);
    assert.match(component, /apiGet<SpeciesCodeAssignment\[]>\('\/api\/taxonomy\/species-codes\/'\)/);
    assert.match(component, /speciesHasCode === false/);
    assert.match(component, /isMissingStrainSpeciesCode\(requestError\)/);
    assert.match(component, /apiPost<StrainReference>\('\/api\/taxonomy\/strains\/', payload\)/);
    const payload = component.match(/const payload: StrainReferencePayload = \{([\s\S]*?)\n\s*\};/);
    assert.ok(payload);
    assert.doesNotMatch(payload[1], /organization|assignment|genus_species_code/);
    assert.match(payload[1], /species: speciesId/);
    assert.doesNotMatch(component, /setCode\(''\)|setStrainCode\(''\)/);
  }
});

test('organization switching remounts code availability and shared-reference navigation uses existing route', () => {
  const app = source('../src/App.tsx');
  const admin = source('../src/components/AdminView.tsx');
  const taxonomy = source('../src/components/TaxonomyAdminSection.tsx');
  assert.match(app, /key=\{activeOrganizationId \?\? 'none'\}/);
  assert.match(admin, /key=\{activeOrganizationId\}/);
  assert.match(app, /openAdminSection\('references'\)/);
  assert.match(app, /references: '\/administration\/reference-data'/);
  assert.match(taxonomy, /setCodeForm\(selected\)/);
});
