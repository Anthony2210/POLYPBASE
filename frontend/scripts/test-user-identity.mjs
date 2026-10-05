import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import React from 'react';
import * as jsxRuntime from 'react/jsx-runtime';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';

const read = path => readFileSync(new URL(path, import.meta.url), 'utf8');
function compile(source, globals = {}, dependencies = {}) {
  const exports = {};
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  vm.runInNewContext(outputText, { exports, ...globals, require(name) {
    if (name === 'react/jsx-runtime') return jsxRuntime;
    assert.ok(Object.hasOwn(dependencies, name), `Unexpected dependency: ${name}`);
    return dependencies[name];
  } });
  return exports;
}
function functions(path, names, globals) {
  const source = read(path);
  const ast = ts.createSourceFile(path, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const selected = names.map(name => {
    const node = ast.statements.find(node => ts.isFunctionDeclaration(node) && node.name?.text === name);
    assert.ok(node, `Missing function ${name} in ${path}`);
    return node.getText(ast);
  });
  return compile(`${selected.join('\n')}\nexport { ${names.join(', ')} };`, globals);
}
const identity = compile(read('../src/utils/userIdentity.ts'));
const dateFormat = compile(read('../src/utils/dateFormat.ts'));
const catalogs = {
  fr: compile(read('../src/i18n/fr.ts')).fr,
  en: compile(read('../src/i18n/en.ts')).en,
};
const createTranslator = language => key => catalogs[language][key] ?? key;
const audit = compile(read('../src/utils/auditPresentation.ts'), {}, { './dateFormat': dateFormat, './userIdentity': identity });
const timeline = compile(read('../src/utils/biologicalTimeline.ts'));
const current = Object.freeze({ first_name: '  ÉLISE-ANNE ', last_name: ' du  Pont-Müller ', email: 'elise@example.org' });
const readable = 'Élise-Anne DU PONT-MÜLLER';
const raw = 'legacy.tech-42';

const cases = [
  [{ first_name: '  JEAN  LUC ', last_name: '', email: 'fallback@example.org' }, 'Jean Luc'],
  [{ first_name: '', last_name: ' du pont-müller ', email: 'fallback@example.org' }, 'DU PONT-MÜLLER'],
  [{ first_name: '', last_name: '', email: '  Person.Name@example.org ' }, 'Person.Name@example.org'],
  [{ first_name: "jean-pIERRE d’ÉTÉ", last_name: "de l’hôpital", email: '' }, 'Jean-Pierre D’Été DE L’HÔPITAL'],
  [current, readable],
  [{ first_name: '  ', last_name: '\t', email: ' ' }, ''],
  [null, ''],
  [undefined, ''],
];
for (const [value, expected] of cases) {
  test(`structured identity: ${expected || 'absent'} preserves name boundaries`, () => {
    const before = JSON.stringify(value);
    assert.equal(identity.formatReadableUserIdentity(value), expected);
    assert.equal(JSON.stringify(value), before);
  });
}
test('unknown raw compatibility fields cannot supply a readable identity', () => {
  assert.equal(identity.formatReadableUserIdentity({ first_name: '', last_name: '', email: '', username: raw, full_name: 'Untrusted Name' }), '');
});

const members = functions('../src/components/AdminView.tsx', ['getMemberDisplayName', 'buildTransferExportData'], {
  ...identity, URL, window: { location: { origin: 'https://polypbase.test' } },
});
const profile = functions('../src/components/ProfileView.tsx', ['formatProfileName'], identity);
test('member and profile display share the structured formatter, never full_name guessing', () => {
  for (const [value, expected] of cases.filter(([value]) => value)) {
    const account = { ...value, full_name: raw, username: raw };
    assert.equal(members.getMemberDisplayName(account), expected);
    assert.equal(profile.formatProfileName(account), expected || '—');
  }
  assert.doesNotMatch(read('../src/components/AdminView.tsx'), /\.full_name|displayName\.split/);
});
test('transfer export preserves the readable prepared_by field verbatim', () => {
  for (const prepared_by of [readable, 'person@example.org', null]) {
    const result = members.buildTransferExportData({ prepared_by }, { id: 7 }, { id: 2, name: 'Partner' });
    assert.equal(result.transfer.prepared_by, prepared_by);
  }
  assert.match(read('../src/components/AdminView.tsx'), /\['prepared_by', 'Préparateur', transfer\.prepared_by\]/);
});

const chart = functions('../src/components/BiologicalTrendChart.tsx', ['buildMeasurementDetailLines'], {
  ...identity,
  findChartLocationAtDate: () => null,
  chartBiologicalValues: point => [{ kind: 'polyps', label: 'Polyps', value: String(point.polypCount) }],
});
const nullComponent = () => null;
const auditTimeline = compile(read('../src/components/AuditTimeline.tsx'), {}, {
  '../utils/auditPresentation': audit,
  './BoxTrackingPreview': { default: nullComponent },
});
const rowGlobals = {
  ...identity, ...audit, ...auditTimeline,
  AuditLinkedActionsPopover: nullComponent, BoxTrackingPreview: nullComponent,
};
const admin = functions('../src/components/AdminAuditSection.tsx', ['AdminAuditRow'], rowGlobals);
const personal = functions('../src/components/ProfileActionsSection.tsx', ['ProfileActionRow'], rowGlobals);
const lineage = functions('../src/components/LineageModal.tsx', ['LineageGroup', 'formatEventDate'], {
  ...identity, createTranslator, getBoxStatusPresentation: () => ({ tone: 'active', label: 'Active' }),
});
for (const language of ['fr', 'en']) {
  const t = createTranslator(language);
  test(`${language}: measurement and subculture chart details ignore raw enteredBy and preserve zero`, () => {
    for (const user_identity of [current, null, undefined]) {
      for (const point of [{ polypCount: 0 }, { kind: 'subculture', polypCount: 0 }]) {
        const lines = chart.buildMeasurementDetailLines({ ...point, enteredBy: raw, user_identity }, {
          polyps: t('polyps'), enteredBy: t('historyEnteredBy'), historicalUser: t('historicalUser'),
        }, []);
        assert.equal(lines.find(line => line.kind === 'user').value, user_identity ? readable : t('historicalUser'));
        assert.equal(lines[0].value, '0');
        assert.equal(JSON.stringify(lines).includes(raw), false);
      }
    }
  });
  test(`${language}: timeline and export preview carry structured authors, not legacy labels`, () => {
    const measurement = { id: 1, measured_on: '2026-10-01', polyp_count: 0, ephyrae_count: 0, user: raw, user_identity: current };
    const data = timeline.prepareBiologicalChartData([measurement], [{
      kind: 'subculture', id: 2, effective_date: '2026-10-02', polyp_count_after: 0,
      user_identity: current, author: { username: raw },
    }], timeline.getBiologicalTimelineLabels(t));
    assert.equal(data.measurements[0].user_identity, current);
    assert.equal(data.polypStates[0].user_identity, current);
    assert.equal(JSON.stringify(data).includes(raw), false);
    const source = read('../src/components/ExportsView.tsx');
    assert.match(source, /user_identity: measurement\.user_identity/);
    assert.match(source, /historicalUser: translations\[language\]\.historicalUser/);
    assert.doesNotMatch(source, /enteredBy: measurement\.user/);
  });
  test(`${language}: account targets use live identity; deleted and legacy targets stay neutral`, () => {
    const entry = { action: 'update', object_type: 'account', object_id: raw, description: 'Member access updated',
      account_identity: current, business_details: { type: 'account',
        values: { nom: raw, email: 'legacy@example.org', username: raw },
        changes: { nom: { before: raw, after: 'another-tech' }, role: { before: 'viewer', after: 'lab_technician' } },
      } };
    assert.equal(audit.getAuditTargetLabel(entry, t), readable);
    assert.equal(audit.getAuditTargetLabel({ ...entry, account_identity: null }, t), t('historicalUser'));
    assert.equal(audit.getAuditTargetLabel({ ...entry, account_identity: undefined }, t), t('historicalUser'));
    assert.equal(audit.getPersonalResourceLabel({ type: 'account', identifier: raw, label: raw, account_identity: null }), '');
    assert.equal(audit.getPersonalResourceLabel({ type: 'account', identifier: raw, label: raw, account_identity: current }), readable);
    assert.equal(JSON.stringify(audit.getAuditInlineBusinessItems(entry.business_details, t)).includes(raw), false);
    assert.equal(JSON.stringify(audit.getAuditBusinessDetailContent(entry.business_details)).includes(raw), false);
  });
  test(`${language}: rendered admin actors and personal account targets never expose raw compatibility fields`, () => {
    for (const user_identity of [current, null, undefined]) {
      const entry = { id: 1, action: 'update', family: 'accounts', object_type: 'account', description: 'Member access updated',
        effective_at: '2026-10-01T10:00:00Z', created_at: '2026-10-01T10:00:00Z',
        user: raw, user_display: raw, edited_by: raw, edited_by_display: raw,
        user_identity, account_identity: user_identity, business_details: { type: 'account', values: { nom: raw } },
        resource: { type: 'account', identifier: raw, label: raw, account_identity: user_identity },
      };
      for (const component of [admin.AdminAuditRow, personal.ProfileActionRow]) {
        const html = renderToStaticMarkup(React.createElement(component, { entry, t, language }));
        assert.ok(html.includes(user_identity ? readable : t('historicalUser')));
        assert.equal(html.includes(raw), false);
      }
    }
  });
  test(`${language}: actual admin/profile summaries and details never expose legacy account descriptions`, () => {
    for (const technicalName of ['legacy-tech', 'internal_opaque']) {
      for (const account_identity of [current, null, undefined]) {
        for (const user_identity of [current, null]) {
          const description = `Historical ${technicalName} description`;
          const entry = {
            id: 1, action: 'update', action_label: technicalName, family: 'accounts', object_type: 'account', object_id: technicalName,
            description, effective_at: '2026-10-01T10:00:00Z', created_at: '2026-10-01T10:00:00Z',
            user: technicalName, user_display: technicalName, user_identity, account_identity,
            edited_by: technicalName, edited_by_display: technicalName, edited_by_identity: null,
            resource: { type: 'account', identifier: technicalName, label: technicalName, account_identity },
            business_details: { type: 'account',
              values: { nom: technicalName, username: technicalName, email: technicalName, note: technicalName },
              changes: {
                nom: { before: technicalName, after: technicalName },
                username: { before: technicalName, after: technicalName },
                email: { before: technicalName, after: technicalName },
                role: { before: 'viewer', after: 'lab_technician' },
              },
            },
          };
          const before = JSON.stringify(entry);
          for (const component of [admin.AdminAuditRow, personal.ProfileActionRow]) {
            const html = renderToStaticMarkup(React.createElement(component, { entry, t, language }));
            assert.ok(html.includes(t('auditActionUpdate')), 'real primary summary renders a localized action');
            assert.ok(html.includes(account_identity ? readable : t('historicalUser')));
            assert.ok(html.includes(t('auditValueViewer')), 'real details retain the previous role');
            assert.ok(html.includes(t('auditValueLabTechnician')), 'real details retain the new role');
            assert.equal(html.includes(technicalName), false, html);
            assert.equal(html.includes(description), false);
          }
          assert.equal(JSON.stringify(entry), before, 'stored descriptions and details are unchanged');
        }
      }
    }
  });
  test(`${language}: legacy account inline changes suppress identity fields across event markers`, () => {
    const changes = Object.fromEntries(['username', 'nom', 'name', 'email', 'first_name', 'last_name', 'full_name', 'display_name']
      .map(key => [key, { before: 'tech', after: `legacy-${key}@example.org` }]));
    const details = { type: 'reference', changes: { ...changes,
      role: { before: 'viewer', after: 'lab_technician' },
      acces_actif: { before: false, after: true },
    } };
    for (const marker of [{ family: 'accounts' }, { object_type: 'account' }, { object_type: 'user' },
      { resource: { type: 'account' } }, { resource: { type: 'user' } }]) {
      const entry = { action: 'update', description: 'Member access updated', business_details: details, ...marker };
      const before = JSON.stringify(entry);
      const items = audit.getAuditInlineBusinessItems(details, t, entry.description, entry);
      assert.deepEqual(Array.from(items, item => item.key), ['role', 'acces_actif']);
      assert.equal(JSON.stringify(items).includes('tech"'), false);
      for (const value of Object.values(changes)) {
        assert.equal(JSON.stringify(items).includes(value.after), false);
      }
      assert.ok(items.some(item => item.before === t('auditValueViewer') && item.after === t('auditValueLabTechnician')));
      assert.ok(items.some(item => item.before === t('auditValueNo') && item.after === t('auditValueYes')));
      assert.equal(JSON.stringify(entry), before);
    }
    const accountItems = audit.getAuditInlineBusinessItems({ ...details, type: 'account' }, t);
    assert.deepEqual(Array.from(accountItems, item => item.key), ['role', 'acces_actif']);
    assert.equal(accountItems.some(item => item.before === 'tech'), false);
    const unrelated = audit.getAuditInlineBusinessItems({ type: 'reference', changes: { name: changes.name, email: changes.email } }, t,
      'Reference updated', { object_type: 'species', family: 'references' });
    assert.deepEqual(Array.from(unrelated, item => item.key), ['name', 'email']);
    assert.ok(unrelated.some(item => item.before === 'tech'), 'unrelated reference changes remain visible');
  });
  test(`${language}: actual admin/profile inline renderers suppress legacy account reference identities`, () => {
    const changes = Object.fromEntries(['username', 'nom', 'name', 'email', 'first_name', 'last_name', 'full_name']
      .map(key => [key, { before: 'tech', after: `legacy-${key}@example.org` }]));
    for (const object_type of ['account', 'user']) {
      const entry = { id: 1, action: 'update', family: 'accounts', object_type,
        description: 'Member access updated', created_at: '2026-10-01T10:00:00Z', effective_at: '2026-10-01T10:00:00Z',
        user_identity: current, account_identity: null, resource: { type: object_type, account_identity: null },
        business_details: { type: 'reference', changes: { ...changes,
          role: { before: 'viewer', after: 'lab_technician' }, acces_actif: { before: false, after: true },
        } },
      };
      for (const component of [admin.AdminAuditRow, personal.ProfileActionRow]) {
        const html = renderToStaticMarkup(React.createElement(component, { entry, t, language }));
        assert.ok(html.includes(t('auditDescriptionMemberUpdated')));
        assert.ok(html.includes(t('auditValueViewer')) && html.includes(t('auditValueLabTechnician')));
        assert.ok(html.includes(t('auditValueNo')) && html.includes(t('auditValueYes')));
        assert.doesNotMatch(html, />tech</);
        for (const value of Object.values(changes)) assert.equal(html.includes(value.after), false, html);
      }
    }
  });
  test(`${language}: account summary guards cover family, legacy users and personal resources`, () => {
    for (const accountShape of [{ family: 'accounts' }, { object_type: 'account' }, { object_type: 'user' },
      { resource: { type: 'account' } }, { resource: { type: 'user' } }, { business_details: { type: 'account' } }]) {
      for (const description of ['Historical legacy-tech description', 'Historical internal_opaque description', 'Box created manually: legacy-tech']) {
        const entry = { ...accountShape, action: 'update', action_label: 'legacy-tech', description };
        assert.equal(audit.getAuditBusinessSummary(entry, t), t('auditActionUpdate'));
        assert.equal(audit.getAuditDescriptionLabel(entry, t), t('auditActionUpdate'));
        assert.equal(audit.getAuditBusinessSummary({ ...entry, action: 'legacy-tech' }, t), t('auditObjectAccount'));
      }
    }
    const nonAccount = { family: 'boxes', action: 'update', description: 'Historical legacy-tech description' };
    assert.equal(audit.getAuditBusinessSummary(nonAccount, t), nonAccount.description);
    assert.equal(audit.getAuditDescriptionLabel(nonAccount, t), nonAccount.description);
    assert.equal(audit.getAuditBusinessNote({ type: 'box', values: { note: 'Preserved note' } }), 'Preserved note');
  });
  test(`${language}: lineage renders structured and historical authors even when the legacy event is absent`, () => {
    for (const user_identity of [current, null, undefined]) {
      for (const event of [{ event_date: '2026-10-01', user: raw, user_identity }, null]) {
        const html = renderToStaticMarkup(React.createElement(lineage.LineageGroup, {
          title: 'Lineage', language, labels: { by: 'by', historicalLink: 'Historical link', noReason: 'No reason' },
          relations: [{ id: 1, box: { id: 2, status: 'active', global_code: 'SF.002', species_name: 'Aurelia' }, event }],
        }));
        assert.ok(html.includes(event && user_identity ? readable : t('historicalUser')));
        assert.equal(html.includes(raw), false);
      }
    }
  });
}

for (const language of ['fr', 'en']) {
  test(`${language}: linked audit actions render structured and historical actors only`, () => {
    for (const user_identity of [current, null, undefined]) {
      let stateIndex = 0;
      const entries = [{ id: 1, effective_at: '2026-10-01T10:00:00Z', user_identity, user: raw, user_display: raw }];
      const linked = functions('../src/components/AuditLinkedActionsPopover.tsx', ['LinkedAuditContent'], {
        ...rowGlobals,
        useState: initial => [stateIndex++ === 0 ? entries : initial, () => {}],
        useEffect() {},
      });
      const html = renderToStaticMarkup(React.createElement(linked.LinkedAuditContent, { language, t: createTranslator(language) }));
      assert.ok(html.includes(user_identity ? readable : catalogs[language].historicalUser));
      assert.equal(html.includes(raw), false);
    }
  });
}

test('all confirmed author consumers use structured identities without directory resolution', () => {
  for (const path of ['BoxInsights', 'BiologicalTrendChart', 'ExportsView', 'LineageModal', 'AdminAuditSection', 'AuditLinkedActionsPopover']) {
    const source = read(`../src/components/${path}.tsx`);
    assert.match(source, /user_identity/);
    assert.doesNotMatch(source, /(?:measurement|movement|event|linkedEntry|entry)\??\.(?:user|user_display)\b|author\?\.username/);
  }
  assert.doesNotMatch(read('../src/utils/biologicalTimeline.ts'), /enteredBy:|author\?\.username/);
  assert.doesNotMatch(read('../src/components/ProfileActionsSection.tsx'), /getAuditTargetLabel[^\n]*\|\| getPersonalResourceLabel/);
});
