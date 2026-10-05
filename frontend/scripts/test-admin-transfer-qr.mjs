import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import { resourceClient } from './resource-test-harness.mjs';

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

const componentCode = compile('../src/components/AdminView.tsx', '\nexport { TransferCreateForm };');
const qr = load(compile('../src/utils/qrLabels.ts'), { require: () => resourceClient(), URL, window: { location: { origin: 'https://polypbase.test' } } });
const locales = {
  fr: load(compile('../src/i18n/fr.ts')).fr,
  en: load(compile('../src/i18n/en.ts')).en,
};
const failureKeys = {
  'popup-blocked': 'qrLabelPopupBlocked',
  'qr-retrieval': 'qrLabelQrUnavailable',
  'fallback-resource': 'qrLabelResourceUnavailable',
  'image-preparation': 'qrLabelImagePreparationFailed',
  preparation: 'qrLabelPreparationFailed',
};
const tick = () => new Promise((resolve) => setImmediate(resolve));

function harness(language = 'en') {
  const slots = [], effects = [], calls = [], transfers = [];
  let index = 0, tree;
  const hooks = {
    useState(initial) {
      const i = index++;
      if (!(i in slots)) slots[i] = typeof initial === 'function' ? initial() : initial;
      return [slots[i], (value) => { slots[i] = typeof value === 'function' ? value(slots[i]) : value; }];
    },
    useRef(initial) { const i = index++; return slots[i] ??= { current: initial }; },
    useMemo(fn) { return fn(); },
    useEffect(fn, deps) {
      const i = index++;
      if (!slots[i] || deps.some((dep, j) => dep !== slots[i][j])) { slots[i] = deps; effects.push(fn); }
    },
  };
  const jsx = (type, props) => ({ type, props });
  const unusedModules = [
    'lucide-react', '../api/client', '../utils/accountMembers', '../utils/dateFormat',
    '../utils/memberMutationLock', '../utils/memberFeedback', '../utils/stepValue', '../utils/zoneOccupancy',
    './AdminActionPanel', './AdminAuditSection', './BoxInventoryAdminSection', './ConfirmActionModal',
    './PageLoader', './PolypbaseIcon', './RowActionMenu', './SkeletonRows', './TaxonomyAdminSection',
  ];
  const modules = Object.fromEntries(unusedModules.map((name) => [name, {}]));
  Object.assign(modules, {
    react: hooks,
    'react/jsx-runtime': { jsx, jsxs: jsx },
    '../utils/userIdentity': load(compile('../src/utils/userIdentity.ts')),
    '../utils/errors': { getErrorMessage: (error) => error.message },
    '../utils/qrLabels': {
      ...qr,
      printQrLabels(items) {
        let resolve;
        const promise = new Promise((settle) => { resolve = settle; });
        calls.push({ items, resolve });
        return promise;
      },
    },
  });
  const component = load(componentCode, {
    URL,
    window: { location: { origin: 'https://polypbase.test' } },
    require(name) { assert.ok(Object.hasOwn(modules, name), `unexpected dependency: ${name}`); return modules[name]; },
  }).TransferCreateForm;
  const box = {
    id: 17, global_code: 'ATL-AAU-1.001', local_code: 'AAU-1', status: 'active',
    species: { scientific_name: 'Aurelia aurita', common_name: 'Moon jelly' },
    strain: { code: 'AAU-1' }, thermal_zone: { id: 3, name: 'Zone 15' },
    organization: { id: 1, name: 'Source' },
  };
  const props = {
    profile: { is_superuser: true, interface_language: language }, boxes: [box],
    organizations: [box.organization, { id: 2, name: 'Target' }],
    t(key) { assert.ok(Object.hasOwn(locales[language], key), `missing ${language} key: ${key}`); return locales[language][key]; },
    async onCreateTransfer(payload) {
      transfers.push(payload);
      if (h.backendError) throw new Error(h.backendError);
      return { id: 42, transfer_date: '2026-10-02', polyp_count: payload.polyp_count, notes: payload.notes };
    },
  };
  function nodes(node = tree) {
    if (!node || typeof node !== 'object') return [];
    return [node, ...[node.props?.children].flat(Infinity).filter(Boolean).flatMap((child) => nodes(child))];
  }
  function text(node = tree) {
    if (node == null || typeof node === 'boolean') return '';
    if (Array.isArray(node)) return node.map(text).join('');
    return typeof node === 'object' ? text(node.props?.children ?? null) : String(node);
  }
  function render() { index = 0; tree = component(props); while (effects.length) effects.shift()(); return tree; }
  function button(key) {
    const found = nodes().find((node) => node.type === 'button' && text(node) === props.t(key));
    assert.ok(found, `missing button ${key}`);
    return found;
  }
  function fill() {
    nodes().find((node) => node.type === 'select').props.onChange({ target: { value: '2' } });
    nodes().find((node) => node.type === 'input' && node.props.type === 'number').props.onChange({ target: { value: '5' } });
    render();
  }
  async function submit() { await tree.props.onSubmit({ preventDefault() {} }); render(); }
  async function prepare() { render(); fill(); await submit(); assert.ok(nodes().some((node) => node.props?.className === 'transfer-package')); }
  const h = { render, nodes, text, button, fill, submit, prepare, calls, transfers, box, props, backendError: null };
  return h;
}

