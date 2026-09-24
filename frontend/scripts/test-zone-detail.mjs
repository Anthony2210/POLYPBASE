import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const zonesSource = readFileSync(new URL('../src/components/ZonesView.tsx', import.meta.url), 'utf8');
const frSource = readFileSync(new URL('../src/i18n/fr.ts', import.meta.url), 'utf8');
const enSource = readFileSync(new URL('../src/i18n/en.ts', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../src/styles/pages/zones.css', import.meta.url), 'utf8');
const boxStylesSource = readFileSync(new URL('../src/styles/pages/box-detail.css', import.meta.url), 'utf8');

const detailSource = zonesSource.slice(
  zonesSource.indexOf('export function ZoneDetailPage'),
  zonesSource.indexOf('export function ZoneBoxesPage'),
);

const requiredKeys = [
  'cancel',
  'manualTemperatureAction',
  'manualSalinityAction',
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
  'temperatureMeasurement',
  'temperatureMeasurementCount',
  'zoneCapacityMissing',
  'zoneNoProbe',
  'zoneRecentMovementsTitle',
  'zoneMovementHistoryAction',
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
  assert.match(frSource, /zoneBoxesDirectoryAction: 'Voir toutes les boîtes'/);
  assert.match(enSource, /zoneBoxesDirectoryAction: 'View all boxes'/);
  assert.doesNotMatch(hero, /boxDirectoryLabel|zone-box-directory-trigger/);
  assert.doesNotMatch(hero, /<Metric|zone-alert-trigger|BellIcon/);
});

test('zone detail removes biological summary and activity components', () => {
  assert.doesNotMatch(zonesSource, /ZoneLatestCountsChart|ZoneRecentActivity/);
  assert.doesNotMatch(detailSource, /latestCounts|zoneActivityTitle/);
});

test('thermal summary shows only gap and available range, while the ruler keeps its facts', () => {
  const temperaturePanel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function buildTemperatureSummary'),
  );
  const ruler = temperaturePanel.slice(
    temperaturePanel.indexOf('<div className="temperature-ruler"'),
    temperaturePanel.indexOf('<div className="temperature-summary"'),
  );
  const summary = temperaturePanel.slice(
    temperaturePanel.indexOf('<div className="temperature-summary"'),
    temperaturePanel.indexOf('{isEditingTemperature ? ('),
  );
  assert.doesNotMatch(temperaturePanel, /temperatureOk|temperatureWatch|absoluteDelta|safe-band/);
  assert.match(ruler, /<b>\{t\('targetTemperature'\)\} \{formatTemperature\(targetTemperature/);
  assert.match(ruler, /measurementCount === 1 \? t\('temperatureMeasurement'\) : t\('temperatureAverage'\)/);
  assert.match(temperaturePanel, /const measurementCount = zone\.latest_temperature\?\.measurement_count \?\? 0/);
  assert.match(temperaturePanel, /const hasTemperatureRange = measurementCount > 1/);
  assert.match(temperaturePanel, /hasTemperatureRange && minPosition !== null && maxPosition !== null/);
  assert.match(temperaturePanel, /const temperatureSummary = buildTemperatureSummary/);
  assert.match(temperaturePanel, /\{delta !== null \|\| hasTemperatureRange \? \(/);
  assert.match(summary, /<small>\{t\('temperatureGap'\)\}<\/small><strong>\{formatTemperatureDelta\(delta\)\}/);
  assert.match(summary, /<small>\{t\('minTemperature'\)\}/);
  assert.match(summary, /<small>\{t\('maxTemperature'\)\}/);
  assert.doesNotMatch(summary, /targetTemperature|temperatureAverage|temperatureMeasurement|measurementCount/);
});

test('manual temperature editor is progressive and supports cancel', () => {
  const temperaturePanel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function buildTemperatureSummary'),
  );
  assert.match(temperaturePanel, /isEditingTemperature/);
  assert.match(temperaturePanel, /manualTemperatureAction/);
  assert.match(temperaturePanel, /closeTemperatureEditor/);
  assert.match(temperaturePanel, /setIsEditingTemperature\(false\)/);
  assert.match(temperaturePanel, /<TemperatureEntryModal/);
  assert.match(temperaturePanel, /<ModalPortal>/);
  assert.match(temperaturePanel, /role="dialog"/);
  assert.match(temperaturePanel, /dateRef\.current\?\.focus\(\)/);
  assert.match(temperaturePanel, /temperatureActionRef\.current\?\.focus\(\)/);
  assert.match(temperaturePanel, /restoreTemperatureFocus\.current = true/);
  assert.match(temperaturePanel, /event\.key === 'Escape'/);
  assert.match(temperaturePanel, /event\.key !== 'Tab'/);
  assert.match(temperaturePanel, /onSubmit=\{handleManualTemperatureSubmit\}/);
  assert.match(temperaturePanel, /onClick=\{onCancel\}/);
  assert.doesNotMatch(temperaturePanel, /secondary-button zone-temperature-action/);
});

test('occupancy distinguishes zero capacity from missing capacity without available-space copy', () => {
  assert.match(detailSource, /capacity === null/);
  assert.match(detailSource, /capacity > 0/);
  assert.match(zonesSource, /capacity == null \? String\(boxCount\) : `\$\{boxCount\} \/ \$\{capacity\}`/);
  assert.doesNotMatch(detailSource, /zonePlacesAvailable|zoneCapacityExceeded/);
  assert.doesNotMatch(frSource, /places disponibles/);
  assert.doesNotMatch(enSource, /spaces available/);
});

test('page orders thermal, salinity, and asymmetric movement/sidebar layout', () => {
  assert.ok(detailSource.indexOf('<TemperatureControlPanel') < detailSource.indexOf('<ZoneFunctionalSections'));
  const sections = zonesSource.slice(zonesSource.indexOf('function ZoneFunctionalSections'), zonesSource.indexOf('function ZoneBoxesPage'));
  assert.ok(sections.indexOf('zone-salinity-section') < sections.indexOf('zone-operational-layout'));
  assert.ok(sections.indexOf('<ZoneRecentMovements') < sections.indexOf('zone-operational-sidebar'));
  assert.ok(sections.indexOf('zone-occupancy-section') < sections.indexOf('zone-probes-section'));
  assert.match(stylesSource, /\.zone-operational-layout \{[^}]*grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/s);
  assert.match(stylesSource, /@media \(max-width: 1280px\) \{\s*\.zone-operational-layout \{\s*grid-template-columns: minmax\(0, 1fr\)/s);
  assert.match(stylesSource, /\.zone-operational-sidebar \{[^}]*align-content: start/s);
  assert.match(sections, /zone-box-directory-trigger/);
  assert.equal((sections.match(/zone-box-directory-trigger/g) ?? []).length, 1);
});

test('zone page contains box-detail grid areas without implicit page columns', () => {
  assert.match(stylesSource, /\.zone-page \{\s*grid-template-columns: minmax\(0, 1fr\);\s*\}/);
  assert.doesNotMatch(stylesSource, /\.zone-overview \{\s*grid-template-columns: minmax\(0, 1fr\)/);
  assert.match(stylesSource, /\.zone-page > \.zone-salinity-section \{\s*grid-area: auto;\s*\}/);
  assert.match(detailSource, /measurement-form-section measurement-module is-expanded zone-salinity-section zone-salinity-editor-section/);
  assert.match(detailSource, /last-reading-card measurement-summary zone-functional-section zone-salinity-section/);
  assert.match(boxStylesSource, /\.box-page-grid \{[^}]*"form last"[^}]*"insights insights"/s);
  for (const [selector, area] of [
    ['measurement-form-section', 'form'],
    ['last-reading-card', 'last'],
    ['measurement-module', 'last'],
    ['box-insights-section', 'insights'],
  ]) {
    assert.match(boxStylesSource, new RegExp(`\\.box-page-grid > \\.${selector} \\{ grid-area: ${area}; \\}`));
    assert.doesNotMatch(boxStylesSource, new RegExp(`(?:^|\\n)\\.${selector} \\{ grid-area: ${area}; \\}`, 'm'));
  }
  assert.ok(detailSource.indexOf('<header className="entity-header') < detailSource.indexOf('<TemperatureControlPanel'));
  const sections = detailSource.slice(detailSource.indexOf('function ZoneFunctionalSections'));
  assert.ok(sections.indexOf('zone-salinity-section') < sections.indexOf('zone-operational-layout'));
  assert.equal((stylesSource.match(/grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/g) ?? []).length, 1);
  assert.match(stylesSource, /\.zone-operational-layout \{[^}]*grid-template-columns: minmax\(0, 2\.3fr\) minmax\(0, 1fr\)/s);
});

test('recent movements replace any generic or biological activity section', () => {
  assert.match(detailSource, /<ZoneRecentMovements/);
  assert.doesNotMatch(detailSource, /old boxes|archived boxes|anciennes boîtes|boîtes archivées/i);
});

test('salinity reuses the compact reading summary and progressive editor for create and update', () => {
  assert.match(detailSource, /last-reading-card measurement-summary zone-functional-section zone-salinity-section/);
  assert.match(detailSource, /measurement-summary-edit-button/);
  assert.match(detailSource, /name=\{zone\.latest_salinity\?\.can_edit \? 'edit' : 'plus'\}/);
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
  assert.match(appSource, /apiPatch<ThermalZone>[\s\S]*salinity\/\$\{measurementId\}\//);
  assert.match(detailSource, /zoneNoProbe/);
  assert.match(detailSource, /rows=\{3\}/);
  assert.match(stylesSource, /\.zone-page \.last-reading-card\.measurement-summary\.zone-salinity-section \{[^}]*minmax\(0, 1fr\)/s);
  assert.match(stylesSource, /\.zone-salinity-section \.metric strong \{[^}]*color: var\(--color-primary-hover\)/s);
  assert.match(stylesSource, /\.zone-salinity-section \.metric \{[^}]*background: var\(--color-primary-faint\)/s);
  assert.match(stylesSource, /\.zone-salinity-entry-grid \{[^}]*minmax\(0, 1fr\)/s);

  assert.match(stylesSource, /\.zone-salinity-entry-grid \.measurement-date-field \{[^}]*display: grid/s);
  assert.match(stylesSource, /\.zone-salinity-entry-grid \.notes-field textarea \{[^}]*min-height: 120px/s);
  assert.match(stylesSource, /\.zone-salinity-section \.measurement-summary-edit-button \{[^}]*position: static/s);
});

test('salinity uses server capability for correction versus a new reading', () => {
  assert.match(detailSource, /const latestSalinity = zone\.latest_salinity\?\.can_edit \? zone\.latest_salinity : null/);
  assert.match(detailSource, /zone\.latest_salinity\?\.can_edit \? 'edit' : 'plus'/);

  assert.match(detailSource, /setEditingSalinityId\(latestSalinity\?\.id \?\? null\)/);
  assert.match(detailSource, /aria-label=\{t\(zone\.latest_salinity\?\.can_edit \? 'zoneSalinityEditAction' : 'zoneSalinityCreateAction'\)\}/);
  assert.match(detailSource, /name=\{zone\.latest_salinity\?\.can_edit \? 'edit' : 'plus'\}/);
  assert.match(detailSource, /if \(editingSalinityId !== null\) \{[\s\S]*onUpdateManualSalinity[\s\S]*\} else \{[\s\S]*onRecordManualSalinity/);
  assert.doesNotMatch(detailSource, /new Date\(zone\.latest_salinity|Date\.now\(\).*can_edit/);
  assert.match(appSource, /apiPost<ThermalZone>\(`\/api\/thermal-zones\/\$\{zoneId\}\/salinity\/`/);
  assert.match(detailSource, /salinity_edit_window_expired/);
  assert.match(detailSource, /await onRefreshZoneSalinityCapability\(zone\.id\)/);
  assert.match(appSource, /refreshZoneSalinityCapability/);
  assert.match(frSource, /zoneSalinityEditExpired:/);
  assert.match(enSource, /zoneSalinityEditExpired:/);
});

test('zone card shows only the latest salinity and never renders business history', () => {
  assert.match(detailSource, /zoneSalinityLatestTitle/);
  assert.match(detailSource, /formatDisplayDate\(zone\.latest_salinity\.measured_on\)/);
  assert.match(detailSource, /formatZoneSalinity\(zone\.latest_salinity\?\.salinity_psu\)/);
  assert.match(detailSource, /zone\.latest_salinity\?\.notes/);
  assert.match(zonesSource, /if \(salinity === null \|\| salinity === undefined \|\| salinity === ''\) return '-';/);
  assert.match(zonesSource, /Number\.isFinite\(numeric\) \? `\$\{numeric\.toFixed\(1\)\} PSU`/);
  assert.doesNotMatch(detailSource, /ZoneSalinityHistory|isSalinityHistoryOpen|salinityHistoryRevision|zoneSalinityHistoryAction|zoneSalinityHistoryHide/);
  assert.doesNotMatch(stylesSource, /\.zone-salinity-history(?:-trigger|-list|-reading)?\b/);
  assert.doesNotMatch(frSource, /zoneSalinityHistoryAction:|zoneSalinityHistoryHide:|zoneSalinityHistoryTitle:|Voir l’historique|Masquer l’historique/);
  assert.doesNotMatch(enSource, /zoneSalinityHistoryAction:|zoneSalinityHistoryHide:|zoneSalinityHistoryTitle:/);
  // The read-only history endpoint is retained solely for refreshing a stale correction capability.
  assert.match(appSource, /refreshZoneSalinityCapability[\s\S]*salinity\/history\//);
});

test('temperature uses a compact named icon action and integrated instrument summary', () => {
  const temperaturePanel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function buildTemperatureSummary'),
  );
  assert.match(temperaturePanel, /className="icon-button zone-temperature-action"/);
  assert.match(temperaturePanel, /aria-label=\{t\('manualTemperatureAction'\)\}/);
  assert.match(temperaturePanel, /<PolypbaseIcon name="plus"/);
  assert.doesNotMatch(temperaturePanel, /secondary-button zone-temperature-action/);
  assert.match(temperaturePanel, /className="temperature-instrument"/);
  assert.match(temperaturePanel, /className="temperature-summary"/);
  assert.match(temperaturePanel, /<TemperatureEntryModal/);
  assert.match(temperaturePanel, /className="zone-inline-editor"/);
  assert.match(temperaturePanel, /className="primary-button" type="submit"/);
  assert.match(stylesSource, /\.temperature-instrument \{[^}]*width: 100%/s);
  assert.match(stylesSource, /\.zone-temperature-modal \{[^}]*width: min\(100%, 540px\)/s);
});

test('zone detail uses one aligned instrument and compact operational sections', () => {
  const temperaturePanel = zonesSource.slice(
    zonesSource.indexOf('function TemperatureControlPanel'),
    zonesSource.indexOf('function buildTemperatureSummary'),
  );
  assert.match(temperaturePanel, /<b>\{t\('targetTemperature'\)\} \{formatTemperature\(targetTemperature/);
  assert.match(temperaturePanel, /measurementCount === 1 \? t\('temperatureMeasurement'\) : t\('temperatureAverage'\)/);
  assert.match(stylesSource, /\.zone-temperature-heading \{[^}]*width: 100%/s);
  assert.match(stylesSource, /\.temperature-instrument \{[^}]*width: 100%/s);
  assert.match(stylesSource, /\.temperature-summary \{[^}]*grid-template-columns: repeat\(3, minmax\(0, 1fr\)\)/s);
  assert.doesNotMatch(stylesSource, /\.temperature-summary \.is-primary/);
  assert.match(stylesSource, /\.temperature-summary \{\s*grid-template-columns: repeat\(2, minmax\(0, 1fr\)\)/s);
  assert.match(stylesSource, /\.zone-salinity-section\.has-edit-capability > div:first-child,\s*\.zone-salinity-section\.has-edit-capability \.last-reading-comment \{\s*padding-inline-end: 0/s);
  assert.ok(temperaturePanel.indexOf('id="zone-temperature-error"') < temperaturePanel.indexOf('zone-inline-editor-actions'));
  assert.match(stylesSource, /\.zone-salinity-editor-section \.inline-error \{[^}]*margin: 0/s);
});

test('thermal ruler separates observed and tick-label lanes', () => {
  assert.match(stylesSource, /\.temperature-ruler\s*\{[^}]*height:\s*116px/s);
  assert.match(stylesSource, /\.temperature-ruler-tick b\s*\{[^}]*top:\s*44px/s);
  assert.match(stylesSource, /\.temperature-observed-marker b\s*\{[^}]*top:\s*24px/s);
});

test('new zone detail copy exists in French and English', () => {
  for (const key of requiredKeys) {
    assert.match(frSource, new RegExp(`\\b${key}:`), `missing French key ${key}`);
    assert.match(enSource, new RegExp(`\\b${key}:`), `missing English key ${key}`);
  }
  assert.match(frSource, /cancel: 'Annuler'/);
  assert.match(enSource, /cancel: 'Cancel'/);
});
