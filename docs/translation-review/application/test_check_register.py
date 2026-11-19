"""Regression tests for check_register.py, on synthetic histories and on the real register.

Run from the repository root:
    python -m unittest discover -s docs/translation-review/application -p "test_*.py"
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_register as reg  # noqa: E402

HEAD_1 = "1" * 40
HEAD_2 = "2" * 40
SHEET = "a" * 64
B01 = "B01-2026-10-28"
B02 = "B02-2026-11-05"
B03 = "B03-2026-11-06"
REAL_B02 = "B02-2026-10-29"
REAL_B03 = "B03-2026-10-29"
# Canonical digest of the 34 B01 rows (parsed fields, independent of line endings and of later batches).
B01_ROWS_SHA256 = "938718e0b044503fd19a48bef9c013ccbd2de4653277f05b9b54aba078c434be"
# Same canonical digest for the 86 recorded B02 rows (T035-T120).
B02_ROWS_SHA256 = "db74004c80933dfc216dd9e4044f277cb4b5bfc892dbbc5b46c454720893ad0e"
# Same canonical digest for the 320 recorded B03 rows (T121-T440).
B03_ROWS_SHA256 = "d31e74d3ccfec4bf6eb1e355e6b70ae8e3d41eac39207932c508b13760a4df67"
# Final B03 cleanup batches B04-B06 (T441-T558) retain their original history.
B03_FINAL_ROWS_SHA256 = "fb799fbdc052eba3071392c492efd00a1619141d9232a7eaaef890e2e8f2318a"
# The independent probe batch follows B06; only its batch and entry IDs were renumbered.
PROBE_ROWS_SHA256 = "6dbf823b88b5fefdeaec8a1449b9b5df33a7e8d98098e86ecca90845c063846d"
# Thermal-control batch B13 (T845-T858), computed from the original rows of commit
# cbc513f (where B13 first appears). Corrections must be appended as new events.
B13 = "B13-2026-11-04"
B13_POSITION = slice(844, 858)
B13_ENTRY_IDS = [f"T{number}" for number in range(845, 859)]
B13_ROWS_SHA256 = "ab96099e1e94d0b1c17ddf7547605960a6c46d6c3c6e44e2184d6c6cae49452b"
# Independent lab events from 5fcc2ea share B13 and its baseline, not its thermal freeze.
LAB_POSITION = slice(858, 886)
LAB_ENTRY_IDS = [f"T{number}" for number in range(859, 887)]
LAB_ROWS_SHA256 = "12474e0f6376a9e3da896e68022f45a954401cce630aff263385c4fc06e5365b"
# Canonical digests of all fields from commit 6d9951f39c34c516fc182f4e2e4f28b7e1d8caaa.
# These corrections may be superseded only by appended events, never rewritten.
MOVEMENT_CORRECTION_FREEZES = (
    ("B18-2026-11-06", slice(1006, 1008), ["T1007", "T1008"],
     "6b21145767adf20561f5168c1d193f99b21c6788a793cf606af96aa5fe3a7ecf"),
    ("B19-2026-11-06", slice(1008, 1010), ["T1009", "T1010"],
     "b816cfc5a006286a5f8e65b829a1464d9d974d5e0f31d5280f1c26b4c724dd9c"),
)


def rows_digest(rows):
    canonical = json.dumps([[row[name] for name in reg.COLUMNS] for row in rows], ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def b13_integrity_errors(rows):
    """Errors if the original B13 events moved, changed, vanished or gained a member."""
    b13 = [row for row in rows if row["batch"] == B13 and row["entry_id"] not in LAB_ENTRY_IDS]
    errors = []
    if rows[B13_POSITION] != b13:
        errors.append("B13 rows are not exactly the contiguous register rows 845-858")
    if [row["entry_id"] for row in b13] != B13_ENTRY_IDS:
        errors.append("B13 entry IDs are not exactly T845-T858 in order")
    if rows_digest(b13) != B13_ROWS_SHA256:
        errors.append("B13 history was modified")
    return errors


def lab_integrity_errors(rows):
    """Preserve all lab event fields, including deferred decisions and their reasons."""
    lab = [row for row in rows if row["entry_id"] in LAB_ENTRY_IDS]
    errors = []
    if rows[LAB_POSITION] != lab or [row["entry_id"] for row in lab] != LAB_ENTRY_IDS:
        errors.append("Lab events are not exactly the contiguous register rows 859-886")
    if rows_digest(lab) != LAB_ROWS_SHA256:
        errors.append("Lab history was modified")
    return errors


def movement_correction_integrity_errors(rows):
    """Freeze B18/B19 independently of current catalogs and later corrections."""
    errors = []
    for batch, position, entry_ids, digest in MOVEMENT_CORRECTION_FREEZES:
        members = [row for row in rows if row["batch"] == batch or row["entry_id"] in entry_ids]
        if rows[position] != members:
            errors.append(f"{batch}: original events moved or gained a member")
        if [row["entry_id"] for row in members] != entry_ids:
            errors.append(f"{batch}: original event identities or order changed")
        if any(row["batch"] != batch for row in members):
            errors.append(f"{batch}: original batch membership changed")
        if rows_digest(members) != digest:
            errors.append(f"{batch}: history was modified")
    return errors


def b24_events(rows):
    """Validate the exact additive B24 events before excluding them from frozen sources."""
    events = [row for row in rows if row["batch"] == "B24-2026-11-10"]
    assert events == rows[1054:1060], "B24: events moved or gained a member"
    assert [row["entry_id"] for row in events] == [f"T{i}" for i in range(1055, 1061)], "B24: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B24: schema changed"
    assert rows_digest(events) == "0f7e33f3574c1c177669ecadf5a708b66b2c2a789b0c53c72f71eced5819864c", "B24: history was modified"
    return events


def before_b24(source, rows, language="EN"):
    """Reverse only the three exact B24 additions; unknown changes stay fingerprinted."""
    for row in b24_events(rows):
        if row["language"] == language:
            line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
            assert source.count(line) == 1, row["key_or_msgid"]
            source = source.replace(line, "")
    return source


# B26: compact probe chart period control keys, English fallbacks without Japanese.
B26 = "B26-2026-11-10"
B26_KEYS = ("probePeriodLast7Days", "probePeriodLast30Days", "probePeriodCustom", "probeTimeZone")
# B27: inter-probe average curve keys, English fallbacks without Japanese.
B27 = "B27-2026-11-11"
B27_KEYS = ("probeAverage", "probeAverageContributors")


def b26_events(rows):
    """Validate the exact additive B26 events before excluding them from frozen sources."""
    events = [row for row in rows if row["batch"] == B26]
    assert events == rows[1086:1094], "B26: events moved or gained a member"
    assert [row["entry_id"] for row in events] == [f"T{i}" for i in range(1087, 1095)], "B26: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B26: schema changed"
    assert rows_digest(events) == "bb061a0b21466b02a2db8ab09453d98c3571c607c7236d28678f0aaa68080955", "B26: history was modified"
    assert [(row["key_or_msgid"], row["language"]) for row in events] == [
        (key, language) for key in B26_KEYS for language in ("FR", "EN")
    ], "B26: keys changed"
    return events


def before_b26(source, rows, language="EN"):
    """Reverse only the four exact B26 additions; unknown changes stay fingerprinted."""
    for row in b26_events(rows):
        if row["language"] == language:
            line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
            assert source.count(line) == 1, row["key_or_msgid"]
            source = source.replace(line, "")
    return source


def b27_events(rows):
    """Validate the exact additive B27 events before excluding them from frozen sources."""
    events = [row for row in rows if row["batch"] == B27]
    assert events == rows[1094:1098], "B27: events moved or gained a member"
    assert [row["entry_id"] for row in events] == [f"T{i}" for i in range(1095, 1099)], "B27: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B27: schema changed"
    assert rows_digest(events) == "988d1d95a042150c7c58f1e55e88c687e864b20b41623af9e69e50ebcea7a8ef", "B27: history was modified"
    assert [(row["key_or_msgid"], row["language"]) for row in events] == [
        (key, language) for key in B27_KEYS for language in ("FR", "EN")
    ], "B27: keys changed"
    return events


def before_b27(source, rows, language="EN"):
    """Reverse only the two exact B27 additions; unknown changes stay fingerprinted."""
    for row in b27_events(rows):
        if row["language"] == language:
            line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
            assert source.count(line) == 1, row["key_or_msgid"]
            source = source.replace(line, "")
    return source


# Published B29 introduction: ae07ef79ad39def03a0eca7541aa69c674e5b522.
# Its parent 84e7f1fb3526d99dd8b9200f0146f8cdbbc623b5 contains exactly T001-T1100.
# Independently computed from those Git blobs, not from the current catalogs.
# B29 records its original development baseline (3802355), not the integration parent.
PRE_B29_ROWS_SHA256 = "98a1755315c89ac3456df918393269fa9f5b7d8522bc6366d9b55932a5bf7f10"
B29 = "B29-2026-11-12"
B29_ROWS_SHA256 = "f1532753960713d43fce130483a7685a8fd4b6da604cb9e5881c6dc9f2e8bf7f"
PRE_B29_CATALOG_SHA256 = {
    "EN": "0b961092c2568d78e8ed570fe503864587be4a7eb838d4300884f81d4392af5b",
    "FR": "25e28173c88539c10a3f37c6c4293c63918a646d72d199cf9b7192ad236fc5dc",
}


def b29_events(rows):
    """Validate every published B29 field before trusting its catalog reversals."""
    entry_ids = [f"T{i}" for i in range(1101, 1402)]
    protected_ids = set(entry_ids)
    events = [row for row in rows if row["batch"] == B29 or row["entry_id"] in protected_ids]
    assert events == rows[1100:1401], "B29: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B29: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B29: schema changed"
    assert rows_digest(events) == B29_ROWS_SHA256, "B29: history was modified"
    return events


# Donation integration: feature T1402-T1471/B30-B31 becomes T1742-T1811/B44-B45.
# Project only IDs and batches back before checking the original feature digests.
def donation_original_projection(rows):
    return [dict(row, entry_id=f"T{int(row['entry_id'][1:]) - 340}",
                 batch=f"B{int(row['batch'][1:3]) - 14}{row['batch'][3:]}")
            for row in rows]


B44 = "B44-2026-11-18"
B44_ROWS_SHA256 = "cd8e6091a166c53d3a746e0f129d2ef2fc413a468892b854a60a15db80fe6469"
B44_ADDED_KEYS = [
    "donationIncoming", "donationSend", "donationRefresh", "donationDestination", "donationSource",
    "donationType", "donationTypeDon", "donationDate", "donationState", "donationPending",
    "donationCancelled", "donationCancelledAt", "donationAllTypes", "donationAllStates", "donationSearch",
    "donationDetails", "donationReference", "donationEmpty", "donationNoMatch", "donationForbidden",
    "donationNotFound", "donationReadError", "donationSendUnavailable", "donationAcceptanceUnavailable",
]


def b44_events(rows):
    """Freeze the same complete B44 events as the Japanese provenance tests."""
    entry_ids = [f"T{i}" for i in range(1742, 1790)]
    protected_ids = set(entry_ids)
    events = [row for row in rows if row["batch"] == B44 or row["entry_id"] in protected_ids]
    assert events == rows[1741:1789], "B44: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B44: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B44: schema changed"
    assert [(row["key_or_msgid"], row["language"]) for row in events] == [
        (key, language) for key in B44_ADDED_KEYS for language in ("FR", "EN")
    ], "B44: key or language order changed"
    assert all((row["surface"], row["source_file"], row["status"], row["initial_value"]) == (
        "react", f"frontend/src/i18n/{row['language'].lower()}.ts", "ALREADY_MATCHING", row["final_value"],
    ) for row in events), "B44: additive event changed"
    assert rows_digest(donation_original_projection(events)) == B44_ROWS_SHA256, "B44: history was modified"
    return events


B45 = "B45-2026-11-18"
B45_ROWS_SHA256 = "ce4c9f91ebdd92c896516483d152efde1959fcd727dd1ad902bc2cf1b83193fc"
B45_ADDED_KEYS = [
    "exchangePreviewTitle", "exchangePreviewNotice", "exchangePreviewType", "exchangePreviewBox",
    "exchangePreviewInstitution", "exchangePreviewBoxPlaceholder", "exchangePreviewInstitutionPlaceholder",
    "exchangePreviewLive", "exchangePreviewFrozen", "exchangePreviewEmail",
]


def b45_events(rows):
    """Freeze all B45 fields independently of the current catalogs."""
    entry_ids = [f"T{i}" for i in range(1790, 1812)]
    events = [row for row in rows if row["batch"] == B45 or row["entry_id"] in entry_ids]
    assert events == rows[1789:1811], "B45: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B45: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B45: schema changed"
    assert [(row["key_or_msgid"], row["language"]) for row in events] == [
        (key, language) for key in ["donationSendUnavailable"] + B45_ADDED_KEYS for language in ("FR", "EN")
    ], "B45: key or language order changed"
    assert rows_digest(donation_original_projection(events)) == B45_ROWS_SHA256, "B45: history was modified"
    return events


def before_b45(source, rows, language="EN"):
    """Undo exact B45 additions and restore the retired warning in its original position."""
    events = [row for row in b45_events(rows) if row["language"] == language]
    for row in events:
        if row["status"] != "ALREADY_MATCHING":
            continue
        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
        assert source.count(line) == 1, row["entry_id"]
        source = source.replace(line, "")
    removed = next(row for row in events if row["status"] == "REMOVED")
    assert not any(line.startswith(f"  {removed['key_or_msgid']}: ") for line in source.splitlines())
    anchor = "  donationAcceptanceUnavailable: "
    assert source.count(anchor) == 1
    return source.replace(anchor, f"  {removed['key_or_msgid']}: '{removed['initial_value']}',\n" + anchor)


def before_b44(source, rows, language="EN"):
    """Remove only exact, frozen B44 additions; leave unregistered text visible."""
    for row in b44_events(rows):
        if row["language"] != language:
            continue
        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
        assert source.count(line) == 1, row["entry_id"]
        source = source.replace(line, "")
    return source


B47 = "B47-2026-11-18"
# Canonical digest of the two B47 register rows in commit 6a0e9d9.
B47_ROWS_SHA256 = "9418ec36675c5cc50ce70402bd2c951712549a6f827fdc1d142f1d413018dd81"


def b47_events(rows):
    entry_ids = ("T1813", "T1814")
    events = [row for row in rows if row["batch"] == B47 or row["entry_id"] in entry_ids]
    assert events == rows[1812:1814], "B47: events moved or gained a member"
    assert [row["entry_id"] for row in events] == list(entry_ids), "B47: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B47: schema changed"
    assert rows_digest(events) == B47_ROWS_SHA256, "B47: history was modified"
    return events


def before_b47(source, rows, language="EN"):
    """Remove only the exact registered demo banner, preserving all other bytes."""
    row = next(row for row in b47_events(rows) if row["language"] == language)
    assert len(re.findall(r'''^[ \t]*(?:demoModeBanner|"demoModeBanner"|'demoModeBanner')[ \t]*:''',
                          source, re.MULTILINE)) == 1, "B47: expected exactly one demoModeBanner entry"
    line = f"\n  demoModeBanner: '{row['final_value']}',\n"
    assert source.count(line) == 1, f"{row['entry_id']}: expected exact registered line"
    return source.replace(line, "\n", 1)


def before_b29(source, rows, language="EN"):
    """Undo frozen B47, B45 and B44 events, then B29 additions and changes."""
    events = b29_events(rows)
    source = before_b47(source, rows, language)
    source = before_b44(before_b45(source, rows, language), rows, language)
    for row in reversed(events):
        if row["language"] != language:
            continue
        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
        assert source.count(line) == 1, row["entry_id"]
        previous = (f"  {row['key_or_msgid']}: '{row['initial_value']}',\n"
                    if row["status"] == "APPLIED" else "")
        source = source.replace(line, previous)
    return source


# Published B30/B31 append-only DNA additions, committed at 630302f.
# Independently computed from that Git blob, not from the current working tree.
# B30 adds the DNA badge copy; B31 records the removal of dnaStateValidated in FR and EN.
B30 = "B30-2026-11-16"
B31 = "B31-2026-11-16"
B30_ROWS_SHA256 = "076b5eb663c1824c81bab89c3af034ca6a65286f6982b4206161aa370cfe2e6f"
B31_ROWS_SHA256 = "c702ed9c9f5450b11066edef261c4ea045be866bbd0ff6aabb01c973e3af1a57"
# Published B32 append-only four-hour monitoring labels, added in this local change.
# B32 adds five new React keys in FR and EN; no existing value or Japanese changes.
B32 = "B32-2026-11-17"
B32_ROWS_SHA256 = "6d7a1eaf48f628124aba324eea02ed971a4f4bd0063875624592398fc83a2243"


def b30_events(rows):
    """Validate every published B30 field before trusting its catalog reversals."""
    entry_ids = [f"T{i}" for i in range(1402, 1458)]
    protected_ids = set(entry_ids)
    events = [row for row in rows if row["batch"] == B30 or row["entry_id"] in protected_ids]
    assert events == rows[1401:1457], "B30: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B30: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B30: schema changed"
    assert rows_digest(events) == B30_ROWS_SHA256, "B30: history was modified"
    return events


def b31_events(rows):
    """Validate the published B31 removals before trusting their catalog reversals."""
    entry_ids = ["T1458", "T1459"]
    protected_ids = set(entry_ids)
    events = [row for row in rows if row["batch"] == B31 or row["entry_id"] in protected_ids]
    assert events == rows[1457:1459], "B31: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B31: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B31: schema changed"
    assert rows_digest(events) == B31_ROWS_SHA256, "B31: history was modified"
    assert {row["status"] for row in events} == {"REMOVED"}, "B31: only removals are expected"
    return events


def b32_events(rows):
    """Validate the published B32 four-hour monitoring labels before trusting their reversals."""
    entry_ids = [f"T{i}" for i in range(1460, 1470)]
    protected_ids = set(entry_ids)
    events = [row for row in rows if row["batch"] == B32 or row["entry_id"] in protected_ids]
    assert events == rows[1459:1469], "B32: events moved or gained a member"
    assert [row["entry_id"] for row in events] == entry_ids, "B32: identities changed"
    assert all(list(row) == reg.COLUMNS for row in events), "B32: schema changed"
    assert rows_digest(events) == B32_ROWS_SHA256, "B32: history was modified"
    assert {row["status"] for row in events} == {"ALREADY_MATCHING"}, "B32: only additions are expected"
    return events


def before_b32(source, rows, language="EN"):
    """Undo only the frozen B32 additions, in reverse event order."""
    for row in reversed(b32_events(rows)):
        if row["surface"] != "react" or row["language"] != language:
            continue
        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
        assert source.count(line) == 1, row["entry_id"]
        source = source.replace(line, "")
    return source


def before_donation_integration_layout(source, rows, language="EN"):
    """Undo only the three separators added by merge 90a1065, not arbitrary whitespace."""
    dna = {row["key_or_msgid"]: row for row in b30_events(rows) if row["language"] == language}
    donation = {row["key_or_msgid"]: row for row in b44_events(rows) if row["language"] == language}
    header = ("export const fr = {\n" if language == "FR"
              else "export const en: Record<TranslationKey, string> = {\n")
    first = f"  dnaMissing: '{dna['dnaMissing']['final_value']}',\n"
    last = f"  dnaAuditRevoked: '{dna['dnaAuditRevoked']['final_value']}',\n"
    warning = f"  donationAcceptanceUnavailable: '{donation['donationAcceptanceUnavailable']['final_value']}',\n"
    for current, previous in ((header + "\n" + first, header + first),
                              (last + "\n", last), (warning + "\n", warning)):
        assert source.count(current) == 1, "Donation integration separator changed"
        source = source.replace(current, previous)
    return source


def before_b30(source, rows, language="EN"):
    """Undo only the frozen B30 additions, honoring the B31 removals.

    A key removed by B31 must already be absent from the catalog: an absent
    removed key is a recorded removal, not an unrecorded missing translation.
    """
    source = before_donation_integration_layout(source, rows, language)
    source = before_species_integration(source, rows, language)
    removed = {(row["key_or_msgid"], row["language"]) for row in b31_events(rows)}
    for row in reversed(b30_events(rows)):
        if row["surface"] != "react" or row["language"] != language:
            continue
        if (row["key_or_msgid"], row["language"]) in removed:
            assert not any(line.startswith(f"  {row['key_or_msgid']}: ")
                           for line in source.splitlines()), row["entry_id"]
            continue
        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
        assert source.count(line) == 1, row["entry_id"]
        source = source.replace(line, "")
    return source


def before_b23(source, rows, language="EN"):
    """Reverse only the exact B23 events before checking older source fingerprints."""
    events = [row for row in rows if row["batch"] == "B23-2026-11-10"]
    assert events == rows[1042:1054], "B23: events moved or gained a member"
    assert [row["entry_id"] for row in events] == [f"T{i}" for i in range(1043, 1055)], "B23: identities changed"
    assert rows_digest(events) == "e923b831b624a2367b92bcfd9ba3b55eec575f1ec9241333d632920dc14b8423", "B23: history was modified"
    entries = {row["key_or_msgid"]: row for row in events if row["language"] == language}
    for key, row in entries.items():
        if row["status"] == "REMOVED":
            assert not any(line.startswith(f"  {key}: ") for line in source.splitlines()), key
        else:
            line = f"  {key}: '{row['final_value']}',\n"
            assert source.count(line) == 1, key
            source = source.replace(line, "")
    for keys, anchor in (
        (("historyEnvironmentOnly", "historyTransitionReview"), "historyReconstructionUnresolved"),
        (("historyProvenance", "historyReconciliation"), "historyLoading"),
    ):
        prefix = f"  {anchor}: "
        assert source.count(prefix) == 1, anchor
        restored = "".join(f"  {key}: '{entries[key]['initial_value']}',\n" for key in keys)
        source = source.replace(prefix, restored + prefix)
    return source


# Independently retained source blobs and the ID/batch-only integration projection.
INTEGRATION_B25 = "B25-2026-11-10"
MOVEMENT_ROWS_SHA256 = "092d2c92d7dbdb16b5f4eccb9687636eca4b567d5f64dd4d57d92c53f1032de1"
THERMAL_SOURCE_ROWS_SHA256 = "05c86c2949ba886fc8c5529163725bb6be21750bf53209c3c10093009cde2eb6"
THERMAL_PROJECTED_ROWS_SHA256 = "ee979768b84b2dd0900773f26ecb25095dd916be7983c61d78f3f440658ea53e"
MAIN_HISTORY_SHA256 = "50fa1116b8dc480c4a900e5ba17eedf865037c88ac0cbb4239c9d082082e0430"
MAIN_BYTES_SHA256 = "d04b4502a3d873a4e6d17da790a971c3e7fc58b2b80e3a1a35b39fce1376119e"
THERMAL_FINAL_ROWS_SHA256 = "0d5243d50bebfc735a5627c768e4973d426b97843038b0e71ca80c98ab92e241"
MOVEMENT_HISTORY_SHA256 = "e5e5f9f6eacaafd47b083dc743e22a8e572121010f9cf123d88a4d71981f1df5"
INTEGRATION_SOURCES = (
    ("base", 1010, "88b17986a4bc5d69ea3c23c3d5dc9b3b5162dd9c40c0b94979d2dd05e7319eda",
     "1c2035ad35072d2df221a4543494701ea82acc7a4645a07d50648cf764c7798d"),
    ("movements", 1036, "19ccea2e745b600b75d0121819dbab11e9b4a7323881c775876ed8312337fc48",
     MOVEMENT_HISTORY_SHA256),
    ("thermal", 1036, "309be58d9c02c101a7db17723cbe1a7f7f18daa131bbd96bd64204d375b822d6",
     "5088da5700fe1b4727898b03ed3b07f2cd13d21dfa8cbdfbc29f2547beafd0c7"),
)


def integration_evidence(name):
    return Path(__file__).with_name(f"integration-2026-11-09-{name}.csv")


def integration_integrity_errors(rows):
    """Freeze complete HEAD history and the exact thermal projection, not today's catalogs."""
    errors = []
    if rows_digest(rows[:1100]) != PRE_B29_ROWS_SHA256:
        errors.append("Pre-B29 history was modified")
    try:
        b29_events(rows)
        b30_events(rows)
        b31_events(rows)
        b32_events(rows)
    except AssertionError as error:
        errors.append(str(error))
    if rows_digest(rows[:1060]) != MAIN_HISTORY_SHA256:
        errors.append("Complete movement HEAD history was modified")
    if rows_digest(rows[1060:1086]) != THERMAL_FINAL_ROWS_SHA256:
        errors.append("Thermal B25 projection was modified")
    protected_batches = {"B20-2026-11-09", "B21-2026-11-09", "B22-2026-11-10",
                         "B23-2026-11-10", "B24-2026-11-10", INTEGRATION_B25}
    if any(row["batch"] in protected_batches or int(row["entry_id"][1:]) <= 1086
           for row in rows[1086:]):
        errors.append("Integrated history gained a member")
    return errors


