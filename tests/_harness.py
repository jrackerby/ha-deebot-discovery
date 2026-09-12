"""Shared plumbing for the suites. NOT a suite itself.

Deliberately named with a leading underscore rather than `test_`: the runner
globs `tests/test_*.py` and asserts the match count before judging anything,
so a support module picked up as a suite would be a suite that runs nothing
and passes.

THE SELF-TEST DISCIPLINE LIVES HERE, once, so no suite can quietly omit it.
`run_suite` refuses to report any PASS until every FAIL_CASE has actually
failed. A suite that cannot fail is not evidence, and the way that usually
happens is not malice -- it is a refactor that makes an assertion vacuous
while every line still prints green.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types

#: tests/ sits directly under the component root in both layouts: this repo
#: standing alone, and this repo installed as custom_components/deebot_discovery.
#: One expression covers both.
PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load(*names):
    """Load modules by path, bypassing the package __init__.

    `__init__.py` imports homeassistant, and these modules do not. Loading
    them by path is what keeps the suite runnable on a plain python3 with
    neither Home Assistant nor deebot-client installed -- which is the whole
    reason the pure layer exists.
    """
    pkg = sys.modules.get("deebot_discovery")
    if pkg is None:
        pkg = types.ModuleType("deebot_discovery")
        pkg.__path__ = [PKG_DIR]
        sys.modules["deebot_discovery"] = pkg

    loaded = []
    for name in names:
        full = f"deebot_discovery.{name}"
        if full not in sys.modules:
            spec = importlib.util.spec_from_file_location(
                full, os.path.join(PKG_DIR, f"{name}.py")
            )
            module = importlib.util.module_from_spec(spec)
            sys.modules[full] = module
            spec.loader.exec_module(module)
        loaded.append(sys.modules[full])
    return loaded[0] if len(loaded) == 1 else tuple(loaded)


def diff(expected, got):
    """Which of `expected`'s keys `got` does not match."""
    return [
        f"{k}: expected {v!r}, got {got.get(k)!r}"
        for k, v in expected.items()
        if got.get(k) != v
    ]


def run_suite(cases, fail_cases):
    """Run the self-test, then the cases. Returns a process exit code."""
    print("SELF-TEST -- every case below must FAIL:")
    ok = True
    for name, fn, wrong in fail_cases:
        found = diff(wrong, fn())
        print(f"  {'ok (failed as required)' if found else 'BROKEN (passed!)':<24} {name}")
        ok = ok and bool(found)
    if not ok:
        print("\nself-test did not fail where it must -- no result below is evidence")
        return 1

    print("\nCASES:")
    failed = 0
    for name, fn, expected in cases:
        found = diff(expected, fn())
        print(f"  {'PASS' if not found else 'FAIL'}  {name}")
        for line in found:
            print(f"          {line}")
        failed += bool(found)
    print(f"\n{len(cases) - failed}/{len(cases)} cases passed")
    return 1 if failed else 0
