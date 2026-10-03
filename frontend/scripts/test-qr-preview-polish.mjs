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

test('real modal wraps the unchanged shared physical label and keeps its metadata and pending image', () => {
  const html = renderModal();
  assert.match(html, /class="qr-label-modal-preview"><div class="qr-label-print-frame"/);
  assert.match(html, /--label-preview-ratio:41 \/ 28/);
  assert.match(html, /--label-preview-qr-size:60\.9756cqw/);
  assert.match(html, /class="qr-label qr-label--label qr-label-print-sheet"/);
  assert.match(html, /<strong>ATL-AAU-1\.001<\/strong>/);
  assert.match(html, /<small>Aurelia aurita<\/small>/);
  assert.match(html, /<img class="qr-label__image" alt="QR code ATL-AAU-1\.001" decoding="async" loading="eager"\/>/);
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

const css = read('styles/components/qr-label.css');
const modalCss = read('styles/pages/exports-labels.css');
const screen = mediaBlock(modalCss, '@media screen');
const preview = rule(screen.content, '.qr-label-modal-preview');
const frame = rule(screen.content, '.qr-label-modal-preview > .qr-label-print-frame');

test('preview sizing and rotation are screen-only and limited to the new modal wrapper', () => {
  assert.deepEqual(rules(screen.content).map((entry) => entry.selector), [
    '.qr-label-modal-preview', '.qr-label-modal-preview > .qr-label-print-frame',
  ]);
  assert.doesNotMatch(screen.outside, /\.qr-label-modal-preview/);
  assert.match(preview, /position: relative;/);
  assert.match(preview, /justify-self: center;/);
  assert.match(preview, /width: min\(100%, 160px\);/);
  assert.match(preview, /aspect-ratio: 28 \/ 41;/);
  assert.match(frame, /position: absolute;/);
  assert.match(frame, /top: 50%;/);
  assert.match(frame, /left: 50%;/);
  assert.match(frame, /width: calc\(100% \* 41 \/ 28\);/);
  assert.match(frame, /transform: translate\(-50%, -50%\) rotate\(90deg\);/);
  assert.match(frame, /transform-origin: center;/);
  assert.match(rule(modalCss, '.qr-label-print-frame'), /container-type: inline-size;/);
  const dialogCss = read('styles/components/lab-dialogs.css');
  assert.match(rule(dialogCss, '.box-dialog'), /width: min\(100%, var\(--box-dialog-width\)\);/);
  assert.match(rule(dialogCss, '.box-dialog--qr'), /--box-dialog-width: 480px;/);
  assert.doesNotMatch(modalCss, /\.qr-label-modal\s*\{/);
  // Keep scoped frame sizing in the same layer as the generic frame: layers outrank specificity.
  const imports = read('styles/index.css');
  assert.match(imports, /exports-labels\.css' layer\(pages\)/);
  assert.doesNotMatch(css, /\.qr-label-modal-preview/);
  assert.ok(screen.content.includes('.qr-label-modal-preview > .qr-label-print-frame'));
});

test('rotated preview fits its reserved footprint, is smaller, and leaves text and QR upright', () => {
  const cap = Number(preview.match(/width: min\(100%, (\d+)px\)/)[1]);
  const [portraitWidth, portraitHeight] = preview.match(/aspect-ratio: (\d+) \/ (\d+)/).slice(1).map(Number);
  const [landscapeWidth, landscapeHeight] = frame.match(/width: calc\(100% \* (\d+) \/ (\d+)\)/).slice(1).map(Number);
  const outerRotation = Number(frame.match(/rotate\((-?\d+)deg\)/)[1]);
  for (const selector of ['.qr-label--label .qr-label__image', '.qr-label--label .qr-label__text']) {
    const innerRotation = Number(rule(css, selector).match(/rotate\((-?\d+)deg\)/)[1]);
    assert.equal(outerRotation + innerRotation, 0, `${selector} must read upright`);
  }
  assert.equal(portraitWidth / portraitHeight, landscapeHeight / landscapeWidth);
  for (const availableWidth of [100, 160, 260, 340, 680]) {
    const width = Math.min(availableWidth, cap);
    const height = width * portraitHeight / portraitWidth;
    const containerWidth = width * landscapeWidth / landscapeHeight;
    const labelHeight = containerWidth * settings.labelHeightMm / settings.labelWidthMm;
    assert.ok(Math.abs(labelHeight - width) < 0.001);
    assert.ok(Math.abs(containerWidth - height) < 0.001);
    assert.ok(width <= availableWidth);
    assert.ok(width * height < 260 * 260 * 28 / 41, 'preview area must be smaller than the old phone and desktop previews');
    if (availableWidth >= cap) {
      const variables = qrLabels.getQrLabelPreviewCssVariables(settings);
      assert.ok(parseFloat(variables['--label-preview-font-size']) * containerWidth / 100 >= 14);
      assert.ok(parseFloat(variables['--label-preview-species-font-size']) * containerWidth / 100 >= 14);
    }
  }
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
