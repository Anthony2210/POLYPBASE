import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

import './test-scan-handoff.mjs';

function loadTypeScript(relativePath) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const exports = {};
  vm.runInNewContext(outputText, { exports });
  return exports;
}

const authRouting = loadTypeScript('../src/utils/authRouting.ts');
const adminViewSource = readFileSync(new URL('../src/components/AdminView.tsx', import.meta.url), 'utf8');
const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

test('recognizes login and complete password-reset paths as public authentication routes', () => {
  assert.equal(authRouting.isPublicAuthPath('/login'), true);
  assert.equal(authRouting.isPublicAuthPath('/reset-password/user-id/token-value'), true);
  assert.equal(authRouting.isPublicAuthPath('/boites/42'), false);
});

test('rejects incomplete or unrelated password-reset paths', () => {
  assert.equal(authRouting.getPasswordResetRoute('/reset-password/user-id'), null);
  assert.equal(authRouting.getPasswordResetRoute('/reset-password/user-id/token-value/extra'), null);
  assert.equal(authRouting.getPasswordResetRoute('/reset-password//token-value'), null);
});

test('only an authentication response from the profile request redirects to login', () => {
  assert.equal(authRouting.shouldRedirectToLogin(false, 401), true);
  assert.equal(authRouting.shouldRedirectToLogin(false, 403), true);
  assert.equal(authRouting.shouldRedirectToLogin(false, 500), false);
  assert.equal(authRouting.shouldRedirectToLogin(true, 401), false);
  assert.equal(authRouting.shouldRedirectToLogin(true, 403), false);
});

test('offers sign-in for an expired session after bootstrap', () => {
  assert.equal(authRouting.requiresSignInRecovery(401, null), true);
  assert.equal(authRouting.requiresSignInRecovery(403, 401), true);
  assert.equal(authRouting.requiresSignInRecovery(403, 403), true);
});

test('shared references navigation and section remain disabled', () => {
  assert.match(adminViewSource, /\{ key: 'references', panelId: 'admin-taxonomy', label: 'adminTabReferences', scope: 'shared' \}/);
  assert.match(adminViewSource, /const DISABLED_ADMIN_SECTIONS[\s\S]*?'references'[\s\S]*?\n\]\);/);
  assert.match(adminViewSource, /const isDisabled = DISABLED_ADMIN_SECTIONS\.has\(item\.key\)[\s\S]*?disabled=\{isDisabled\}/);
  assert.match(adminViewSource, /if \(DISABLED_ADMIN_SECTIONS\.has\(section\)\) return;/);
  assert.match(adminViewSource, /const displayedSection = DISABLED_ADMIN_SECTIONS\.has\(activeSection\) \? 'accounts' : activeSection/);
  assert.match(adminViewSource, /displayedSection === 'references' && activeOrganizationId != null[\s\S]*?<TaxonomyAdminSection/);
});

test('Administration permissions and desktop-only guard remain in place', () => {
  assert.match(adminViewSource, /if \(!profile \|\| !userHasAdminRole\(profile\)\) return null/);
  assert.match(appSource, /activeTab === 'admin' && !isDesktopApp/);
  assert.match(appSource, /activeTab === 'admin' && isDesktopApp/);
});

test('does not present genuine forbidden or generic failures as session expiry', () => {
  assert.equal(authRouting.requiresSignInRecovery(403, 200), false);
  assert.equal(authRouting.requiresSignInRecovery(403, 500), false);
  assert.equal(authRouting.requiresSignInRecovery(500, null), false);
  assert.equal(authRouting.requiresSignInRecovery(null, null), false);
});
