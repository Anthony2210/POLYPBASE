"""Exact post-plan additions; no database access or historical-document mutation."""

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

ADDITIONS = {
    104688: (1836, "2026-10-21T09:37:07.809036+00:00"),
    104689: (1837, "2026-10-21T09:44:03.511877+00:00"),
}


def validate_snapshot(plan, snapshot):
    """Bind every normalized scientific baseline field to the complete raw snapshot."""
    groups = (
        ("measurements", plan["baseline"]["measurements"], {"created_at"}, {"salinity_psu"}),
        ("boxes", plan["baseline"]["boxes"], set(), {"volume_liters"}),
        ("locations", plan["baseline"]["location_context"]["locations"], {"starts_at", "ends_at"}, set()),
        ("movements", plan["baseline"]["location_context"]["movements"], {"moved_at"}, set()),
    )
    def fail():
        raise ValueError("Scientific baseline differs from the complete raw CCA-EOB snapshot.")
    try:
        for name, rows, timestamps, decimals in groups:
            raw_rows = snapshot[name]
            if len(rows) != len(raw_rows):
                fail()
            for row, raw in zip(rows, raw_rows):
                if set(row) != set(raw):
                    fail()
                for key in row:
                    a, b = row[key], raw[key]
                    if a is None or b is None:
                        if a is not b:
                            fail()
                    elif key in timestamps:
                        if datetime.fromisoformat(a) != datetime.fromisoformat(b):
                            fail()
                    elif key in decimals:
                        if Decimal(str(a)) != Decimal(str(b)):
                            fail()
                    elif type(a) is not type(b) or a != b:
                        fail()
        context = plan["baseline"]["location_context"]
        for key in ("zones", "external_movement_links"):
            if context[key] != snapshot[key]:
                fail()
        histories = plan["baseline"]["histories"]
        if histories == dict.fromkeys(("subculture_events", "subculture_allocations", "lineages", "transfer_items"), []):
            histories = dict.fromkeys(histories, False)
        if histories != snapshot["histories"]:
            fail()
    except (KeyError, TypeError, ValueError, ArithmeticError):
        fail()


def baseline_sql():
    """Typed record conversion mirrors ORM normalization without dropping columns."""
    checks = []
    for name, table, path in (
        ("measurements", "measurements_biologicalmeasurement", "{baseline,measurements}"),
        ("boxes", "cultures_box", "{baseline,boxes}"),
        ("locations", "cultures_boxlocation", "{baseline,location_context,locations}"),
        ("movements", "cultures_boxmovement", "{baseline,location_context,movements}"),
    ):
        checks.append(f"""
        IF (SELECT coalesce(jsonb_agg(to_jsonb(jsonb_populate_record(NULL::public.{table},b)) ORDER BY ord),'[]'::jsonb)
            FROM jsonb_array_elements(correction#>'{path}') WITH ORDINALITY x(b,ord)) IS DISTINCT FROM state->'{name}'
            OR EXISTS (SELECT 1 FROM jsonb_array_elements(correction#>'{path}') b
                WHERE (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(b) k) IS DISTINCT FROM
                    (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(to_jsonb(jsonb_populate_record(NULL::public.{table},b))) k)) THEN
            RAISE EXCEPTION 'Complete CCA-EOB {name} baseline differs from locked raw snapshot';
        END IF;
""")
    return "".join(checks) + """
        IF correction#>'{baseline,histories}' IS DISTINCT FROM state->'histories'
            OR correction#>'{baseline,location_context,zones}' IS DISTINCT FROM state->'zones'
            OR correction#>'{baseline,location_context,external_movement_links}' IS DISTINCT FROM state->'external_movement_links' THEN
            RAISE EXCEPTION 'Complete CCA-EOB relationship baseline differs from locked raw snapshot';
        END IF;
"""


def revisions(plan):
    moves = {e["measurement_pk"]: e["target_box_pk"] for e in plan["entries"]
             if e["classification"] == "REASSIGN_REQUIRED"}
    result = []
    for box in plan["baseline"]["boxes"]:
        touched = sum(m["id"] in moves and (m["box_id"] == box["id"] or moves[m["id"]] == box["id"])
                      for m in plan["baseline"]["measurements"])
        if touched:
            sequences = [m["polyp_state_sequence"] for m in plan["baseline"]["measurements"]
                         if moves.get(m["id"], m["box_id"]) == box["id"] and m["polyp_state_sequence"] is not None]
            result.append({"box_id": box["id"], "before": box["polyp_state_revision"],
                           "after": max([box["polyp_state_revision"] + touched, *sequences])})
    return result


