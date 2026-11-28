// From frontend/: node --test scripts/qa-labels-browser.mjs
// Uses the installed Playwright Chromium, without Django or any external API.
import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { createServer as createHttpServer } from 'node:http';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import react from '@vitejs/plugin-react';
import { chromium } from 'playwright';
import { createServer } from 'vite';

const root = fileURLToPath(new URL('../', import.meta.url));
const organization = { id: 42, name: 'Demo Lab', slug: 'demo-lab' };
const species = {
  id: 1,
  scientific_name: 'Chrysaora chesapeakei',
  common_name: '',
  genus_species_code: 'CCH',
};
const qrSvg =
  '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 29 29"><path d="M4 4h7v7H4z M18 4h7v7h-7z M4 18h7v7H4z M14 14h3v3h-3z M21 21h4v4h-4z"/></svg>';
const today = '2026-11-27';

function measurement(id, polyp_count = 8, measured_on = today) {
  return {
    id,
    measured_on,
    polyp_count,
    ephyrae_count: 0,
    strobila_count: null,
    salinity_psu: null,
    culture_status: null,
    needs_attention: null,
    notes: null,
    user: null,
    user_identity: null,
    created_at: `${measured_on}T09:00:00Z`,
    can_edit: false,
    edit_deadline: null,
  };
}

function makeBox(
  id,
  {
    status = 'active',
    reading = measurement(id),
    strainName = 'Demo Culture',
  } = {},
) {
  const box_number = String(id).padStart(3, '0');
  return {
    id,
    organization,
    species,
    status,
    box_number,
    global_code: `ATL-CCH-1.${box_number}`,
    local_code: `CCH-1.${box_number}`,
    strain: {
      id,
      code: `CCH-ATL-${id}`,
      number: id,
      origin_code: 'ATL',
      species,
      name: strainName,
    },
    thermal_zone: null,
    entered_on: today,
    current_location_started_at: null,
    latest_measurement: reading,
    latest_salinity_psu: null,
    current_polyp_state: {
      polyp_count: reading?.polyp_count ?? null,
      revision: `demo-${id}`,
      source: reading
        ? { kind: 'measurement', id, timestamp: reading.created_at }
        : null,
    },
  };
}

const old = makeBox(199, { reading: measurement(199, 8, '2020-01-01') });
const zero = makeBox(200, { reading: measurement(200, 0) });
const named = makeBox(1, { strainName: 'Moonlight & <Blue>' });
const secondNamed = makeBox(2, { strainName: '紫色' });
const child = makeBox(201, { reading: null, strainName: null });
child.current_polyp_state = {
  polyp_count: null,
  revision: 'demo-subculture-201',
  source: {
    kind: 'subculture_initialization',
    id: 1,
    timestamp: `${today}T09:00:00Z`,
  },
};
const inactive = makeBox(202, { status: 'inactive' });
const pending = makeBox(203, { status: 'pending_review' });
const catalogue = [
  named,
  secondNamed,
  ...Array.from({ length: 196 }, (_, index) => makeBox(index + 3)),
  old,
  zero,
  child,
  inactive,
  pending,
];
const longName = makeBox(204, { strainName: 'North Pacific Rearing Group' });
const japanese = makeBox(205, { strainName: '北太平洋クラゲ' });
const overflow = makeBox(206, {
  strainName: `${'Oversized synthetic culture name '.repeat(5)}end marker`,
});
const unnamed = makeBox(207, { strainName: null });

let vite, server, cacheDir, browser, origin;
before(async () => {
  cacheDir = await mkdtemp(join(tmpdir(), 'polypbase-labels-qa-'));
  server = createHttpServer();
  // Do not load project env files or the app's dev-server API proxy.
  vite = await createServer({
    root,
    cacheDir,
    configFile: false,
    envDir: false,
    plugins: [react()],
    define: { __API_PROXY_ORIGIN__: 'null' },
    server: {
      middlewareMode: true,
      host: '127.0.0.1',
      ws: { server },
      watch: null,
    },
  });
  server.on('request', vite.middlewares);
  await new Promise((resolve, reject) =>
    server.once('error', reject).listen(0, '127.0.0.1', resolve),
  );
  origin = `http://127.0.0.1:${server.address().port}`;
  browser = await chromium.launch({
    args: [
      '--disable-background-networking',
      '--proxy-server=http://127.0.0.1:1',
    ],
  });
});

