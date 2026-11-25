import { createRequire } from 'node:module';
import { mkdir, mkdtemp, readFile, realpath, rm, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { parseArgs } from 'node:util';

const localDocs = fileURLToPath(new URL('../../.local-docs', import.meta.url));
const devices = {
  desktop: { viewport: { width: 1440, height: 1000 }, hasTouch: false, isMobile: false },
  tablet: { viewport: { width: 820, height: 768 }, hasTouch: true, isMobile: true },
  phone: { viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
};
const roles = { admin: 'admin', lab: 'lab_technician', viewer: 'viewer' };
const limitations = [
  'Synthetic loopback navigation only. Browser filtering does not isolate the application server or its database; the launcher must do that.',
  'One Lighthouse navigation per case, with the measured viewport and host CPU/network (no throttling). Compare repeated like-for-like runs on the same build, dataset and machine; dev/HMR is not a production baseline.',
  'Routing disables the Chromium HTTP cache; Lighthouse keeps session cookies and storage. These are direct authenticated document navigations, not first-ever visits or in-app transition timings.',
  'Lighthouse trace-derived FCP/LCP/CLS/Speed Index/TBT reflect its bounded navigation window. TBT is not INP; no field Core Web Vitals, interaction trace, or real-device measurements are captured.',
  'Screenshot, console/CSP and network measurements come from a separate navigation, sampled until the capture; request duration is browser-observed, not Django execution time. Intentional blocks are separate from failures.',
  'Layout findings are heuristics: horizontal geometry, clipping and sampled control hit-testing may flag intentional scrolling, overlays or truncation, and can miss transient or below-fold issues. A screenshot is not a visual diff.',
  'Automated accessibility scores do not establish WCAG compliance. Keyboard, touch, zoom and screen-reader workflows require manual checks.',
];

const help = `Usage: npm run perf:sweep -- --synthetic [options]
  --base-url URL       Exact HTTP(S) origin on 127.0.0.1 or [::1] (default http://127.0.0.1:5173)
  --roles LIST         admin,lab,viewer (default all)
  --viewports LIST     desktop,tablet,phone (default all)
  --screens LIST       pilotage,overview,zones,zone,box,labels,exports,admin-team,admin-inventory,admin-guard
  --org-slug SLUG      Default aquarium-de-paris
  --box-code CODE      Default AAU-1.001-ATL
  --zone-name NAME     Default Cabinet 15 C, looked up in the organization
  --zone-id ID         Override zone lookup
  --slow-ms MS         Slow request threshold (default 500)
  --settle-ms MS       Extra observation after ready (default 1000)
  --skip-lighthouse    Screenshots and diagnostics only
  --channel CHANNEL    Use installed Chromium channel (e.g. msedge); default Playwright Chromium

Set PERF_ADMIN_EMAIL/PERF_ADMIN_PASSWORD, PERF_LAB_EMAIL/PERF_LAB_PASSWORD,
and PERF_VIEWER_EMAIL/PERF_VIEWER_PASSWORD for the selected roles; passwords
can share PERF_PASSWORD for the synthetic seed. Default emails match the seed.
Node 22.12+; install the default browser with npx playwright install chromium.
Does not start the app. Reports go under .local-docs/perf-sweep/.
`;

function config() {
  const { values: v } = parseArgs({ options: {
    ...Object.fromEntries(['base-url', 'roles', 'viewports', 'screens', 'org-slug', 'box-code', 'zone-name', 'zone-id', 'slow-ms', 'settle-ms', 'channel'].map(key => [key, { type: 'string' }])),
    help: { type: 'boolean' }, synthetic: { type: 'boolean' }, 'skip-lighthouse': { type: 'boolean' },
  } });
  if (v.help) return;
  if (!v.synthetic) throw new Error('Confirm a disposable synthetic app with --synthetic.');
  const base = new URL(v['base-url'] ?? 'http://127.0.0.1:5173');
  if (!['http:', 'https:'].includes(base.protocol) || !['127.0.0.1', '[::1]'].includes(base.hostname)
      || base.username || base.password || base.href !== `${base.origin}/`) {
    throw new Error('--base-url must be an HTTP(S) loopback IP origin without a path, query, or credentials.');
  }
  const select = (key, allowed) => {
    const values = v[key] === undefined ? allowed : [...new Set(v[key].split(',').map(s => s.trim()))];
    if (values.some(value => !allowed.includes(value))) throw new Error(`Invalid --${key}; choose from ${allowed.join(',')}.`);
    return values;
  };
  const integer = (key, fallback, min, max = Number.MAX_SAFE_INTEGER) => {
    if (v[key] === undefined) return fallback;
    if (!/^\d+$/.test(v[key]) || Number(v[key]) < min || Number(v[key]) > max) throw new Error(`Invalid --${key}.`);
    return Number(v[key]);
  };
  const selectedRoles = select('roles', Object.keys(roles));
  const credentials = Object.fromEntries(selectedRoles.map(role => {
    const prefix = `PERF_${role.toUpperCase()}`;
    const password = process.env[`${prefix}_PASSWORD`] ?? process.env.PERF_PASSWORD;
    if (!password) throw new Error(`Set ${prefix}_PASSWORD (or PERF_PASSWORD) for synthetic role ${role}.`);
    return [role, { email: process.env[`${prefix}_EMAIL`] ?? `demo_${role}@example.test`, password }];
  }));
  return {
    base, selectedRoles, credentials, viewports: select('viewports', Object.keys(devices)),
    screens: select('screens', ['pilotage', 'overview', 'zones', 'zone', 'box', 'labels', 'exports', 'admin-team', 'admin-inventory', 'admin-guard']),
    orgSlug: v['org-slug'] ?? 'aquarium-de-paris', boxCode: v['box-code'] ?? 'AAU-1.001-ATL',
    zoneName: v['zone-name'] ?? 'Cabinet 15 C', zoneId: integer('zone-id', undefined, 1),
    slowMs: integer('slow-ms', 500, 1), settleMs: integer('settle-ms', 1000, 0, 15000),
    lighthouse: !v['skip-lighthouse'], channel: v.channel,
  };
}

function screenMatrix(c, role, device) {
  const inventory = '/administration/box-inventory';
  return Object.entries({
    pilotage: ['/', '.pilotage-flow'], overview: ['/overview', '.overview-page'],
    zones: ['/zones', '.zone-overview-shell'], zone: [null, '.zone-page .zone-occupancy-section'],
    box: [`/boxes/${encodeURIComponent(c.boxCode)}`, '.box-sheet-identity'], labels: ['/labels', '.labels-page .label-selection-card'],
    ...(device === 'desktop' ? { exports: ['/exports', '.export-page'] } : {}),
    ...(role === 'admin' && device === 'desktop' ? {
      'admin-team': ['/administration/team', '#admin-accounts'], 'admin-inventory': [inventory, '#admin-inventory'],
    } : {}),
    ...(role === 'admin' && device !== 'desktop' ? { 'admin-guard': [inventory, '.pilotage-flow', '/'] } : {}),
  }).filter(([name]) => c.screens.includes(name));
}

async function jsonGet(page, path, org) {
  return page.evaluate(async ({ path, org }) => {
    const response = await fetch(path, { cache: 'no-store', redirect: 'error', headers: org ? { 'X-Organization-Id': String(org) } : {} });
    if (!response.ok) throw new Error(`Setup API ${response.status}: ${new URL(path, location.origin).pathname}`);
    return response.json();
  }, { path, org });
}

export async function setup(page, c, role, screenNames) {
  await page.goto(new URL('/login?next=%2F', c.base).href, { waitUntil: 'domcontentloaded' });
  const login = page.locator('.login-screen');
  await login.locator('input[type=email]').fill(c.credentials[role].email);
  await login.locator('input[type=password]').fill(c.credentials[role].password);
  const response = page.waitForResponse(r => r.url() === new URL('/api/auth/session/', c.base).href && r.request().method() === 'POST');
  const [loggedIn] = await Promise.all([response, login.locator('button[type=submit]').click()]);
  if (!loggedIn.ok()) throw new Error(`Login failed for ${role}: HTTP ${loggedIn.status()}.`);
  // Wait for the application's authenticated bootstrap before the separate QA read.
  await page.locator('.pilotage-flow, .organization-choice-page').first().waitFor();
  const profile = await jsonGet(page, '/api/profile/');
  const membership = profile.memberships?.find(m => m.organization.slug === c.orgSlug);
  if (membership?.role !== roles[role] || (role !== 'admin' && profile.is_superuser)) {
    throw new Error(`Expected ${roles[role]} membership for ${role} in ${c.orgSlug}.`);
  }
  const orgId = membership.organization.id;
  const active = await page.evaluate(() => localStorage.getItem('polypbase.activeOrganizationId'));
  if (active !== String(orgId)) {
    await page.evaluate(id => localStorage.setItem('polypbase.activeOrganizationId', String(id)), orgId);
    await page.goto(c.base.href, { waitUntil: 'domcontentloaded' });
    await assertScreen(page, '.pilotage-flow', '/', c.base, orgId);
  }
  let boxPath;
  if (screenNames.includes('box')) {
    const box = await jsonGet(page, `/api/boxes/by-code/${encodeURIComponent(c.boxCode)}/`, orgId);
    if (!box.global_code) throw new Error('Box lookup did not return a canonical code.');
    boxPath = `/boxes/${encodeURIComponent(box.global_code)}`;
  }
  if (!screenNames.includes('zone') || c.zoneId) return { orgId, boxPath, zoneId: c.zoneId };
  let next = new URL('/api/thermal-zones/?limit=80', c.base);
  const visited = new Set();
  const matches = [];
  while (next) {
    if (next.origin !== c.base.origin || next.pathname !== '/api/thermal-zones/' || visited.has(next.href) || visited.size >= 100) {
      throw new Error('Unexpected thermal zone pagination; use --zone-id.');
    }
    visited.add(next.href);
    const data = await jsonGet(page, next.pathname + next.search, orgId);
    matches.push(...(Array.isArray(data) ? data : data.results).filter(z => z.name === c.zoneName));
    next = data.next ? new URL(data.next, next) : null;
  }
  if (matches.length !== 1) throw new Error(`Expected one zone named ${c.zoneName}; use --zone-id.`);
  return { orgId, boxPath, zoneId: matches[0].id };
}

function safeUrl(raw) {
  try {
    const u = new URL(raw);
    return ['http:', 'https:', 'ws:', 'wss:'].includes(u.protocol) ? u.origin + u.pathname : u.protocol;
  } catch { return ''; }
}

function monitor(page, c, blocked) {
  const console = [], requests = new Map();
  const onConsole = msg => console.push({ type: msg.type(), text: msg.text().slice(0, 1600), source: safeUrl(msg.location().url), line: msg.location().lineNumber });
  const onError = error => console.push({ type: 'pageerror', text: error.message.slice(0, 1600) });
  const onRequest = req => requests.set(req, { url: safeUrl(req.url()), method: req.method(), type: req.resourceType(), start: performance.now() });
  const onResponse = res => { const row = requests.get(res.request()); if (row) row.status = res.status(); };
  const done = req => {
    const row = requests.get(req);
    if (row) Object.assign(row, { durationMs: Math.round(performance.now() - row.start), failure: req.failure()?.errorText, policyBlocked: blocked.has(req) });
  };
  const events = [['console', onConsole], ['pageerror', onError], ['request', onRequest], ['response', onResponse], ['requestfinished', done], ['requestfailed', done]];
  for (const [event, fn] of events) page.on(event, fn);
  return async () => {
    for (const [event, fn] of events) page.off(event, fn);
    const csp = await page.evaluate(() => window.__perfCsp ?? []).catch(() => []);
    const rows = [...requests.values()].map(({ start, ...row }) => ({ ...row, elapsedAtCaptureMs: row.durationMs === undefined ? Math.round(performance.now() - start) : undefined }));
    return { console, csp: csp.map(e => ({ ...e, blockedURI: safeUrl(e.blockedURI) || e.blockedURI, sourceFile: safeUrl(e.sourceFile) })), network: {
      total: rows.length, blocked: rows.filter(r => r.policyBlocked), pending: rows.filter(r => r.durationMs === undefined),
      failed: rows.filter(r => !r.policyBlocked && (r.failure || r.status >= 400)),
      slow: rows.filter(r => !r.policyBlocked && (r.durationMs ?? r.elapsedAtCaptureMs) >= c.slowMs).sort((a, b) => (b.durationMs ?? b.elapsedAtCaptureMs) - (a.durationMs ?? a.elapsedAtCaptureMs)),
      requests: rows,
    } };
  };
}

function inspectLayout() {
  const root = document.documentElement;
  const width = root.clientWidth, height = root.clientHeight;
  const viewportOverflow = Math.max(0, root.scrollWidth - width);
  const describe = e => `${e.localName}${e.id ? `#${e.id}` : ''}${[...e.classList].slice(0, 3).map(c => `.${c}`).join('')}`;
  const rendered = e => e instanceof HTMLElement && e.getClientRects().length && !e.closest('[hidden],[inert],[aria-hidden=true]') && getComputedStyle(e).visibility === 'visible';
  const outside = [], clipped = [], obstructed = [];
  for (const e of document.body.querySelectorAll('*')) {
    if (!rendered(e)) continue;
    const r = e.getBoundingClientRect();
    if (r.width <= 1 || r.height <= 1 || r.bottom <= 0 || r.top >= height) continue;
    if (outside.length < 30 && (r.right > width + 2 || r.left < -2)) {
      outside.push({ element: describe(e), left: Math.round(r.left), right: Math.round(r.right) });
    }
    const extra = e.scrollWidth - e.clientWidth;
    if (clipped.length < 30 && e.clientWidth && extra > 2 && ['hidden', 'clip', 'auto', 'scroll'].includes(getComputedStyle(e).overflowX)) {
      clipped.push({ element: describe(e), extra, overflowX: getComputedStyle(e).overflowX });
    }
    if (obstructed.length >= 30 || !e.matches('button:not([disabled]), a[href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [role=button]')) continue;
    const x = r.left + r.width / 2, y = r.top + r.height / 2;
    if (x < 0 || x >= width || y < 0 || y >= height) continue;
    const hit = document.elementFromPoint(x, y);
    if (hit && !e.contains(hit) && !hit.contains(e)) obstructed.push({ element: describe(e), by: describe(hit), x: Math.round(x), y: Math.round(y) });
  }
  return { width, height, viewportOverflow, outside, clipped, obstructed, coarsePointer: matchMedia('(pointer: coarse)').matches, hover: matchMedia('(hover: hover)').matches };
}

async function assertScreen(page, selector, path, base, orgId) {
  await page.waitForURL(u => u.origin === base.origin && u.pathname === path);
  await page.locator(selector).first().waitFor();
  const active = await page.evaluate(() => localStorage.getItem('polypbase.activeOrganizationId'));
  if (active !== String(orgId)) throw new Error('The expected organization is not active.');
}

async function audit(lighthouse, puppetPage, page, url, selector, c, orgId, device) {
  const { lhr } = await lighthouse(url, {
    logLevel: 'error', onlyCategories: ['performance', 'accessibility'], disableStorageReset: true,
    formFactor: device === 'desktop' ? 'desktop' : 'mobile', screenEmulation: { disabled: true },
    emulatedUserAgent: false, throttlingMethod: 'provided', maxWaitForLoad: 30000,
  }, undefined, puppetPage);
  if (lhr.runtimeError) throw new Error(`Lighthouse: ${lhr.runtimeError.code}`);
  const target = new URL(url);
  const audited = new URL(lhr.finalDisplayedUrl);
  if (audited.origin !== target.origin || audited.pathname !== target.pathname) throw new Error('Lighthouse audited an unexpected URL.');
  await assertScreen(page, selector, target.pathname, c.base, orgId);
  const a11y = lhr.categories.accessibility.auditRefs.map(({ id }) => lhr.audits[id]);
  const metrics = ['first-contentful-paint', 'largest-contentful-paint', 'speed-index', 'total-blocking-time', 'cumulative-layout-shift'];
  return {
    version: lhr.lighthouseVersion, finalUrl: safeUrl(lhr.finalDisplayedUrl), warnings: lhr.runWarnings,
    performance: lhr.categories.performance.score, accessibility: lhr.categories.accessibility.score,
    metrics: Object.fromEntries(metrics.map(id => [id, { value: lhr.audits[id]?.numericValue ?? null, unit: lhr.audits[id]?.numericUnit ?? null }])),
    accessibilityFindings: a11y.filter(a => (a.score !== null && a.score < 1) || a.scoreDisplayMode === 'error').map(a => ({ id: a.id, title: a.title, mode: a.scoreDisplayMode, selectors: a.details?.items?.map(item => item.node?.selector).filter(Boolean) ?? [] })),
    manualAccessibilityAudits: a11y.filter(a => a.scoreDisplayMode === 'manual').map(a => a.title),
  };
}

async function connectPuppeteer(profilePath) {
  // Lighthouse 12 uses this bundled dependency for the supplied-page API. Using
  // that page keeps Playwright routing and the authenticated browser context active.
  const require = createRequire(import.meta.resolve('lighthouse'));
  const { connect } = require('puppeteer-core');
  const port = (await readFile(join(profilePath, 'DevToolsActivePort'), 'utf8')).split(/\r?\n/)[0];
  if (!/^\d+$/.test(port)) throw new Error('Could not locate the loopback Chromium DevTools port.');
  return connect({ browserURL: `http://127.0.0.1:${port}`, defaultViewport: null });
}

async function sweep(c, output, summary, report) {
  const { chromium } = await import('playwright');
  const lighthouse = c.lighthouse ? (await import('lighthouse')).default : null;
  for (const role of c.selectedRoles) {
    for (const device of c.viewports) {
      const screens = screenMatrix(c, role, device);
      if (!screens.length) continue;
      const profilePath = await mkdtemp(join(output, '.chromium-'));
      let context, puppeteer;
      try {
        const port = c.base.port || (c.base.protocol === 'https:' ? '443' : '80');
        context = await chromium.launchPersistentContext(profilePath, {
          ...devices[device], channel: c.channel, serviceWorkers: 'block', deviceScaleFactor: 1,
          // Routing handles normal requests. The dead proxy also covers redirected
          // requests (which Playwright may not intercept) and Chromium background traffic.
          args: ['--disable-background-networking', '--disable-quic', '--force-webrtc-ip-handling-policy=disable_non_proxied_udp',
            '--proxy-server=http://127.0.0.1:1', `--proxy-bypass-list=<-loopback>;${c.base.protocol}//${c.base.hostname}:${port}`,
            ...(lighthouse ? ['--remote-debugging-port=0', '--remote-debugging-address=127.0.0.1'] : [])],
        });
        context.setDefaultTimeout(12000);
        context.setDefaultNavigationTimeout(20000);
        let allowLogin = true;
        const blocked = new WeakSet();
        const blockedSockets = [];
        await context.route('**/*', route => {
          const request = route.request(), url = new URL(request.url());
          const read = ['GET', 'HEAD', 'OPTIONS'].includes(request.method());
          if (url.origin === c.base.origin && (read || (allowLogin && request.method() === 'POST' && url.pathname === '/api/auth/session/'))) return route.continue();
          blocked.add(request);
          return route.abort('blockedbyclient');
        });
        await context.routeWebSocket('**/*', ws => { blockedSockets.push(safeUrl(ws.url())); return ws.close(); });
        await context.addInitScript(() => {
          window.__perfCsp = [];
          document.addEventListener('securitypolicyviolation', e => {
            window.__perfCsp.push({ effectiveDirective: e.effectiveDirective, blockedURI: e.blockedURI, disposition: e.disposition, sourceFile: e.sourceFile, lineNumber: e.lineNumber });
          });
        });
        const page = context.pages()[0] ?? await context.newPage();
        const { orgId, boxPath, zoneId } = await setup(page, c, role, screens.map(([name]) => name));
        allowLogin = false;
        let puppetPage;
        if (lighthouse && screens.some(([, screen]) => !screen[2])) {
          puppeteer = await connectPuppeteer(profilePath);
          [puppetPage] = (await puppeteer.pages()).filter(p => p.url() === page.url());
          if (!puppetPage) throw new Error('Could not find the authenticated Chromium page for Lighthouse.');
        }
        for (const [name, [path, selector, redirect]] of screens) {
          const routePath = name === 'box' ? boxPath : path ?? `/zones/${zoneId}`;
          const url = new URL(routePath, c.base).href;
          const id = `${role}-${device}-${name}`;
          const row = { role, device, screen: name, path: routePath };
          summary.cases.push(row);
          const stop = monitor(page, c, blocked);
          const socketOffset = blockedSockets.length;
          try {
            const response = await page.goto(url, { waitUntil: 'domcontentloaded' });
            if (!response?.ok()) throw new Error(`Navigation failed: HTTP ${response?.status() ?? 'unknown'}.`);
            await assertScreen(page, selector, redirect ?? routePath, c.base, orgId);
            await sleep(c.settleMs);
            row.layout = await page.evaluate(inspectLayout);
            await page.screenshot({ path: join(output, `${id}.png`), animations: 'disabled', timeout: 10000 });
            row.screenshot = `${id}.png`;
          } catch (error) {
            row.error = error.message.split('\n')[0];
          } finally {
            Object.assign(row, await stop());
            row.blockedWebSockets = blockedSockets.slice(socketOffset);
          }
          if (row.error || redirect || !lighthouse) {
            row.lighthouseSkipped = row.error ? 'screen not verified' : redirect ? 'expected redirect' : 'disabled by CLI';
          } else {
            try { row.lighthouse = await audit(lighthouse, puppetPage, page, url, selector, c, orgId, device); }
            catch (error) { row.lighthouseError = error.message.split('\n')[0]; }
          }
          row.status = row.error || row.lighthouseError ? 'error' : 'ok';
          console.log(`${id}: ${row.status}${row.error || row.lighthouseError ? ` (${row.error ?? row.lighthouseError})` : ''}`);
          await report();
        }
      } catch (error) {
        // Playwright login errors can include the arguments passed to fill().
        let message = error.message.split('\n')[0];
        for (const { email, password } of Object.values(c.credentials)) message = message.replaceAll(password, '[redacted]').replaceAll(email, '[redacted]');
        summary.setupErrors.push({ role, device, message });
        console.error(`${role}-${device}: ${message}`);
        await report();
      } finally {
        try { await puppeteer?.disconnect(); }
        finally {
          try { await context?.close(); }
          finally { await rm(profilePath, { recursive: true, force: true, maxRetries: 4 }); }
        }
      }
    }
  }
}

async function main() {
  const c = config();
  if (!c) return console.log(help);
  if (!c.selectedRoles.some(role => c.viewports.some(device => screenMatrix(c, role, device).length))) throw new Error('No applicable role/viewport/screen combinations.');
  await mkdir(localDocs, { recursive: true });
  const parent = join(localDocs, 'perf-sweep');
  await mkdir(parent, { recursive: true });
  const canonical = s => process.platform === 'win32' ? s.toLowerCase() : s;
  if (canonical(await realpath(parent)) !== canonical(parent)) throw new Error('Refusing a redirected artifact directory.');
  const started = new Date().toISOString();
  const output = await mkdtemp(join(parent, `${started.replaceAll(':', '-')}-`));
  const summary = { started, origin: c.base.origin, organization: c.orgSlug, devices, slowMs: c.slowMs, settleMs: c.settleMs, limitations, cases: [], setupErrors: [] };
  const report = () => writeFile(join(output, 'report.json'), `${JSON.stringify(summary, null, 2)}\n`);
  console.log(`Local artifacts: ${output}`);
  await report();
  await sweep(c, output, summary, report);
  summary.finished = new Date().toISOString();
  await report();
  if (summary.setupErrors.length || summary.cases.some(row => row.status === 'error')) process.exitCode = 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
