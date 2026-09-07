class PlatformError(Exception):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


class Conflict(Exception):
    """An optimistic transaction must be replayed from a fresh read set."""
