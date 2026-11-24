import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

function compile(path, suffix = '') {
  return ts.transpileModule(readFileSync(new URL(path, import.meta.url), 'utf8') + suffix, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
}

function load(code, globals = {}) {
  const exports = {};
  vm.runInNewContext(code, { exports, ...globals });
  return exports;
}

const componentCode = compile('../src/components/AdminView.tsx', '\nexport { csvCell, downloadTransferCsv, downloadTransferCsvExample };');

test('legacy Transfer CSV sanitizes text and quotes the actual download cells', () => {
  let csv;
  const filenames = [];
  const { csvCell, downloadTransferCsv, downloadTransferCsvExample } = load(componentCode, {
    require: () => ({}),
    URL: { createObjectURL: () => 'blob:test', revokeObjectURL() {} },
    Blob: class { constructor(parts) { [csv] = parts; } },
    document: {
      body: { appendChild() {} },
      createElement: () => ({ click() { filenames.push(this.download); }, remove() {} }),
    },
  });
  for (const prefix of ['=', '+', '-', '@', '\t']) {
    assert.equal(csvCell(prefix), `'${prefix}`);
    assert.equal(csvCell(`box${prefix}`), `box${prefix}`);
  }
  assert.equal(csvCell('\r'), `"'\r"`);
  assert.equal(csvCell("'@"), "'@");
  assert.equal(csvCell(0), '0');
  assert.equal(csvCell(-1.25), '-1.25');
  assert.equal(csvCell(null), '');
  assert.equal(csvCell(undefined), '');
  assert.equal(csvCell('a\rb'), '"a\rb"');

  const box = { global_code: 'BOX.01', species: {}, strain: {}, latest_salinity_psu: '0.00' };
  downloadTransferCsv({
    box,
    transfer: { transfer_date: '2026-10-02' },
    exportData: {
      format: 'polypbase.box_transfer.v1',
      transfer: {
        id: 42, date: '2026-10-02', source_organization: { name: 'Source' },
        target_organization: { name: 'Target' }, polyp_count: 0,
        notes: '@Note, "sample"\r\nline', parent_box_codes: [],
      },
      box,
    },
  }, false);
  assert.ok(csv.startsWith('\uFEFFformat,transfer_id,transfer_date,'));
  assert.equal(csv.slice(csv.indexOf('\r\n') + 2), [
    'polypbase.box_transfer.v1', '42', '2026-10-02', 'Source', 'Target', '', '0',
    `"'@Note, ""sample""\r\nline"`, 'BOX.01', '', '', '', '', '', '', '', '', '', '0.00', '', '',
  ].join(',') + '\r\n');

  downloadTransferCsvExample(true);
  assert.ok(csv.startsWith('\uFEFFFormat,Identifiant transfert,Date transfert,'));
  assert.ok(csv.endsWith(',https://example.org/bac/1/\r\n'));
  assert.deepEqual(filenames, ['transfert_BOX.01_2026-10-02.csv', 'exemple_transfert_polypbase.csv']);
});
