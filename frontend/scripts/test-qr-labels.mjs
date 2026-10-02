import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

import './test-qr-preparation.mjs';
import './test-admin-transfer-qr.mjs';

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
  { globalCode: 'CCO-PAC.1.001', speciesName: 'Chrysaora quinquecirrha' },
  { globalCode: 'CCO-PAC.1.002', speciesName: 'Chrysaora chesapeakei "pink striped"' },
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

const labelsViewSource = readFileSync(new URL('../src/components/LabelsView.tsx', import.meta.url), 'utf8');

function renderLabelsSelection(selected) {
  const { outputText } = ts.transpileModule(labelsViewSource, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX, esModuleInterop: true },
  });
  const view = { exports: {} };
  const selectedCount = typeof selected === 'number' ? selected : selected ? 1 : 0;
  const boxes = Array.from({ length: Math.max(selectedCount, 1) }, (_, index) => ({
    id: index + 1, global_code: `AAU-ATL-1.00${index + 1}`, local_code: 'AAU-ATL-1', status: 'active',
    species: { id: 1, scientific_name: 'Aurelia aurita' }, strain: { code: 'AAU-ATL-1' },
    organization: { id: 1 }, thermal_zone: { id: 2, name: 'Armoire 15°C' },
    latest_measurement: { measured_on: '2099-01-01' },
  }));
  const box = boxes[0];
  const modules = {
    react: React,
    'react/jsx-runtime': jsxRuntime,
    'lucide-react': { ChevronDown: () => null, ChevronRight: () => null, X: () => null, Printer: () => null },
    '../utils/qrLabels': {
      DEFAULT_QR_LABEL_PRINT_SETTINGS: {},
      buildQrLabelItem: (item) => ({ id: item.id, globalCode: item.global_code, speciesName: item.species.scientific_name, zoneName: item.thermal_zone?.name ?? '', qrImageUrl: '' }),
      printQrLabels() {},
    },
    './BoxTrackingPreview': ({ code }) => React.createElement('a', { href: `/boxes/${code}` }, code),
    './ModalPortal': ({ children }) => children,
    './PageLoader': () => null,
  };
  vm.runInNewContext(outputText, {
    exports: view.exports,
    require: (name) => {
      assert.ok(Object.hasOwn(modules, name), `unexpected LabelsView dependency: ${name}`);
      return modules[name];
    },
  });
  const labels = {
    allZones: 'Toutes', close: 'Fermer', noZone: 'Sans zone', pageTitle: 'Étiquettes',
    qrLabelAddResults: (count) => `Ajouter les ${count} résultats`, qrLabelAddResultsCompact: (count) => `Ajouter ${count}`, qrLabelClearSelection: 'Vider la liste',
    qrLabelNoEligibleBoxes: 'Aucune boîte', qrLabelNoMatches: 'Aucun résultat', qrLabelRemove: 'Retirer',
    qrLabelPrintCount: (count) => `Imprimer ${count}`, qrLabelRemoveBox: (code) => `Retirer ${code}`,
    qrLabelSearchTitle: 'Rechercher',
    qrLabelSelectedSingular: 'étiquette sélectionnée', qrLabelSelectedPlural: 'étiquettes sélectionnées',
    qrLabelSelectionCountText: (count) => `${count} étiquette${count === 1 ? '' : 's'}`, qrLabelSearchPlaceholder: 'Rechercher',
    qrLabelSpeciesCount: (count) => `${count} boîtes`, qrLabelSpeciesSelected: (count) => `${count} sélectionnées`,
    qrLabelSelectSpecies: (count, species) => `Sélectionner les ${count} résultats de ${species}`,
    qrLabelDeselectSpecies: (count, species) => `Retirer les ${count} résultats de ${species}`,
    qrLabelViewSelection: 'Voir la sélection', selectBox: 'Sélectionner la boîte', zoneLabel: 'Emplacement',
  };
  return renderToStaticMarkup(React.createElement(view.exports.default, {
    boxes, labels, language: 'fr', isLoading: false,
    profile: { is_superuser: true, memberships: [] },
    qrLabelSelection: boxes.slice(0, selectedCount).map((item) => modules['../utils/qrLabels'].buildQrLabelItem(item)),
    onAddQrLabel() {}, onClearQrLabelSelection() {}, onRemoveQrLabel() {}, onOpenBox() {}, t: () => '',
  }));
}

function labelSelectionHelpers(...names) {
  const ast = ts.createSourceFile('LabelsView.tsx', labelsViewSource, ts.ScriptTarget.ES2022, true, ts.ScriptKind.TSX);
  const declarations = ast.statements.filter((statement) =>
    ts.isFunctionDeclaration(statement) && names.includes(statement.name?.text));
  assert.equal(declarations.length, names.length, 'selection helper functions must exist');
  const code = declarations.map((statement) => statement.getText(ast)).join('\n');
  const { outputText } = ts.transpileModule(`${code}\nexports.result = { ${names.join(', ')} };`, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  });
  const context = { exports: {} };
  vm.runInNewContext(outputText, context);
  return context.exports.result;
}