def project(original, current, snapshot, digest, pinned_hash):
    """Remove only the authenticated delta for comparison, never from persisted data."""
    def fail():
        raise ValueError("Exact authenticated CCA-EOB additions, creation audits and revisions required.")

    if digest != pinned_hash or type(snapshot) is not dict:
        fail()
    try:
        if original["counts"] != {"REASSIGN_REQUIRED": 72, "ALREADY_MATCHES": 8, "OUT_OF_SOURCE": 4}:
            fail()
        if current["counts"] != {"REASSIGN_REQUIRED": 72, "ALREADY_MATCHES": 8, "OUT_OF_SOURCE": 6}:
            fail()
        if current["revision_changes"] != revisions(current) or original["revision_changes"] != revisions(original):
            fail()
        rows = current["baseline"]["measurements"]
        old_ids = [m["id"] for m in original["baseline"]["measurements"]]
        ids = [m["id"] for m in rows]
        if len(set(ids)) != len(ids) or set(ids) - set(old_ids) != set(ADDITIONS) or not set(old_ids) <= set(ids):
            fail()
        audits = snapshot["addition_audits"]
        if len(audits) != 2 or set(ADDITIONS) & set(snapshot["historical_linked_measurement_ids"]):
            fail()
        for pk, (box_id, timestamp) in ADDITIONS.items():
            row, = [m for m in rows if m["id"] == pk]
            entry, = [e for e in current["entries"] if e["measurement_pk"] == pk]
            audit, = [a for a in audits if a["metadata"].get("measurement_id") == pk]
            raw, = [m for m in snapshot["measurements"] if m["id"] == pk]
            ordinary = set(row) - {"created_at", "salinity_psu"}
            if (set(raw) != set(row) or any(raw[k] != row[k] for k in ordinary)
                    or datetime.fromisoformat(raw["created_at"]) != datetime.fromisoformat(row["created_at"])
                    or (None if raw["salinity_psu"] is None else Decimal(str(raw["salinity_psu"])))
                    != (None if row["salinity_psu"] is None else Decimal(str(row["salinity_psu"])))):
                fail()
            measured_on = date.fromisoformat(row["measured_on"])
            if (entry["classification"] != "OUT_OF_SOURCE" or entry["before"] != row
                    or row["box_id"] != box_id or row["week_start"] != "2026-10-19"
                                        or (measured_on - timedelta(days=measured_on.weekday())).isoformat() != row["week_start"]
                    or datetime.fromisoformat(row["created_at"]).astimezone(timezone.utc) != datetime.fromisoformat(timestamp)
                    or row["origin"] != "direct_entry" or row["temporal_precision"] != "exact_date"
                    or row["polyp_state_sequence"] != 25 or row["observer_status"] != "known"
                    or row["user_id"] is None or row["original_observer_id"] != row["user_id"]
                                        or row["user_id"] not in snapshot["attribution_user_ids"]
                    or row["imported_by_id"] is not None or row["import_format"] != ""
                    or row["duplicate_of_id"] is not None or row["environment_source_box_id"] is not None):
                fail()
            values = {"date": row["measured_on"], "polypes": row["polyp_count"],
                      "ephyrules": row["ephyrae_count"], "strobiles": row["strobila_count"],
                      "salinite_psu": None if row["salinity_psu"] is None else str(Decimal(row["salinity_psu"]).quantize(Decimal("0.01"))),
                      "statut_culture": row["culture_status"], "a_verifier": row["needs_attention"], "note": row["notes"]}
            if (audit["organization_id"] != 1 or audit["user_id"] != row["user_id"]
                    or audit["action"] != "entry" or audit["object_type"] != "box"
                    or audit["object_id"] != f"CCA-EOB-2.{box_id - 1831:03}"
                    or audit["metadata"] != {"measurement_id": pk, "valeurs": values}
                    or audit["edited_at"] is not None or audit["edited_by_id"] is not None
                    or audit["original_values"] != {}
                    or datetime.fromisoformat(audit["created_at"]) < datetime.fromisoformat(row["created_at"])):
                fail()
        projected = deepcopy(current)
        projected["baseline"]["measurements"] = [m for m in rows if m["id"] not in ADDITIONS]
        projected["entries"] = [e for e in current["entries"] if e["measurement_pk"] not in ADDITIONS]
        projected["counts"]["OUT_OF_SOURCE"] = 4
        for box_id in (1836, 1837):
            old, = [b for b in original["baseline"]["boxes"] if b["id"] == box_id]
            box, = [b for b in projected["baseline"]["boxes"] if b["id"] == box_id]
            if old["polyp_state_revision"] != 24 or box["polyp_state_revision"] != 25:
                fail()
            box["polyp_state_revision"] = 24
        projected["revision_changes"] = revisions(projected)
        return projected
    except (KeyError, TypeError, ValueError, ArithmeticError):
        fail()


