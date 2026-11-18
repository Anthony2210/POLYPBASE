import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { moduleLoader, read } from './sharing-test-harness.mjs';

const load = moduleLoader();
const DemoModeBanner = load('src/components/DemoModeBanner.tsx').default;
const { createTranslator } = load('src/i18n/index.ts');

test('demo indicator is absent in ordinary mode and renders in FR and EN', () => {
  for (const [language, expected] of [['fr', 'Mode démonstration'], ['en', 'Demonstration mode']]) {
    const t = createTranslator(language);
    const render = enabled => renderToStaticMarkup(React.createElement(DemoModeBanner, { enabled, t }));
    assert.equal(render(false), '');
    assert.equal(render(true), `<p class="demo-mode-banner">${expected}</p>`);
  }
});

test('only the explicit server marker enables the persistent indicator', () => {
  const app = read('src/App.tsx');
  assert.match(app, /\[demoMode, setDemoMode\] = useState\(false\)/);
  assert.match(app, /setDemoMode\(session\.demo_mode === true\)/);
  assert.match(app, /\{demoBanner\}[\s\S]*?<PasswordResetPage/);
  assert.match(app, /\{demoBanner\}<LoginPage/);
  assert.match(app, /\{demoBanner\}[\s\S]*?<OrganizationChoiceScreen/);
  assert.match(app, /<section className="workspace">\s*\{demoBanner\}/);
  assert.match(read('src/styles/layout.css'), /\.demo-mode-banner\s*\{\s*position: sticky;/);
});