test('canonical label is a 41 x 28 mm landscape label with a 25 mm QR', () => {
  assert.equal(settings.labelWidthMm, 41);
  assert.equal(settings.labelHeightMm, 28);
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
  assert.doesNotMatch(labelRule, /border:|border-radius:/);
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

test('print gives both label elements larger bold text with the required styles', () => {
  const html = printDocument();
  const codeRule = cssRule(html, '.label-code');
  const speciesRule = cssRule(html, '.label-species');
  const codeSize = Number(codeRule.match(/font-size: ([\d.]+)pt/)[1]);
  const speciesSize = Number(speciesRule.match(/font-size: ([\d.]+)pt/)[1]);

  assert.equal(codeSize, settings.textFontPt);
  assert.equal(speciesSize, exports.QR_LABEL_SPECIES_FONT_PT);
  assert.ok(codeSize > 7);
  assert.ok(speciesSize >= 7);
  assert.match(codeRule, /font-style: normal/);
  assert.match(codeRule, /font-weight: 900/);
  assert.match(speciesRule, /font-style: italic/);
  assert.match(speciesRule, /font-weight: 900/);
  assert.match(speciesRule, /-webkit-line-clamp: 3/);
});

test('print text wraps without ellipsis or horizontal truncation', () => {
  const html = printDocument();
  const codeRule = cssRule(html, '.label-code');
  const speciesRule = cssRule(html, '.label-species');

  assert.match(codeRule, /overflow-wrap: break-word/);
  assert.match(speciesRule, /overflow-wrap: break-word/);
  assert.doesNotMatch(`${codeRule}${speciesRule}`, /text-overflow: ellipsis/);
  assert.doesNotMatch(`${codeRule}${speciesRule}`, /white-space: nowrap/);
  assert.match(
    exports.buildQrPrintDocument([{ ...label, speciesName: 'Chrysaora chesapeakei "pink striped"' }], settings),
    /Chrysaora chesapeakei "pink striped"/,
  );
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
    assert.match(html, /@page \{ size: 41mm 28mm; margin: 0; \}/);
    assert.match(html, /break-after: page; page-break-after: always/);
    assert.match(html, /\.label-slot:last-child \{ break-after: auto; page-break-after: auto; \}/);
    assert.doesNotMatch(html, /size: A4|class="sheet"/);
  }
});

test('the QR and rotated text block fit the physical label without clipping', () => {
  const contentWidth = settings.labelWidthMm - 2 * settings.paddingMm;
  const contentHeight = settings.labelHeightMm - 2 * settings.paddingMm;
  const codeFontMm = exports.pointsToMillimetres(settings.textFontPt);
  const speciesFontMm = exports.pointsToMillimetres(exports.QR_LABEL_SPECIES_FONT_PT);
  const fiveLineHeight = 2 * codeFontMm * 1.05
    + exports.QR_LABEL_TEXT_GAP_MM
    + 3 * speciesFontMm * 1.1;

  assert.equal(exports.QR_LABEL_TEXT_LINE_LENGTH_MM, contentHeight);
  assert.ok(settings.qrSizeMm <= contentHeight, 'the QR must fit the label height');
  assert.ok(
    settings.qrSizeMm + exports.QR_LABEL_QR_TEXT_GAP_MM + exports.QR_LABEL_TEXT_ZONE_MM <= contentWidth + 0.001,
    'the QR and text band must fit the label width',
  );
  assert.ok(fiveLineHeight <= exports.QR_LABEL_TEXT_ZONE_MM, 'two code and three species lines must fit the text band');
});

test('SVG text stays complete, bold, and allows three species lines', () => {
  assert.equal(settings.textFontPt, 7.5);

  for (const item of representativeLabels) {
    const svg = svgFor(item);
    const codeLines = [...svg.matchAll(/<text class="label-code"[^>]*>(.*?)<\/text>/g)]
      .map((match) => match[1]);
    const speciesLines = [...svg.matchAll(/<text class="label-species"[^>]*>(.*?)<\/text>/g)]
      .map((match) => match[1]);

    assert.ok(codeLines.length <= 2, `${item.globalCode} must use at most two lines`);
    assert.ok(speciesLines.length <= 3, `${item.speciesName} must use at most three lines`);
    if (item.globalCode === 'CCO-PAC.1.001') {
      assert.equal(codeLines.length, 1, 'the normal box code should fit one line');
      assert.equal(speciesLines.length, 2, 'the longer species should use two complete lines');
    }
    if (item.speciesName.includes('pink striped')) {
      assert.equal(speciesLines.length, 3, 'the printed example needs three complete species lines');
    }
    assert.equal(codeLines.join('').replace(/&amp;/g, '&'), item.globalCode);
    assert.equal(speciesLines.join(' ').replace(/&quot;/g, '"').replace(/\s+/g, ' ').trim(), item.speciesName);
    assert.match(svg, /class="label-code"[^>]*font-weight="900"[^>]*font-style="normal"/);
    assert.match(svg, /class="label-species"[^>]*font-weight="900"[^>]*font-style="italic"/);
    assert.doesNotMatch(svg, /textLength/);
  }
});

test('the downloaded SVG mirrors the rotated landscape label design', () => {
  const svg = svgFor({});

  assert.match(svg, /width="41mm" height="28mm"/);
  assert.match(svg, /viewBox="0 0 41 28"/);
  assert.doesNotMatch(svg, /<rect\b|stroke="#[0-9a-f]+"/);
  assert.match(svg, /width="25" height="25" transform="rotate\(-90 [\d.]+ [\d.]+\)"/);
  assert.match(svg, /<g class="label-text" transform="translate\(([\d.]+) 14\) rotate\(-90\)">/);

  const imageX = Number(svg.match(/<image [^>]*x="([\d.]+)"/)[1]);
  const textCenterX = Number(svg.match(/class="label-text" transform="translate\(([\d.]+)/)[1]);
  assert.ok(imageX < settings.labelWidthMm / 2, 'the QR must sit at the left extremity');
  assert.ok(textCenterX > imageX + settings.qrSizeMm, 'the rotated text must be centred after the QR');
  assert.ok(textCenterX + exports.QR_LABEL_TEXT_ZONE_MM / 2 <= settings.labelWidthMm - settings.paddingMm + 0.001);
  assert.ok(settings.labelHeightMm / 2 + exports.QR_LABEL_TEXT_LINE_LENGTH_MM / 2 <= settings.labelHeightMm - settings.paddingMm + 0.001);
  assert.ok(settings.labelHeightMm / 2 - exports.QR_LABEL_TEXT_LINE_LENGTH_MM / 2 >= settings.paddingMm - 0.001);
});

test('the modal preview keeps the canonical landscape geometry', () => {
  const modal = exports.getQrLabelPreviewCssVariables(settings);

  assert.equal(modal['--label-preview-ratio'], '41 / 28');
  assert.equal(modal['--label-preview-qr-size'], '60.9756cqw');
  assert.equal(modal['--label-preview-text-zone-width'], '34.878cqw');
  assert.equal(modal['--label-preview-text-line-length'], '65.122cqw');
  assert.equal(modal['--label-preview-font-size'], '6.4533cqw');
  assert.equal(modal['--label-preview-species-font-size'], '6.023cqw');
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
  assert.match(rule, /border: 0/);
  assert.match(rule, /border-radius: 0/);
  assert.match(css, /\.qr-label--label \.qr-label__image \{[^}]*transform: rotate\(-90deg\)/s);
  assert.match(css, /\.qr-label--label \.qr-label__metadata strong \{[^}]*font-style: normal/s);
  assert.match(css, /\.qr-label--label \.qr-label__metadata small \{[^}]*font-style: italic/s);
  assert.match(css, /\.qr-label--label \.qr-label__metadata :where\(strong, small\) \{[^}]*font-weight: 900/s);
  assert.match(textRule, /rotate\(-90deg\)/);
  assert.match(textRule, /width: var\(--label-preview-text-line-length\)/);
  assert.match(textRule, /height: var\(--label-preview-text-zone-width\)/);
  assert.match(css, /\.qr-label--label \.qr-label__metadata small \{[^}]*-webkit-line-clamp: 3/s);
});

test('the legacy Ctrl+P print path uses the canonical geometry', () => {
  const css = readFileSync(new URL('../src/styles/responsive/print.css', import.meta.url), 'utf8');
  const rule = css.match(/\.qr-label-print-sheet \{([^}]*)\}/)[1];

  assert.match(rule, new RegExp(`width: ${settings.labelWidthMm}mm`));
  assert.match(rule, new RegExp(`height: ${settings.labelHeightMm}mm`));
  assert.match(rule, new RegExp(`padding: ${settings.paddingMm}mm`));
  assert.match(rule, new RegExp(`gap: ${exports.QR_LABEL_QR_TEXT_GAP_MM}mm`));
  assert.match(rule, /border: 0/);
  assert.match(rule, /border-radius: 0/);
  assert.match(
    css,
    new RegExp(`\\.qr-label-print-sheet \\.qr-label__image \\{[^}]*width: ${settings.qrSizeMm}mm`),
  );
  assert.match(css, new RegExp(`font-size: ${settings.textFontPt}pt`));
  assert.match(css, new RegExp(`font-size: ${exports.QR_LABEL_SPECIES_FONT_PT}pt`));
  assert.match(css, /\.qr-label-print-sheet \.qr-label__metadata strong \{[^}]*font-style: normal/s);
  assert.match(css, /\.qr-label-print-sheet \.qr-label__metadata small \{[^}]*font-style: italic/s);
  assert.match(css, /\.qr-label-print-sheet \.qr-label__metadata :where\(strong, small\) \{[^}]*font-weight: 900/s);
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

test('the Labels workspace keeps the shared frame and lays out species accordions as rows', () => {
  const css = readFileSync(new URL('../src/styles/pages/exports-labels.css', import.meta.url), 'utf8');
  const layout = readFileSync(new URL('../src/styles/layout.css', import.meta.url), 'utf8');
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');

  // Labels inherits the shared outer frame; it owns no local width.
  assert.match(layout, /\.page-heading,\s*\n\.workspace-page\s*\{[^}]*width:\s*min\(var\(--page-width\),\s*100%\)/s);
  assert.match(app, /<section className="workspace">/);
  assert.doesNotMatch(app, /page-width-/);
  assert.doesNotMatch(css, /\.workspace-page:has\(/);
  assert.doesNotMatch(css, /\.labels-page\s*\{[^}]*max-width/s);

  assert.match(css, /\.labels-page \.label-species-group\s*\{/);
  assert.match(css, /\.labels-page \.label-species-rows:not\(\[hidden\]\)\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/s);
  assert.match(css, /@media \(max-width: 1119px\)[\s\S]*?\.labels-page \.label-species-rows:not\(\[hidden\]\)\s*\{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css, /@media \(min-width: 920px\) and \(max-width: 1180px\) and \(pointer: coarse\)[\s\S]*?\.labels-page \.label-species-rows:not\(\[hidden\]\)\s*\{[^}]*grid-template-columns:\s*repeat\(3,\s*minmax\(0,\s*1fr\)\)/);
  assert.match(css, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)[\s\S]*?\.labels-page \.label-species-rows:not\(\[hidden\]\)\s*\{[^}]*grid-template-columns:\s*1fr/);
  assert.match(css, /\.labels-page \.label-species-rows\[hidden\]\s*\{[^}]*display:\s*none/s);
  const row = cssRule(css, '.labels-page .profile-label-selector .label-box-row');
  const copy = cssRule(css, '.labels-page .profile-label-selector .label-box-copy');
  assert.match(row, /min-height:\s*52px/);
  assert.match(copy, /gap:\s*2px;\s*padding-block:\s*var\(--space-2\)/);
  assert.match(css, /\.labels-page \.label-box-toggle\s*\{[^}]*min-height:\s*48px/s);
  const tablet = css.slice(css.indexOf('@media (min-width: 760px) and (max-width: 1023px)'));
  assert.match(tablet, /\.labels-page \.profile-label-selector \.label-box-row\s*\{[^}]*min-height:\s*44px;[^}]*gap:\s*var\(--space-1\);[^}]*padding-inline:\s*var\(--space-1\)/s);
  assert.match(tablet, /\.labels-page \.label-box-toggle\s*\{[^}]*min-height:\s*44px/s);
  assert.match(tablet, /\.labels-page \.profile-label-selector \.label-box-copy\s*\{[^}]*gap:\s*0;[^}]*padding:\s*0/s);
  const tabletCss = readFileSync(new URL('../src/styles/responsive/tablet.css', import.meta.url), 'utf8');
  assert.match(tabletCss, /\.box-inventory-cell a\s*\{[^}]*min-height:\s*44px/s);
  assert.match(tabletCss, /\.labels-page \.label-box-copy a\s*\{[^}]*min-height:\s*22px/s);
  const phone = css.slice(css.indexOf('@media (max-width: 759px), (max-width: 900px) and (orientation: portrait)'));
  assert.match(phone, /\.labels-page \.profile-label-selector \.label-box-row\s*\{[^}]*min-height:\s*46px;[^}]*padding-inline:\s*var\(--space-1\)/s);
  assert.match(phone, /\.labels-page \.label-box-toggle\s*\{[^}]*min-height:\s*46px/s);
  assert.match(phone, /\.labels-page \.profile-label-selector \.label-box-copy\s*\{[^}]*gap:\s*0;[^}]*padding:\s*0/s);
  assert.match(phone, /\.labels-page \.profile-label-selector \.label-box-copy a\s*\{[^}]*min-height:\s*22px/s);
  assert.doesNotMatch(row, /transform|box-shadow|transition/);
  const hover = cssRule(css, '.labels-page .profile-label-selector .label-box-row:hover:not(.is-selected)');
  assert.match(hover, /background:\s*var\(--color-surface-subtle\)/);
  assert.doesNotMatch(hover, /transform|translate|box-shadow|transition/);
  assert.match(labelsViewSource, /aria-label=\{labels\.qrLabelAddResults\(labelBoxes\.length\)\}/);
  assert.match(labelsViewSource, /labels\.qrLabelAddResultsCompact\(labelBoxes\.length\)/);
  assert.match(css, /\.labels-page \.label-add-results-full\s*\{[^}]*display:\s*none/);
  assert.match(css, /\.labels-page \.label-add-results-compact\s*\{[^}]*display:\s*inline/);
  assert.match(labelsViewSource, /selectedLabels\.length > 0 \? \(\s*<div className="label-selection-dock"/);
  assert.match(css, /\.labels-page \.label-selection-dock\s*\{[^}]*position:\s*fixed;[^}]*bottom:\s*22px;[^}]*left:\s*var\(--sidebar-width\)/s);
  assert.match(css, /\.labels-page \.label-selection-bar\s*\{[^}]*width:\s*max-content;[^}]*max-width:\s*min\(100%,\s*560px\);[^}]*min-height:\s*64px;[^}]*border-radius:\s*calc\(var\(--radius-md\) \* 2\)/s);
  assert.match(css, /\.labels-page\.has-selection\s*\{[^}]*padding-bottom:\s*calc\(88px/s);
  assert.match(css, /\.app-shell\.is-tablet-rail \.labels-page \.label-selection-dock\s*\{[^}]*left:\s*var\(--tablet-rail-width\)/s);
  assert.match(css, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)[\s\S]*?\.labels-page \.label-selection-dock\s*\{[^}]*bottom:\s*calc\(58px \+ env\(safe-area-inset-bottom\) \+ var\(--space-3\)\)/);
  assert.match(css, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)[\s\S]*?\.labels-page \.label-selection-bar\s*\{[^}]*width:\s*max-content;[^}]*max-width:\s*100%/);
  assert.match(css, /@media \(max-width: 759px\), \(max-width: 900px\) and \(orientation: portrait\)[\s\S]*?\.labels-page\.has-selection\s*\{[^}]*padding-bottom:\s*calc\(190px \+ env\(safe-area-inset-bottom\)\)/);
  assert.match(labelsViewSource, /isExpanded \? <ChevronDown size=\{18\} \/> : <ChevronRight size=\{18\} \/>/);
  assert.doesNotMatch(css, /\.label-species-chevron\s*\{[^}]*transition\s*:/s);
  assert.doesNotMatch(css, /\.label-species-chevron\s*\{[^}]*border\s*:/s);
  assert.doesNotMatch(css, /\.label-species-chevron\s*\{[^}]*background\s*:/s);
  assert.doesNotMatch(css, /\.label-species-chevron[^}]*transform\s*:/s);
  assert.match(css, /\.labels-page \.profile-label-selector \.label-box-row\.is-selected\s*\{[^}]*background:\s*var\(--color-primary-soft\)/s);
  assert.match(css, /\.label-box-row:focus-within\s*\{[^}]*outline:/s);
  assert.doesNotMatch(css, /\.label-zone-group(?:-list)?\s*\{/);
});

