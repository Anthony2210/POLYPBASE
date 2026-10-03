import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CirclePause, CirclePlay, GitFork, Route } from 'lucide-react';
import ts from 'typescript';


const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const source = read('../src/App.tsx');
const ast = ts.createSourceFile('App.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
let boxPage, header;
function visit(node) {
  if (ts.isFunctionDeclaration(node) && node.name?.text === 'BoxPage') boxPage = node;
  if (ts.isJsxElement(node) && node.openingElement.tagName.getText(ast) === 'header'
    && node.getText(ast).includes('box-sheet-hero')) header = node;
  ts.forEachChild(node, visit);
}
visit(ast);
assert.ok(boxPage);
assert.ok(header);
const body = boxPage.getText(ast);
const actions = body.slice(body.indexOf('type BoxAction'), body.indexOf('async function saveMeasurement'));
function evaluate(code, context) {
  vm.runInNewContext(ts.transpileModule(code, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.React },
  }).outputText, context);
}
function catalogue(language) {
  const context = { exports: {} };
  evaluate(read(`../src/i18n/${language}.ts`), context);
  return context.exports[language];
}
const catalogues = { fr: catalogue('fr'), en: catalogue('en') };

function render({ desktop = false, phone = false, active = true, busy = false, write = true, status = true, language = 'en' } = {}) {
  const contextSignal = new AbortController().signal;
  const context = {
    React, useState: React.useState, useEffect: React.useEffect,
    getOrganizationResourceSignal: () => contextSignal,
    apiGetResource: () => { throw new Error('Server rendering must not fetch QR resources'); },
    CirclePause, CirclePlay, GitFork, Route,
    isDesktopApp: desktop, isPhoneLayout: phone, isTabletLayout: !desktop && !phone,
    canWriteLabData: write, canShowStatusButton: status, isBoxActive: active, isChangingBoxStatus: busy,
    box: { global_code: 'LONG-BOX-CODE-123456789', species: { scientific_name: 'Aurelia aurita' }, thermal_zone: null },
    qr: { imageUrl: '/api/boxes/7/qr.svg', scanUrl: '/bac/7' },
    statusPresentation: { tone: active ? 'active' : 'inactive' }, displayDate: { labelKey: 'created', date: null }, currentZone: null,
    t: key => catalogues[language][key] ?? key,
    formatSalinity: value => value ?? '-', formatTemperature: value => value ?? '-', formatDisplayDate: value => value,
    buildQrLabelItem: (box, imageUrl) => ({ globalCode: box.global_code, qrImageUrl: imageUrl }),
    InfoPill: ({ label, value }) => React.createElement('div', null, label, value),
    PolypbaseIcon: ({ name }) => React.createElement('svg', { 'data-icon': name }),
    RowActionMenu: props => {
      context.menu = props;
      return React.createElement('button', { className: 'row-action-menu-trigger', 'aria-label': props.ariaLabel });
    },
  };
  const qrAst = ts.createSourceFile('QrLabel.tsx', read('../src/components/QrLabel.tsx'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const qrFunction = qrAst.statements.find(node => ts.isFunctionDeclaration(node));
  evaluate(qrFunction.getText(qrAst).replace('export default ', ''), context);
  evaluate(`${actions}\n globalThis.renderHeader = () => (${header.getText(ast)});`, context);
  return { markup: renderToStaticMarkup(React.createElement(context.renderHeader)), context };
}

for (const language of ['fr', 'en']) {
  for (const active of [true, false]) {
    test(`React tablet render binds the final action glyphs and ${language} accessible labels (${active ? 'active' : 'inactive'})`, () => {
      const { markup } = render({ language, active });
      assert.match(markup, /class="[^"]*is-tablet/);
      assert.match(markup, /box-hero-qr[\s\S]*qr-label--trigger[\s\S]*box-tablet-actions/);
      assert.match(markup, /loading="eager"/);
      // QrLabel must resolve its resource in the organization context, not expose
      // the API URL as a direct, unauthenticated image source during render.
      assert.doesNotMatch(markup, /src="\/api\/boxes/);
      assert.match(markup, /<svg[^>]*width="36"[^>]*height="36"[^>]*class="lucide lucide-route"[^>]*aria-hidden="true"/);
      assert.match(markup, /<svg[^>]*width="36"[^>]*height="36"[^>]*class="lucide lucide-git-fork box-subculture-glyph"[^>]*aria-hidden="true"/);
      assert.match(markup, new RegExp(`<svg[^>]*width="36"[^>]*height="36"[^>]*class="lucide ${active ? 'lucide-circle-pause' : 'lucide-circle-play'}"[^>]*aria-hidden="true"`));
      assert.doesNotMatch(markup, /lucide-share2|lucide-arrow-right|box-move-glyph|lucide-git-branch|lucide-map-pin-house|row-action-menu-trigger/);
      for (const key of ['moveAction', 'subcultureAction', active ? 'boxArchiveAction' : 'boxActivateAction']) {
        assert.ok(markup.includes(`aria-label="${catalogues[language][key]}"`));
      }
      assert.ok(markup.includes('LONG-BOX-CODE-123456789'));
    });
  }
}

test('React phone render has one contextual trigger and no inline QR or tablet buttons', () => {
  const { markup, context } = render({ phone: true, busy: true });
  assert.match(markup, /class="[^"]*is-phone/);
  assert.equal((markup.match(/row-action-menu-trigger/g) ?? []).length, 1);
  assert.doesNotMatch(markup, /box-hero-qr|box-tablet-actions|box-action-stack/);
  assert.deepEqual(Array.from(context.menu.actions, item => item.action), ['qr', 'move', 'subculture', 'tracking']);
  assert.equal(context.menu.actions.at(-1).disabled, true);
});

test('React desktop render retains labeled actions and pending tracking without compact UI', () => {
  const { markup } = render({ desktop: true, busy: true });
  assert.match(markup, /box-hero-qr/);
  assert.match(markup, /class="move-trigger"[^>]*>Move<\/button>/);
  assert.match(markup, /class="subculture-trigger"/);
  assert.match(markup, /class="archive-box-trigger"[^>]*disabled=""/);
  assert.doesNotMatch(markup, /box-tablet-actions|row-action-menu-trigger|box-move-glyph/);
});

test('React read-only render exposes no QR or mutation controls when both capabilities are absent', () => {
  for (const layout of [{}, { phone: true }, { desktop: true }]) {
    const { markup } = render({ ...layout, write: false, status: false });
    assert.doesNotMatch(markup, /box-hero-qr|box-compact-action|row-action-menu-trigger|move-trigger|subculture-trigger|archive-box-trigger/);
  }
});

test('Box tablet actions match the 64px QR surface, retain the 54px image and rotated fork, and allow identity wrapping', () => {
  const tablet = read('../src/styles/responsive/tablet.css');
  const phone = read('../src/styles/responsive/phone.css');
  const qrCss = read('../src/styles/components/qr-label.css');
  const compact = tablet.match(/\.is-tablet \.box-compact-action \{([^}]+)\}/)?.[1];
  assert.ok(compact);
  for (const property of ['width', 'min-width', 'height', 'min-height']) assert.match(compact, new RegExp(`${property}: 64px`));
  assert.match(compact, /border-radius: var\(--radius-md\)/);
  const tabletQrWidth = Number(tablet.match(/\.is-tablet \.box-hero-qr \{ width: (\d+)px/)?.[1]);
  const desktopQrWidth = Number(qrCss.match(/\.qr-label--trigger \{[^}]*width: (\d+)px/)?.[1]);
  assert.equal(tabletQrWidth, 64);
  assert.ok(tabletQrWidth < desktopQrWidth);
  assert.equal(Number(compact.match(/(?:^|\s)width: (\d+)px/)?.[1]), tabletQrWidth);
  assert.match(source, /import \{[^}]*\bRoute\b[^}]*\} from 'lucide-react'/);
  assert.match(tablet, /--qr-label-image-size: 54px/);
  assert.match(tablet, /--qr-label-padding: 4px/);
  assert.match(tablet, /\.is-tablet \.box-subculture-glyph \{ transform: rotate\(90deg\); \}/);
  assert.match(tablet, /@container box-detail \(width < 680px\)[\s\S]*"identity" "tools" "summary"/);
  assert.match(tablet, /\.is-tablet \.box-code-line h2 \{ overflow-wrap: anywhere; white-space: normal; \}/);
  assert.match(phone, /\.is-phone \.box-header-tools \{ align-self: start; justify-self: end; \}/);
});