after(async () => {
  await browser?.close();
  await vite?.close();
  if (server?.listening)
    await new Promise((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
  if (cacheDir) await rm(cacheDir, { recursive: true, force: true });
});

function boxDetail(box) {
  return {
    ...box,
    dna_validation: null,
    can_edit_dna_validation: false,
    created_on: today,
    volume_liters: null,
    stop_reason: '',
    deactivated_on: null,
    notes: '',
    lineage: { parents: [], children: [] },
    locations: [],
    movements: [],
    biological_measurements: box.latest_measurement
      ? [box.latest_measurement]
      : [],
    biological_timeline: [],
    temperature_history: [],
    can_create_measurement: false,
    scan_url: `/bac/${box.id}/`,
    qr_image_url: `/boites/${box.id}/qr.svg`,
  };
}

async function demoPage(
  t,
  {
    language = 'en',
    width = 1440,
    height = 900,
    hasTouch = false,
    boxes = catalogue,
  } = {},
) {
  const context = await browser.newContext({
    viewport: { width, height },
    hasTouch,
    locale: language === 'ja' ? 'ja-JP' : 'en-US',
    serviceWorkers: 'block',
  });
  const page = await context.newPage();
  page.setDefaultTimeout(7000);
  await page.clock.setFixedTime(new Date(`${today}T12:00:00Z`));
  const offsets = [],
    unexpected = [],
    errors = [];
  page.on('pageerror', (error) => errors.push(error.message));
  t.after(async () => {
    await context.close();
    assert.deepEqual(unexpected, [], 'Unexpected requests were blocked');
    assert.deepEqual(errors, [], 'Uncaught browser errors');
  });

  await context.routeWebSocket('**/*', (route) => {
    if (new URL(route.url()).origin === origin.replace(/^http:/, 'ws:'))
      route.connectToServer();
    else {
      unexpected.push(`WebSocket ${new URL(route.url()).origin}`);
      return route.close();
    }
  });
  await context.route('**/*', async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const json = (data) => route.fulfill({ json: data });
    const reject = () => {
      unexpected.push(`${request.method()} ${url.origin}${path}${url.search}`);
      return route.abort();
    };
    if (url.origin !== origin) return reject();
    const detailFixtures = [
      named,
      secondNamed,
      longName,
      japanese,
      overflow,
      unnamed,
    ];
    if (
      request.method() === 'POST' &&
      detailFixtures.some((box) => path === `/api/boxes/${box.id}/access/`)
    ) {
      return request.headers()['x-organization-id'] === String(organization.id)
        ? route.fulfill({ status: 204 })
        : reject();
    }
    if (request.method() !== 'GET') return reject();
    if (path === '/api/auth/session/') return json({ demo_mode: true });
    if (path === '/api/profile/')
      return json({
        id: 1,
        email: 'labels@example.test',
        first_name: '',
        last_name: '',
        is_superuser: false,
        interface_language: language,
        active_organization: organization,
        organizations: [organization],
        available_languages: [],
        memberships: [
          {
            organization,
            role: 'lab_technician',
            role_label: 'Technician',
            is_responsable: false,
          },
        ],
      });
    if (path.startsWith('/api/') || path.startsWith('/boites/')) {
      if (request.headers()['x-organization-id'] !== String(organization.id))
        return reject();
      if (path === '/api/dashboard/')
        return json({ stats: {}, latest_entries: [], recent_accesses: [] });
      if (path === '/api/thermal-zones/')
        return json({ count: 0, next: null, previous: null, results: [] });
      if (path === '/api/boxes/') {
        if (
          url.searchParams.get('limit') !== '100' ||
          url.searchParams.has('q')
        )
          return reject();
        const offset = Number(url.searchParams.get('offset') ?? 0);
        offsets.push(offset);
        const pageUrl = (start) => `${origin}${path}?limit=100&offset=${start}`;
        return json({
          count: boxes.length,
          next: offset + 100 < boxes.length ? pageUrl(offset + 100) : null,
          previous: offset > 0 ? pageUrl(offset - 100) : null,
          results: boxes.slice(offset, offset + 100),
        });
      }
      if (
        detailFixtures.some((box) => path === `/api/boxes/${box.id}/history/`)
      )
        return json({ events: [] });
      if (
        detailFixtures.some((box) =>
          [
            `/api/boxes/${box.id}/temperatures/`,
            `/api/boxes/${box.id}/historical-temperatures/`,
          ].includes(path),
        )
      )
        return json({ count: 0, next: null, previous: null, results: [] });
      const fixture = detailFixtures.find((box) =>
        [
          `/api/boxes/by-code/${box.global_code}/`,
          `/api/boxes/${box.id}/`,
          `/boites/${box.id}/qr.svg`,
        ].includes(path),
      );
      if (fixture)
        return path.startsWith('/boites/')
          ? route.fulfill({ contentType: 'image/svg+xml', body: qrSvg })
          : json(boxDetail(fixture));
      return reject();
    }
    if (/^\/(accounts|bac)(\/|$)/.test(path)) return reject();
    return route.continue();
  });
  return { page, offsets };
}

function resultFor(page, box) {
  return page.getByRole('checkbox', {
    name: new RegExp(`${box.global_code.replace('.', '\\.')}\\s*$`),
  });
}

async function openPicker(t, options) {
  const demo = await demoPage(t, options);
  await demo.page.goto(`${origin}/labels`);
  const search = demo.page.locator('.labels-page').getByRole('searchbox');
  await search.waitFor();
  return { ...demo, search };
}

test('Labels includes all 201 active boxes across three pages, regardless of measurement', async (t) => {
  const { page, search, offsets } = await openPicker(t);
  assert.deepEqual(offsets, [0, 100, 200]);
  const visible = [];
  for (const box of [zero, child, old, inactive, pending]) {
    await search.fill(box.global_code);
    if (await resultFor(page, box).isVisible()) visible.push(box.id);
  }
  assert.deepEqual(
    visible,
    [zero.id, child.id, old.id],
    'Only the active cases should be selectable',
  );
  await search.clear();
  await page
    .getByRole('button', { name: 'Add 201 results', exact: true })
    .waitFor();
});

test('Labels searches the wire strain.name, not just strain code', async (t) => {
  const { page, search } = await openPicker(t);
  await search.fill('  mOoNLiGhT  ');
  const result = resultFor(page, named);
  assert.ok(
    await result.isVisible(),
    'The display name should match the active box',
  );
  await result.check();
  assert.ok(await result.isChecked());
  assert.equal(await page.locator('.label-box-row:visible').count(), 1);
  await page.getByRole('button', { name: 'Print 1', exact: true }).waitFor();
});

async function openQrModal(t, box, options) {
  const { page } = await demoPage(t, options);
  const title = options?.language === 'ja' ? 'QRコードラベル' : 'QR code label';
  await page.goto(`${origin}/boxes/${box.global_code}`);
  await interceptPrint(page);
  await page
    .getByRole('button', { name: `${title} ${box.global_code}`, exact: true })
    .click();
  const dialog = page.getByRole('dialog', { name: title });
  await dialog.waitFor();
  return { page, dialog };
}

async function interceptPrint(page) {
  await page.evaluate(() => {
    window.qaPrints = 0;
    window.qaDownloads = 0;
    document.addEventListener(
      'click',
      (event) => {
        if (
          event.target instanceof HTMLAnchorElement &&
          event.target.hasAttribute('download')
        ) {
          window.qaDownloads += 1;
        }
      },
      true,
    );
    const open = window.open.bind(window);
    window.open = (...args) => {
      const popup = open(...args);
      if (popup)
        popup.print = () => {
          window.qaPrints += 1;
        };
      return popup;
    };
  });
}

function assertNear(actual, mm, label) {
  assert.ok(
    Math.abs(actual - (mm * 96) / 25.4) < 0.1,
    `${label}: ${actual} CSS px != ${mm} mm`,
  );
}

async function assertPrintableGeometry(popup, box) {
  popup.setDefaultTimeout(7000);
  await popup.emulateMedia({ media: 'print' });
  await popup.evaluate(() => document.fonts.ready.then(() => true));
  const label = await popup.locator('.label').boundingBox();
  const qr = await popup.locator('.label-qr img').boundingBox();
  const band = await popup.locator('.label-main').boundingBox();
  assertNear(label.width, 41, 'label width');
  assertNear(label.height, 28, 'label height');
  assertNear(qr.width, 25, 'QR width');
  assertNear(qr.height, 25, 'QR height');
  assert.ok(qr.x + qr.width <= band.x, 'QR and text band must not overlap');
  for (const text of [
    box.global_code,
    box.species.scientific_name,
    box.strain.name,
  ].filter(Boolean)) {
    const line = popup.locator('.label-main').getByText(text, { exact: true });
    assert.ok(await line.isVisible(), `Missing printable text: ${text}`);
    const rects = await line.evaluate((node) => {
      const range = document.createRange();
      range.selectNodeContents(node);
      return Array.from(range.getClientRects(), (rect) => rect.toJSON());
    });
    assert.ok(rects.length > 0, `No rendered text for ${text}`);
    for (const rect of rects) {
      assert.ok(
        rect.left >= band.x - 1 &&
          rect.right <= band.x + band.width + 1 &&
          rect.top >= band.y - 1 &&
          rect.bottom <= band.y + band.height + 1,
        `Text outside printable band: ${text} ${JSON.stringify({ rect, band })}`,
      );
    }
  }
}

for (const [name, box, options, print] of [
  ['unnamed strain', unnamed, {}, 'Print'],
  ['long Latin', longName, {}, 'Print'],
  [
    'Japanese',
    japanese,
    { language: 'ja', width: 960, height: 600, hasTouch: true },
    '印刷',
  ],
]) {
  test(`QR label prints ${name} text in the 41 × 28 mm stock`, async (t) => {
    const { page, dialog } = await openQrModal(t, box, options);
    const [popup] = await Promise.all([
      page.waitForEvent('popup'),
      dialog.getByRole('button', { name: print, exact: true }).click(),
    ]);
    await page.waitForFunction(() => window.qaPrints === 1);
    await assertPrintableGeometry(popup, box);
    const expected = [box.species.scientific_name, box.strain.name]
      .filter(Boolean)
      .join(' ');
    assert.equal(
      await dialog.locator('.utility-dialog-identity small').textContent(),
      expected,
    );
    assert.equal(await popup.locator('.label-species').textContent(), expected);
    if (box.strain.name) {
      assert.equal(
        await popup
          .locator('.label-strain')
          .evaluate((node) => getComputedStyle(node).fontStyle),
        'normal',
      );
      assert.equal(
        await popup
          .locator('.label-species')
          .evaluate((node) => getComputedStyle(node).fontStyle),
        'italic',
      );
    }
    const pdf = await popup.pdf({ preferCSSPageSize: true });
    assert.equal(
      (pdf.toString('latin1').match(/\/Type \/Page\b/g) ?? []).length,
      1,
    );
  });
}

test('batch print hands off two complete, escaped labels and produces two PDF pages', async (t) => {
  const { page, search } = await openPicker(t);
  await interceptPrint(page);
  for (const box of [named, secondNamed]) {
    await search.fill(box.global_code);
    await resultFor(page, box).check();
  }
  const [popup] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'Print 2', exact: true }).click(),
  ]);
  await page.waitForFunction(() => window.qaPrints === 1);
  assert.deepEqual(
    await popup.locator('.label-species').allTextContents(),
    [named, secondNamed].map(
      (box) => `${box.species.scientific_name} ${box.strain.name}`,
    ),
  );
  assert.equal(await popup.locator('.label-strain blue').count(), 0);
  const pdf = await popup.pdf({ preferCSSPageSize: true });
  assert.equal(
    (pdf.toString('latin1').match(/\/Type \/Page\b/g) ?? []).length,
    2,
  );
});

