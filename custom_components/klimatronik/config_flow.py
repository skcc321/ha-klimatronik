"""Config flow for Klimatronik integration."""

from __future__ import annotations

from collections.abc import Mapping
import concurrent.futures
import re
import select
import socket
import struct
import subprocess
import time
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import callback

from .api import is_valid_hhmm
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
    DEFAULT_INTENSITY,
    DEFAULT_MANUAL_INFLOW,
    DEFAULT_MANUAL_OUTFLOW,
    DEFAULT_QUIET_WEEKDAY_END,
    DEFAULT_QUIET_WEEKDAY_START,
    DEFAULT_QUIET_WEEKEND_END,
    DEFAULT_QUIET_WEEKEND_START,
    DEFAULT_TURBO_DURATION,
    DEFAULT_TURBO_RPM,
    DOMAIN,
    QUIET_TIME_OPTION_KEYS,
)

ESPRESSIF_OUI_PREFIXES = {
    "24:0a:c4",
    "30:ae:a4",
    "7c:df:a1",
    "84:f3:eb",
    "a0:a3:b3",
    "b4:e6:2d",
    "c8:2e:18",
    "cc:db:a7",
}
DISCOVERY_PORT = 8080
DISCOVERY_MAX_WORKERS = 64
DISCOVERY_CONNECT_TIMEOUT = 0.25
DISCOVERY_PROBE_TIMEOUT = 1.0
DISCOVERY_HOST_ATTEMPTS = 2
DISCOVERY_READ_WAIT_SLICE = 0.15
DISCOVERY_ACK_TOKEN = b"ccmdiAuthorizecerrceok"
DISCOVERY_AUTH_PAYLOAD = b"ccmdiAuthorizecpin" + bytes([0x1A, 0xFF, 0xFF, 0x00, 0x00])
DISCOVERY_AUTH_FRAME = (
    struct.pack(">HB", len(DISCOVERY_AUTH_PAYLOAD) + 1, 0xA2) + DISCOVERY_AUTH_PAYLOAD
)


def _int_field(
    defaults: Mapping[str, Any],
    key: str,
    fallback: int,
    *,
    min_value: int,
    max_value: int,
) -> tuple[Any, Any]:
    return vol.Optional(key, default=defaults.get(key, fallback)), vol.All(
        vol.Coerce(int), vol.Range(min=min_value, max=max_value)
    )


def _host_name_fields(defaults: Mapping[str, Any]) -> dict:
    return {
        vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
        vol.Optional(CONF_NAME, default=defaults.get(CONF_NAME, "")): str,
    }


def _performance_fields(defaults: Mapping[str, Any]) -> dict:
    key, validator = _int_field(
        defaults, CONF_DEFAULT_INTENSITY, DEFAULT_INTENSITY, min_value=0, max_value=100
    )
    return {key: validator}


def _manual_fields(defaults: Mapping[str, Any]) -> dict:
    inflow_key, inflow_validator = _int_field(
        defaults,
        CONF_MANUAL_INFLOW,
        DEFAULT_MANUAL_INFLOW,
        min_value=1,
        max_value=10,
    )
    outflow_key, outflow_validator = _int_field(
        defaults,
        CONF_MANUAL_OUTFLOW,
        DEFAULT_MANUAL_OUTFLOW,
        min_value=1,
        max_value=10,
    )
    return {
        inflow_key: inflow_validator,
        outflow_key: outflow_validator,
    }


def _turbo_fields(defaults: Mapping[str, Any]) -> dict:
    duration_key, duration_validator = _int_field(
        defaults,
        CONF_TURBO_DURATION,
        DEFAULT_TURBO_DURATION,
        min_value=1,
        max_value=65535,
    )
    rpm_key, rpm_validator = _int_field(
        defaults,
        CONF_TURBO_RPM,
        DEFAULT_TURBO_RPM,
        min_value=1,
        max_value=65535,
    )
    return {
        duration_key: duration_validator,
        rpm_key: rpm_validator,
    }


def _quiet_time_fields(defaults: Mapping[str, Any]) -> dict:
    return {
        vol.Optional(
            CONF_QUIET_WEEKDAY_START,
            default=defaults.get(CONF_QUIET_WEEKDAY_START, DEFAULT_QUIET_WEEKDAY_START),
        ): str,
        vol.Optional(
            CONF_QUIET_WEEKDAY_END,
            default=defaults.get(CONF_QUIET_WEEKDAY_END, DEFAULT_QUIET_WEEKDAY_END),
        ): str,
        vol.Optional(
            CONF_QUIET_WEEKEND_START,
            default=defaults.get(CONF_QUIET_WEEKEND_START, DEFAULT_QUIET_WEEKEND_START),
        ): str,
        vol.Optional(
            CONF_QUIET_WEEKEND_END,
            default=defaults.get(CONF_QUIET_WEEKEND_END, DEFAULT_QUIET_WEEKEND_END),
        ): str,
    }


