import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/qrLabels.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});

const origin = 'https://polypbase.test';
const exports = {};
vm.runInNewContext(outputText, {
  exports,
  window: {
    location: { origin },
    btoa: (value) => Buffer.from(value, 'binary').toString('base64'),
    open: () => null,
  },
  document: {
    body: { appendChild() {} },
    createElement: () => ({ click() {}, remove() {} }),
  },
  URL,
  Blob: class {},
  fetch: async () => {
    throw new Error('offline');
  },
  unescape,
  encodeURIComponent,
});

const settings = exports.DEFAULT_QR_LABEL_PRINT_SETTINGS;
const label = {
  id: 17,
  globalCode: 'ATL-AAU-1.001',
  speciesName: 'Aurelia aurita',
  zoneName: 'Zone 15',
  qrImageUrl: `${origin}/boites/17/qr.svg`,
};

// Box codes and species names already present in the repository.
const representativeLabels = [
  { globalCode: 'ATL-AAU-1.001', speciesName: 'Aurelia aurita' },
  { globalCode: 'AAU-1.001-ATL', speciesName: 'Aurelia coerulea' },
  { globalCode: 'AFL-TAI-1.001', speciesName: 'Aurelia labiata' },
  { globalCode: 'AHI-LAB-1.004', speciesName: 'Cassiopea andromeda' },
  { globalCode: 'CCO-2.001-PAC', speciesName: 'Chrysaora colorata' },
  { globalCode: 'PARTNER-AAU-1.001', speciesName: 'Aurelia aurita' },
];

function printDocument() {
  return exports.buildQrPrintDocument([label], settings);
}

function cssRule(html, selector) {
  const match = html.match(new RegExp(`${selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')} \\{([^}]*)\\}`));
  assert.ok(match, `missing CSS rule for ${selector}`);
  return match[1];
}

function svgFor(item) {
  return exports.buildQrLabelSvg({ ...label, ...item }, 'data:image/svg+xml;base64,AAAA');
}

test('canonical label is a 40 x 30 mm landscape label with a 25 mm QR', () => {
  assert.equal(settings.labelWidthMm, 40);
  assert.equal(settings.labelHeightMm, 30);
  assert.equal(settings.qrSizeMm, 25);
  assert.ok(settings.labelWidthMm > settings.labelHeightMm, 'the label must read as landscape');
});

test('the print document lays the QR and rotated text band side by side', () => {
  const html = printDocument();
  const labelRule = cssRule(html, '.label');
  const markup = html.match(/<section class="label">([\s\S]*?)<\/section>/)[1];

  assert.match(labelRule, /display: flex/);
  assert.match(labelRule, /flex-direction: row/);
  assert.doesNotMatch(labelRule, /flex-direction: column/);
  assert.match(labelRule, /align-items: center/);
  assert.ok(markup.indexOf('label-qr') < markup.indexOf('label-main'), 'the QR must come first');
});

test('the text is rotated upward in the band beside the QR', () => {
  const html = printDocument();
  const labelRule = cssRule(html, '.label');
  const mainRule = cssRule(html, '.label-main');
  const textRule = cssRule(html, '.label-text');

  assert.doesNotMatch(labelRule, /flex-direction: column/);
  assert.doesNotMatch(mainRule, /margin-top/);
  assert.match(mainRule, new RegExp(`margin-left: ${exports.QR_LABEL_QR_TEXT_GAP_MM}mm`));
  assert.match(mainRule, /overflow: hidden/);
  assert.match(textRule, /transform: translate\(-50%, -50%\) rotate\(-90deg\)/);
  assert.match(textRule, new RegExp(`width: ${exports.QR_LABEL_TEXT_LINE_LENGTH_MM}mm`));
  assert.match(textRule, new RegExp(`height: ${exports.QR_LABEL_TEXT_ZONE_MM}mm`));
});

test('the QR stays square at 25 mm and keeps its vector rendering', () => {
  const rule = cssRule(printDocument(), '.label-qr img');
  const width = rule.match(/width: ([\d.]+)mm/)[1];
  const height = rule.match(/height: ([\d.]+)mm/)[1];

  assert.equal(width, height);
  assert.equal(Number(width), settings.qrSizeMm);
  assert.match(rule, /object-fit: contain/);
  assert.match(rule, /transform: rotate\(-90deg\)/);
  assert.doesNotMatch(rule, /image-rendering: pixelated/);
});

