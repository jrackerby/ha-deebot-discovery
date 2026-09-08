# deebot-estate

A Home Assistant integration for Ecovacs Deebot robots that **discovers** what
a robot supports instead of looking it up in a table.

**This repository is not yet an installable integration.** It holds the
capability resolver and its constants — no `manifest.json`, no `hacs.json`, no
platforms, no config flow. HACS cannot install it, and it is published here for
the resolver's sake rather than as something to add to Home Assistant.

## Why it exists

`deebot-client` resolves a robot's capabilities from a per-model lookup table
keyed on an opaque class string. This estate's robot — a DEEBOT T90 PRO OMNI
Care, class `nv8fz5` — is in none of that library's 248 hardware modules, even
though four sibling T90 PRO OMNI classes are. Since v14 removed the fallback,
`get_static_device_info` returns `None` for an unknown class and the robot gets
**zero** entities.

The library is 55,184 lines of per-model capability tables against 9,021 lines
of model-agnostic protocol. The part that fails here is the table; the part
worth depending on is the protocol.

## The hard part

The robot does not publish what it supports. There is no
`getSupportedFunctions`-style command anywhere in the protocol — which is
precisely why upstream hand-maintains those tables. So support has to be
inferred from whether a command answers, and **the failure code that means
"not supported" is the same code that means "the network ate it"**
(`errno 500`, in deebot-client's own words).

`probe.py` is the whole answer to that ambiguity, and it imports nothing from
`homeassistant` and nothing from `deebot_client`, so it is exercisable on a
plain `python3` with no HA, no cloud account and no robot.

Three rules, each because of a specific way the ambiguity can produce a
confident wrong answer:

1. **Every pass carries a control** — a command this robot has already
   answered. Control fails → the pass is **VOID**, not "unsupported", and the
   previous map stands. Without this, one bad minute of wifi silently strips
   every capability at once, and the stripped map looks exactly like a correct
   reading of a robot that lost its features.
2. **A pass with no control is also VOID**, not merely unguarded.
3. **Promote on one OK; demote only after `miss_threshold` consecutive misses
   across VOID-free passes.** Fall dwell, never rise dwell.

It never invents a capability it has not seen answer. A seed hypothesis
borrowed from a sibling model marks a key as worth probing *first*; it never
marks it *supported*.

## Tests

```
./tools/run_tests.sh
```

Standard library only. `FAIL_CASES` assert deliberately wrong outcomes for real
scenarios and the runner proves every one of them actually fails before
trusting any pass — a suite that cannot fail is not evidence.

## Layout

Root layout: the integration's modules sit at the repository root rather than
under `custom_components/`, so the `hacs.json` this repo does not yet carry
will declare `content_in_root: true` when it arrives.
