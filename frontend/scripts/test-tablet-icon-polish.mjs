import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ArrowLeft } from 'lucide-react';
import ts from 'typescript';

const require = createRequire(import.meta.url);
const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const iconSource = read('../src/components/PolypbaseIcon.tsx');
const zonesSource = read('../src/components/ZonesView.tsx');
const buttonsCss = read('../src/styles/components/buttons.css');
const zonesCss = read('../src/styles/pages/zones.css');
const backSource = read('../src/components/DetailBackButton.tsx');

function evaluate(source, context) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  });
  vm.runInNewContext(outputText, context);
}

const iconExports = {};
evaluate(iconSource, { React, exports: iconExports, require });
const PolypbaseIcon = iconExports.default;

function renderIcon(name, props = {}) {
  return renderToStaticMarkup(React.createElement(PolypbaseIcon, { name, ...props }));
}

for (const name of ['close', 'plus', 'minus', 'search', 'edit', 'info', 'chevron-left', 'settings']) {
  test(`generic ${name} icon opts into shared UI stroke styling`, () => {
    assert.match(renderIcon(name), /class="polypbase-icon polypbase-icon--ui"/);
  });
}
for (const name of ['location', 'box-alt', 'box-location-alt', 'qr-scan', 'label-qr', 'probe', 'culture-box', 'thermal-zone', 'inactive-alt']) {
  test(`domain or QR ${name} icon retains its stroke`, () => {
    const markup = renderIcon(name);
    assert.doesNotMatch(markup, /polypbase-icon--ui/);
    assert.match(markup, /stroke-width="2"/);
  });
}

test('icon sizing, custom strokes, class names and accessibility remain intact', () => {
  const markup = renderIcon('edit', { size: 23, strokeWidth: 1.8, className: 'custom-icon', 'aria-label': 'Edit' });
  assert.match(markup, /height="23"/);
  assert.match(markup, /width="23"/);
  assert.match(markup, /stroke-width="1.8"/);
  assert.match(markup, /class="polypbase-icon polypbase-icon--ui custom-icon"/);
  assert.match(markup, /aria-label="Edit"/);
  assert.doesNotMatch(markup, /aria-hidden/);
  assert.match(renderIcon('close'), /aria-hidden="true"/);
});

test('shared lightening targets only UI icon roots in the inverse desktop media query', () => {
  const query = read('../src/hooks/useIsDesktopApp.ts').match(/DESKTOP_APP_QUERY = '([^']+)'/)[1];
  assert.ok(buttonsCss.includes(`@media not all and ${query} {`));
  const rule = buttonsCss.match(/@media not all[^\{]+\{\s*(:is\([^\)]+\))\s*\{([^}]+)\}/);
  assert.ok(rule);
  assert.equal(rule[1], ':is(.lucide, .polypbase-icon--ui, .phone-nav-icon > .polypbase-icon, .tab-icon-slot > .polypbase-icon, .tablet-qr-action > .polypbase-icon)');
  assert.equal(rule[2].trim(), 'stroke-width: 1.25;');
  assert.match(renderToStaticMarkup(React.createElement(ArrowLeft)), /class="lucide lucide-arrow-left"/);
  assert.equal((buttonsCss.match(/stroke-width:/g) ?? []).length, 1);
});

const zonesAst = ts.createSourceFile('ZonesView.tsx', zonesSource, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
const detailNode = zonesAst.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === 'ZoneDetailPage');

function detailTree(isDesktop, zone, onBack) {
  const backExports = {};
  evaluate(backSource, {
    React, exports: backExports,
    require(name) {
      if (name === '../hooks/useIsDesktopApp') return { useIsDesktopApp: () => isDesktop };
      return require(name);
    },
  });
  const DetailBackButton = backExports.default;
  const exports = {};
  evaluate(detailNode.getText(zonesAst), {
    exports, React, DetailBackButton,
    PageLoader: 'loader', TemperatureControlPanel: 'temperature-panel', ZoneFunctionalSections: 'zone-sections',
  });
  return { tree: exports.ZoneDetailPage({ boxes: [], zone, isLoading: false, onBack, t: key => `translated:${key}` }), DetailBackButton };
}

for (const zone of [null, { id: 1, name: 'Zone', organization: { name: 'Lab' } }]) {
  for (const desktop of [false, true]) {
    test(`zone detail ${zone ? 'loaded' : 'missing'} keeps ${desktop ? 'desktop text' : 'tablet arrow'} navigation accessible`, () => {
      let calls = 0;
      const { tree, DetailBackButton } = detailTree(desktop, zone, () => { calls += 1; });
      const back = React.Children.toArray(tree.props.children)[0];
      assert.equal(back.type, DetailBackButton);
      assert.equal(back.props.label, 'translated:back');
      const button = DetailBackButton(back.props);
      assert.equal(button.props.onClick, back.props.onBack);
      assert.equal(button.type, 'button');
      assert.equal(button.props.type, 'button');
      button.props.onClick();
      assert.equal(calls, 1);
      if (desktop) {
        assert.equal(button.props.className, 'text-button detail-back-button zone-back-button');
        assert.equal(button.props.children, 'translated:back');
      } else {
        assert.equal(button.props.className, 'icon-button detail-back-button detail-back-button--icon');
        assert.equal(button.props['aria-label'], 'translated:back');
        assert.equal(button.props.title, 'translated:back');
        assert.equal(button.props.children.type, ArrowLeft);
        assert.equal(button.props.children.props.size, 20);
        assert.equal(button.props.children.props['aria-hidden'], 'true');
      }
    });
  }
}

test('zone and directory back controls use shared circular geometry and preserve contextual labels', () => {
  const sharedRule = buttonsCss.match(/\.detail-back-button--icon \{([^}]+)\}/)?.[1];
  assert.ok(sharedRule);
  assert.match(sharedRule, /width: 44px;/);
  assert.match(sharedRule, /min-width: 44px;/);
  assert.match(sharedRule, /height: 44px;/);
  assert.match(sharedRule, /border-radius: 50%;/);
  assert.doesNotMatch(zonesCss, /\.zone-page > \.zone-back-action/);
  const directory = zonesSource.slice(zonesSource.indexOf('export function ZoneBoxesPage'));
  assert.equal((directory.match(/<DetailBackButton label=\{t\('back'\)\} onBack=\{onBack\} desktopClassName="zone-back-button"/g) ?? []).length, 2);
  assert.match(buttonsCss, /\.icon-button,[\s\S]*?place-items: center;/);
});
