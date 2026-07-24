"""Central version constants.

These are recorded in the database and manifests so every collected row can be
traced back to the exact code that produced it.
"""

APP_VERSION = "2.0.0"

# Bump when the CSV parsing / normalization logic changes in a way that affects
# stored raw or normalized rows.
PARSER_VERSION = "hdb-parser-1"

# Current preferred event fingerprint version.  Fallback versions are declared
# in :mod:`hdb.fingerprint`.
FINGERPRINT_VERSION = "fp-v1"

# Target SQLite schema version applied by the migration runner.
SCHEMA_VERSION = 2
