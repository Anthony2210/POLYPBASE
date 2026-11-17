"""Real BoxPage Chromium QA with synthetic props and mocked HTTP; no App or database.

From the repository root:
  uv run --no-project --with playwright python frontend/scripts/qa-dna-validation.py
If Chromium is missing:
  uv run --no-project --with playwright python -m playwright install chromium
Screenshots and report.json are written OUTSIDE the repository, to a temporary
directory by default (never committed). Use --output-dir to choose another
location outside the repository. No product dependencies change.
"""

import argparse
from datetime import datetime, timezone
from functools import partial
import hashlib
from importlib.metadata import version
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import tempfile
from threading import Thread
from urllib.parse import urlparse

from playwright.sync_api import expect, sync_playwright

expect.set_options(timeout=10000)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = r'''
import React, { Suspense, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import BoxPage from '/src/components/BoxPage';
import { useConfirmAction } from '/src/components/ConfirmActionModal';
import { apiGet, apiPost, apiPatch } from '/src/api/client';
import { createTranslator } from '/src/i18n';
import '/src/styles/index.css';
document.documentElement.lang = new URLSearchParams(location.search).get('language') || 'fr';
function Fixture() {
  const query = new URLSearchParams(location.search);
  const [box, setBox] = useState(null);
  const language = query.get('language') || 'fr';
  const reader = query.get('reader') === '1';
  const { confirmAction, confirmActionModal } = useConfirmAction();
  const reload = async () => {
    const detail = await apiGet('/api/boxes/7/', { cache: 'no-store' });
    setBox({...detail, can_edit_dna_validation: !reader});
    return detail;
  };
  useEffect(() => { reload(); }, []);
  const unused = () => { throw new Error('Out-of-scope fixture action'); };
  const organization = {id: 1, name: 'Synthetic QA laboratory', slug: 'qa'};
  const profile = {id: 10, is_superuser: false, active_organization: organization,
    organizations: [organization], memberships: [{organization, role: reader ? 'reader' : 'lab_technician'}]};
  return <main className="workspace"><div className="workspace-page">
    <Suspense fallback={<p>Loading fixture</p>}>
      <BoxPage box={box} zones={box?.thermal_zone ? [box.thermal_zone] : []} profile={profile}
        language={language} t={createTranslator(language)} isLoading={!box} qrLabelSelection={[]}
        confirmAction={confirmAction} isOperationCurrent={() => true}
        onRefreshDnaValidation={reload}
        onMutateDnaValidation={async (boxId, {operation, ...payload}) => {
          if (operation === 'update') await apiPatch(`/api/boxes/${boxId}/dna-validation/`, payload);
          else await apiPost(`/api/boxes/${boxId}/dna-validation/${operation === 'revoke' ? 'revoke/' : ''}`, payload);
          await reload();
        }}
        onCreateMeasurement={unused} onUpdateMeasurement={unused} onRefreshMeasurementState={unused}
        onCorrectMovement={unused} onRefreshMovementState={unused} onCreateSubculture={unused}
        onMoveBox={unused} onDeactivateBox={unused} onReactivateBox={unused} onLoadLineageGraph={unused}
        measurementPrefill={null} onMeasurementPrefillConsumed={unused} onOpenBox={unused}
        onOpenZone={unused} onAddQrLabel={unused} onBack={unused} onOpenQrLabelSelection={unused} />
    </Suspense>
    {confirmActionModal}
  </div></main>;
}
createRoot(document.getElementById('root')).render(<Fixture />);
'''


