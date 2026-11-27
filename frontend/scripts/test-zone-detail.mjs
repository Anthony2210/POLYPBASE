import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { loadI18n } from './i18n-runtime-test-helper.mjs';

const appSource = readFileSync(
  new URL('../src/App.tsx', import.meta.url),
  'utf8',
);
const zonesSource = readFileSync(
  new URL('../src/components/ZonesView.tsx', import.meta.url),
  'utf8',
);
const { fr, en } = loadI18n().translations;
const stylesSource = readFileSync(
  new URL('../src/styles/pages/zones.css', import.meta.url),
  'utf8',
);
const boxStylesSource = readFileSync(
  new URL('../src/styles/pages/box-detail.css', import.meta.url),
  'utf8',
);

const detailSource = zonesSource.slice(
  zonesSource.indexOf('export function ZoneDetailPage'),
  zonesSource.indexOf('export function ZoneBoxesPage'),
);

const requiredKeys = [
  'cancel',
  'manualTemperatureAction',
  'manualSalinityDate',
  'manualSalinityValue',
  'manualSalinitySave',
  'zoneSalinityLatestTitle',
  'zoneSalinityNoReading',
  'zoneSalinityCreateAction',
  'zoneSalinityEditAction',
  'zoneSalinityCreateTitle',
  'zoneSalinityEditTitle',
  'zoneSalinityNotes',

  'temperatureAverage',
  'temperatureRecentError',
  'thermalMonitoringPeriod',
  'thermalMonitoringDeviation',
  'thermalMonitoringExtrema',
  'thermalMonitoringEmpty',
  'thermalMonitoringKeyboard',
  'zoneCapacityMissing',
  'zoneNoProbe',
  'zoneRecentMovementsTitle',
  'zoneMovementEntries',
  'zoneMovementExits',
];

test('zone hero keeps identity without KPIs, local alert, or duplicate box action', () => {
  const hero = detailSource.slice(
    detailSource.indexOf('<header className="entity-header'),
    detailSource.indexOf('</header>'),
  );
  assert.match(hero, /zoneSheet/);
  assert.match(hero, /zone\.organization\.name/);
  assert.match(detailSource, /zoneBoxesDirectoryAction/);
  assert.doesNotMatch(detailSource, /replace\(\s*'\{count\}'/);
  assert.equal(fr.zoneBoxesDirectoryAction, 'Voir toutes les boîtes');
  assert.equal(en.zoneBoxesDirectoryAction, 'View all boxes');
  assert.doesNotMatch(hero, /boxDirectoryLabel|zone-box-directory-trigger/);
  assert.doesNotMatch(hero, /<Metric|zone-alert-trigger|BellIcon/);
});

test('zone detail removes biological summary and activity components', () => {
  assert.doesNotMatch(zonesSource, /ZoneLatestCountsChart|ZoneRecentActivity/);
  assert.doesNotMatch(detailSource, /latestCounts|zoneActivityTitle/);
});

test('thermal monitoring replaces ruler and daily facts without changing the historical chart', () => {
  const panel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function TemperatureEntryModal'),
  );
  assert.match(
    zonesSource,
    /import ThermalMonitoringChart from '.\/ThermalMonitoringChart'/,
  );
  assert.match(panel, /<ThermalMonitoringChart zone=\{zone\} t=\{t\} \/>/);
  assert.equal(
    (panel.match(/t\('thermalMonitoringPeriod'\)/g) ?? []).length,
    1,
  );
  assert.match(
    panel,
    /<h2>\{t\('temperatureControl'\)\}<\/h2>\s*<p>\{t\('thermalMonitoringPeriod'\)\}<\/p>/,
  );
  assert.match(panel, /useZoneChartReadings\(zone, hasCollectedProbe\)/);
  assert.match(panel, /useProbeMonitoring\(zone, chart.initial\)/);
  assert.match(
    panel,
    /hasCollectedProbe \? <ZoneProbeChart chart=\{chart\} zone=\{zone\} t=\{t\} \/>/,
  );
  assert.doesNotMatch(
    panel,
    /temperature-ruler|temperature-summary|temperatureDaily|temperatureRecentMean|latest_temperature|useThermalControl|buildTemperatureSummary/,
  );
});

