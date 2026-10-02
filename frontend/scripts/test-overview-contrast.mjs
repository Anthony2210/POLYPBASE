import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const readSource = (relativePath) => readFileSync(new URL(relativePath, import.meta.url), 'utf8');
const tokensCss = readSource('../src/styles/tokens.css');
const overviewCss = readSource('../src/styles/pages/overview.css');
const overviewSource = readSource('../src/components/OverviewView.tsx');
const normalize = (value) => value.trim().replace(/\s+/g, ' ');

// Source-contract checks, not a browser CSS engine. Keep selectors exact so a
// specificity change requires reviewing the selected/focused cascade again.
function cssRules(source) {
  return [...source.replace(/\/\*[\s\S]*?\*\//g, '').matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .map((match) => ({
      selector: normalize(match[1]),
      declarations: new Map(match[2].split(';').filter((entry) => entry.trim()).map((entry) => {
        const colon = entry.indexOf(':');
        assert.ok(colon > 0, `Invalid CSS declaration: ${entry}`);
        return [entry.slice(0, colon).trim(), normalize(entry.slice(colon + 1))];
      })),
      index: match.index,
    }));
}

const rules = cssRules(overviewCss);
const tokenRules = cssRules(tokensCss);
const tokens = tokenRules.find((rule) => rule.selector === ':root')?.declarations;
assert.ok(tokens, 'The color tokens must have a :root rule');

function rule(selector) {
  const matches = rules.filter((candidate) => candidate.selector === normalize(selector));
  assert.ok(matches.length > 0, `Missing CSS rule for ${selector}`);
  // Existing responsive repeats only change sizing/spacing. Do not let a
  // repeated rule silently override a contrast or selection declaration.
  for (const repeated of matches.slice(1)) {
    for (const property of repeated.declarations.keys()) {
      assert.doesNotMatch(property, /^(?:color$|background|border|outline|opacity$|filter$|--status-)/,
        `Repeated ${selector} changes ${property}; review its cascade`);
    }
  }
  return matches[0];
}

function expectDeclarations(selector, expected) {
  const actual = rule(selector);
  for (const [property, value] of Object.entries(expected)) {
    assert.equal(actual.declarations.get(property), value, `${selector}: ${property}`);
  }
  return actual;
}

function resolveTokens(value, localTokens = new Map(), seen = new Set()) {
  return value.replace(/var\((--[\w-]+)\)/g, (_, name) => {
    assert.ok(!seen.has(name), `Circular color token: ${name}`);
    const resolved = localTokens.get(name) ?? tokens.get(name);
    assert.ok(resolved, `Missing color token: ${name}`);
    return resolveTokens(resolved, localTokens, new Set([...seen, name]));
  });
}