test('Labels refuses the whole batch and names the overflowing box', async (t) => {
  const { page, search } = await openPicker(t, {
    boxes: [named, overflow],
    width: 960,
    height: 600,
    hasTouch: true,
  });
  await interceptPrint(page);
  for (const box of [named, overflow]) {
    await search.fill(box.global_code);
    await resultFor(page, box).check();
  }
  await page.getByRole('button', { name: 'Print 2', exact: true }).click();
  const alert = page
    .getByRole('alert')
    .filter({ hasText: overflow.global_code });
  await alert.waitFor();
  assert.equal(
    await alert.textContent(),
    `${overflow.global_code}: The strain name is too long for this label format. No labels were printed or downloaded.`,
  );
  assert.deepEqual(
    await page.evaluate(() => [window.qaPrints, window.qaDownloads]),
    [0, 0],
  );
  assert.equal(
    await page.getByRole('button', { name: 'Retry', exact: true }).count(),
    0,
  );
});

for (const box of [named, longName, japanese, unnamed]) {
  test(`download and browser print preserve the SVG text for ${box.global_code}`, async (t) => {
    const { page, dialog } = await openQrModal(t, box);
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      dialog.getByRole('button', { name: 'Download', exact: true }).click(),
    ]);
    let svg = '';
    for await (const chunk of await download.createReadStream())
      svg += chunk.toString();
    const layout = await page.evaluate((markup) => {
      const root = new DOMParser().parseFromString(
        markup,
        'image/svg+xml',
      ).documentElement;
      if (root.localName !== 'svg') throw new Error('Invalid SVG');
      document.body.append(root);
      try {
        const taxon = [...root.querySelectorAll('.label-species')];
        const strains = [...root.querySelectorAll('.label-strain')];
        const { x, y, width, height } = root
          .querySelector('.label-text')
          .getBBox();
        return {
          text: taxon
            .map((node) => node.textContent)
            .join(strains.length ? '' : ' '),
          styles: strains.map((node) => getComputedStyle(node).fontStyle),
          inside:
            x >= -13.5 && x + width <= 13.5 && y >= -7.15 && y + height <= 7.15,
          qr: root.querySelector('image').getAttribute('href'),
        };
      } finally {
        root.remove();
      }
    }, svg);
    assert.equal(
      layout.text,
      [box.species.scientific_name, box.strain.name].filter(Boolean).join(' '),
    );
    assert.ok(layout.inside, 'The SVG text stays inside the rotated text band');
    assert.ok(layout.styles.every((style) => style === 'normal'));
    assert.equal(layout.styles.length > 0, Boolean(box.strain.name));
    assert.match(layout.qr, /^data:image\/svg\+xml;base64,/);
    if (box.strain.name) {
      await page.emulateMedia({ media: 'print' });
      const native = page.locator('.qr-label-print-svg');
      await native.waitFor();
      assert.ok(
        await native.evaluate((img) =>
          img.decode().then(() => img.naturalWidth > 0),
        ),
      );
      assertNear(
        (await native.boundingBox()).width,
        41,
        'browser-print SVG width',
      );
    }
  });
}