test('manual temperature editor is progressive and supports cancel', () => {
  const temperaturePanel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function Metric'),
  );
  assert.match(temperaturePanel, /isEditingTemperature/);
  assert.match(temperaturePanel, /manualTemperatureAction/);
  assert.match(temperaturePanel, /closeTemperatureEditor/);
  assert.match(temperaturePanel, /setIsEditingTemperature\(false\)/);
  assert.match(temperaturePanel, /<TemperatureEntryModal/);
  assert.match(temperaturePanel, /<ModalPortal>/);
  assert.match(temperaturePanel, /role="dialog"/);
  assert.match(
    temperaturePanel,
    /dialogRef\.current\?\.focus\(\{ preventScroll: true \}\)/,
  );
  assert.match(
    temperaturePanel,
    /className="zone-temperature-modal"\s+tabIndex=\{-1\}/,
  );
  assert.match(temperaturePanel, /type="date"\s+value=\{date\}/);
  assert.doesNotMatch(temperaturePanel, /dateRef|autoFocus|\.select\(/);
  assert.match(temperaturePanel, /temperatureActionRef\.current\?\.focus\(\)/);
  assert.match(temperaturePanel, /restoreTemperatureFocus\.current = true/);
  assert.match(temperaturePanel, /event\.key === 'Escape'/);
  assert.match(temperaturePanel, /event\.key !== 'Tab'/);
  assert.match(temperaturePanel, /onSubmit=\{handleManualTemperatureSubmit\}/);
  assert.match(temperaturePanel, /onClick=\{onCancel\}/);
  assert.doesNotMatch(
    temperaturePanel,
    /secondary-button zone-temperature-action/,
  );
});

test('occupancy distinguishes zero capacity from missing capacity without available-space copy', () => {
  assert.match(detailSource, /capacity === null/);
  assert.match(detailSource, /capacity > 0/);
  assert.match(
    zonesSource,
    /capacity == null \? String\(boxCount\) : `\$\{boxCount\} \/ \$\{capacity\}`/,
  );
  assert.doesNotMatch(detailSource, /zonePlacesAvailable|zoneCapacityExceeded/);
  assert.doesNotMatch(Object.values(fr).join('\n'), /places disponibles/);
  assert.doesNotMatch(Object.values(en).join('\n'), /spaces available/);
});