def _add_invalid_time_errors(
    errors: dict[str, str], user_input: Mapping[str, Any]
) -> None:
    for key in QUIET_TIME_OPTION_KEYS:
        if key in user_input and not is_valid_hhmm(user_input[key]):
            errors[key] = "invalid_time"


def _base_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(
        {
            **_host_name_fields(data),
            **_performance_fields(data),
            **_manual_fields(data),
            **_turbo_fields(data),
            **_quiet_time_fields(data),
        }
    )


def _discovered_device_schema(
    hosts: list[str], defaults: Mapping[str, Any] | None = None
) -> vol.Schema:
    data = defaults or {}
    if not hosts:
        hosts = [""]
    host_default = data.get(CONF_HOST, hosts[0])
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=host_default): vol.In(hosts),
            vol.Optional(CONF_NAME, default=data.get(CONF_NAME, "")): str,
            **_performance_fields(data),
            **_manual_fields(data),
            **_turbo_fields(data),
            **_quiet_time_fields(data),
        }
    )


class KlimatronikConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle config flow."""

    VERSION = 1
    _discovered_hosts: list[str]

    def __init__(self) -> None:
        self._discovered_hosts = []

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        _ = user_input
        return self.async_show_menu(step_id="user", menu_options=["manual", "discover"])

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            _add_invalid_time_errors(errors, user_input)
            try:
                socket.gethostbyname(host)
            except OSError:
                errors[CONF_HOST] = "invalid_host"
            if not errors:
                await self.async_set_unique_id(host.lower())
                self._abort_if_unique_id_configured()
                title = user_input.get(CONF_NAME, "").strip() or host
                return self.async_create_entry(
                    title=title, data={**user_input, CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="manual", data_schema=_base_schema(user_input), errors=errors
        )

    async def async_step_discover(self, user_input: dict[str, Any] | None = None):
        _ = user_input
        errors: dict[str, str] = {}
        subnets = self._discover_subnet_prefixes()
        if not subnets:
            errors["base"] = "cannot_detect_subnet"
        else:
            discovered: list[str] = []
            for subnet in subnets:
                discovered.extend(
                    await self._async_discover_hosts(subnet_prefix=subnet, limit=0)
                )
            self._discovered_hosts = sorted(set(discovered))
            if not self._discovered_hosts:
                errors["base"] = "no_devices_found"
            else:
                return await self.async_step_pick_discovered()
        return self.async_show_form(
            step_id="discover",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={
                "subnets": ", ".join(subnets) if subnets else "N/A"
            },
        )

    async def async_step_pick_discovered(
        self, user_input: dict[str, Any] | None = None
    ):
        if not self._discovered_hosts:
            return await self.async_step_discover()

        errors: dict[str, str] = {}
        defaults = user_input or {CONF_HOST: self._discovered_hosts[0]}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            _add_invalid_time_errors(errors, user_input)
            if not errors:
                await self.async_set_unique_id(host.lower())
                self._abort_if_unique_id_configured()
                title = user_input.get(CONF_NAME, "").strip() or host
                return self.async_create_entry(
                    title=title, data={**user_input, CONF_HOST: host}
                )

        return self.async_show_form(
            step_id="pick_discovered",
            data_schema=_discovered_device_schema(self._discovered_hosts, defaults),
            errors=errors,
        )

    def _detect_local_subnet_prefixes(self) -> list[str]:
        prefixes: list[str] = []
        seen: set[str] = set()

        def add_ip(ip: str) -> None:
            parts = ip.split(".")
            if len(parts) != 4:
                return
            prefix = ".".join(parts[:3])
            if prefix not in seen:
                seen.add(prefix)
                prefixes.append(prefix)

        # Linux: collect all global IPv4 addresses from interfaces.
        try:
            out = subprocess.check_output(
                ["ip", "-4", "-o", "addr", "show", "scope", "global"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in out.splitlines():
                match = re.search(r"\binet\s+(\d+\.\d+\.\d+\.\d+)/", line)
                if match:
                    add_ip(match.group(1))
        except (OSError, subprocess.CalledProcessError):
            pass

        # macOS: derive default interface and read its IPv4 address.
        try:
            route_out = subprocess.check_output(
                ["route", "-n", "get", "default"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            iface = ""
            for line in route_out.splitlines():
                match = re.match(r"^\s*interface:\s+(\S+)\s*$", line)
                if match:
                    iface = match.group(1)
                    break
            iface_candidates = [iface, "en0", "en1", "en2", "bridge0", "bridge100"]
            for candidate in iface_candidates:
                if not candidate:
                    continue
                ip = subprocess.check_output(
                    ["ipconfig", "getifaddr", candidate],
                    text=True,
                    stderr=subprocess.DEVNULL,
                ).strip()
                if ip:
                    add_ip(ip)
        except (OSError, subprocess.CalledProcessError):
            pass

        # Cross-platform fallback: route-selected source IP.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                sock.connect(("8.8.8.8", 80))
                add_ip(sock.getsockname()[0])
        except OSError:
            pass

        return prefixes

    def _discover_subnet_prefixes(self) -> list[str]:
        detected_prefixes: list[str] = self._detect_local_subnet_prefixes()
        entry_prefixes: list[str] = []

        for entry in self.hass.config_entries.async_entries(DOMAIN):
            host = str(entry.data.get(CONF_HOST, "")).strip()
            if re.fullmatch(r"(?:\d{1,3}\.){3}\d{1,3}", host):
                parts = host.split(".")
                if len(parts) == 4:
                    entry_prefixes.append(".".join(parts[:3]))

        # Scan already configured subnets first, then local interface subnets.
        prefixes = [*entry_prefixes, *detected_prefixes]

        out: list[str] = []
        seen: set[str] = set()
        for prefix in prefixes:
            # Skip obvious non-LAN local ranges.
            if prefix.startswith("127.") or prefix.startswith("169.254."):
                continue
            if prefix not in seen:
                seen.add(prefix)
                out.append(prefix)
        return out

    async def _async_discover_hosts(
        self, *, subnet_prefix: str, limit: int
    ) -> list[str]:
        return await self.hass.async_add_executor_job(
            self._discover_hosts_blocking,
            subnet_prefix,
            limit,
        )

    def _discover_hosts_blocking(self, subnet_prefix: str, limit: int) -> list[str]:
        preferred_hosts = self._preferred_hosts_for_subnet(subnet_prefix)
        confirmed: set[str] = set()

        # First pass: probe likely devices (ARP/neigh Espressif hints) with minimal fanout.
        for ip in preferred_hosts:
            if self._probe_host_blocking(ip):
                confirmed.add(ip)

        if limit > 0 and len(confirmed) >= limit:
            return sorted(confirmed)[:limit]

        # Second pass: scan the whole /24, skipping hosts already confirmed.
        hosts = [
            f"{subnet_prefix}.{host}"
            for host in range(1, 255)
            if f"{subnet_prefix}.{host}" not in confirmed
        ]
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=DISCOVERY_MAX_WORKERS
        ) as pool:
            futures = {pool.submit(self._probe_host_blocking, ip): ip for ip in hosts}
            for fut in concurrent.futures.as_completed(futures):
                ip = futures[fut]
                try:
                    if fut.result():
                        confirmed.add(ip)
                        if limit > 0 and len(confirmed) >= limit:
                            break
                except Exception:
                    continue
        discovered = sorted(confirmed)
        return discovered[:limit] if limit > 0 else discovered

    def _preferred_hosts_for_subnet(self, subnet_prefix: str) -> list[str]:
        hosts: list[str] = []
        seen: set[str] = set()

        def add_if_likely(ip: str, mac: str) -> None:
            mac_prefix = ":".join(mac.lower().split(":")[:3]) if mac else ""
            if mac_prefix not in ESPRESSIF_OUI_PREFIXES:
                return
            if ip in seen:
                return
            seen.add(ip)
            hosts.append(ip)

        try:
            neigh_out = subprocess.check_output(
                ["ip", "neigh", "show"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            neigh_out = ""

        for line in neigh_out.splitlines():
            ip_match = re.search(r"\b(\d+\.\d+\.\d+\.\d+)\b", line)
            mac_match = re.search(r"\blladdr\s+([0-9a-f:]{17})\b", line.lower())
            if not ip_match or not mac_match:
                continue
            ip = ip_match.group(1)
            if ip.startswith(f"{subnet_prefix}."):
                add_if_likely(ip, mac_match.group(1))

        try:
            arp_out = subprocess.check_output(
                ["arp", "-an"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError):
            arp_out = ""

        for line in arp_out.splitlines():
            ip_match = re.search(r"\((\d+\.\d+\.\d+\.\d+)\)", line)
            mac_match = re.search(r"\bat\s+([0-9a-fA-F:]{17}|[0-9a-fA-F-]{17})\b", line)
            if not ip_match or not mac_match:
                continue
            ip = ip_match.group(1)
            if ip.startswith(f"{subnet_prefix}."):
                add_if_likely(ip, mac_match.group(1).replace("-", ":"))

        return hosts

    def _probe_host_blocking(self, ip: str) -> bool:
        for _attempt in range(DISCOVERY_HOST_ATTEMPTS):
            sock: socket.socket | None = None
            try:
                sock = socket.create_connection(
                    (ip, DISCOVERY_PORT), timeout=DISCOVERY_CONNECT_TIMEOUT
                )
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.sendall(DISCOVERY_AUTH_FRAME)
                raw = b""
                deadline = time.monotonic() + DISCOVERY_PROBE_TIMEOUT
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    wait = min(remaining, DISCOVERY_READ_WAIT_SLICE)
                    readable, _, _ = select.select([sock], [], [], wait)
                    if not readable:
                        continue
                    chunk = sock.recv(8192)
                    if not chunk:
                        break
                    raw += chunk
                    if DISCOVERY_ACK_TOKEN in raw:
                        return True
            except OSError:
                pass
            finally:
                if sock is not None:
                    sock.close()
            time.sleep(0.03)
        return False

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry):
        return KlimatronikOptionsFlow(config_entry)


class KlimatronikOptionsFlow(config_entries.OptionsFlow):
    """Handle options."""

    def __init__(self, entry: config_entries.ConfigEntry) -> None:
        self._entry = entry
        self._data: dict[str, Any] = {**entry.data, **entry.options}

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(
                {
                    CONF_HOST: user_input[CONF_HOST].strip(),
                    CONF_NAME: user_input.get(CONF_NAME, "").strip(),
                }
            )
            return await self.async_step_auto()

        return self.async_show_form(
            step_id="init", data_schema=_host_name_schema(self._data)
        )

    async def async_step_auto(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_turbo()

        return self.async_show_form(
            step_id="auto", data_schema=_auto_schema(self._data)
        )

    async def async_step_turbo(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_manual()

        return self.async_show_form(
            step_id="turbo", data_schema=_turbo_schema(self._data)
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            self._data.update(user_input)
            return await self.async_step_quiet()

        return self.async_show_form(
            step_id="manual", data_schema=_manual_schema(self._data)
        )

    async def async_step_quiet(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            _add_invalid_time_errors(errors, user_input)
            if not errors:
                self._data.update(user_input)
                self.hass.config_entries.async_update_entry(
                    self._entry,
                    data={
                        **self._entry.data,
                        CONF_HOST: self._data[CONF_HOST],
                        CONF_NAME: self._data.get(CONF_NAME, ""),
                    },
                )
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_DEFAULT_INTENSITY: self._data[CONF_DEFAULT_INTENSITY],
                        CONF_MANUAL_INFLOW: self._data[CONF_MANUAL_INFLOW],
                        CONF_MANUAL_OUTFLOW: self._data[CONF_MANUAL_OUTFLOW],
                        CONF_TURBO_DURATION: self._data[CONF_TURBO_DURATION],
                        CONF_TURBO_RPM: self._data[CONF_TURBO_RPM],
                        CONF_QUIET_WEEKDAY_START: self._data[CONF_QUIET_WEEKDAY_START],
                        CONF_QUIET_WEEKDAY_END: self._data[CONF_QUIET_WEEKDAY_END],
                        CONF_QUIET_WEEKEND_START: self._data[CONF_QUIET_WEEKEND_START],
                        CONF_QUIET_WEEKEND_END: self._data[CONF_QUIET_WEEKEND_END],
                    },
                )

        return self.async_show_form(
            step_id="quiet",
            data_schema=_quiet_schema(self._data),
            errors=errors,
        )


def _host_name_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(_host_name_fields(data))


def _auto_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(_performance_fields(data))


def _turbo_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(_turbo_fields(data))


def _manual_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(_manual_fields(data))


def _quiet_schema(defaults: Mapping[str, Any] | None = None) -> vol.Schema:
    data = defaults or {}
    return vol.Schema(_quiet_time_fields(data))
