import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const zonesSource = readFileSync(new URL('../src/components/ZonesView.tsx', import.meta.url), 'utf8');
const previewSource = readFileSync(new URL('../src/components/BoxTrackingPreview.tsx', import.meta.url), 'utf8');
const stylesSource = readFileSync(new URL('../src/styles/pages/zones.css', import.meta.url), 'utf8');
const popoverStyles = readFileSync(new URL('../src/styles/components/popovers.css', import.meta.url), 'utf8');
const pageSource = zonesSource.slice(
  zonesSource.indexOf('export function ZoneBoxesPage'),
  zonesSource.indexOf('function buildZoneOverviewEntry'),
);

test('zone box directory uses the fiche-family identity header without legacy KPIs', () => {
  const header = pageSource.slice(pageSource.indexOf('<header className="entity-header'), pageSource.indexOf('</header>'));
  assert.match(header, /zone-sheet-hero zone-directory-hero/);
  assert.match(header, /zone\.organization\.name/);
  assert.doesNotMatch(header, /zone-directory-summary|<Metric|zoneSummaryAlive|taxonomySpecies/);
});

test('zone box rows preserve measurements and both canonical dates including scientific zero', () => {
  assert.match(pageSource, /measurement\?\.polyp_count \?\? '-'/);
  assert.match(pageSource, /measurement\?\.ephyrae_count \?\? '-'/);
  assert.match(pageSource, /box\.current_location_started_at/);
  assert.match(pageSource, /zoneCurrentStaySince/);
  assert.match(pageSource, /measurement\.measured_on/);
  assert.match(pageSource, /latestReadingDate/);
});

test('zone box P and E values reuse established semantic series classes', () => {
  assert.match(pageSource, /className="is-polyps"/);
  assert.match(pageSource, /className="is-ephyrae"/);
  assert.match(stylesSource, /\.zone-directory-counts strong\s*\{[^}]*var\(--color-primary\)/s);
  assert.match(stylesSource, /\.zone-directory-counts \.is-ephyrae strong\s*\{[^}]*var\(--color-ephyrae\)/s);
});

test('directory removes follow-up status and duplicate local number from the visible row', () => {
  assert.doesNotMatch(pageSource, /getZoneBoxFollowUp|weeklyDueNow|weeklyDueSoon|weeklyUpToDate|zone-directory-follow-up/);
  const row = pageSource.slice(pageSource.indexOf('<div className="zone-directory-box-row"'), pageSource.indexOf('</div>', pageSource.indexOf('<div className="zone-directory-box-row"')));
  assert.doesNotMatch(row, /box\.local_code|<small>\{box\.local_code\}/);
});

test('zone boxes reuse the lazy rich Inventory preview and canonical identity style', () => {
  assert.match(pageSource, /className="box-inventory-identity zone-directory-box-identity"/);
  assert.match(pageSource, /<BoxTrackingPreview/);
  assert.match(pageSource, /speciesName=\{box\.species\.scientific_name\}/);
  assert.match(pageSource, /className="zone-directory-box-open"/);
  assert.match(pageSource, /onClick=\{\(\) => onOpenBox\(box\.id\)\}/);
  assert.doesNotMatch(pageSource, /zone-directory-row-arrow/);
  assert.doesNotMatch(stylesSource, /\.zone-directory-row-arrow/);
  assert.match(previewSource, /useAnchoredPopover<HTMLAnchorElement>/);
  assert.match(previewSource, /onPointerEnter=\{\(event\) =>/);
  assert.match(previewSource, /event\.pointerType === 'mouse'/);
  assert.match(previewSource, /openTimer\.current = window\.setTimeout\(\(\) => setIsOpen\(true\), 280\)/);
  assert.match(previewSource, /closeTimer\.current = window\.setTimeout/);
  assert.match(previewSource, /onFocus=\{\(\) =>/);
  assert.match(previewSource, /onClick=\{\(event\) =>/);
  assert.match(previewSource, /role="dialog"/);
  assert.match(previewSource, /apiGet<BoxDetail>\(`\/api\/boxes\/\$\{boxId\}\//);
  assert.match(previewSource, /lazy\(async \(\) =>/);
});

test('box rows have stable hover presentation and visible keyboard focus', () => {
  assert.doesNotMatch(stylesSource, /\.zone-directory-box-row:hover/);
  assert.doesNotMatch(stylesSource, /\.zone-directory-box-row[^}]*transform/s);
  assert.match(stylesSource, /\.zone-directory-box-identity a:focus-visible/);
  assert.match(stylesSource, /\.zone-directory-box-open:focus-visible/);
  assert.match(popoverStyles, /\.box-tracking-preview/);
  assert.doesNotMatch(popoverStyles, /\.zone-box-identity-popover/);
});
