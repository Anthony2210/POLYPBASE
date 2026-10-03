import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const backSource = read('../src/components/DetailBackButton.tsx');
const zonesSource = read('../src/components/ZonesView.tsx');
const historySource = read('../src/components/ZoneMovementHistory.tsx');
const buttonsCss = read('../src/styles/components/buttons.css');
const zonesCss = read('../src/styles/pages/zones.css');

function load(source, imports) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    },
  });
  return exports;
}

function nodes(tree) {
  const result = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== 'object') return;
    result.push(node);
    visit(node.props?.children);
  }
  visit(tree);
  return result;
}

const ArrowLeft = () => null;
const ArrowDownToLine = () => null;
const ArrowUpFromLine = () => null;
const Placeholder = () => null;
const hooks = {
  useMemo: (factory) => factory(),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
};
const t = (key) => `translated:${key}`;

function views(isDesktopApp) {
  const DetailBackButton = load(backSource, {
    'react/jsx-runtime': jsxRuntime,
    'lucide-react': { ArrowLeft },
    '../hooks/useIsDesktopApp': { useIsDesktopApp: () => isDesktopApp },
  }).default;
  const common = {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    './DetailBackButton': { default: DetailBackButton },
    './BoxTrackingPreview': { default: Placeholder },
    './PageLoader': { default: Placeholder },
    '../utils/errors': { getErrorMessage: (error) => error.message },
  };
  const zones = load(zonesSource, {
    ...common,
    '../api/client': {},
    '../utils/dateFormat': { formatDisplayDate: (value) => value },
    '../utils/temperatureScale': {},
    './ModalPortal': { default: Placeholder },
    './PolypbaseIcon': { default: Placeholder },
    './ZoneMovementHistory': { ZoneRecentMovements: Placeholder },
  });
  const history = load(`${historySource}\nexport { MovementRows };`, {
    ...common,
    'lucide-react': { ArrowDownToLine, ArrowUpFromLine },
    '../api/client': {
      getStoredActiveOrganizationId: () => 'test',
      apiGet: () => assert.fail('Focused render tests must not make API requests'),
    },
    '../utils/dateFormat': { formatDisplayDateTime: (value) => value },
    './SkeletonRows': { default: Placeholder },
  });
  return { DetailBackButton, ...zones, ...history, ZoneMovementHistoryPage: history.default };
}

for (const desktop of [true, false]) {
  test(`shared back button preserves its label and callback (${desktop ? 'desktop' : 'touch'})`, () => {
    const { DetailBackButton } = views(desktop);
    let calls = 0;
    const onBack = () => calls++;
    const button = DetailBackButton({ label: 'Translated destination', onBack, desktopClassName: 'box-back-button' });
    assert.equal(button.type, 'button');
    assert.equal(button.props.type, 'button');
    assert.equal(button.props['aria-label'], 'Translated destination');
    assert.equal(button.props.title, 'Translated destination');
    assert.equal(button.props.onClick, onBack);
    button.props.onClick();
    assert.equal(calls, 1);
    if (desktop) {
      assert.equal(button.props.children, 'Translated destination');
      assert.match(button.props.className, /\btext-button\b/);
      assert.match(button.props.className, /\bbox-back-button\b/);
    } else {
      assert.match(button.props.className, /\bicon-button\b/);
      assert.match(button.props.className, /\bdetail-back-button--icon\b/);
      assert.equal(button.props.children.type, ArrowLeft);
      assert.equal(button.props.children.props['aria-hidden'], 'true');
      assert.doesNotMatch(button.props.className, /box-back-button/);
    }
    const defaultButton = DetailBackButton({ label: 'Back', onBack });
    assert.doesNotMatch(defaultButton.props.className, /undefined/);
  });

  for (const missing of [false, true]) {
    for (const [pageName, label] of [
      ['ZoneDetailPage', 'back'],
      ['ZoneBoxesPage', 'back'],
      ['ZoneMovementHistoryPage', 'back'],
    ]) {
      test(`${pageName} uses shared back control (${desktop ? 'desktop' : 'touch'}, ${missing ? 'missing' : 'populated'})`, () => {
        const loaded = views(desktop);
        const onBack = () => {};
        const tree = loaded[pageName]({
          zone: missing ? null : { id: 7, name: 'Test zone', organization: { name: 'Test lab' } },
          boxes: [], isLoading: false, language: 'en', direction: 'arrival', onBack, t,
        });
        const backs = nodes(tree).filter((node) => node.type === loaded.DetailBackButton);
        assert.equal(backs.length, 1);
        assert.equal(backs[0].props.label, t(label));
        assert.equal(backs[0].props.onBack, onBack);
        const button = loaded.DetailBackButton(backs[0].props);
        assert.equal(button.props.onClick, onBack);
        if (desktop) assert.match(button.props.className, /\bzone-back-button\b/);
        else assert.match(button.props.className, /\bdetail-back-button--icon\b/);
        if (missing) assert.ok(nodes(tree).some((node) => node.props?.children === t('noZone')));
        if (!missing && pageName === 'ZoneBoxesPage') {
          assert.ok(nodes(tree).some((node) => node.props?.className === 'entity-header entity-header--zone zone-sheet-hero zone-directory-hero'));
        }
      });
    }
  }
}

