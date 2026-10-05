import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const origin = 'https://polypbase.test';
const contextSignal = new AbortController().signal;
const client = {
  getOrganizationResourceSignal: () => contextSignal,
  apiGetResource() { throw new Error('Preview render tests must not request resources'); },
};

function loadModule(path, imports, globals = {}) {
  const code = ts.transpileModule(read(path), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const exports = {};
  vm.runInNewContext(code, {
    exports,
    require(name) {
      assert.ok(name in imports, `Unexpected import: ${name}`);
      return imports[name];
    },
    ...globals,
  });
  return exports;
}

const qrLabels = loadModule('utils/qrLabels.ts', { '../api/client': client }, { URL, window: { location: { origin } } });
const QrLabel = loadModule('components/QrLabel.tsx', { react: React, 'react/jsx-runtime': jsxRuntime, '../api/client': client }).default;
const QrLabelModal = loadModule('components/QrLabelModal.tsx', {
  // SSR cannot run layout effects; focus and preparation are covered by test-qr-preparation.mjs.
  react: { ...React, useLayoutEffect: React.useEffect },
  'react/jsx-runtime': jsxRuntime,
  '../utils/qrLabels': qrLabels,
  './QrLabel': { default: QrLabel },
  './ModalPortal': { default: ({ children }) => children },
  './PolypbaseIcon': { default: () => null },
  './box-utility-dialogs.css': {},
}).default;

const box = {
  id: 17,
  global_code: 'ATL-AAU-1.001',
  species: { scientific_name: 'Aurelia aurita' },
  thermal_zone: { name: 'Zone 15' },
};
const labels = {
  title: 'QR label', close: 'Close', download: 'Download', print: 'Print', qrCode: 'QR code',
  addToSelection: 'Add', alreadySelected: 'Already selected', selectionCount: 'Selected', viewSelection: 'View selection',
  qrLabelPreparing: 'Preparing', qrLabelPopupBlocked: 'Popup blocked', qrLabelQrUnavailable: 'QR unavailable',
  qrLabelResourceUnavailable: 'Resource unavailable', qrLabelImagePreparationFailed: 'Image unavailable',
  qrLabelPreparationFailed: 'Preparation failed', qrLabelRetry: 'Retry',
};
const qrImageUrl = '/boites/17/qr.svg';
const item = qrLabels.buildQrLabelItem(box, qrImageUrl);
const settings = qrLabels.DEFAULT_QR_LABEL_PRINT_SETTINGS;

function renderModal(labelOverrides = {}, selectedLabels = []) {
  return renderToStaticMarkup(React.createElement(QrLabelModal, {
    box, qrImageUrl, selectedLabels,
    labels: { ...labels, ...labelOverrides },
    onAddToSelection() {}, onClose() {}, onViewSelection() {},
  }));
}

for (const [language, title, help] of [
  ['fr', 'Étiquette QR', 'Étiquette prête à imprimer et coller sur la boîte.'],
  ['en', 'QR label', 'Label ready to print and attach to the box.'],
]) {
  test(`real modal omits retired ${language} help while retaining an accessible title`, () => {
    const html = renderModal({ title, help });
    assert.equal(html.includes(help), false);
    assert.doesNotMatch(html, /aria-describedby=/);
    const dialog = html.match(/<section\b[^>]*role="dialog"[^>]*>/)?.[0];
    assert.ok(dialog);
    assert.match(dialog, /aria-modal="true"/);
    const titleId = dialog.match(/aria-labelledby="([^"]+)"/)?.[1];
    assert.ok(titleId);
    assert.ok(html.includes(`<h2 id="${titleId}">${title}</h2>`));
    assert.match(html, /<button[^>]*aria-label="Close"/);
  });
}

test('real modal accepts callers that no longer supply the retired help prop', () => {
  const html = renderModal();
  assert.match(html, /role="dialog"/);
  assert.doesNotMatch(html, /aria-describedby=|undefined/);
});

test('real modal shows one identity and a metadata-free upright QR, with physical output isolated', () => {
  const html = renderModal();
  const scan = html.match(/<div class="utility-qr-scan">([\s\S]*?)<\/div>/)?.[1];
  assert.ok(scan);
  assert.match(scan, /class="qr-label qr-label--preview qr-label--image-only"/);
  assert.doesNotMatch(scan, /qr-label__metadata|<strong>|<small>|print-sheet/);
  assert.match(scan, /<img class="qr-label__image" alt="QR code ATL-AAU-1\.001" decoding="async" loading="lazy"\/>/);
  const visible = html.replace(/<div class="utility-qr-physical" aria-hidden="true">[\s\S]*?<\/div>/, '');
  assert.equal((visible.match(/<strong>ATL-AAU-1\.001<\/strong>/g) ?? []).length, 1);
  assert.equal((visible.match(/<small>Aurelia aurita<\/small>/g) ?? []).length, 1);
  assert.match(html, /class="utility-qr-physical" aria-hidden="true"><span class="qr-label qr-label--label qr-label-print-sheet"/);
  assert.doesNotMatch(html, /qr-label-modal-preview|qr-label-print-frame|--label-preview-/);
  assert.match(html, /role="status"/);
});

