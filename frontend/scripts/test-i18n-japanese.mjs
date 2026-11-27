import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { loadI18n } from './i18n-runtime-test-helper.mjs';
import { readSections } from './i18n-sections.mjs';
import { moduleLoader } from './sharing-test-harness.mjs';

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return {
    values,
    getItem: (key) => (values.has(key) ? values.get(key) : null),
    setItem: (key, value) => values.set(key, String(value)),
  };
}

const placeholders = (value) =>
  (value.match(/\{[A-Za-z_][A-Za-z0-9_]*\}/g) ?? []).sort();
const japaneseCharacter = /[぀-ヿ㐀-鿿＀-￯]/;
// Validated values that are language-neutral tokens (identical or without kana/kanji).
const NEUTRAL_VALUES = new Set([
  'auditMetaIdentifier',
  'auditValueCsv',
  'auditRelationTransfer',
  'taxonomyAphiaId',
]);
const ADMIN_INVITATION = /管理者に(?:お問い合わせください|連絡してください)/;
const PERMISSION_KEYS = [
  'boxActivateForbidden',
  'createBoxForbidden',
  'manageErrorMembershipAdminRequired',
  'manageErrorResponsableRequired',
  'measurementForbidden',
  'moveForbidden',
  'manageErrorActiveResponsableRequired',
  'boxArchiveForbidden',
  'subcultureForbidden',
];

test('explicit Japanese values display Japanese rather than copied FR/EN text', () => {
  const { fr, en, ja } = loadI18n().translations;
  for (const { key, values } of readSections().entries.values()) {
    if (values.ja === undefined) continue;
    const value = ja[key];
    if (NEUTRAL_VALUES.has(key)) continue;
    // These reviewed Japanese units can match a source while still containing Japanese text.
    if (
      ![
        'probeElapsedMinutes',
        'probeElapsedHours',
        'probeElapsedDays',
      ].includes(key)
    ) {
      assert.notEqual(value, en[key], `${key}: English copied as Japanese`);
      assert.notEqual(value, fr[key], `${key}: French copied as Japanese`);
    }
    assert.match(value, japaneseCharacter, `${key}: no Japanese text`);
  }
});

test('glossary-sensitive Japanese terms are never displayed against the reference', () => {
  const { fr, en, ja } = loadI18n().translations;
  for (const { key, values } of readSections().entries.values()) {
    if (values.ja === undefined) continue;
    const value = ja[key];
    assert.equal(
      value.includes('継代'),
      false,
      `${key}: G18 transplant is 移植`,
    );
    if (/repiqu/i.test(fr[key]))
      assert.ok(value.includes('移植'), `${key}: G18 移植`);
    if (/\bRead-only mode\b/.test(en[key]))
      assert.ok(value.includes('閲覧専用'), `${key}: G36 閲覧専用`);
    if (/\bManager\b/.test(en[key]))
      assert.ok(value.includes('責任者'), `${key}: G33 責任者`);
    if (/salinity measurement/i.test(en[key]))
      assert.equal(value.includes('測定記録'), false, `${key}: Measurement`);
  }
  for (const key of PERMISSION_KEYS) {
    if (ja[key] === en[key])
      assert.match(ja[key], /Please contact your administrator\.$/, key);
    else
      assert.match(
        ja[key],
        ADMIN_INVITATION,
        `${key}: permission refusal invites contacting an administrator`,
      );
  }
});

test('Japanese uses localized values and falls back to English for untranslated keys', () => {
  const { createTranslator } = loadI18n();
  const en = createTranslator('en');
  const ja = createTranslator('ja');
  assert.equal(ja('createBoxTitle'), '容器を作成');
  assert.equal(ja('back'), '戻る');
  assert.equal(ja('boxActivateForbidden'), en('boxActivateForbidden'));
  assert.equal(ja('missingKeyForTest'), 'missingKeyForTest');
});

test('Japanese create-box count composition preserves zero and singular/plural selection', () => {
  const translate = loadI18n().createTranslator('ja');
  const { default: StrainPicker } = moduleLoader({
    'src/components/PolypbaseIcon.tsx': { default: () => null },
  })('src/components/StrainPicker.tsx');
  for (const count of [0, 1, 2, 12]) {
    const countKeys = [];
    const markup = renderToStaticMarkup(
      createElement(StrainPicker, {
        options: [
          {
            id: 1,
            name: '',
            code: 'AAA-BBB-01',
            active_box_count: count,
            species: { id: 1, scientific_name: 'Example species' },
            identity: { status: 'usable', strain_number: '01' },
          },
        ],
        selectedId: null,
        disabled: false,
        onSelect() {},
        t(key) {
          if (key === 'createBoxActiveBox' || key === 'createBoxActiveBoxes')
            countKeys.push(key);
          return translate(key);
        },
      }),
    );
    assert.deepEqual(countKeys, [
      count === 1 ? 'createBoxActiveBox' : 'createBoxActiveBoxes',
    ]);
    assert.ok(markup.includes(`<small>${count} 個の有効な容器</small>`));
  }
});