def box_detail(state):
    return {
        'id': 7, 'global_code': state['code'],
        'local_code': 'AUR-0007', 'box_number': '7', 'status': 'active',
        'organization': {'id': 1, 'name': 'Synthetic QA laboratory', 'slug': 'qa'},
        'species': {'id': 1, 'scientific_name': 'Aurelia aurita'},
        'strain': {'id': 1, 'code': 'QA-AUR', 'name': 'Synthetic strain'},
        'thermal_zone': {'id': 1, 'name': 'Zone témoin', 'salinity_psu': '35.0', 'latest_temperature': None},
        'entered_on': '2026-11-16', 'created_on': '2026-11-16',
        'current_location_started_at': '2026-11-16T09:00:00Z',
        'latest_measurement': None, 'latest_salinity_psu': None,
        'current_polyp_state': {'count': None, 'state': 'unknown'},
        'dna_validation': state['validation'], 'can_edit_dna_validation': True,
        'volume_liters': None, 'stop_reason': '', 'stop_reason_missing_from_history': False,
        'deactivated_on': None, 'notes': '', 'lineage': {'parents': [], 'children': []},
        'locations': [], 'movements': [], 'biological_measurements': [],
        'biological_timeline': [], 'temperature_history': [],
        'scan_url': 'http://127.0.0.1/qa-synthetic-box',
        'qr_image_url': '/boites/7/qr.svg',
        'can_create_measurement': False,
    }


