import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';
import {
  appHarness,
  functionNode,
  tick,
} from './app-operation-test-harness.mjs';
import { loadI18n } from './i18n-runtime-test-helper.mjs';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const runtime = {
  jsx: (type, props) => ({ type, props }),
  jsxs: (type, props) => ({ type, props }),
};
function load(path, modules = {}, globals = {}) {
  const output = ts.transpileModule(read(path), {
    compilerOptions: {
      target: ts.ScriptTarget.ES2022,
      module: ts.ModuleKind.CommonJS,
      jsx: ts.JsxEmit.ReactJSX,
    },
  }).outputText;
  const exports = {};
  vm.runInNewContext(output, {
    exports,
    URL,
    ...globals,
    require(id) {
      assert.ok(Object.hasOwn(modules, id), `Unexpected dependency ${id}`);
      return modules[id];
    },
  });
  return exports;
}
const { fr, en } = loadI18n().translations;
const lucide = { Pencil: 'pencil', ExternalLink: 'external-link' };
const links = load('../src/components/DnaEvidenceLink.tsx', {
  'react/jsx-runtime': runtime,
  'lucide-react': lucide,
});
const validation = {
  id: 4,
  evidence_url: 'https://lab.example/proof',
  created_by: 'QA Validator',
  created_at: '2026-11-16T10:00:00Z',
  revision: 8,
};
function nodes(node) {
  if (!node || typeof node !== 'object') return [];
  if (Array.isArray(node)) return node.flatMap(nodes);
  return [node, ...nodes(node.props?.children)];
}
function harness(extra = {}, language = 'fr') {
  let index = 0;
  const slots = [];
  const calls = [],
    reads = [],
    confirmations = [];
  class ApiError extends Error {
    constructor(status) {
      super();
      this.status = status;
    }
  }
  const hooks = {
    useState(initial) {
      const i = index++;
      if (!(i in slots)) slots[i] = initial;
      return [
        slots[i],
        (value) => {
          slots[i] = typeof value === 'function' ? value(slots[i]) : value;
        },
      ];
    },
    useRef(initial) {
      const i = index++;
      slots[i] ??= { current: initial };
      return slots[i];
    },
    useCallback(fn) {
      index++;
      return fn;
    },
    useEffect() {
      index++;
    },
    useLayoutEffect() {
      index++;
    },
  };
  const Component = load(
    '../src/components/DnaValidationBadge.tsx',
    {
      react: hooks,
      'react/jsx-runtime': runtime,
      'react-dom': { createPortal: (x) => x },
      'lucide-react': lucide,
      '../api/client': { ApiError },
      '../hooks/useAnchoredPopover': {
        useAnchoredPopover: () => ({
          anchorRef: { current: null },
          panelRef: { current: null },
          position: {},
          id: 'dna',
        }),
      },
      '../utils/dateFormat': { formatDisplayDateTime: (value) => value },
      './ModalPortal': { default: 'portal' },
      './PolypbaseIcon': { default: 'polypbase-icon' },
      './DnaEvidenceLink': links,
    },
    { document: { body: {} }, HTMLButtonElement: class HTMLButtonElement {} },
  ).default;
  let tree;
  const props = {
    validation: null,
    canEdit: true,
    language,
    t: (key) => (language === 'fr' ? fr : en)[key],
    isOperationCurrent: () => true,
    onMutate: async (value) => {
      calls.push(value);
    },
    onReload: async () => {
      reads.push(true);
    },
    confirmAction: async (value) => {
      confirmations.push(value);
      return true;
    },
    ...extra,
  };
  const render = () => {
    index = 0;
    tree = Component(props);
    return tree;
  };
  render();
  return {
    render,
    props,
    calls,
    reads,
    confirmations,
    ApiError,
    all: () => nodes(tree),
    button: (label) =>
      nodes(tree).find(
        (n) =>
          n.type === 'button' &&
          (n.props['aria-label'] === label || n.props.children === label),
      ),
    form: () => nodes(tree).find((n) => n.type === 'form'),
    input: () => nodes(tree).find((n) => n.type === 'input'),
  };
}

