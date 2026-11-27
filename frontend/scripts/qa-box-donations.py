"""Real Chromium QA with MOCKED APIs only; no Django or database.

Run after npm run build: python scripts/qa-box-donations.py [--screenshots]
Serves dist on [::1]:5174 only (leaves any IPv4 dev server untouched). Captures are temporary and removed on exit.
Use --screenshots --inspect-seconds 60 for a bounded temporary inspection window.
"""
from __future__ import annotations

import argparse
import copy
from functools import partial
from http.server import ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import shutil
import socket
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("sharing_reference", ROOT / "scripts/qa-box-sharing.py")
REFERENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(REFERENCE)
BASE = "http://[::1]:5174"
DONATIONS = "/api/box-donations/"
SOURCE_BOXES = "/api/boxes/"
HISTORY_HASH = "0123456789abcdef" * 4
DESTINATIONS = [
    {"institution_id": f"10000000-0000-4000-8000-{index:012d}", "name": name, "country": country}
    for index, (name, country) in enumerate([
        ("Tokyo Lab", "Japon"), ("Zulu Lab", "France"), ("Unknown Lab", ""),
        ("Alpha Lab", " France "), ("Blank Lab", "   "),
    ], 1)
]
SELECTION = {"source_box_id": 12, "destination_institution_id": DESTINATIONS[3]["institution_id"]}


def proposal(direction, index):
    row = {
        "proposal_id": f"00000000-0000-4000-8000-{index + (100 if direction == 'incoming' else 0):012d}",
        "state": "pending" if index % 2 else "cancelled", "revision": 1,
        "created_at": "2026-11-15T12:00:00Z",
        "cancelled_at": None if index % 2 else "2026-11-16T12:00:00Z",
        "source_institution": {"name": "Institut source témoin"},
        "destination_institution": {"name": "Musée destinataire témoin"},
        "box": {"global_code": f"MOCK-{'OUT' if direction == 'outgoing' else 'IN'}-{index:03d}",
                "species_scientific_name": "Aurelia aurita", "strain_code": "MOCK-A",
                "global_strain_id": "mock-strain", "global_box_id": "mock-box"},
        "history_verified_at": "2026-11-15T10:00:00Z",
    }
    if direction == "outgoing":
        identity = {"first_name": "Donor", "last_name": "Test", "email": "qa@example.test"}
        row.update(created_by=identity, cancelled_by=identity if index % 2 == 0 else None,
                   history_verification={"verified_by": identity, "verified_at": row["history_verified_at"],
                                         "snapshot_version": "1.0", "sha256": "a" * 64,
                                         "polyp_state_revision": 0, "current": True})
        row["box"].update(id=7 + index, ownership_sequence=1)
    return row


