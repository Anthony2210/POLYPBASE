import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const manifest = JSON.parse(readFileSync(new URL('../dist/.vite/manifest.json', import.meta.url), 'utf8'));

function closure(roots, includeDynamic = false) {
  const visited = new Set();
  const pending = [...roots];
  while (pending.length) {
    const key = pending.pop();
    if (visited.has(key)) continue;
    const chunk = manifest[key];
    assert.ok(chunk, `Missing manifest entry ${key}`);
    visited.add(key);
    pending.push(...(chunk.imports ?? []));
    if (includeDynamic) pending.push(...(chunk.dynamicImports ?? []));
  }
  return visited;
}

// App is imported on normal application entry even though it is async in Vite.
const bootstrap = closure(['index.html', 'src/App.tsx']);
const reachable = closure(['index.html'], true);
const eagerFiles = new Set([...bootstrap].map(key => manifest[key].file));
const decoder = Object.keys(manifest).filter(key => key.includes('node_modules/@zxing/browser/'));
assert.ok(decoder.length > 0, 'Missing ZXing decoder in the manifest');
for (const key of [...decoder, 'src/components/InteractiveLineageGraph.tsx', 'src/components/AdminView.tsx']) {
  assert.ok(reachable.has(key), `${key} is no longer reachable from the application`);
  assert.ok(!eagerFiles.has(manifest[key].file), `${key} moved into the startup dependency closure`);
}
console.log('ZXing, lineage graph and administration are outside the startup chunks.');