SPECIES_EVIDENCE_PATH = Path(__file__).with_name("species-strains-integration.json")
SPECIES_EVIDENCE_SHA256 = "1323838b5e1ac8cfa94a0f42f5e9cf40417b3a85f2a22be7b55409404b434985"
_evidence_bytes = SPECIES_EVIDENCE_PATH.read_bytes().replace(b"\r\n", b"\n")
assert hashlib.sha256(_evidence_bytes).hexdigest() == SPECIES_EVIDENCE_SHA256
SPECIES_EVIDENCE = json.loads(_evidence_bytes)


SPECIES_TAIL = slice(1469, 1741)


def species_original_projection(rows):
    """Map reallocated feature events back to their original T1460-T1731/B32-B42 identities."""
    offsets = SPECIES_EVIDENCE["reallocation"]
    return [dict(row, entry_id=f"T{int(row['entry_id'][1:]) - offsets['entry_id_offset']}",
                 batch=f"B{int(row['batch'][1:3]) - offsets['batch_number_offset']}{row['batch'][3:]}")
            for row in rows]


def species_integration_errors(rows):
    """Freeze all main events (including thermal B32) and all fields of the reallocated feature tail."""
    errors = []
    if rows_digest(rows[:1459]) != SPECIES_EVIDENCE["main_register_rows_sha256"]:
        errors.append("Species integration: complete main history was modified")
    if rows_digest(rows[:1469]) != SPECIES_EVIDENCE["thermal_main_register_rows_sha256"]:
        errors.append("Species integration: main thermal B32 history was modified")
    tail = rows[SPECIES_TAIL]
    if rows_digest(tail) != SPECIES_EVIDENCE["reallocated_register_rows_sha256"]:
        errors.append("Species integration: appended events were modified")
    try:
        original = species_original_projection(tail)
    except (KeyError, ValueError):
        original = []
    if rows_digest(original) != SPECIES_EVIDENCE["integrated_register_rows_sha256"]:
        errors.append("Species integration: reallocation changed more than entry_id and batch")
    protected_batches = {f"B{n}-2026-11-17" for n in range(32, 44)}
    if any(row["batch"] in protected_batches or int(row["entry_id"][1:]) <= 1741 for row in rows[1741:]):
        errors.append("Species integration: frozen history gained a member")
    return errors


