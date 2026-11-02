"""Rebuild the Japanese interface manifest and catalogs from the frozen workbook.

Read-only for the workbook. Writes, under the repository root:

- docs/translation-review/japanese/ja-manifest.csv
- docs/translation-review/japanese/ja-obsolete-workbook-keys.csv
- docs/translation-review/japanese/ja-takeru-review.csv (questions only, no proposal)
- frontend/src/i18n/ja.ts
- backend/locale/ja/LC_MESSAGES/django.po

``--check`` writes nothing and fails if any output would change.

Only Japanese validated "Fait" by Takeru is reused, and only for a current key whose
FR/EN still match the workbook, or whose difference has an explicit reviewed
decision in SEMANTIC_DECISIONS below. Everything else is left to the runtime
English fallback. No Japanese text is generated here.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "docs/translation-review/application"))
sys.path.insert(0, str(ROOT / "scripts"))
import check_i18n  # noqa: E402
import check_register  # noqa: E402

WORKBOOK_SHA256 = "e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307"
SHEET = "Traductions i18n"
COLUMNS = {"section": "A", "key": "B", "fr": "C", "en": "D", "en_status": "E",
           "correction": "F", "ja": "G", "ja_status": "H"}
EXPECTED_HEADERS = {"B": "Keys", "C": "🇫🇷", "D": "🇬🇧", "F": "Correction", "G": "🇯🇵"}
# The two administrator invitations found in validated JA permission refusals.
JA_CONTACT = re.compile(r"管理者に(?:お問い合わせください|連絡してください)")
PLACEHOLDER = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}|\$\{[A-Za-z_][A-Za-z0-9_]*\}")

MANIFEST = ROOT / "docs/translation-review/japanese/ja-manifest.csv"
OBSOLETE = ROOT / "docs/translation-review/japanese/ja-obsolete-workbook-keys.csv"
TAKERU = ROOT / "docs/translation-review/japanese/ja-takeru-review.csv"
JA_TS = ROOT / "frontend/src/i18n/ja.ts"
JA_PO = ROOT / "backend/locale/ja/LC_MESSAGES/django.po"

# Frontend permission refusals that must invite the user to contact an administrator
# (same reviewed list as frontend/scripts/test-i18n-catalogs.mjs).
PERMISSION_KEYS = frozenset({
    "boxActivateForbidden", "createBoxForbidden", "manageErrorMembershipAdminRequired",
    "manageErrorResponsableRequired", "measurementForbidden", "moveForbidden",
    "manageErrorActiveResponsableRequired", "boxArchiveForbidden", "subcultureForbidden",
})

# Reviewed decision for every row whose current FR/EN differs from the workbook, or
# which hits a glossary guard. (decision, reason_code, note)
R, D = "REUSE", "DEFER"
SEMANTIC_DECISIONS = {
    # React catalog: compatible differences.
    "auditDescriptionManualTemperature": (R, "JA_MATCHES_CURRENT", "Workbook EN was a wrong sentence (AC11); JA already says manual temperature for {zone}."),
    "auditMetaResponsableStatus": (R, "EN_TYPO_ONLY", "statut -> status."),
    "auditMetaCultureStatus": (R, "EN_TYPO_ONLY", "statut -> status."),
    "auditMetaMeasuredTemperature": (R, "GLOSSARY_MATCH", "Glossary G13 Measured temperature = 実測水温."),
    "confirmSubcultureTitle": (R, "EN_FORM_ONLY", "transplanting -> transplant; JA 移植 unchanged."),
    "confirmSubcultureAction": (R, "EN_FORM_ONLY", "transplanting -> transplant; JA 移植 unchanged."),
    "subcultureNote": (R, "EN_FORM_ONLY", "transplanting -> Transplant; JA 移植 unchanged."),
    "subcultureDeactivateParentMessage": (R, "JA_MATCHES_CURRENT", "JA already says the transplant is recorded."),
    "boxArchiveForbidden": (R, "PUNCTUATION_ONLY", "FR final period; JA contains the administrator invitation."),
    "adminZonesProbesTitle": (R, "SAME_CONCEPT", "FR unchanged; Thermal location -> Thermal zones; JA 温度管理設備 as in the validated export filter."),
    "adminExistingOrganizations": (R, "FR_TERM_ONLY", "Institutions -> Structures, same entity; 機関 is the reference JA for the entity."),
    "profileAllOrganizationsAccess": (R, "EN_TYPO_ONLY", "every institutions -> every institution."),
    "manageMemberDemoted": (R, "PUNCTUATION_ONLY", "EN final period."),
    "manageRelinquishResponsableConfirmTitle": (R, "PUNCTUATION_ONLY", "Space before question mark removed."),
    "chartEmpty": (R, "PUNCTUATION_ONLY", "EN final period."),
    "loginIntro": (R, "EN_TYPO_ONLY", "Acces -> Access."),
    "profileActiveOrganization": (R, "GLOSSARY_MATCH", "FR now Institution de travail; glossary G32 = 所属機関."),
    "moveDialogCurrentZone": (R, "EN_TYPO_ONLY", "Curent -> Current."),
    "historicalTemperatureFailed": (R, "EN_TYPO_ONLY", "Temperatures -> Temperature."),
    "qrLabelSpeciesSelectedCompact": (R, "EN_ABBREVIATION_ONLY", "select. -> sel."),
    "recentAccess": (R, "EN_TYPO_ONLY", "Lastest -> Latest."),
    "scanSearch": (R, "JA_MATCHES_CURRENT", "Scanned -> opened; JA already says recently opened."),
    "subcultureCompleted": (R, "PUNCTUATION_ONLY", "EN final period."),
    "subcultureForbidden": (R, "JA_ALREADY_HAS_INVITATION", "FR gained the administrator invitation already present in JA."),
    # React catalog: deferred to English.
    "auditFamilyMeasurements": (D, "RECORD_MEASUREMENT_OPEN", "EN Records -> Measurements; the mixed audit family name is still open."),
    "auditActionSubculture": (D, "GLOSSARY_G18", "JA uses 継代 where glossary G18 says 移植 (open question Q048)."),
    "auditMetaMeasurementId": (D, "RECORD_MEASUREMENT_OPEN", "EN Record ID -> Measurement id; JA 測定記録ID follows Record."),
    "auditValuePendingReview": (D, "EN_EXCEPTION_CONTESTED", "EN To be checked -> Needs review; contested application exception."),
    "adminSuperuserNavigation": (D, "MEANING_CHANGED", "EN Super administrator (person) -> Super administration (area); JA システム管理者 is a person."),
    "adminPaginationLabel": (D, "MEANING_CHANGED", "EN Results per page -> Results pagination; JA is page size."),
    "boxInventoryQualificationAid": (D, "TERMINOLOGY_OPEN", "Qualification versus classification wording remains open."),
    "boxActivateForbidden": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it."),
    "manageErrorMembershipAdminRequired": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it."),
    "manageErrorResponsableRequired": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it."),
    "manageErrorActiveResponsableRequired": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it."),
    "createBoxTitle": (D, "FEATURE_REDESIGN", "Box creation redesign: Create a box -> New box."),
    "createBoxNotes": (D, "FEATURE_REDESIGN", "Box creation redesign: Note -> Note (optional); JA lacks optional."),
    "createBoxSubmit": (D, "FEATURE_REDESIGN", "Box creation redesign: Create -> Create box."),
    "manageDemoteToViewer": (D, "GLOSSARY_G36", "JA 閲覧者 where glossary G36 says 閲覧専用; workbook EN said Promote."),
    "organizationChoiceIntro": (D, "MEANING_CHANGED", "FR rewritten (AC18); open question Q067."),
    "organizationMenuTitle": (D, "GLOSSARY_G32", "FR Institution de travail = G32 所属機関; JA 機関 (open question Q057)."),
    "measurementCountsInvalid": (D, "MEANING_CHANGED", "Now states the integer range; JA asks for a valid number."),
    "movementKindUnclassified": (D, "EN_EXCEPTION_CONTESTED", "EN Origin unknown -> Provenance not established; contested exception."),
    "movementErrorForbidden": (D, "MEANING_CHANGED", "FR/EN no longer include the invitation (shared 401/403); JA still does."),
    "temperatureOutsideScale": (D, "MEANING_CHANGED", "Now an expanded-scale note; JA asks to select a marker."),
    "historicalTemperaturePeriod": (D, "MEANING_CHANGED", "Now Measured for the period:; JA 対象期間 translated Available date range."),
    "overviewEmpty": (D, "MEANING_CHANGED", "FR active -> vivante (protected Living exception); JA says active."),
    "subcultureNoZones": (D, "MEANING_CHANGED", "Now No active thermal zone; JA says no location."),
    "zoneSalinityLatestTitle": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityNoReading": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityCreateAction": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityEditAction": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityCreateTitle": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityEditTitle": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    "zoneSalinityEditExpired": (D, "RECORD_MEASUREMENT", "Salinity is a Measurement (測定値); JA uses 測定記録."),
    # Django gettext: compatible differences.
    "django:gettext:Email :": (R, "PUNCTUATION_ONLY", "Space before colon removed."),
    "django:gettext:Nom :": (R, "PUNCTUATION_ONLY", "Space before colon removed."),
    "django:gettext:Only an active box can be subcultured.": (R, "EN_TYPO_ONLY", "transplant -> transplanted."),
    "django:gettext:Duplicate measurement evidence is read-only.": (R, "JA_MATCHES_CURRENT", "JA already says read-only."),
    "django:gettext:Identifiants invalides.": (R, "JA_MATCHES_CURRENT", "Workbook EN Invalid IDs; JA already says invalid login credentials."),
    "django:gettext:The allocation exceeds the current parent count.": (R, "JA_MATCHES_CURRENT", "JA compares polyp counts, as the service does (T842)."),
    "django:gettext:Lecture seule": (R, "GLOSSARY_MATCH", "Glossary G36 閲覧専用; EN Viewer remains an open EN decision (T840)."),
    "django:gettext:Date du relevé": (R, "GLOSSARY_MATCH", "Glossary G17 Date du relevé = 測定日."),
    "django:gettext:Polypes": (R, "GLOSSARY_MATCH", "FR unchanged, EN untranslated; glossary G3 ポリプ."),
    "django:gettext:Éphyrules": (R, "GLOSSARY_MATCH", "FR unchanged, EN untranslated; glossary G4 エフィラ."),
    "django:gettext:Strobiles": (R, "GLOSSARY_MATCH", "FR unchanged, EN untranslated; glossary G5 ストロビラ."),
    "django:gettext:Commentaire": (R, "FR_UNCHANGED_EN_UNTRANSLATED", "FR unchanged; EN has no approved entry (T106)."),
    "django:gettext:Vigilance": (R, "FR_UNCHANGED_EN_UNTRANSLATED", "FR unchanged; EN has no approved entry (T119)."),
    "django:gettext:État de la culture": (R, "FR_UNCHANGED", "FR unchanged; JA 容器の状態 as validated for auditMetaCultureStatus."),
    "django:gettext:This code is reserved by a historical culture identity.": (R, "EN_UNCHANGED", "EN unchanged; FR has no entry and shows the English msgid."),
    # Django gettext: deferred to English.
    "django:gettext:Emplacement à la qualification": (D, "TERMINOLOGY_OPEN", "Qualification versus classification wording remains open."),
    "django:gettext:Provenance non établie": (D, "EN_EXCEPTION_CONTESTED", "Same open question as movementKindUnclassified."),
    "django:gettext:The culture identity changed. Reload the culture.": (D, "SOURCE_WORDING_UNAPPROVED", "JA translates an unapproved EN (Reload the page)."),
    "django:gettext:The parent box is outside the active organization.": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it."),
    "django:gettext:This account cannot create subculture events.": (D, "PERMISSION_INVITATION_MISSING", "FR/EN gained the administrator invitation; JA lacks it (Q070)."),
    "django:gettext:À vérifier": (D, "GLOSSARY_G28", "Pending box status: glossary G28 確認待ち; JA 要確認 (Q059)."),
}

# Validated JA whose FR/EN still match, but with an unresolved terminology question.
# Product rule: uncertain Japanese is not displayed; English is shown until Takeru
# decides. The validated text and the question stay in the review records.
OPEN_QUESTIONS = {
    "movementErrorProvenance": "Q024: 出発地点は不明 versus provenance not established.",
    "adminTransferText": "Q032: 別の水族館 versus the proposed another institution (AC17 not applied).",
    "auditObjectOrganization": "Q056: 所属機関 here, 機関 on auditMetaOrganization.",
    "boxLifecycleLastKnownLocation": "Q060: 最後に確認された設置場所 may read as verified.",
    "boxInventoryLastKnownLocation": "Q060: 最後に確認された設置場所 may read as verified.",
    "zoneMovementExits": "Q061: 撤去 for the end of a location period.",
    "auditActionTransfer": "Q062: transfer term 移動 shared with Déplacement.",
    "auditMetaMembershipId": "GP-N11: membership identifier labelled as access (アクセス権ID).",
}

# Local presentation tables integrated in components (file, table entry) and inline
# ternaries left on the English fallback.
LOCAL_DECISIONS = {
    "local:frontend/src/components/ErrorBoundary.tsx#copy.text": (R, "EN_PHRASING_ONLY", "FR unchanged; EN reworded without meaning change."),
    "local:frontend/src/components/InteractiveLineageGraph.tsx#labels.truncated": (D, "MEANING_CHANGED", "FR lignée -> famille; JA 系譜 is lineage."),
    "local:frontend/src/boxStatus.ts#statusLabels.pending_review.label": (R, "GLOSSARY_MATCH", "Glossary G28 確認待ち; local EN still Pending review."),
    "local:frontend/src/components/ExportsView.tsx#copy.zones": (R, "SAME_CONCEPT", "Thermal location -> Thermal zones; FR unchanged."),
}
EN_STALE_RECORD = ("EN_NOT_SYNCHRONIZED", "FR relevé unchanged; local EN still says measurement; JA 測定記録 matches FR and workbook EN record.")
for _name in ("previewEmpty", "previewEmptyTitle", "previewError", "previewLoading",
              "previewMeasurements", "previewReading", "previewTitle", "zoneScopeLabel", "zoneScopeHelp"):
    LOCAL_DECISIONS[f"local:frontend/src/components/ExportsView.tsx#copy.{_name}"] = (R, *EN_STALE_RECORD)


def norm(value: str | None) -> str:
    return unicodedata.normalize("NFC", (value or "").strip())


def placeholders(value: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(value or ""))


def read_workbook(path: Path) -> dict[str, dict]:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != WORKBOOK_SHA256:
        raise SystemExit(f"workbook SHA-256 {digest} differs from {WORKBOOK_SHA256}")
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
          "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships"}
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        sst = ["".join(t.text or "" for t in si.iter(f"{{{ns['m']}}}t"))
               for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        rels = {r.get("Id"): r.get("Target") for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))}
        sheets = {s.get("name"): rels[s.get(f"{{{ns['r']}}}id")]
                  for s in ET.fromstring(z.read("xl/workbook.xml")).find("m:sheets", ns)}
        target = sheets[SHEET].lstrip("/")
        root = ET.fromstring(z.read(target if target.startswith("xl/") else f"xl/{target}"))
    rows = []
    for row in root.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for c in row.findall("m:c", ns):
            column = re.match(r"[A-Z]+", c.get("r")).group(0)
            if c.find("m:f", ns) is not None:
                raise SystemExit(f"{SHEET}!{c.get('r')}: formulas are not supported")
            v = c.find("m:v", ns)
            if c.get("t") == "s" and v is not None:
                cells[column] = sst[int(v.text)]
            elif c.get("t") == "inlineStr":
                cells[column] = "".join(x.text or "" for x in c.iter(f"{{{ns['m']}}}t"))
            elif v is not None:
                cells[column] = v.text
        rows.append((int(row.get("r")), cells))
    header = rows[0][1]
    for column, expected in EXPECTED_HEADERS.items():
        if norm(header.get(column)) != expected:
            raise SystemExit(f"unexpected header {column}: {header.get(column)!r}")
    entries, section = {}, ""
    for number, cells in rows[1:]:
        section = cells.get(COLUMNS["section"]) or section
        key = norm(cells.get(COLUMNS["key"]))
        if not key:
            continue
        if key in entries:
            raise SystemExit(f"duplicate workbook key {key!r}")
        entries[key] = {"row": number, "section": norm(section),
                        **{name: cells.get(col) for name, col in COLUMNS.items() if name not in ("section", "key")}}
    return entries


def guard_issues(key: str, cur_fr: str, cur_en: str, ja: str) -> list[str]:
    issues = []
    if re.search(r"repiqu", cur_fr, re.I) and "移植" not in ja:
        issues.append("G18 expects 移植")
    if re.search(r"\bRead-only mode\b", cur_en) and "閲覧専用" not in ja:
        issues.append("G36 expects 閲覧専用")
    if re.search(r"\bManager\b", cur_en) and "責任者" not in ja:
        issues.append("G33 expects 責任者")
    if cur_fr == "À vérifier" and cur_en == "Pending" and "確認待ち" not in ja:
        issues.append("G28 expects 確認待ち")
    if cur_fr == "Institution de travail" and "所属機関" not in ja:
        issues.append("G32 expects 所属機関")
    if ("Please contact your administrator." in cur_en or key in PERMISSION_KEYS) and not JA_CONTACT.search(ja):
        issues.append("permission refusal without the administrator invitation")
    return issues


def classify(identity: str, cur_fr: str, cur_en: str, entry: dict | None, surface: str) -> dict:
    record = {"surface": surface, "identity": identity, "current_fr": cur_fr, "current_en": cur_en,
              "sheet_row": "", "sheet_section": "", "sheet_fr": "", "sheet_en": "", "ja_validated": "",
              "status": "", "reason_code": "", "note": "", "takeru_review": "no"}
    if entry is None:
        record.update(status="FALLBACK_EN", reason_code="ABSENT_FROM_WORKBOOK",
                      note="Current key added or not tracked in the workbook.")
        return record
    ja = norm(entry["ja"])
    record.update(sheet_row=entry["row"], sheet_section=entry["section"], sheet_fr=norm(entry["fr"]),
                  sheet_en=norm(entry["en"]), ja_validated=ja)
    if norm(entry["ja_status"]) != "Fait" or not ja:
        record.update(status="FALLBACK_EN", reason_code="JA_NOT_VALIDATED", note="No validated JA.")
        return record
    differences = []
    if norm(cur_en) != record["sheet_en"]:
        differences.append("EN")
    if norm(cur_fr) != record["sheet_fr"]:
        differences.append("FR")
    issues = guard_issues(identity, cur_fr, cur_en, ja)
    if placeholders(ja) != placeholders(cur_en) or placeholders(ja) != placeholders(cur_fr):
        issues.append(f"placeholders {placeholders(ja)} vs {placeholders(cur_en)}")
    if "·" in ja or ja != ja.strip():
        issues.append("forbidden middle dot or edge whitespace")
    decisions = LOCAL_DECISIONS if surface in ("local", "inline") else SEMANTIC_DECISIONS
    decision = decisions.get(identity)
    if differences or issues:
        if decision is None:
            raise SystemExit(f"{identity}: needs a reviewed decision ({differences}, {issues})")
        verdict, code, note = decision
        if verdict == R and issues:
            raise SystemExit(f"{identity}: REUSE decision contradicts guards {issues}")
        record.update(reason_code=code, note=note + (f" Guards: {'; '.join(issues)}." if issues else ""))
        if verdict == R:
            record["status"] = "REUSED"
        else:
            record.update(status="FALLBACK_EN_REVIEW", takeru_review="yes")
        return record
    if decision is not None:
        raise SystemExit(f"{identity}: stale reviewed decision; FR/EN now match the workbook")
    if norm(entry["en_status"]) != "Fait":
        record.update(status="FALLBACK_EN_REVIEW", reason_code="EN_NOT_VALIDATED",
                      note="Workbook EN source not validated.", takeru_review="yes")
        return record
    if identity in OPEN_QUESTIONS:
        record.update(status="FALLBACK_EN_REVIEW", reason_code="OPEN_QUESTION", note=OPEN_QUESTIONS[identity],
                      takeru_review="yes")
        return record
    record.update(status="REUSED", reason_code="EXACT_MATCH")
    return record


def local_value(identity: str, language: str) -> str | None:
    """Read one string of a local FR/EN presentation table from the current source."""
    location, _, member = identity.split(":", 1)[1].partition("#")
    table, prop = member.split(".")[:2]
    source = decode_js_escapes((ROOT / location).read_text(encoding="utf-8"))
    start = re.search(rf"\bconst {re.escape(table)}\b[^=]*=\s*\{{", source)
    if start is None:
        return None
    block = re.search(rf"\n  {language}: \{{\n(.*?)\n  \}},", source[start.end():], re.S)
    if block is None:
        return None
    string = r"""(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
    match = re.search(rf"\b{re.escape(prop)}: (?:\{{ label: )?{string}", block.group(1))
    if match is None:
        return None
    return re.sub(r"\\(.)", r"\1", match.group(1) if match.group(1) is not None else match.group(2))


def decode_js_escapes(text: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), text)


