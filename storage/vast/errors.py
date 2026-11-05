class VastError(Exception):
    """Base class for all VAST-related errors."""


class VastConnectionError(VastError):
    """Raised when there is a connection issue with VAST."""


class VastTimeoutError(VastError):
    """Raised when a VAST operation times out."""


class VastNotFoundError(VastError):
    """Raised when a requested resource is not found in VAST."""


class VastViewNotFoundError(VastNotFoundError):
    """Raised when a requested view is not found in VAST."""


class VastQuotaNotFoundError(VastNotFoundError, ValueError):
    """Raised when VAST has no quota for a path.

    Also a ValueError, which is what a missing quota raised before this class existed.
    """