function luminance(hex) {
  assert.match(hex, /^#[\da-f]{6}$/i, `Expected an opaque sRGB hex color, got ${hex}`);
  const channels = [1, 3, 5].map((offset) => {
    const channel = Number.parseInt(hex.slice(offset, offset + 2), 16) / 255;
    return channel <= 0.04045 ? channel / 12.92 : ((channel + 0.055) / 1.055) ** 2.4;
  });
  return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
}

function contrast(foreground, background) {
  const first = luminance(foreground);
  const second = luminance(background);
  return (Math.max(first, second) + 0.05) / (Math.min(first, second) + 0.05);
}

const statusSelector = ':where(.overview-summary-actions button, .overview-zone-progress-card, .overview-box-summary-priority)';
const soonSelector = ':where(.overview-summary-actions button, .overview-box-summary-priority).is-soon';
const controls = [
  { name: 'summary', selector: '.overview-summary-actions button', statuses: ['success', 'warning', 'danger'] },
  // OverviewView renders zone cards as is-ok or is-due, never is-soon.
  { name: 'zone progress', selector: '.overview-zone-progress-card', statuses: ['success', 'danger'] },
];
const statusOverrides = {
  success: null,
  warning: soonSelector,
  danger: `${statusSelector}.is-due`,
};
const trackBackground = 'color-mix(in srgb, var(--status-color) 14%, var(--color-surface))';

function statusTokens(status) {
  const result = new Map(rule(statusSelector).declarations);
  const override = statusOverrides[status];
  if (override) {
    for (const [name, value] of rule(override).declarations) result.set(name, value);
  }
  assert.equal(result.get('--status-color'), `var(--color-${status})`);
  assert.equal(result.get('--status-soft'), `var(--color-${status}-soft)`);
  return result;
}

function expectLater(selected, base) {
  assert.ok(selected.index > base.index, `${selected.selector} must follow ${base.selector}`);
}

test('WCAG contrast calculation uses linearized sRGB and known reference pairs', () => {
  assert.equal(contrast('#000000', '#ffffff'), 21);
  assert.equal(contrast('#101923', '#101923'), 1);
  assert.ok(Math.abs(contrast('#777777', '#ffffff') - 4.478089453577214) < 1e-12);
  assert.equal(contrast('#ffffff', '#777777'), contrast('#777777', '#ffffff'));
});

for (const control of controls) {
  test(`selected ${control.name} retains ink on status-soft and a dark double selection cue`, () => {
    expectDeclarations(control.selector, {
      border: '1px solid var(--color-line)',
      'border-left': '4px solid var(--status-color)',
    });
    expectDeclarations(`${control.selector}.is-active`, {
      color: 'var(--color-ink)',
      background: 'var(--status-soft)',
      'border-color': 'var(--color-ink)',
      outline: '1px solid var(--color-ink)',
      'outline-offset': '-2px',
    });
  });

  for (const status of control.statuses) {
    test(`selected ${control.name} ${status} text meets WCAG AA normal-text contrast (4.5:1)`, (context) => {
      const selected = rule(`${control.selector}.is-active`).declarations;
      const localTokens = statusTokens(status);
      const foreground = resolveTokens(selected.get('color'), localTokens);
      const background = resolveTokens(selected.get('background'), localTokens);
      const ratio = contrast(foreground, background);
      context.diagnostic(`${control.name} ${status}: ${foreground} on ${background} = ${ratio.toFixed(12)}:1 (minimum 4.5:1)`);
      assert.ok(ratio >= 4.5, `${control.name} ${status}: ${ratio}:1 is below 4.5:1`);
    });
  }
}

test('all selected summary labels and counts use ink, including the label marker', () => {
  expectDeclarations('.overview-summary-actions :is(span, strong)', { color: 'inherit' });
  const selectedCount = expectDeclarations('.overview-summary-actions button.is-active strong', {
    color: 'var(--color-ink)',
  });
  expectLater(selectedCount, rule('.overview-summary-actions strong'));
  expectDeclarations('.overview-summary-actions span::after', { background: 'var(--status-color)' });
  const selectedMarker = expectDeclarations('.overview-summary-actions button.is-active span::after', {
    background: 'var(--color-ink)',
  });
  expectLater(selectedMarker, rule('.overview-summary-actions span::after'));
});

test('selected zone names, descriptions, counts and totals override their unselected text colors with ink', () => {
  const selectedText = expectDeclarations('.overview-zone-progress-card.is-active :is(strong, small, em, span)', {
    color: 'var(--color-ink)',
  });
  for (const selector of [
    '.overview-zone-progress-card :is(strong, small, em)',
    '.overview-zone-progress-copy > strong',
    '.overview-zone-progress-copy > small',
    '.overview-zone-progress-count strong',
    '.overview-zone-progress-count span',
  ]) {
    // The exact selected selector has higher specificity than each base rule.
    expectLater(selectedText, rule(selector));
  }
});

test('zone progress keeps semantic status fills and the soft track when selected', () => {
  expectDeclarations('.overview-zone-progress-card i > b', { background: 'var(--status-color)' });
  const selectedFill = expectDeclarations('.overview-zone-progress-card.is-active b', {
    background: 'var(--status-color)',
  });
  expectLater(selectedFill, rule('.overview-zone-progress-card i > b'));
  expectDeclarations('.overview-zone-progress-card i', { background: trackBackground });
  const selectedTrack = expectDeclarations('.overview-zone-progress-card.is-active i', {
    background: trackBackground,
  });
  expectLater(selectedTrack, rule('.overview-zone-progress-card i'));
  for (const status of controls[1].statuses) {
    assert.equal(resolveTokens(selectedFill.declarations.get('background'), statusTokens(status)),
      resolveTokens(`var(--color-${status})`));
  }
  assert.match(overviewSource, /<i aria-hidden="true">\s*<b style=\{\{ width: `\$\{Math\.round\(doneRatio \* 100\)\}%` \}\} \/>\s*<\/i>/);
});

test('selected focus-visible explicitly wins over the inset active selection outline', () => {
  const focused = expectDeclarations(':is(.overview-summary-actions button, .overview-zone-progress-card).is-active:focus-visible', {
    outline: '2px solid var(--color-primary)',
    'outline-offset': '2px',
  });
  for (const control of controls) {
    const unselectedFocus = expectDeclarations(`${control.selector}:focus-visible`, {
      outline: '2px solid var(--status-color)',
      'outline-offset': '2px',
    });
    const active = rule(`${control.selector}.is-active`);
    expectLater(active, unselectedFocus);
    expectLater(focused, active);
    // :is() takes the summary branch's specificity. With .is-active and
    // :focus-visible this is (0,4,1), above active (0,2,1)/(0,2,0).
    // Do not accept an earlier generic focus rule: active would override it.
    assert.notEqual(focused.declarations.get('outline'), active.declarations.get('outline'));
    assert.notEqual(focused.declarations.get('outline-offset'), active.declarations.get('outline-offset'));
  }
});

test('native summary buttons keep aria-pressed in sync with selected status classes', () => {
  const buttons = [...overviewSource.matchAll(/<button\b[\s\S]*?<\/button>/g)].map((match) => match[0]);
  const summaryButtons = buttons.filter((button) => /aria-pressed=\{focusFilter ===/.test(button));
  assert.equal(summaryButtons.length, 3);
  for (const status of ['done', 'soon', 'due']) {
    const button = summaryButtons.find((candidate) => candidate.includes(`aria-pressed={focusFilter === '${status}'}`));
    assert.ok(button, `Missing ${status} summary toggle`);
    assert.match(button, /type="button"/);
    assert.ok(button.includes(`className={focusFilter === '${status}' ? 'is-active is-${status}' : 'is-${status}'}`));
    assert.ok(button.includes(`onClick={() => toggleFocusFilter('${status}')}`));
    assert.match(button, /<span>[\s\S]*?<\/span>\s*<strong>[\s\S]*?<\/strong>/);
  }
});

test('native zone buttons keep aria-pressed in sync with selection and existing status meanings', () => {
  assert.match(overviewSource, /const isZoneActive = zoneFilter === summary\.zoneName;/);
  const zoneButtons = [...overviewSource.matchAll(/<button\b[\s\S]*?<\/button>/g)]
    .map((match) => match[0]).filter((button) => button.includes('overview-zone-progress-card'));
  assert.equal(zoneButtons.length, 1);
  const [button] = zoneButtons;
  assert.match(button, /type="button"/);
  assert.match(button, /aria-pressed=\{isZoneActive\}/);
  assert.ok(button.includes("${summary.due ? 'is-due' : 'is-ok'} ${isZoneActive ? 'is-active' : ''}"));
  assert.match(button, /onClick=\{\(\) => toggleZoneFilter\(summary\.zoneName\)\}/);
});