test('the species name is larger, italic and limited to two wrapped lines', () => {
  const html = printDocument();
  const codeSize = Number(cssRule(html, '.label-code').match(/font-size: ([\d.]+)pt/)[1]);
  const speciesRule = cssRule(html, '.label-species');
  const speciesSize = Number(speciesRule.match(/font-size: ([\d.]+)pt/)[1]);

  assert.equal(codeSize, settings.textFontPt);
  assert.ok(speciesSize > codeSize);
  assert.match(speciesRule, /font-style: italic/);
  assert.match(speciesRule, /-webkit-line-clamp: 2/);
});

test('print text wraps without ellipsis or horizontal truncation', () => {
  const html = printDocument();
  const codeRule = cssRule(html, '.label-code');
  const speciesRule = cssRule(html, '.label-species');

  assert.match(codeRule, /overflow-wrap: break-word/);
  assert.match(speciesRule, /overflow-wrap: break-word/);
  assert.doesNotMatch(`${codeRule}${speciesRule}`, /text-overflow: ellipsis/);
  assert.doesNotMatch(`${codeRule}${speciesRule}`, /white-space: nowrap/);
});

test('each selected label gets one exact-size print page with no trailing blank page', () => {
  for (const count of [1, 2, 5]) {
    const html = exports.buildQrPrintDocument(
      Array.from({ length: count }, (_, index) => ({ ...label, id: index + 1 })),
      settings,
    );
    const pages = html.match(/<main class="label-slot">/g) ?? [];
    assert.equal(pages.length, count, `${count} labels must produce ${count} pages`);
    assert.equal((html.match(/<section class="label">/g) ?? []).length, count);
    assert.match(html, /@page \{ size: 40mm 30mm; margin: 0; \}/);
    assert.match(html, /break-after: page; page-break-after: always/);
    assert.match(html, /\.label-slot:last-child \{ break-after: auto; page-break-after: auto; \}/);
    assert.doesNotMatch(html, /size: A4|class="sheet"/);
  }
});

test('the QR and rotated text block fit the physical label without clipping', () => {
  const contentWidth = settings.labelWidthMm - 2 * exports.QR_LABEL_BORDER_MM - 2 * settings.paddingMm;
  const contentHeight = settings.labelHeightMm - 2 * exports.QR_LABEL_BORDER_MM - 2 * settings.paddingMm;
  const fontMm = exports.pointsToMillimetres(settings.textFontPt);
  const fourLineFallbackHeight = 2 * fontMm * 1.05
    + exports.QR_LABEL_TEXT_GAP_MM
    + 2 * fontMm * 1.1;

  assert.equal(exports.QR_LABEL_TEXT_LINE_LENGTH_MM, contentHeight);
  assert.ok(settings.qrSizeMm <= contentHeight, 'the QR must fit the label height');
  assert.ok(
    settings.qrSizeMm + exports.QR_LABEL_QR_TEXT_GAP_MM + exports.QR_LABEL_TEXT_ZONE_MM <= contentWidth + 0.001,
    'the QR and text band must fit the label width',
  );
  assert.ok(fourLineFallbackHeight <= exports.QR_LABEL_TEXT_ZONE_MM, 'two wrapped lines per value must fit the text band');
});

test('7 pt fits representative codes and species without squeezing', () => {
  const fontMm = exports.pointsToMillimetres(settings.textFontPt);
  const longestCode = Math.max(...representativeLabels.map((item) => item.globalCode.length));
  const longestSpecies = Math.max(...representativeLabels.map((item) => item.speciesName.length));

  assert.equal(settings.textFontPt, 7);
  assert.ok(longestCode * fontMm * 0.62 <= exports.QR_LABEL_TEXT_LINE_LENGTH_MM);
  assert.ok(longestSpecies * fontMm * 0.55 <= exports.QR_LABEL_TEXT_LINE_LENGTH_MM);
  assert.ok(longestCode * exports.pointsToMillimetres(7.5) * 0.62 > exports.QR_LABEL_TEXT_LINE_LENGTH_MM);

  for (const item of representativeLabels) {
    const svg = svgFor(item);
    assert.doesNotMatch(
      svg,
      /textLength/,
      `${item.globalCode} / ${item.speciesName} had to be squeezed`,
    );
    assert.match(svg, new RegExp(`>${item.globalCode}<`));
    assert.match(svg, new RegExp(`>${item.speciesName}<`));
  }
});

