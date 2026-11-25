import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import test from 'node:test';

function source(path) {
  return readFileSync(new URL(path, import.meta.url), 'utf8');
}

test('no legacy surface advertises or sends direct Strain creation', () => {
  assert.equal(existsSync(new URL('../src/components/QuickStrainCreator.tsx', import.meta.url)), false);
});

test('organization switching remounts the New Box form and Species workspace', () => {
  const pilotage = source('../src/components/PilotageView.tsx');
  const admin = source('../src/components/AdminView.tsx');
  assert.match(pilotage, /<CreateBoxPanel\s+key=\{`\$\{profile\?\.id \?\? 'none'\}:\$\{activeOrganizationId \?\? 'none'\}`\}/);
  assert.match(admin, /<SpeciesStrainsAdminSection\s+key=\{activeOrganizationId\}/);
});