test('QR modal shows the full strain name even when it will not fit on the stock', async (t) => {
  const { dialog } = await openQrModal(t, overflow);
  const name = dialog
    .locator('.utility-dialog-identity')
    .getByText(overflow.strain.name, { exact: true });
  assert.ok(await name.isVisible(), 'The modal must show the full strain name');
  const hasClippedText = await name.evaluate((element) => {
    const range = document.createRange();
    range.selectNodeContents(element);
    const rects = [...range.getClientRects()];
    const dialog = element.closest('[role="dialog"]').getBoundingClientRect();
    return (
      !rects.length ||
      rects.some((rect) => rect.left < dialog.left || rect.right > dialog.right)
    );
  });
  assert.equal(
    hasClippedText,
    false,
    'Full strain name must wrap inside the dialog',
  );
});

for (const action of ['Print', 'Download']) {
  test(`QR modal refuses an overflowing ${action.toLowerCase()}`, async (t) => {
    const { page, dialog } = await openQrModal(t, overflow);
    await dialog.getByRole('button', { name: action, exact: true }).click();
    await dialog.and(page.locator('[aria-busy="false"]')).waitFor();
    const handoffs = await page.evaluate(() => ({
      prints: window.qaPrints,
      downloads: window.qaDownloads,
    }));
    assert.deepEqual(
      handoffs,
      { prints: 0, downloads: 0 },
      'An overflowing label must not reach the browser',
    );
    const alert = dialog.getByRole('alert');
    assert.equal(
      await alert.textContent(),
      `${overflow.global_code}: The strain name is too long for this label format.`,
    );
    assert.equal(
      await dialog.getByRole('button', { name: 'Retry', exact: true }).count(),
      0,
    );
  });
}