test('species groups sort by name and keep boxes from distinct zones together', () => {
  const { compareLabelValue, groupLabelBoxes } = labelSelectionHelpers('compareLabelValue', 'groupLabelBoxes');
  const boxes = [
    { id: 3, global_code: 'BOX-10', species: { id: 2, scientific_name: 'Zebra species' }, thermal_zone: { id: 1 } },
    { id: 2, global_code: 'BOX-2', species: { id: 2, scientific_name: 'Zebra species' }, thermal_zone: { id: 9 } },
    { id: 1, global_code: 'BOX-1', species: { id: 1, scientific_name: 'Aurelia aurita' }, thermal_zone: null },
  ];
  assert.ok(compareLabelValue('BOX-2', 'BOX-10') < 0);
  const groups = groupLabelBoxes(boxes);
  assert.deepEqual(Array.from(groups, (group) => [group.key, group.name, Array.from(group.boxes, (box) => box.id)]), [
    [1, 'Aurelia aurita', [1]],
    [2, 'Zebra species', [2, 3]],
  ]);
  assert.deepEqual(Array.from(groupLabelBoxes([])), []);
});

test('search and zone combine without changing the global selected set or grouping by zone', () => {
  const { compareLabelValue, getLabelZoneKey, filterLabelBoxes, groupLabelBoxes } = labelSelectionHelpers(
    'compareLabelValue', 'getLabelZoneKey', 'filterLabelBoxes', 'groupLabelBoxes',
  );
  const boxes = [
    { id: 1, global_code: 'AAA-2', local_code: 'local-2', species: { id: 1, scientific_name: 'Aurelia aurita' }, strain: { code: 'A-1' }, thermal_zone: { id: 1 } },
    { id: 2, global_code: 'AAA-10', local_code: 'local-10', species: { id: 1, scientific_name: 'Aurelia aurita' }, strain: { code: 'A-1' }, thermal_zone: { id: 2 } },
    { id: 3, global_code: 'CCC-1', local_code: 'local-3', species: { id: 2, scientific_name: 'Chrysaora colorata' }, strain: { code: 'C-1' }, thermal_zone: { id: 1 } },
  ];
  const selectedIds = new Set([2]);
  const allGroups = groupLabelBoxes(filterLabelBoxes(boxes, 'all', ''));
  assert.deepEqual(Array.from(allGroups, (group) => Array.from(group.boxes, (box) => box.id)), [[1, 2], [3]]);
  const matching = filterLabelBoxes(boxes, 'zone-1', 'aurita');
  assert.deepEqual(Array.from(matching, (box) => box.id), [1]);
  assert.deepEqual(Array.from(matching.filter((box) => !selectedIds.has(box.id)), (box) => box.id), [1]);
  assert.equal(selectedIds.size, 1, 'a hidden selection remains selected');
  assert.deepEqual(Array.from(filterLabelBoxes(boxes, 'zone-2', 'ccc')), []);
  assert.deepEqual(Array.from(filterLabelBoxes(boxes, 'all', 'a-1'), (box) => box.id), [1, 2]);
  assert.deepEqual(Array.from(filterLabelBoxes(boxes, 'all', 'local-3'), (box) => box.id), [3]);
});

