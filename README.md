<picture>
  <source media="(prefers-color-scheme: dark)" srcset="brand/dark_icon.png">
  <img src="brand/icon.png" alt="" width="96" align="right">
</picture>

# Deebot Discovery

[![HACS custom repository](https://img.shields.io/badge/HACS-custom%20repository-41BDF5?logo=homeassistant&logoColor=white)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jrackerby&repository=ha-deebot-discovery&category=integration)
[![validate](https://github.com/jrackerby/ha-deebot-discovery/actions/workflows/validate.yml/badge.svg)](https://github.com/jrackerby/ha-deebot-discovery/actions/workflows/validate.yml)
[![release](https://img.shields.io/github/v/release/jrackerby/ha-deebot-discovery?sort=semver)](https://github.com/jrackerby/ha-deebot-discovery/releases)
[![license](https://img.shields.io/github/license/jrackerby/ha-deebot-discovery)](LICENSE)

A Home Assistant integration for Ecovacs Deebot robots that **discovers** what
a robot supports instead of looking it up in a table.

If Home Assistant's built-in Ecovacs integration logs `Device "..." not
supported` for your robot and gives it no entities, this is the integration
for that robot.

## Why it exists

`deebot-client` — the library Home Assistant's built-in `ecovacs` integration
is built on — resolves a robot's capabilities from a per-model lookup table
keyed on an opaque class string. The robot this was written for, a **DEEBOT T90
PRO OMNI Care** with class `nv8fz5`, is in none of that library's 248 hardware
modules, even though four sibling *T90 PRO OMNI* classes are. Since v14 removed
the fallback, `get_static_device_info` returns `None` for an unknown class and
the robot gets **zero entities**. A retail variant of a supported model gets
nothing.

The library is tens of thousands of lines of per-model capability tables
against a few thousand lines of model-agnostic protocol. The part that fails
here is the table. The part worth depending on is the protocol.

## The hard part

The robot does not publish what it supports. There is no
`getSupportedFunctions`-style command anywhere in the protocol — which is
precisely why upstream hand-maintains those tables. So support has to be
inferred from whether a command answers, and **the failure code that means
"not supported" is the same code that means "the network ate it"**
(`errno 500`, in deebot-client's own words).

`probe.py` and `classify.py` are the whole answer to that ambiguity, and they
import nothing from `homeassistant` and nothing from `deebot_client`, so they
are exercisable on a plain `python3` with no Home Assistant, no cloud account
and no robot.

Four rules, each because of a specific way the ambiguity can produce a
confident wrong answer:

1. **Every pass carries a control** — a command this robot has already
   answered. Control fails → the pass is **VOID**, not "unsupported", and the
   previous map stands. Without this, one bad minute of wifi silently strips
   every capability at once, and the stripped map looks exactly like a correct
   reading of a robot that lost its features.
2. **A pass with no control is also VOID**, not merely unguarded.
3. **Promote on one OK; demote only after three consecutive misses across
   VOID-free passes.** Fall dwell, never rise dwell.
4. **An outcome nobody has classified is not a miss.** Exactly one failure
   code carries the "or does not support the command" reading. Everything else
   the cloud can answer leaves the belief and the streak untouched.

It never invents a capability it has not seen answer. A seed hypothesis
borrowed from a sibling model marks a key as worth probing *first*; it never
marks it *supported*.

### The one thing that is inferred, and how it is kept honest

Some capabilities have no read command at all. `Charge` drives the robot to
the dock; `PlaySound` makes it beep; a station action runs a mop wash. Probing
those by doing them is not probing — it is operating someone's robot at 3am to
find out whether it can be operated.

So those are **implied**, and an implication may only hang off a capability
that was **measured on this robot**:

| Inferred | From | Why |
| --- | --- | --- |
| Start / pause / stop | `state` | The robot answered with a state machine; the actions drive it. |
| Return to dock | `state` | The state probe is `GetChargeState`, so the robot models a charge state. |
| Locate (beep) | `volume` | `GetVolume` answering proves the speaker. |
| Station actions, station state | `auto_empty` | `GetAutoEmpty` is station-only, so an answer proves a station is attached. |

An implication whose antecedent was itself implied would launder a guess
through a second guess. The catalogue refuses one at import, and the suite
proves it refuses it.

## Supported devices

Any Ecovacs robot reachable through the Ecovacs cloud, whether or not
deebot-client recognises its class. The case it was built for is the one it
handles that nothing else does: a class with **no** hardware table.

Measured so far:

| Robot | Class | Result |
| --- | --- | --- |
| DEEBOT T90 PRO OMNI Care | `nv8fz5` | Every function listed below. |

If you run it on another robot, [open a robot report](https://github.com/jrackerby/ha-deebot-discovery/issues/new?template=robot-report.yml)
with the class string and a diagnostics download. That is how the table above
grows, and it is the only way a robot with fewer or more functions than the
seed's probe order gets its order tuned.

The protocol client is built from a sibling class's table — `rx6f4s`, one of
four *DEEBOT T90 PRO OMNI* classes whose modules are byte-identical in
deebot-client 18.5.1. That table supplies the object the library needs to talk
at all, and the probe order. It does not decide which entities exist.

## Supported functions

Entities appear for whatever the robot answers for, which on a T90 PRO OMNI
Care is: the vacuum itself (start, pause, stop, return, locate, fan speed),
battery, error code and problem, state, work mode, water flow and mop-attached,
consumable wear and its reset buttons, station state and station actions,
auto-empty frequency, cleaning and lifetime statistics, the last clean, Wi-Fi
signal, volume, cleaning passes, mop wash interval, child lock, TrueDetect,
ZigZag mopping, voice assistant, continue-after-charging and automatic firmware
updates.

On a robot that answers fewer of those, fewer entities appear. That is the
whole point: **degrade to fewer features rather than to none.**

## Installation

Requires Home Assistant **2026.9.0** or newer and [HACS](https://hacs.xyz).

[![Open your Home Assistant instance and add this repository to HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=jrackerby&repository=ha-deebot-discovery&category=integration)

Or by hand: HACS → three-dot menu → **Custom repositories** → add
`https://github.com/jrackerby/ha-deebot-discovery` as an **Integration**. Then
install it and restart Home Assistant.

[![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=deebot_discovery)

Or **Settings → Devices & Services → Add Integration → Deebot Discovery**.

It can run beside the built-in `ecovacs` integration on the same account: both
use `deebot-client`, which is why this integration declares a version *floor*
for that library rather than a pin — a pin would fight core's own.

### What it asks for

| Field | Notes |
| --- | --- |
| Email address | The Ecovacs account the robot is registered to. |
| Password | Stored in the config entry, as Home Assistant stores every cloud credential. |
| Country | Must match the country the Ecovacs app uses — it selects which regional servers the robot is reachable on. |
| Verification code | Asked only when Ecovacs demands it. One-time, emailed, and per Home Assistant installation rather than per sign-in. |

If the account has more than one robot, the flow asks which. One entry per
robot, so removing one leaves the others untouched.

## How data is updated

Readings arrive over **MQTT push**. Nothing polls them.

Separately, a **probe pass** runs every three hours. That pass is not a state
refresh: it is what decides which entities should exist. It keeps running
rather than settling once because a firmware update can add a function, and
because a demotion costs three consecutive misses across VOID-free passes.

A capability promoted by a later pass gets its entity without a reload.

## Removing it

Delete the config entry (**Settings → Devices & Services**, three dots,
**Delete**). The device and its entities go with it.

**The capability map goes too, and nothing else keeps a copy.** It is this
integration's own ledger — what was measured about this robot and when. That
is deliberate: a map restored for a robot that is no longer on the account
would be belief with no measurement behind it. Adding the robot again rebuilds
the map, which costs one probe pass, not a reinstall.

## Known limitations

- **Maps are not implemented.** No room cleaning, no map card, no positions.
  The vacuum cleans everything or it cleans nothing.
- **The option lists are borrowed.** There is no read command that enumerates
  the fan speeds or work modes a robot *accepts* — the protocol answers with
  the current value and nothing else. Those lists come from the sibling
  table. The entity only exists because the robot answered the read that owns
  them, and an option the robot rejects raises rather than failing silently,
  but the list itself is not measured.
- **A demoted capability keeps its entity**, reading unavailable. Promotion is
  cheap and reversible; deletion would take the user's automations and history
  with it on the strength of three consecutive misses.
- **No local control.** Everything goes through the Ecovacs cloud, including
  the probe passes.
- **The seed is a dependency on a corner of deebot-client that moves.** If the
  sibling class is ever dropped from the library, setup fails loudly with a
  message saying so. CI checks it on every run.
- **The MQTT TLS policy is the library's, not this integration's.**
  deebot-client connects to the Ecovacs broker with certificate verification
  and hostname checking off, because the broker's certificate does not
  validate. This integration never passes an SSL context, so it never restates
  that decision — it calls the library's own factory on a worker thread, which
  keeps the CA-bundle read off the event loop without changing what the
  library does with the context afterwards.

## Troubleshooting

**No entities at all after setup.** The first probe pass establishes what
exists, and it runs before any platform is set up. If it could not establish
anything, setup fails and retries rather than loading an empty entry — so an
entry that is retrying means the robot is not answering. Check that the robot
is powered and on wifi, and that the country matches the Ecovacs app's.

**Fewer entities than expected.** Download diagnostics (device page → three
dots → **Download diagnostics**). Every capability is listed with what it
means, whether it was probed or inferred, whether it is currently usable, and
how many consecutive misses it has. A capability at `unknown` has not been
established; one at `unsupported` missed three passes in a row.

**An entity is unavailable.** Three different things produce that: the cloud
is unreachable, the cloud says the robot is offline, or the capability behind
it was demoted. The connectivity binary sensor stays available in all three —
it is the one entity that must not disappear with its subject — so read it
first.

**A command did nothing and raised.** The message names the command. Ecovacs
answers a command it cannot carry out the same way it answers one the robot
does not support, so a repeated failure on one entity is worth reading
diagnostics over.

## Tests

```
./tools/run_tests.sh
```

Standard library only — no Home Assistant, no deebot-client, no pytest. Four
suites: the resolver, the raw-response classifier, the capability catalogue,
and a static wiring sweep over the modules a plain `python3` cannot import.

Every suite asserts deliberately **wrong** outcomes for real scenarios and
proves each one actually fails before trusting any pass. A suite that cannot
fail is not evidence.

What the suites do **not** prove is that any of it runs: that is the CI
`imports` job, which installs real Home Assistant and real deebot-client and
imports every module against them.

## Branding

The mark in `brand/` is served by Home Assistant itself, not by the brands CDN.
Since core 2026.3 a custom integration ships its own brand images: the loader
treats a top-level `brand/` directory as branding, and
`/api/brands/integration/deebot_discovery/icon.png` returns those bytes directly,
falling through to the CDN only if the file is absent. The
`custom_integrations/` folder of `home-assistant/brands` is the legacy path for
this and no pull request against it is needed.

Four files, and no `logo.png`: the mark is square, so the brands specification
says to ship the icon alone and core's own fallback chain resolves `logo.png`
to `icon.png`. The `dark_` pair is not cosmetic — the mark's navy against a
dark card is a near-invisible smudge, so the dark variant lifts value while
holding hue.

| URL | File |
| --- | --- |
| `/api/brands/integration/deebot_discovery/icon.png` | `brand/icon.png` (256×256) |
| `/api/brands/integration/deebot_discovery/icon@2x.png` | `brand/icon@2x.png` (512×512) |
| `/api/brands/integration/deebot_discovery/dark_icon.png` | `brand/dark_icon.png` (256×256) |
| `/api/brands/integration/deebot_discovery/dark_icon@2x.png` | `brand/dark_icon@2x.png` (512×512) |

Those URLs are what a dashboard outside Home Assistant should reference; they
need a bearer token like any other API path. `tests/test_brand.py` holds the
files to the specification, because nothing in either serving path validates
them — a non-square icon or one that kept its white matte is served exactly as
committed.

## Layout

The integration's modules sit at the repository root rather than under
`custom_components/`, which is the layout HACS installs from — `hacs.json`
declares `content_in_root`. CI stages the `custom_components/deebot_discovery/`
layout that hassfest expects.

The domain is `deebot_discovery`. It was `deebot_estate` before the first
release; nothing installed from a release ever carried the old name.

## Contributing

Robot reports are the most useful contribution — see *Supported devices*. For
code, run `./tools/run_tests.sh` before opening a pull request; CI runs the
same suites plus hassfest, HACS validation and an import of every module
against the oldest Home Assistant `hacs.json` claims.

## License

[MIT](LICENSE).
