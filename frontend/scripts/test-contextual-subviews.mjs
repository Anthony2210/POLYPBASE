import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';
import * as jsxRuntime from 'react/jsx-runtime';
import ts from 'typescript';

const read = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const backSource = read('../src/components/DetailBackButton.tsx');
const zonesSource = read('../src/components/ZonesView.tsx');
const historySource = read('../src/components/ZoneMovementHistory.tsx');
const buttonsCss = read('../src/styles/components/buttons.css');
const zonesCss = read('../src/styles/pages/zones.css');

function load(source, imports) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  const exports = {};
  vm.runInNewContext(outputText, {
    exports,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    },
  });
  return exports;
}

function nodes(tree) {
  const result = [];
  function visit(node) {
    if (Array.isArray(node)) return node.forEach(visit);
    if (!node || typeof node !== 'object') return;
    result.push(node);
    visit(node.props?.children);
  }
  visit(tree);
  return result;
}

const ArrowLeft = () => null;
const ArrowDownToLine = () => null;
const ArrowUpFromLine = () => null;
const Placeholder = () => null;
const hooks = {
  useMemo: (factory) => factory(),
  useState: (initial) => [typeof initial === 'function' ? initial() : initial, () => {}],
  useEffect: () => {},
  useLayoutEffect: () => {},
  useRef: (current) => ({ current }),
};
const t = (key) => `translated:${key}`;

function views(isDesktopApp, { zoneImports = {}, historyImports = {} } = {}) {
  const DetailBackButton = load(backSource, {
    'react/jsx-runtime': jsxRuntime,
    'lucide-react': { ArrowLeft },
    '../hooks/useIsDesktopApp': { useIsDesktopApp: () => isDesktopApp },
  }).default;
  const common = {
    react: hooks,
    'react/jsx-runtime': jsxRuntime,
    './DetailBackButton': { default: DetailBackButton },
    './BoxTrackingPreview': { default: Placeholder },
    './PageLoader': { default: Placeholder },
    '../utils/errors': { getErrorMessage: (error) => error.message },
  };
  const zones = load(zonesSource, {
    ...common,
    '../api/client': {},
    '../hooks/useProbeMonitoring': { default: (zone) => ({ monitoring: zone.probe_monitoring, error: null, revalidate() {} }) },
    // Shallow page renders keep TemperatureControlPanel as an element; fail if its hook runs.
    '../hooks/useThermalControl': { default: () => assert.fail('Shallow page renders must not run thermal control') },
    './ZoneProbeCharts': { ZoneProbeChart: Placeholder, ZoneThermalTrend: Placeholder, useZoneChartReadings: () => ({ initial: { settled: true, data: null } }) },
    '../utils/dateFormat': { formatDisplayDate: (value) => value, formatDisplayDateTime: (value) => value, getDocumentLocale: () => 'en' },
    '../utils/probeStatus': { formatProbeReadingTime: (value) => value, getProbeElapsedTime: () => null },
    '../utils/temperatureScale': {},
    './ModalPortal': { default: Placeholder },
    './PolypbaseIcon': { default: Placeholder },
    './ZoneMovementHistory': { ZoneRecentMovements: Placeholder },
    ...zoneImports,
  });
  const history = load(`${historySource}\nexport { MovementRows };`, {
    ...common,
    'lucide-react': { ArrowDownToLine, ArrowUpFromLine },
    '../api/client': {
      getStoredActiveOrganizationId: () => 'test',
      apiGet: () => assert.fail('Focused render tests must not make API requests'),
    },
    '../utils/boxHistory': { zoneHistoricalIntervalLabel: () => 'interval' },
    '../utils/dateFormat': { formatDisplayDateTime: (value) => value },
    './SkeletonRows': { default: Placeholder },
    ...historyImports,
  });
  return { DetailBackButton, ...zones, ...history, ZoneMovementHistoryPage: history.default };
}

