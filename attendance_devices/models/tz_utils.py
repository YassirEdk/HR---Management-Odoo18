"""Single source of truth for the attendance timezone.

Every date/time rule of the module (shift start/end, break window, shift-day,
absences, statuses) is expressed in LOCAL time. The local timezone used to be
hardcoded as "Africa/Casablanca" in a dozen places; it is now read from
Settings > Attendances > Pointeuses (ir.config_parameter), so it can be changed
without touching the code.
"""
from datetime import datetime, time
from zoneinfo import ZoneInfo
import logging

_logger = logging.getLogger(__name__)

DEFAULT_TZ = 'Africa/Casablanca'
TZ_PARAM   = 'attendance_devices.timezone'
UTC        = ZoneInfo('UTC')


def get_attendance_tz_name(env):
    return env['ir.config_parameter'].sudo().get_param(TZ_PARAM) or DEFAULT_TZ


def get_attendance_tz(env):
    name = get_attendance_tz_name(env)
    try:
        return ZoneInfo(name)
    except Exception:
        _logger.warning("[TZ] Invalid attendance timezone %r — falling back to %s", name, DEFAULT_TZ)
        return ZoneInfo(DEFAULT_TZ)


def float_to_time(value):
    """8.5 -> 08:30. Rounds to the minute and wraps 24:00 to 00:00."""
    minutes = int(round((value or 0.0) * 60)) % 1440
    return time(minutes // 60, minutes % 60)


def local_to_utc(day, t, tz):
    """(local date, local time) -> naive UTC datetime (how Odoo stores it)."""
    return datetime.combine(day, t, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def utc_to_local(ts, tz):
    """naive UTC datetime -> naive local datetime."""
    return ts.replace(tzinfo=UTC).astimezone(tz).replace(tzinfo=None)