test('real modal retains selection availability and independent download and print controls', () => {
  for (const selected of [[], [item]]) {
    const html = renderModal({}, selected);
    const buttons = [...html.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)];
    const find = (text) => {
      const button = buttons.find((match) => match[2].replace(/<[^>]+>/g, '') === text);
      assert.ok(button, `Missing ${text} button`);
      return button[1];
    };
    assert.equal(/disabled=/.test(find(selected.length ? 'Already selected' : 'Add')), selected.length > 0);
    assert.equal(/disabled=/.test(find('View selection')), selected.length === 0);
    assert.doesNotMatch(find('Download'), /disabled=/);
    assert.doesNotMatch(find('Print'), /disabled=/);
    assert.match(html, new RegExp(`<strong>${selected.length}</strong><span>Selected</span>`));
  }
});

function mediaBlock(source, header) {
  const start = source.indexOf(header);
  assert.notEqual(start, -1, `Missing ${header}`);
  const opening = source.indexOf('{', start);
  let depth = 1;
  let end = opening + 1;
  for (; depth && end < source.length; end++) {
    if (source[end] === '{') depth++;
    if (source[end] === '}') depth--;
  }
  assert.equal(depth, 0);
  return { content: source.slice(opening + 1, end - 1), outside: source.slice(0, start) + source.slice(end) };
}

function rules(source) {
  return [...source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((match) => ({ selector: match[1].trim(), body: match[2] }));
}

function rule(source, selector) {
  const found = rules(source).find((entry) => entry.selector === selector);
  assert.ok(found, `Missing ${selector}`);
  return found.body;
}

const localCss = read('components/box-utility-dialogs.css');
const screen = mediaBlock(localCss, '@media screen');

test('local scan presentation is screen-only, square, unrotated and does not alter physical output', () => {
  assert.match(read('components/QrLabelModal.tsx'), /import '\.\/box-utility-dialogs\.css'/);
  assert.match(rule(screen.content, '.utility-qr-scan'), /width: min\(100%, 224px\)/);
  assert.match(rule(screen.content, '.utility-qr-scan .qr-label'), /--qr-label-image-size: 100%/);
  assert.match(rule(screen.content, '.utility-qr-scan .qr-label'), /border: 0/);
  assert.match(rule(screen.content, '.utility-qr-scan .qr-label__image'), /image-rendering: auto/);
  assert.match(rule(screen.content, '.utility-qr-physical'), /display: none/);
  assert.doesNotMatch(screen.outside, /utility-qr-/);
  assert.doesNotMatch(localCss, /rotate\(|label-preview-|qr-label-print-sheet|!important/);
  assert.match(rule(read('styles/components/qr-label.css'), '.qr-label__image'), /aspect-ratio: 1/);
});

test('scan size adapts to short landscape screens while actions stay standard and secondary', () => {
  const tablet = mediaBlock(screen.content, '@media (max-width: 1023px), (pointer: coarse), (max-height: 600px)');
  assert.match(rule(tablet.content, '.utility-qr-scan'), /width: min\(100%, 176px\)/);
  const short = mediaBlock(screen.content, '@media (max-height: 600px)');
  assert.match(rule(short.content, '.utility-qr-scan'), /width: min\(100%, 144px\)/);
  const html = renderModal();
  const footer = html.match(/<footer[^>]*>([\s\S]*?)<\/footer>/)[1];
  assert.equal((footer.match(/class="secondary-button is-secondary"/g) ?? []).length, 2);
  assert.doesNotMatch(footer, /primary-button|is-primary/);
  assert.match(rule(screen.content, '.box-dialog--qr .qr-label-selection-panel'), /flex-wrap: wrap/);
});

test('print document, downloadable SVG, and QR targets retain their canonical geometry and content', () => {
  assert.equal(settings.labelWidthMm, 41);
  assert.equal(settings.labelHeightMm, 28);
  assert.equal(settings.qrSizeMm, 25);
  assert.equal(item.qrImageUrl, '/boites/17/qr.svg?public_base_url=https%3A%2F%2Fpolypbase.test');
  assert.equal(qrLabels.getBoxScanUrl(box), `${origin}/bac/17/`);
  const payload = 'data:image/svg+xml;base64,AAAA';
  const svg = qrLabels.buildQrLabelSvg(item, payload);
  assert.match(svg, /width="41mm" height="28mm" viewBox="0 0 41 28"/);
  assert.ok(svg.includes(`href="${payload}"`));
  assert.match(svg, /width="25" height="25" transform="rotate\(-90 /);
  assert.match(svg, /class="label-text" transform="translate\([\d.]+ 14\) rotate\(-90\)"/);
  assert.match(svg, />ATL-AAU-1\.001<\/text>/);
  assert.match(svg, />Aurelia aurita<\/text>/);
  const print = qrLabels.buildQrPrintDocument([item], settings);
  assert.match(print, /@page \{ size: 41mm 28mm; margin: 0; \}/);
  assert.match(rule(print, '.label-qr img'), /width: 25mm; height: 25mm;.*transform: rotate\(-90deg\)/);
  assert.match(rule(print, '.label-text'), /rotate\(-90deg\)/);
  assert.doesNotMatch(print + svg, /qr-label-modal-preview|rotate\(90(?:deg)?\)/);
  const legacyPrint = read('styles/responsive/print.css');
  assert.match(rule(legacyPrint, '.qr-label-print-sheet'), /width: 41mm;\s*height: 28mm;/);
  assert.match(rule(legacyPrint, '.qr-label-print-sheet .qr-label__image'), /width: 25mm;\s*height: 25mm;/);
  assert.match(rule(legacyPrint, '.qr-label-print-sheet .qr-label__text'), /rotate\(-90deg\)/);
  assert.doesNotMatch(legacyPrint, /qr-label-modal-preview/);
});