for (const desktop of [true, false]) {
  test(`shared back button preserves its label and callback (${desktop ? 'desktop' : 'touch'})`, () => {
    const { DetailBackButton } = views(desktop);
    let calls = 0;
    const onBack = () => calls++;
    const button = DetailBackButton({ label: 'Translated destination', onBack, desktopClassName: 'box-back-button' });
    assert.equal(button.type, 'button');
    assert.equal(button.props.type, 'button');
    assert.equal(button.props['aria-label'], 'Translated destination');
    assert.equal(button.props.title, 'Translated destination');
    assert.equal(button.props.onClick, onBack);
    button.props.onClick();
    assert.equal(calls, 1);
    if (desktop) {
      assert.equal(button.props.children, 'Translated destination');
      assert.match(button.props.className, /\btext-button\b/);
      assert.match(button.props.className, /\bbox-back-button\b/);
    } else {
      assert.match(button.props.className, /\bicon-button\b/);
      assert.match(button.props.className, /\bdetail-back-button--icon\b/);
      assert.equal(button.props.children.type, ArrowLeft);
      assert.equal(button.props.children.props['aria-hidden'], 'true');
      assert.doesNotMatch(button.props.className, /box-back-button/);
    }
    const defaultButton = DetailBackButton({ label: 'Back', onBack });
    assert.doesNotMatch(defaultButton.props.className, /undefined/);
  });

  for (const missing of [false, true]) {
    for (const [pageName, label] of [
      ['ZoneDetailPage', 'back'],
      ['ZoneBoxesPage', 'back'],
      ['ZoneMovementHistoryPage', 'back'],
    ]) {
      test(`${pageName} uses shared back control (${desktop ? 'desktop' : 'touch'}, ${missing ? 'missing' : 'populated'})`, () => {
        const loaded = views(desktop);
        const onBack = () => {};
        const tree = loaded[pageName]({
          zone: missing ? null : { id: 7, name: 'Test zone', organization: { name: 'Test lab' }, probes: [] },
          boxes: [], isLoading: false, language: 'en', direction: 'arrival', onBack, t,
        });
        const backs = nodes(tree).filter((node) => node.type === loaded.DetailBackButton);
        assert.equal(backs.length, 1);
        assert.equal(backs[0].props.label, t(label));
        assert.equal(backs[0].props.onBack, onBack);
        const button = loaded.DetailBackButton(backs[0].props);
        assert.equal(button.props.onClick, onBack);
        if (desktop) assert.match(button.props.className, /\bzone-back-button\b/);
        else assert.match(button.props.className, /\bdetail-back-button--icon\b/);
        if (missing) assert.ok(nodes(tree).some((node) => node.props?.children === t('noZone')));
        if (!missing && pageName === 'ZoneBoxesPage') {
          assert.ok(nodes(tree).some((node) => node.props?.className === 'entity-header entity-header--zone zone-sheet-hero zone-directory-hero'));
        }
      });
    }
  }
}