for (const language of ['fr', 'en']) {
  const copy = language === 'fr' ? fr : en;
  test(`${language}: missing reader badge is static with no invitation`, () => {
    const h = harness({ canEdit: false }, language);
    assert.equal(h.all().filter((n) => n.type === 'button').length, 0);
    assert.ok(
      h
        .all()
        .some(
          (n) =>
            n.props?.role === 'img' &&
            n.props['aria-label'] === copy.dnaMissing,
        ),
    );
    assert.ok(!JSON.stringify(h.render()).includes(copy.dnaAdd));
  });
  test(`${language}: validated reader can inspect evidence, never edit`, () => {
    const h = harness({ canEdit: false, validation }, language);
    h.button(copy.dnaValidated).props.onClick();
    h.render();
    assert.ok(
      h
        .all()
        .some(
          (n) =>
            n.type === links.DnaEvidenceLink &&
            n.props.url === validation.evidence_url,
        ),
    );
    assert.ok(JSON.stringify(h.render()).includes('QA Validator'));
    assert.equal(h.button(copy.dnaEdit), undefined);
    assert.equal(h.button(copy.dnaRevoke), undefined);
  });
}

test('both badge states retain the DNA helix and differ by question mark or check', () => {
  for (const [value, marker] of [
    [null, 'M20 4'],
    [validation, 'm19 6'],
  ]) {
    const h = harness({ validation: value });
    const paths = h
      .all()
      .filter((n) => n.type === 'path')
      .map((n) => n.props.d);
    assert.ok(
      paths.some((d) => d.startsWith('M14 2.5')),
      'shield',
    );
    assert.ok(
      paths.some((d) => d.startsWith('M10 8c0 6')),
      'double helix',
    );
    assert.ok(
      paths.some((d) => d.startsWith(marker)),
      'non-color status marker',
    );
  }
});

test('reduced-motion popovers avoid visibility transitions that discard initial focus', () => {
  const css = read('../src/styles/components/popovers.css');
  assert.match(
    css,
    /@media \(prefers-reduced-motion: reduce\)\s*\{[\s\S]*?\.dna-validation-popover,\s*\.dna-validation-popover \*\s*\{\s*transition-property: none;/,
  );
});

test('URL validation trims, requires http(s), rejects empty/oversized/dangerous links', () => {
  for (const url of [
    '',
    ' ',
    'ftp://lab.example/a',
    'javascript:alert(1)',
    'https://',
    `https://a.example/${'a'.repeat(2000)}`,
  ])
    assert.equal(links.isDnaEvidenceUrl(url), false, url);
  assert.equal(links.isDnaEvidenceUrl('  https://lab.example/a  '), true);
  const link = links.DnaEvidenceLink({ url: validation.evidence_url });
  const children = [link.props.children].flat();
  assert.ok(
    children.some((child) => child?.props?.children === 'lab.example'),
    'shows the hostname',
  );
  assert.ok(
    children.some((child) => child?.type === 'external-link'),
    'shows the external link icon',
  );
  assert.equal(link.props.title, validation.evidence_url);
  assert.equal(link.props.target, '_blank');
  assert.equal(link.props.rel, 'noopener noreferrer');
  assert.equal(
    links.DnaEvidenceLink({ url: 'javascript:alert(1)' }).type,
    'span',
  );
});

test('editor validates inline, trims payload and never changes the badge optimistically', async () => {
  const h = harness();
  h.button(fr.dnaMissing).props.onClick();
  h.render();
  assert.equal(h.input().props.required, true);
  assert.equal(h.input().props.maxLength, 2000);
  h.form().props.onSubmit({ preventDefault() {} });
  h.render();
  assert.equal(h.calls.length, 0);
  assert.equal(h.input().props['aria-invalid'], true);
  h.input().props.onChange({
    target: { value: '  https://lab.example/new  ' },
  });
  h.render();
  h.form().props.onSubmit({ preventDefault() {} });
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0].evidence_url, 'https://lab.example/new');
  assert.equal(h.calls[0].operation, 'add');
  assert.ok(h.button(fr.dnaMissing));
  await tick();
  h.render();
  assert.equal(h.form(), undefined);
});

