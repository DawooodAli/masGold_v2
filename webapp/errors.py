"""The one error type of the web API (an HTTP status and a short Arabic message that is safe to show)."""


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message
