"""Bounded, read-only client for the official WoRMS REST API.

WoRMS is an assistant for Species identification only. Its records never
create or modify local data by themselves, and it is not an authority for
strain identities, provenance, AAA/BBB codes, collection origin, acquisition
or DNA certification. See https://www.marinespecies.org/rest/.
"""

import json
import re
import socket
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from django.conf import settings

DEFAULT_BASE_URL = "https://www.marinespecies.org/rest"
# One bounded attempt, no retry. Observed latency is mostly under 3 s, but a cold
# genus-wide lookup reached 6.2 s, past the former 6 s limit.
DEFAULT_TIMEOUT_SECONDS = 10
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RESULTS = 25
# Official AphiaTaxonRanksByID/0, checked 2026-11-02. IDs are not a range.
# Require both fields: missing, unknown or contradictory ranks fail closed.
SPECIES_LEVEL_RANKS = {
    220: "species",
    230: "subspecies",
    240: "variety",
    250: "subvariety",
    260: "forma",
    270: "subforma",
    280: "mutatio",
}
MIN_QUERY_LENGTH = 3
MAX_QUERY_LENGTH = 120
# Latin binomials, subgenera, "sp." and hybrid markers. No wildcard characters:
# the official "like" parameter already appends one.
QUERY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z .()'×-]*$")


class WormsError(Exception):
    code = "worms_unavailable"


class WormsUnavailable(WormsError):
    code = "worms_unavailable"


class WormsInvalidResponse(WormsError):
    code = "worms_invalid_response"


class WormsReferenceRejected(Exception):
    """WoRMS answered, and the submitted AphiaID does not identify this species."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def normalize_query(value):
    """Return a safe scientific-name query, or None when it must be rejected."""
    if not isinstance(value, str):
        return None
    query = " ".join(value.split())
    if not MIN_QUERY_LENGTH <= len(query) <= MAX_QUERY_LENGTH:
        return None
    if not QUERY_PATTERN.fullmatch(query):
        return None
    return query


def _base_url():
    return getattr(settings, "WORMS_REST_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout():
    return getattr(settings, "WORMS_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)


def _fetch_json(url):
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Polypbase"})
    try:
        with urlopen(request, timeout=_timeout()) as response:  # noqa: S310 - fixed https base URL
            status = getattr(response, "status", 200)
            if status == 204:
                return None
            if status != 200:
                raise WormsUnavailable()
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        # WoRMS answers 204 for "no match"; some deployments surface it as an error.
        if error.code == 204:
            return None
        raise WormsUnavailable() from error
    except (URLError, HTTPException, socket.timeout, TimeoutError, OSError) as error:
        raise WormsUnavailable() from error
    if len(body) > MAX_RESPONSE_BYTES:
        raise WormsInvalidResponse()
    if not body.strip():
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WormsInvalidResponse() from error


def _text(record, key, max_length=255):
    value = record.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise WormsInvalidResponse()
    return value.strip()[:max_length]


def _positive_int(record, key, *, required=False):
    value = record.get(key)
    if value is None and not required:
        return None
    # bool is an int subclass and must not pass as an identifier.
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > 2147483647:
        if required:
            raise WormsInvalidResponse()
        return None
    return value


def is_species_level(rank_id, rank):
    """True for Species and infraspecific records; genus or higher, or unknown, is not."""
    return rank_id in SPECIES_LEVEL_RANKS and SPECIES_LEVEL_RANKS[rank_id] == rank.lower()


def parse_record(record):
    """Keep only the documented AphiaRecord fields that Polypbase displays."""
    if not isinstance(record, dict):
        raise WormsInvalidResponse()
    aphia_id = _positive_int(record, "AphiaID", required=True)
    scientific_name = _text(record, "scientificname", 150)
    if not scientific_name:
        raise WormsInvalidResponse()
    valid_aphia_id = _positive_int(record, "valid_AphiaID")
    rank = _text(record, "rank", 80)
    rank_id = _positive_int(record, "taxonRankID")
    return {
        "aphia_id": aphia_id,
        "scientific_name": scientific_name,
        "authority": _text(record, "authority"),
        "status": _text(record, "status", 40),
        "unaccept_reason": _text(record, "unacceptreason"),
        "rank": rank,
        "rank_id": rank_id,
        "is_species_level": is_species_level(rank_id, rank),
        "valid_aphia_id": valid_aphia_id,
        "valid_name": _text(record, "valid_name", 150) if valid_aphia_id else "",
        "valid_authority": _text(record, "valid_authority") if valid_aphia_id else "",
        "kingdom": _text(record, "kingdom", 80),
        "phylum": _text(record, "phylum", 80),
        "class_name": _text(record, "class", 80),
        "order": _text(record, "order", 80),
        "family": _text(record, "family", 80),
    }


def search_by_name(query):
    """Search records whose scientific name starts with the normalized query.

    Return the first MAX_RESULTS parsed records and whether WoRMS returned more.
    Only the first upstream page is read; a truncated list is never exhaustive.
    """
    url = (
        f"{_base_url()}/AphiaRecordsByName/{quote(query, safe='')}?"
        + urlencode({"like": "true", "marine_only": "false", "offset": 1})
    )
    payload = _fetch_json(url)
    if payload is None:
        return [], False
    if not isinstance(payload, list):
        raise WormsInvalidResponse()
    return [parse_record(record) for record in payload[:MAX_RESULTS]], len(payload) > MAX_RESULTS


def get_record(aphia_id):
    """Fetch one AphiaRecord by identifier, or None when WoRMS has no such record."""
    payload = _fetch_json(f"{_base_url()}/AphiaRecordByAphiaID/{int(aphia_id)}")
    if payload is None:
        return None
    return parse_record(payload)


def verify_species_reference(aphia_id, scientific_name):
    """Confirm with WoRMS that aphia_id names this species-level taxon.

    The submitted name must be the record's own scientific name, so an explicit
    choice of an unaccepted name or of its accepted name is kept as chosen.
    Raises WormsReferenceRejected when WoRMS answers that the reference does not
    fit, and WormsError when WoRMS cannot confirm it (never treated as valid).
    """
    record = get_record(aphia_id)
    if record is None:
        raise WormsReferenceRejected("worms_reference_not_found")
    if record["aphia_id"] != aphia_id:
        raise WormsInvalidResponse()
    if not record["is_species_level"]:
        raise WormsReferenceRejected("worms_reference_rank")
    if " ".join(record["scientific_name"].split()) != scientific_name:
        raise WormsReferenceRejected("worms_reference_name_mismatch")
    return record


def record_url(aphia_id):
    return f"https://www.marinespecies.org/aphia.php?p=taxdetails&id={int(aphia_id)}"