for (const language of ['fr', 'en']) {
  for (const [reason, key] of Object.entries(failureKeys)) {
    test(`${language}: ${reason} is factual, retry keeps the prepared transfer and does not recreate it`, async () => {
      const h = harness(language);
      await h.prepare();
      const packageBefore = h.text(h.nodes().find((node) => node.props?.className === 'transfer-package').props.children[0]);
      h.button('adminTransferPrintLabel').props.onClick();
      assert.equal(h.calls.length, 1);
      assert.equal(h.calls[0].items.length, 1);
      assert.equal(JSON.stringify(h.calls[0].items[0]), JSON.stringify(qr.buildQrLabelItem(h.box)));
      h.calls[0].resolve({ status: 'failed', reason });
      await tick(); h.render();
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'alert')), locales[language][key]);
      assert.equal(h.nodes().filter((node) => node.props?.className === 'inline-error').length, 1);
      h.button('qrLabelRetry').props.onClick();
      h.render();
      assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
      assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), locales[language].qrLabelPreparing);
      assert.equal(JSON.stringify(h.calls[1].items), JSON.stringify(h.calls[0].items));
      h.calls[1].resolve({ status: 'prepared' });
      await tick(); h.render();
      assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
      assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), '');
      assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
      assert.equal(h.text(h.nodes().find((node) => node.props?.className === 'transfer-package').props.children[0]), packageBefore);
      assert.equal(h.transfers.length, 1);
    });
  }

  test(`${language}: cancellation is not an error and printing remains available`, async () => {
    const h = harness(language);
    await h.prepare();
    h.button('adminTransferPrintLabel').props.onClick();
    h.calls[0].resolve({ status: 'cancelled' });
    await tick(); h.render();
    assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
    assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
    assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'status')), '');
    h.button('adminTransferPrintLabel').props.onClick();
    assert.equal(h.calls.length, 2);
    h.calls[1].resolve({ status: 'prepared' });
    await tick(); h.render();
    assert.equal(h.transfers.length, 1);
  });
}

test('synchronous guard blocks duplicate clicks before rendering and while busy', async () => {
  const h = harness();
  await h.prepare();
  const click = h.button('adminTransferPrintLabel').props.onClick;
  click(); click();
  assert.equal(h.calls.length, 1);
  h.render();
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
  h.button('adminTransferPrintLabel').props.onClick(); click();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick(); h.render();
  const retry = h.button('qrLabelRetry').props.onClick;
  retry(); retry(); click();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'prepared' });
  await tick(); h.render();
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
});

test('old pending QR failure cannot attach to a newly prepared transfer package', async () => {
  const h = harness();
  await h.prepare();
  h.button('adminTransferPrintLabel').props.onClick();
  h.render();
  h.fill();
  assert.equal(h.nodes().find((node) => node.props?.type === 'submit').props.disabled, false);
  await h.submit();
  assert.equal(h.transfers.length, 2);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, true);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 1);
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.nodes().some((node) => node.type === 'button' && h.text(node) === locales.en.qrLabelRetry), false);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'failed', reason: 'qr-retrieval' });
  await tick(); h.render();
  assert.equal(h.text(h.nodes().find((node) => node.props?.role === 'alert')), locales.en.qrLabelQrUnavailable);
  h.button('qrLabelRetry').props.onClick();
  h.calls[2].resolve({ status: 'prepared' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.transfers.length, 2);
});

test('submission invalidates the old package synchronously before rendering or backend completion', async () => {
  const h = harness();
  await h.prepare();
  const oldClick = h.button('adminTransferPrintLabel').props.onClick;
  oldClick();
  h.fill();
  const createTransfer = h.props.onCreateTransfer;
  let resolveTransfer;
  h.props.onCreateTransfer = (payload) => new Promise((resolve) => {
    resolveTransfer = async () => resolve(await createTransfer(payload));
  });
  h.render();
  const submitting = h.submit();
  h.calls[0].resolve({ status: 'failed', reason: 'popup-blocked' });
  await tick();
  oldClick();
  assert.equal(h.calls.length, 1);
  h.render();
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  await resolveTransfer();
  await submitting;
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.button('adminTransferPrintLabel').props.disabled, false);
  h.button('adminTransferPrintLabel').props.onClick();
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve({ status: 'prepared' });
  await tick(); h.render();
});

test('backend transfer errors and QR preparation errors remain independent', async () => {
  const h = harness();
  await h.prepare();
  h.button('adminTransferPrintLabel').props.onClick();
  h.calls[0].resolve({ status: 'failed', reason: 'image-preparation' });
  await tick(); h.render();
  assert.equal(h.transfers.length, 1);
  assert.equal(h.nodes().filter((node) => node.props?.className === 'inline-error').length, 1);
  h.backendError = 'Transfer rejected by server';
  h.fill(); await h.submit();
  assert.equal(h.text(h.nodes().find((node) => node.props?.className === 'inline-error')), h.backendError);
  assert.equal(h.nodes().some((node) => node.props?.role === 'alert'), false);
  assert.equal(h.calls.length, 1);
  h.backendError = null;
  h.fill(); await h.submit();
  assert.equal(h.nodes().some((node) => node.props?.className === 'inline-error'), false);
  h.button('adminTransferPrintLabel').props.onClick();
  h.calls[1].resolve({ status: 'cancelled' });
  await tick(); h.render();
  assert.equal(h.nodes().some((node) => node.props?.className === 'inline-error'), false);
  assert.equal(h.transfers.length, 3);
});
