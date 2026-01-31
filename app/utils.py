from datetime import datetime
from zoneinfo import ZoneInfo

def to_warsaw_time(value, format='%Y-%m-%d %H:%M:%S'):
    """
    Converts a UTC datetime object to Europe/Warsaw time string.
    If value is naive, it assumes UTC.
    """
    if value is None:
        return ""
    if not isinstance(value, datetime):
        return value

    # Assume value is UTC if naive
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("UTC"))

    warsaw = ZoneInfo('Europe/Warsaw')
    local_dt = value.astimezone(warsaw)
    return local_dt.strftime(format)
