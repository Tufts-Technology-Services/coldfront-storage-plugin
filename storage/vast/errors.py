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