for (const failure of [null, 'chart', 'history']) {
  test(`zone detail keeps thermal charts and historical movements independent (${failure ?? 'success'})`, () => {
    const measuredAt = '2026-11-09T10:00:00Z';
    const monitoring = { has_collected_probes: true, last_reading_at: measuredAt, manual_entry_available: false, fallback_after_hours: 6 };
    const zone = {
      id: 7, name: 'Test zone', organization: { id: 1, name: 'Test lab' }, capacity: 0,
      target_temperature_c: '0', latest_temperature: null, latest_salinity: null,
      probes: [{ id: 3, code: 'PROBE-3', location: 'Left', is_active: true, is_collected: true }],
      probe_monitoring: monitoring,
    };
    const data = {
      zone_id: zone.id, start: '2026-11-08T10:00:00Z', end: measuredAt, resolution_minutes: 1,
      target_temperature_c: '0', monitoring,
      probes: [{ ...zone.probes[0], external_channel: 1, sampling_interval_seconds: 60,
        points: [[Date.parse(measuredAt), 0, null, null]],
        point_sources: [[[Date.parse(measuredAt), null, '0', 1, 0, 0, Date.parse(measuredAt)]]] }],
    };
    const chartError = failure === 'chart' ? new Error('Chart unavailable') : null;
    const chart = {
      custom: null, setCustom() {}, initial: { settled: true, data: chartError ? null : data },
      readings: { data: chartError ? null : data, error: chartError, isLoading: false, retry() {} },
    };
    const interval = { from_iso_year: 2026, from_iso_week: 38, to_iso_year: 2026, to_iso_week: 40 };
    const movement = {
      location_id: null, occurred_at: null, temporal_precision: 'observation_interval',
      transition_id: 'historical-42', historical_interval: interval, event_type: 'arrival',
      box_id: 42, box_code: 'BOX-42', box_status: 'active', related_zone_id: 8, related_zone_name: 'Origin',
    };
    const summary = {
      recent_arrivals: [movement], recent_departures: [], weeks: [],
      historical_counts: { entry_count: 1, exit_count: 0 },
    };
    const Trend = () => null;
    const CombinedChart = () => null;
    let loaded;
    const RecentMovements = (props) => loaded.ZoneRecentMovements(props);
    loaded = views(true, {
      zoneImports: {
        '../hooks/useThermalControl': { default: () => ({ summary: {
          temperature_c: '0', oldest_measured_at: measuredAt, newest_measured_at: measuredAt, included_probe_count: 1,
        }, error: null }) },
        '../utils/temperatureScale': { buildTemperatureScale: () => null },
        './ZoneProbeCharts': {
          ZoneThermalTrend: Trend, ZoneProbeChart: CombinedChart,
          useZoneChartReadings(actualZone, enabled) {
            assert.equal(actualZone, zone);
            assert.equal(enabled, true);
            return chart;
          },
        },
        './ZoneMovementHistory': { ZoneRecentMovements: RecentMovements },
      },
      historyImports: {
        react: { ...hooks, useState(initial) {
          const [state, setState] = hooks.useState(initial);
          return state && Object.hasOwn(state, 'summary')
            ? [{ ...state, summary: failure === 'history' ? null : summary,
              error: failure === 'history' ? new Error('History unavailable') : null, isLoading: false }, setState]
            : [state, setState];
        } },
        '../utils/boxHistory': { zoneHistoricalIntervalLabel(actualInterval) {
          assert.equal(actualInterval, interval);
          return 'Between weeks 38 and 40 of 2026';
        } },
        '../utils/dateFormat': { formatDisplayDateTime: () => assert.fail('An interval movement has no exact timestamp') },
      },
    });
    const onOpenBox = () => {};
    const page = loaded.ZoneDetailPage({
      zone, boxes: [], isLoading: false, language: 'en', canRecordManualTemperature: false,
      onBack() {}, onOpenBox, onOpenHistory() {}, onOpenBoxes() {}, t,
    });
    const panelElement = nodes(page).find((node) => node.type.name === 'TemperatureControlPanel');
    const functionalElement = nodes(page).find((node) => node.type.name === 'ZoneFunctionalSections');
    assert.ok(panelElement);
    assert.ok(functionalElement);
    assert.ok(page.props.children.includes(panelElement));
    assert.ok(page.props.children.includes(functionalElement));
    // Expand the real composition only; chart leaves stay at their tested boundary.
    const panel = panelElement.type(panelElement.props);
    const functional = functionalElement.type(functionalElement.props);

    const combined = nodes(panel).find((node) => node.type === CombinedChart);
    const recent = nodes(functional).find((node) => node.type === RecentMovements);

    assert.ok(combined);
    assert.ok(recent);
    assert.ok(panel.props.children.includes(combined), 'The combined chart is outside the thermal control section');
    assert.equal(nodes(functional).some((node) => node.type === CombinedChart), false);
    assert.equal(nodes(panel).some((node) => node.type === RecentMovements), false);

    assert.equal(combined.props.chart, chart);
    assert.equal(combined.props.chart.readings.error, chartError);
    assert.equal(recent.props.zoneId, zone.id);
    assert.equal(recent.props.onOpenBox, onOpenBox);
    assert.ok(nodes(panel).some((node) => node.type === 'dd' && node.props.children === '0.0°C'));
    assert.equal(data.probes[0].points[0][1], 0);
    if (!chartError) assert.equal(combined.props.chart.readings.data, data);

    const history = recent.type(recent.props);
    if (failure === 'history') {
      assert.ok(nodes(history).some((node) => node.type === 'p' && node.props.children === 'History unavailable'));
      assert.equal(combined.props.chart.readings.data, data, 'History failure does not withdraw chart data');
    } else {
      assert.ok(!nodes(history).some((node) => node.type === 'p' && String(node.props.children).includes('Not placed on the chart')));
      assert.equal(nodes(history).some((node) => node.type.name === 'WeeklyMovementChart'), false);
      const column = nodes(history).find((node) => node.type.name === 'RecentMovementColumn' && node.props.direction === 'arrival');
      assert.ok(column);
      const rowsElement = nodes(column.type(column.props)).find((node) => node.type === loaded.MovementRows);
      assert.ok(rowsElement);
      const rows = rowsElement.type(rowsElement.props);
      assert.ok(nodes(rows).some((node) => node.type === 'span' && node.props.children === 'Between weeks 38 and 40 of 2026'));
      assert.equal(nodes(rows).some((node) => node.type === 'time'), false);
      const open = nodes(rows).find((node) => node.type === 'button');
      assert.match(open.props['aria-label'], /BOX-42, Between weeks 38 and 40 of 2026$/);
      assert.equal(nodes(rows).find((node) => node.type === Placeholder).props.boxId, movement.box_id);
    }
  });
}