test('the downloaded SVG mirrors the rotated landscape label design', () => {
  const svg = svgFor({});

  assert.match(svg, /width="40mm" height="30mm"/);
  assert.match(svg, /viewBox="0 0 40 30"/);
  assert.match(svg, /width="25" height="25" transform="rotate\(-90 [\d.]+ [\d.]+\)"/);
  assert.match(svg, /<g class="label-text" transform="translate\(([\d.]+) 15\) rotate\(-90\)">/);

  const imageX = Number(svg.match(/<image [^>]*x="([\d.]+)"/)[1]);
  const textCenterX = Number(svg.match(/class="label-text" transform="translate\(([\d.]+)/)[1]);
  assert.ok(imageX < 40 / 2, 'the QR must sit at the left extremity');
  assert.ok(textCenterX > imageX + 25, 'the rotated text must be centred after the QR');
  assert.ok(textCenterX + exports.QR_LABEL_TEXT_ZONE_MM / 2 <= 40 - exports.QR_LABEL_BORDER_MM - settings.paddingMm + 0.001);
  assert.ok(15 + exports.QR_LABEL_TEXT_LINE_LENGTH_MM / 2 <= 30 - exports.QR_LABEL_BORDER_MM - settings.paddingMm + 0.001);
  assert.ok(15 - exports.QR_LABEL_TEXT_LINE_LENGTH_MM / 2 >= exports.QR_LABEL_BORDER_MM + settings.paddingMm - 0.001);
});

test('the modal preview keeps the canonical landscape geometry', () => {
  const modal = exports.getQrLabelPreviewCssVariables(settings);

  assert.equal(modal['--label-preview-ratio'], '40 / 30');
  assert.equal(modal['--label-preview-qr-size'], '62.5cqw');
  assert.equal(modal['--label-preview-text-zone-width'], '30.5cqw');
  assert.equal(modal['--label-preview-text-line-length'], '69.25cqw');
  assert.equal(modal['--label-preview-font-size'], '6.1736cqw');
});

test('QR payload and scan routing semantics are unchanged', () => {
  assert.equal(
    exports.getBoxQrImageUrl({ id: 17, qr_image_url: '/boites/17/qr.svg' }),
    '/boites/17/qr.svg?public_base_url=https%3A%2F%2Fpolypbase.test',
  );
  assert.equal(exports.getBoxScanUrl({ id: 17 }), `${origin}/bac/17/`);
  assert.match(printDocument(), /src="https:\/\/polypbase\.test\/boites\/17\/qr\.svg"/);
});

test('the shared preview component renders a rotated text block beside the QR', () => {
  const css = readFileSync(new URL('../src/styles/components/qr-label.css', import.meta.url), 'utf8');
  const rule = css.match(/\.qr-label--label \{([^}]*)\}/)[1];
  const textRule = css.match(/\.qr-label--label \.qr-label__text \{([^}]*)\}/)[1];

  assert.match(rule, /display: flex/);
  assert.match(rule, /flex-direction: row/);
  assert.doesNotMatch(rule, /flex-direction: column/);
  assert.match(rule, /aspect-ratio: var\(--label-preview-ratio\)/);
  assert.match(css, /\.qr-label--label \.qr-label__image \{[^}]*transform: rotate\(-90deg\)/s);
  assert.match(textRule, /rotate\(-90deg\)/);
  assert.match(textRule, /width: var\(--label-preview-text-line-length\)/);
  assert.match(textRule, /height: var\(--label-preview-text-zone-width\)/);
});

