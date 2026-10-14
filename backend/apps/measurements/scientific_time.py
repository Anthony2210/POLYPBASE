"""Pure observation chronology, independent of persistence and source consumption."""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import Enum


class ObservationPrecision(str, Enum):
    EXACT_DATE = "exact_date"
    LEGACY_UNVERIFIED = "legacy_unverified"
    ISO_WEEK = "iso_week"


class TemporalRelation(str, Enum):
    BEFORE = "before"
    AFTER = "after"
    SAME_DAY = "same_day"
    UNRESOLVED = "unresolved"


def _require_date(value):
    if not isinstance(value, date) or isinstance(value, datetime):
        raise ValueError("Expected a calendar date, not an occurrence timestamp.")


@dataclass(frozen=True)
class ObservationTime:
    """Keep an observation date distinct from its ISO-week lookup key.

    Interval bounds express uncertainty, never an effective observation date.
    Legacy dates deliberately retain their established runtime interpretation.
    ISO_WEEK is supported here only; it is not a persistable model precision.
    """

    precision: ObservationPrecision
    measured_on: date | None
    week_start: date

    def __post_init__(self):
        object.__setattr__(self, "precision", ObservationPrecision(self.precision))
        _require_date(self.week_start)
        if self.week_start.weekday() != 0:
            raise ValueError("The ISO-week key must be a Monday.")
        if self.precision == ObservationPrecision.ISO_WEEK:
            if self.measured_on is not None:
                raise ValueError("A week-only observation has no observation day.")
            # Validate that both interval bounds fit the calendar date range.
            date.fromisocalendar(self.iso_year, self.iso_week, 7)
        else:
            _require_date(self.measured_on)
            if self.measured_on - timedelta(days=self.measured_on.weekday()) != self.week_start:
                raise ValueError("The observation day must belong to its ISO-week key.")

    @classmethod
    def for_iso_week(cls, iso_year, iso_week):
        return cls(ObservationPrecision.ISO_WEEK, None, date.fromisocalendar(iso_year, iso_week, 1))

    @classmethod
    def from_measurement(cls, measurement):
        """Read loaded scalar fields, including non-persisted week-only objects.

        Callers must load all three fields to avoid Django deferred-field queries.
        """
        return cls(measurement.temporal_precision, measurement.measured_on, measurement.week_start)

    @property
    def iso_year(self):
        return self.week_start.isocalendar().year

    @property
    def iso_week(self):
        return self.week_start.isocalendar().week

    @property
    def interval(self):
        if self.precision == ObservationPrecision.ISO_WEEK:
            return self.week_start, date.fromisocalendar(self.iso_year, self.iso_week, 7)
        return self.measured_on, self.measured_on

    def compare_operation_day(self, operation_day):
        """Compare against a confirmed occurrence's local calendar day.

        Pass None when occurrence is unconfirmed, even if a legacy event has a
        stored event_date. SAME_DAY preserves dated-source >= eligibility, but
        does not prove within-day order. Source sequence eligibility remains a
        separate mandatory check. An overlapping ISO week is always unresolved.
        This comparison can also underpin a future reverse write guard; it does
        not authorize a write or decide how an unresolved operation is handled.
        """
        if operation_day is None:
            return TemporalRelation.UNRESOLVED
        _require_date(operation_day)
        first, last = self.interval
        if last < operation_day:
            return TemporalRelation.BEFORE
        if first > operation_day:
            return TemporalRelation.AFTER
        if self.precision == ObservationPrecision.ISO_WEEK:
            return TemporalRelation.UNRESOLVED
        return TemporalRelation.SAME_DAY