test('Japanese probe interpolation preserves zero, durations and scientific units', () => {
  const {
    createTranslator,
    translations: { en },
  } = loadI18n();
  const translate = createTranslator('ja');
  for (const [key, unit] of [
    ['probeElapsedMinutes', '分'],
    ['probeElapsedHours', '時間'],
    ['probeElapsedDays', '日'],
  ]) {
    for (const count of [0, 1, 24]) {
      const duration = translate(key).replace('{count}', String(count));
      assert.ok(duration.includes(String(count)), `${key}: preserve ${count}`);
      assert.ok(duration.includes(unit), `${key}: preserve the Japanese unit`);
      assert.deepEqual(placeholders(duration), []);
      const readingAgo = translate('probeReadingAgo').replace(
        '{duration}',
        duration,
      );
      assert.equal(
        readingAgo,
        `${duration}前`,
        'preserve the full Japanese duration including zero',
      );
      assert.deepEqual(placeholders(readingAgo), []);
      const silent = translate('probeSilentDescription').replace(
        '{duration}',
        duration,
      );
      assert.equal(
        silent,
        en.probeSilentDescription.replace('{duration}', duration),
      );
      assert.ok(
        silent.includes(duration),
        'English fallback preserves the interpolated Japanese duration',
      );
      assert.deepEqual(placeholders(silent), []);
    }
  }
  for (const [minimum, maximum] of [
    ['0 °C', '24.5 °C'],
    ['-1.25 °C', '0 °C'],
  ]) {
    const summary = translate('probeChartSummary')
      .replace('{min}', minimum)
      .replace('{max}', maximum);
    assert.ok(summary.includes(minimum), 'preserve minimum and its unit');
    assert.ok(summary.includes(maximum), 'preserve maximum and its unit');
    assert.equal(
      summary.match(/°C/g)?.length,
      2,
      'both scientific units survive interpolation',
    );
    assert.deepEqual(placeholders(summary), []);
    assert.match(summary, japaneseCharacter);
  }
});

test('Japanese is selectable and persisted; unknown values keep the French default', () => {
  const localStorage = storage();
  const i18n = loadI18n({ window: { localStorage } });
  for (const [input, expected] of [
    ['ja', 'ja'],
    ['ja-JP', 'ja'],
    [' JA ', 'ja'],
    ['en-GB', 'en'],
    ['fr', 'fr'],
    ['de', 'fr'],
    ['', 'fr'],
    [null, 'fr'],
    [undefined, 'fr'],
    ['constructor', 'fr'],
    ['toString', 'fr'],
  ]) {
    assert.equal(i18n.resolveLanguage(input), expected, String(input));
  }
  assert.equal(i18n.getStoredInterfaceLanguage(), null);
  assert.equal(i18n.setStoredInterfaceLanguage('ja'), 'ja');
  assert.equal(localStorage.values.get('polypbase.interfaceLanguage'), 'ja');
  assert.equal(
    loadI18n({ window: { localStorage } }).getStoredInterfaceLanguage(),
    'ja',
    'survives a reload',
  );
  assert.equal(i18n.setStoredInterfaceLanguage('en-GB'), 'en');
  assert.equal(i18n.getStoredInterfaceLanguage(), 'en');
  assert.equal(i18n.setStoredInterfaceLanguage('xx'), 'fr');
  assert.equal(i18n.getStoredInterfaceLanguage(), 'fr');
  const broken = {
    getItem() {
      throw new Error('denied');
    },
    setItem() {
      throw new Error('denied');
    },
  };
  const restricted = loadI18n({ window: { localStorage: broken } });
  assert.equal(restricted.getStoredInterfaceLanguage(), null);
  assert.equal(restricted.setStoredInterfaceLanguage('ja'), 'ja');
});

test('local presentation tables keep FR/EN and use validated Japanese with English fallback', () => {
  const i18n = loadI18n();
  const copy = {
    fr: { a: 'A fr', b: 'B fr' },
    en: { a: 'A en', b: 'B en' },
    ja: { a: 'A ja' },
  };
  assert.equal(i18n.selectLocalCopy('fr', copy), copy.fr);
  assert.equal(i18n.selectLocalCopy('en', copy), copy.en);
  assert.deepEqual(
    { ...i18n.selectLocalCopy('ja', copy) },
    { a: 'A ja', b: 'B en' },
  );
  assert.deepEqual(
    { ...i18n.selectLocalCopy('ja', { fr: copy.fr, en: copy.en }) },
    copy.en,
  );
});

