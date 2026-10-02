import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import ts from 'typescript';

const code = ts.transpileModule(readFileSync(new URL('../src/api/client.ts', import.meta.url), 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

export function resourceClient(globals = {}) {
  const storage = new Map([['polypbase.activeOrganizationId', '1']]);
  const exports = {};
  const window = {
    location: { origin: 'https://polypbase.test' },
    ...globals.window,
    localStorage: {
      getItem: (key) => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, value),
      removeItem: (key) => storage.delete(key),
    },
  };
  vm.runInNewContext(code, { exports, Headers, URL, AbortController, ...globals, window });
  return exports;
}
