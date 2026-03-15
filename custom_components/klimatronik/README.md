# Klimatronik Home Assistant Custom Integration

This integration talks to Klimatronik devices directly over local TCP (`<device_ip>:8080`) and does not use MQTT.

## What it provides

- 1 `fan` entity per device
  - Off / On
  - Speed percentage (mapped to `auto` intensity `0..100`)
  - Preset modes: `auto`, `manual`, `turbo`, `quiet`
- Sensors:
  - inside temp
  - outside temp
  - inside humidity
  - CO2
  - TVOC
  - inflow fan RPM
  - outflow fan RPM

## Scope

This README focuses on protocol behavior, runtime notes, and maintenance details
for the integration implementation.

For installation and end-user setup, use the repository root README.

## Current Runtime Model

This integration uses a hybrid model:

1. During HA setup, it performs the normal one-shot refresh path.
2. After Home Assistant startup, it enables one long-lived background read
   session per device.
3. That session performs:
   - TCP connect
   - `Authorize`
   - `SetDeviceLocation`
   - passive `NotifyTick` reads
4. Coordinator refreshes use cached stream data while the session is healthy.
5. Commands are currently allowed to run in parallel with the background read
   session because this was observed to make UI actions much more responsive on
   the tested devices.

## Refresh Interval

The integration uses a fixed internal refresh interval.

In the current background-session model this is not a direct device poll
interval. Instead it controls how often Home Assistant:
- refreshes coordinator state
- checks whether the background stream is still fresh
- falls back to a one-shot poll if the stream has gone stale
- reevaluates availability recovery

Older config entries may still contain a stored `scan_interval` value, but it
is now ignored.

## Device-Specific Notes

These devices are sensitive to access patterns.

Observed during live testing:
- overlapping short-lived reconnect sessions can trigger instability
- a single owner read session per device is much smoother than reconnecting on
  every poll
- some devices still show occasional transient connect failures, so the
  integration keeps the last good state through the first failed poll and marks
  the device unavailable only after 2 consecutive failed polls
- background stream tasks must be cancelled on the HA stop event, not only on
  entry unload, or Home Assistant will warn during shutdown
- the background stream is self-healing: if it disconnects or stops receiving
  `NotifyTick`, the stream loop reconnects after a short delay, and coordinator
  refresh falls back to one-shot polling if the stream stays stale

## Sensor Semantics

The current parser/mapping follows the working HA interpretation validated
against live device readings:

- `temp_inside_c`: inside air
- `temp_inflow_inlet_c`: `jt3`
- `temp_inflow_outlet_c`: `jt2`
- `temp_outflow_inlet_c`: inside air
- `temp_outflow_outlet_c`: `jt4`
- `temp_outside_c`: same semantic slot as inflow inlet (`jt3`)

## Control Scales

The device does not use one uniform scale for all modes:
- `auto` intensity uses `0..100`
- `manual` inflow/outflow use app-style levels `1..10`

Parser notes from live captures:
- `il1.ltr329` uses CBOR-like tag `0x1A` (`U32`)
- `il1.ltr329` comes from an ambient light sensor family and is plausibly
  lux-correlated, but this integration exposes it only as ambient light and
  does not validate or convert it as true lux
- if `U32` support is missing, parsing loses alignment and the following fan
  fields (`ff1.*`, `ff2.*`) show up as missing/unknown
- some payloads contain noisy prefixed keys, so the parser normalizes suffixes
  like `ff1.pwm`, `ff2.rpm`, and `il1.ltr329` back to their canonical names

## Option Changes

Integration options such as:
- quiet weekday/weekend times
- turbo duration / RPM
- default intensity
- manual inflow / outflow

are reloaded into the running integration when the config entry is updated.

Important:
- these are integration-side settings and defaults
- they are sent to the device when the related command is executed
- for example, new quiet times take effect when `quiet` mode is sent

The options UI is split into multiple steps:
1. host / name
2. auto
3. turbo
4. manual
5. quiet

Host and name updates are written back to the config entry data. Mode-specific
settings are stored in config entry options.

## Best Practices

- Prefer one HA integration entry per physical device.
- Avoid running extra debug clients against the same device while HA is using
  it, unless you are deliberately testing concurrency.
- If availability flaps but the device does not reboot, check for transient
  connect failures in `home-assistant.log` before changing parser logic.
- If mode UI disagrees with the physical device, inspect `NotifyTick` parsing
  first; mixed mode/status text blocks can contain stale historical tokens.
- If you change the session model again, keep the normal HA first-refresh setup
  path intact. Starting background socket tasks too early can block HA startup.

## Maintenance Notes

Files worth understanding before protocol changes:
- `api.py`: frame encoding, command payloads, `NotifyTick` parsing
- `coordinator.py`: session ownership, refresh fallback, availability policy
- `fan.py` and `select.py`: user-facing control surface
- `config_flow.py`: persisted options and validation
