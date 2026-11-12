"""Run Chromium UI checks against the built frontend and an in-memory synthetic API.

No Django, database, account or SMTP service is used. Requires the optional local
Python Playwright tooling and its Chromium installation, not a product dependency.
Run after npm run build: python scripts/qa-box-sharing.py [--screenshots].
"""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timedelta, timezone
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import threading
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
GRANT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
OTHER_GRANT = "11111111-2222-4333-8444-555555555555"
NOW = datetime.now(timezone.utc)


def instant(hours):
    return (NOW + timedelta(hours=hours)).isoformat()


def date_input(hours):
    return (NOW + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M")


ORG = {"id": 1, "name": "Laboratoire témoin", "slug": "synthetic"}
PROFILE = {
    "id": 10, "email": "recipient@example.test", "first_name": "Alex", "last_name": "Témoin",
    "is_superuser": False, "interface_language": "fr", "organizations": [ORG], "active_organization": ORG,
    "memberships": [{"organization": ORG, "role": "admin", "role_label": "Admin", "is_responsable": False}],
    "available_languages": [{"code": "fr", "label": "Français"}, {"code": "en", "label": "English"}],
}
NAMES = ["source.institution_name", "source.institution_portable_id", "source.box_code",
         "identity.species_scientific_name", "identity.strain_code", "identity.global_strain_id",
         "observation.time", "observation.polyp_count", "observation.ephyrae_count", "observation.strobila_count",
         "observation.origin", "observation.observer_status"]
OPTIONS = {
    "schema": "polypbase.scientific-snapshot", "schema_version": "1.0", "modes": ["live", "frozen"],
    "frozen_capture_available": True, "mandatory_selectors": ["source.institution_name"],
    "selectors": [{"name": name, "group": name.split(".")[0], "mandatory": name == "source.institution_name",
                   "requires": ["observation.time"] if name.startswith("observation.") and name != "observation.time" else []} for name in NAMES],
    "lifetime_policy": {"configured": True, "invitation_max_lifetime_seconds": 86400, "access_max_lifetime_seconds": 604800},
}


def snapshot(selectors=None):
    selectors = selectors or NAMES
    result = {"schema": OPTIONS["schema"], "schema_version": "1.0", "disclosed": selectors}
    values = {"source.institution_name": "Institut témoin — source autorisée", "source.institution_portable_id": OTHER_GRANT,
              "source.box_code": "SYN-001", "identity.species_scientific_name": "Aurelia aurita",
              "identity.strain_code": "SYN-A", "identity.global_strain_id": GRANT}
    for group in ["source", "identity"]:
        fields = {name.split(".")[1]: {"state": "value", "value": values[name]} for name in selectors if name.startswith(group + ".")}
        if fields:
            result[group] = fields
    if "observation.time" in selectors:
        result["observations"] = []
        for index in range(155):
            row = {"time": {"precision": "iso_week" if index % 2 == 0 else "legacy_unverified", "iso_year": 2023 + index // 52,
                            "iso_week": index % 52 + 1, "measured_on": None}}
            counts = {name.split(".")[1]: ({"state": "null"} if name.endswith("strobila_count") else {"state": "value", "value": 0 if index == 0 else index})
                      for name in selectors if name.startswith("observation.") and name.endswith("_count")}
            if counts:
                row["values"] = counts
            provenance = {}
            if "observation.origin" in selectors:
                provenance.update(origin={"state": "value", "value": "file_import"}, import_format={"state": "value", "value": "csv"})
            if "observation.observer_status" in selectors:
                provenance["observer_status"] = {"state": "value", "value": "not_recorded"}
            if provenance:
                row["provenance"] = provenance
            result["observations"].append(row)
    return result


def summary():
    return {"id": GRANT, "mode": "live", "read_only": True, "state": "active", "policy_revision": 1,
            "schema_version": "1.0", "access_expires_at": instant(72), "source_institution": {"name": "Institut témoin — source autorisée"}}


def share_record(identifier, body):
    return {"id": identifier, "box": {"id": 7, "global_code": "SYN-001"}, "recipient_email": body["recipient_email"],
            "mode": body["mode"], "state": "pending", "schema_version": "1.0", "selectors": body["selectors"], "policy_revision": 1,
            "invitation": {"generation": 1, "issued_at": instant(0), "expires_at": body["invitation_expires_at"], "accepted_at": None,
                           "delivery": {"status": "uncertain", "updated_at": instant(0)}},
            "access_expires_at": body["access_expires_at"], "revoked_at": None, "created_at": instant(0),
            "created_by": {"first_name": "Alex", "last_name": "Témoin", "email": PROFILE["email"]},
            "capture": {"captured_at": instant(0), "content_sha256": "a" * 64} if body["mode"] == "frozen" else None}


class StaticApp(SimpleHTTPRequestHandler):
    def do_GET(self):
        if not Path(self.translate_path(urlparse(self.path).path)).is_file():
            self.path = "/index.html"
        super().do_GET()

    def log_message(self, *args):
        pass


def run(screenshots=False):
    assert (ROOT / "dist/index.html").is_file(), "Build the frontend before browser QA"
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(StaticApp, directory=str(ROOT / "dist")))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    state = {"profile": copy.deepcopy(PROFILE), "authenticated": True, "options": copy.deepcopy(OPTIONS), "shares": [],
             "reader_error": None, "invitation_error": None, "conflict": False, "accepted": False}
    requests, mutations, failures = [], [], []
    capture_dir = ROOT / ".sharing-qa"
    if screenshots:
        capture_dir.mkdir(exist_ok=True)

    def api(route):
        request = route.request
        url = urlparse(request.url)
        path = url.path
        query = parse_qs(url.query)
        body = request.post_data_json if request.post_data else {}
        requests.append((request.method, path, request.headers, body))
        if request.method in ["POST", "PATCH"]:
            mutations.append((path, body))
        def respond(data, status=200):
            route.fulfill(status=status, content_type="application/json", body=json.dumps(data), headers={"Cache-Control": "no-store"})
        def error(code, status):
            respond({"code": code, "detail": "Synthetic response"}, status)
        if path == "/api/auth/session/":
            if request.method == "POST":
                state["authenticated"] = True
            return respond({"interface_language": state["profile"]["interface_language"]})
        if path == "/api/auth/logout/":
            state["authenticated"] = False
            return respond({})
        if not state["authenticated"]:
            return error("not_authenticated", 403)
        if path == "/api/profile/":
            return respond(state["profile"])
        if path == "/api/thermal-zones/":
            return respond({"count": 0, "next": None, "previous": None, "results": []})
        if path == "/api/dashboard/":
            return respond({"recent_accesses": [], "active_box_count": 0, "box_count": 0, "species_count": 0, "strain_count": 0})
        if path == "/api/boxes/":
            found = [] if "FOREIGN" in query.get("q", [""])[0] else [{"id": 7, "global_code": "SYN-001", "organization": ORG}]
            return respond({"count": len(found), "next": None, "previous": None, "results": found})
        if path == "/api/admin/box-shares/options/":
            return respond(state["options"])
        if path == "/api/admin/box-shares/preview/":
            return respond({"snapshot": snapshot(body["selectors"])})
        if path == "/api/admin/box-shares/":
            if request.method == "POST":
                share = share_record(len(state["shares"]) + 1, body)
                state["shares"].insert(0, share)
                return respond({"share": share}, 201)
            offset = int(query.get("offset", [0])[0])
            rows = [item for item in state["shares"] if not query.get("box_id") or item["box"]["id"] == int(query["box_id"][0])]
            return respond({"shares": rows[offset:offset + 25], "has_more": len(rows) > offset + 25, "server_time": instant(0)})
        match = re.fullmatch(r"/api/admin/box-shares/(\d+)/(revoke/|reissue-invitation/)?", path)
        if match:
            share = next(item for item in state["shares"] if item["id"] == int(match[1]))
            if match[2] == "revoke/":
                share.update(state="revoked", revoked_at=instant(0))
            elif match[2] == "reissue-invitation/":
                share["invitation"].update(expires_at=body["invitation_expires_at"], generation=share["invitation"]["generation"] + 1)
            elif request.method == "PATCH":
                if state["conflict"]:
                    state["conflict"] = False
                    share["policy_revision"] += 1
                    return error("policy_revision_conflict", 409)
                if "selectors" in body:
                    share["selectors"] = body["selectors"]
                    share["policy_revision"] += 1
                if "access_expires_at" in body:
                    share["access_expires_at"] = body["access_expires_at"]
            return respond({"share": share, "server_time": instant(0)})
        if path == "/api/shared-boxes/":
            return respond({"shares": [summary()] if state["accepted"] and state["profile"]["id"] == 10 else [], "server_time": instant(0)})
        if path == "/api/shared-boxes/invitations/inspect/":
            if state["invitation_error"]:
                return error(*state["invitation_error"])
            info = {key: value for key, value in summary().items() if key in ["mode", "read_only", "access_expires_at", "source_institution"]}
            info["invitation_expires_at"] = instant(4)
            return respond({"invitation": info})
        if path == "/api/shared-boxes/invitations/accept/":
            state["accepted"] = True
            return respond({"share": summary()})
        if path == f"/api/shared-boxes/{GRANT}/":
            if state["profile"]["id"] != 10:
                return error("share_not_found", 404)
            if state["reader_error"]:
                return error(*state["reader_error"])
            return respond({"share": summary(), "content": {"kind": "live_projection", "projected_at": instant(0), "consistency": "non_atomic"}, "snapshot": snapshot()})
        if path.startswith("/api/shared-boxes/"):
            return error("share_not_found", 404)
        failures.append((request.method, path))
        return error("not_found", 404)

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.route("**/api/**", api)
            context.add_cookies([{"name": "csrftoken", "value": "synthetic-csrf", "url": base}])
            page = context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(base + "/administration/transfers")
            expect(page.get_by_role("button", name="Envois et réceptions", exact=True)).to_have_attribute("aria-current", "page")
            expect(page.get_by_role("button", name="Partager une boîte", exact=True)).to_be_enabled()
            page.get_by_role("button", name="Partager une boîte", exact=True).click()
            page.get_by_role("searchbox").fill("SYN-001")
            page.get_by_role("button", name="Rechercher", exact=True).click()
            page.get_by_role("button", name="SYN-001", exact=True).click()
            expect(page.get_by_role("checkbox", name=re.compile("^Polypes"))).to_be_disabled()
            page.get_by_role("checkbox", name=re.compile("^Structure propriétaire")).check()
            page.get_by_role("checkbox", name=re.compile("^Code de la boîte")).check()
            page.get_by_role("checkbox", name=re.compile("^Temps du relevé")).check()
            page.get_by_role("checkbox", name=re.compile("^Polypes")).check()
            page.get_by_label("Destinataire", exact=True).fill(PROFILE["email"])
            page.get_by_role("radio", name=re.compile("^Données actualisées")).check()
            page.get_by_label(re.compile("^Expiration de l’invitation")).fill(date_input(2))
            page.get_by_label(re.compile("^Expiration de l’accès")).fill(date_input(72))
            page.get_by_role("button", name="Calculer l’aperçu des données").click()
            expect(page.locator(".sharing-preview")).to_be_visible()
            expect(page.locator(".sharing-preview td").first).to_have_text("0")
            if screenshots:
                page.screenshot(path=str(capture_dir / "admin-form.png"))
            page.get_by_role("button", name="Créer le partage et envoyer l’invitation").click()
            dialog = page.get_by_role("dialog")
            expect(dialog).to_be_visible()
            assert page.locator("#root").evaluate("el => el.inert")
            dialog.get_by_role("button", name="Créer le partage et envoyer l’invitation").click()
            expect(page.locator(".sharing-row")).to_have_count(1)
            expect(page.get_by_text("Envoi incertain", exact=True)).to_be_visible()
            created = next(body for path, body in mutations if path == "/api/admin/box-shares/")
            assert created["selectors"] == ["source.institution_name", "source.box_code", "observation.time", "observation.polyp_count"]
            assert created["mode"] == "live"
            assert not page.locator("#root").evaluate("el => el.inert")
            # Concurrent policy changes require an explicit reload; no mutation is replayed.
            page.get_by_role("button", name="Modifier le partage").click()
            page.get_by_role("checkbox", name=re.compile("^Éphyrules")).check()
            page.get_by_role("button", name="Calculer l’aperçu des données").click()
            expect(page.locator(".sharing-preview")).to_be_visible()
            state["conflict"] = True
            page.get_by_role("button", name="Enregistrer les modifications", exact=True).click()
            expect(page.get_by_role("dialog")).to_contain_text("informations supplémentaires")
            page.get_by_role("dialog").get_by_role("button", name="Enregistrer les modifications", exact=True).click()
            expect(page.get_by_role("button", name="Relire la politique actuelle et abandonner le brouillon")).to_be_visible()
            assert mutations[-1][1]["confirm_expansion"] is True
            page.get_by_role("button", name="Relire la politique actuelle et abandonner le brouillon").click()
            expect(page.get_by_role("checkbox", name=re.compile("^Éphyrules"))).not_to_be_checked()
            page.locator(".sharing-editor").get_by_role("button", name="Annuler", exact=True).click()
            # Reissue leaves access expiry untouched, then revoke requires confirmation.
            page.get_by_role("button", name="Régénérer l’invitation", exact=True).click()
            page.locator(".sharing-reissue").get_by_label(re.compile("^Expiration de l’invitation")).fill(date_input(3))
            page.locator(".sharing-reissue").get_by_role("button", name="Régénérer l’invitation", exact=True).click()
            expect(page.get_by_role("dialog")).to_contain_text("invalide tous les liens précédents")
            page.get_by_role("dialog").get_by_role("button", name="Régénérer l’invitation", exact=True).click()
            expect(page.locator(".sharing-reissue")).to_have_count(0)
            assert list(mutations[-1][1]) == ["invitation_expires_at"]
            page.get_by_role("button", name="Révoquer le partage", exact=True).click()
            count = len(mutations)
            page.get_by_role("dialog").locator(".confirm-action-cancel").click()
            assert len(mutations) == count
            page.get_by_role("button", name="Révoquer le partage", exact=True).click()
            page.get_by_role("dialog").get_by_role("button", name="Révoquer le partage", exact=True).click()
            expect(page.get_by_role("button", name="Révoquer le partage", exact=True)).to_be_disabled()
            if screenshots:
                page.screenshot(path=str(capture_dir / "admin-list.png"))
            # Frozen revisions must explicitly recapture, without silently changing expiry.
            state["shares"][0].update(mode="frozen", state="pending", revoked_at=None,
                                       capture={"captured_at": instant(0), "content_sha256": "a" * 64})
            page.reload()
            page.get_by_role("button", name="Modifier le partage").click()
            page.get_by_role("checkbox", name=re.compile("^Éphyrules")).check()
            page.get_by_role("button", name="Calculer l’aperçu des données").click()
            expect(page.locator(".sharing-preview")).to_be_visible()
            page.get_by_role("button", name="Enregistrer les modifications", exact=True).click()
            expect(page.get_by_role("dialog")).to_contain_text("nouvelle capture")
            page.get_by_role("dialog").get_by_role("button", name="Enregistrer les modifications", exact=True).click()
            expect(page.locator(".sharing-editor")).to_have_count(0)
            assert mutations[-1][1]["confirm_recapture"] is True
            assert "access_expires_at" not in mutations[-1][1]
            # Pagination is server-bounded; filtering sends the authorized Box ID.
            first = copy.deepcopy(state["shares"][0])
            state["shares"] = [dict(copy.deepcopy(first), id=index + 1) for index in range(26)]
            page.reload()
            expect(page.locator(".sharing-row")).to_have_count(25)
            page.get_by_role("navigation", name="Pages des envois").get_by_role("button", name="Suivant", exact=True).click()
            expect(page.locator(".sharing-row")).to_have_count(1)
            expect(page.get_by_text("Page 2", exact=True)).to_be_visible()
            page.get_by_role("button", name="Filtrer par boîte", exact=True).click()
            page.get_by_role("searchbox").fill("SYN-001")
            page.get_by_role("button", name="Rechercher", exact=True).click()
            page.get_by_role("button", name="SYN-001", exact=True).click()
            expect(page.get_by_text("Page 1", exact=True)).to_be_visible()
            assert any(path == "/api/admin/box-shares/" and method == "GET" for method, path, headers, body in requests)
            state["options"]["lifetime_policy"]["configured"] = False
            page.reload()
            expect(page.get_by_role("button", name="Partager une boîte", exact=True)).to_be_disabled()
            state["options"]["lifetime_policy"]["configured"] = True
            # Standalone invitation: existing login, fragment preservation, inspect, explicit accept.
            state["authenticated"] = False
            state["profile"].update(organizations=[], active_organization=None, memberships=[])
            requests.clear()
            page.goto(base + "/shared-boxes/accept#token=synthetic-secret")
            page.get_by_label("Adresse e-mail", exact=True).fill(PROFILE["email"])
            page.get_by_label("Mot de passe", exact=True).fill("synthetic-only-not-a-real-password")
            page.get_by_role("button", name="Se connecter", exact=True).click()
            expect(page.get_by_role("button", name="Accepter l’invitation")).to_be_visible()
            assert "#token=synthetic-secret" in page.url
            assert not any(path.endswith("/accept/") for method, path, headers, body in requests)
            page.get_by_role("button", name="Accepter l’invitation").click()
            expect(page.locator(".sharing-readonly-banner")).to_be_visible()
            assert "#" not in page.url and "token" not in page.url
            expect(page.locator(".sharing-readonly-banner")).to_contain_text("Lecture seule")
            expect(page.locator(".sharing-readonly-banner")).to_contain_text("Institut témoin — source autorisée")
            assert len(page.locator("tbody tr").all()) == 50
            expect(page.locator("tbody tr").first).to_contain_text("0")
            expect(page.locator("tbody tr").first).to_contain_text("Inconnu (NULL)")
            page.evaluate("window.scrollTo(0, 800)")
            assert abs(page.locator(".sharing-readonly-banner").bounding_box()["y"]) < 2
            assert page.locator(".sharing-readonly-banner button").count() == 0
            colors = page.locator(".sharing-readonly-banner").evaluate("el => [getComputedStyle(el).backgroundColor, getComputedStyle(el).color, getComputedStyle(el.querySelector('small')).color]")
            def luminance(color):
                rgb = [int(value) / 255 for value in re.findall(r"\d+", color)[:3]]
                linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in rgb]
                return sum(value * weight for value, weight in zip(linear, [0.2126, 0.7152, 0.0722]))
            contrast = (luminance(colors[0]) + 0.05) / (luminance(colors[1]) + 0.05)
            assert contrast >= 4.5 and colors[1] == colors[2], colors
            print(f"Banner measured contrast: {contrast:.2f}:1")
            if screenshots:
                page.screenshot(path=str(capture_dir / "reader-desktop.png"))
            page.get_by_role("button", name="Suivant", exact=True).click()
            expect(page.get_by_text("Page 2", exact=True)).to_be_visible()
            for width, height in [(960, 600), (390, 844), (320, 800)]:
                page.set_viewport_size({"width": width, "height": height})
                page.evaluate("window.scrollTo(0, 900)")
                assert abs(page.locator(".sharing-readonly-banner").bounding_box()["y"]) < 2
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), f"Reader overflow at {width}px"
                if screenshots and width == 390:
                    page.screenshot(path=str(capture_dir / "reader-phone.png"))
            assert not any(path.startswith(("/api/boxes", "/api/thermal-zones", "/api/dashboard", "/api/exports")) for method, path, headers, body in requests)
            for method, path, headers, body in requests:
                assert "x-organization-id" not in headers, (method, path)
                assert "synthetic-secret" not in path
            # Revocation/expiry clear the snapshot, never supplement or fall back.
            for code, status in [("share_revoked", 410), ("share_expired", 410), ("share_policy_changed", 409), ("share_not_found", 404)]:
                state["reader_error"] = (code, status)
                page.reload()
                expect(page.locator(".sharing-unavailable")).to_be_visible()
                assert not page.locator(".sharing-science").count()
                assert not page.get_by_text("Aurelia aurita", exact=True).count()
            state["reader_error"] = None
            page.goto(base + f"/shared-boxes/{OTHER_GRANT}")
            expect(page.locator(".sharing-unavailable")).to_be_visible()
            for code, status in [("invitation_recipient_mismatch", 403), ("invitation_expired", 410), ("invitation_unavailable", 404)]:
                state["invitation_error"] = (code, status)
                page.goto(base + "/shared-boxes/accept#token=synthetic-secret")
                expect(page.locator(".sharing-unavailable")).to_be_visible()
                assert not page.get_by_role("button", name="Accepter l’invitation").count()
            state["invitation_error"] = None
            page.goto(base + "/shared-boxes")
            expect(page.get_by_role("link", name="Consulter le partage")).to_be_visible()
            # An account change detected while focused cannot retain a previous snapshot.
            page.get_by_role("link", name="Consulter le partage").click()
            expect(page.locator(".sharing-readonly-banner")).to_be_visible()
            state["profile"].update(id=11, email="other@example.test")
            page.get_by_role("button", name="Recharger", exact=True).click()
            expect(page.locator(".sharing-unavailable")).to_be_visible()
            assert page.locator(".sharing-science").count() == 0
            assert page.get_by_text("Aurelia aurita", exact=True).count() == 0
            state["profile"].update(id=10, email=PROFILE["email"])
            page.goto(base + "/shared-boxes")
            expect(page.get_by_role("link", name="Consulter le partage")).to_be_visible()
            # Logout removes all scientific content and uses the existing login page.
            page.get_by_role("button", name="Se déconnecter", exact=True).click()
            expect(page.get_by_role("button", name="Se connecter", exact=True)).to_be_visible()
            assert page.locator(".sharing-science").count() == 0
            assert not any("snapshot" in key for key in page.evaluate("Object.keys(localStorage) + Object.keys(sessionStorage)"))
            # The Administration desktop-only guard is independent of reader availability.
            state["authenticated"] = True
            state["profile"] = copy.deepcopy(PROFILE)
            page.set_viewport_size({"width": 960, "height": 600})
            for admin_path in ["/administration/transfers", "/administration/unknown-sub-route", "/administration/team"]:
                page.goto(base + admin_path)
                expect(page).to_have_url(base + "/")
            expect(page.locator(".admin-panel")).to_have_count(0)
            page.goto(base + f"/shared-boxes/{GRANT}")
            expect(page.locator(".sharing-readonly-banner")).to_be_visible()
            page.locator('.sharing-skip').focus()
            page.keyboard.press('Enter')
            expect(page.locator('#shared-content')).to_be_focused()
            assert page.url.endswith(GRANT)
            assert not errors, errors
            assert not failures, failures
            print("PASS: Admin integration, source lookup, explicit disclosure/preview/create, revision conflict, FROZEN recapture, pagination/filtering, reissue/revoke, configuration guard")
            print("PASS: login/fragment/inspect/explicit acceptance without membership, received grants, direct UUID denial, expired/revoked states, focused account switch, logout")
            print("PASS: no institutional API/header in recipient context; 0/NULL/week semantics; 50-row history pages")
            print("PASS: sticky non-dismissible blue banner at 1440x1000, 960x600, 390x844 and 320x800; no page overflow; tablet Administration redirect")
            print("Console page errors: 0; unexpected API requests: 0; all fixtures synthetic, no database")
            context.close()
            browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--screenshots", action="store_true")
    run(parser.parse_args().screenshots)
