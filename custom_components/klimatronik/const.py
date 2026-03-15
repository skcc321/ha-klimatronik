"""Constants for the Klimatronik integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "klimatronik"

PLATFORMS: list[Platform] = [
    Platform.FAN,
    Platform.SENSOR,
    Platform.SELECT,
]

DEFAULT_PORT = 8080
DEFAULT_SCAN_INTERVAL = 30
DEFAULT_READY_WAIT = 2.0

# Stored option key kept for backward compatibility. In the current runtime
# model this is no longer a direct device poll interval; it behaves as the
# coordinator refresh / stream health-check interval.
CONF_SCAN_INTERVAL = "scan_interval"
CONF_REFRESH_INTERVAL = CONF_SCAN_INTERVAL
CONF_DEFAULT_INTENSITY = "default_intensity"
CONF_MANUAL_INFLOW = "manual_inflow"
CONF_MANUAL_OUTFLOW = "manual_outflow"
CONF_TURBO_DURATION = "turbo_duration"
CONF_TURBO_RPM = "turbo_rpm"
CONF_QUIET_WEEKDAY_START = "quiet_weekday_start"
CONF_QUIET_WEEKDAY_END = "quiet_weekday_end"
CONF_QUIET_WEEKEND_START = "quiet_weekend_start"
CONF_QUIET_WEEKEND_END = "quiet_weekend_end"

DEFAULT_INTENSITY = 32
DEFAULT_MANUAL_INFLOW = 5
DEFAULT_MANUAL_OUTFLOW = 3
DEFAULT_TURBO_DURATION = 180
DEFAULT_TURBO_RPM = 4500
DEFAULT_QUIET_WEEKDAY_START = "22:00"
DEFAULT_QUIET_WEEKDAY_END = "06:00"
DEFAULT_QUIET_WEEKEND_START = "23:30"
DEFAULT_QUIET_WEEKEND_END = "07:15"