def inline_record(identity: str, entry: dict) -> dict:
    """Inline FR/EN ternaries are not wired to Japanese; English is shown."""
    source = decode_js_escapes((ROOT / identity.split(":", 1)[1].split("#")[0].split(":")[0]).read_text(encoding="utf-8"))
    sheet_fr, sheet_en = norm(entry["fr"]), norm(entry["en"])
    unchanged = sheet_fr in source and sheet_en in source
    return {"surface": "inline", "identity": identity,
            "current_fr": sheet_fr if sheet_fr in source else "(differs in source)",
            "current_en": sheet_en if sheet_en in source else "(differs in source)",
            "sheet_row": entry["row"], "sheet_section": entry["section"], "sheet_fr": sheet_fr,
            "sheet_en": sheet_en, "ja_validated": norm(entry["ja"]), "status": "FALLBACK_EN_INLINE",
            "reason_code": "INLINE_NOT_INTEGRATED",
            "note": ("FR/EN found unchanged" if unchanged else "FR/EN wording differs in source")
            + "; inline FR/EN ternary, English shown; integrating needs a separate refactor.",
            "takeru_review": "no"}


def build(workbook: Path) -> dict[Path, str]:
    entries = read_workbook(workbook)
    fr = check_register.read_catalog(ROOT / "frontend/src/i18n/fr.ts")
    en = check_register.read_catalog(ROOT / "frontend/src/i18n/en.ts")
    if set(fr) != set(en):
        raise SystemExit("FR/EN catalogs are not equivalent")
    po = {lang: check_register.read_po(ROOT / f"backend/locale/{lang}/LC_MESSAGES/django.po") for lang in ("fr", "en")}
    sources = check_i18n.discover_sources(ROOT)

    records = [classify(key, fr[key], en[key], entries.get(key), "react") for key in fr]
    for msgid in sorted(sources):
        records.append(classify(f"django:gettext:{msgid}", po["fr"].get(msgid) or msgid,
                                po["en"].get(msgid) or msgid, entries.get(f"django:gettext:{msgid}"), "django"))
    for identity, entry in entries.items():
        if identity.startswith("inline:"):
            records.append(inline_record(identity, entry))
        elif identity.startswith("local:"):
            cur_fr = local_value(identity, "fr")
            cur_en = local_value(identity, "en")
            if cur_fr is None or cur_en is None:
                raise SystemExit(f"{identity}: local table entry not found in source")
            records.append(classify(identity, cur_fr, cur_en, entry, "local"))

    react_keys, django_keys = set(fr), {f"django:gettext:{m}" for m in sources}
    obsolete = [{"surface": "django" if k.startswith("django:") else "react", "identity": k,
                 "sheet_row": e["row"], "sheet_section": e["section"]}
                for k, e in entries.items()
                if not k.startswith(("local:", "inline:")) and k not in react_keys and k not in django_keys]

    review = [{name: r[name] for name in ("identity", "status", "reason_code", "note", "current_fr", "current_en",
                                          "sheet_row", "sheet_fr", "sheet_en", "ja_validated")}
              for r in records if r["takeru_review"] == "yes"]
    outputs = {MANIFEST: to_csv(records), OBSOLETE: to_csv(obsolete), TAKERU: to_csv(review),
               JA_TS: render_ts(records, fr),
               JA_PO: render_po(records)}
    return outputs


