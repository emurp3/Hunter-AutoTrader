"""
QUARANTINED — MockFileAdapter has been retired from active use.

Hunter operates on real AutoTrader sources only (file or http).
The original implementation is preserved at app/adapters/_dev/mock_file.py
for reference but must not be used in production.

If you are trying to test intake, point AUTOTRADER_FILE_PATH at a real
AutoTrader JSON export and set AUTOTRADER_SOURCE_TYPE=file.
"""

raise ImportError(
    "MockFileAdapter is quarantined and cannot be imported. "
    "Configure a real AutoTrader source via AUTOTRADER_SOURCE_TYPE and "
    "AUTOTRADER_FILE_PATH or AUTOTRADER_HTTP_URL."
)
