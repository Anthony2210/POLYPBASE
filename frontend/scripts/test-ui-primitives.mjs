import assert from 'node:assert/strict';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { ast as appAst, boxAst as ast, appHarness } from './app-operation-test-harness.mjs';
import { fakeClock, loadComponent, readComponent, stepperHarness } from './ui-primitives-test-harness.mjs';

const modules = { react: React, 'react/jsx-runtime': jsxRuntime };
for (const [name, className] of [['InfoPill', 'info-pill'], ['Metric', 'metric']]) {
  test(`${name} renders label/value markup and escapes supplied text`, () => {
    const Component = loadComponent(name, modules);
    const markup = renderToStaticMarkup(React.createElement(Component, {
      label: '<script> & "label"', value: '<b>0</b> & "value"',
    }));
    assert.equal(markup, `<span class="${className}"><small>&lt;script&gt; &amp; &quot;label&quot;</small><strong>&lt;b&gt;0&lt;/b&gt; &amp; &quot;value&quot;</strong></span>`);
  });
  for (const value of ['0', '—', '']) {
    test(`${name} preserves supplied ${JSON.stringify(value)} without substituting missing for zero`, () => {
      const Component = loadComponent(name, modules);
      assert.equal(renderToStaticMarkup(React.createElement(Component, { label: 'Polyps', value })),
        `<span class="${className}"><small>Polyps</small><strong>${value}</strong></span>`);
    });
  }
}
for (const [strong, expected] of [[undefined, 'info-pill'], [false, 'info-pill'], [true, 'info-pill is-strong']]) {
  test(`InfoPill strong=${strong} only changes its wrapper class`, () => {
    const Component = loadComponent('InfoPill', modules);
    assert.equal(renderToStaticMarkup(React.createElement(Component, { label: 'Zone', value: 'A1', strong })),
      `<span class="${expected}"><small>Zone</small><strong>A1</strong></span>`);
  });
}

function pointerEvent(log = []) {
  return { preventDefault() { log.push('preventDefault'); }, currentTarget: { focus(options) {
    assert.equal(options.preventScroll, true);
    assert.deepEqual(Object.keys(options), ['preventScroll']);
    log.push('focus');
  } } };
}
function pressed(props) { return props.className === 'count-stepper-button is-pressed'; }

test('StepperButton markup preserves type, accessible label and children without a duplicate click handler', () => {
  const h = stepperHarness();
  const child = { type: 'svg', props: { 'aria-hidden': true } };
  const props = h.render('Polyps +1', () => {}, { children: child });
  assert.equal(h.controls.get('Polyps +1').node.type, 'button');
  assert.equal(props.type, 'button');
  assert.equal(props['aria-label'], 'Polyps +1');
  assert.equal(props.children, child);
  assert.equal(props.className, 'count-stepper-button');
  assert.equal(props.onClick, undefined);
});

test('pointer prevents default, focuses without scroll, steps immediately and repeats at 435 ms then every 95 ms', () => {
  const h = stepperHarness();
  const log = [], steps = [];
  const step = () => { log.push('step'); steps.push(h.clock.now); };
  h.render('plus', step).onPointerDown(pointerEvent(log));
  assert.deepEqual(log, ['preventDefault', 'focus', 'step']);
  assert.ok(pressed(h.render('plus', step)));
  assert.deepEqual(steps, [0]);
  assert.equal(h.clock.pending, 1);
  h.clock.advance(339);
  assert.deepEqual(steps, [0]);
  h.clock.advance(1);
  assert.deepEqual(steps, [0], '340 ms starts the interval but does not itself step');
  assert.equal(h.clock.pending, 1);
  h.clock.advance(94);
  assert.deepEqual(steps, [0]);
  h.clock.advance(1);
  assert.deepEqual(steps, [0, 435]);
  h.clock.advance(190);
  assert.deepEqual(steps, [0, 435, 530, 625]);
  h.unmount('plus');
  assert.equal(h.clock.pending, 0);
});

