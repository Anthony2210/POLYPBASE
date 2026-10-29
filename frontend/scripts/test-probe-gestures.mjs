import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import ts from 'typescript';

const source = readFileSync(new URL('../src/utils/probeGestures.ts', import.meta.url), 'utf8');
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
});
const gestures = {};
vm.runInNewContext(outputText, { exports: gestures });
const MINUTE = 60000;
const HOUR = 60 * MINUTE;
const window = { start: 0, end: 8 * HOUR };

for (const fraction of [0, 0.25, 0.5, 0.9, 1]) {
  test('zoom preserves the cursor anchor at fraction ' + fraction, () => {
    const anchor = window.start + fraction * (window.end - window.start);
    const next = gestures.zoomProbeAt(window, anchor, 0.5);
    assert.equal(next.end - next.start, 4 * HOUR);
    assert.equal((anchor - next.start) / (next.end - next.start), fraction);
    const restored = gestures.zoomProbeAt(next, anchor, 2);
    assert.deepEqual({ ...restored }, window);
  });
}

test('zoom respects 30 minutes and the 366-day ceiling', () => {
  assert.equal(gestures.zoomProbeAt(window, HOUR, 0.0001).end
    - gestures.zoomProbeAt(window, HOUR, 0.0001).start, 30 * MINUTE);
  const maximum = gestures.zoomProbeAt(window, 0, 1e10);
  assert.equal(maximum.end - maximum.start, 366 * 24 * HOUR);
});

test('pointer fractions account for margins and clamp outside the plot', () => {
  assert.equal(gestures.probePointerFraction(42, 42, 842), 0);
  assert.equal(gestures.probePointerFraction(442, 42, 842), 0.5);
  assert.equal(gestures.probePointerFraction(-50, 42, 842), 0);
  assert.equal(gestures.probePointerFraction(1000, 42, 842), 1);
  assert.ok(Number.isFinite(gestures.probePointerFraction(42, 42, 42)));
});

test('pinch zoom anchors the midpoint and preserves its original baseline', () => {
  const next = gestures.pinchProbeWindow(window, 0.25, 0.75, 0, 1);
  assert.deepEqual({ ...next }, { start: 2 * HOUR, end: 6 * HOUR });
  // A parent render may publish next; subsequent events still use window.
  const further = gestures.pinchProbeWindow(window, 0.25, 0.75, 0.1, 0.9);
  assert.deepEqual({ ...further }, { start: 1.5 * HOUR, end: 6.5 * HOUR });
  assert.deepEqual({ ...window }, { start: 0, end: 8 * HOUR });
  const returned = gestures.pinchProbeWindow(window, 0.25, 0.75, 0.25, 0.75);
  assert.deepEqual({ ...returned }, window);
});

test('moving both fingers keeps the original time under their midpoint', () => {
  const moved = gestures.pinchProbeWindow(window, 0.25, 0.75, 0.35, 0.85);
  assert.equal(moved.end - moved.start, 8 * HOUR);
  assert.ok(Math.abs(moved.start + 0.6 * (moved.end - moved.start) - 4 * HOUR) <= 1);
});

test('coincident fingers do not produce an invalid window', () => {
  assert.deepEqual({ ...gestures.pinchProbeWindow(window, 0.5, 0.5, 0.25, 0.75) }, window);
  assert.deepEqual({ ...gestures.pinchProbeWindow(window, 0.25, 0.75, 0.5, 0.5) }, window);
});

test('pinching respects the minimum duration', () => {
  const next = gestures.pinchProbeWindow({ start: 0, end: HOUR }, 0.49, 0.51, 0, 1);
  assert.equal(next.end - next.start, 30 * MINUTE);
});

test('wheel normalizes pixels, lines and pages, with bounded factors', () => {
  assert.equal(gestures.probeWheelFactor(16, 0, 300), gestures.probeWheelFactor(1, 1, 300));
  assert.equal(gestures.probeWheelFactor(300, 0, 300), gestures.probeWheelFactor(1, 2, 300));
  assert.ok(gestures.probeWheelFactor(-100, 0, 300) < 1);
  assert.ok(gestures.probeWheelFactor(100, 0, 300) > 1);
  assert.equal(gestures.probeWheelFactor(0, 0, 300), 1);
  assert.ok(Number.isFinite(gestures.probeWheelFactor(1e12, 0, 300)));
});

test('keyboard cursor moves by cadence and preserves timestamp zero', () => {
  assert.equal(gestures.probeCursorStep(window, 0, 1, 1), MINUTE);
  assert.equal(gestures.probeCursorStep(window, null, -1, 15), 4 * HOUR - 15 * MINUTE);
  assert.equal(gestures.probeCursorStep(window, 0, -1, 1), 0);
  assert.equal(gestures.probeCursorStep(window, window.end, 1, 60), window.end);
});

const chart = readFileSync(new URL('../src/components/ProbeTemperatureChart.tsx', import.meta.url), 'utf8');
test('chart keeps a focusable frame, shared cursor and SVG-only native wheel listener', () => {
  assert.match(chart, /tabIndex=\{0\}/);
  assert.match(chart, /aria-description=\{labels.keyboardDescription\}/);
  assert.match(chart, /svg.addEventListener\('wheel', wheel, \{ passive: false \}\)/);
  assert.match(chart, /svg.removeEventListener\('wheel', wheel\)/);
  assert.match(chart, /setPointerCapture\(event.pointerId\)/);
  assert.match(chart, /onLostPointerCapture=/);
  assert.doesNotMatch(chart, /event.isPrimary|setHoverIndex|setReadingIndex|type="range"/);
  assert.doesNotMatch(chart, /key=\{points|key=\{window/);
  assert.match(chart, /onDoubleClick=/);
  assert.match(chart, /onResetZoom\(\)/);
  assert.doesNotMatch(chart, /event.key === 'Escape'|stopPropagation/);
});

test('data paths remain split, clipped and gap-aware without discarding zero', () => {
  assert.match(chart, /splitProbeSegments\(points, resolutionMinutes\)/);
  assert.match(chart, /Math.abs\(candidate\[0\] - cursorTime\) < 1/);
  assert.match(chart, /point\[2\] \?\? point\[1\]/);
  assert.match(chart, /point\[3\] \?\? point\[1\]/);
  assert.match(chart, /<clipPath/);
  assert.match(chart, /<g clipPath=/);
  assert.doesNotMatch(chart, /geometry && summary \?/);
});

test('vertical and diagonal pinches use two-dimensional finger distance', () => {
  const vertical = gestures.pinchProbeWindow(window, 0.5, 0.5, 0.5, 0.5, 100, 200);
  assert.deepEqual({ ...vertical }, { start: 2 * HOUR, end: 6 * HOUR });
  const diagonal = gestures.pinchProbeWindow(window, 0.25, 0.75, 0.25, 0.75, 100, 200);
  assert.deepEqual({ ...diagonal }, { start: 2 * HOUR, end: 6 * HOUR });
});