test('409 blocks retries until explicit GET reload, including duplicate creation', async () => {
  const h = harness();
  h.props.onMutate = async (value) => {
    h.calls.push(value);
    throw new h.ApiError(409);
  };
  h.button(fr.dnaMissing).props.onClick();
  h.render();
  h.input().props.onChange({ target: { value: validation.evidence_url } });
  h.render();
  h.form().props.onSubmit({ preventDefault() {} });
  await tick();
  h.render();
  assert.equal(h.calls.length, 1);
  assert.equal(h.reads.length, 0);
  assert.ok(h.button(fr.dnaReload));
  assert.equal(h.button(fr.dnaSave).props.disabled, true);
  h.form().props.onSubmit({ preventDefault() {} });
  await tick();
  assert.equal(h.calls.length, 1);
  await h.button(fr.dnaReload).props.onClick();
  await tick();
  h.render();
  assert.equal(h.reads.length, 1);
  assert.equal(h.calls.length, 1);
  assert.equal(h.form(), undefined);
});

test('edit and revoke use the server revision, revoke requires confirmation', async () => {
  const h = harness({ validation });
  h.button(fr.dnaValidated).props.onClick();
  h.render();
  h.button(fr.dnaEdit).props.onClick();
  h.render();
  h.input().props.onChange({ target: { value: 'https://lab.example/new' } });
  h.render();
  h.form().props.onSubmit({ preventDefault() {} });
  await tick();
  assert.equal(h.calls[0].expected_revision, 8);
  assert.equal(h.calls[0].operation, 'update');
  const r = harness({ validation });
  r.button(fr.dnaValidated).props.onClick();
  r.render();
  r.button(fr.dnaRevoke).props.onClick();
  await tick();
  assert.equal(r.confirmations.length, 1);
  assert.equal(r.confirmations[0].variant, 'danger');
  assert.equal(r.calls[0].operation, 'revoke');
  assert.equal(r.calls[0].expected_revision, 8);
});

for (const role of ['admin', 'lab_technician']) {
  test(`${role}: server capability exposes add/edit actions in both badge states`, () => {
    const h = harness({ canEdit: true });
    h.button(fr.dnaMissing).props.onClick();
    h.render();
    assert.ok(h.form());
    const v = harness({ canEdit: true, validation });
    v.button(fr.dnaValidated).props.onClick();
    v.render();
    assert.ok(v.button(fr.dnaEdit));
    assert.ok(v.button(fr.dnaRevoke));
  });
}

test('editor retains the revision observed when opened, even after detail refresh/replacement', async () => {
  const h = harness({ validation });
  h.button(fr.dnaValidated).props.onClick();
  h.render();
  h.button(fr.dnaEdit).props.onClick();
  h.render();
  h.props.validation = {
    ...validation,
    evidence_url: 'https://lab.example/other',
    revision: 12,
  };
  h.render();
  h.input().props.onChange({ target: { value: 'https://lab.example/draft' } });
  h.render();
  h.form().props.onSubmit({ preventDefault() {} });
  await tick();
  assert.equal(h.calls[0].expected_revision, 8);
  assert.equal(h.calls[0].operation, 'update');
});