test('page orders thermal, salinity, and asymmetric movement/sidebar layout', () => {
  assert.ok(
    detailSource.indexOf('<TemperatureControlPanel') <
      detailSource.indexOf('<ZoneFunctionalSections'),
  );
  const sections = zonesSource.slice(
    zonesSource.indexOf('function ZoneFunctionalSections'),
    zonesSource.indexOf('function ZoneBoxesPage'),
  );
  assert.ok(
    sections.indexOf('zone-salinity-section') <
      sections.indexOf('zone-operational-layout'),
  );
  assert.ok(
    sections.indexOf('<ZoneRecentMovements') <
      sections.indexOf('zone-operational-sidebar'),
  );
  assert.ok(
    sections.indexOf('zone-occupancy-section') <
      sections.indexOf('zone-probes-section'),
  );
  assert.match(
    stylesSource,
    /\.zone-operational-layout \{[^}]*grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/s,
  );
  assert.match(
    stylesSource,
    /@media \(max-width: 1280px\) \{\s*\.zone-operational-layout \{\s*grid-template-columns: minmax\(0, 1fr\)/s,
  );
  assert.match(
    stylesSource,
    /\.zone-operational-sidebar \{[^}]*align-content: start/s,
  );
  assert.match(sections, /zone-box-directory-trigger/);
  assert.equal((sections.match(/zone-box-directory-trigger/g) ?? []).length, 1);
});

test('zone page contains box-detail grid areas without implicit page columns', () => {
  assert.match(
    stylesSource,
    /\.zone-page \{\s*grid-template-columns: minmax\(0, 1fr\);\s*\}/,
  );
  assert.doesNotMatch(
    stylesSource,
    /\.zone-overview \{\s*grid-template-columns: minmax\(0, 1fr\)/,
  );
  assert.match(
    stylesSource,
    /\.zone-page > \.zone-salinity-section \{\s*grid-area: auto;\s*\}/,
  );
  assert.match(
    detailSource,
    /measurement-form-section measurement-module is-expanded zone-salinity-section zone-salinity-editor-section/,
  );
  assert.match(
    detailSource,
    /last-reading-card measurement-summary zone-functional-section zone-salinity-section/,
  );
  assert.match(
    boxStylesSource,
    /\.box-page-grid \{[^}]*"form last"[^}]*"insights insights"/s,
  );
  for (const [selector, area] of [
    ['measurement-form-section', 'form'],
    ['last-reading-card', 'last'],
    ['measurement-module', 'last'],
    ['box-insights-section', 'insights'],
  ]) {
    assert.match(
      boxStylesSource,
      new RegExp(
        `\\.box-page-grid > \\.${selector} \\{ grid-area: ${area}; \\}`,
      ),
    );
    assert.doesNotMatch(
      boxStylesSource,
      new RegExp(`(?:^|\\n)\\.${selector} \\{ grid-area: ${area}; \\}`, 'm'),
    );
  }
  assert.ok(
    detailSource.indexOf('<header className="entity-header') <
      detailSource.indexOf('<TemperatureControlPanel'),
  );
  const sections = detailSource.slice(
    detailSource.indexOf('function ZoneFunctionalSections'),
  );
  assert.ok(
    sections.indexOf('zone-salinity-section') <
      sections.indexOf('zone-operational-layout'),
  );
  assert.equal(
    (
      stylesSource.match(
        /grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/g,
      ) ?? []
    ).length,
    1,
  );
  assert.match(
    stylesSource,
    /\.zone-operational-layout \{[^}]*grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/s,
  );
});

test('recent movements replace any generic or biological activity section', () => {
  assert.match(detailSource, /<ZoneRecentMovements/);
  assert.doesNotMatch(
    detailSource,
    /old boxes|archived boxes|anciennes boîtes|boîtes archivées/i,
  );
});

test('salinity reuses the compact reading summary and progressive editor for create and update', () => {
  assert.match(
    detailSource,
    /last-reading-card measurement-summary zone-functional-section zone-salinity-section/,
  );
  assert.match(detailSource, /measurement-summary-edit-button/);
  assert.match(detailSource, /name="plus"/);
  assert.match(detailSource, /name="edit"/);
  assert.match(detailSource, /isEditingSalinity/);
  assert.match(detailSource, /zoneSalinityCreateTitle/);
  assert.match(detailSource, /zoneSalinityEditTitle/);
  assert.match(detailSource, /disabled=\{editingSalinityId !== null\}/);
  assert.match(detailSource, /onRecordManualSalinity/);
  assert.match(detailSource, /onUpdateManualSalinity/);
  assert.match(detailSource, /notes: salinityNotes\.trim\(\)/);
  assert.match(detailSource, /salinityValue\.trim\(\)/);
  assert.match(detailSource, /closeSalinityEditor/);
  assert.match(detailSource, /\{t\('cancel'\)\}/);
  assert.match(
    appSource,
    /apiPatch<ThermalZone>[\s\S]*salinity\/\$\{measurementId\}\//,
  );
  assert.match(detailSource, /zoneNoProbe/);
  assert.match(detailSource, /rows=\{3\}/);
  assert.match(
    stylesSource,
    /\.zone-page \.last-reading-card\.measurement-summary\.zone-salinity-section \{[^}]*minmax\(0, 1fr\)/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-section \.metric strong \{[^}]*color: var\(--color-primary-hover\)/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-section \.metric \{[^}]*background: var\(--color-primary-faint\)/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-entry-grid \{[^}]*minmax\(0, 1fr\)/s,
  );

  assert.match(
    stylesSource,
    /\.zone-salinity-entry-grid \.measurement-date-field \{[^}]*display: grid/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-entry-grid \.notes-field textarea \{[^}]*min-height: 120px/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-section \.measurement-summary-edit-button \{[^}]*position: static/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-section\.has-edit-capability > div:first-child,\s*\.zone-salinity-section\.has-edit-capability \.last-reading-comment \{\s*padding-inline-end: 0/s,
  );
  assert.match(
    stylesSource,
    /\.zone-salinity-editor-section \.inline-error \{[^}]*margin: 0/s,
  );
});

test('salinity uses server capability for correction versus a new reading', () => {
  assert.match(
    detailSource,
    /mode === 'correct' && zone\.latest_salinity\?\.can_edit/,
  );
  assert.match(
    detailSource,
    /if \(mode === 'correct' && !latestSalinity\) return/,
  );
  assert.match(
    detailSource,
    /setEditingSalinityId\(latestSalinity\?\.id \?\? null\)/,
  );
  assert.match(detailSource, /aria-label=\{t\('zoneSalinityCreateAction'\)\}/);
  assert.match(detailSource, /aria-label=\{t\('zoneSalinityEditAction'\)\}/);
  assert.match(detailSource, /openSalinityEditor\('create'\)/);
  assert.match(detailSource, /openSalinityEditor\('correct'\)/);
  assert.match(
    detailSource,
    /if \(editingSalinityId !== null\) \{[\s\S]*onUpdateManualSalinity[\s\S]*\} else \{[\s\S]*onRecordManualSalinity/,
  );
  assert.doesNotMatch(
    detailSource,
    /new Date\(zone\.latest_salinity|Date\.now\(\).*can_edit/,
  );
  assert.match(
    appSource,
    /apiPost<ThermalZone>\(`\/api\/thermal-zones\/\$\{zoneId\}\/salinity\/`/,
  );
  assert.match(detailSource, /salinity_edit_window_expired/);
  assert.match(
    detailSource,
    /await onRefreshZoneSalinityCapability\(zone\.id\)/,
  );
  assert.match(appSource, /refreshZoneSalinityCapability/);
  assert.ok(Object.hasOwn(fr, 'zoneSalinityEditExpired'));
  assert.ok(Object.hasOwn(en, 'zoneSalinityEditExpired'));
});

test('zone card shows only the latest salinity and never renders business history', () => {
  assert.match(detailSource, /zoneSalinityLatestTitle/);
  assert.match(
    detailSource,
    /formatDisplayDate\(zone\.latest_salinity\.measured_on\)/,
  );
  assert.match(
    detailSource,
    /formatZoneSalinity\(zone\.latest_salinity\?\.salinity_psu\)/,
  );
  assert.match(detailSource, /zone\.latest_salinity\?\.notes/);
  assert.match(
    zonesSource,
    /if \(salinity === null \|\| salinity === undefined \|\| salinity === ''\) return '-';/,
  );
  assert.match(
    zonesSource,
    /Number\.isFinite\(numeric\) \? `\$\{numeric\.toFixed\(1\)\} PSU`/,
  );
  assert.doesNotMatch(
    detailSource,
    /ZoneSalinityHistory|isSalinityHistoryOpen|salinityHistoryRevision|zoneSalinityHistoryAction|zoneSalinityHistoryHide/,
  );
  assert.doesNotMatch(
    stylesSource,
    /\.zone-salinity-history(?:-trigger|-list|-reading)?\b/,
  );
  for (const catalog of [fr, en]) {
    for (const key of [
      'zoneSalinityHistoryAction',
      'zoneSalinityHistoryHide',
      'zoneSalinityHistoryTitle',
    ]) {
      assert.equal(Object.hasOwn(catalog, key), false, key);
    }
  }
  assert.doesNotMatch(
    Object.values(fr).join('\n'),
    /Voir l’historique|Masquer l’historique/,
  );

  // The read-only history endpoint is retained solely for refreshing a stale correction capability.
  assert.match(
    appSource,
    /refreshZoneSalinityCapability[\s\S]*salinity\/history\//,
  );
});

test('temperature keeps its compact named action and existing manual-entry modal', () => {
  const panel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function Metric'),
  );
  assert.match(panel, /className="icon-button zone-temperature-action"/);
  assert.match(panel, /aria-label=\{t\('manualTemperatureAction'\)\}/);
  assert.match(panel, /<PolypbaseIcon name="plus"/);
  assert.match(panel, /<ThermalMonitoringChart/);
  assert.match(panel, /<TemperatureEntryModal/);
  assert.match(panel, /className="zone-inline-editor"/);
  assert.match(panel, /className="primary-button" type="submit"/);
  assert.match(
    stylesSource,
    /\.zone-temperature-modal \{[^}]*width: min\(100%, 540px\)/s,
  );
  assert.ok(
    panel.indexOf('id="zone-temperature-error"') <
      panel.indexOf('zone-inline-editor-actions'),
  );
});

test('new zone detail copy exists in French and English', () => {
  for (const key of requiredKeys) {
    assert.ok(Object.hasOwn(fr, key), `missing French key ${key}`);
    assert.ok(Object.hasOwn(en, key), `missing English key ${key}`);
  }
  assert.equal(fr.cancel, 'Annuler');
  assert.equal(en.cancel, 'Cancel');
});