def before_species_integration(source, rows, language="EN"):
    """Reverse exact feature lines only; unknown edits remain fingerprinted."""
    assert rows_digest(rows[SPECIES_TAIL]) == SPECIES_EVIDENCE["reallocated_register_rows_sha256"], "Species integration history was modified"
    language = language.lower()
    for line in SPECIES_EVIDENCE["catalog_additions"][language]:
        assert source.count(line) == 1, line
        source = source.replace(line, "")
    for change in SPECIES_EVIDENCE["catalog_changes"][language]:
        assert source.count(change["after"]) == 1, change["key"]
        source = source.replace(change["after"], change["before"])
    return source


class IntegrationHistoryTests(unittest.TestCase):
    def test_original_source_evidence_is_complete_and_independently_frozen(self):
        sources = {}
        for name, count, byte_digest, field_digest in INTEGRATION_SOURCES:
            with self.subTest(source=name):
                path = integration_evidence(name)
                # Git blobs use LF; Windows checkouts may use CRLF.
                blob = path.read_bytes().replace(b"\r\n", b"\n")
                self.assertEqual(hashlib.sha256(blob).hexdigest(), byte_digest)
                sources[name] = reg.load_register(path)
                self.assertEqual(len(sources[name]), count)
                self.assertEqual(rows_digest(sources[name]), field_digest)
        for name in ("movements", "thermal"):
            self.assertEqual(sources[name][:1010], sources["base"])
        self.assertEqual(rows_digest(sources["movements"][1010:]), MOVEMENT_ROWS_SHA256)
        self.assertEqual(rows_digest(sources["thermal"][1010:]), THERMAL_SOURCE_ROWS_SHA256)

    def test_complete_head_bytes_and_fields_are_preserved(self):
        original = integration_evidence("movements")
        current = reg.REGISTER.read_bytes().replace(b"\r\n", b"\n")
        self.assertTrue(current.startswith(original.read_bytes().replace(b"\r\n", b"\n")))
        rows = reg.load_register()
        self.assertEqual(rows[:1036], reg.load_register(original))
        prefix = b"".join(current.splitlines(keepends=True)[:1061])
        self.assertEqual(hashlib.sha256(prefix).hexdigest(), MAIN_BYTES_SHA256)
        self.assertEqual(rows_digest(rows[:1060]), MAIN_HISTORY_SHA256)
        self.assertEqual(integration_integrity_errors(rows), [])

    def test_species_integration_preserves_main_bytes_and_rejects_mutation(self):
        rows = reg.load_register()
        lines = reg.REGISTER.read_bytes().replace(b"\r\n", b"\n").splitlines(keepends=True)
        self.assertEqual(hashlib.sha256(b"".join(lines[:1460])).hexdigest(), SPECIES_EVIDENCE["main_register_bytes_sha256"])
        self.assertEqual(hashlib.sha256(b"".join(lines[:1470])).hexdigest(),
                         SPECIES_EVIDENCE["thermal_main_register_bytes_sha256"])
        self.assertEqual(species_integration_errors(rows), [])
        self.assertEqual(rows_digest(species_original_projection(rows[SPECIES_TAIL])),
                         SPECIES_EVIDENCE["integrated_register_rows_sha256"])
        self.assertEqual([row["entry_id"] for row in rows[SPECIES_TAIL]], [f"T{n}" for n in range(1470, 1742)])
        self.assertEqual({row["batch"] for row in rows[SPECIES_TAIL]}, {f"B{n}-2026-11-17" for n in range(33, 44)})
        for index in (0, 1100, 1401, 1458, 1459, 1468, 1469, 1600, 1740):
            for field in reg.COLUMNS:
                changed = rows.copy()
                changed[index] = dict(rows[index])
                changed[index][field] += " altered"
                self.assertTrue(species_integration_errors(changed), (index, field))
            self.assertTrue(species_integration_errors(rows[:index] + rows[index + 1:]))
        next_id = max(int(row["entry_id"][1:]) for row in rows) + 1
        self.assertTrue(species_integration_errors(rows + [dict(rows[SPECIES_TAIL][-1], entry_id=f"T{next_id}")]))
        self.assertEqual(species_integration_errors(rows + [dict(rows[-1], entry_id=f"T{next_id}",
                                                               batch="B99-2026-11-19")]), [])
        for language in ("FR", "EN"):
            source = (reg.ROOT / f"frontend/src/i18n/{language.lower()}.ts").read_text(encoding="utf-8")
            restored = before_species_integration(source, rows, language)
            for line in SPECIES_EVIDENCE["catalog_additions"][language.lower()]:
                for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                    with self.assertRaises(AssertionError):
                        before_species_integration(source.replace(line, replacement), rows, language)
            extra = "  unapprovedKey: 'Unapproved',\n"
            self.assertEqual(before_species_integration(source + extra, rows, language), restored + extra)

    def test_thermal_source_mapping_changes_only_entry_id_and_batch(self):
        source = reg.load_register(integration_evidence("thermal"))[1010:]
        projected = reg.load_register()[1060:1086]
        self.assertEqual(len(source), 26)
        self.assertEqual(len(projected), 26)
        for number, (before, after) in enumerate(zip(source, projected), 1011):
            with self.subTest(source_entry=before["entry_id"]):
                self.assertEqual(before["entry_id"], f"T{number}")
                self.assertEqual(before["batch"], "B20-2026-11-09")
                self.assertEqual(after, dict(before, entry_id=f"T{number + 50}", batch=INTEGRATION_B25))
        self.assertEqual(rows_digest(projected), THERMAL_FINAL_ROWS_SHA256)
        # Preserve the earlier B22 projection as independent historical evidence.
        historical = [dict(row, entry_id=f"T{number + 26}", batch="B22-2026-11-09")
                      for number, row in enumerate(source, 1011)]
        self.assertEqual(rows_digest(historical), THERMAL_PROJECTED_ROWS_SHA256)
        self.assertEqual(rows_digest(reg.load_register(integration_evidence("movements")) + historical),
                         "cf1b7f575ce11e801e78ce81385fb133b3ee07e04bfca63ffc4eff4d77b9308c")

    def test_integration_has_exact_count_unique_ordered_ids_and_chronology(self):
        rows = reg.load_register()
        integrated = rows[:1811]
        self.assertEqual(len(integrated), 1811)
        self.assertEqual(len(b44_events(rows)), 48)
        self.assertEqual(len(b45_events(rows)), 22)
        self.assertEqual(Counter(row["batch"] for row in integrated[1741:]), Counter({B44: 48, B45: 22}))
        self.assertEqual([row["entry_id"] for row in integrated], [f"T{n:03d}" for n in range(1, 1812)])
        self.assertEqual(species_integration_errors(rows), [])
        self.assertEqual(rows_digest(rows[:1100]), PRE_B29_ROWS_SHA256)
        self.assertEqual(integration_integrity_errors(rows), [])
        b29 = b29_events(rows)
        self.assertEqual(Counter((row["language"], row["status"]) for row in b29),
                         Counter({("FR", "ALREADY_MATCHING"): 150, ("EN", "ALREADY_MATCHING"): 150,
                                  ("EN", "APPLIED"): 1}))
        self.assertEqual({row["baseline_git_head"] for row in b29},
                         {"380235543d7be7d715b5de2b81fade1f38e90936"})
        self.assertEqual({row["baseline_sheet_sha256"] for row in b29},
                         {"e80b409fbea485d97fbccf66d06da83eb46374883d1e3343f50e51e656b8c307"})
        b30 = b30_events(rows)
        b31 = b31_events(rows)
        b32 = b32_events(rows)
        self.assertEqual(len(b30), 56)
        self.assertEqual(len(b31), 2)
        self.assertEqual(len(b32), 10)
        self.assertEqual([(row["key_or_msgid"], row["language"], row["status"]) for row in b31],
                         [("dnaStateValidated", "FR", "REMOVED"), ("dnaStateValidated", "EN", "REMOVED")])
        self.assertEqual(Counter(row["batch"] for row in rows[1010:1469]),
                         Counter({"B20-2026-11-09": 6, "B21-2026-11-09": 20, "B22-2026-11-10": 6, "B23-2026-11-10": 12, "B24-2026-11-10": 6, INTEGRATION_B25: 26, B26: 8, B27: 4, "B28-2026-11-11": 2, B29: 301, B30: 56, B31: 2, B32: 10}))
        self.assertEqual(Counter(row["batch"] for row in rows[SPECIES_TAIL]),
                         Counter({f"B{n}-2026-11-17": count for n, count in
                                  zip(range(33, 44), (2, 12, 6, 2, 160, 34, 38, 4, 6, 6, 2))}))
        self.assertEqual(len(b26_events(rows)), 8)
        self.assertEqual(len(b27_events(rows)), 4)
        self.assertEqual(len({(row["batch"], reg.identity(row)) for row in rows}), len(rows))
        self.assertEqual(reg.check_chronology(rows), [])
        self.assertEqual(reg.validate(rows), [])
        self.assertEqual(reg.check_catalog_parity(), [])

    def test_every_incoming_and_movement_field_edit_is_rejected(self):
        original = reg.load_register()
        for index in range(1010, 1086):
            for name in reg.COLUMNS:
                with self.subTest(entry=original[index]["entry_id"], field=name):
                    changed = original.copy()
                    changed[index] = dict(original[index])
                    changed[index][name] += " altered"
                    self.assertTrue(integration_integrity_errors(changed))
        # The complete prefix freeze covers every older row, not only known batches.
        for index in range(1010):
            with self.subTest(older_entry=original[index]["entry_id"]):
                changed = original.copy()
                changed[index] = dict(original[index], reason=original[index]["reason"] + " altered")
                self.assertTrue(integration_integrity_errors(changed))

    def test_deletions_duplicates_reordering_and_batch_reuse_are_rejected(self):
        original = reg.load_register()
        for index in (0, 1009, 1010, 1016, 1035, 1060, 1085):
            variants = {
                "deleted": original[:index] + original[index + 1:],
                "inserted": original[:index] + [dict(original[index])] + original[index:],
                "duplicated": original + [dict(original[index])],
                "reordered": original[:index] + list(reversed(original[index:index + 2])) + original[index + 2:],
                "batch reuse": original + [dict(original[index], entry_id="T1087", batch=INTEGRATION_B25)],
            }
            for label, changed in variants.items():
                if changed != original:
                    with self.subTest(entry=original[index]["entry_id"], case=label):
                        self.assertTrue(integration_integrity_errors(changed))
        for batch in ("B20-2026-11-09", "B22-2026-11-08"):
            changed = [dict(row, batch=batch) if i >= 1060 else row for i, row in enumerate(original)]
            self.assertTrue(integration_integrity_errors(changed))
            self.assertTrue(reg.check_chronology(changed))

    def test_coordinated_catalog_rewrite_cannot_change_frozen_integration(self):
        original = reg.load_register()
        # Use final events for these identities so the coordinated edit remains valid history.
        for index in (1010, 1036, 1060):
            changed = [dict(row) for row in original]
            row = changed[index]
            row["final_value"] += " altered"
            if row["status"] == "ALREADY_MATCHING":
                row["initial_value"] = row["final_value"]
            def state(event):
                if reg.identity(event) == reg.identity(row):
                    return reg.SourceState(True, row["final_value"])
                return reg.read_source_state(event)
            self.assertEqual(reg.validate(changed, state), [])
            self.assertTrue(integration_integrity_errors(changed))

    def test_later_corrections_must_be_appended(self):
        original = reg.load_register()
        # A correction must target a live identity and use a new batch after all appended events.
        row = next(entry for entry in reversed(original) if entry["status"] != "REMOVED")
        next_id = max(int(entry["entry_id"][1:]) for entry in original) + 1
        next_batch = max(int(entry["batch"].split("-", 1)[0][1:]) for entry in original) + 1
        correction = dict(row, entry_id=f"T{next_id}", batch=f"B{next_batch}-2026-11-19", status="APPLIED",
                          baseline_git_head=HEAD_2, decision_ref="synthetic-future-correction",
                          initial_value=row["final_value"], final_value=row["final_value"] + " corrected")
        appended = original + [correction]
        state = lambda event: (reg.SourceState(True, correction["final_value"])
                               if reg.identity(event) == reg.identity(correction) else reg.read_source_state(event))
        self.assertEqual(integration_integrity_errors(appended), [])
        self.assertEqual(species_integration_errors(appended), [])
        self.assertEqual(reg.check_chronology(appended), [])
        self.assertEqual(reg.validate(appended, state), [])
        variants = {
            "reopened B28": (original + [dict(correction, batch="B28-2026-11-11")], "is split"),
            "reopened B29": (original + [dict(correction, batch=B29)], "several baseline_git_head"),
            "split B29": (appended + [dict(correction, entry_id=f"T{next_id + 1}", batch=B29)], "is split"),
            "mixed future baselines": (appended + [dict(correction, entry_id=f"T{next_id + 1}", baseline_git_head=HEAD_1)],
                                    "several baseline_git_head"),
        }
        for label, (changed, expected) in variants.items():
            with self.subTest(case=label):
                self.assertTrue(any(expected in error for error in reg.check_chronology(changed)))
                self.assertTrue(reg.validate(changed, state))


