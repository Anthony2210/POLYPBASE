import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8');
const labelsCss = read('styles/pages/exports-labels.css');
const modalsCss = read('styles/components/modals.css');
const phoneCss = read('styles/responsive/phone.css');
const scanner = read('components/TabletQrScanner.tsx');
const labels = read('components/LabelsView.tsx');
const app = read('App.tsx');
const phoneMedia = '@media (max-width: 759px), (max-width: 900px) and (orientation: portrait)';

function mediaBlock(source, header) {
  const start = source.lastIndexOf(header);
  assert.notEqual(start, -1, `Missing ${header}`);
  const opening = source.indexOf('{', start);
  let depth = 1;
  let end = opening + 1;
  for (; depth && end < source.length; end += 1) {
    if (source[end] === '{') depth += 1;
    if (source[end] === '}') depth -= 1;
  }
  assert.equal(depth, 0);
  return source.slice(opening + 1, end - 1);
}

function rule(source, selector) {
  const rules = [...source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const match = rules.find((entry) => entry[1].trim() === selector);
  assert.ok(match, `Missing ${selector}`);
  return match[2];
}

const phoneLabels = mediaBlock(labelsCss, phoneMedia);
const phoneModal = mediaBlock(modalsCss, phoneMedia);

test('phone selection dock clears raised navigation and the safe area', () => {
  const clearance = 'var(--phone-nav-clearance, calc(100px + env(safe-area-inset-bottom)))';
  assert.ok(rule(phoneLabels, '.labels-page .label-selection-dock').includes(`bottom: calc(${clearance} + var(--space-3))`));
  const page = rule(phoneLabels, '.labels-page.has-selection');
  assert.ok(page.includes(`--label-selection-clearance: calc(150px + ${clearance})`));
  assert.match(page, /padding-bottom:\s*var\(--label-selection-clearance\)/);
  assert.match(rule(phoneLabels, '.labels-page.has-selection :is(.label-species-group, .label-box-row)'), /scroll-margin-block-end:\s*var\(--label-selection-clearance\)/);
});

test('phone clear and print share available width and allow translated labels to wrap', () => {
  assert.match(rule(phoneLabels, '.labels-page .label-selection-bar'), /width:\s*100%/);
  assert.match(rule(phoneLabels, '.labels-page .label-selection-summary'), /white-space:\s*normal/);
  assert.match(rule(phoneLabels, '.labels-page .label-selection-actions'), /grid-template-columns:\s*minmax\(0,\s*1fr\) minmax\(0,\s*1fr\)/);
  const buttons = rule(phoneLabels, '.labels-page .label-selection-actions button');
  assert.match(buttons, /min-height:\s*var\(--touch-target\)/);
  assert.match(buttons, /white-space:\s*normal/);
  assert.match(buttons, /overflow-wrap:\s*anywhere/);
  assert.doesNotMatch(phoneLabels, /(?:label-selection-clear|label-selection-print)[^{}]*\{[^}]*display:\s*none/);
});

test('species rows remain single-column and collapsed rows stay hidden', () => {
  assert.match(rule(phoneLabels, '.labels-page .label-species-rows:not([hidden])'), /grid-template-columns:\s*1fr/);
  assert.match(rule(labelsCss, '.labels-page .label-species-rows[hidden]'), /display:\s*none/);
  assert.match(labels, /className="label-species-rows"[^>]*hidden=\{!isExpanded\}/);
});

test('preview floor is phone-only, works in idle and live states, and is bounded on short screens', () => {
  const preview = rule(phoneModal, '.qr-search-modal .scanner-preview');
  assert.match(preview, /min-height:\s*min\(72vw,\s*36dvh,\s*320px\)/);
  assert.equal(modalsCss.includes('.tablet-scanner-modal .scanner-preview'), false);
  for (const [width, height] of [[320, 568], [360, 780], [390, 844], [430, 932], [740, 360]]) {
    const floor = Math.min(width * 0.72, height * 0.36, 320);
    assert.ok(floor <= height * 0.36);
    assert.ok(floor >= 120);
    if (height >= 568) assert.ok(floor > Math.min(width * 0.48, 220));
  }
  assert.match(rule(phoneModal, '.qr-search-modal .scanner-live-label'), /max-width:\s*calc\(100% - 2 \* var\(--space-2\)\)/);
  assert.match(rule(phoneModal, '.qr-search-modal .scanner-live-label'), /overflow-wrap:\s*anywhere/);
});

test('phone modal keeps safe-area bounds, scrollable lookup, close and keyboard access', () => {
  assert.match(rule(phoneCss, '.qr-search-backdrop'), /safe-area-inset-top[\s\S]*safe-area-inset-bottom/);
  assert.match(rule(phoneCss, '.qr-search-modal'), /100dvh[\s\S]*safe-area-inset-top[\s\S]*safe-area-inset-bottom/);
  assert.match(rule(read('styles/components/qr-search.css'), '.qr-search-content'), /overflow-y:\s*auto/);
  const modal = app.slice(app.indexOf('function QrSearchModal('), app.indexOf('function PilotageView('));
  assert.match(modal, /event\.key === 'Escape'/);
  assert.match(modal, /event\.key !== 'Tab'/);
  assert.match(modal, /returnFocusRef\.current\?\.focus/);
  assert.match(modal, /<TabletQrScanner\s+autoStart/);
  assert.match(modal, /className="qr-search-manual"/);
  assert.match(modal, /className="modal-close-button"[^>]*onClick=\{onClose\}/);
});

test('scan state retains real camera checks, authorized-box resolution and cleanup', () => {
  assert.match(scanner, /if \(!window\.isSecureContext\)/);
  assert.match(scanner, /navigator\.mediaDevices\?\.getUserMedia/);
  assert.match(scanner, /decodeFromConstraints\(/);
  assert.match(scanner, /getBoxIdFromQrValue\(scannedValue, boxesRef\.current\)/);
  assert.match(scanner, /if \(scannedBoxId != null\) \{\s*selectScannedBox\(scannedBoxId\);\s*return;\s*\}/);
  assert.match(scanner, /if \(isCancelled \|\| hasDetectedBox\)\s*\{\s*controls\.stop\(\)/);
  assert.match(scanner, /return \(\) => \{\s*isCancelled = true;\s*stopQrScanner\(scannerControlsRef\)/);
  assert.match(scanner, /setMessage\(permission\);\s*setIsScanning\(false\)/);
  assert.match(app, /setIsPhoneQrOpen\(false\);\s*setIsTabletScannerOpen\(false\);\s*\}, \[activeOrganizationId\]\)/);
});

test('clear and print preserve existing selection and preparation state contracts', () => {
  assert.match(labels, /selectedLabels\.length > 0 \? \(/);
  assert.match(labels, /className="label-selection-clear"[\s\S]*?onClick=\{onClearQrLabelSelection\}/);
  assert.match(labels, /className="primary-button label-selection-print"[\s\S]*?disabled=\{isPreparing\}[\s\S]*?onClick=\{\(\) => void handlePrint\(\)\}/);
  assert.match(labels, /if \(preparingRef\.current \|\| !selectedLabels\.length\) return/);
  assert.match(labels, /const result = await printQrLabels\(selectedLabels, printSettings\)/);
  assert.match(labels, /setPrintFailure\(result\.status === 'failed' \? result : null\)/);
});