test('box status labels are validated Japanese with unchanged tones and fallbacks', () => {
  const { getBoxStatusPresentation } = moduleLoader()('src/boxStatus.ts');
  for (const [status, label] of [
    ['pending_review', '確認待ち'],
    ['active', '有効'],
    ['inactive', '無効'],
  ]) {
    const english = getBoxStatusPresentation(status, 'en');
    assert.deepEqual(
      { ...getBoxStatusPresentation(status, 'ja') },
      { label, tone: english.tone },
    );
  }
  assert.deepEqual(
    { ...getBoxStatusPresentation('pending_review', 'fr') },
    { label: 'À vérifier', tone: 'warning' },
  );
  assert.deepEqual(
    { ...getBoxStatusPresentation('pending_review', 'en') },
    { label: 'Pending review', tone: 'warning' },
  );
  for (const language of ['fr', 'en', 'ja']) {
    assert.deepEqual(
      { ...getBoxStatusPresentation('legacy_state', language) },
      { label: 'legacy state', tone: 'neutral' },
    );
  }
});

test('Japanese dates use the ja-JP locale while FR/EN keep their locales', () => {
  const i18n = loadI18n();
  assert.deepEqual(['fr', 'en', 'ja'].map(i18n.getLanguageLocale), [
    'fr-FR',
    'en-GB',
    'ja-JP',
  ]);
  for (const lang of ['ja', 'ja-JP']) {
    const dateFormat = moduleLoader(
      {},
      { document: { documentElement: { lang } } },
    )('src/utils/dateFormat.ts');
    assert.equal(dateFormat.getDocumentLocale(), 'ja-JP');
  }
  for (const [lang, locale] of [
    ['fr', 'fr-FR'],
    ['en', 'en-GB'],
  ]) {
    const dateFormat = moduleLoader(
      {},
      { document: { documentElement: { lang } } },
    )('src/utils/dateFormat.ts');
    assert.equal(dateFormat.getDocumentLocale(), locale);
  }
  class ApiError extends Error {}
  const movement = moduleLoader({ 'src/api/client.ts': { ApiError } })(
    'src/utils/boxMovementCorrection.ts',
  );
  const day = new Date('2026-11-02T00:00:00Z');
  for (const [language, locale] of [
    ['fr', 'fr-FR'],
    ['en', 'en-GB'],
    ['ja', 'ja-JP'],
  ]) {
    assert.equal(
      movement.formatMovementTimestamp('2026-11-02', language),
      new Intl.DateTimeFormat(locale, {
        timeZone: 'UTC',
        dateStyle: 'medium',
      }).format(day),
    );
  }
  assert.match(
    movement.formatMovementTimestamp('2026-11-02T13:05:00Z', 'ja'),
    /2026/,
  );
});

test('the error screen follows the stored interface language, then the browser', () => {
  const jsx = (type, props) => ({ type, props });
  class Component {
    constructor(props) {
      this.props = props;
    }
  }
  function render({ stored, browser }) {
    const window = {
      localStorage: storage(
        stored ? { 'polypbase.interfaceLanguage': stored } : {},
      ),
    };
    const i18n = loadI18n({ window });
    const ErrorBoundary = moduleLoader(
      {
        react: { Component },
        'react/jsx-runtime': { jsx, jsxs: jsx },
        'src/i18n/index.ts': i18n,
      },
      { navigator: { language: browser }, window },
    )('src/components/ErrorBoundary.tsx').default;
    const boundary = new ErrorBoundary({ children: 'content' });
    boundary.state = { error: new Error('boom') };
    const tree = boundary.render();
    return tree.props.children.props.children[0].props.children;
  }
  assert.equal(
    render({ stored: 'ja', browser: 'fr-FR' }),
    'エラーが発生しました',
  );
  assert.equal(
    render({ stored: null, browser: 'ja-JP' }),
    'エラーが発生しました',
  );
  assert.equal(
    render({ stored: null, browser: 'en-US' }),
    'Something went wrong',
  );
  assert.equal(
    render({ stored: null, browser: 'de-DE' }),
    'Une erreur est survenue',
  );
  assert.equal(
    render({ stored: 'en', browser: 'fr-FR' }),
    'Something went wrong',
  );
  assert.equal(
    render({ stored: 'fr', browser: 'en-US' }),
    'Une erreur est survenue',
  );
});
