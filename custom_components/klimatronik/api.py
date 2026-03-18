"""Async Klimatronik TCP client and notify parser."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import socket
import struct
from typing import Any


HHMM_RE = re.compile(r"([01]\d|2[0-3]):[0-5]\d")


def is_valid_hhmm(value: Any) -> bool:
    """Return True when the value is a valid HH:MM string."""
    return bool(HHMM_RE.fullmatch(str(value).strip()))


class KlimatronikError(Exception):
    """Base exception."""


class KlimatronikConnectionError(KlimatronikError):
    """Connection error."""


class KlimatronikProtocolError(KlimatronikError):
    """Protocol error."""


class KlimatronikTimeoutError(KlimatronikError):
    """Timeout error."""


class KlimatronikNotifyParser:
    """Parser for ccmdjNotifyTick payloads."""

    TAG_INLINE_MIN = 0x01
    TAG_INLINE_MAX = 0x17
    TAG_U8 = 0x18
    TAG_U16 = 0x19
    TAG_U32 = 0x1A
    TAG_F64 = 0xFB
    TAG_NONE = 0x00
    TAG_END = 0xFF
    TAGS = set(range(TAG_INLINE_MIN, TAG_INLINE_MAX + 1)) | {
        TAG_U8,
        TAG_U16,
        TAG_U32,
        TAG_F64,
        TAG_NONE,
        TAG_END,
    }
    DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
    ZERO_VALUE_KEYS = {"ff1.pwm", "ff1.rpm", "ff2.pwm", "ff2.rpm"}
    NOISY_SUFFIX_KEYS = {
        "ff1.pwm",
        "ff1.rpm",
        "ff2.pwm",
        "ff2.rpm",
        "il1.ltr329",
    }

    SENSOR_SCALES = {
        "hh1.sht3x": ("humidity_inside_pct", 100.0),
        "ht1.sht3x": ("temp_inside_c", 100.0),
        "jt2.mcp9808": ("temp_jt2_c", 16.0),
        "jt3.mcp9808": ("temp_jt3_c", 16.0),
        "jt4.mcp9808": ("temp_jt4_c", 16.0),
        "jt5.mcp9808": ("temp_jt5_c", 16.0),
    }

    def parse(self, payload: bytes) -> dict[str, Any] | None:
        marker = b"ccmdjNotifyTick"
        start = payload.find(marker)
        if start < 0:
            return None

        idx = start + len(marker)
        raw: dict[str, Any] = {}
        while idx < len(payload) - 1:
            key_end = idx
            while key_end < len(payload) and self._is_key_byte(payload[key_end]):
                key_end += 1

            key = payload[idx:key_end].decode("utf-8", errors="replace")
            if not key or key_end >= len(payload):
                break

            tag = payload[key_end]
            idx = key_end + 1
            if tag == self.TAG_END:
                break

            value, idx = self._parse_tagged(key, payload, tag, idx)
            if idx is None:
                break

            raw[key] = value

        text = payload.decode("utf-8", errors="replace")
        mode_hits = re.findall(
            r"dmode(?:coff|dauto|fmanual|equiet|jauto(?:\.quiet)?|[ie]turbo)", text
        )
        if mode_hits:
            # Some mixed mode/status blocks contain stale historical mode tokens.
            # The last token observed in the payload tracks the current state best.
            raw["dmode"] = mode_hits[-1]

        raw.update(self._extract_mode_info(text))
        raw.update(self._extract_compact_probe_values(payload, raw))
        raw.update(self._extract_quiet_schedule(payload))
        self._normalize_noisy_suffix_keys(raw)
        self._scrub_noisy_keys(raw)
        return {"raw": raw, "decoded": self.decode(raw)}

    def decode(self, raw: dict[str, Any]) -> dict[str, Any]:
        decoded = dict(raw)
        for source_key, (target_key, scale) in self.SENSOR_SCALES.items():
            value = raw.get(source_key)
            if isinstance(value, int):
                decoded[target_key] = round(value / scale, 2)

        temp_inside = decoded.get("temp_inside_c")
        temp_jt2 = decoded.get("temp_jt2_c")
        temp_jt3 = decoded.get("temp_jt3_c")
        temp_jt4 = decoded.get("temp_jt4_c")

        if temp_jt3 is not None:
            decoded["temp_outside_c"] = temp_jt3
            decoded["temp_inflow_inlet_c"] = temp_jt3
        elif temp_jt2 is not None:
            decoded["temp_outside_c"] = temp_jt2
            decoded["temp_inflow_inlet_c"] = temp_jt2

        if temp_jt2 is not None:
            decoded["temp_inflow_outlet_c"] = temp_jt2
        if temp_inside is not None:
            decoded["temp_outflow_inlet_c"] = temp_inside
        if temp_jt4 is not None:
            decoded["temp_outflow_outlet_c"] = temp_jt4
        return decoded

    def _parse_tagged(
        self, key: str, payload: bytes, tag: int, idx: int
    ) -> tuple[Any, int | None]:
        if tag == self.TAG_NONE:
            if key in self.ZERO_VALUE_KEYS:
                return 0, idx
            return None, idx
        if self.TAG_INLINE_MIN <= tag <= self.TAG_INLINE_MAX:
            # Some devices emit compact inline integer values directly as tag bytes.
            return int(tag), idx
        if tag == self.TAG_U8:
            if idx >= len(payload):
                return None, None
            return payload[idx], idx + 1
        if tag == self.TAG_U16:
            if idx + 1 >= len(payload):
                return None, None
            return ((payload[idx] << 8) | payload[idx + 1]), idx + 2
        if tag == self.TAG_U32:
            # Light readings use a 32-bit integer tag. If this is not handled,
            # parsing loses alignment and the following fan fields disappear.
            if idx + 3 >= len(payload):
                return None, None
            return struct.unpack(">I", payload[idx : idx + 4])[0], idx + 4
        if tag == self.TAG_F64:
            if idx + 7 >= len(payload):
                return None, None
            return struct.unpack(">d", payload[idx : idx + 8])[0], idx + 8
        # Unknown tag: assume inline/no-payload so parsing can continue.
        return None, idx

    def _is_key_byte(self, value: int) -> bool:
        return (
            48 <= value <= 57
            or 65 <= value <= 90
            or 97 <= value <= 122
            or value in {46, 95}
        )

    def _extract_mode_info(self, text: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        mode_block = re.search(r"(dmode[^$]*?)btzx", text, flags=re.DOTALL)
        if not mode_block:
            return out

        block = mode_block.group(1)
        mode_hits = re.findall(
            r"dmode(?:coff|dauto|fmanual|equiet|jauto(?:\.quiet)?|[ie]turbo)", block
        )
        if mode_hits:
            mode_hint = mode_hits[-1]
        else:
            mode_hint = ""

        if mode_hint == "dmodecoff":
            out["app_mode"] = "off"
        elif mode_hint in {"dmodejauto", "dmodedauto"}:
            out["app_mode"] = "auto"
        elif mode_hint == "dmodefmanual":
            out["app_mode"] = "manual"
        elif mode_hint in {"dmodeiturbo", "dmodeeturbo"}:
            out["app_mode"] = "turbo"
        elif mode_hint in {"dmodejauto.quiet", "dmodeequiet"}:
            out["app_mode"] = "quiet"

        if out.get("app_mode") == "turbo":
            turbo = re.search(r"turbo\|(\d+)", block)
            if turbo:
                out["turbo_duration_s"] = int(turbo.group(1))

        states = re.search(
            r"fheater[a-z](?P<heater>.*?)idefroster[a-z](?P<defroster>.*?)ealarm[a-z](?P<alarm>.*?)eservo[a-z](?P<servo>.*)$",
            block,
            flags=re.DOTALL,
        )
        if states:
            out["heater_state"] = self._normalize_state_flag(states.group("heater"))
            out["defroster_state"] = self._normalize_state_flag(
                states.group("defroster")
            )
            out["alarm_state"] = self._normalize_state_flag(states.group("alarm"))
            out["servo_state"] = self._normalize_state_flag(states.group("servo"))
        return out

    def _normalize_state_flag(self, value: str) -> str:
        cleaned = str(value).strip().lower()
        if cleaned in {"on", "off"}:
            return cleaned
        if (
            len(cleaned) > 1
            and cleaned[0] in {"b", "c", "d"}
            and cleaned[1:] in {"on", "off"}
        ):
            return cleaned[1:]
        if (
            len(cleaned) > 1
            and cleaned[-1] in {"b", "c", "d", "e"}
            and cleaned[:-1] in {"on", "off"}
        ):
            return cleaned[:-1]
        return cleaned

    def _extract_quiet_schedule(self, payload: bytes) -> dict[str, Any]:
        out: dict[str, Any] = {}
        marker = b"bqt" + bytes([0x8E])
        start = payload.find(marker)
        if start < 0:
            return out

        idx = start + len(marker)
        slots: list[tuple[int, int]] = []
        for _ in range(7):
            start_min, idx = self._read_tagged_int(payload, idx)
            end_min, idx = self._read_tagged_int(payload, idx)
            if start_min is None or end_min is None:
                return {}
            slots.append((start_min, end_min))

        out["quiet_slots_minutes"] = slots
        for day_idx, day in enumerate(self.DAYS):
            start_min, end_min = slots[day_idx]
            out[f"quiet_{day}_start"] = self._format_hhmm(start_min)
            out[f"quiet_{day}_end"] = self._format_hhmm(end_min)

        weekdays = slots[0:5]
        weekends = slots[5:7]
        if len(set(weekdays)) == 1:
            out["quiet_weekday_start"] = self._format_hhmm(weekdays[0][0])
            out["quiet_weekday_end"] = self._format_hhmm(weekdays[0][1])
        if len(set(weekends)) == 1:
            out["quiet_weekend_start"] = self._format_hhmm(weekends[0][0])
            out["quiet_weekend_end"] = self._format_hhmm(weekends[0][1])

        return out

    def _extract_compact_probe_values(
        self, payload: bytes, raw: dict[str, Any]
    ) -> dict[str, Any]:
        out: dict[str, Any] = {}
        key = b"jt3.mcp9808"
        if isinstance(raw.get("jt3.mcp9808"), int):
            return out
        idx = payload.find(key)
        if idx < 0:
            return out

        marker_idx = idx + len(key)
        if marker_idx >= len(payload):
            return out
        marker = payload[marker_idx]

        if marker in self.TAGS or self._is_key_byte(marker):
            return out

        if marker_idx + 1 >= len(payload) or not self._is_key_byte(
            payload[marker_idx + 1]
        ):
            return out

        out["jt3.mcp9808"] = int(marker)
        return out

    def _read_tagged_int(self, payload: bytes, idx: int) -> tuple[int | None, int]:
        if idx >= len(payload):
            return None, idx
        tag = payload[idx]
        idx += 1
        if tag == self.TAG_U8:
            if idx >= len(payload):
                return None, idx
            return payload[idx], idx + 1
        if tag == self.TAG_U16:
            if idx + 1 >= len(payload):
                return None, idx
            return ((payload[idx] << 8) | payload[idx + 1]), idx + 2
        if tag == self.TAG_U32:
            if idx + 3 >= len(payload):
                return None, idx
            return struct.unpack(">I", payload[idx : idx + 4])[0], idx + 4
        return None, idx

    def _format_hhmm(self, minutes: int) -> str:
        hours = (minutes // 60) % 24
        mins = minutes % 60
        return f"{hours:02d}:{mins:02d}"

    def _scrub_noisy_keys(self, raw: dict[str, Any]) -> None:
        for key in list(raw.keys()):
            if "btzx" in key and "bqt" in key:
                raw.pop(key, None)

    def _normalize_noisy_suffix_keys(self, raw: dict[str, Any]) -> None:
        for canonical in self.NOISY_SUFFIX_KEYS:
            if canonical in raw:
                continue
            for key in list(raw.keys()):
                if key == canonical:
                    continue
                if key.endswith(canonical):
                    raw[canonical] = raw[key]
                    raw.pop(key, None)
                    break


@dataclass(slots=True)
class KlimatronikStateSample:
    """Single parsed sample."""

    timestamp: str
    frame_type: int
    raw: dict[str, Any]
    decoded: dict[str, Any]


class KlimatronikClient:
    """Async client for Klimatronik protocol."""

    FRAME_AUTH = 0xA2
    FRAME_LOC = 0xA3
    FRAME_MODE_TURBO = 0xA4

    MODE_ACK = b"ccmdmSetDeviceModecerrceok"
    GENERIC_ACK = b"ceok"

    DEFAULT_LATITUDE = 48.93626281203828
    DEFAULT_LONGITUDE = 24.77790558533836

    def __init__(
        self,
        host: str,
        *,
        port: int = 8080,
        connect_timeout: float = 4.0,
        read_timeout: float = 1.0,
        ready_wait: float = 2.0,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._ready_wait = ready_wait
        self._notify_parser = KlimatronikNotifyParser()

    async def off(self) -> dict[str, Any]:
        return await self._execute_mode(
            frame_type=self.FRAME_AUTH,
            payload=b"ccmdmSetDeviceModedmodecoff",
            expected_ack=self.MODE_ACK,
        )

    async def on(self, *, intensity: int = 32) -> dict[str, Any]:
        return await self.auto(intensity=intensity)

    async def auto(self, *, intensity: int = 32) -> dict[str, Any]:
        checked = self._validate_intensity(intensity)
        payload = b"ccmdmSetDeviceModedmodedautoiintensity" + self._encode_tagged_uint(
            checked
        )
        return await self._execute_mode(
            frame_type=self.FRAME_LOC,
            payload=payload,
            expected_ack=self.MODE_ACK,
        )

    async def turbo(self, *, duration_s: int = 180, rpm: int = 4500) -> dict[str, Any]:
        turbo_duration = self._validate_turbo_value(duration_s, "duration_s")
        turbo_rpm = self._validate_turbo_value(rpm, "rpm")
        payload = (
            b"ccmdmSetDeviceModecrpm"
            + self._encode_tagged_uint(turbo_rpm)
            + b"dmodeeturbohduration"
            + self._encode_tagged_uint(turbo_duration)
        )
        return await self._execute_mode(
            frame_type=self.FRAME_MODE_TURBO,
            payload=payload,
            expected_ack=self.MODE_ACK,
        )

    async def manual(self, *, inflow: int = 5, outflow: int = 5) -> dict[str, Any]:
        in_level = self._validate_level(inflow, "inflow")
        out_level = self._validate_level(outflow, "outflow")
        async with self._connect() as (reader, writer):
            await self._prepare_session(reader, writer)
            reply = b""
            for frame_type, payload, timeout in (
                (self.FRAME_AUTH, b"ccmdmSetDeviceModedmodefmanual", 1.2),
                (self.FRAME_LOC, self._manual_pwm_payload(0, in_level), 0.8),
                (self.FRAME_LOC, self._manual_pwm_payload(1, out_level), 1.2),
            ):
                await self._send_frame(writer, frame_type, payload)
                reply += await self._read_available(reader, timeout=timeout)
            return self._ack_result(reply, self.GENERIC_ACK)

    async def quiet(
        self,
        *,
        weekday_start: str = "22:00",
        weekday_end: str = "06:00",
        weekend_start: str = "23:30",
        weekend_end: str = "07:15",
    ) -> dict[str, Any]:
        self._validate_hhmm(weekday_start, "weekday_start")
        self._validate_hhmm(weekday_end, "weekday_end")
        self._validate_hhmm(weekend_start, "weekend_start")
        self._validate_hhmm(weekend_end, "weekend_end")
        schedule = self._normalize_quiet_schedule(
            weekday_start=weekday_start,
            weekday_end=weekday_end,
            weekend_start=weekend_start,
            weekend_end=weekend_end,
        )
        async with self._connect() as (reader, writer):
            await self._prepare_session(reader, writer)
            reply = b""
            for frame_type, payload, timeout in (
                (self.FRAME_AUTH, b"ccmdmSetDeviceModedmodeequiet", 1.2),
                (self.FRAME_AUTH, self._quiet_payload(schedule), 1.2),
            ):
                await self._send_frame(writer, frame_type, payload)
                reply += await self._read_available(reader, timeout=timeout)
            return self._ack_result(reply, self.GENERIC_ACK)

    async def state(
        self, *, samples: int = 3, timeout: float = 12.0
    ) -> KlimatronikStateSample:
        readings = await self.readings(samples=samples, timeout=timeout)
        if not readings:
            raise KlimatronikTimeoutError(
                f"No notify readings from {self._host}:{self._port}"
            )
        return readings[-1]

    async def readings(
        self, *, samples: int = 1, timeout: float = 8.0
    ) -> list[KlimatronikStateSample]:
        wanted = int(samples)
        if wanted < 1:
            raise KlimatronikProtocolError("samples must be >= 1")

        out: list[KlimatronikStateSample] = []
        deadline = asyncio.get_running_loop().time() + float(timeout)

        async with self._connect() as (reader, writer):
            await self._prepare_read_session(writer)
            while asyncio.get_running_loop().time() < deadline and len(out) < wanted:
                frame = await self._recv_frame(
                    reader,
                    timeout=min(
                        self._read_timeout,
                        max(0.01, deadline - asyncio.get_running_loop().time()),
                    ),
                )
                if frame is None:
                    continue
                parsed = self._notify_parser.parse(frame["payload"])
                if not parsed:
                    continue

                out.append(
                    KlimatronikStateSample(
                        timestamp=datetime.now(tz=timezone.utc).isoformat(),
                        frame_type=frame["frame_type"],
                        raw=parsed["raw"],
                        decoded=parsed["decoded"],
                    )
                )

        if not out:
            raise KlimatronikTimeoutError(
                f"No notify readings from {self._host}:{self._port}"
            )
        return out

    @asynccontextmanager
    async def open_read_stream(self):
        async with self._connect() as (reader, writer):
            await self._prepare_read_session(writer)
            yield reader, writer

    async def next_notify(
        self, reader: asyncio.StreamReader, *, timeout: float = 12.0
    ) -> KlimatronikStateSample | None:
        deadline = asyncio.get_running_loop().time() + float(timeout)
        while asyncio.get_running_loop().time() < deadline:
            frame = await self._recv_frame(
                reader,
                timeout=min(
                    self._read_timeout,
                    max(0.01, deadline - asyncio.get_running_loop().time()),
                ),
            )
            if frame is None:
                continue
            parsed = self._notify_parser.parse(frame["payload"])
            if not parsed:
                continue
            return KlimatronikStateSample(
                timestamp=datetime.now(tz=timezone.utc).isoformat(),
                frame_type=frame["frame_type"],
                raw=parsed["raw"],
                decoded=parsed["decoded"],
            )
        return None

    async def _execute_mode(
        self, *, frame_type: int, payload: bytes, expected_ack: bytes
    ) -> dict[str, Any]:
        async with self._connect() as (reader, writer):
            await self._prepare_session(reader, writer)
            await self._send_frame(writer, frame_type, payload)
            reply = await self._read_available(reader, timeout=5.0)
            if expected_ack not in reply:
                reply += await self._read_available(reader, timeout=2.0)
            return self._ack_result(reply, expected_ack)

    @asynccontextmanager
    async def _connect(self):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self._host, self._port),
                timeout=self._connect_timeout,
            )
        except (OSError, asyncio.TimeoutError) as err:
            raise KlimatronikConnectionError(
                f"Failed to connect to {self._host}:{self._port}: {err}"
            ) from err

        sock = writer.get_extra_info("socket")
        if sock:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

        try:
            yield reader, writer
        finally:
            writer.close()
            await writer.wait_closed()

    async def _prepare_session(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        await self._authorize(writer)
        await self._read_available(reader, timeout=0.6)
        await self._set_location(writer)
        await self._read_available(reader, timeout=0.8)
        await asyncio.sleep(self._ready_wait)
        await self._read_available(reader, timeout=0.5)

    async def _prepare_read_session(self, writer: asyncio.StreamWriter) -> None:
        await self._authorize(writer)
        await asyncio.sleep(0.2)
        await self._set_location(writer)

    async def _authorize(self, writer: asyncio.StreamWriter) -> None:
        payload = b"ccmdiAuthorizecpin" + bytes([0x1A, 0xFF, 0xFF, 0x00, 0x00])
        await self._send_frame(writer, self.FRAME_AUTH, payload)

    async def _set_location(
        self,
        writer: asyncio.StreamWriter,
        *,
        latitude: float = DEFAULT_LATITUDE,
        longitude: float = DEFAULT_LONGITUDE,
    ) -> None:
        payload = (
            b"ccmdqSetDeviceLocationhlatitude"
            + struct.pack(">d", latitude)
            + b"ilongitude"
            + struct.pack(">d", longitude)
        )
        await self._send_frame(writer, self.FRAME_LOC, payload)

    async def _send_frame(
        self, writer: asyncio.StreamWriter, frame_type: int, payload: bytes
    ) -> None:
        writer.write(self._frame(frame_type, payload))
        try:
            await writer.drain()
        except (OSError, ConnectionError) as err:
            raise KlimatronikConnectionError(f"Send failed: {err}") from err

    async def _recv_exact(
        self, reader: asyncio.StreamReader, nbytes: int, timeout: float
    ) -> bytes:
        try:
            return await asyncio.wait_for(reader.readexactly(nbytes), timeout=timeout)
        except asyncio.TimeoutError as err:
            raise KlimatronikTimeoutError from err
        except asyncio.IncompleteReadError as err:
            raise KlimatronikConnectionError("Socket closed by device") from err

    async def _recv_frame(
        self, reader: asyncio.StreamReader, timeout: float
    ) -> dict[str, Any] | None:
        if timeout <= 0:
            return None
        try:
            header = await self._recv_exact(reader, 2, timeout)
            length = struct.unpack(">H", header)[0]
            body = await self._recv_exact(reader, length, timeout)
            return {"frame_type": body[0], "payload": body[1:]}
        except KlimatronikTimeoutError:
            return None

    async def _read_available(
        self, reader: asyncio.StreamReader, *, timeout: float
    ) -> bytes:
        if timeout <= 0:
            return b""

        end = asyncio.get_running_loop().time() + timeout
        out = bytearray()
        while (left := end - asyncio.get_running_loop().time()) > 0:
            try:
                chunk = await asyncio.wait_for(
                    reader.read(8192), timeout=min(left, 0.15)
                )
            except asyncio.TimeoutError:
                continue
            except OSError as err:
                raise KlimatronikConnectionError(f"Read failed: {err}") from err

            if not chunk:
                break
            out.extend(chunk)
        return bytes(out)

    def _manual_pwm_payload(self, fan: int, level: int) -> bytes:
        pwm = level * 200
        return (
            b"ccmdoSetManualFanPwmcfan"
            + bytes([fan])
            + b"cpwm"
            + self._encode_tagged_uint(pwm)
        )

    def _quiet_payload(self, schedule: dict[str, str]) -> bytes:
        payload = bytearray(b"bqt")
        payload.append(0x8E)
        for day in KlimatronikNotifyParser.DAYS:
            start_min = self._hhmm_to_min(schedule[f"{day}_start"])
            end_min = self._hhmm_to_min(schedule[f"{day}_end"])
            payload.extend(bytes([0x19]) + struct.pack(">H", start_min))
            payload.extend(bytes([0x19]) + struct.pack(">H", end_min))
        payload.extend(b"ccmdlSetQuietTime")
        return bytes(payload)

    def _normalize_quiet_schedule(
        self,
        *,
        weekday_start: str,
        weekday_end: str,
        weekend_start: str,
        weekend_end: str,
    ) -> dict[str, str]:
        out: dict[str, str] = {}
        for day in KlimatronikNotifyParser.DAYS:
            is_weekend = day in {"sat", "sun"}
            out[f"{day}_start"] = weekend_start if is_weekend else weekday_start
            out[f"{day}_end"] = weekend_end if is_weekend else weekday_end
        return out

    def _hhmm_to_min(self, value: str) -> int:
        hours_s, mins_s = value.split(":", maxsplit=1)
        return int(hours_s) * 60 + int(mins_s)

    def _ack_result(self, data: bytes, token: bytes) -> dict[str, Any]:
        return {"acknowledged": token in data, "reply_bytes": len(data)}

    def _frame(self, frame_type: int, payload: bytes) -> bytes:
        return struct.pack(">HB", len(payload) + 1, frame_type) + payload

    def _encode_tagged_uint(self, value: int) -> bytes:
        checked = int(value)
        if checked < 0:
            raise KlimatronikProtocolError(
                f"Negative values are not supported: {checked}"
            )
        if checked <= 0x17:
            return bytes([checked])
        if checked <= 0xFF:
            return bytes([0x18, checked])
        if checked <= 0xFFFF:
            return bytes([0x19]) + struct.pack(">H", checked)
        if checked <= 0xFFFFFFFF:
            return bytes([0x1A]) + struct.pack(">I", checked)
        raise KlimatronikProtocolError(f"Value exceeds uint32: {checked}")

    def _validate_intensity(self, value: int) -> int:
        checked = int(value)
        if not 0 <= checked <= 100:
            raise KlimatronikProtocolError("Intensity must be in 0..100")
        return checked

    def _validate_level(self, value: int, field: str) -> int:
        checked = int(value)
        # Older capture notes documented manual levels as 1..9, but a live
        # device read confirmed that level 10 is accepted and reported back as
        # a 2000 manual parameter value.
        if not 1 <= checked <= 10:
            raise KlimatronikProtocolError(f"{field} must be in 1..10")
        return checked

    def _validate_turbo_value(self, value: int, field: str) -> int:
        checked = int(value)
        if not 1 <= checked <= 65535:
            raise KlimatronikProtocolError(f"{field} must be in 1..65535")
        return checked

    def _validate_hhmm(self, value: str, field: str) -> None:
        if not is_valid_hhmm(value):
            raise KlimatronikProtocolError(f"{field} must be in HH:MM format")