test('the legacy Ctrl+P print path uses the canonical geometry', () => {
  const css = readFileSync(new URL('../src/styles/responsive/print.css', import.meta.url), 'utf8');
  const rule = css.match(/\.qr-label-print-sheet \{([^}]*)\}/)[1];

  assert.match(rule, new RegExp(`width: ${settings.labelWidthMm}mm`));
  assert.match(rule, new RegExp(`height: ${settings.labelHeightMm}mm`));
  assert.match(rule, new RegExp(`padding: ${settings.paddingMm}mm`));
  assert.match(rule, new RegExp(`gap: ${exports.QR_LABEL_QR_TEXT_GAP_MM}mm`));
  assert.match(
    css,
    new RegExp(`\\.qr-label-print-sheet \\.qr-label__image \\{[^}]*width: ${settings.qrSizeMm}mm`),
  );
  assert.match(css, new RegExp(`font-size: ${settings.textFontPt}pt`));
  assert.match(css, /\.qr-label-print-sheet \.qr-label__text \{[^}]*rotate\(-90deg\)/s);
  assert.match(css, new RegExp(`width: ${exports.QR_LABEL_TEXT_LINE_LENGTH_MM}mm`));
  assert.match(css, new RegExp(`height: ${exports.QR_LABEL_TEXT_ZONE_MM}mm`));
});

test('the obsolete page preview is absent while selection and print remain available', () => {
  const labelsView = readFileSync(new URL('../src/components/LabelsView.tsx', import.meta.url), 'utf8');

  assert.match(labelsView, /profile-label-selector/);
  assert.match(labelsView, /onClearQrLabelSelection/);
  assert.match(labelsView, /printQrLabels\(selectedLabels, printSettings\)/);
  assert.doesNotMatch(labelsView, /label-sheet-preview|label-pages-preview|label-workspace-tabs|QrLabel item=/);
});

test('the Labels desktop workspace uses the shared page frame without stretching sparse box groups', () => {
  const css = readFileSync(new URL('../src/styles/pages/exports-labels.css', import.meta.url), 'utf8');
  const layout = readFileSync(new URL('../src/styles/layout.css', import.meta.url), 'utf8');
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const selectorRule = css.match(/\.profile-label-selector \{([^}]*)\}/)[1];
  const groupRule = css.match(/\.label-zone-group \{([^}]*)\}/)[1];
  const groupListRule = css.match(/\.label-zone-group-list \{([^}]*)\}/)[1];
  const cardRules = [...css.matchAll(/\.labels-page \.profile-label-selector label \{([^}]*)\}/g)]
    .map((match) => match[1]);

  // Labels inherits the one shared outer frame; it owns no local width.
  assert.match(layout, /\.page-heading,\s*\n\.workspace-page\s*\{[^}]*width:\s*min\(var\(--page-width\),\s*100%\)/s);
  assert.match(app, /<section className="workspace">/);
  assert.doesNotMatch(app, /page-width-/);
  assert.doesNotMatch(css, /\.workspace-page:has\(/);
  assert.doesNotMatch(css, /\.labels-page\s*\{[^}]*max-width/s);
  assert.match(selectorRule, /align-content: start/);
  assert.match(groupRule, /align-content: start/);
  assert.match(groupListRule, /grid-auto-rows: max-content/);
  assert.match(groupListRule, /align-content: start/);
  assert.ok(cardRules.length >= 2, 'desktop and tablet card rules must both be present');
  assert.match(cardRules[0], /align-self: start/);
  assert.match(css, /\.labels-page \.profile-label-selector label \{[^}]*min-height: 54px/s);
  for (const rule of cardRules) {
    assert.doesNotMatch(rule, /(?:^|[;\s])height\s*:/, 'box cards must not receive a fixed or stretching height');
  }
});

test('the Labels page has no sheet preview and keeps its selection heading', () => {
  const labelsView = readFileSync(new URL('../src/components/LabelsView.tsx', import.meta.url), 'utf8');

  assert.doesNotMatch(labelsView, /label-sheet-preview|label-pages-preview|label-workspace-tabs|label-preview/);
  assert.match(labelsView, /<h2>\{labels\.qrLabelSelectionTitle\}<\/h2>/);
  assert.match(labelsView, /qrLabelPrintSelection/);
});
