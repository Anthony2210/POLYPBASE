import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
const app = read('../src/App.tsx');
const zones = read('../src/components/ZonesView.tsx');
const css = read('../src/styles/pages/pilotage.css');
function sourceFunction(source, name) {
  const file = ts.createSourceFile('test.tsx', source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  let found;
  function visit(node) {
    if (ts.isFunctionDeclaration(node) && node.name?.text === name) found = node.getText(file);
    ts.forEachChild(node, visit);
  }
  visit(file);
  assert.ok(found, name);
  return found;
}
function evaluate(source, context) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  });
  vm.runInNewContext(outputText, context);
  return context;
}
const React = { createElement: (type, props, ...children) => ({ type, props: props ?? {}, children: children.flat() }) };
function nodes(tree) {
  return tree && typeof tree === 'object' ? [tree, ...tree.children.flatMap(nodes)] : [];
}

test('thermal modal initially focuses its dialog, not a native date segment', () => {
  const effects = [];
  const focusCalls = [];
  const listeners = {};
  const controls = [0, 1, 2, 3, 4].map(id => ({ focus: () => focusCalls.push(id) }));
  const dialog = { focus: options => focusCalls.push(options), querySelectorAll: () => controls, contains: el => controls.includes(el) };
  let refIndex = 0;
  const context = {
    React, ModalPortal: 'portal', PolypbaseIcon: 'icon',
    useRef: value => ({ current: refIndex++ === 0 ? dialog : value }),
    useLayoutEffect: effect => effects.push(effect), useEffect: effect => effects.push(effect),
    window: { addEventListener: (name, callback) => { listeners[name] = callback; }, removeEventListener() {} },
    document: { activeElement: dialog },
  };
  evaluate(sourceFunction(zones, 'TemperatureEntryModal'), context);
  let cancelled = false;
  const tree = context.TemperatureEntryModal({ date: '2026-10-03', value: '', isSaving: false, t: key => key, onCancel: () => { cancelled = true; } });
  effects.forEach(effect => effect());
  assert.equal(focusCalls.length, 1);
  assert.equal(focusCalls[0].preventScroll, true);
  const elements = nodes(tree);
  assert.equal(elements.find(el => el.props.role === 'dialog').props.tabIndex, -1);
  const date = elements.find(el => el.props.type === 'date');
  assert.equal(date.props.value, '2026-10-03');
  assert.equal(date.props.autoFocus, undefined);
  assert.equal(date.props.ref, undefined);
  listeners.keydown({ key: 'Tab', preventDefault() {} });
  assert.equal(focusCalls.at(-1), 0);
  context.document.activeElement = controls.at(-1);
  listeners.keydown({ key: 'Tab', preventDefault() {} });
  assert.equal(focusCalls.at(-1), 0);
  context.document.activeElement = controls[0];
  listeners.keydown({ key: 'Tab', shiftKey: true, preventDefault() {} });
  assert.equal(focusCalls.at(-1), 4);
  listeners.keydown({ key: 'Escape' });
  assert.equal(cancelled, true);
});