def to_csv(records: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(records[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(records)
    return buffer.getvalue()


def js_string(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def render_ts(records: list[dict], order: dict[str, str]) -> str:
    reused = {r["identity"]: r["ja_validated"] for r in records if r["surface"] == "react" and r["status"] == "REUSED"}
    lines = [
        "import type { TranslationKey } from './fr';",
        "",
        "// Japanese validated by Takeru in the frozen translation workbook",
        "// (SHA-256 e80b409f...), reused only where current FR/EN still match it.",
        "// Partial by design: a missing key falls back to English at runtime.",
        "// Generated by docs/translation-review/japanese/build_ja_catalogs.py; never add",
        "// English or unreviewed Japanese here. See ja-manifest.csv for every key.",
        "export const ja: Partial<Record<TranslationKey, string>> = {",
    ]
    lines += [f"  {key}: {js_string(reused[key])}," for key in order if key in reused]
    lines.append("};")
    return "\n".join(lines) + "\n"


def po_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def render_po(records: list[dict]) -> str:
    header = [
        'msgid ""', 'msgstr ""',
        '"Project-Id-Version: Polypbase\\n"',
        '"Language: ja\\n"',
        '"MIME-Version: 1.0\\n"',
        '"Content-Type: text/plain; charset=UTF-8\\n"',
        '"Content-Transfer-Encoding: 8bit\\n"',
        "",
        "# Japanese validated by Takeru (frozen workbook), active msgids only.",
        "# The MO adds approved English or the source msgid for missing JA entries.",
        "# This PO keeps only validated Japanese; compile with compile_django_messages.py.",
        "# Generated by docs/translation-review/japanese/build_ja_catalogs.py.",
        "",
    ]
    body = []
    for r in records:
        if r["surface"] == "django" and r["status"] == "REUSED":
            msgid = r["identity"].removeprefix("django:gettext:")
            body += [f"msgid {po_string(msgid)}", f"msgstr {po_string(r['ja_validated'])}", ""]
    return "\n".join(header + body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("workbook", type=Path)
    parser.add_argument("--check", action="store_true", help="fail if any generated file differs; write nothing")
    args = parser.parse_args(argv)
    outputs = build(args.workbook)
    changed = [path for path, text in outputs.items()
               if not path.exists() or path.read_text(encoding="utf-8") != text]
    if args.check:
        for path in changed:
            print(f"out of date: {path.relative_to(ROOT).as_posix()}")
        return int(bool(changed))
    for path, text in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
    print("written:", ", ".join(p.relative_to(ROOT).as_posix() for p in outputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
