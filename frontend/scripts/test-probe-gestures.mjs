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
