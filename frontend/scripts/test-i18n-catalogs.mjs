import assert from 'node:assert/strict';
import test from 'node:test';
import { loadI18n } from './i18n-runtime-test-helper.mjs';
import { readSections } from './i18n-sections.mjs';

// Deliberately scoped to reviewed keys, not a suffix or vocabulary heuristic.
const permissionKeys = [
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
const contactSentence = {
  fr: 'Merci de contacter un administrateur.',
  en: 'Please contact your administrator.',
};

// Advisory only: these need contextual review, not an automatic wording failure.
const advisoryExclusions = {
  shared401:
    'Shared API authentication/session fallback, not a catalog permission refusal.',
  shared403:
    'Shared API fallback can cover several causes; review separately from these catalog keys.',
  qrScannerPermission:
    'Browser/device camera access, not an application administrator permission.',
  qrScannerSecureContext: 'HTTPS prerequisite, not an application permission.',
  csvErrorPermission:
    'Mixed session/institution guidance; not in the approved sentence-only batch.',
  manualTemperatureForbidden:
    'Deferred legacy temperature refusal; not in the approved permission-key batch.',
  movementErrorForbidden:
    'Deferred shared 401/403 movement fallback; not in the approved permission-key batch.',
  manageErrorResponsableProtected:
    'Manager-protection invariant, not a permission refusal.',
  manageErrorLastActiveResponsable:
    'Last-active-manager invariant, not a permission refusal.',
  validations:
    'Validation, conflicts, business rules and technical errors must not gain a permission invitation.',
};

// Application decisions from B01 take precedence over historical Sheet wording.
// No global ban on Organization, Subculture or Measurement: meaning is contextual.
const approved = {
  fr: {
    adminZoneTypeCabinet: 'Emplacement',
    historicalTemperaturePeriod: 'Mesuré pour la période :',
    organizationChoiceIntro:
      'Choisissez la structure dans laquelle vous souhaitez travailler. Les données affichées et vos droits dépendent de la structure sélectionnée.',
  },
  en: {
    overviewEmpty: 'No living box to display.',
    movementKindUnclassified: 'Provenance not established',
    auditValuePendingReview: 'Needs review',
    adminZoneTypeCabinet: 'Rack',
    historicalTemperaturePeriod: 'Measured for the period:',
    moveDialogCurrentZone: 'Current location',
    loginIntro: 'Access your boxes and records.',
    recentAccess: 'Latest access',
    auditMetaResponsableStatus: 'Manager status',
    auditMetaCultureStatus: 'Box status',
    historicalTemperatureFailed: 'Temperature history unavailable.',
    manageRelinquishResponsableConfirmTitle: 'Step down as manager?',
    auditDescriptionManualTemperature: 'Manual temperature for {zone} recorded',
    manageDemoteToViewer: 'Demote to Read-only mode',
    subcultureForbidden:
      'This account cannot create transplant events. Please contact your administrator.',
  },
};

function checkDecisions(catalogs) {
  const errors = [];
  for (const language of ['fr', 'en']) {
    for (const [key, expected] of Object.entries(approved[language])) {
      if (catalogs[language].get(key) !== expected)
        errors.push(`${language}.${key}: approved application wording changed`);
    }
    const sentence = contactSentence[language];
    for (const key of permissionKeys) {
      const value = catalogs[language].get(key);
      if (
        typeof value !== 'string' ||
        value.split(sentence).length - 1 !== 1 ||
        !value.endsWith(` ${sentence}`)
      ) {
        errors.push(
          `${language}.${key}: must end with exactly one invitation: ${sentence}`,
        );
      }
    }
  }
  return errors;
}

const decisionFixture = () =>
  Object.fromEntries(
    ['fr', 'en'].map((language) => [
      language,
      new Map([
        ...permissionKeys.map((key) => [
          key,
          `Refusal. ${contactSentence[language]}`,
        ]),
        ...Object.entries(approved[language]),
      ]),
    ]),
  );

test('all approved application decisions and single permission invitations pass', () => {
  assert.deepEqual(checkDecisions(decisionFixture()), []);
});
for (const language of ['fr', 'en']) {
  for (const key of Object.keys(approved[language])) {
    for (const mutation of ['changed', 'missing']) {
      test(`protects ${language}.${key} against ${mutation} approved wording`, () => {
        const catalogs = decisionFixture();
        if (mutation === 'missing') catalogs[language].delete(key);
        else catalogs[language].set(key, 'Unapproved replacement');
        assert.ok(
          checkDecisions(catalogs).includes(
            `${language}.${key}: approved application wording changed`,
          ),
        );
      });
    }
  }
  for (const key of permissionKeys) {
    for (const [label, value] of [
      ['missing key', undefined],
      ['missing invitation', 'Refusal.'],
      [
        'duplicate invitation',
        `Refusal. ${contactSentence[language]} ${contactSentence[language]}`,
      ],
      [
        'nonterminal invitation',
        `Refusal. ${contactSentence[language]} More text.`,
      ],
      [
        'invitation without separating space',
        `Refusal.${contactSentence[language]}`,
      ],
    ]) {
      test(`permission ${language}.${key} rejects ${label}`, () => {
        const catalogs = decisionFixture();
        if (value === undefined) catalogs[language].delete(key);
        else catalogs[language].set(key, value);
        assert.ok(
          checkDecisions(catalogs).some((error) =>
            error.startsWith(`${language}.${key}: must end`),
          ),
        );
      });
    }
  }
}

test('advisory exclusions and contextual vocabulary do not become global bans', () => {
  const catalogs = decisionFixture();
  for (const language of ['fr', 'en']) {
    for (const [key, reason] of Object.entries(advisoryExclusions)) {
      assert.ok(reason.length > 0);
      catalogs[language].set(key, 'Organization Subculture Measurement');
    }
  }
  assert.deepEqual(checkDecisions(catalogs), []);
});

test('runtime catalogs contain every registered section key and per-language value', () => {
  const { entries } = readSections();
  const { translations, createTranslator } = loadI18n();
  const keys = [...entries.keys()];
  const languages = ['en', 'fr', 'ja'];
  assert.deepEqual(Object.keys(translations).sort(), languages);
  for (const language of languages) {
    const catalog = translations[language];
    const translate = createTranslator(language);
    const missing = keys.filter((key) => !Object.hasOwn(catalog, key));
    const extra = Object.keys(catalog).filter((key) => !entries.has(key));
    assert.deepEqual(
      { missing, extra },
      { missing: [], extra: [] },
      `${language}: runtime and static key inventory differ`,
    );
    for (const { key, path, line, values } of entries.values()) {
      const expected = values[language] ?? values.en;
      const location = `${path}:${line}: ${key}.${language}`;
      assert.equal(catalog[key], expected, location);
      assert.equal(translate(key), expected, `${location}: translator`);
    }
  }
});

test('real FR/EN catalogs protect reviewed wording and permission invitations', () => {
  const { fr, en } = loadI18n().translations;
  assert.deepEqual(
    checkDecisions({
      fr: new Map(Object.entries(fr)),
      en: new Map(Object.entries(en)),
    }),
    [],
  );
});