test('species selection is tri-state and limited to its currently matching boxes', () => {
  const { filterLabelBoxes, getLabelZoneKey, groupLabelBoxes, compareLabelValue, getSpeciesSelectionState } = labelSelectionHelpers(
    'filterLabelBoxes', 'getLabelZoneKey', 'groupLabelBoxes', 'compareLabelValue', 'getSpeciesSelectionState',
  );
  const boxes = [
    { id: 1, global_code: 'AA-1', local_code: '', species: { id: 1, scientific_name: 'Aurelia' }, strain: { code: '' }, thermal_zone: { id: 2 } },
    { id: 2, global_code: 'AA-2', local_code: '', species: { id: 1, scientific_name: 'Aurelia' }, strain: { code: '' }, thermal_zone: { id: 2 } },
    { id: 3, global_code: 'AA-3', local_code: '', species: { id: 1, scientific_name: 'Aurelia' }, strain: { code: '' }, thermal_zone: { id: 3 } },
    { id: 4, global_code: 'BB-1', local_code: '', species: { id: 2, scientific_name: 'Chrysaora' }, strain: { code: '' }, thermal_zone: { id: 2 } },
  ];
  const matching = groupLabelBoxes(filterLabelBoxes(boxes, 'zone-2', 'aurelia'))[0].boxes;
  const selected = new Set([3, 4]);
  const state = () => getSpeciesSelectionState(matching, selected);
  assert.equal(state().selectedCount, 0);
  assert.equal(state().allSelected, false);
  assert.deepEqual(Array.from(state().unselectedBoxes, (box) => box.id), [1, 2]);
  state().unselectedBoxes.forEach((box) => selected.add(box.id));
  assert.deepEqual(Array.from(selected).sort(), [1, 2, 3, 4], 'hidden and other species selections are untouched');
  assert.equal(state().allSelected, true);
  selected.delete(1); // A row is unchecked after opening the group.
  assert.equal(state().selectedCount, 1);
  assert.equal(state().allSelected, false);
  assert.deepEqual(Array.from(state().unselectedBoxes, (box) => box.id), [1]);
  matching.forEach((box) => selected.delete(box.id));
  assert.deepEqual(Array.from(selected).sort(), [3, 4], 'deselecting this scope keeps hidden boxes selected');
  assert.equal(state().selectedCount, 0);
});

