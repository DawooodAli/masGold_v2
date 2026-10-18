"""The refusal type of the market-trip services, in a module of its own so the pure BOOK rules (services.book_rules) and the services share ONE class."""


class TripError(ValueError):
    """A refused trip operation. `code` is a stable machine reason; the message is Arabic, ready to show."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