function app() {
  const h = appHarness();
  for (const name of ['mutateDnaValidation', 'refreshDnaValidation'])
    h.evaluate(functionNode(name).getText());
  return h;
}
for (const [mutation, method, suffix, payload] of [
  [
    { operation: 'add', evidence_url: validation.evidence_url },
    'apiPost',
    '',
    { evidence_url: validation.evidence_url },
  ],
  [
    {
      operation: 'update',
      evidence_url: validation.evidence_url,
      expected_revision: 8,
    },
    'apiPatch',
    '',
    { evidence_url: validation.evidence_url, expected_revision: 8 },
  ],
  [
    { operation: 'revoke', expected_revision: 8 },
    'apiPost',
    'revoke/',
    { expected_revision: 8 },
  ],
]) {
  test(`App ${mutation.operation}: API write followed by guarded detail GET`, async () => {
    const h = app();
    const pending = h.context.mutateDnaValidation(7, mutation);
    assert.equal(h.requests[0].method, method);
    assert.equal(h.requests[0].url, `/api/boxes/7/dna-validation/${suffix}`);
    assert.equal(
      JSON.stringify(h.requests[0].payload),
      JSON.stringify(payload),
    );
    assert.equal(h.state.data.boxDetails[7], undefined);
    h.requests[0].resolve(null);
    await tick();
    assert.equal(h.requests[1].method, 'apiGet');
    assert.equal(h.requests[1].url, '/api/boxes/7/');
    h.requests[1].resolve({
      id: 7,
      dna_validation: mutation.operation === 'revoke' ? null : validation,
    });
    await pending;
    assert.equal(
      h.state.data.boxDetails[7].dna_validation?.revision ?? null,
      mutation.operation === 'revoke' ? null : 8,
    );
  });
}
test('App conflict performs no automatic reload or retry; explicit reload is GET only', async () => {
  const h = app();
  const pending = h.context.mutateDnaValidation(7, {
    operation: 'revoke',
    expected_revision: 8,
  });
  h.requests[0].reject(new Error('409'));
  await assert.rejects(pending);
  assert.equal(h.requests.length, 1);
  const reload = h.context.refreshDnaValidation(7);
  assert.equal(h.requests[1].method, 'apiGet');
  h.requests[1].resolve({ id: 7, dna_validation: null });
  await reload;
  assert.equal(h.requests.length, 2);
});
test('App read failure recovery is read-only; organization switch rejects stale writes', async () => {
  const h = app();
  const pending = h.context.mutateDnaValidation(7, {
    operation: 'revoke',
    expected_revision: 8,
  });
  h.requests[0].resolve(null);
  await tick();
  h.requests[1].reject(new Error('offline'));
  await pending;
  assert.ok(h.state.recovery);
  const recovered = h.state.recovery();
  assert.equal(h.requests[2].method, 'apiGet');
  h.requests[2].resolve({ id: 7, dna_validation: null });
  await recovered;
  const other = app();
  const stale = other.context.mutateDnaValidation(7, {
    operation: 'revoke',
    expected_revision: 8,
  });
  other.switchOrganization();
  other.requests[0].resolve(null);
  await assert.rejects(stale);
  assert.equal(other.requests.length, 1);
});

test('badge wiring uses only explicit detail capability and shared accessibility conventions', () => {
  const source = read('../src/components/DnaValidationBadge.tsx');
  const box = read('../src/components/BoxPage.tsx');
  assert.match(box, /canEdit=\{box.can_edit_dna_validation === true\}/);
  assert.doesNotMatch(source, /superuser|userCanWriteLabData|profile\.role/);
  for (const text of [
    'ModalPortal',
    'aria-modal="true"',
    "event.key === 'Escape'",
    "event.key !== 'Tab'",
    'trigger?.focus',
    'useAnchoredPopover',
  ])
    assert.ok(source.includes(text), text);
  assert.ok(
    box.indexOf('<DnaValidationBadge') > box.indexOf('{box.global_code}</h2>'),
  );
});

for (const language of ['fr', 'en']) {
  const copy = language === 'fr' ? fr : en;
  test(`${language}: validated popover is a titled info block with no close button`, () => {
    const h = harness({ validation }, language);
    h.button(copy.dnaValidated).props.onClick();
    h.render();
    const text = JSON.stringify(h.render());
    for (const value of [
      copy.dnaTitle,
      copy.dnaEvidenceLabel,
      copy.dnaAddedByLabel,
      copy.dnaDateLabel,
    ]) {
      assert.ok(text.includes(value), value);
    }
    assert.ok(!text.includes(copy.close), 'no close button copy');
    assert.equal(h.button(copy.close), undefined);
    assert.ok(
      h
        .all()
        .some(
          (n) =>
            n.type === 'dl' && n.props.className === 'dna-validation-facts',
        ),
    );
    assert.ok(
      h
        .all()
        .some(
          (n) =>
            n.type === 'time' && n.props.dateTime === validation.created_at,
        ),
    );
  });
}