for (const handler of ['onPointerUp', 'onPointerLeave', 'onPointerCancel', 'onBlur']) {
  for (const elapsed of [200, 340, 625]) {
    test(`${handler} clears pressed state and all timers at ${elapsed} ms`, () => {
      const h = stepperHarness();
      let steps = 0;
      const step = () => steps++;
      h.pointer('plus', step);
      h.clock.advance(elapsed);
      assert.equal(steps, elapsed === 625 ? 4 : 1);
      const props = h.render('plus', step);
      assert.ok(pressed(props), 'rerender must not run mount-only cleanup');
      props[handler]();
      assert.ok(!pressed(h.render('plus', step)));
      assert.equal(h.clock.pending, 0);
      const releasedSteps = steps;
      h.clock.advance(2000);
      assert.equal(steps, releasedSteps);
      h.render('plus', step)[handler]();
      assert.equal(h.clock.pending, 0, 'cleanup is idempotent');
      h.unmount('plus');
    });
  }
}
for (const elapsed of [200, 340, 625]) {
  test(`unmount cancels delayed/interval steps at ${elapsed} ms`, () => {
    const h = stepperHarness();
    let steps = 0;
    const step = () => steps++;
    h.pointer('plus', step);
    h.clock.advance(elapsed);
    assert.equal(steps, elapsed === 625 ? 4 : 1);
    h.render('plus', step);
    assert.equal(h.controls.get('plus').effectSetups, 1);
    h.unmount('plus');
    assert.equal(h.controls.get('plus').effectCleanups, 1);
    assert.equal(h.clock.pending, 0);
    const finalSteps = steps;
    h.clock.advance(2000);
    assert.equal(steps, finalSteps);
  });
}

for (const key of ['Enter', ' ', 'Space', 'Spacebar', 'Escape', 'ArrowUp', 'a']) {
  for (const repeat of [false, true]) {
    test(`keyboard ${JSON.stringify(key)}, repeat=${repeat} only steps for initial Enter/Space`, () => {
      const h = stepperHarness();
      let steps = 0, prevented = 0;
      const step = () => steps++;
      h.render('plus', step).onKeyDown({ key, repeat, preventDefault() { prevented++; } });
      const expected = !repeat && (key === 'Enter' || key === ' ') ? 1 : 0;
      assert.equal(steps, expected);
      assert.equal(prevented, expected);
      assert.ok(!pressed(h.render('plus', step)), 'keyboard must not start pointer pressed styling');
      assert.equal(h.clock.pending, 0);
      h.clock.advance(1000);
      assert.equal(steps, expected);
    });
  }
}
test('keyboard and context menu preserve an ongoing pointer hold without starting additional timers', () => {
  const h = stepperHarness();
  let steps = 0, prevented = 0;
  const step = () => steps++;
  h.pointer('plus', step);
  const props = h.render('plus', step);
  props.onKeyDown({ key: 'Enter', repeat: false, preventDefault() { prevented++; } });
  props.onContextMenu({ preventDefault() { prevented++; } });
  assert.equal(steps, 2);
  assert.equal(prevented, 2);
  assert.ok(pressed(h.render('plus', step)));
  assert.equal(h.clock.pending, 1);
  h.clock.advance(435);
  assert.equal(steps, 3);
  h.unmount('plus');
});
test('context menu alone prevents default without stepping or pressing', () => {
  const h = stepperHarness();
  let steps = 0, prevented = 0;
  const step = () => steps++;
  h.render('plus', step).onContextMenu({ preventDefault() { prevented++; } });
  assert.equal(prevented, 1);
  assert.equal(steps, 0);
  assert.ok(!pressed(h.render('plus', step)));
  assert.equal(h.clock.pending, 0);
});