test('species accordion exposes expanded state and box-level checked selection', () => {
  assert.match(labelsViewSource, /const isExpanded = Boolean\(normalizedLabelSearch\) \|\| expandedSpecies\.has\(group\.key\)/);
  assert.match(labelsViewSource, /<button\s+type="button"\s+aria-expanded=\{isExpanded\}\s+aria-controls=\{`label-species-\$\{group\.key\}`\}/);
  assert.match(labelsViewSource, /onClick=\{\(\) => setExpandedSpecies\(\(current\) => \{/);
  assert.match(labelsViewSource, /className="label-species-rows" id=\{`label-species-\$\{group\.key\}`\} hidden=\{!isExpanded\}/);
  assert.match(labelsViewSource, /className=\{`label-box-row\$\{selectedLabelIds\.has\(box\.id\) \? ' is-selected' : ''\}`\}/);
  assert.match(labelsViewSource, /onClick=\{\(event\) => \{[\s\S]*?toggleQrLabel\(box\);/);
  assert.match(labelsViewSource, /<label className="label-box-toggle">\s*<input\s+type="checkbox"\s+checked=\{selectedLabelIds\.has\(box\.id\)\}/);
  assert.match(labelsViewSource, /onChange=\{\(\) => toggleQrLabel\(box\)\}/);
  assert.match(labelsViewSource, /event\.target as HTMLElement\)\.closest\('a, input, button'\)/);
  assert.match(labelsViewSource, /toggleQrLabel\(box\);\s*\}\}/);
  assert.match(labelsViewSource, /if \(selectedLabelIds\.has\(box\.id\)\) onRemoveQrLabel\(box\.id\);\s*else onAddQrLabel\(buildQrLabelItem\(box\)\)/);
  assert.match(labelsViewSource, /\{labels\.qrLabelSpeciesCount\(group\.boxes\.length\)\}/);
  assert.match(labelsViewSource, /labels\.qrLabelSpeciesSelected\(selectedCount\)/);
  assert.match(labelsViewSource, /<\/button>\s*<label className="label-species-toggle">\s*<input\s+type="checkbox"/);
  assert.match(labelsViewSource, /checked=\{allSelected\}/);
  assert.match(labelsViewSource, /aria-checked=\{selectedCount && !allSelected \? 'mixed' : allSelected\}/);
  assert.match(labelsViewSource, /input\.indeterminate = selectedCount > 0 && !allSelected/);
  assert.match(labelsViewSource, /qrLabelSelectSpecies\(group\.boxes\.length, group\.name\)/);
  assert.match(labelsViewSource, /qrLabelDeselectSpecies\(group\.boxes\.length, group\.name\)/);
  assert.match(labelsViewSource, /if \(allSelected\) group\.boxes\.forEach\(\(box\) => onRemoveQrLabel\(box\.id\)\)/);
  assert.match(labelsViewSource, /else unselectedBoxes\.forEach\(\(box\) => onAddQrLabel\(buildQrLabelItem\(box\)\)\)/);
  const css = readFileSync(new URL('../src/styles/pages/exports-labels.css', import.meta.url), 'utf8');
  assert.match(css, /\.labels-page \.label-species-toggle\s*\{[^}]*min-height:\s*52px/s);
  assert.match(css, /\.labels-page \.label-species-toggle input:focus-visible\s*\{[^}]*outline:/s);
});

test('search and zone filters limit results and bulk add without clearing selected labels', () => {
  assert.match(labelsViewSource, /const normalizedLabelSearch = labelSearch\.trim\(\)\.toLocaleLowerCase\(\)/);
  assert.match(labelsViewSource, /zoneFilter !== 'all' && getLabelZoneKey\(box\) !== zoneFilter/);
  assert.match(labelsViewSource, /box\.global_code, box\.local_code, box\.species\.scientific_name, box\.strain\.code/);
  assert.match(labelsViewSource, /\.some\(\(value\) => value!\.toLocaleLowerCase\(\)\.includes\(normalizedLabelSearch\)\)/);
  assert.match(labelsViewSource, /groupLabelBoxes\(labelBoxes\)/);
  assert.match(labelsViewSource, /filterLabelBoxes\(eligibleLabelBoxes, zoneFilter, normalizedLabelSearch\)/);
  assert.match(labelsViewSource, /<select value=\{zoneFilter\} onChange=\{\(event\) => setZoneFilter\(event\.target\.value\)\}>/);
  assert.doesNotMatch(labelsViewSource, /label-results-count|qrLabelMatches/);
  assert.match(labelsViewSource, /labelBoxes\.filter\(\(box\) => !selectedLabelIds\.has\(box\.id\)\)/);
  assert.match(labelsViewSource, /disabled=\{!labelBoxesToAdd\.length\}\s+onClick=\{\(\) => labelBoxesToAdd\.forEach\(\(box\) => onAddQrLabel\(buildQrLabelItem\(box\)\)\)\}/);
  assert.match(labelsViewSource, /labels\.qrLabelAddResults\(labelBoxes\.length\)/);
  assert.match(labelsViewSource, /aria-label=\{labels\.qrLabelAddResults\(labelBoxes\.length\)\}/);
  assert.match(labelsViewSource, /labels\.qrLabelAddResultsCompact\(labelBoxes\.length\)/);
  const css = readFileSync(new URL('../src/styles/pages/exports-labels.css', import.meta.url), 'utf8');
  assert.match(cssRule(css, '.labels-page .label-add-results'), /background:\s*transparent;[^]*color:\s*var\(--color-muted\)/);
});

test('compact selection bar provides only count, clear, and print actions', () => {
  assert.match(labelsViewSource, /selectedLabels\.length > 0 \? \(\s*<div className="label-selection-dock">\s*<div className="label-selection-bar"/);
  assert.match(labelsViewSource, /<div className="label-selection-summary" role="status">[\s\S]*?<strong>[\s\S]*?qrLabelSelectedSingular[\s\S]*?qrLabelSelectedPlural[\s\S]*?<\/strong>[\s\S]*?<\/div>\s*<div className="label-selection-actions">/);
  assert.match(labelsViewSource, /aria-label=\{labels\.qrLabelClearSelection\}/);
  assert.match(labelsViewSource, /onClick=\{onClearQrLabelSelection\}/);
  assert.doesNotMatch(labelsViewSource, /searchRef|\.focus\(\)|window\.scrollTo|scrollTo\(/);
  assert.match(labelsViewSource, /const result = await printQrLabels\(selectedLabels, printSettings\)/);
  assert.match(labelsViewSource, /onClick=\{\(\) => void handlePrint\(\)\}/);
  assert.match(labelsViewSource, /disabled=\{isPreparing\}/);
  assert.match(labelsViewSource, /isExpanded \? <ChevronDown size=\{18\} \/> : <ChevronRight size=\{18\} \/>/);
  assert.doesNotMatch(labelsViewSource, /qrLabelSelectionContext|qrLabelViewSelection|isReviewOpen|reviewGroups|label-review-modal/);
  assert.equal((labelsViewSource.match(/labels\.qrLabelPrintCount\(selectedLabels.length\)/g) ?? []).length, 1);
  assert.match(labelsViewSource, /onRemoveQrLabel\(box\.id\)/);
});

test('Labels reuses Inventory box identity and keeps navigation distinct from selection', () => {
  const app = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../src/styles/pages/exports-labels.css', import.meta.url), 'utf8');
  const inventory = readFileSync(new URL('../src/components/BoxInventoryAdminSection.tsx', import.meta.url), 'utf8');
  const preview = readFileSync(new URL('../src/components/BoxTrackingPreview.tsx', import.meta.url), 'utf8');
  assert.match(preview, /href=\{`\/boxes\/\$\{encodeURIComponent\(code\)\}`\}/);
  assert.match(preview, /onOpenBox\(boxId, code\)/);
  assert.match(preview, /import\('\.\/BoxTrackingChart'\)/);
  assert.match(inventory, /<BoxTrackingPreview boxId=\{box\.id\} code=\{box\.global_code\}/);
  assert.match(labelsViewSource, /import BoxTrackingPreview from '\.\/BoxTrackingPreview'/);
  assert.match(labelsViewSource, /<BoxTrackingPreview\s+boxId=\{box\.id\}\s+code=\{box\.global_code\}\s+speciesName=\{box\.species\.scientific_name\}\s+language=\{language\}\s+onOpenBox=\{onOpenBox\}\s+t=\{t\}/);
  assert.match(labelsViewSource, /className="box-inventory-cell box-inventory-identity label-box-copy"/);
  assert.match(app, /<LabelsView[\s\S]*?language=\{language\}[\s\S]*?onOpenBox=\{openBox\}/);
  assert.match(css, /\.labels-page \.label-box-toggle\s*\{[^}]*min-height:\s*48px/s);
  assert.doesNotMatch(css, /\.labels-page \.label-selection-bar\s*\{[^}]*box-shadow:/s);
  assert.doesNotMatch(css, /\.labels-page \.profile-label-selector \.label-box-row\.is-selected\s*\{[^}]*box-shadow:/s);
  assert.doesNotMatch(css, /\.label-review-modal\s*\{[^}]*box-shadow:/s);
});

test('rendered Labels bar appears only when a box is selected', () => {
  const empty = renderLabelsSelection(false);
  const populated = renderLabelsSelection(true);
  const plural = renderLabelsSelection(3);
  assert.doesNotMatch(empty, /label-selection-bar|Imprimer 0|Vider la liste/);
  assert.match(populated, /class="label-selection-dock"/);
  assert.match(populated, /class="label-selection-bar"/);
  assert.match(populated, /<b>1<\/b> étiquette sélectionnée/);
  assert.match(populated, /Imprimer 1/);
  assert.match(populated, /Vider la liste/);
  assert.doesNotMatch(populated, /Préparation des étiquettes/);
  assert.match(plural, /<b>3<\/b> étiquettes sélectionnées/);
  assert.match(plural, /Imprimer 3/);
  assert.match(empty, /Armoire 15°C/);
  assert.match(empty, /AAU-ATL-1\.001/);
  assert.doesNotMatch(empty, /AAU-ATL-1 · Armoire/);
});

test('Labels rows show only the current zone beneath the canonical box code', () => {
  const rowStart = labelsViewSource.indexOf('className="box-inventory-cell box-inventory-identity label-box-copy"');
  const rowEnd = labelsViewSource.indexOf('</span>', rowStart);
  assert.ok(rowStart > 0 && rowEnd > rowStart);
  const rowIdentity = labelsViewSource.slice(rowStart, rowEnd);
  assert.match(rowIdentity, /<BoxTrackingPreview[\s\S]*?code=\{box\.global_code\}/);
  assert.match(rowIdentity, /<small>\{box\.thermal_zone\?\.name \?\? labels\.noZone\}<\/small>/);
  assert.doesNotMatch(rowIdentity, /box\.strain\.code|box\.local_code|<strong>\{box\.global_code\}/);
});

test('Labels has no redundant subtitle or sheet preview', () => {
  assert.doesNotMatch(labelsViewSource, /label-sheet-preview|label-pages-preview|label-workspace-tabs|label-preview/);
  assert.doesNotMatch(labelsViewSource, /qrLabelSelectionTitle|label-selection-heading/);
  assert.match(labelsViewSource, /<PageLoader variant="labels" label=\{labels\.pageTitle\}/);
  assert.match(labelsViewSource, /qrLabelPrintCount\(selectedLabels\.length\)/);
});