test('shared touch back style owns the circular 44px contract without changing UI stroke', () => {
  const rule = buttonsCss.match(/\.detail-back-button--icon\s*\{([^}]*)\}/)?.[1];
  assert.ok(rule);
  for (const property of ['width', 'min-width', 'height']) assert.match(rule, new RegExp(`(?:^|[;\\s])${property}: 44px;`));
  assert.match(rule, /border-radius: 50%;/);
  assert.match(rule, /padding: 0;/);
  assert.match(buttonsCss, /stroke-width: 1\.25;/);
  assert.doesNotMatch(zonesCss, /\.zone-page > \.zone-back-action/);
});

for (const direction of ['arrival', 'departure']) {
  for (const showRelatedZone of [false, true]) {
    test(`shared MovementRows keeps only the arrow directional (${direction}, ${showRelatedZone ? 'full' : 'compact'})`, () => {
      const { MovementRows } = views(false);
      const onOpenBox = () => {};
      const tree = MovementRows({
        direction, showRelatedZone, language: 'en', onOpenBox, t,
        movements: [{ box_id: 42, box_code: 'BOX-42', location_id: 9, event_type: 'transfer', occurred_at: '2026-10-03T09:00:00Z', related_zone_name: 'Related zone' }],
      });
      const row = nodes(tree).find((node) => node.props?.className === `zone-movement-row is-${direction}`);
      assert.ok(row);
      const icon = row.props.children[0];
      assert.equal(icon.type, direction === 'arrival' ? ArrowDownToLine : ArrowUpFromLine);
      assert.equal(icon.props.className, 'zone-movement-direction-icon');
      assert.equal(icon.props['aria-hidden'], 'true');
      assert.equal(row.props.style, undefined);
      assert.equal(nodes(tree).find((node) => node.type === Placeholder).props.code, 'BOX-42');
      assert.equal(nodes(tree).find((node) => node.type === 'time').props.children, '2026-10-03T09:00:00Z');
      assert.equal(nodes(tree).filter((node) => node.props?.className === 'zone-movement-related').length, showRelatedZone ? 1 : 0);
    });
  }
}

test('compact and full history share MovementRows and directional color selectors target only icons', () => {
  assert.equal((historySource.match(/<MovementRows\b/g) ?? []).length, 2);
  assert.match(historySource, /showRelatedZone=\{false\}/);
  assert.match(historySource, /showRelatedZone\s+t=\{t\}/);
  const directionalRules = [...zonesCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selector]) => /zone-movement-row\.is-(arrival|departure)/.test(selector));
  assert.equal(directionalRules.length, 2);
  for (const [, selector, declarations] of directionalRules) {
    const direction = selector.includes('is-arrival') ? 'arrival' : 'departure';
    assert.equal(selector.trim(), `.zone-movement-row.is-${direction} > .zone-movement-direction-icon`);
    assert.equal(declarations.trim(), `color: var(--color-${direction === 'arrival' ? 'success' : 'danger'});`);
  }
});