test('restarting a pointer hold replaces its previous interval and resets the delay', () => {
  const h = stepperHarness();
  const steps = [];
  const step = () => steps.push(h.clock.now);
  h.pointer('plus', step);
  h.clock.advance(435);
  h.pointer('plus', step);
  assert.equal(h.clock.pending, 1);
  h.clock.advance(434);
  assert.deepEqual(steps, [0, 435, 435]);
  h.clock.advance(1);
  assert.deepEqual(steps, [0, 435, 435, 870]);
  h.unmount('plus');
});
for (const elapsed of [200, 435]) {
  test(`rerender at ${elapsed} ms retains the held callback until a new pointer hold`, () => {
    const h = stepperHarness();
    let oldSteps = 0, newSteps = 0;
    const oldStep = () => oldSteps++;
    const newStep = () => newSteps++;
    h.pointer('plus', oldStep);
    h.clock.advance(elapsed);
    h.render('plus', newStep);
    assert.equal(h.controls.get('plus').effectSetups, 1);
    h.clock.advance(530 - elapsed);
    assert.equal(oldSteps, 3);
    assert.equal(newSteps, 0, 'existing timeout/interval captures the original onStep');
    h.pointer('plus', newStep);
    assert.equal(newSteps, 1);
    h.clock.advance(435);
    assert.equal(oldSteps, 3);
    assert.equal(newSteps, 2);
    h.unmount('plus');
  });
}

test('separate instances own pressed state and timers, including identical children', () => {
  const h = stepperHarness();
  const steps = [0, 0];
  const first = () => steps[0]++;
  const second = () => steps[1]++;
  h.render('polyps', first, { children: '+' }).onPointerDown(pointerEvent());
  assert.ok(pressed(h.render('polyps', first)));
  assert.ok(!pressed(h.render('ephyrae', second, { children: '+' })));
  h.clock.advance(100);
  h.render('ephyrae', second, { children: '+' }).onPointerDown(pointerEvent());
  assert.equal(h.clock.pending, 2);
  h.clock.advance(435);
  assert.deepEqual(steps, [3, 2]);
  h.render('polyps', first).onPointerUp();
  assert.ok(!pressed(h.render('polyps', first)));
  assert.ok(pressed(h.render('ephyrae', second)));
  assert.equal(h.clock.pending, 1);
  h.unmount('polyps');
  h.clock.advance(95);
  assert.deepEqual(steps, [3, 3]);
  h.unmount('ephyrae');
  assert.equal(h.clock.pending, 0);
  h.clock.advance(1000);
  assert.deepEqual(steps, [3, 3]);
});

for (const elapsed of [200, 435]) {
  test(`unmounting one held instance at ${elapsed} ms leaves the sibling hold running`, () => {
    const h = stepperHarness();
    const steps = [0, 0];
    const first = () => steps[0]++;
    const second = () => steps[1]++;
    h.render('polyps', first).onPointerDown(pointerEvent());
    h.render('ephyrae', second).onPointerDown(pointerEvent());
    h.clock.advance(elapsed);
    const stoppedSteps = steps[0];
    h.unmount('polyps');
    assert.equal(h.clock.pending, 1);
    assert.ok(pressed(h.render('ephyrae', second)));
    h.clock.advance(530 - elapsed);
    assert.deepEqual(steps, [stoppedSteps, 3]);
    h.unmount('ephyrae');
    assert.equal(h.clock.pending, 0);
  });
}

