"""
The quota-and-usage fields every storage answer in the REST API shares: a person's home or scratch
(storage.user_storage) and an allocation's usage (storage.allocation_usage). Each source fills in
a StorageUsage, so callers see the same names and the same state whatever system answered, and
nothing of the system's own vocabulary.
"""

from dataclasses import dataclass


@dataclass
class StorageUsage:
    """
    limit_bytes is the one limit people are told (see limit_of()). measured_at is when used_bytes
    was measured, limit_reported_at when the limit was read; ISO 8601 with an offset, or None.
    """

    limit_bytes: int | None = None
    used_bytes: int | None = None
    measured_at: str | None = None
    limit_reported_at: str | None = None

    @property
    def state(self):
        """ "ok", "over_limit", or None when there's no limit or no usage to compare."""
        if self.limit_bytes is None or self.used_bytes is None:
            return None
        return "over_limit" if self.used_bytes > self.limit_bytes else "ok"

    def as_dict(self) -> dict:
        return {
            "limit_bytes": self.limit_bytes,
            "used_bytes": self.used_bytes,
            "state": self.state,
            "measured_at": self.measured_at,
            "limit_reported_at": self.limit_reported_at,
        }


def limit_of(soft, hard):
    """The soft limit if there is one, else the hard one; None if neither. 0 is no limit: VAST's
    batch task and TrueNAS (ZFS) both record no quota as 0."""
    return soft or hard or None
