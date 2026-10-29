"""Read-only client for the iMinilide cloud (www.iminilide.com).

The cloud has no documented API. This client reproduces what its web page does:
it signs in with the account form, then asks for the readings of one channel
since a given local time::

    GET {base}/en/mesuresjson_<serial>_<channel>_<YYYYMMDDHHMMSS>

The answer is a JSON list of ``["YYYY/MM/DD HH:MM:SS", value]`` pairs, one per
minute, in the local time of the logger and without any UTC offset.

Only the standard library is used, so no production dependency is added.
"""

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal, InvalidOperation
from http.cookiejar import CookieJar
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPCookieProcessor, Request, build_opener
from zoneinfo import ZoneInfo

from django.conf import settings

LOGIN_PATH = "/en/"
READINGS_PATH = "/en/mesuresjson_{serial}_{channel}_{since}"
USER_AGENT = "Polypbase-collector/1.0 (+https://polypbase.org)"
TIMESTAMP_FORMAT = "%Y/%m/%d %H:%M:%S"
SINCE_FORMAT = "%Y%m%d%H%M%S"
SERIAL_PATTERN = re.compile(r"^\d{4,20}$")

# Measuring range documented for iMinilide-A loggers. A value outside it is not
# a temperature this logger can measure, so it is skipped, never stored.
MIN_PLAUSIBLE_C = Decimal("-100")
MAX_PLAUSIBLE_C = Decimal("250")


class IminilideError(Exception):
    """Base error of the iMinilide client."""


class IminilideConfigurationError(IminilideError):
    """Credentials or settings are missing."""


class IminilideAuthenticationError(IminilideError):
    """The cloud did not accept the session."""


class IminilideResponseError(IminilideError):
    """The cloud answered with something that is not a list of readings."""


@dataclass(frozen=True)
class Reading:
    measured_at: datetime  # aware
    temperature_c: Decimal


@dataclass
class ParsedReadings:
    readings: list
    missing_values: int = 0
    out_of_range_values: int = 0
    invalid_rows: int = 0


def format_since(moment, time_zone=None):
    """Return the local ``YYYYMMDDHHMMSS`` string expected by the cloud."""
    zone = ZoneInfo(time_zone or settings.IMINILIDE_TIME_ZONE)
    return moment.astimezone(zone).strftime(SINCE_FORMAT)


def _parse_value(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", ".")
        if not value:
            return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("not a number") from None
    if not number.is_finite():
        raise ValueError("not a finite number")
    return number


def _possible_instants(naive, zone):
    """Return valid UTC instants, excluding nonexistent spring-transition times."""
    instants = set()
    for fold in (0, 1):
        utc = naive.replace(tzinfo=zone, fold=fold).astimezone(datetime_timezone.utc)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            instants.add(utc)
    return sorted(instants)


def collection_window_start(moment, time_zone=None):
    """Request the whole fallback-transition day, including after its repeated hour."""
    zone = ZoneInfo(time_zone or settings.IMINILIDE_TIME_ZONE)
    local = moment.astimezone(zone)
    start = datetime.combine(local.date(), time.min, tzinfo=zone)
    end = datetime.combine(local.date() + timedelta(days=1), time.min, tzinfo=zone)
    if start.utcoffset() > end.utcoffset():
        return start
    return moment


def parse_readings(payload, time_zone=None):
    """Parse readings without guessing an ambiguous local time's occurrence.

    Accept both occurrences only when the response contains the strict backwards
    jump. Otherwise count ambiguous rows as invalid. Missing values participate
    in detecting the jump; zero remains a real measurement.
    """
    if not isinstance(payload, list):
        raise IminilideResponseError("The cloud answer is not a list of readings.")
    zone = ZoneInfo(time_zone or settings.IMINILIDE_TIME_ZONE)
    result = ParsedReadings(readings=[])
    rows = []
    rollbacks = defaultdict(list)
    previous = None
    for index, row in enumerate(payload):
        if not isinstance(row, (list, tuple)) or len(row) < 2 or not isinstance(row[0], str):
            result.invalid_rows += 1
            continue
        try:
            naive = datetime.strptime(row[0].strip(), TIMESTAMP_FORMAT)
        except ValueError:
            result.invalid_rows += 1
            continue
        instants = _possible_instants(naive, zone)
        if len(instants) == 2 and previous is not None:
            previous_naive, previous_instants = previous
            if len(previous_instants) == 2 and naive.date() == previous_naive.date() and naive < previous_naive:
                rollbacks[naive.date()].append(index)
        previous = naive, instants
        rows.append((index, naive, instants, row[1]))
    for index, naive, instants, raw_value in rows:
        if not instants:
            result.invalid_rows += 1
            continue
        if len(instants) == 2:
            jumps = rollbacks[naive.date()]
            if len(jumps) != 1:
                result.invalid_rows += 1
                continue
            instant = instants[1 if index >= jumps[0] else 0]
        else:
            instant = instants[0]
        try:
            value = _parse_value(raw_value)
        except ValueError:
            result.invalid_rows += 1
            continue
        if value is None:
            result.missing_values += 1
            continue
        if value < MIN_PLAUSIBLE_C or value > MAX_PLAUSIBLE_C:
            result.out_of_range_values += 1
            continue
        result.readings.append(Reading(measured_at=instant.astimezone(zone), temperature_c=value))
    return result


class IminilideClient:
    """One authenticated browsing session on the iMinilide cloud."""

    def __init__(self, *, email=None, password=None, base_url=None, timeout=None, opener=None):
        self.email = settings.IMINILIDE_EMAIL if email is None else email
        self.password = settings.IMINILIDE_PASSWORD if password is None else password
        self.base_url = (base_url or settings.IMINILIDE_BASE_URL).rstrip("/")
        self.timeout = timeout or settings.IMINILIDE_TIMEOUT_SECONDS
        if not self.email or not self.password:
            raise IminilideConfigurationError(
                "IMINILIDE_EMAIL and IMINILIDE_PASSWORD must be set in the environment."
            )
        self._opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))
        self._signed_in = False

    def _open(self, request):
        request.add_header("User-Agent", USER_AGENT)
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                return response.read()
        except HTTPError as error:
            raise IminilideError(f"The cloud answered HTTP {error.code}.") from None
        except (URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise IminilideError(f"The cloud could not be reached: {reason}.") from None

    def sign_in(self):
        body = urlencode(
            {"action": "se_connecter", "email": self.email, "mdp": self.password}
        ).encode("utf-8")
        request = Request(
            f"{self.base_url}{LOGIN_PATH}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self._open(request)
        self._signed_in = True

    def _get_json(self, path):
        raw = self._open(Request(f"{self.base_url}{path}", method="GET"))
        try:
            return json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None

    def fetch_readings(self, *, serial, channel, since):
        """Return parsed readings of one channel since an aware datetime."""
        serial = str(serial).strip()
        if not SERIAL_PATTERN.match(serial):
            raise IminilideError("Invalid logger serial number.")
        channel = int(channel)
        if channel < 1 or channel > 99:
            raise IminilideError("Invalid channel number.")
        path = READINGS_PATH.format(serial=serial, channel=channel, since=format_since(since))

        if not self._signed_in:
            self.sign_in()
        payload = self._get_json(path)
        if not isinstance(payload, list):
            # An expired or refused session returns an HTML page: sign in once more.
            self.sign_in()
            payload = self._get_json(path)
            if not isinstance(payload, list):
                raise IminilideAuthenticationError(
                    "The cloud did not return readings. Check the account credentials "
                    "and that this logger is visible on the account."
                )
        return parse_readings(payload)