def run(screenshots=False, inspect_seconds=0, keep_screenshots=False):
    assert (ROOT / "dist/index.html").is_file(), "Run npm run build first"
    class IPv6Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6

    server = IPv6Server(("::1", 5174), partial(REFERENCE.StaticApp, directory=str(ROOT / "dist")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    captures = ROOT / ".donations-qa-temp" if screenshots else Path(tempfile.mkdtemp(prefix="polypbase-donations-qa-"))
    if screenshots:
        captures.mkdir(exist_ok=False)
    state = {"language": "fr", "list_error": 0, "detail_error": 0, "preview_error": 0,
             "empty": False, "hold": False, "choices": "ready"}
    requests, unexpected, mutations, errors, checks, held, pending_sends = [], [], [], [], [], [], []
    rows = {direction: [proposal(direction, i) for i in range(1, 5)] for direction in ("outgoing", "incoming")}

    def api(route):
        request = route.request
        url = urlparse(request.url)
        path = url.path
        query = parse_qs(url.query, keep_blank_values=True)
        requests.append({"path": path, "query": query, "method": request.method, "headers": request.headers})
        def respond(data, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data), headers={"Cache-Control": "no-store"})
        def error(status):
            respond({"detail": "MOCKED access failure", "code": "mocked_failure"}, status)
        if request.method != "GET":
            mutations.append((request.method, path, request.post_data))
            if request.method == "POST" and path == DONATIONS + "preflight/":
                return respond({"history": {"sha256": HISTORY_HASH}})
            if request.method == "POST" and path == DONATIONS + "outgoing/":
                if not pending_sends:
                    pending_sends.append(route)
                    return
                created = proposal("outgoing", 5)
                if len(rows["outgoing"]) == 4:
                    rows["outgoing"].append(created)
                return respond(created, 201)
            unexpected.append((request.method, path))
            return error(404)
        if path in (DONATIONS + "destinations/", SOURCE_BOXES):
            if state["choices"] == "error":
                return error(403 if path.endswith("destinations/") else 500)
            if path.endswith("destinations/"):
                return respond({"results": [] if state["choices"] == "empty" else DESTINATIONS})
            boxes = [] if state["choices"] == "empty" else [
                {"id": 12, "global_code": "MOCK-OUT-005", "organization": REFERENCE.ORG, "status": "active"},
            ]
            return respond({"count": len(boxes), "next": None, "previous": None, "results": boxes})
        if path == "/api/auth/session/":
            return respond({"interface_language": state["language"]})
        if path == "/api/profile/":
            profile = copy.deepcopy(REFERENCE.PROFILE)
            profile["interface_language"] = state["language"]
            return respond(profile)
        if path == "/api/profile/actions/":
            return respond({"results": [], "has_more": False, "next_offset": None})
        if path == "/api/thermal-zones/":
            return respond({"count": 0, "next": None, "previous": None, "results": []})
        if path == "/api/dashboard/":
            return respond({"recent_accesses": [], "active_box_count": 0, "box_count": 0, "species_count": 0, "strain_count": 0})
        if path in ("/api/box-donations/outgoing/", "/api/box-donations/incoming/"):
            direction = path.split("/")[-2]
            if state["hold"]:
                held.append(route)
                return
            if state["list_error"]:
                return error(state["list_error"])
            return respond({"results": [] if state["empty"] else rows[direction]})
        if path.startswith("/api/box-donations/"):
            if state["detail_error"]:
                return error(state["detail_error"])
            row = next(r for group in rows.values() for r in group if r["proposal_id"] == path.split("/")[-2])
            return respond(row)
        if re.fullmatch(r"/api/boxes/\d+/", path):
            if state["preview_error"]:
                return error(state["preview_error"])
            return respond({"id": int(path.split("/")[-2]), "global_code": "MOCK-OUT-001",
                            "species": {"id": 1, "scientific_name": "Aurelia aurita"},
                            "lineage": {"parents": [], "children": []}, "movements": [], "locations": [],
                            "biological_measurements": [], "biological_timeline": [], "temperature_history": []})
        if re.fullmatch(r"/api/boxes/\d+/history/", path):
            return respond({"events": [], "candidates": [], "assessments": [], "historical_transitions": [], "starting_location": None})
        if "temperature" in path and path.startswith("/api/boxes/"):
            return respond({"results": [], "series": [], "points": []})
        if path == "/api/accounts/members/":
            member = {"membership_id": 1, "user_id": 10, "full_name": "Donor Témoin", "first_name": "Donor",
                      "last_name": "Témoin", "email": "qa@example.test", "organization": REFERENCE.ORG,
                      "role": "admin", "role_label": "Admin", "is_responsable": False,
                      "is_active": True, "last_login": None, "is_self": True}
            return respond({"members": [member], "manageable_organizations": [REFERENCE.ORG],
                            "roles": [{"value": "admin", "label": "Admin"}],
                            "can_manage_admin_memberships": True, "can_relinquish_responsable": False})
        if path == "/api/accounts/invitations/":
            return respond({"invitations": [], "count": 0, "results": []})
        if path == "/api/admin/box-inventory/":
            return respond({"count": 1, "next": None, "previous": None,
                            "results": [{"id": 8, "global_code": "MOCK-OUT-001", "local_code": "001", "status": "active",
                                         "species": {"id": 1, "scientific_name": "Aurelia aurita"}, "thermal_zone": None,
                                         "created_on": "2026-11-15", "inventory_created_on": "2026-11-15",
                                         "latest_measurement": None, "current_polyp_state": None, "last_location": None}],
                            "summary": {"pending_review_count": 0, "active_without_location_count": 1, "pending_without_location_count": 0},
                            "filter_options": {"creation_years": [2026], "reference_date": "2026-11-18"},
                            "selection": {"eligible_count": 0, "max_count": 100}})
        unexpected.append((request.method, path))
        return error(404)

    def passed(name, data=None):
        checks.append(name)
        print("PASS [MOCKED] " + name + (" " + json.dumps(data, ensure_ascii=False) if data is not None else ""), flush=True)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            print("Chromium " + browser.version + "; API responses exclusively mocked; port 5174", flush=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000}, service_workers="block")
            # Block all off-origin traffic; only static files reach the local server.
            context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(BASE + "/") else route.abort())
            context.route(re.compile(re.escape(BASE) + r"/api/"), api)
            context.add_cookies([{"name": "csrftoken", "value": "mock-csrf", "url": BASE}])
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" and "status of" not in message.text else None)
            def load(path="/administration/transfers"):
                page.goto(BASE + path)
                page.wait_for_load_state("networkidle")
            def ready():
                expect(page.locator("#admin-transfers .exchange-row")).to_have_count(4)
                expect(page.locator("#admin-receipts .exchange-row")).to_have_count(4)
            def capture(name):
                page.screenshot(path=str(captures / (name + ".png")), full_page=False)
            def detail(section, index=0):

                section.locator(".box-inventory-actions button").nth(index).click()
                page.get_by_role("menuitem").click()
                return page.locator(".exchange-modal")
            def close_detail():
                page.locator(".exchange-modal .modal-close-button").click()
                expect(page.locator(".exchange-modal")).to_have_count(0)
            def metrics(selector):
                return page.locator(selector).first.evaluate("el => { const r=el.getBoundingClientRect(),s=getComputedStyle(el); return {height:r.height,font:s.fontSize,padding:s.padding,gap:s.gap}; }")

            load()
            ready()
            for width, height in [(1440, 1000), (1280, 800)]:
                page.set_viewport_size({"width": width, "height": height})
                expect(page.locator(".admin-flow-nav .admin-flow-step[aria-current='page']")).to_have_text("Envois et réceptions")
                outgoing = page.locator("#admin-transfers")
                incoming = page.locator("#admin-receipts")
                a, b = outgoing.bounding_box(), incoming.bounding_box()
                assert b["y"] >= a["y"] + a["height"] + 32 and abs(a["x"] - b["x"]) < 2
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                for section in [outgoing, incoming]:
                    assert section.locator(".exchange-row").first.evaluate("el => el.scrollWidth <= el.clientWidth + 1")
                expect(outgoing.get_by_role("button", name="Envoyer", exact=True)).to_be_enabled()
                expect(outgoing.get_by_text(re.compile("L’envoi est indisponible|Sending is unavailable"))).to_have_count(0)
                expect(page.get_by_role("button", name=re.compile(r"^(Accept|Accepter)"))).to_have_count(0)
                expect(incoming.locator(".box-tracking-preview-link, canvas, svg[role='img']")).to_have_count(0)
                capture(f"desktop-{width}")
                passed(f"{width}x{height}: native AdminFlowNav, stacked sections, no page/row overflow, active Send, no old warning, no Accept, incoming no graph", metrics(".exchange-row"))

            page.set_viewport_size({"width": 1440, "height": 1000})
            outgoing = page.locator("#admin-transfers")
            incoming = page.locator("#admin-receipts")
            send = outgoing.get_by_role("button", name="Envoyer", exact=True)
            before_choices = len(requests)
            state["choices"] = "error"
            send.click()
            modal = page.get_by_role("dialog", name="Nouvel envoi", exact=True)
            expect(modal).to_be_visible()
            exchange_type = modal.get_by_label("Type d’échange", exact=True)
            expect(exchange_type).to_be_focused()
            expect(exchange_type).to_have_value("donation")
            assert exchange_type.locator("option").evaluate_all("els => els.map(el => [el.value, el.disabled])") == [
                ["donation", False], ["live", True], ["frozen", True], ["email_frozen", True],
            ]
            box = modal.get_by_label("Boîte concernée", exact=True)
            institution = modal.get_by_label("Institution destinataire", exact=True)
            submit = modal.locator("button[type='submit']")

            expect(modal.get_by_role("alert")).to_have_count(2)
            expect(box).to_be_disabled()
            expect(institution).to_be_disabled()
            expect(submit).to_be_disabled()
            state["choices"] = "empty"
            for select, empty_message in [(box, "Aucune boîte"), (institution, "Aucune autre structure")]:
                field = select.locator("..")
                field.get_by_role("button", name="Recharger", exact=True).click()
                expect(field.get_by_role("status")).to_contain_text(empty_message)
                expect(select).to_be_disabled()
                expect(select.locator("option")).to_have_count(1)
            expect(modal.get_by_role("alert")).to_have_count(0)
            expect(submit).to_be_disabled()
            assert not mutations
            choice_reads = requests[before_choices:]
            assert sum(r["path"] == DONATIONS + "destinations/" for r in choice_reads) == 2
            assert sum(r["path"] == SOURCE_BOXES for r in choice_reads) == 2
            footer_close = modal.locator("footer button[type='button']")
            x_close = modal.locator(".modal-close-button")
            footer_close.focus()
            page.keyboard.press("Tab")
            expect(x_close).to_be_focused()
            page.keyboard.press("Shift+Tab")
            expect(footer_close).to_be_focused()
            page.keyboard.press("Escape")
            expect(modal).to_have_count(0)
            expect(send).to_be_focused()
            passed("FR form: only Don enabled, independent choice errors and retries, distinct empty states, focus trap and restore")

            state["choices"] = "ready"
            send.click()
            expect(box).to_be_enabled()
            expect(institution.locator("optgroup")).to_have_count(3)
            groups = institution.locator("optgroup").evaluate_all(
                "els => els.map(el => [el.label, Array.from(el.children, opt => opt.textContent)])"
            )
            assert dict(groups)["France"] == ["Alpha Lab", "Zulu Lab"]
            assert dict(groups)["Japon"] == ["Tokyo Lab"]
            missing_label = next(label for label, names in groups if names == ["Blank Lab", "Unknown Lab"])
            assert "pays" in missing_label.lower(), groups
            assert [label for label, _ in groups] == page.evaluate(
                "labels => labels.toSorted(new Intl.Collator('fr').compare)", ["France", "Japon", missing_label]
            )
            expect(box).to_have_value("")
            expect(institution).to_have_value("")
            expect(submit).to_be_disabled()
            capture("donation-form")
            box.select_option(str(SELECTION["source_box_id"]))
            institution.select_option(SELECTION["destination_institution_id"])
            submit.click()
            confirmation = modal.get_by_role("checkbox")
            expect(confirmation).to_be_visible()
            expect(confirmation).not_to_be_checked()
            expect(modal.get_by_role("link")).to_have_attribute("href", "/boxes/MOCK-OUT-005")
            expect(submit).to_be_disabled()
            assert len(mutations) == 1
            assert mutations[0][:2] == ("POST", DONATIONS + "preflight/")
            assert json.loads(mutations[0][2]) == SELECTION
            confirmation.check()
            expect(submit).to_be_enabled()
            with page.expect_request(lambda request: request.method == "POST" and request.url == BASE + DONATIONS + "outgoing/"):
                submit.dblclick()
            expect(submit).to_be_disabled()
            assert len(pending_sends) == 1 and len(mutations) == 2
            pending_sends[0].fulfill(status=503, content_type="application/json", body='{"detail":"Mock uncertain send"}')
            expect(modal.get_by_role("alert")).to_be_visible()
            expect(submit).to_have_text("Recharger")
            expect(box).to_be_disabled()
            expect(institution).to_be_disabled()
            expect(confirmation).to_be_disabled()
            expect(outgoing.locator(".exchange-row")).to_have_count(4)
            before_retry = len(requests)
            submit.click()
            expect(modal).to_have_count(0)
            expect(outgoing.locator(":scope > [role='status']")).to_have_text(re.compile(r"\S+"))
            expect(outgoing.locator(".exchange-row")).to_have_count(5)
            expect(outgoing.locator(".exchange-row").last).to_contain_text("MOCK-OUT-005")
            expect(incoming.locator(".exchange-row")).to_have_count(4)
            assert [r["method"] for r in requests[before_retry:] if r["path"] == DONATIONS + "outgoing/"] == ["POST", "GET"]
            assert len(mutations) == 3
            assert mutations[1] == mutations[2], "retry must replay identical method, URL and raw body"
            body = json.loads(mutations[1][2])
            request_id = UUID(body["request_id"])
            assert request_id.version == 4
            assert body == {**SELECTION, "request_id": str(request_id), "history_verified": True, "history_sha256": HISTORY_HASH}
            passed("FR form: sorted country optgroups including missing country, exact preflight and confirmed hash, duplicate click suppressed, identical retry, outgoing GET refresh")
            rows["outgoing"].pop()
            load()
            ready()
            for section, term in [(outgoing, "musee"), (incoming, "source")]:
                search = section.get_by_role("searchbox")
                for query, count in [(term, 4), ("aurelia", 4), ("001", 1), ("no-match", 0)]:
                    search.fill(query)
                    expect(section.locator(".exchange-row")).to_have_count(count)
                search.fill("")
                section.locator("select").first.select_option("donation")
                for status in ["pending", "cancelled"]:
                    section.locator("select").nth(1).select_option(status)
                    expect(section.locator(".exchange-row")).to_have_count(2)
                section.locator(".box-inventory-reset-slot button").click()
                expect(section.locator(".exchange-row")).to_have_count(4)
            expect(incoming.get_by_role("button", name="Actualiser")).to_have_count(0)
            passed("FR: search box/species/accent-folded institution, type and pending/cancelled filters, reset; no refresh button")

            assert not any(re.fullmatch(r"/api/boxes/\d+/", r["path"]) for r in requests)
            link = outgoing.locator(".box-tracking-preview-link").first
            link.hover()
            expect(page.locator(".box-tracking-preview")).to_be_visible()
            expect(page.locator(".box-tracking-preview-state[role='status']")).to_have_count(0)
            expect(page.locator(".box-tracking-preview [role='alert']")).to_have_count(0)
            expect(page.locator(".box-tracking-preview")).not_to_contain_text("Historique unifié indisponible")
            expect(page.locator(".box-tracking-preview .bio-trend-canvas svg")).to_have_count(1)
            capture("preview-hover")
            assert any(r["path"] == "/api/boxes/8/" for r in requests)
            page.locator(".box-tracking-preview-heading button").click()
            link.blur()
            page.mouse.move(0, 0)
            link.focus()
            expect(page.locator(".box-tracking-preview")).to_be_visible()
            page.keyboard.press("Tab")
            expect(page.locator(".box-tracking-preview-heading button")).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.locator(".box-tracking-preview")).to_have_count(0)
            passed("Outgoing BoxTrackingPreview lazy authenticated reader: hover, focus, Tab into close, Escape; no pre-hover detail read")
            # A new navigation clears shared GET caches before a denied preview read.
            for code in [403, 404, 500]:
                state["preview_error"] = code
                load()
                ready()
                outgoing.locator(".box-tracking-preview-link").first.hover()
                expect(page.locator(".box-tracking-preview [role='alert']")).to_be_visible()
                expect(page.locator(".box-tracking-preview .bio-trend-canvas svg")).to_have_count(0)
                passed(f"Preview mocked {code}: error rather than unauthorized graph")
            state["preview_error"] = 0
            load()
            ready()
            for section in [outgoing, incoming]:
                for index in [0, 1]:
                    dialog = detail(section, index)
                    expect(dialog.locator(".exchange-facts")).to_be_visible()
                    expected = rows["outgoing" if section == outgoing else "incoming"][index]["proposal_id"]
                    expect(dialog).to_contain_text(expected)
                    assert any(r["path"] == f"/api/box-donations/{expected}/" for r in requests)
                    expect(dialog.get_by_role("button", name=re.compile(r"^(Accept|Accepter)"))).to_have_count(0)
                    page.keyboard.press("Tab")
                    assert dialog.evaluate("el => el.contains(document.activeElement)")
                    page.keyboard.press("Shift+Tab")
                    assert dialog.evaluate("el => el.contains(document.activeElement)")
                    if index == 1:
                        expect(dialog).to_contain_text("Date d’annulation")
                    capture("details-" + ("out" if section == outgoing else "in") + str(index))
                    close_detail()
            passed("Both row menus fetch proposal detail, pending/cancelled facts, cancellation date, keyboard focus contained, no Accept")
            for code in [403, 404, 500]:
                state["detail_error"] = code
                dialog = detail(outgoing)
                expect(dialog.get_by_role("alert")).to_be_visible()
                expect(dialog.locator(".exchange-facts")).to_have_count(0)
                state["detail_error"] = 0
                dialog.get_by_role("alert").get_by_role("button").click()
                expect(dialog.locator(".exchange-facts")).to_be_visible()
                close_detail()
                state["list_error"] = code
                load()
                expect(page.locator(".exchange-section [role='alert']")).to_have_count(2)
                expect(page.locator(".exchange-row")).to_have_count(0)
                if code == 403:
                    expect(outgoing.get_by_role("alert")).to_contain_text("Accès refusé")
                elif code == 404:
                    expect(outgoing.get_by_role("alert")).to_contain_text("indisponible")
                else:
                    expect(outgoing.get_by_role("alert")).to_contain_text("Impossible")
                state["list_error"] = 0
                for section in [outgoing, incoming]:
                    section.get_by_role("alert").get_by_role("button").click()
                ready()
                passed(f"Mocked {code}: distinct list error, stale rows absent, detail error, explicit retry recovery")
            state["empty"] = True
            load()
            expect(page.get_by_text("Aucune proposition de don", exact=True)).to_have_count(2)
            state["empty"] = False
            state["hold"] = True
            page.goto(BASE + "/administration/transfers")
            expect(page.locator(".exchange-list[aria-busy='true']")).to_have_count(2)
            expect(page.locator(".exchange-list [role='status']")).to_have_count(2)
            expect(incoming.get_by_role("button", name="Actualiser")).to_have_count(0)
            capture("loading")
            state["hold"] = False
            for route in held:
                direction = urlparse(route.request.url).path.split("/")[-2]
                route.fulfill(content_type="application/json", body=json.dumps({"results": rows[direction]}))
            held.clear()
            ready()
            passed("Empty distinct from loading; both skeletons aria-busy; no refresh button")

            # Compare native reference screens, with mocked rows rather than production fixtures.
            exchange_metrics = metrics(".exchange-row")
            page.locator(".admin-flow-step[aria-controls='admin-inventory']").click()
            expect(page.locator("#admin-inventory .box-inventory-row")).to_have_count(1)
            inventory_metrics = metrics("#admin-inventory .box-inventory-row")
            capture("inventory-reference")
            page.locator(".admin-flow-step[aria-controls='admin-accounts']").click()
            expect(page.locator(".member-table tbody tr")).to_have_count(1)
            capture("team-reference")
            team_metrics = metrics(".member-table tbody tr")
            passed("Inventory/Team native reference screens rendered; measured Inventory vs exchange density", {"exchange": exchange_metrics, "inventory": inventory_metrics, "team": team_metrics})

            state["language"] = "en"
            page.add_init_script("localStorage.setItem('polypbase.interfaceLanguage', 'en')")
            load()
            ready()
            expect(outgoing.get_by_role("heading", name="Sending", exact=True)).to_be_visible()
            expect(incoming.get_by_role("heading", name="Receiving", exact=True)).to_be_visible()
            expect(outgoing.get_by_role("button", name="Send", exact=True)).to_be_enabled()
            expect(page.get_by_text(re.compile("Sending is unavailable|L’envoi est indisponible"))).to_have_count(0)
            before = len(requests)
            outgoing.get_by_role("button", name="Send", exact=True).click()
            english_modal = page.get_by_role("dialog", name="New shipment", exact=True)
            expect(english_modal).to_be_visible()
            expect(english_modal).not_to_contain_text("Form preview. Nothing will be sent.")
            expect(english_modal.get_by_label("Exchange type", exact=True)).to_be_focused()
            expect(english_modal.get_by_label("Box", exact=True)).to_be_enabled()
            expect(english_modal.locator("optgroup")).to_have_count(3)
            english_modal.locator("footer").get_by_role("button", name="Close", exact=True).click()
            expect(english_modal).to_have_count(0)
            expect(outgoing.get_by_role("button", name="Send", exact=True)).to_be_focused()
            assert sorted(r["path"] for r in requests[before:]) == sorted([DONATIONS + "destinations/", SOURCE_BOXES])
            outgoing.locator("select").nth(1).select_option("cancelled")
            expect(outgoing.locator(".exchange-row")).to_have_count(2)
            expect(outgoing.locator(".exchange-row").first).to_contain_text("Cancelled")
            outgoing.locator("select").nth(1).select_option("pending")
            expect(outgoing.locator(".exchange-row").first).to_contain_text("Pending")
            capture("english")
            passed("EN headings, translated donation form reads only its choices, close restores focus, Pending/Cancelled labels and filters")
            load("/profile")
            expect(page.locator(".profile-identity-actions")).to_be_visible()
            expect(page.locator("a[href^='/shared-boxes']")).to_have_count(0)
            expect(page.locator(".profile-identity-actions")).not_to_contain_text("Received shares")
            for path in ["/shared-boxes", "/shared-boxes/accept#token=mock", "/shared-boxes/mock-id"]:
                load(path)
                expect(page).to_have_url(BASE + "/")
            assert not any("shared-boxes" in r["path"] or "box-shares" in r["path"] for r in requests)
            passed("Profile has no personal sharing link; retired shared routes redirect home without old sharing API calls")
            tablet = browser.new_context(viewport={"width": 960, "height": 600}, has_touch=True, is_mobile=False, service_workers="block")
            tablet.route("**/*", lambda route: route.continue_() if route.request.url.startswith(BASE + "/") else route.abort())
            tablet.route(re.compile(re.escape(BASE) + r"/api/"), api)
            tp = tablet.new_page()
            tp.on("pageerror", lambda error: errors.append(str(error)))
            tp.add_init_script("window.__adminSeen=false; new MutationObserver(() => {if(document.querySelector('.admin-panel,.exchange-section')) window.__adminSeen=true;}).observe(document,{childList:true,subtree:true});")
            before = len(requests)
            tp.goto(BASE + "/administration/transfers")
            expect(tp).to_have_url(BASE + "/")
            tp.wait_for_load_state("networkidle")
            expect(tp.locator(".admin-panel, .admin-flow-nav, .exchange-section")).to_have_count(0)
            assert not tp.evaluate("window.__adminSeen")
            assert not any("box-donations" in r["path"] for r in requests[before:])
            expect(tp.get_by_role("button", name="Administration", exact=True)).to_have_count(0)
            tp.screenshot(path=str(captures / "tablet-960.png"), full_page=True)
            passed("960x600 touch tablet direct admin URL redirects /; no observed intermediate admin DOM, admin menu or donation fetch")
            assert not unexpected, f"Unmocked endpoints: {unexpected}"
            assert [(method, path) for method, path, _ in mutations] == [
                ("POST", DONATIONS + "preflight/"), ("POST", DONATIONS + "outgoing/"), ("POST", DONATIONS + "outgoing/"),
            ]
            assert not errors, f"Browser errors: {errors}"
            donation_requests = [r for r in requests if r["path"].startswith(DONATIONS)]
            assert all(r["headers"].get("x-organization-id") == "1" for r in donation_requests)
            assert any(r["path"] == SOURCE_BOXES and r["query"] == {
                "organization": ["1"], "status": ["active"], "q": [""], "limit": ["20"], "offset": ["0"],
            } and r["headers"].get("x-organization-id") == "1" for r in requests)
            passed("Only expected mocked POSTs, no unexpected API endpoints or browser errors; donation requests scoped to the mock organization")
            if screenshots:
                print("Temporary screenshots: " + str(captures), flush=True)
                time.sleep(max(0, min(inspect_seconds, 120)))
            browser.close()
            print(f"RESULT: {len(checks)} check groups passed [MOCKED], {len(requests)} intercepted API requests", flush=True)
    except Exception:
        print("QA failure diagnostics: " + json.dumps({"unexpected": unexpected, "browser_errors": errors, "last_requests": requests[-6:]}, ensure_ascii=False), flush=True)
        raise
    finally:
        server.shutdown()
        server.server_close()
        if not keep_screenshots:
            shutil.rmtree(captures)
        print("Port 5174 server stopped; " + ("remove temporary screenshots after inspection: " + str(captures) if keep_screenshots else "temporary screenshots removed"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    parser.add_argument("--inspect-seconds", type=int, default=0, help="Keep temporary screenshots available for up to 120 seconds")
    parser.add_argument("--keep-screenshots", action="store_true", help="Manual inspection only: remove .donations-qa-temp afterward")
    args = parser.parse_args()
    run(args.screenshots or args.keep_screenshots, args.inspect_seconds, args.keep_screenshots)
