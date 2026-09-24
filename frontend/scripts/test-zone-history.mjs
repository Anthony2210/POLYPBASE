import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const appSource = readFileSync(new URL('../src/App.tsx', import.meta.url), 'utf8');
const historySource = readFileSync(new URL('../src/components/ZoneMovementHistory.tsx', import.meta.url), 'utf8');
const previewSource = readFileSync(new URL('../src/components/BoxTrackingPreview.tsx', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../src/styles/pages/zones.css', import.meta.url), 'utf8');
const frSource = readFileSync(new URL('../src/i18n/fr.ts', import.meta.url), 'utf8');
const enSource = readFileSync(new URL('../src/i18n/en.ts', import.meta.url), 'utf8');

const historyKeys = [
  'zoneRecentMovementsTitle',
  'zoneMovementHistoryTitle',
  'zoneMovementHistoryBack',
  'zoneMovementEntries',
  'zoneMovementExits',
  'zoneMovementAllEntries',
  'zoneMovementAllExits',
  'zoneMovementEntriesEmpty',
  'zoneMovementExitsEmpty',
  'zoneMovementDirectionFilter',
  'zoneMovementFlowChartLabel',
  'zoneMovementWeekShort',
  'zoneMovementWeekSummary',
  'zoneMovementFrom',
  'zoneMovementTo',
  'zoneMovementRetry',
  'zoneMovementPagination',
  'zoneMovementRange',
  'zoneMovementPrevious',
  'zoneMovementNext',
];

test('zone movement history route carries an explicit direction filter', () => {
  assert.match(appSource, /zoneHistoryDirection\?: 'arrival' \| 'departure'/);
  assert.match(appSource, /`\/zones\/\$\{zoneId\}\/history\?direction=\$\{direction\}`/);
  assert.match(appSource, /new URLSearchParams\(window\.location\.search\)\.get\('direction'\)/);
  assert.match(appSource, /onChangeDirection=\{\(direction\) => openZoneHistory/);
});

test('recent movements use the bounded summary endpoint with independent columns', () => {
  assert.match(historySource, /history\/summary\//);
  assert.match(historySource, /summary\.recent_arrivals/);
  assert.match(historySource, /summary\.recent_departures/);
  assert.match(historySource, /direction="arrival"/);
  assert.match(historySource, /direction="departure"/);
  assert.match(historySource, /zoneMovementAllEntries/);
  assert.match(historySource, /zoneMovementAllExits/);
});

test('weekly movement chart is factual, discrete, distinctly styled, and accessible', () => {
  assert.match(historySource, /summary\.weeks\.map/);
  assert.match(historySource, /week\.entry_count/);
  assert.match(historySource, /week\.exit_count/);
  assert.match(historySource, /<rect/);
  assert.match(historySource, /role="img"/);
  assert.match(historySource, /className="sr-only"/);
  assert.match(stylesSource, /\.zone-movement-flow-entry\s*\{[^}]*var\(--color-success\)/s);
  assert.match(stylesSource, /\.zone-movement-flow-exit\s*\{[^}]*var\(--color-danger\)/s);
  assert.match(stylesSource, /\.zone-movement-flow-legend \.is-exit::before\s*\{[^}]*background: var\(--color-danger\)/s);
  assert.match(stylesSource, /\.zone-movement-flow-exit\s*\{\s*fill: var\(--color-danger\);\s*\}/);
  assert.doesNotMatch(stylesSource, /\.zone-movement-flow-exit\s*\{[^}]*stroke:|\.zone-movement-flow-(?:entry|exit)\s*\{[^}]*opacity:/s);
  assert.match(historySource, /groupWidth \* index \+ groupWidth \/ 2/);
  assert.match(historySource, /className="zone-movement-week-slot" key=\{week\.week_start\}/);
  assert.match(historySource, /hasBoth \? center \+ 2 : center - barWidth \/ 2/);
  assert.doesNotMatch(historySource, /Date\.parse\(week\.week_start\)|new Date\(week\.week_start\)/);
  assert.doesNotMatch(historySource, /forecast|smooth|danger|warning/i);
  assert.match(historySource, /FLOW_CHART_HEIGHT = 130/);
  assert.match(stylesSource, /\.zone-movement-flow-chart \{[^}]*width: 100%/s);
  assert.match(stylesSource, /\.zone-recent-movement-columns \{[^}]*min-width: 0/s);
  assert.match(stylesSource, /\.zone-movement-flow-grid text,\s*\.zone-movement-flow-week \{[^}]*font-size: 11px;[^}]*font-weight: 650/s);
  assert.match(stylesSource, /\.zone-movement-flow-legend \{[^}]*font-size: \.78rem;[^}]*font-weight: 700/s);
  assert.match(stylesSource, /\.zone-movement-flow-grid line \{[^}]*stroke-dasharray: 4 6/s);
  assert.doesNotMatch(stylesSource, /\.zone-movement-flow-chart \{[^}]*border:/s);
});

test('complete history filters the existing endpoint and preserves pagination', () => {
  assert.match(historySource, /history\/\?direction=\$\{direction\}&limit=/);
  assert.match(historySource, /aria-pressed=\{direction === option\}/);
  assert.match(historySource, /response\.previous/);
  assert.match(historySource, /response\.next/);
});

test('all movement rows reuse the lazy rich Inventory preview without actor data', () => {
  assert.match(historySource, /movement\.box_code/);
  assert.match(historySource, /<BoxTrackingPreview/);
  assert.match(historySource, /boxId=\{movement\.box_id\}/);
  assert.match(historySource, /className="box-inventory-identity zone-movement-identity"/);
  assert.match(historySource, /language=\{language\}/);
  assert.match(previewSource, /onPointerEnter=\{\(event\) =>/);
  assert.match(previewSource, /onFocus=\{\(\) =>/);
  assert.match(previewSource, /onClick=\{\(event\) =>/);
  assert.match(previewSource, /BoxTrackingChart/);
  assert.match(previewSource, /onSpeciesLoaded\(result\.species\.scientific_name\)/);
  assert.match(historySource, /ArrowDownToLine/);
  assert.match(historySource, /ArrowUpFromLine/);
  assert.match(historySource, /movement\.occurred_at/);
  assert.match(historySource, /movement\.related_zone_name/);
  assert.doesNotMatch(historySource, /movement\.(user|actor)|historyEnteredBy|avatar/i);
});

test('movement columns remain neutral with directional icons while the chart uses green and red', () => {
  assert.match(historySource, /zone-movement-row is-\$\{direction\}/);
  assert.match(historySource, /zone-recent-movement-column is-\$\{direction\}/);
  assert.match(historySource, /ArrowDownToLine/);
  assert.match(historySource, /ArrowUpFromLine/);
  assert.doesNotMatch(stylesSource, /\.zone-recent-movement-column\.is-(?:arrival|departure) h3/);
  assert.doesNotMatch(stylesSource, /\.zone-movement-row\.is-(?:arrival|departure) \.zone-movement-direction-icon/);
  assert.match(stylesSource, /\.zone-movement-direction-icon \{ color: var\(--color-primary-hover\); \}/);
  assert.match(stylesSource, /\.zone-recent-movements \.zone-movement-row \{[^}]*min-height: 50px;[^}]*padding: var\(--space-1\) 0/s);
  assert.match(stylesSource, /\.zone-movement-history-action \{[^}]*min-height: var\(--control-height\)/s);
});

test('entry and exit copy exists in French and English', () => {
  for (const key of historyKeys) {
    assert.match(frSource, new RegExp(`\\b${key}:`), `missing French key ${key}`);
    assert.match(enSource, new RegExp(`\\b${key}:`), `missing English key ${key}`);
  }
  assert.match(frSource, /zoneMovementEntries: 'Entrées'/);
  assert.match(frSource, /zoneMovementExits: 'Sorties'/);
  assert.match(enSource, /zoneMovementEntries: 'Entries'/);
  assert.match(enSource, /zoneMovementExits: 'Exits'/);
});
