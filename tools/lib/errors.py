# Shared error types for connectors and the run engine.


class ConnectorError(Exception):
    """A metrics connector failed. raw_secrets maps alias -> raw value for
    anything that might be embedded in the message, so the caller redacts
    before the error touches disk. Real connectors (gsc/dataforseo) redact
    internally and pass an empty map. tool_name identifies the failing
    connector in run.json's tool_calls."""

    def __init__(self, message, raw_secrets=None, tool_name=None):
        super().__init__(message)
        self.raw_secrets = raw_secrets or {}
        self.tool_name = tool_name