test('popover header shows only the title, without a state chip', () => {
  const h = harness({ validation });
  h.button(fr.dnaValidated).props.onClick();
  h.render();
  assert.ok(
    !h.all().some((n) => n.props?.className === 'dna-validation-state'),
  );
  const source = read('../src/components/DnaValidationBadge.tsx');
  assert.doesNotMatch(source, /dnaStateValidated|dna-validation-state/);
  assert.doesNotMatch(
    read('../src/styles/components/popovers.css'),
    /dna-validation-state/,
  );
  assert.ok(
    !Object.hasOwn(fr, 'dnaStateValidated') &&
      !Object.hasOwn(en, 'dnaStateValidated'),
  );
});

test('editor reuses the shared dialog shell, header, field and button classes', () => {
  const h = harness();
  h.button(fr.dnaMissing).props.onClick();
  h.render();
  const text = JSON.stringify(h.render());
  for (const cls of [
    'box-dialog',
    'subculture-heading',
    'box-dialog-heading',
    'box-dialog-close',
    'box-dialog-form',
    'box-dialog-body',
    'box-dialog-actions',
    'secondary-button',
    'primary-button',
  ]) {
    assert.ok(text.includes(cls), cls);
  }
  const source = read('../src/components/DnaValidationBadge.tsx');
  assert.ok(source.includes('inline-error'), 'inline-error');
  assert.ok(
    h.all().some((n) => n.type === 'h2' && n.props.children === fr.dnaAdd),
    'title',
  );
  assert.ok(
    h.all().some((n) => n.type === 'p' && n.props.children === fr.dnaTitle),
    'kicker',
  );
  assert.ok(
    h
      .all()
      .some((n) => n.type === 'button' && n.props['aria-label'] === fr.cancel),
    'close button labelled',
  );
  assert.doesNotMatch(source, /dna-validation-modal|confirm-action-modal/);
  assert.doesNotMatch(
    read('../src/styles/pages/box-detail.css'),
    /dna-validation-modal/,
  );
});

test('URL error appears only after a save attempt or leaving a modified field', () => {
  const h = harness();
  h.button(fr.dnaMissing).props.onClick();
  h.render();
  const alert = () => h.all().find((n) => n.props?.role === 'alert');
  assert.equal(alert(), undefined, 'no error on open');
  h.input().props.onChange({ target: { value: 'not-a-url' } });
  h.render();
  assert.equal(alert(), undefined, 'no error while typing');
  h.input().props.onBlur({ relatedTarget: null });
  h.render();
  assert.equal(
    alert()?.props.children,
    fr.dnaInvalidUrl,
    'error after leaving a modified field',
  );
  const s = harness();
  s.button(fr.dnaMissing).props.onClick();
  s.render();
  s.form().props.onSubmit({ preventDefault() {} });
  s.render();
  assert.equal(
    s.all().find((n) => n.props?.role === 'alert')?.props.children,
    fr.dnaInvalidUrl,
    'error after save attempt',
  );
});

test('popover closes through the anchored popover contract, never a close button', () => {
  const source = read('../src/components/DnaValidationBadge.tsx');
  assert.ok(source.includes('useAnchoredPopover'));
  assert.doesNotMatch(source, /t\('close'\)/);
  // Re-clicking the badge toggles the popover; Escape and outside clicks are the hook's job.
  assert.match(
    source,
    /onClick=\{\(\) => validation \|\| blocked \? setOpen\(!open\)/,
  );
  assert.match(source, /const close = useCallback\(\(restoreFocus = false\)/);
});
