import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

import ts from 'typescript';

const names = ['getBoxInsightsLabels', 'getProfileLabels', 'getLabelsViewLabels'];

function load(source) {
  const exports = {};
  vm.runInNewContext(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText, { exports });
  return exports;
}
const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const modulePath = '../src/utils/presentationLabels.ts';
const builders = load(read(modulePath));
const catalogues = Object.fromEntries(['fr', 'en'].map((language) => [language, load(read(`../src/i18n/${language}.ts`))[language]]));

// Property names and order recorded from the App.tsx implementation before extraction.
const expectedKeys = {
  getBoxInsightsLabels: ['chartEmpty', 'chartTitle', 'children', 'close', 'ephyraeFull', 'events', 'historyButton', 'historyAllYears', 'historyVisibleCount', 'historyYearFilter', 'historyEnteredBy', 'historyHideComment', 'historyObservation', 'historyReadComment', 'historyShowMore', 'historyYear', 'lineageEmptyGraph', 'lineageLoading', 'lineageRetry', 'lineageTab', 'measurementHistory', 'measurementsTab', 'missingReading', 'missingReadingRange', 'movementEvent', 'movedTo', 'movementHistoryTitle', 'movementsTab', 'noComment', 'noMeasurementHistory', 'noMovementHistory', 'oneMonth', 'oneYear', 'parents', 'polyps', 'salinityFull', 'sixMonths', 'subcultureEvent', 'temperature', 'temperatureNoData', 'threeMonths'],
  getProfileLabels: ['account', 'logoutAction', 'logoutError', 'profileEmail', 'profileLanguage', 'profileAdminAction', 'profileMemberships', 'profileNoEmail', 'profileNoMembership', 'profileAllOrganizationsAccess', 'profilePreferences', 'profileActiveOrganization', 'profileDefaultOrganization', 'profileFullAccess', 'roleResponsable', 'roleDescAdmin', 'roleDescTechnician', 'roleDescViewer', 'saving'],
  getLabelsViewLabels: ['allZones', 'noZone', 'qrLabelAddResults', 'qrLabelAddResultsCompact', 'qrLabelClearSelection', 'qrLabelPreparing', 'qrLabelPopupBlocked', 'qrLabelQrUnavailable', 'qrLabelResourceUnavailable', 'qrLabelImagePreparationFailed', 'qrLabelPreparationFailed', 'qrLabelRetry', 'qrLabelNoEligibleBoxes', 'qrLabelNoMatches', 'qrLabelPrintCount', 'qrLabelSearchTitle', 'qrLabelSelectedSingular', 'qrLabelSelectedPlural', 'pageTitle', 'qrLabelSearchPlaceholder', 'qrLabelSpeciesCount', 'qrLabelSpeciesSelected', 'qrLabelSpeciesSelectedCompact', 'qrLabelSelectSpecies', 'qrLabelDeselectSpecies', 'selectBox', 'zoneLabel'],
};
const recorder = (language) => {
  const calls = [];
  const t = (key) => {
    assert.equal(typeof catalogues[language][key], 'string', `Missing ${language} key ${key}`);
    calls.push(key);
    return catalogues[language][key];
  };
  return { t, calls };
};

test('builders are exported from the utility module', () => {
  for (const name of names) assert.equal(typeof builders[name], 'function', name);
});

for (const language of ['fr', 'en']) {
  for (const name of names) {
    test(`${name} ${language} evaluates static keys eagerly and keeps lazy ones deferred`, () => {
      const { t, calls } = recorder(language);
      const labels = builders[name](t);
      const eager = [...calls];
      const functionKeys = Object.keys(labels).filter((key) => typeof labels[key] === 'function');
      assert.equal(eager.length, Object.keys(labels).length - functionKeys.length, 'Only static labels read the catalogue up front');
      for (const key of Object.keys(labels)) {
        if (typeof labels[key] === 'string') assert.ok(labels[key].length > 0, key);
      }
    });

    test(`${name} ${language} returns the recorded property names in order`, () => {
      assert.deepEqual(Object.keys(builders[name](recorder(language).t)), expectedKeys[name]);
    });
  }

  test(`getBoxInsightsLabels ${language} substitutes visible and total counts, including zero`, () => {
    const { t, calls } = recorder(language);
    const labels = builders.getBoxInsightsLabels(t);
    const before = calls.length;
    const template = catalogues[language].historyVisibleCount;
    for (const [visible, total] of [[0, 0], [1, 1], [5, 12]]) {
      assert.equal(labels.historyVisibleCount(visible, total),
        template.replace('{visible}', String(visible)).replace('{total}', String(total)));
    }
    assert.ok(labels.historyVisibleCount(0, 0).includes('0'), 'Zero is rendered, not dropped');
    assert.equal(calls.length - before, 4, 'Lookup is deferred to each call');
    assert.equal(labels.lineageTab, catalogues[language].analysisTabLineage);
    assert.equal(labels.missingReadingRange, catalogues[language].chartMissingReading);
  });

  test(`getLabelsViewLabels ${language} substitutes counts and species names`, () => {
    const labels = builders.getLabelsViewLabels(recorder(language).t);
    const c = catalogues[language];
    for (const count of [0, 1, 3]) {
      for (const key of ['qrLabelAddResults', 'qrLabelAddResultsCompact', 'qrLabelPrintCount',
        'qrLabelSpeciesCount', 'qrLabelSpeciesSelected', 'qrLabelSpeciesSelectedCompact']) {
        assert.equal(labels[key](count), c[key].replace('{count}', String(count)), `${key} ${count}`);
      }
      for (const key of ['qrLabelSelectSpecies', 'qrLabelDeselectSpecies']) {
        assert.equal(labels[key](count, 'Aurelia aurita'),
          c[key].replace('{count}', String(count)).replace('{species}', 'Aurelia aurita'), `${key} ${count}`);
      }
    }
    assert.equal(labels.pageTitle, c.labelsTitle);
    assert.equal(labels.allZones, c.zoneFilterAll);
    assert.equal(labels.selectBox, c.boxInventoryBatchSelectBox);
    assert.equal(labels.qrLabelSearchPlaceholder, c.adminPrintLabelsSearchPlaceholder);
  });

  test(`getProfileLabels ${language} maps every key to its catalogue entry`, () => {
    const labels = builders.getProfileLabels(recorder(language).t);
    for (const [key, value] of Object.entries(labels)) assert.equal(value, catalogues[language][key], key);
  });
}
