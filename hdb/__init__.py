"""hdb: Trade Ideas historical-alert collection system.

This package contains the platform-independent core (CSV parsing, exchange
calendar, session classification, event fingerprinting, SQLite migrations,
import/reconciliation, and the collection state machine) plus isolated Windows
automation adapters.

The core modules are importable and fully unit-testable on any OS.  Windows-only
dependencies (``pywinauto``, ``Pillow``) are imported lazily inside the
automation backend so the rest of the system works on Linux/macOS for
development and testing.
"""

from .version import (
    APP_VERSION,
    PARSER_VERSION,
    FINGERPRINT_VERSION,
    SCHEMA_VERSION,
)

__all__ = [
    "APP_VERSION",
    "PARSER_VERSION",
    "FINGERPRINT_VERSION",
    "SCHEMA_VERSION",
]