test('shared touch back style owns the circular 44px contract without changing UI stroke', () => {
  const rule = buttonsCss.match(/\.detail-back-button--icon\s*\{([^}]*)\}/)?.[1];
  assert.ok(rule);
  for (const property of ['width', 'min-width', 'height']) assert.match(rule, new RegExp(`(?:^|[;\\s])${property}: 44px;`));
  assert.match(rule, /border-radius: 50%;/);
  assert.match(rule, /padding: 0;/);
  assert.match(buttonsCss, /stroke-width: 1\.25;/);
  assert.doesNotMatch(zonesCss, /\.zone-page > \.zone-back-action/);
});

for (const direction of ['arrival', 'departure']) {
  for (const showRelatedZone of [false, true]) {
    test(`shared MovementRows keeps only the arrow directional (${direction}, ${showRelatedZone ? 'full' : 'compact'})`, () => {
      const { MovementRows } = views(false);
      const onOpenBox = () => {};
      const tree = MovementRows({
        direction, showRelatedZone, language: 'en', onOpenBox, t,
        movements: [{ box_id: 42, box_code: 'BOX-42', location_id: 9, event_type: 'transfer', occurred_at: '2026-10-03T09:00:00Z', related_zone_name: 'Related zone' }],
      });
      const row = nodes(tree).find((node) => node.props?.className === `zone-movement-row is-${direction}`);
      assert.ok(row);
      const icon = row.props.children[0];
      assert.equal(icon.type, direction === 'arrival' ? ArrowDownToLine : ArrowUpFromLine);
      assert.equal(icon.props.className, 'zone-movement-direction-icon');
      assert.equal(icon.props['aria-hidden'], 'true');
      assert.equal(row.props.style, undefined);
      assert.equal(nodes(tree).find((node) => node.type === Placeholder).props.code, 'BOX-42');
      assert.equal(nodes(tree).find((node) => node.type === 'time').props.children, '2026-10-03T09:00:00Z');
      assert.equal(nodes(tree).filter((node) => node.props?.className === 'zone-movement-related').length, showRelatedZone ? 1 : 0);
    });
  }
}

test('compact and full history share MovementRows and directional color selectors target only icons', () => {
  assert.equal((historySource.match(/<MovementRows\b/g) ?? []).length, 2);
  assert.match(historySource, /showRelatedZone=\{false\}/);
  assert.match(historySource, /showRelatedZone\s+t=\{t\}/);
  const directionalRules = [...zonesCss.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, selector]) => /zone-movement-row\.is-(arrival|departure)/.test(selector));
  assert.equal(directionalRules.length, 2);
  for (const [, selector, declarations] of directionalRules) {
    const direction = selector.includes('is-arrival') ? 'arrival' : 'departure';
    assert.equal(selector.trim(), `.zone-movement-row.is-${direction} > .zone-movement-direction-icon`);
    assert.equal(declarations.trim(), `color: var(--color-${direction === 'arrival' ? 'success' : 'danger'});`);
  }
});