def evidence(revision=12):
    return {'id': 1, 'evidence_url': 'https://lab.example/read-only', 'created_by': 'Alex QA',
            'created_at': '2026-11-16T10:00:00Z', 'revision': revision}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path,
                        default=Path(tempfile.gettempdir()) / 'polypbase-qa' / 'box-dna-validation')
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Real BoxPage, synthetic fixture props, real API client, mocked HTTP; no App shell/backend/database',
              'started_at': datetime.now(timezone.utc).isoformat(), 'playwright': version('playwright'),
              'source_sha256': {name: hashlib.sha256((ROOT / 'src/components' / name).read_bytes()).hexdigest()
                                for name in ['BoxPage.tsx', 'DnaValidationBadge.tsx']},
              'cases': [], 'screenshots': []}
    server = None
    thread = None
    # Keep generated inputs/build isolated, and remove them even on browser/assertion failure.
    with tempfile.TemporaryDirectory(prefix='qa-dna-', dir=ROOT / 'scripts') as temporary:
        temp = Path(temporary)
        html = temp / 'index.html'
        entry = temp / 'fixture.tsx'
        config = temp / 'vite.config.mjs'
        dist = temp / 'dist'
        html.write_text('<!doctype html><html lang="fr"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"></head><body><div id="root"></div><script type="module" src="/' + entry.relative_to(ROOT).as_posix() + '"></script></body></html>', encoding='utf-8')
        entry.write_text(FIXTURE, encoding='utf-8')
        config.write_text("import react from '@vitejs/plugin-react'; export default { plugins: [react()], define: { __API_PROXY_ORIGIN__: 'null' }, build: { outDir: " + json.dumps(dist.as_posix()) + ", rollupOptions: { input: " + json.dumps(html.as_posix()) + " } } };", encoding='utf-8')
        try:
            subprocess.run(['node', 'node_modules/vite/bin/vite.js', 'build', '--config', str(config)], cwd=ROOT, check=True, timeout=300)

            class QuietHandler(SimpleHTTPRequestHandler):
                def log_message(self, *_args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(dist)))
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            origin = f'http://127.0.0.1:{server.server_port}'
            built_html = next(dist.rglob('index.html')).relative_to(dist).as_posix()
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                report['browser'] = browser.version
                try:
                    for width, height in [(1440, 1000), (960, 600), (390, 844), (320, 844)]:
                        for language in ['fr', 'en']:
                            result = {'viewport': f'{width}x{height}', 'language': language, 'failures': [], 'checks': []}
                            report['cases'].append(result)
                            try:
                                run_case(browser, f'{origin}/{built_html}', width, height, language, output, report, result)
                            except Exception as error:
                                result['failures'].append(f'{type(error).__name__}: {error}')
                            print(('FAIL' if result['failures'] else 'PASS') + f' Chromium {width}x{height} {language}: '
                                  + json.dumps(result['failures'], ensure_ascii=False) + f" ({len(result['checks'])} checks passed)", flush=True)
                finally:
                    browser.close()
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=5)
            (output / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Persistent QA deliverables: {output}', flush=True)
    if any(case['failures'] for case in report['cases']):
        raise SystemExit(1)


def run_case(browser, fixture_url, width, height, language, output, report, result):
    context = browser.new_context(viewport={'width': width, 'height': height}, has_touch=width <= 960,
                                  locale='fr-FR' if language == 'fr' else 'en-GB', timezone_id='Europe/Paris',
                                  reduced_motion='reduce', service_workers='block')
    context.add_init_script("localStorage.setItem('polypbase.activeOrganizationId', '1');")
    page = context.new_page()
    page.set_default_timeout(8000)
    errors, requests, cancellations = [], [], []
    state = {'validation': None, 'conflict': False, 'revision': 0, 'code': 'STS-MGF-1.002'}
    origin = urlparse(fixture_url)
    local_origin = f'{origin.scheme}://{origin.netloc}'
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.on('console', lambda message: errors.append(message.text) if message.type == 'error' and '409 (Conflict)' not in message.text else None)
    def failed_request(request):
        path = urlparse(request.url).path
        if request.method == 'GET' and request.failure == 'net::ERR_ABORTED' and path in [
            '/api/boxes/7/temperatures/', '/api/boxes/7/historical-temperatures/',
            '/api/boxes/7/history/', '/boites/7/qr.svg',
        ]:
            # Real child components cancel obsolete resource reads on detail refresh/unmount.
            cancellations.append(path)
        else:
            errors.append(f'{request.url}: {request.failure}')

    page.on('requestfailed', failed_request)
    labels = {
        'fr': ['Validation ADN : non renseignée', 'Validation ADN : validée', 'Ajouter une preuve ADN', 'Enregistrer', 'Annuler', 'Modifier le lien', 'Retirer la validation', 'Recharger la fiche', 'Validation ADN', 'Validée', 'Preuve', 'Ajoutée par', 'Date', 'Fermer'],
        'en': ['DNA validation: not provided', 'DNA validation: validated', 'Add DNA evidence', 'Save', 'Cancel', 'Edit link', 'Remove validation', 'Reload box', 'DNA validation', 'Validated', 'Evidence', 'Added by', 'Date', 'Close'],
    }[language]
    missing, valid, add, save, cancel, edit, revoke, reload, title, state_label, evidence_label, added_by_label, date_label, close_label = labels

    def check(condition, description):
        if not condition:
            result['failures'].append(description)
        else:
            result['checks'].append(description)

    def capture(name):
        page.evaluate('document.fonts.ready')
        page.screenshot(path=str(output / f'{width}x{height}-{language}-{name}.png'), full_page=False, animations='disabled')
        report['screenshots'].append(f'{width}x{height}-{language}-{name}.png')

    def geometry(name, panel=None):
        check(page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'{name}: no document horizontal overflow')
        if panel is not None:
            rect = panel.bounding_box()
            check(rect is not None and rect['x'] >= 0 and rect['x'] + rect['width'] <= width + 1
                  and rect['y'] >= 0 and rect['y'] + rect['height'] <= height + 1, f'{name}: panel inside viewport')

    def api(route):
        request = route.request
        path = urlparse(request.url).path
        if not request.url.startswith(local_origin + '/'):
            errors.append(f'Blocked external request: {request.url}')
            route.abort()
            return
        if path == '/boites/7/qr.svg':
            route.fulfill(content_type='image/svg+xml', body='<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64"><path d="M4 4h20v20H4zM40 4h20v20H40zM4 40h20v20H4zM32 32h12v12H32zM48 48h12v12H48z"/></svg>')
            return
        if not path.startswith('/api/'):
            route.continue_()
            return
        requests.append((request.method, path))
        if request.method == 'GET' and path == '/api/boxes/7/history/':
            route.fulfill(json={'events': [], 'candidates': [], 'assessments': [], 'historical_transitions': [], 'starting_location': None})
            return
        if request.method == 'GET' and path in ['/api/boxes/7/temperatures/', '/api/boxes/7/historical-temperatures/']:
            route.fulfill(json={'count': 0, 'next': None, 'previous': None, 'results': []})
            return
        if request.method == 'GET' and path == '/api/boxes/7/':
            route.fulfill(json=box_detail(state))
            return
        if path not in ['/api/boxes/7/dna-validation/', '/api/boxes/7/dna-validation/revoke/'] or request.method not in ['POST', 'PATCH']:
            errors.append(f'Unexpected mocked API request: {request.method} {path}')
            route.fulfill(status=501, json={'detail': 'Unexpected fixture request'})
            return
        if state['conflict']:
            route.fulfill(status=409, json={'code': 'dna_validation_changed'})
            return
        payload = request.post_data_json
        if request.method == 'PATCH' or path.endswith('revoke/'):
            assert payload['expected_revision'] == state['revision']
        state['revision'] += 1
        if path.endswith('revoke/'):
            state['validation'] = None
            route.fulfill(status=204)
        else:
            assert payload['evidence_url'] == payload['evidence_url'].strip()
            state['validation'] = {**evidence(state['revision']), 'evidence_url': payload['evidence_url']}
            route.fulfill(status=200 if request.method == 'PATCH' else 201, json=state['validation'])

    # All browser HTTP is intercepted: no accidental live API or external service.
    page.route('**/*', api)
    try:
        page.goto(f'{fixture_url}?language={language}')
        badge = page.get_by_role('button', name=missing, exact=True)
        expect(badge).to_be_visible()
        expect(page.locator('.box-page .box-insights-section')).to_be_visible()
        code_lines = page.locator('.box-code-line h2').evaluate(
            "(node) => { const range = document.createRange(); range.selectNodeContents(node); return range.getClientRects().length; }")
        check(code_lines == 1, f'realistic box code stays on one line (got {code_lines} line boxes)')
        result['code_metrics'] = page.locator('.box-code-line').evaluate(
            "(node) => ({ code: Math.round(node.querySelector('h2').getBoundingClientRect().width), line: Math.round(node.getBoundingClientRect().width) })")
        capture('technician-missing')
        geometry('missing')
        badge.focus()
        page.keyboard.press('Enter')
        dialog = page.get_by_role('dialog', name=add, exact=True)
        expect(dialog.locator('input')).to_be_focused()
        check(dialog.get_by_role('heading', name=add, exact=True).count() == 1, 'editor shows the action title')
        check(dialog.get_by_text(title, exact=True).count() == 1, 'editor shows the kicker')
        check(dialog.locator('.box-dialog-close').count() == 1, 'editor reuses the shared close button')
        check(dialog.locator('.box-dialog-body').count() == 1 and dialog.locator('.box-dialog-actions').count() == 1,
              'editor reuses the shared body and actions')
        check(dialog.get_by_role('alert').count() == 0, 'no URL error before a save attempt')
        capture('technician-add-editor')
        dialog.locator('input').fill('not-a-url')
        check(dialog.get_by_role('alert').count() == 0, 'no URL error while typing')
        dialog.locator('input').blur()
        expect(dialog.get_by_role('alert')).to_be_visible()
        check(True, 'URL error after leaving a modified field')
        dialog.locator('input').fill('')
        check(dialog.get_by_role('alert').count() == 0, 'editing clears the URL error')
        before = len(requests)
        dialog.get_by_role('button', name=save, exact=True).click()
        expect(dialog.get_by_role('alert')).to_be_visible()
        check(not any(method != 'GET' for method, _ in requests[before:]), 'invalid empty URL sends no write')
        expect(dialog.locator('input')).to_be_focused(timeout=3000)
        check(True, 'invalid URL restores input focus')
        check(page.locator('#root').evaluate('(node) => node.inert || !!node.closest("[inert]")'), 'editor makes background inert')
        geometry('editor', dialog)
        capture('technician-invalid-editor')
        dialog.locator('input').fill('  https://lab.example/proof  ')
        dialog.get_by_role('button', name=save, exact=True).focus()
        page.keyboard.press('Tab')
        expect(dialog.locator('.box-dialog-close')).to_be_focused()
        page.keyboard.press('Shift+Tab')
        expect(dialog.get_by_role('button', name=save, exact=True)).to_be_focused()
        page.keyboard.press('Escape')
        expect(badge).to_be_focused()
        check(not page.locator('#root').evaluate('(node) => node.inert'), 'editor close removes background inert')
        badge.click()
        dialog.locator('input').fill(' https://lab.example/proof ')
        dialog.get_by_role('button', name=save, exact=True).click()
        validated = page.get_by_role('button', name=valid, exact=True)
        expect(validated).to_be_visible()
        expect(page.get_by_role('dialog')).to_have_count(0)
        capture('technician-validated')
        validated.click()
        popover = page.get_by_role('dialog', name=title, exact=True)
        link = popover.get_by_role('link', name='lab.example', exact=True)
        expect(link).to_be_visible()
        try:
            expect(link).to_be_focused(timeout=1000)
            check(True, 'popover initially focuses evidence link')
        except AssertionError:
            check(False, 'popover initially focuses evidence link; active=' + page.evaluate('document.activeElement.tagName + ": " + document.activeElement.getAttribute("aria-label")'))
        expect(link).to_have_attribute('href', 'https://lab.example/proof')
        expect(link).to_have_attribute('title', 'https://lab.example/proof')
        expect(link).to_have_attribute('rel', 'noopener noreferrer')
        check(popover.get_by_role('button', name=close_label, exact=True).count() == 0, 'popover has no close button')
        check(popover.get_by_role('heading', name=title, exact=True).count() == 1, 'popover shows the title heading')
        check(popover.get_by_text(state_label, exact=True).count() == 0, 'popover header has no state chip')
        check(popover.get_by_text(evidence_label, exact=True).count() == 1, 'popover shows the evidence label')
        check(popover.get_by_text(added_by_label, exact=True).count() == 1, 'popover shows the added-by label')
        check(popover.get_by_text(date_label, exact=True).count() == 1, 'popover shows the date label')
        if width <= 960:
            edit_rect = popover.get_by_role('button', name=edit, exact=True).bounding_box()
            check(edit_rect['width'] >= 44 and edit_rect['height'] >= 44, 'edit action keeps a sufficient touch target')
        geometry('technician popover', popover)
        capture('technician-validated-popover')
        page.keyboard.press('Escape')
        expect(validated).to_be_focused()
        check(page.get_by_role('dialog').count() == 0, 'Escape closes the popover')
        validated.click()
        expect(popover).to_be_visible()
        validated.click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        expect(validated).to_be_focused()
        check(True, 're-clicking the badge closes the popover and restores focus')
        validated.click()
        expect(popover).to_be_visible()
        page.locator('.box-insights-section').click(position={'x': 8, 'y': 8})
        expect(page.get_by_role('dialog')).to_have_count(0)
        check(True, 'outside click closes the popover')
        validated.click()
        popover.get_by_role('button', name=edit, exact=True).click()
        editor = page.get_by_role('dialog', name=edit, exact=True)
        expect(editor.get_by_role('button', name=save, exact=True)).to_be_disabled()
        check(editor.get_by_role('alert').count() == 0, 'edit dialog opens without a URL error')
        capture('technician-edit-editor')
        editor.locator('input').fill('https://lab.example/changed')
        editor.get_by_role('button', name=save, exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        validated.click()
        expect(link).to_have_attribute('href', 'https://lab.example/changed')
        popover.get_by_role('button', name=revoke, exact=True).click()
        confirmation = page.get_by_role('dialog', name=revoke, exact=True)
        expect(confirmation).to_be_visible()
        geometry('revoke confirmation', confirmation)
        capture('technician-revoke-confirmation')
        before = len(requests)
        page.keyboard.press('Escape')
        expect(validated).to_be_focused()
        check(not any(method != 'GET' for method, _ in requests[before:]), 'cancel revoke sends no write')
        validated.click()
        popover.get_by_role('button', name=revoke, exact=True).click()
        confirmation.get_by_role('button', name=revoke, exact=True).click()
        expect(badge).to_be_visible()
        result['checks'].append('add, trimmed URL, edit, unchanged edit disabled, revoke and revision payloads')
        state['conflict'] = True
        badge.click()
        dialog.locator('input').fill('https://lab.example/replacement')
        dialog.get_by_role('button', name=save, exact=True).click()
        expect(dialog.get_by_role('button', name=reload, exact=True)).to_be_visible()
        expect(dialog.get_by_role('button', name=save, exact=True)).to_be_disabled()
        capture('technician-409')
        before = len(requests)
        dialog.get_by_role('button', name=reload, exact=True).click()
        expect(page.get_by_role('dialog')).to_have_count(0)
        check([request for request in requests[before:] if request[1] == '/api/boxes/7/' or request[0] != 'GET']
                      == [('GET', '/api/boxes/7/')], '409 recovery is exactly one detail GET, no write replay')
        state['conflict'] = False
        reader_start = len(requests)
        page.goto(f'{fixture_url}?language={language}&reader=1')
        expect(page.get_by_role('img', name=missing, exact=True)).to_be_visible()
        check(page.locator('.dna-validation-badge').get_attribute('tabindex') is None
              and page.locator('button.dna-validation-badge').count() == 0, 'missing reader badge is not interactive')
        capture('reader-missing')
        state['validation'] = evidence()
        page.reload()
        expect(validated).to_be_visible()
        capture('reader-validated')
        validated.focus()
        page.keyboard.press('Enter')
        expect(link).to_be_visible()
        check(page.get_by_role('button', name=edit, exact=True).count() == 0
              and page.get_by_role('button', name=revoke, exact=True).count() == 0, 'validated reader has no mutation controls')
        geometry('reader popover', popover)
        capture('reader-validated-popover')
        page.keyboard.press('Escape')
        expect(validated).to_be_focused()
        check(not any(method != 'GET' for method, _ in requests[reader_start:]), 'reader scenarios send no writes')
        state['code'] = 'LONG-BOX-CODE-' * 8
        page.goto(f'{fixture_url}?language={language}')
        expect(validated).to_be_visible()
        rect = validated.bounding_box()
        heading = page.locator('.box-code-line h2').bounding_box()
        check(rect['width'] >= 44 and rect['height'] >= 44, 'badge hit target at least 44px')
        check(rect['x'] >= heading['x'] + heading['width'] - 1 or rect['y'] >= heading['y'] + heading['height'] - 1,
              'long code keeps the badge beside or below it without overlap')
        check(rect['x'] + rect['width'] <= width + 1, 'badge stays inside the viewport')
        geometry('long code')
        capture('technician-long-code')
        validated.click()
        geometry('long code popover', popover)
        capture('technician-long-code-popover')
        result['checks'].append('Enter, Tab/Shift+Tab wrap, Escape and trigger focus restoration')
    finally:
        check(not errors, f'no unexpected browser errors: {errors}')
        result['requests'] = requests
        result['expected_cancelled_resource_reads'] = cancellations
        try:
            if result['failures']:
                capture('failure')
        finally:
            context.close()


if __name__ == '__main__':
    main()
