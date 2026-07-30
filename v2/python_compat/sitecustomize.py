"""Compatibility loaded by V2 subprocesses on Python versions before 3.11."""

import datetime


if not hasattr(datetime, "UTC"):
    datetime.UTC = datetime.timezone.utc