test('mount effects are deferred until commit and mount-only cleanup survives rerenders', () => {
  const h = stepperHarness();
  let steps = 0;
  const step = () => steps++;
  h.render('plus', step, { flush: false });
  assert.equal(h.controls.get('plus').effectSetups, 0);
  h.flushEffects('plus');
  assert.equal(h.controls.get('plus').effectSetups, 1);
  h.pointer('plus', step);
  h.render('plus', () => steps++);
  assert.equal(h.controls.get('plus').effectSetups, 1);
  assert.equal(h.controls.get('plus').effectCleanups, 0);
  h.clock.advance(435);
  assert.equal(steps, 2);
  h.unmount('plus');
  assert.equal(h.clock.pending, 0);
});
for (const elapsed of [200, 435]) {
  test(`StrictMode-style effect setup/cleanup/setup still cleans a hold at ${elapsed} ms`, () => {
    const h = stepperHarness({ strictEffects: true });
    let steps = 0;
    const step = () => steps++;
    h.render('plus', step);
    assert.equal(h.controls.get('plus').effectSetups, 2);
    assert.equal(h.controls.get('plus').effectCleanups, 1);
    h.pointer('plus', step);
    h.clock.advance(elapsed);
    assert.equal(steps, elapsed === 435 ? 2 : 1);
    assert.ok(pressed(h.render('plus', step)));
    assert.equal(h.controls.get('plus').effectSetups, 2);
    h.unmount('plus');
    assert.equal(h.controls.get('plus').effectCleanups, 2);
    assert.equal(h.clock.pending, 0);
    h.clock.advance(1000);
    assert.equal(steps, elapsed === 435 ? 2 : 1);
  });
}

test('fake clock respects cancellation, interval boundaries and scheduling from a timeout', () => {
  const clock = fakeClock();
  const calls = [];
  const cancelled = clock.window.setTimeout(() => calls.push('cancelled'), 1);
  clock.window.clearTimeout(cancelled);
  clock.window.setTimeout(() => {
    calls.push(clock.now);
    const interval = clock.window.setInterval(() => {
      calls.push(clock.now);
      clock.window.clearInterval(interval);
    }, 95);
  }, 340);
  clock.advance(434);
  assert.deepEqual(calls, [340]);
  clock.advance(1000);
  assert.deepEqual(calls, [340, 435]);
  assert.equal(clock.pending, 0);
});

function nodes(root, predicate) {
  const found = [];
  function visit(node) {
    if (predicate(node)) found.push(node);
    ts.forEachChild(node, visit);
  }
  visit(root);
  return found;
}
function uses(name) {
  return nodes(ast, node => (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
    && node.tagName.getText(ast) === name);
}
function expression(element, name) {
  const attr = element.attributes.properties.find(node => ts.isJsxAttribute(node) && node.name.text === name);
  assert.ok(attr && ts.isJsxExpression(attr.initializer), `${name} must be a JSX expression`);
  return attr.initializer.expression.getText(ast);
}
function evaluateExpression(source, context) {
  const { outputText } = ts.transpileModule(`const result = (${source});`, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  });
  return vm.runInNewContext(`${outputText}\nresult;`, { ...context });
}

test('BoxPage imports default primitives and uses them without retained local implementations', () => {
  for (const name of ['InfoPill', 'Metric', 'StepperButton']) {
    const imports = ast.statements.filter(node => ts.isImportDeclaration(node)
      && node.moduleSpecifier.text === `./${name}`);
    assert.equal(imports.length, 1);
    assert.equal(imports[0].importClause.name.text, name);
    assert.ok(uses(name).length > 0);
    for (const file of [appAst, ast]) {
      assert.equal(nodes(file, node => (ts.isFunctionDeclaration(node) || ts.isVariableDeclaration(node))
        && node.name?.getText(file) === name).length, 0);
    }
  }
  assert.match(readComponent('StepperButton'), /useEffect\(\(\) => clearRepeat, \[\]\);/);
});