def event(entry, batch, key, initial, final, status="APPLIED", surface="react", language="EN", source=None):
    head = HEAD_1 if batch == B01 else HEAD_2
    default_source = {
        "react": f"frontend/src/i18n/{language.lower()}.ts",
        "django": f"backend/locale/{language.lower()}/LC_MESSAGES/django.po",
        "inline": "frontend/src/boxStatus.ts",
    }[surface]
    return {
        "entry_id": f"T{entry:03d}", "batch": batch, "surface": surface, "key_or_msgid": key,
        "language": language, "source_file": source or default_source, "initial_value": initial,
        "final_value": final, "sheet_value": "", "decision_ref": "test", "status": status,
        "reason": "synthetic", "baseline_git_head": head, "baseline_sheet_sha256": SHEET,
    }


def removal(entry, batch, key, initial, language="EN", **overrides):
    return dict(event(entry, batch, key, initial, "", status="REMOVED", language=language), **overrides)


def fake_sources(values):
    """values maps (surface, key, language) to the current application value; missing means absent."""
    def state(row):
        value = values.get((row["surface"], row["key_or_msgid"], row["language"]))
        return reg.SourceState(value is not None, value)
    return state


def roundtrip(rows):
    """Write and reload through CSV so the tests exercise the real file format."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "register.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, reg.COLUMNS, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        return reg.load_register(path)


class SyntheticHistoryTests(unittest.TestCase):
    def assertValid(self, rows, sources):
        self.assertEqual(reg.validate(roundtrip(rows), fake_sources(sources)), [])

    def assertRejected(self, rows, sources, fragment):
        errors = reg.validate(roundtrip(rows), fake_sources(sources))
        self.assertTrue(errors, "invalid history was accepted")
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_a_b02_continuing_b01_is_valid(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "recentAccess", "Latest access", "Last access"),
        ]
        self.assertValid(rows, {("react", "recentAccess", "EN"): "Last access"})

    def test_b_b02_initial_value_must_continue_b01_final_value(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "recentAccess", "Recent access", "Last access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Last access"}, "does not continue")

    def test_c_b02_rows_before_b01_are_rejected(self):
        rows = [
            event(1, B02, "recentAccess", "Latest access", "Last access"),
            event(2, B01, "recentAccess", "Recent access", "Latest access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "not numbered after")

    def test_c_interleaved_batches_are_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B02, "loginIntro", "Old", "New"),
            event(3, B01, "overviewEmpty", "No box.", "No box.", status="ALREADY_MATCHING"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New",
                   ("react", "overviewEmpty", "EN"): "No box."}
        self.assertRejected(rows, sources, "is split by another batch")

    def test_c_entry_ids_must_follow_file_order(self):
        rows = [
            event(2, B01, "recentAccess", "Recent access", "Latest access"),
            event(1, B02, "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "entry_id not strictly increasing")

    def test_c_later_batch_dated_earlier_is_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, "B02-2026-10-01", "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "dated before")

    def test_c_impossible_calendar_dates_are_rejected(self):
        for batch in ("B02-2026-99-99", "B02-2026-02-29", "B02-2026-04-31"):
            with self.subTest(batch=batch):
                rows = [event(1, batch, "key", "Value", "Value", status="ALREADY_MATCHING")]
                self.assertRejected(rows, {("react", "key", "EN"): "Value"}, "invalid calendar date")
        self.assertValid([event(1, "B02-2028-02-29", "key", "Value", "Value", status="ALREADY_MATCHING")],
                         {("react", "key", "EN"): "Value"})

    def test_c_same_batch_number_with_two_ids_is_ambiguous(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, "B01-2026-10-30", "loginIntro", "Old", "New"),
        ]
        sources = {("react", "recentAccess", "EN"): "Latest access", ("react", "loginIntro", "EN"): "New"}
        self.assertRejected(rows, sources, "batch number 1 is used by")

    def test_d_duplicate_event_in_same_batch_is_rejected(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B01, "recentAccess", "Latest access", "Last access"),
        ]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Last access"}, "duplicate event")

    def test_d_identical_duplicate_is_also_rejected(self):
        row = event(1, B01, "recentAccess", "Recent access", "Latest access")
        duplicate = dict(row, entry_id="T002")
        self.assertRejected([row, duplicate], {("react", "recentAccess", "EN"): "Latest access"}, "duplicate event")

    def test_d_later_contradiction_does_not_mask_earlier_event(self):
        rows = [
            event(1, B01, "recentAccess", "Recent access", "Latest access"),
            event(2, B01, "recentAccess", "Recent access", "Recent access", status="DEFERRED"),
        ]
        # The code still shows the first event's value; the contradictory second event must not win silently.
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "duplicate event")

    def test_e_same_key_on_different_surfaces_is_independent(self):
        rows = [
            event(1, B01, "Pending", "Pending", "Pending", status="DEFERRED", surface="react"),
            event(2, B01, "Pending", "À vérifier", "Pending", surface="django"),
            event(3, B01, "Pending", "Pending", "Pending", status="ALREADY_MATCHING", surface="inline"),
        ]
        sources = {("react", "Pending", "EN"): "Pending", ("django", "Pending", "EN"): "Pending",
                   ("inline", "Pending", "EN"): "Pending"}
        self.assertValid(rows, sources)

    def test_e_same_key_in_two_languages_is_independent(self):
        rows = [
            event(1, B01, "subcultureForbidden", "Old FR", "New FR", language="FR"),
            event(2, B01, "subcultureForbidden", "Old EN", "New EN", language="EN"),
        ]
        sources = {("react", "subcultureForbidden", "FR"): "New FR", ("react", "subcultureForbidden", "EN"): "New EN"}
        self.assertValid(rows, sources)

    def test_f_historical_b01_may_differ_from_code_when_b02_matches(self):
        rows = [
            event(1, B01, "loginIntro", "Access your cultures.", "Access your boxes."),
            event(2, B02, "loginIntro", "Access your boxes.", "Access your boxes and records."),
        ]
        self.assertValid(rows, {("react", "loginIntro", "EN"): "Access your boxes and records."})

    def test_f_latest_event_must_still_match_code(self):
        rows = [
            event(1, B01, "loginIntro", "Access your cultures.", "Access your boxes."),
            event(2, B02, "loginIntro", "Access your boxes.", "Access your boxes and records."),
        ]
        self.assertRejected(rows, {("react", "loginIntro", "EN"): "Access your boxes."}, "latest event expects")

    def test_g_no_op_statuses_carry_the_value_forward(self):
        rows = [
            event(1, B01, "overviewEmpty", "No living box.", "No living box.", status="ALREADY_MATCHING"),
            event(2, B01, "movementKindUnclassified", "Provenance", "Provenance", status="PRESERVED"),
            event(3, B01, "adminTransferText", "Old", "Old", status="DEFERRED"),
            event(4, B02, "adminTransferText", "Old", "New"),
            event(5, B02, "movementKindUnclassified", "Provenance", "Provenance", status="PRESERVED"),
        ]
        sources = {("react", "overviewEmpty", "EN"): "No living box.",
                   ("react", "movementKindUnclassified", "EN"): "Provenance",
                   ("react", "adminTransferText", "EN"): "New"}
        self.assertValid(rows, sources)

    def test_g_no_op_status_cannot_change_value(self):
        for status in ("ALREADY_MATCHING", "PRESERVED", "DEFERRED"):
            with self.subTest(status=status):
                rows = [event(1, B01, "adminTransferText", "Old", "New", status=status)]
                self.assertRejected(rows, {("react", "adminTransferText", "EN"): "New"}, "must not change the value")

    def test_g_deferred_cannot_hide_an_unrecorded_code_change(self):
        rows = [event(1, B01, "adminTransferText", "Old", "Old", status="DEFERRED")]
        self.assertRejected(rows, {("react", "adminTransferText", "EN"): "Edited"}, "latest event expects")

    def test_g_applied_requires_a_change(self):
        rows = [event(1, B01, "recentAccess", "Latest access", "Latest access")]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "APPLIED without a change")

    def test_g_not_found_has_no_values_and_must_be_absent(self):
        rows = [event(1, B01, "missingKey", "", "", status="NOT_FOUND")]
        self.assertValid(rows, {})
        self.assertRejected(rows, {("react", "missingKey", "EN"): "Here"}, "marked NOT_FOUND but present")
        invented = [event(1, B01, "missingKey", "", "Invented", status="NOT_FOUND")]
        self.assertRejected(invented, {}, "NOT_FOUND must leave")

    def test_i_inline_not_found_is_rejected(self):
        rows = [event(1, B01, "boxStatus.ts#statusLabels.en.pending_review", "", "", status="NOT_FOUND",
                      surface="inline")]
        # Rejected whatever the source reports: absence of an inline string cannot be proven.
        self.assertRejected(rows, {}, "NOT_FOUND is not supported for inline translations")
        self.assertRejected(rows, {("inline", "boxStatus.ts#statusLabels.en.pending_review", "EN"): "Pending review"},
                            "NOT_FOUND is not supported for inline translations")
        self.assertTrue(any("not supported for inline" in error for error in reg.validate(roundtrip(rows))))

    def test_i_react_not_found_is_accepted_when_key_is_absent(self):
        rows = [event(1, B01, "retiredKey", "", "", status="NOT_FOUND", surface="react")]
        self.assertValid(rows, {("react", "otherKey", "EN"): "Other"})

    def test_i_react_not_found_is_rejected_when_key_exists(self):
        rows = [event(1, B01, "recentAccess", "", "", status="NOT_FOUND", surface="react")]
        self.assertRejected(rows, {("react", "recentAccess", "EN"): "Latest access"}, "marked NOT_FOUND but present")

    def test_i_inline_supported_statuses_remain_valid(self):
        key = "boxStatus.ts#statusLabels.en.pending_review"
        rows = [
            event(1, B01, key, "Pending review", "Pending review", status="DEFERRED", surface="inline"),
            event(2, B02, key, "Pending review", "Pending", status="APPLIED", surface="inline"),
        ]
        self.assertValid(rows, {("inline", key, "EN"): "Pending"})
        for status in ("ALREADY_MATCHING", "PRESERVED", "DEFERRED"):
            with self.subTest(status=status):
                single = [event(1, B01, key, "Pending review", "Pending review", status=status, surface="inline")]
                self.assertValid(single, {("inline", key, "EN"): "Pending review"})

    def test_g_event_after_not_found_starts_from_absence(self):
        rows = [
            event(1, B01, "missingKey", "", "", status="NOT_FOUND"),
            event(2, B02, "missingKey", "Was there", "Now", status="APPLIED"),
        ]
        self.assertRejected(rows, {("react", "missingKey", "EN"): "Now"}, "does not continue")

    def test_placeholders_must_be_preserved(self):
        rows = [event(1, B01, "auditDescriptionManualTemperature", "Recorded for {zone}", "Recorded for {code}")]
        self.assertRejected(rows, {("react", "auditDescriptionManualTemperature", "EN"): "Recorded for {code}"},
                            "placeholders differ")

    def test_placeholder_multiplicity_must_be_preserved(self):
        rows = [event(1, B01, "key", "{count} / {count}", "{count}")]
        self.assertRejected(rows, {("react", "key", "EN"): "{count}"}, "placeholders differ")

    def test_django_presence_is_independent_of_translation_value(self):
        row = event(1, B01, "empty", "", "", status="NOT_FOUND", surface="django")
        # Isolate presence from strict PO validation, which separately rejects empty translations.
        with patch.object(reg, "read_po", return_value={"empty": ""}):
            state = reg.read_source_state(row)
            self.assertEqual(state, reg.SourceState(True, "empty"))
            self.assertTrue(any("marked NOT_FOUND but present" in error for error in reg.validate([row])))
        with patch.object(reg, "read_po", return_value={}):
            self.assertEqual(reg.read_source_state(row), reg.SourceState(False, "empty"))
            self.assertEqual(reg.validate([row]), [])

    def test_register_reuses_strict_po_parser(self):
        row = event(1, B01, "key", "Value", "Value", status="DEFERRED", surface="django")
        with patch.object(reg, "parse_po_file", side_effect=ValueError("invalid PO")):
            with self.assertRaisesRegex(ValueError, "invalid PO"):
                reg.read_source_state(row)

    def test_japanese_is_not_accepted(self):
        rows = [event(1, B01, "boxes", "箱", "容器", language="JA", source="frontend/src/i18n/ja.ts")]
        self.assertRejected(rows, {("react", "boxes", "JA"): "容器"}, "unsupported language")


class ApprovedPlaceholderAdditionTests(unittest.TestCase):
    """B28 introduces {minutes} in probeLatestInterval under one explicit product decision."""

    def b28_row(self, language):
        rows = [row for row in reg.load_register() if row["batch"] == "B28-2026-11-11"]
        return dict(next(row for row in rows if row["language"] == language))

    def test_approved_b28_events_introduce_minutes(self):
        for language in ("FR", "EN"):
            with self.subTest(language=language):
                row = self.b28_row(language)
                self.assertIn("{minutes}", row["final_value"])
                self.assertNotIn("{", row["initial_value"])
                self.assertEqual(reg.check_row(row), [])

    def test_approval_is_bound_to_the_exact_event(self):
        row = dict(self.b28_row("EN"), entry_id="T1101")
        self.assertTrue(any("placeholders differ" in error for error in reg.check_row(row)))

    def test_approved_event_may_not_remove_a_placeholder(self):
        row = dict(self.b28_row("EN"), final_value="Latest mean")
        self.assertTrue(any("placeholders differ" in error for error in reg.check_row(row)))

    def test_approved_event_may_not_add_another_placeholder(self):
        row = dict(self.b28_row("EN"), final_value="Latest {minutes}-min mean for {probe}")
        self.assertTrue(any("placeholders differ" in error for error in reg.check_row(row)))

    def test_unapproved_placeholder_introduction_is_still_rejected(self):
        values = {("react", "probeLatestInterval", "EN"): "Latest {minutes}-min mean"}
        rows = [event(1, B01, "probeLatestInterval", "Latest interval (mean)", "Latest {minutes}-min mean")]
        errors = reg.validate(roundtrip(rows), fake_sources(values))
        self.assertTrue(any("placeholders differ" in error for error in errors), errors)


class RemovedStatusTests(unittest.TestCase):
    """REMOVED records an intentional React catalog deletion, paired in FR and EN."""

    def errors(self, rows, sources):
        return reg.validate(roundtrip(rows), fake_sources(sources))

    def assertRejected(self, rows, sources, fragment):
        errors = self.errors(rows, sources)
        self.assertTrue(any(fragment in error for error in errors), errors)

    def test_paired_removal_of_absent_keys_is_valid(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old", "EN")]
        self.assertEqual(self.errors(rows, {("react", "otherKey", "EN"): "Other"}), [])

    def test_removal_continues_every_previous_value_status(self):
        for status in ("APPLIED", "ALREADY_MATCHING", "PRESERVED", "DEFERRED"):
            with self.subTest(status=status):
                initial = "Before" if status == "APPLIED" else "Old"
                rows = [
                    event(1, B01, "oldKey", "Ancien", "Ancien", status="DEFERRED", language="FR"),
                    event(2, B01, "oldKey", initial, "Old", status=status),
                    removal(3, B03, "oldKey", "Ancien", "FR"),
                    removal(4, B03, "oldKey", "Old", "EN"),
                ]
                self.assertEqual(self.errors(rows, {}), [])
                broken = rows[:3] + [removal(4, B03, "oldKey", "Something else", "EN")]
                self.assertRejected(broken, {}, "does not continue")

    def test_removal_keeps_placeholders_out_of_the_comparison(self):
        rows = [removal(1, B03, "countKey", "{count} boîtes", "FR"), removal(2, B03, "countKey", "{count} boxes")]
        self.assertEqual(self.errors(rows, {}), [])

    def test_initial_value_is_required(self):
        rows = [removal(1, B03, "oldKey", "", "FR"), removal(2, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "REMOVED requires the exact pre-removal value")

    def test_final_value_must_be_empty(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR", final_value="Ancien"), removal(2, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "REMOVED must leave final_value empty")

    def test_sheet_value_is_not_invented(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR", sheet_value="Ancien"), removal(2, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "REMOVED must not carry a sheet_value")

    def test_only_react_surface_is_supported(self):
        for surface in ("django", "inline"):
            with self.subTest(surface=surface):
                rows = [dict(removal(1, B03, "Old", "Ancien", "FR"), surface=surface),
                        dict(removal(2, B03, "Old", "Old"), surface=surface)]
                self.assertRejected(rows, {}, "REMOVED is only supported for the React catalog surface")

    def test_source_file_must_be_the_catalog_of_the_language(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR", source_file="frontend/src/i18n/en.ts"),
                removal(2, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "REMOVED must name the FR React catalog")

    def test_key_still_present_is_rejected(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old")]
        self.assertRejected(rows, {("react", "oldKey", "EN"): "Old"}, "marked REMOVED but present")

    def test_removed_key_cannot_silently_reappear_later(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old")]
        # Re-added with a new wording but no register event: the latest event is still REMOVED.
        self.assertRejected(rows, {("react", "oldKey", "FR"): "Nouveau"}, "cannot reappear")

    def test_unpaired_removal_is_rejected(self):
        self.assertRejected([removal(1, B03, "oldKey", "Old")], {}, "exactly one FR and one EN")
        self.assertRejected([removal(1, B03, "oldKey", "Ancien", "FR")], {}, "exactly one FR and one EN")

    def test_pair_split_across_batches_is_rejected(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, "B04-2026-11-07", "oldKey", "Old")]
        self.assertRejected(rows, {}, "exactly one FR and one EN")

    def test_pair_must_be_the_same_key(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "otherKey", "Old")]
        self.assertRejected(rows, {}, "exactly one FR and one EN")

    def test_duplicate_removal_in_one_batch_is_rejected(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old"),
                removal(3, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "duplicate event")

    def test_later_event_contradicting_a_removal_is_rejected(self):
        b04 = "B04-2026-11-07"
        base = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old")]
        for status, initial, final in (("APPLIED", "", "Back"), ("APPLIED", "Old", "Back"),
                                       ("DEFERRED", "Old", "Old"), ("ALREADY_MATCHING", "Old", "Old"),
                                       ("PRESERVED", "Old", "Old")):
            with self.subTest(status=status, initial=initial):
                later = event(3, b04, "oldKey", initial, final, status=status)
                self.assertRejected(base + [later], {("react", "oldKey", "EN"): final}, "contradicts the removal")
        again = [removal(3, b04, "oldKey", "Ancien", "FR"), removal(4, b04, "oldKey", "Old")]
        self.assertRejected(base + again, {}, "contradicts the removal")

    def test_not_found_may_confirm_a_removal(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old"),
                event(3, "B04-2026-11-07", "oldKey", "", "", status="NOT_FOUND")]
        self.assertEqual(self.errors(rows, {}), [])
        self.assertRejected(rows, {("react", "oldKey", "EN"): "Old"}, "marked NOT_FOUND but present")

    def test_removal_cannot_follow_absence(self):
        rows = [event(1, B01, "oldKey", "", "", status="NOT_FOUND"),
                removal(2, B03, "oldKey", "Ancien", "FR"), removal(3, B03, "oldKey", "Old")]
        self.assertRejected(rows, {}, "does not continue")

    def test_unrelated_parity_and_placeholder_checks_remain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalogs = root / "frontend/src/i18n"
            catalogs.mkdir(parents=True)
            (catalogs / "fr.ts").write_text("export const fr = { a: '{count}', b: 'B' } as const;\n", encoding="utf-8")
            (catalogs / "en.ts").write_text("export const en = { a: '{zone}' };\n", encoding="utf-8")
            with patch.object(reg, "ROOT", root):
                errors = reg.check_catalog_parity()
        self.assertTrue(any("keys differ" in error for error in errors), errors)
        self.assertTrue(any("placeholders differ for a" in error for error in errors), errors)


class CatalogParserTests(unittest.TestCase):
    """Presence checks must not be bypassed by catalog formatting: parse exactly or fail closed."""

    def parse(self, body, language="en", header="", suffix=""):
        return reg.parse_catalog(f"{header}export const {language} = {{\n{body}\n}} as const;\n{suffix}", language)

    def test_real_catalog_shapes_are_accepted(self):
        text = ("import type { TranslationKey } from './fr';\n\n"
                "export const en: Record<TranslationKey, string> = {\n  a: 'A',\n    b: \"B\",\n"
                "  // comment: 'ignored'\n  /* hidden: 'no' */ c: `C`,\n};\n")
        self.assertEqual(reg.parse_catalog(text, "en"), {"a": "A", "b": "B", "c": "C"})
        fr = "export const fr = {\n  a: 'A',\n} as const;\n\nexport type TranslationKey = keyof typeof fr;\n"
        self.assertEqual(reg.parse_catalog(fr, "fr"), {"a": "A"})

    def test_unusual_formatting_is_still_detected(self):
        cases = {
            "multiline entry": "oldKey:\n    'Old',",
            "two entries on one line": "a: 'A', oldKey: 'Old',",
            "quoted key": "'oldKey': 'Old',",
            "double-quoted key": "\"oldKey\": \"Old\"",
            "escaped key": "'old\\u004bey': 'Old'",
            "no trailing comma": "a: 'A', oldKey: 'Old'",
            "comment between tokens": "oldKey /* why */ : // value\n 'Old',",
            "template value": "oldKey: `Old`,",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                self.assertEqual(self.parse(body).get("oldKey"), "Old")

    def test_escapes_and_zero_are_decoded(self):
        values = self.parse("zero: '0', quote: 'l\\'aquarium', nl: 'a\\nb', hex: '\\x41', u: '\\u00e9\\u{1F600}'")
        self.assertEqual(values, {"zero": "0", "quote": "l'aquarium", "nl": "a\nb", "hex": "A", "u": "é\U0001F600"})

    def test_unsupported_constructs_fail_closed(self):
        cases = {
            "spread": "...other, a: 'A'",
            "computed key": "['oldKey']: 'Old'",
            "numeric key": "0: 'Zero'",
            "identifier value": "oldKey: other",
            "number value": "oldKey: 0",
            "template substitution": "oldKey: `Old ${x}`",
            "concatenation": "oldKey: 'O' + 'ld'",
            "shorthand": "oldKey",
            "method": "oldKey() { return 'Old'; }",
            "duplicate key": "oldKey: 'One', 'oldKey': 'Two'",
            "empty key": "'': 'Empty'",
            "missing comma": "a: 'A' oldKey: 'Old'",
            "unicode escape in identifier": "old\\u004bey: 'Old'",
            "legacy octal escape": "oldKey: '\\1'",
            "unterminated string": "oldKey: 'Old",
        }
        for label, body in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(reg.CatalogFormatError):
                    self.parse(body)

    def test_code_outside_the_catalog_fails_closed(self):
        cases = {
            "late assignment": ("", "Object.assign(en, { oldKey: 'Old' });\n"),
            "second export": ("", "export const extra = { oldKey: 'Old' };\n"),
            "value import": ("import { other } from './other';\n", ""),
            "statement before": ("const x = 'oldKey';\n", ""),
        }
        for label, (header, suffix) in cases.items():
            with self.subTest(label=label):
                with self.assertRaises(reg.CatalogFormatError):
                    self.parse("a: 'A'", header=header, suffix=suffix)
        with self.assertRaises(reg.CatalogFormatError):
            reg.parse_catalog("export const fr = ({ a: 'A' });", "fr")
        with self.assertRaises(reg.CatalogFormatError):
            reg.parse_catalog("export let en = { a: 'A' };", "en")
        with self.assertRaises(reg.CatalogFormatError):
            reg.parse_catalog("export const fr = { a: 'A' };", "en")

    def test_removed_presence_uses_the_strict_parser(self):
        rows = [removal(1, B03, "oldKey", "Ancien", "FR"), removal(2, B03, "oldKey", "Old")]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            catalogs = root / "frontend/src/i18n"
            catalogs.mkdir(parents=True)
            (catalogs / "fr.ts").write_text("export const fr = { a: 'A' } as const;\n", encoding="utf-8")
            with patch.object(reg, "ROOT", root):
                for label, entry in (("plain", "  oldKey: 'Old',"), ("multiline", "  oldKey:\n    'Old',"),
                                     ("same line", "  b: 'B', oldKey: 'Old',"), ("quoted", "  \"oldKey\": 'Old',")):
                    with self.subTest(label=label):
                        (catalogs / "en.ts").write_text(f"export const en = {{\n  a: 'A',\n{entry}\n}};\n",
                                                        encoding="utf-8")
                        self.assertTrue(any("marked REMOVED but present" in error
                                            for error in reg.validate(roundtrip(rows))))
                (catalogs / "en.ts").write_text("export const en = { a: 'A' };\n", encoding="utf-8")
                self.assertEqual(reg.validate(roundtrip(rows)), [])
                (catalogs / "en.ts").write_text("export const en = { a: 'A', ...extra };\n", encoding="utf-8")
                with self.assertRaises(reg.CatalogFormatError):
                    reg.validate(roundtrip(rows))


class RealRegisterTests(unittest.TestCase):
    def test_h_b47_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        for entry in b47_events(rows):
            language = entry["language"]
            source = (reg.ROOT / reg.REACT_CATALOGS[language]).read_text(encoding="utf-8")
            line = f"  demoModeBanner: '{entry['final_value']}',\n"
            altered = line.replace(": '", ": 'changed ")
            restored = before_b47(source, rows, language)
            self.assertEqual(restored, source.replace(line, ""))
            extra = "  unregisteredKey: 'Unapproved',\n"
            self.assertEqual(before_b47(source.replace(line, line + extra), rows, language),
                             source.replace(line, extra))
            for replacement in ("", line + line, line + altered, altered):
                with self.subTest(language=language, replacement=replacement), self.assertRaises(AssertionError):
                    before_b47(source.replace(line, replacement), rows, language)
            for changed in ([row for row in rows if row != entry], rows + [entry],
                            rows + [dict(entry, entry_id="T1815", key_or_msgid="unregisteredKey")]):
                with self.assertRaises(AssertionError):
                    before_b47(source, changed, language)
            for field in reg.COLUMNS + ["unknown_column"]:
                changed = [dict(row, **{field: row.get(field, "") + " changed"}) if row == entry else row
                           for row in rows]
                with self.subTest(language=language, field=field), self.assertRaises(AssertionError):
                    before_b47(source, changed, language)
            changed_value = "changed " + entry["final_value"]
            coordinated = [dict(row, initial_value=changed_value, final_value=changed_value) if row == entry else row
                           for row in rows]
            with self.assertRaisesRegex(AssertionError, "B47: history was modified"):
                before_b47(source.replace(line, altered), coordinated, language)

    def test_h_b45_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        events = b45_events(rows)
        for index in range(1789, 1811):
            for field in reg.COLUMNS + ["unknown_column"]:
                changed = rows.copy()
                changed[index] = dict(rows[index])
                changed[index][field] = changed[index].get(field, "") + " changed"
                with self.subTest(entry=rows[index]["entry_id"], field=field):
                    with self.assertRaises(AssertionError):
                        b45_events(changed)
            changed = rows.copy()
            changed[index] = dict(rows[index])
            changed[index].pop("sheet_value")
            for invalid in (changed, rows[:index] + rows[index + 1:]):
                with self.assertRaises(AssertionError):
                    b45_events(invalid)
        reordered = rows.copy()
        reordered[1789], reordered[1790] = reordered[1790], reordered[1789]
        for changed in (reordered, rows + [dict(events[0], entry_id="T1812")],
                        [dict(events[0], batch="earlier")] + rows):
            with self.assertRaises(AssertionError):
                b45_events(changed)
        self.assertEqual(b45_events(rows + [dict(events[2], entry_id="T1812", batch="later")]), events)
        for language, digest in (
            ("FR", "b987f50afd2504ac6b3de347ccf3a5bca4aa4abfbe8e6a2f80dc040c7e5cfb22"),
            ("EN", "f51265056ec7e3f84057b4e8405410292465240beb720bbc2e5d4e01ebf25a77"),
        ):
            source = (reg.ROOT / reg.REACT_CATALOGS[language]).read_text(encoding="utf-8")
            layout = before_donation_integration_layout(source, rows, language)
            self.assertEqual(len(source.splitlines()) - len(layout.splitlines()), 3)
            for key in ("dnaMissing", "exchangePreviewTitle", "shareWorkspace"):
                line = next(line for line in source.splitlines(keepends=True) if line.startswith(f"  {key}: "))
                with self.subTest(language=language, separator=key):
                    with self.assertRaises(AssertionError):
                        before_donation_integration_layout(source.replace("\n" + line, line), rows, language)
            # Other whitespace changes must survive inversion and fail the historical hash.
            unknown_space = "\n  shareWorkspaceHelp: "
            changed_space = source.replace(unknown_space, "\n" + unknown_space)
            self.assertNotEqual(changed_space, source)
            self.assertEqual(before_donation_integration_layout(changed_space, rows, language),
                             layout.replace(unknown_space, "\n" + unknown_space))
            restored = before_b45(source, rows, language)
            # Remove main-only layers to recover the independently frozen feature B30 source.
            feature_source = before_b32(before_b30(before_b47(restored, rows, language), rows, language),
                                        rows, language)
            self.assertEqual(hashlib.sha256(feature_source.encode()).hexdigest(), digest)
            extra = "  unregisteredKey: 'Unapproved',\n"
            self.assertEqual(before_b45(source + extra, rows, language), restored + extra)
            for row in events:
                if row["language"] != language:
                    continue
                if row["status"] == "REMOVED":
                    with self.assertRaises(AssertionError):
                        before_b45(source + f"  {row['key_or_msgid']}: '{row['initial_value']}',\n", rows, language)
                    continue
                line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                    with self.assertRaises(AssertionError):
                        before_b45(source.replace(line, replacement), rows, language)
                changed = [dict(entry, initial_value="changed " + entry["initial_value"],
                                final_value="changed " + entry["final_value"]) if entry == row else entry
                           for entry in rows]
                with self.assertRaises(AssertionError):
                    before_b45(source.replace(line, line.replace(": '", ": 'changed ")), changed, language)

    def test_h_b44_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        events = b44_events(rows)
        for index in range(1741, 1789):
            for field in reg.COLUMNS + ["unknown_column"]:
                changed = rows.copy()
                changed[index] = dict(rows[index])
                changed[index][field] = changed[index].get(field, "") + " changed"
                with self.subTest(entry=rows[index]["entry_id"], field=field):
                    with self.assertRaises(AssertionError):
                        b44_events(changed)
            changed = rows.copy()
            changed[index] = dict(rows[index])
            changed[index].pop("sheet_value")
            with self.assertRaises(AssertionError):
                b44_events(changed)
            with self.assertRaises(AssertionError):
                b44_events(rows[:index] + rows[index + 1:])
        reordered = rows.copy()
        reordered[1741], reordered[1742] = reordered[1742], reordered[1741]
        for changed in (reordered, rows + [dict(events[0], entry_id="T1812")],
                        [dict(events[0], batch="earlier")] + rows):
            with self.assertRaises(AssertionError):
                b44_events(changed)
        correction = dict(events[0], entry_id="T1812", batch="B46-2026-11-19", status="APPLIED",
                          final_value="Later correction")
        self.assertEqual(b44_events(rows + [correction]), events)
        for language in ("FR", "EN"):
            source = (reg.ROOT / reg.REACT_CATALOGS[language]).read_text(encoding="utf-8")
            source = before_b45(source, rows, language)
            restored = before_b44(source, rows, language)
            extra = "  unregisteredKey: 'Unapproved',\n"
            self.assertEqual(before_b44(source + extra, rows, language), restored + extra)
            for row in events:
                if row["language"] != language:
                    continue
                line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                    with self.assertRaises(AssertionError):
                        before_b44(source.replace(line, replacement), rows, language)
                changed = [dict(entry, initial_value="changed " + entry["initial_value"],
                                final_value="changed " + entry["final_value"]) if entry == row else entry
                           for entry in rows]
                with self.assertRaises(AssertionError):
                    before_b44(source.replace(line, line.replace(": '", ": 'changed ")), changed, language)

    def test_h_b01_register_is_unchanged_and_valid(self):
        rows = reg.load_register()
        b01 = [row for row in rows if row["batch"] == B01]
        self.assertEqual(len(b01), 34)
        self.assertEqual(rows[:34], b01, "B01 rows must stay first and contiguous")
        self.assertEqual(rows_digest(b01), B01_ROWS_SHA256, "B01 history was modified")
        self.assertEqual(reg.validate(rows), [])
        self.assertEqual(reg.check_catalog_parity(), [])

    def test_h_b02_register_is_unchanged(self):
        rows = reg.load_register()
        b02 = [row for row in rows if row["batch"] == REAL_B02]
        self.assertEqual(len(b02), 86)
        self.assertEqual(rows[34:120], b02, "B02 rows must directly follow B01 and stay contiguous")
        self.assertEqual([b02[0]["entry_id"], b02[-1]["entry_id"]], ["T035", "T120"])
        self.assertEqual(rows_digest(b02), B02_ROWS_SHA256, "B02 history was modified")

    def test_h_corrupted_copy_of_b02_is_detected(self):
        b02 = [dict(row) for row in reg.load_register() if row["batch"] == REAL_B02]
        b02[-1]["reason"] += " edited"
        self.assertNotEqual(rows_digest(b02), B02_ROWS_SHA256)

    def test_h_b03_removals_are_paired_and_absent(self):
        rows = reg.load_register()
        b03 = [row for row in rows if row["batch"] == REAL_B03]
        self.assertEqual(rows[120:440], b03, "B03 rows must directly follow B02 and stay contiguous")
        self.assertEqual([b03[0]["entry_id"], b03[-1]["entry_id"]], ["T121", "T440"])
        self.assertEqual(rows_digest(b03), B03_ROWS_SHA256, "B03 history was modified")
        self.assertEqual({row["status"] for row in b03}, {"REMOVED"})
        keys = {language: {row["key_or_msgid"] for row in b03 if row["language"] == language}
                for language in ("FR", "EN")}
        self.assertEqual(len(b03), 320)
        self.assertEqual(len(keys["FR"]), 160)
        self.assertEqual(keys["FR"], keys["EN"])
        for language, path in reg.REACT_CATALOGS.items():
            self.assertFalse(keys[language] & set(reg.read_catalog(reg.ROOT / path)))

    def test_h_final_b03_cleanup_is_unchanged(self):
        rows = reg.load_register()
        batches = {"B04-2026-10-29", "B05-2026-10-29", "B06-2026-10-29"}
        cleanup = [row for row in rows if row["batch"] in batches]
        self.assertEqual(len(cleanup), 118)
        self.assertEqual(rows[440:558], cleanup, "Final B03 cleanup must directly follow B03")
        self.assertEqual([cleanup[0]["entry_id"], cleanup[-1]["entry_id"]], ["T441", "T558"])
        self.assertEqual(rows_digest(cleanup), B03_FINAL_ROWS_SHA256, "Final B03 history was modified")
        self.assertEqual({row["status"] for row in cleanup}, {"REMOVED"})
        self.assertEqual(reg.validate(rows), [])

    def test_h_probe_batch_follows_final_cleanup_unchanged(self):
        rows = reg.load_register()
        probes = [row for row in rows if row["batch"] == "B07-2026-10-29"]
        self.assertEqual(len(probes), 58)
        self.assertEqual(rows[558:616], probes, "Probe rows must directly follow B06 and stay contiguous")
        self.assertEqual([probes[0]["entry_id"], probes[-1]["entry_id"]], ["T559", "T616"])
        self.assertEqual(rows_digest(probes), PROBE_ROWS_SHA256, "Probe translation history was modified")
        self.assertEqual({row["status"] for row in probes}, {"ALREADY_MATCHING"})
        for language, path in reg.REACT_CATALOGS.items():
            catalog = reg.read_catalog(reg.ROOT / path)
            translated = [row for row in probes if row["language"] == language]
            react = [row for row in translated if row["surface"] == "react"]
            self.assertEqual(len(translated), 29)
            self.assertEqual(len(react), 28)
            for row in react:
                self.assertEqual(catalog[row["key_or_msgid"]], row["final_value"])
        self.assertEqual(reg.validate(rows), [])
        self.assertEqual(reg.check_catalog_parity(), [])

    def test_h_b13_thermal_batch_is_unchanged(self):
        rows = reg.load_register()
        self.assertEqual(b13_integrity_errors(rows), [])
        b13 = rows[B13_POSITION]
        self.assertEqual({(row["surface"], row["status"]) for row in b13}, {("react", "ALREADY_MATCHING")})
        keys = {language: [row["key_or_msgid"] for row in b13 if row["language"] == language]
                for language in ("FR", "EN")}
        self.assertEqual(len(keys["FR"]), 7)
        self.assertEqual(keys["FR"], keys["EN"])
        for language, path in reg.REACT_CATALOGS.items():
            catalog = reg.read_catalog(reg.ROOT / path)
            for row in b13:
                if row["language"] == language:
                    self.assertEqual(catalog[row["key_or_msgid"]], row["final_value"], row["entry_id"])

    def coordinated(self, rows, overrides):
        """Real sources, except catalog values changed together with the register."""
        def state(row):
            value = overrides.get((row["surface"], row["key_or_msgid"], row["language"]))
            return reg.SourceState(True, value) if value is not None else reg.read_source_state(row)
        return reg.validate(rows, state)

    def test_h_b18_b19_every_original_field_is_frozen(self):
        original = reg.load_register()
        self.assertEqual(movement_correction_integrity_errors(original), [])
        for batch, position, _, _ in MOVEMENT_CORRECTION_FREEZES:
            for index in range(position.start, position.stop):
                for name in reg.COLUMNS:
                    with self.subTest(entry=original[index]["entry_id"], field=name):
                        changed = [dict(row) for row in original]
                        changed[index][name] += " altered"
                        self.assertIn(f"{batch}: history was modified", movement_correction_integrity_errors(changed))

    def test_h_b18_b19_identity_position_order_and_membership_are_frozen(self):
        original = reg.load_register()
        for batch, position, _, _ in MOVEMENT_CORRECTION_FREEZES:
            index = position.start
            variants = {
                "deleted": original[:index] + original[index + 1:],
                "duplicated": original + [dict(original[index])],
                "reordered": original[:index] + [original[index + 1], original[index]] + original[index + 2:],
                "renumbered": original[:index] + [dict(original[index], entry_id="T9999")] + original[index + 1:],
                "moved batch": original[:index] + [dict(original[index], batch="B99-2026-11-07")] + original[index + 1:],
                "extra member": original + [dict(original[index], entry_id="T9999")],
                "shifted": original[:index] + [dict(original[0])] + original[index:],
            }
            for label, changed in variants.items():
                with self.subTest(batch=batch, case=label):
                    errors = movement_correction_integrity_errors(changed)
                    self.assertTrue(any(error.startswith(f"{batch}:") for error in errors), errors)

    def test_h_coordinated_b18_b19_edits_are_rejected_before_catalog_reconstruction(self):
        original = reg.load_register()
        for batch, position, _, _ in MOVEMENT_CORRECTION_FREEZES:
            for index in range(position.start, position.stop):
                with self.subTest(entry=original[index]["entry_id"]):
                    changed = [dict(row) for row in original]
                    row = changed[index]
                    row["final_value"] += " altered"
                    overrides = {(row["surface"], row["key_or_msgid"], row["language"]): row["final_value"]}
                    self.assertEqual(self.coordinated(changed, overrides), [],
                                     "Coordinated source changes bypass ordinary continuity/current-value checks")
                    self.assertIn(f"{batch}: history was modified", movement_correction_integrity_errors(changed))
                    # Exercise the real regression path: the freeze must fail before any catalog read/reversal.
                    with patch.object(reg, "load_register", return_value=changed), patch.object(
                        Path, "read_text", side_effect=AssertionError("Catalog read before frozen checks"),
                    ):
                        with self.assertRaisesRegex(AssertionError, f"{batch}: history was modified"):
                            self.test_h_coordinated_b13_register_and_catalog_edit_is_rejected()

    def test_h_b24_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        events = b24_events(rows)
        for language in ("FR", "EN"):
            source = (reg.ROOT / f"frontend/src/i18n/{language.lower()}.ts").read_text(encoding="utf-8")
            before_b24(source, rows, language)
            for row in events:
                for field in reg.COLUMNS:
                    changed = [dict(entry) for entry in rows]
                    changed[rows.index(row)][field] += " changed"
                    with self.subTest(language=language, entry=row["entry_id"], field=field):
                        with self.assertRaises(AssertionError):
                            before_b24(source, changed, language)
                with self.assertRaises(AssertionError):
                    before_b24(source, [entry for entry in rows if entry != row], language)
                if row["language"] == language:
                    line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                    for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                        with self.assertRaises(AssertionError):
                            before_b24(source.replace(line, replacement), rows, language)
            reordered = list(rows)
            reordered[1054], reordered[1055] = reordered[1055], reordered[1054]
            for changed in (reordered, rows + [dict(events[0], entry_id="T1061")],
                            rows[:1054] + [dict(rows[0])] + rows[1054:]):
                with self.assertRaises(AssertionError):
                    before_b24(source, changed, language)
            for field in ("unknown_column", "sheet_value"):
                changed = [dict(entry) for entry in rows]
                if field == "unknown_column":
                    changed[1054][field] = "unapproved"
                else:
                    changed[1054].pop(field)
                with self.assertRaises(AssertionError):
                    before_b24(source, changed, language)

    def test_h_b29_reconstruction_rejects_every_field_edit(self):
        rows = reg.load_register()
        source = (reg.ROOT / "frontend/src/i18n/en.ts").read_text(encoding="utf-8")
        for index in range(1100, 1401):
            for field in reg.COLUMNS:
                changed = rows.copy()
                changed[index] = dict(rows[index])
                changed[index][field] += " changed"
                with self.subTest(entry=rows[index]["entry_id"], field=field):
                    with self.assertRaisesRegex(AssertionError, "B29:"):
                        before_b29(source, changed)
        for field in ("unknown_column", "sheet_value"):
            changed = [dict(row) for row in rows]
            if field == "unknown_column":
                changed[1100][field] = "unapproved"
            else:
                changed[1100].pop(field)
            with self.assertRaisesRegex(AssertionError, "B29: schema changed"):
                before_b29(source, changed)

    def test_h_b29_reconstruction_rejects_membership_and_source_mutations(self):
        rows = reg.load_register()
        events = b29_events(rows)
        for language in ("FR", "EN"):
            source = (reg.ROOT / f"frontend/src/i18n/{language.lower()}.ts").read_text(encoding="utf-8")
            restored = before_b29(before_b30(before_b32(source, rows, language), rows, language), rows, language)
            self.assertNotEqual(restored, source)
            self.assertEqual(hashlib.sha256(restored.encode("utf-8")).hexdigest(),
                             PRE_B29_CATALOG_SHA256[language])
            # Unregistered additions must remain visible to the historical fingerprint.
            extra = "  unregisteredKey: 'Unapproved',\n"
            self.assertEqual(before_b29(before_b30(before_b32(source + extra, rows, language), rows, language), rows, language),
                             restored + extra)
            for index in range(1100, 1401):
                row = rows[index]
                with self.subTest(language=language, entry=row["entry_id"]):
                    for changed in (
                        rows[:index] + rows[index + 1:],
                        rows + [dict(row, entry_id="T1402")],
                        rows[:index] + [dict(row, batch="B30-2026-11-13")] + rows[index + 1:],
                    ):
                        with self.assertRaises(AssertionError):
                            before_b29(source, changed, language)
                    if row["language"] == language:
                        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                        for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                            with self.assertRaises(AssertionError):
                                before_b29(source.replace(line, replacement), rows, language)
            for index in (1100, 1398, 1399):
                changed = rows.copy()
                changed[index], changed[index + 1] = changed[index + 1], changed[index]
                with self.assertRaises(AssertionError):
                    before_b29(source, changed, language)
            self.assertEqual(len(events), 301)

    def test_h_coordinated_b29_register_and_catalog_edits_are_rejected(self):
        original = reg.load_register()
        # Snapshot real sources once; each mutation still runs the full validator.
        sources = {reg.identity(row): reg.read_source_state(row) for row in original}
        catalogs = {language: (reg.ROOT / path).read_text(encoding="utf-8")
                    for language, path in reg.REACT_CATALOGS.items()}
        for index in range(1100, 1401):
            changed = original.copy()
            row = changed[index] = dict(original[index])
            row["final_value"] += " altered"
            if row["status"] == "ALREADY_MATCHING":
                row["initial_value"] = row["final_value"]
            overrides = {reg.identity(row): row["final_value"]}
            with self.subTest(entry=row["entry_id"]):
                def state(event):
                    key = reg.identity(event)
                    return reg.SourceState(True, overrides[key]) if key in overrides else sources[key]
                self.assertEqual(reg.validate(changed, state), [])
                source = catalogs[row["language"]]
                old_line = f"  {row['key_or_msgid']}: '{original[index]['final_value']}',\n"
                new_line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                edited = source.replace(old_line, new_line)
                self.assertNotEqual(edited, source)
                with self.assertRaisesRegex(AssertionError, "B29: history was modified"):
                    before_b29(edited, changed, row["language"])

    def test_h_b30_b31_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        events = b30_events(rows) + b31_events(rows)
        self.assertEqual(len(events), 58)
        active = {row["key_or_msgid"] for row in b30_events(rows) if row["surface"] == "react"}
        active -= {row["key_or_msgid"] for row in b31_events(rows)}
        self.assertEqual(len(active), 21)
        removed_pairs = {(row["key_or_msgid"], row["language"]) for row in b31_events(rows)}
        for language in ("FR", "EN"):
            source = (reg.ROOT / f"frontend/src/i18n/{language.lower()}.ts").read_text(encoding="utf-8")
            restored = before_b30(source, rows, language)
            self.assertNotEqual(restored, source)
            # Reverse the frozen feature layer, then exactly 21 active DNA keys.
            # Also reverse the three merge separators. The English rewording adds no lines; B31 stays absent.
            self.assertEqual(len(SPECIES_EVIDENCE["catalog_additions"][language.lower()]), 266)
            self.assertEqual(len(source.splitlines()) - len(restored.splitlines()), 266 + 21 + 3)
            for key in active:
                self.assertNotIn(f"  {key}: ", restored)
            self.assertNotIn("  dnaStateValidated: ", restored)
            # Reversing B30/B31 then B29 restores the independently frozen pre-B29 catalog.
            self.assertEqual(hashlib.sha256(before_b29(before_b32(restored, rows, language), rows, language).encode("utf-8")).hexdigest(),
                             PRE_B29_CATALOG_SHA256[language])
            # Unregistered additions must remain visible to the historical fingerprint.
            extra = "  unregisteredKey: 'Unapproved',\n"
            self.assertEqual(before_b30(source + extra, rows, language), restored + extra)
            for index in range(1401, 1459):
                row = rows[index]
                with self.subTest(language=language, entry=row["entry_id"]):
                    for changed in (
                        rows[:index] + rows[index + 1:],
                        rows + [dict(row, entry_id="T1470")],
                        rows[:index] + [dict(row, batch="B99-2026-11-17")] + rows[index + 1:],
                    ):
                        with self.assertRaises(AssertionError):
                            before_b30(source, changed, language)
                    if row["surface"] != "react" or row["language"] != language:
                        continue
                    if row["status"] == "REMOVED" or (row["key_or_msgid"], row["language"]) in removed_pairs:
                        # A key removed by B31 must stay absent: re-adding it is rejected.
                        with self.assertRaises(AssertionError):
                            before_b30(source + f"  {row['key_or_msgid']}: '{row['initial_value']}',\n",
                                       rows, language)
                    else:
                        line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                        for replacement in ("", line + line, line.replace(": '", ": 'changed ")):
                            with self.assertRaises(AssertionError):
                                before_b30(source.replace(line, replacement), rows, language)
            for index in (1401, 1456, 1457):
                changed = rows.copy()
                changed[index], changed[index + 1] = changed[index + 1], changed[index]
                with self.assertRaises(AssertionError):
                    before_b30(source, changed, language)

    def test_h_b23_reconstruction_rejects_history_and_source_mutations(self):
        rows = reg.load_register()
        for language in ("FR", "EN"):
            source = (reg.ROOT / f"frontend/src/i18n/{language.lower()}.ts").read_text(encoding="utf-8")
            before_b23(source, rows, language)
            events = rows[1042:1054]
            for row in events:
                for field in reg.COLUMNS:
                    changed = [dict(entry) for entry in rows]
                    changed[rows.index(row)][field] += " changed"
                    with self.subTest(language=language, entry=row["entry_id"], field=field):
                        with self.assertRaises(AssertionError):
                            before_b23(source, changed, language)
                with self.assertRaises(AssertionError):
                    before_b23(source, [entry for entry in rows if entry != row], language)
                if row["language"] != language:
                    continue
                key = row["key_or_msgid"]
                if row["status"] == "REMOVED":
                    with self.assertRaises(AssertionError):
                        before_b23(source + f"  {key}: '{row['initial_value']}',\n", rows, language)
                else:
                    line = f"  {key}: '{row['final_value']}',\n"
                    for replacement in ("", line.replace(": '", ": 'changed ")):
                        with self.assertRaises(AssertionError):
                            before_b23(source.replace(line, replacement), rows, language)
            reordered = list(rows)
            reordered[1042], reordered[1043] = reordered[1043], reordered[1042]
            with self.assertRaises(AssertionError):
                before_b23(source, reordered, language)
            with self.assertRaises(AssertionError):
                before_b23(source, rows + [dict(events[0], entry_id="T1055")], language)

    def test_h_coordinated_b13_register_and_catalog_edit_is_rejected(self):
        # Reported scenario: rewrite T856 (EN temperatureDailyMean) and the EN catalog together.
        rows = [dict(row) for row in reg.load_register()]
        self.assertEqual(movement_correction_integrity_errors(rows), [])
        self.assertEqual(reg.validate(rows), [], "Current corrections must continue history and match FR/EN sources")
        self.assertEqual(lab_integrity_errors(rows), [])
        self.assertEqual(rows_digest(rows[886:974]),
                         "df3d1228fa718616598ea8bf5c87f2a600007cc94a7514db773abc150960b5c7")
        t856 = next(row for row in rows if row["entry_id"] == "T856")
        self.assertEqual((t856["key_or_msgid"], t856["language"], t856["final_value"]),
                         ("temperatureDailyMean", "EN", "Daily mean"))
        t856.update(initial_value="Daily average", final_value="Daily average")
        # The other register checks accept the coordinated edit...
        self.assertEqual(self.coordinated(rows, {("react", "temperatureDailyMean", "EN"): "Daily average"}), [])
        # ...and so does the historical reconstruction, which removes the edited line itself.
        en = (reg.ROOT / "frontend/src/i18n/en.ts").read_text(encoding="utf-8")
        edited = en.replace("  temperatureDailyMean: 'Daily mean',\n", "  temperatureDailyMean: 'Daily average',\n")
        self.assertNotEqual(edited, en)
        edited = before_b29(before_b30(edited, rows), rows)
        # Undo the latest value changes first, so earlier frozen additions still match the catalog.
        for row in reversed(rows[1098:1100]):
            if row["surface"] == "react" and row["language"] == "EN" and row["status"] == "APPLIED":
                current = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                previous = f"  {row['key_or_msgid']}: '{row['initial_value']}',\n"
                self.assertEqual(edited.count(current), 1, row["entry_id"])
                edited = edited.replace(current, previous)
        edited = before_b23(before_b24(before_b26(edited, rows), rows), rows)
        # Undo later recorded value changes before removing the frozen additions.
        # Reverse order preserves continuity if the same key is corrected more than once.
        # Remove thermal B25 once; movement B20/B21 are removed separately below.
        for row in rows:
            if row["batch"] in (INTEGRATION_B25, B27) and row["language"] == "EN":
                addition = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                self.assertEqual(edited.count(addition), 1, row["entry_id"])
                edited = edited.replace(addition, "")
        # Reverse later changes in reverse order to preserve value continuity.
        for row in reversed(rows[1006:1098]):
            if row["surface"] == "react" and row["language"] == "EN" and row["status"] == "APPLIED":
                current = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                previous = f"  {row['key_or_msgid']}: '{row['initial_value']}',\n"
                self.assertEqual(edited.count(current), 1, row["entry_id"])
                edited = edited.replace(current, previous)
        baseline = edited.replace("  temperatureDailyMean: 'Daily average',\n", "")
        for key in [row["key_or_msgid"] for row in rows[844:886] if row["language"] == "EN"]:
            if key != "temperatureDailyMean":
                value = next(r["final_value"] for r in rows[844:886]
                             if r["language"] == "EN" and r["key_or_msgid"] == key)
                baseline = baseline.replace(f"  {key}: '{value}',\n", "")
        historical_keys = json.loads((reg.ROOT / "docs/translation-review/japanese/historical-movement-keys.json")
                                     .read_text(encoding="utf-8"))
        self.assertEqual(len(historical_keys), 44)
        self.assertEqual(len(set(historical_keys)), 44)
        for key in historical_keys:
            value = next(row["final_value"] for row in rows[886:974]
                         if row["key_or_msgid"] == key and row["language"] == "EN")
            line = f"  {key}: '{value}',\n"
            self.assertEqual(baseline.count(line), 1, key)
            baseline = baseline.replace(line, "")
        # Exclude only the independently frozen incoming lab additions, not arbitrary later events.
        refinement = rows[974:1006]
        self.assertEqual([row["entry_id"] for row in refinement],
                         [f"T{number}" for number in range(975, 1007)])
        self.assertEqual([row for row in rows if row["batch"] == "B17-2026-11-05"], refinement)
        self.assertEqual(rows_digest(refinement),
                         "c9e9c5d39cb1f27fc2bceddd7e40436cc2d79fdc31125ceb17f06d9d48b11134")
        for row in refinement:
            if row["language"] == "EN":
                line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                self.assertEqual(baseline.count(line), 1, row["entry_id"])
                baseline = baseline.replace(line, "")
        # B20 and B21 only add these exact registered lines after the frozen baseline.
        for row in rows:
            if row["batch"] in ("B20-2026-11-09", "B21-2026-11-09") and row["language"] == "EN":
                line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                self.assertEqual(baseline.count(line), 1, row["entry_id"])
                baseline = baseline.replace(line, "")
        # B32 only adds these exact registered four-hour monitoring lines after the frozen baseline.
        for row in b32_events(rows):
            if row["language"] == "EN":
                line = f"  {row['key_or_msgid']}: '{row['final_value']}',\n"
                self.assertEqual(baseline.count(line), 1, row["entry_id"])
                baseline = baseline.replace(line, "")
        self.assertEqual(hashlib.sha256(baseline.encode()).hexdigest(),
                         "62989721d2528126ab48704fe783597dba0d484d2f24ce05a7391e0e9bdbfa27")
        # Only the independent B13 fingerprint detects the rewrite.
        self.assertIn("B13 history was modified", b13_integrity_errors(rows))

    def test_h_lab_provenance_is_independent_of_the_thermal_freeze(self):
        rows = reg.load_register()
        self.assertEqual(lab_integrity_errors(rows), [])
        self.assertEqual(rows_digest(rows[B13_POSITION]), B13_ROWS_SHA256)
        self.assertEqual(Counter(row["status"] for row in rows[LAB_POSITION]),
                         Counter({"DEFERRED": 26, "ALREADY_MATCHING": 2}))
        for index in range(LAB_POSITION.start, LAB_POSITION.stop):
            for name in reg.COLUMNS:
                with self.subTest(entry=rows[index]["entry_id"], field=name):
                    changed = [dict(row) for row in rows]
                    changed[index][name] += "x"
                    self.assertTrue(lab_integrity_errors(changed))
        self.assertTrue(lab_integrity_errors(rows[:858]))
        self.assertTrue(lab_integrity_errors(rows[:858] + rows[859:]))
        self.assertTrue(lab_integrity_errors(rows[:858] + [rows[859], rows[858]] + rows[860:]))
        self.assertTrue(lab_integrity_errors(rows + [dict(rows[LAB_POSITION.start])]))

    def test_h_every_b13_field_edit_is_rejected(self):
        original = reg.load_register()
        for index in range(B13_POSITION.start, B13_POSITION.stop):
            for name in reg.COLUMNS:
                with self.subTest(entry=original[index]["entry_id"], field=name):
                    rows = [dict(row) for row in original]
                    rows[index][name] += "x"
                    self.assertTrue(b13_integrity_errors(rows))

    def test_h_b13_deletion_insertion_replacement_and_reordering_are_rejected(self):
        original = reg.load_register()
        b13 = original[B13_POSITION]
        later = dict(b13[-1], entry_id="T859", batch="B14-2026-11-05")
        inserted = dict(b13[0], entry_id="T845b")
        replacement = dict(b13[3], key_or_msgid="temperatureOther")
        variants = {
            "deleted": original[:844] + b13[1:],
            "deleted last": original[:857],
            "inserted inside": original[:845] + [inserted] + original[845:],
            "row before B13": original[:844] + [later] + original[844:],
            "replaced": original[:847] + [replacement] + original[848:],
            "reordered": original[:844] + [b13[1], b13[0]] + b13[2:],
            "renumbered": original[:844] + [dict(row, entry_id=f"T{900 + i}") for i, row in enumerate(b13)],
            "moved to another batch": original[:844] + [dict(row, batch="B14-2026-11-05") for row in b13],
        }
        for label, rows in variants.items():
            with self.subTest(label):
                self.assertTrue(b13_integrity_errors(rows))

    def test_h_corrections_after_b13_are_appended_events(self):
        rows = reg.load_register()
        t856 = rows[855]
        next_entry = max(int(row["entry_id"][1:]) for row in rows) + 1
        batches = [reg.BATCH_ID.fullmatch(row["batch"]) for row in rows]
        next_batch = max(int(batch.group(1)) for batch in batches) + 1
        batch_date = max(batch.group(2) for batch in batches)
        correction = dict(t856, entry_id=f"T{next_entry:03d}", batch=f"B{next_batch:02d}-{batch_date}", initial_value="Daily mean",
                          final_value="Daily average", status="APPLIED", decision_ref="synthetic",
                          baseline_git_head=HEAD_2)
        appended = rows + [correction]
        self.assertEqual(b13_integrity_errors(appended), [])
        self.assertEqual(movement_correction_integrity_errors(appended), [])
        self.assertEqual(self.coordinated(appended, {("react", "temperatureDailyMean", "EN"): "Daily average"}), [])
        for _, position, _, _ in MOVEMENT_CORRECTION_FREEZES:
            for protected in rows[position]:
                with self.subTest(appended_correction=protected["entry_id"]):
                    latest = next(row for row in reversed(rows) if reg.identity(row) == reg.identity(protected))
                    later = dict(protected, entry_id=correction["entry_id"], batch=correction["batch"],
                                 initial_value=latest["final_value"], final_value=latest["final_value"] + " corrected",
                                 baseline_git_head=HEAD_2)
                    self.assertEqual(movement_correction_integrity_errors(rows + [later]), [])
                    overrides = {(later["surface"], later["key_or_msgid"], later["language"]): later["final_value"]}
                    self.assertEqual(self.coordinated(rows + [later], overrides), [])
        for label, (invalid, reason) in {
            "duplicate": (appended + [correction], "duplicate entry_id"),
            "reordered": (rows[:-1] + [correction, rows[-1]], "entry_id not strictly increasing in file order"),
            "contradictory": (rows + [dict(correction, initial_value="Not the previous value")], "does not continue"),
        }.items():
            with self.subTest(label):
                errors = self.coordinated(invalid, {("react", "temperatureDailyMean", "EN"): "Daily average"})
                self.assertTrue(any(reason in error for error in errors), errors)

    def test_h_corrupted_copy_of_b01_is_rejected(self):
        rows = reg.load_register()
        corrupted = [dict(row) for row in rows]
        corrupted[0]["final_value"] = corrupted[0]["initial_value"]
        self.assertTrue(reg.validate(corrupted))
        self.assertNotEqual(rows_digest(corrupted[:34]), B01_ROWS_SHA256)

    def test_h_valid_b02_can_be_appended_without_touching_b01(self):
        rows = [row for row in reg.load_register() if row["batch"] == B01]
        self.assertEqual(len(rows), 34)
        last = next(row for row in rows if row["key_or_msgid"] == "recentAccess")
        b02 = dict(last, entry_id="T035", batch=B02, initial_value=last["final_value"],
                   final_value=last["final_value"], status="ALREADY_MATCHING", baseline_git_head=HEAD_2)
        appended = rows + [b02]
        self.assertEqual(rows_digest(appended[:34]), B01_ROWS_SHA256)
        historical_sources = lambda row: reg.SourceState(True, row["final_value"])
        self.assertEqual(reg.validate(appended, historical_sources), [])
        broken = dict(b02, initial_value="Recent access")
        self.assertTrue(any("does not continue" in e
                            for e in reg.validate(rows + [broken], historical_sources)))
        b03 = dict(b02, entry_id="T036", batch="B03-2026-11-06")
        self.assertEqual(reg.validate(appended + [b03], historical_sources), [])
        self.assertEqual(rows_digest((appended + [b03])[:34]), B01_ROWS_SHA256)


if __name__ == "__main__":
    unittest.main()