# This is executed inline by the definer writer after it acquires its existing
# locks. Audit SHARE additionally excludes edits and phantom audit events until
# the correction's own audit INSERTs (the same transaction) have completed.
SQL = """
        IF historical_plan IS DISTINCT FROM correction THEN
            IF (doc#>>'{plan,thermal_exception,original_plan_hash}'='__ADDITIVE_HASH__'
                AND historical_plan->'counts'='{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":4}'::jsonb
                AND correction->'counts'='{"REASSIGN_REQUIRED":72,"ALREADY_MATCHES":8,"OUT_OF_SOURCE":6}'::jsonb
                AND jsonb_array_length(state->'addition_audits')=2
                AND NOT EXISTS (SELECT 1 FROM public.measurements_historicaltemperaturereading
                    WHERE biological_measurement_id IN (104688,104689))) IS NOT TRUE THEN
                RAISE EXCEPTION 'Exact authenticated CCA-EOB additive delta required';
            END IF;
            FOR pk, box_pk, addition_time IN VALUES
                (104688::bigint,1836::bigint,'2026-10-21T09:37:07.809036Z'::timestamptz),
                (104689::bigint,1837::bigint,'2026-10-21T09:44:03.511877Z'::timestamptz) LOOP
                SELECT b INTO STRICT raw FROM jsonb_array_elements(state->'measurements') b WHERE (b->>'id')::bigint=pk;
                SELECT b INTO STRICT reviewed_raw FROM jsonb_array_elements(correction#>'{baseline,measurements}') b
                    WHERE (b->>'id')::bigint=pk;
                SELECT a INTO STRICT audit FROM jsonb_array_elements(state->'addition_audits') a
                    WHERE a#>'{metadata,measurement_id}'=to_jsonb(pk);
                SELECT e INTO STRICT entry FROM jsonb_array_elements(correction->'entries') e
                    WHERE (e->>'measurement_pk')::bigint=pk;
                IF (raw->'box_id'=to_jsonb(box_pk) AND raw->>'week_start'='2026-10-19'
                                    AND date_trunc('week',(raw->>'measured_on')::date)::date=(raw->>'week_start')::date
                    AND (raw->>'created_at')::timestamptz=addition_time
                    AND raw->>'origin'='direct_entry' AND raw->>'temporal_precision'='exact_date'
                    AND raw->'polyp_state_sequence'='25'::jsonb AND raw->>'observer_status'='known'
                    AND raw->'user_id'<>'null'::jsonb AND raw->'original_observer_id'=raw->'user_id'
                    AND raw->'imported_by_id'='null'::jsonb AND raw->>'import_format'=''
                    AND raw->'duplicate_of_id'='null'::jsonb AND raw->'environment_source_box_id'='null'::jsonb
                    AND entry->>'classification'='OUT_OF_SOURCE' AND entry->'before'=reviewed_raw
                    AND raw-'created_at'-'salinity_psu'=reviewed_raw-'created_at'-'salinity_psu'
                    AND (reviewed_raw->>'created_at')::timestamptz=addition_time
                    AND (raw->>'salinity_psu')::numeric IS NOT DISTINCT FROM (reviewed_raw->>'salinity_psu')::numeric
                    AND EXISTS (SELECT 1 FROM public.accounts_organizationmembership
                        WHERE organization_id=1 AND user_id=(raw->>'user_id')::bigint)
                    AND audit->'organization_id'='1'::jsonb AND audit->'user_id'=raw->'user_id'
                    AND audit->>'action'='entry' AND audit->>'object_type'='box'
                    AND audit->>'object_id'=CASE box_pk WHEN 1836 THEN 'CCA-EOB-2.005' ELSE 'CCA-EOB-2.006' END
                    AND audit->'edited_at'='null'::jsonb AND audit->'edited_by_id'='null'::jsonb
                    AND audit->'original_values'='{}'::jsonb
                    AND (audit->>'created_at')::timestamptz >= (raw->>'created_at')::timestamptz
                    AND audit->'metadata'=jsonb_build_object('measurement_id',pk,'valeurs',jsonb_build_object(
                        'date',raw->'measured_on','polypes',raw->'polyp_count','ephyrules',raw->'ephyrae_count',
                        'strobiles',raw->'strobila_count','salinite_psu',CASE WHEN raw->'salinity_psu'='null'::jsonb THEN NULL
                            ELSE to_char((raw->>'salinity_psu')::numeric,'FM999990.00') END,
                        'statut_culture',raw->'culture_status','a_verifier',raw->'needs_attention','note',raw->'notes'))
                    AND NOT EXISTS (SELECT 1 FROM jsonb_array_elements(historical_plan#>'{baseline,measurements}') b
                        WHERE (b->>'id')::bigint=pk)
                    AND (SELECT b->'polyp_state_revision' FROM jsonb_array_elements(historical_plan#>'{baseline,boxes}') b
                        WHERE (b->>'id')::bigint=box_pk)='24'::jsonb
                    AND (SELECT b->'polyp_state_revision' FROM jsonb_array_elements(correction#>'{baseline,boxes}') b
                        WHERE (b->>'id')::bigint=box_pk)='25'::jsonb) IS NOT TRUE THEN
                    RAISE EXCEPTION 'CCA-EOB addition provenance, audit, thermal state or revision differs';
                END IF;
            END LOOP;
            -- Preserve array ordering and every unrelated field in this projection.
            projected := jsonb_set(correction,'{baseline,measurements}',
                (SELECT jsonb_agg(b ORDER BY ord) FROM jsonb_array_elements(correction#>'{baseline,measurements}') WITH ORDINALITY x(b,ord)
                    WHERE (b->>'id')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,'{entries}',
                (SELECT jsonb_agg(e ORDER BY ord) FROM jsonb_array_elements(correction->'entries') WITH ORDINALITY x(e,ord)
                    WHERE (e->>'measurement_pk')::bigint NOT IN (104688,104689)));
            projected := jsonb_set(projected,'{counts,OUT_OF_SOURCE}','4'::jsonb);
            projected := jsonb_set(projected,'{baseline,boxes}',
                (SELECT jsonb_agg(CASE WHEN (b->>'id')::bigint IN (1836,1837)
                    THEN jsonb_set(b,'{polyp_state_revision}','24'::jsonb) ELSE b END ORDER BY ord)
                 FROM jsonb_array_elements(projected#>'{baseline,boxes}') WITH ORDINALITY x(b,ord)));
            IF (SELECT jsonb_agg(r->'box_id' ORDER BY ord) FROM jsonb_array_elements(correction->'revision_changes') WITH ORDINALITY x(r,ord))
                IS DISTINCT FROM (SELECT jsonb_agg(r->'box_id' ORDER BY ord) FROM jsonb_array_elements(historical_plan->'revision_changes') WITH ORDINALITY x(r,ord)) THEN
                RAISE EXCEPTION 'CCA-EOB revision identities/order changed';
            END IF;
            -- The existing writer below recomputes every current revision against
            -- raw rows and touched counts before writing. Historical values stay authenticated.
            projected := jsonb_set(projected,'{revision_changes}',historical_plan->'revision_changes');
            IF projected IS DISTINCT FROM historical_plan THEN
                RAISE EXCEPTION 'CCA-EOB historical scientific identity changed';
            END IF;
        END IF;
"""
