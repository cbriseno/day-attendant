"""Resolve input once; persist instants in UTC and retain the origin zone."""
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import AssistantError

UTC = timezone.utc
MIN_RULE_YEAR, MAX_RULE_YEAR = 2, 9998


def utcnow():
    return datetime.now(UTC).replace(microsecond=0)


def stamp(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def instant(text, zone=None, fold=None):
    try:
        value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if value.tzinfo is not None:
            if zone:
                ZoneInfo(zone)
            return stamp(value)
        if not zone:
            raise AssistantError("timezone_required", "Include an offset or an IANA time zone.")
        tz = ZoneInfo(zone)
        candidates = {}
        for candidate_fold in (0, 1):
            local = value.replace(tzinfo=tz, fold=candidate_fold)
            resolved = local.astimezone(UTC)
            if resolved.astimezone(tz).replace(tzinfo=None) == value:
                candidates[candidate_fold] = stamp(resolved)
        if not candidates:
            raise AssistantError("nonexistent_time", "This local time does not exist.", {"zone": zone})
        if len(set(candidates.values())) > 1 and fold is None:
            raise AssistantError("ambiguous_time", "Choose fold 0 or 1 for this repeated local time.", candidates)
        if fold is not None and fold not in candidates:
            raise AssistantError("invalid_fold", "The requested fold is invalid.")
        return candidates[fold if fold is not None else min(candidates)]
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise AssistantError("invalid_time", "Use an ISO 8601 date and a valid IANA zone.") from error


def parse(value):
    if not value:
        return None
    if len(value) == 16 and value.endswith("Z") and "-" not in value:
        # Every stored task date is read on each state view; strptime dominated that cost.
        if value.isascii() and value[8] == "T" and value[:8].isdigit() and value[9:15].isdigit():
            return datetime(int(value[:4]), int(value[4:6]), int(value[6:8]), int(value[9:11]), int(value[11:13]), int(value[13:15]), tzinfo=UTC)
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def wire(value):
    return parse(value).strftime("%Y%m%dT%H%M%SZ") if value else None


def _offsets(zone, start, until):
    """Whole-span UTC offsets in minutes. Daily steps find each transition; bisection places it."""
    tz = ZoneInfo(zone)
    offset = lambda m: datetime.fromtimestamp(m * 60, UTC).astimezone(tz).utcoffset().total_seconds() / 60
    first, last = int(start.timestamp()) // 60, int(until.timestamp()) // 60
    offsets = [[first * 60000, offset(first)]]
    for low in range(first, last, 1440):
        high = min(low + 1440, last)
        if offset(high) == offsets[-1][1]:
            continue
        while high - low > 1:
            mid = (low + high) // 2
            low, high = (mid, high) if offset(mid) == offsets[-1][1] else (low, mid)
        offsets.append([high * 60000, offset(high)])
    return {"zone": zone, "from": first * 60000, "until": last * 60000, "offsets": offsets}


@lru_cache(maxsize=8)
def zone_rules(zone, day):
    """UTC offsets of `zone` from a year before `day` to five years after it.

    The browser resolves local dates with these rules instead of its own time zone
    data, so what a person sees and types matches the instant the service keeps.
    Other years come from zone_year, on request or for stored dates."""
    start = datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=366)
    return _offsets(zone, start, start + timedelta(days=366 * 6))


@lru_cache(maxsize=64)
def zone_year(zone, year):
    """The same rules for one calendar year, with margin for any local offset."""
    if not MIN_RULE_YEAR <= year <= MAX_RULE_YEAR:
        raise AssistantError("invalid_time", "That year is outside the supported dates.")
    return _offsets(zone, datetime(year, 1, 1, tzinfo=UTC) - timedelta(days=2), datetime(year + 1, 1, 1, tzinfo=UTC) + timedelta(days=2))
