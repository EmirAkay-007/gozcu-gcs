"""The latest value of every registered field, and snapshots of it for the browser.

Not thread-safe: the source's thread owns it and hands snapshots, which are
plain data, to other threads.
"""

from dataclasses import dataclass
from typing import Any

from gcs import units
from gcs.fields import FIELDS, Field

NOT_RECEIVED = "not received"
NO_DATA = "no data"


@dataclass(frozen=True)
class Sample:
    value: Any  # None when unknown
    reason: str | None  # why value is None
    received: float  # monotonic time of the update


class TelemetryState:
    def __init__(self, fields: tuple[Field, ...] = FIELDS) -> None:
        self._fields = {field.key: field for field in fields}
        self._by_message: dict[str, list[Field]] = {}
        for field in fields:
            if field.message:
                self._by_message.setdefault(field.message, []).append(field)
        self._samples: dict[str, Sample] = {}

    def update(self, msg: Any, now: float) -> None:
        """Take every registered value a message from the vehicle carries."""
        kind = msg.get_type()
        for field in self._by_message.get(kind, ()):
            value, reason = field.read(msg)
            self._samples[field.key] = Sample(value, reason, now)
        if kind in ("GLOBAL_POSITION_INT", "HOME_POSITION"):
            self._update_home_distance(now)

    def set(self, key: str, value: Any, now: float, reason: str | None = None) -> None:
        """Store a value the GCS works out itself (mode, link quality, ...)."""
        if key not in self._fields:
            raise KeyError(f"no field {key!r} in gcs.fields.FIELDS")
        if value is None and reason is None:
            reason = NO_DATA
        self._samples[key] = Sample(value, reason, now)

    def value(self, key: str) -> Any:
        sample = self._samples.get(key)
        return None if sample is None else sample.value

    def fresh(self, key: str, now: float) -> Sample | None:
        """The newest sample of a field, or None when there is none or it is stale."""
        sample = self._samples.get(key)
        if sample is None:
            return None
        limit = self._fields[key].stale_limit()
        return None if limit is not None and now - sample.received > limit else sample

    def snapshot(self, now: float) -> dict[str, dict[str, Any]]:
        """Every field as plain, JSON-ready data: value, text, age, stale flag, alert level."""
        out = {}
        for key, field in self._fields.items():
            sample = self._samples.get(key)
            if sample is None:
                out[key] = {
                    "value": None,
                    "text": field.format(None),
                    "reason": NOT_RECEIVED,
                    "age": None,
                    "stale": True,
                    "level": None,
                }
                continue
            age = now - sample.received
            limit = field.stale_limit()
            out[key] = {
                "value": sample.value,
                "text": field.format(sample.value),
                "reason": sample.reason,
                "age": round(age, 2),
                "stale": limit is not None and age > limit,
                "level": field.level(sample.value),
            }
        return out

    def _update_home_distance(self, now: float) -> None:
        lat, lon = self.value("lat"), self.value("lon")
        home_lat, home_lon = self.value("home_lat"), self.value("home_lon")
        if lat is None or lon is None:
            self.set("home_distance", None, now, reason="no position")
        elif home_lat is None or home_lon is None:
            self.set("home_distance", None, now, reason="home not set")
        else:
            self.set("home_distance", units.haversine_m(lat, lon, home_lat, home_lon), now)