test('tablet recent limit includes portrait and updates on media changes', () => {
  const hook = read('../src/hooks/useRecentBoxLimit.ts').replace(/^import .*;\r?\n/gm, '').replace('export function', 'function');
  const query = read('../src/hooks/useIsTabletLayout.ts').match(/TABLET_LAYOUT_QUERY = '([^']+)'/)[1];
  for (const tablet of [true, false]) {
    let listener;
    let state;
    const media = { matches: tablet, addEventListener: (_, cb) => { listener = cb; }, removeEventListener() {} };
    const context = { TABLET_LAYOUT_QUERY: query, window: { matchMedia: value => { assert.equal(value, query); return media; } },
      useState: init => [init(), value => { state = value; }], useEffect: effect => effect() };
    evaluate(hook, context);
    assert.equal(context.useRecentBoxLimit(), tablet ? 6 : 5);
    media.matches = !tablet;
    listener();
    assert.equal(state, tablet ? 5 : 6);
  }
  assert.doesNotMatch(hook, /PHONE_LAYOUT_QUERY|orientation/);
  assert.match(css, /@media \(min-width: 760px\) and \(max-width: 1023px\), \(min-width: 760px\) and \(max-width: 1180px\) and \(pointer: coarse\) \{\s*\.recent-strip \{\s*display: grid;\s*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/);
});

test('recent bootstrap preserves access ordering, uniqueness and six-item cap', () => {
  // Recent ids come from the dashboard accesses themselves: no Box list is needed.
  const exports = {};
  evaluate(ts.transpileModule(read('../src/utils/boxCollection.ts'), {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText, { exports });
  const { getRecentBoxIds } = exports;
  const accesses = [{ object_id: 'unknown' }, { metadata: null }, { metadata: { box_id: '4' } }, { metadata: { box_id: 0 } },
    ...[4, 2, 4, 9, 1, 6, 3, 8].map(box_id => ({ object_id: `B${box_id}`, metadata: { box_id } }))];
  assert.deepEqual(Array.from(getRecentBoxIds({ recent_accesses: accesses })), [4, 2, 9, 1, 6, 3]);
  assert.deepEqual(Array.from(getRecentBoxIds({ recent_accesses: [] })), []);
  assert.deepEqual(Array.from(getRecentBoxIds({ recent_accesses: accesses.slice(0, 7) })), [4, 2]);
  assert.match(app, /currentIds\.filter\(\(currentId\) => currentId !== selectedBoxId\),\s*\]\.slice\(0, 6\)/);
  assert.match(app, /\.slice\(0, recentBoxLimit\);\s*\}, \[data\.boxes, recentBoxIds, recentBoxLimit\]\)/);
});

test('portrait search renders empty feedback and clearing; no search renders no results', () => {
  const source = sourceFunction(app, 'PilotageView');
  for (const search of ['missing', '']) {
    let index = 0;
    const context = { React, useState: init => [index++ === 0 ? 'search' : init, () => {}], userCanCreateBoxes: () => false,
      PHONE_RESULT_LIMIT: 5, PILOTAGE_RESULT_LIMIT: 15, SearchField: 'SearchField', SuggestionList: 'SuggestionList', RecentAccessList: 'RecentAccessList',
      BoxSearchStatus: 'BoxSearchStatus' };
    evaluate(source, context);
    let cleared;
    const props = { isPhoneLayout: true, search, searchResults: [], recentBoxes: [], boxCollectionStatus: 'ready', t: key => key, onSearch: value => { cleared = value; } };
    const tree = context.PilotageView(props);
    const results = nodes(tree).find(el => el.type === 'SuggestionList');
    assert.equal(Boolean(results), Boolean(search));
    if (results) { assert.equal(results.props.boxes.length, 0); results.props.onClear(); assert.equal(cleared, ''); }
  }
});

test('arrow selection reveals its row without moving focus or submitting', () => {
  let selected;
  let scroll;
  const context = { isPhoneLayout: false, visibleSuggestions: [{ id: 1 }, { id: 2 }], highlightedSuggestionIndex: 0,
    setHighlightedSuggestionIndex: value => { selected = value; },
    document: { getElementById: id => { assert.equal(id, 'box-search-result-2'); return { scrollIntoView: value => { scroll = value; } }; } } };
  const source = sourceFunction(app, 'PilotageView');
  evaluate(sourceFunction(source, 'highlightSuggestion') + sourceFunction(source, 'handleSearchKeyDown'), context);
  let prevented = false;
  context.handleSearchKeyDown({ key: 'ArrowDown', preventDefault() { prevented = true; } });
  assert.equal(selected, 1);
  assert.equal(prevented, true);
  assert.equal(scroll.block, 'nearest');
  assert.equal(scroll.inline, 'nearest');
});
