import assert from 'node:assert/strict';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { discoverTestEntries } from './run-tests.mjs';

function fixture(t, files) {
  const directory = mkdtempSync(join(tmpdir(), 'polypbase-test-discovery-'));
  t.after(() => rmSync(directory, { recursive: true, force: true }));
  for (const [name, source] of Object.entries(files)) writeFileSync(join(directory, name), source);
  return directory;
}

test('discovery sorts all test modules and excludes helpers, checks and directories', (t) => {
  const directory = fixture(t, {
    'test-z.mjs': '', 'test-a.mjs': '', 'run-tests.mjs': '',
    'app-operation-test-harness.mjs': '', 'check-css.mjs': '', 'test-other.txt': '',
  });
  mkdirSync(join(directory, 'test-directory.mjs'));
  assert.deepEqual(discoverTestEntries(directory), {
    files: ['test-a.mjs', 'test-z.mjs'], entries: ['test-a.mjs', 'test-z.mjs'],
  });
});

test('discovery retains bundled tests without executing imported modules again', (t) => {
  const directory = fixture(t, {
    'test-labels.mjs': "import './test-transfer.mjs';\nimport './test-resources.mjs';",
    'test-transfer.mjs': "import './resource-test-harness.mjs';",
    'test-resources.mjs': '', 'resource-test-harness.mjs': '', 'test-independent.mjs': '',
  });
  assert.deepEqual(discoverTestEntries(directory), {
    files: ['test-independent.mjs', 'test-labels.mjs', 'test-resources.mjs', 'test-transfer.mjs'],
    entries: ['test-independent.mjs', 'test-labels.mjs'],
  });
});

test('transitive and repeated imports within one worker execute each module once', (t) => {
  const directory = fixture(t, {
    'test-a.mjs': "import './test-b.mjs';\nimport './test-c.mjs';\nimport './test-c.mjs';",
    'test-b.mjs': "export * from './test-c.mjs';", 'test-c.mjs': '',
  });
  assert.deepEqual(discoverTestEntries(directory).entries, ['test-a.mjs']);
});

test('discovery does not mistake comments and strings for test imports', (t) => {
  const directory = fixture(t, {
    'test-a.mjs': "// import './test-b.mjs';\nconst example = \"import './test-b.mjs';\";",
    'test-b.mjs': '',
  });
  assert.deepEqual(discoverTestEntries(directory).entries, ['test-a.mjs', 'test-b.mjs']);
});

test('discovery rejects shared tests that would execute in two isolated workers', (t) => {
  const directory = fixture(t, {
    'test-a.mjs': "import './test-shared.mjs';",
    'test-b.mjs': "import './test-shared.mjs';", 'test-shared.mjs': '',
  });
  assert.throws(() => discoverTestEntries(directory), /test-shared\.mjs would execute twice through test-a\.mjs and test-b\.mjs/);
});

test('discovery rejects cycles with no entry point rather than dropping tests', (t) => {
  const directory = fixture(t, {
    'test-a.mjs': "import './test-b.mjs';", 'test-b.mjs': "import './test-a.mjs';",
    'test-independent.mjs': '',
  });
  assert.throws(() => discoverTestEntries(directory), /no entry point \(import cycle\): test-a\.mjs, test-b\.mjs/);
});

test('discovery rejects missing and non-sibling test imports', (t) => {
  for (const specifier of ['./test-missing.mjs', '../test-a.mjs']) {
    const directory = fixture(t, { 'test-a.mjs': `import '${specifier}';` });
    assert.throws(() => discoverTestEntries(directory), /test import must name a discovered sibling module/);
  }
});

test('discovery rejects an empty suite instead of reporting success', (t) => {
  const directory = fixture(t, { 'check-css.mjs': '' });
  assert.throws(() => discoverTestEntries(directory), /No test modules found/);
});
