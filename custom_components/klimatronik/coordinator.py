"""Coordinator for Klimatronik integration."""

from __future__ import annotations

import contextlib
import time
from datetime import timedelta
import asyncio
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import (
    KlimatronikClient,
    KlimatronikConnectionError,
    KlimatronikError,
    KlimatronikTimeoutError,
    is_valid_hhmm,
)
from .const import (
    CONF_DEFAULT_INTENSITY,
    CONF_MANUAL_INFLOW,
    CONF_MANUAL_OUTFLOW,
    CONF_QUIET_WEEKDAY_END,
    CONF_QUIET_WEEKDAY_START,
    CONF_QUIET_WEEKEND_END,
    CONF_QUIET_WEEKEND_START,
    CONF_TURBO_DURATION,
    CONF_TURBO_RPM,
    DEFAULT_READY_WAIT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ENTRY_OPTION_DEFAULTS,
    MODES,
    QUIET_TIME_OPTION_KEYS,
)

_POLL_LOCK = asyncio.Lock()
_LAST_POLL_TS = 0.0
_POLL_GAP_SECONDS = 5.0
_DEVICE_LOCKS: dict[str, asyncio.Lock] = {}
_DEVICE_LAST_OP_TS: dict[str, float] = {}
_DEVICE_MIN_GAP_SECONDS = 2.0
_POST_COMMAND_SETTLE_SECONDS = 2.0
_FAILURES_UNTIL_UNAVAILABLE = 2
_STREAM_NOTIFY_TIMEOUT = 15.0
_STREAM_RECONNECT_DELAY = 5.0
_STREAM_FRESHNESS_SECONDS = 45.0


class KlimatronikCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch state and execute control commands."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        # The user-facing scan/refresh setting was removed once the background
        # stream became self-healing. Keep a fixed internal coordinator refresh
        # cadence for stream freshness checks and one-shot recovery polls.
        scan_seconds = DEFAULT_SCAN_INTERVAL

        super().__init__(
            hass,
            logger=hass.data[DOMAIN]["logger"],
            name=f"{DOMAIN}-{entry.entry_id}",
            update_interval=timedelta(seconds=scan_seconds),
        )
        self._entry = entry
        self._host = entry.data[CONF_HOST]
        self._name = entry.data.get(CONF_NAME) or self._host
        self._scan_seconds = scan_seconds
        self._device_lock = _DEVICE_LOCKS.setdefault(self._host, asyncio.Lock())
        self._stream_task: asyncio.Task[None] | None = None
        self._stream_desired = False
        self._stream_last_sample_monotonic = 0.0

        self.client = KlimatronikClient(
            host=self._host,
            ready_wait=DEFAULT_READY_WAIT,
        )

        self.default_intensity = int(self._entry_option(CONF_DEFAULT_INTENSITY))
        self.manual_inflow = int(self._entry_option(CONF_MANUAL_INFLOW))
        self.manual_outflow = int(self._entry_option(CONF_MANUAL_OUTFLOW))
        self.turbo_duration = int(self._entry_option(CONF_TURBO_DURATION))
        self.turbo_rpm = int(self._entry_option(CONF_TURBO_RPM))
        self.quiet_weekday_start = str(self._entry_option(CONF_QUIET_WEEKDAY_START))
        self.quiet_weekday_end = str(self._entry_option(CONF_QUIET_WEEKDAY_END))
        self.quiet_weekend_start = str(self._entry_option(CONF_QUIET_WEEKEND_START))
        self.quiet_weekend_end = str(self._entry_option(CONF_QUIET_WEEKEND_END))
        self._last_intensity = self.default_intensity
        self._last_mode = "off"
        self._sticky_decoded: dict[str, Any] = {}
        self._sticky_raw: dict[str, Any] = {}
        self._consecutive_failures = 0

    @property
    def host(self) -> str:
        return self._host

    @property
    def display_name(self) -> str:
        return self._name

    def _entry_option(self, key: str) -> int | str:
        return self._entry.options.get(
            key, self._entry.data.get(key, ENTRY_OPTION_DEFAULTS[key])
        )

    async def _async_update_data(self) -> dict[str, Any]:
        # When the background session is healthy, the coordinator's scheduled
        # refresh should return cached stream data instead of opening another
        # short-lived session.
        if self._stream_is_fresh() and self.data:
            self._consecutive_failures = 0
            return {**self.data, "available": True}

        try:
            sample = await self._poll_state()
        except (
            KlimatronikConnectionError,
            KlimatronikTimeoutError,
            KlimatronikError,
        ) as err:
            self._consecutive_failures += 1
            if self.data and self._consecutive_failures < _FAILURES_UNTIL_UNAVAILABLE:
                return {**self.data, "available": True}
            raise UpdateFailed(str(err)) from err

        self._consecutive_failures = 0
        return self._build_state_payload(sample)

    async def async_enable_background_session(self) -> None:
        self._stream_desired = True
        async with self._device_lock:
            self._ensure_stream_locked()

    async def async_shutdown(self) -> None:
        self._stream_desired = False
        await self._stop_stream()

    async def _poll_state(self):
        async def _do_poll():
            global _LAST_POLL_TS

            async with _POLL_LOCK:
                # Multiple Klimatronik devices are polled by independent
                # coordinators. A small global gap reduces reconnect bursts
                # across devices, which these units do not handle well.
                now = time.monotonic()
                wait_time = _POLL_GAP_SECONDS - (now - _LAST_POLL_TS)
                if wait_time > 0:
                    await asyncio.sleep(wait_time)

                sample = await self.client.state(samples=1, timeout=8.0)
                _LAST_POLL_TS = time.monotonic()
                return sample

        return await self._run_serialized(_do_poll, restart_stream=True)

    async def async_set_off(self) -> None:
        await self._run_command("off", self.client.off)

    async def async_set_auto(self, intensity: int | None = None) -> None:
        target = self.default_intensity if intensity is None else int(intensity)
        await self._run_command("auto", self.client.auto, intensity=target)
        self._last_intensity = target

    async def async_set_turbo(self) -> None:
        await self._run_command(
            "turbo",
            self.client.turbo,
            duration_s=self.turbo_duration,
            rpm=self.turbo_rpm,
        )

    async def async_set_manual(self) -> None:
        await self._run_command(
            "manual",
            self.client.manual,
            inflow=self.manual_inflow,
            outflow=self.manual_outflow,
        )

    async def async_set_quiet(self) -> None:
        await self._run_command(
            "quiet",
            self.client.quiet,
            weekday_start=self.quiet_weekday_start,
            weekday_end=self.quiet_weekday_end,
            weekend_start=self.quiet_weekend_start,
            weekend_end=self.quiet_weekend_end,
        )

    async def async_set_mode(self, mode: str, *, intensity: int | None = None) -> None:
        if mode not in MODES:
            raise UpdateFailed(f"Unsupported mode: {mode}")

        if mode == "off":
            await self.async_set_off()
            return
        if mode == "auto":
            await self.async_set_auto(
                self.default_intensity if intensity is None else intensity
            )
            return
        if mode == "manual":
            await self.async_set_manual()
            return
        if mode == "turbo":
            await self.async_set_turbo()
            return
        if mode == "quiet":
            await self.async_set_quiet()
            return
        raise UpdateFailed(f"Unsupported mode: {mode}")

    async def async_update_setting(self, key: str, value: int | str) -> None:
        if key in {
            CONF_DEFAULT_INTENSITY,
            CONF_MANUAL_INFLOW,
            CONF_MANUAL_OUTFLOW,
            CONF_TURBO_DURATION,
            CONF_TURBO_RPM,
        }:
            checked_value = int(value)
        elif key in QUIET_TIME_OPTION_KEYS:
            checked_value = str(value).strip()
            if not is_valid_hhmm(checked_value):
                raise UpdateFailed(f"Invalid HH:MM value for {key}: {checked_value}")
        else:
            raise UpdateFailed(f"Unsupported setting key: {key}")

        setattr(self, key, checked_value)
        self.hass.config_entries.async_update_entry(
            self._entry,
            options={**self._entry.options, key: checked_value},
        )
        if self.data:
            self.data = {**self.data, key: checked_value}
            self.async_update_listeners()

    async def _run_command(self, mode: str, func, **kwargs: Any) -> None:
        async def _do_command_and_refresh():
            try:
                result = await func(**kwargs)
            except (
                KlimatronikConnectionError,
                KlimatronikTimeoutError,
                KlimatronikError,
            ) as err:
                raise UpdateFailed(str(err)) from err

            if not result.get("acknowledged", False):
                raise UpdateFailed(f"Device did not acknowledge {mode} command")

            self._last_mode = mode
            if self.data:
                self.data = {**self.data, "mode": mode}

            await asyncio.sleep(_POST_COMMAND_SETTLE_SECONDS)
            sample = await self._poll_state_unlocked()
            self._consecutive_failures = 0
            updated = self._build_state_payload(sample)
            self.async_set_updated_data(updated)

        await self._run_serialized(_do_command_and_refresh, restart_stream=True)

    async def _run_serialized(self, operation, *, restart_stream: bool) -> Any:
        async with self._device_lock:
            now = time.monotonic()
            last = _DEVICE_LAST_OP_TS.get(self._host, 0.0)
            wait_time = _DEVICE_MIN_GAP_SECONDS - (now - last)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            try:
                return await operation()
            finally:
                _DEVICE_LAST_OP_TS[self._host] = time.monotonic()
                if restart_stream:
                    self._ensure_stream_locked()

    async def _poll_state_unlocked(self):
        global _LAST_POLL_TS

        async with _POLL_LOCK:
            now = time.monotonic()
            wait_time = _POLL_GAP_SECONDS - (now - _LAST_POLL_TS)
            if wait_time > 0:
                await asyncio.sleep(wait_time)

            sample = await self.client.state(samples=1, timeout=8.0)
            _LAST_POLL_TS = time.monotonic()
            return sample

    def _stream_is_fresh(self) -> bool:
        if not self.data:
            return False
        return (
            time.monotonic() - self._stream_last_sample_monotonic
        ) <= _STREAM_FRESHNESS_SECONDS

    def _ensure_stream_locked(self) -> None:
        if not self._stream_desired:
            return
        if self._stream_task and not self._stream_task.done():
            return
        # The background task owns the long-lived read session after HA has
        # finished starting. It should be the steady-state source of updates.
        self._stream_task = self.hass.async_create_task(self._stream_loop())

    async def _stop_stream(self) -> None:
        task = self._stream_task
        if task is None:
            return
        self._stream_task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _stream_loop(self) -> None:
        while self._stream_desired:
            try:
                async with self.client.open_read_stream() as (reader, _writer):
                    _DEVICE_LAST_OP_TS[self._host] = time.monotonic()
                    while self._stream_desired:
                        sample = await self.client.next_notify(
                            reader, timeout=_STREAM_NOTIFY_TIMEOUT
                        )
                        if sample is None:
                            raise KlimatronikTimeoutError(
                                f"No notify readings from {self._host}:8080"
                            )
                        self._consecutive_failures = 0
                        self._stream_last_sample_monotonic = time.monotonic()
                        # Stream updates are pushed straight into the
                        # coordinator so the UI reflects device-pushed state
                        # without waiting for the next coordinator refresh.
                        self.async_set_updated_data(self._build_state_payload(sample))
            except asyncio.CancelledError:
                raise
            except (
                KlimatronikConnectionError,
                KlimatronikTimeoutError,
                KlimatronikError,
            ):
                if not self._stream_desired:
                    break
                await asyncio.sleep(_STREAM_RECONNECT_DELAY)
            except Exception:
                self.logger.exception(
                    "Unexpected Klimatronik stream error for %s", self._host
                )
                if not self._stream_desired:
                    break
                await asyncio.sleep(_STREAM_RECONNECT_DELAY)

    def _build_state_payload(self, sample) -> dict[str, Any]:
        decoded = self._merge_sticky(sample.decoded, self._sticky_decoded)
        raw = self._merge_sticky(sample.raw, self._sticky_raw)
        mode = self._extract_mode(decoded) or self._last_mode
        intensity = self._extract_intensity(decoded, raw)
        if intensity is not None:
            self._last_intensity = intensity
        self._last_mode = mode

        return {
            "mode": mode,
            "intensity": self._last_intensity,
            "default_intensity": self.default_intensity,
            "manual_inflow": self.manual_inflow,
            "manual_outflow": self.manual_outflow,
            "turbo_duration": self.turbo_duration,
            "turbo_rpm": self.turbo_rpm,
            "quiet_weekday_start": self.quiet_weekday_start,
            "quiet_weekday_end": self.quiet_weekday_end,
            "quiet_weekend_start": self.quiet_weekend_start,
            "quiet_weekend_end": self.quiet_weekend_end,
            "decoded": decoded,
            "raw": raw,
            "sample_timestamp": sample.timestamp,
            "frame_type": sample.frame_type,
            "host": self._host,
            "name": self._name,
            "available": True,
        }

    def _merge_sticky(
        self, current: dict[str, Any], cache: dict[str, Any]
    ) -> dict[str, Any]:
        merged = dict(current)
        for key, value in current.items():
            if self._value_present(value):
                cache[key] = value
        for key, value in cache.items():
            if not self._value_present(merged.get(key)):
                merged[key] = value
        return merged

    def _value_present(self, value: Any) -> bool:
        if value is None:
            return False
        if isinstance(value, str) and value.strip() == "":
            return False
        return True

    def _extract_mode(self, decoded: dict[str, Any]) -> str | None:
        if decoded.get("app_mode"):
            return decoded["app_mode"]

        mode_hint = str(decoded.get("dmode", ""))
        if "coff" in mode_hint:
            return "off"
        if "fmanual" in mode_hint:
            return "manual"
        if "equiet" in mode_hint or "jauto.quiet" in mode_hint:
            return "quiet"
        if "turbo" in mode_hint:
            return "turbo"
        if "jauto" in mode_hint or "dauto" in mode_hint:
            return "auto"
        return None

    def _extract_intensity(
        self, decoded: dict[str, Any], raw: dict[str, Any]
    ) -> int | None:
        for source in (decoded, raw):
            value = source.get("iintensity")
            if isinstance(value, str):
                value = value.strip()
                if not value.isdigit():
                    continue
                value = int(value)
            elif isinstance(value, (int, float)):
                value = int(value)
            else:
                continue

            if 0 <= value <= 100:
                return value
        return None
