# Klimatronik Home Assistant Integration

[![HACS](https://img.shields.io/badge/HACS-Custom%20Integration-41BDF5.svg)](https://hacs.xyz/)
[![Release](https://img.shields.io/github/v/release/skcc321/ha-klimatronik)](https://github.com/skcc321/ha-klimatronik/releases)
[![Tests](https://github.com/skcc321/ha-klimatronik/actions/workflows/tests.yml/badge.svg)](https://github.com/skcc321/ha-klimatronik/actions/workflows/tests.yml)
[![License](https://img.shields.io/github/license/skcc321/ha-klimatronik)](https://github.com/skcc321/ha-klimatronik/blob/main/LICENSE)

Local Home Assistant custom integration for Klimatronik ventilation devices.

Tested on: Klimatronik 160 PRO series.

This repository contains the standalone integration code in:

- `custom_components/klimatronik`

## Features

- Config Flow support (`Settings -> Devices & Services -> Add Integration`)
- `fan` entity with:
  - on/off
  - percentage speed
  - preset modes: `auto`, `manual`, `turbo`, `quiet`
- Sensor entities for temperatures, humidity, CO2, TVOC, fan RPM and related values

## Installation

### HACS (recommended)

1. Open HACS in Home Assistant.
2. Go to Integrations.
3. Add custom repository URL for this repo, category `Integration`.
4. Install `Klimatronik`.
5. Restart Home Assistant.
6. Add integration: `Settings -> Devices & Services -> Add Integration -> Klimatronik`.

### Manual

1. Copy `custom_components/klimatronik` into your HA config at:
   - `<config>/custom_components/klimatronik`
2. Restart Home Assistant.
3. Add integration from UI.

## Development

- Run Home Assistant `check_config` before release.
- Use semantic versioning.

## Releases

- Bump `custom_components/klimatronik/manifest.json` `version` in a PR.
- After that PR is merged into `main`, the `Release` workflow runs automatically.
- The workflow validates tests, creates tag `v<manifest_version>` (if missing),
  and publishes the GitHub release used by HACS.

## License

MIT

## Integration Notes

Detailed protocol/runtime notes are in:

- `custom_components/klimatronik/README.md`

## Vendor Reference

Klimatronik devices vendor page:

- https://klimatronik.com.ua/

This is an independent community integration and is not affiliated with or endorsed by Klimatronik.