test('all six BoxPage stepper consumers retain their count/salinity updater wiring', () => {
  const elements = uses('StepperButton');
  assert.equal(elements.length, 6);
  const counts = appHarness().context;
  const expectations = [
    ['polypCount', 'decrementCountValue', '3', '2'],
    ['polypCount', 'incrementCountValue', '3', '4'],
    ['ephyraeCount', 'decrementCountValue', '3', '2'],
    ['ephyraeCount', 'incrementCountValue', '3', '4'],
    ['salinity', 'stepBiologicalSalinity', '32.15', '31.15'],
    ['salinity', 'stepBiologicalSalinity', '32.15', '33.15'],
  ];

  // Use the existing real count helpers; track salinity arguments separately so
  // this wiring check does not duplicate the salinity utility's own tests.
  for (let i = 0; i < elements.length; i++) {
    const [field, helper, initial, expected] = expectations[i];
    let form = { polypCount: '7', ephyraeCount: '8', salinity: '31', notes: 'keep', [field]: initial };
    const before = { ...form };
    const calls = [];
    const context = {
      SALINITY_STEP: 1,
      setForm(update) { form = update(form); },
      [helper](value, increment) {
        calls.push([value, increment]);
        if (helper === 'stepBiologicalSalinity') return expected;
        return counts[helper](value, increment);
      },
    };
    evaluateExpression(expression(elements[i], 'onStep'), context)();
    assert.deepEqual({ ...form }, { ...before, [field]: expected });
    assert.deepEqual(calls, [[initial, helper === 'decrementCountValue' ? undefined
      : helper === 'incrementCountValue' ? 1 : i === 4 ? -1 : 1]]);
    assert.match(expression(elements[i], 'aria-label'), new RegExp(i % 2 ? '\\+' : '-'));
  }
});

test('all six BoxPage InfoPill consumers pass their labels, values and emphasis to the extracted component', () => {
  const elements = uses('InfoPill');
  assert.equal(elements.length, 6);
  const Component = loadComponent('InfoPill', modules);
  for (const missing of [false, true]) {
    const context = {
      t: key => key,
      displayDate: { labelKey: 'createdOn', date: missing ? null : '2026-10-12' },
      formatDisplayDate: value => `date:${value}`,
      currentZone: missing ? undefined : { salinity_psu: 0, latest_temperature: { average_temperature_c: 0 } },
      box: { latest_salinity_psu: missing ? null : 0 },
      formatSalinity: value => value == null ? '—' : `${value} PSU`,
      formatTemperature: value => value == null ? '—' : `${value} °C`,
    };
    const expected = [
      ['createdOn', missing ? 'noDate' : 'date:2026-10-12'],
      ['createdOn', missing ? 'noDate' : 'date:2026-10-12'],
      ['zones', 'noZone'],
      ['zoneSalinityShort', missing ? '—' : '0 PSU'],
      ['boxSalinityShort', missing ? '—' : '0 PSU'],
      ['temperatureShort', missing ? '—' : '0 °C'],
    ];
    for (const [i, element] of elements.entries()) {
      const label = evaluateExpression(expression(element, 'label'), context);
      const value = evaluateExpression(expression(element, 'value'), context);
      const strong = element.attributes.properties.some(attr => ts.isJsxAttribute(attr) && attr.name.text === 'strong');
      assert.deepEqual([label, value], expected[i]);
      assert.equal(strong, i === 2);
      assert.equal(renderToStaticMarkup(React.createElement(Component, { label, value, strong })),
        `<span class="info-pill${strong ? ' is-strong' : ''}"><small>${label}</small><strong>${value}</strong></span>`);
    }
  }
});

test('BoxPage Metric values pass recorded zero and missing measurements distinctly to the formatter', () => {
  const elements = uses('Metric');
  assert.equal(elements.length, 2);
  for (const [i, field] of ['polyp_count', 'ephyrae_count'].entries()) {
    for (const showWeeklyMeasurementSummary of [false, true]) {
      for (const value of [0, null, undefined]) {
        const weeklyMeasurement = { [field]: value };
        const box = { latest_measurement: { [field]: value } };
        if (showWeeklyMeasurementSummary) box.latest_measurement[field] = 99;
        else weeklyMeasurement[field] = 99;
        const received = [];
        const result = evaluateExpression(expression(elements[i], 'value'), {
          weeklyMeasurement, showWeeklyMeasurementSummary, box,
          formatMeasurementCount(input) { received.push(input); return input == null ? '—' : String(input); },
        });
        assert.deepEqual(received, [value]);
        assert.equal(result, value == null ? '—' : '0');
      }
    }
  }
});
