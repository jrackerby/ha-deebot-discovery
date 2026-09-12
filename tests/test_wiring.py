#!/usr/bin/env python3
"""Static checks over the modules a plain python3 cannot import.

WHY A STATIC SUITE AT ALL. Most of this integration imports `homeassistant`
and `deebot_client`, neither of which is installed where the pure suites run,
and the CI job that DOES import them against real core only proves the
modules load. Between those two sits a class of defect that neither catches:
an entity description whose capability key is misspelled, a translation key
with no string behind it, a platform listed and never written. Each of those
imports cleanly, runs cleanly, and is wrong on a dashboard.

So these checks read the source with `ast` and with plain text. That is a
weaker instrument than running the code, and it is stated here so nobody
reads more into a green run than is in it: this suite proves the pieces refer
to each other correctly, never that any of them works.

THE SELF-TEST IS THE POINT. A static check is unusually easy to write in a
form that can never fail -- a walk that finds no nodes reports no findings,
and prints exactly like a clean sweep. So every check below is also run
against a deliberately BROKEN copy of the tree, and no result above is
trusted until each one has found the break.
"""

import ast
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite  # noqa: E402

PKG_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

const, discovery = load("const", "discovery")

#: Modules that must stay importable with neither Home Assistant nor
#: deebot-client installed. This is the contract the pure suites depend on.
PURE_MODULES = ("const", "probe", "classify", "discovery")

#: Every platform module, by the Home Assistant platform name it serves.
PLATFORM_MODULES = (
    "binary_sensor",
    "button",
    "number",
    "select",
    "sensor",
    "switch",
    "vacuum",
)

FORBIDDEN_IN_PURE = ("homeassistant", "deebot_client")


# --- reading the tree -------------------------------------------------------


def read_tree():
    """Every python module at the component root, plus the two json files."""
    tree = {}
    for name in sorted(os.listdir(PKG_DIR)):
        if name.endswith(".py"):
            with open(os.path.join(PKG_DIR, name), encoding="utf-8") as handle:
                tree[name] = handle.read()
    for name in ("manifest.json", "hacs.json", "strings.json"):
        with open(os.path.join(PKG_DIR, name), encoding="utf-8") as handle:
            tree[name] = handle.read()
    suite_dir = os.path.dirname(os.path.abspath(__file__))
    for name in sorted(os.listdir(suite_dir)):
        if name.endswith(".py"):
            with open(os.path.join(suite_dir, name), encoding="utf-8") as handle:
                tree[f"tests/{name}"] = handle.read()
    return tree


def mutate(tree, path, old, new):
    """A copy of the tree with one exact substitution, asserted to have landed.

    The assert is not decoration. A self-test whose mutation silently failed
    to apply would run every check against the REAL tree, find nothing, and
    report that the checks cannot fail -- turning the one guard against a
    vacuous suite into the thing that is vacuous.
    """
    broken = dict(tree)
    source = broken[path]
    if source.count(old) != 1:
        msg = f"mutation for {path} matched {source.count(old)} times, expected 1"
        raise AssertionError(msg)
    broken[path] = source.replace(old, new)
    return broken


def _modules(tree):
    return {
        name[:-3]: ast.parse(source, filename=name)
        for name, source in tree.items()
        if name.endswith(".py") and "/" not in name
    }


def _imported_roots(module):
    roots = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def _string_literals(module):
    return {
        node.value
        for node in ast.walk(module)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def _description_calls(module):
    """Every call to something whose name ends in `EntityDescription`."""
    for node in ast.walk(module):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name and name.endswith("EntityDescription"):
                yield node


def _keyword(call, name):
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


# --- the checks -------------------------------------------------------------
# Each returns a list of findings. Empty means clean.


def check_everything_parses(tree):
    findings = []
    for name, source in tree.items():
        if not name.endswith(".py"):
            continue
        try:
            ast.parse(source, filename=name)
        except SyntaxError as err:
            findings.append(f"{name} does not parse: {err}")
    return findings


def check_pure_layer_stays_pure(tree):
    """The contract every docstring in the pure layer claims."""
    findings = []
    for name, module in _modules(tree).items():
        if name not in PURE_MODULES:
            continue
        for root in sorted(_imported_roots(module) & set(FORBIDDEN_IN_PURE)):
            findings.append(f"{name}.py imports {root}, which breaks the pure contract")
    return findings


def check_seed_layer_has_no_home_assistant(tree):
    """seed.py speaks the vendor protocol and must know nothing about HA."""
    module = _modules(tree).get("seed")
    if module is None:
        return ["seed.py is missing"]
    if "homeassistant" in _imported_roots(module):
        return ["seed.py imports homeassistant"]
    return []


def check_every_probed_capability_has_a_command(tree):
    """`seed._PROBES` and the catalogue must name the same capabilities.

    Read statically rather than by import, because importing seed.py needs
    deebot-client. The module asserts the same thing at import time; this is
    the copy that runs where the library is absent, which is where a mistake
    is most likely to be made.
    """
    module = _modules(tree).get("seed")
    if module is None:
        return ["seed.py is missing"]

    probes = None
    for node in ast.walk(module):
        annotated = (
            isinstance(node, ast.AnnAssign)
            and getattr(node.target, "id", "") == "_PROBES"
        )
        plain = isinstance(node, ast.Assign) and any(
            getattr(t, "id", "") == "_PROBES" for t in node.targets
        )
        if annotated or plain:
            probes = node.value
    if probes is None:
        return ["seed.py declares no _PROBES map"]
    if isinstance(probes, ast.Call):  # wrapped, e.g. MappingProxyType({...})
        probes = probes.args[0] if probes.args else None
    if not isinstance(probes, ast.Dict):
        return ["seed.py's _PROBES is not a dict literal and cannot be read statically"]

    keys = {
        node.value
        for node in probes.keys
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    catalogue = set(discovery.PROBED_KEYS)
    findings = []
    for key in sorted(catalogue - keys):
        findings.append(f"capability {key!r} is probed but seed.py has no command for it")
    for key in sorted(keys - catalogue):
        findings.append(f"seed.py probes {key!r}, which is not a catalogue capability")
    return findings


def check_descriptions_name_a_real_capability(tree):
    """Every entity description carries a `capability` and it is a real key."""
    findings = []
    known = set(discovery.BY_KEY)
    for name, module in _modules(tree).items():
        if name not in PLATFORM_MODULES:
            continue
        for call in _description_calls(module):
            value = _keyword(call, "capability")
            if value is None:
                findings.append(
                    f"{name}.py line {call.lineno}: an entity description with no "
                    "capability -- an entity nobody measured"
                )
                continue
            if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
                findings.append(
                    f"{name}.py line {call.lineno}: capability is not a literal, so "
                    "nothing can check it"
                )
                continue
            if value.value not in known:
                findings.append(
                    f"{name}.py line {call.lineno}: capability {value.value!r} is not "
                    "in the catalogue"
                )
    return findings


def check_no_capability_is_unreachable(tree):
    """Every catalogue key is named by some module that can act on it.

    A capability nobody consumes is measured every pass, stored, reported in
    diagnostics -- and can never produce anything. That is not harmless: it
    reads in a dump exactly like a capability that works.
    """
    modules = _modules(tree)
    consumers = {
        name: _string_literals(module)
        for name, module in modules.items()
        if name in PLATFORM_MODULES
    }
    findings = []
    for key in discovery.BY_KEY:
        if not any(key in literals for literals in consumers.values()):
            findings.append(f"capability {key!r} is measured but no platform reads it")
    return findings


def check_platform_list_matches_the_files(tree):
    """`PLATFORMS` in __init__.py and the platform modules must agree."""
    module = _modules(tree).get("__init__")
    if module is None:
        return ["__init__.py is missing"]

    declared = set()
    for node in ast.walk(module):
        if isinstance(node, ast.Attribute) and getattr(node.value, "id", "") == "Platform":
            declared.add(node.attr.lower())
    present = {name for name in _modules(tree) if name in PLATFORM_MODULES}

    findings = []
    for name in sorted(declared - present):
        findings.append(f"PLATFORMS declares {name}, but there is no {name}.py")
    for name in sorted(present - declared):
        findings.append(f"{name}.py exists, but PLATFORMS does not declare it")
    return findings


def check_translations_and_code_agree(tree):
    """Every translation key used has a string, and every string is used."""
    strings = json.loads(tree["strings.json"])
    entity = strings.get("entity", {})
    modules = _modules(tree)
    findings = []

    for platform in PLATFORM_MODULES:
        module = modules.get(platform)
        if module is None:
            continue
        declared = set(entity.get(platform, {}))
        used = {
            keyword.value.value
            for call in _description_calls(module)
            for keyword in call.keywords
            if keyword.arg == "translation_key"
            and isinstance(keyword.value, ast.Constant)
            and isinstance(keyword.value.value, str)
        }
        literals = _string_literals(module)

        for key in sorted(used - declared):
            findings.append(
                f"{platform}.py uses translation key {key!r}, which strings.json "
                "does not define"
            )
        for key in sorted(declared - literals):
            findings.append(
                f"strings.json defines entity.{platform}.{key}, which {platform}.py "
                "never names"
            )
    return findings


def check_suite_imports_only_the_standard_library(tree):
    """The suites declare no dependencies, so they may have none.

    Extracting a component exposes its suite's undeclared dependencies, and
    the first run on a clean runner is when it learns this. The check is
    cheap and static, so it runs on every commit instead.
    """
    findings = []
    allowed = set(sys.stdlib_module_names) | {"_harness"}
    for name, source in tree.items():
        if not name.startswith("tests/") or not name.endswith(".py"):
            continue
        module = ast.parse(source, filename=name)
        for root in sorted(_imported_roots(module) - allowed):
            findings.append(
                f"{name} imports {root!r}, which is neither the standard library "
                "nor this suite's own support module"
            )
    return findings


def check_manifest_agrees_with_the_code(tree):
    manifest = json.loads(tree["manifest.json"])
    hacs = json.loads(tree["hacs.json"])
    findings = []

    if manifest.get("domain") != const.DOMAIN:
        findings.append(
            f"manifest domain {manifest.get('domain')!r} is not const.DOMAIN "
            f"{const.DOMAIN!r} -- the storage key and every entity id hang off this"
        )
    if not manifest.get("config_flow"):
        findings.append("manifest does not declare config_flow, but config_flow.py exists")
    if not any(
        req.startswith("deebot-client") for req in manifest.get("requirements", [])
    ):
        findings.append("manifest declares no deebot-client requirement")
    for field in ("documentation", "issue_tracker", "codeowners", "version"):
        if not manifest.get(field):
            findings.append(f"manifest is missing {field}")
    # Root layout: the modules sit at the repository root, so HACS has to be
    # told, or it installs a directory with no manifest in it.
    if hacs.get("content_in_root") is not True:
        findings.append("hacs.json does not declare content_in_root for a root layout")
    if not hacs.get("homeassistant"):
        findings.append("hacs.json declares no minimum Home Assistant version")
    return findings


def check_the_password_is_hashed_before_it_is_sent(tree):
    """`Authenticator`'s third argument must be an md5, never the password.

    THE DEFECT THIS EXISTS FOR SHIPPED ONCE. deebot-client names the parameter
    `password_hash` and puts whatever it is handed straight into the login
    call's `password` field, so passing the plaintext is not a type error, not
    an import error and not a crash -- the cloud simply answers `invalid_auth`,
    which from inside the config flow is indistinguishable from the owner
    mistyping their password. Both call sites had it, the modules imported
    cleanly against real core, and every suite was green.

    Static because it has to be: the only dynamic proof is a live login
    against someone's real Ecovacs account, which no suite can have.
    """
    findings = []
    for name, module in _modules(tree).items():
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            if (getattr(node.func, "id", None) or getattr(node.func, "attr", None)) != (
                "Authenticator"
            ):
                continue
            # Positional third, or the keyword the library names it by.
            argument = node.args[2] if len(node.args) > 2 else _keyword(node, "password_hash")
            hashed = (
                isinstance(argument, ast.Call)
                and (getattr(argument.func, "id", None) or getattr(argument.func, "attr", None))
                == "md5"
            )
            if not hashed:
                findings.append(
                    f"{name}.py line {node.lineno}: Authenticator is built with a "
                    "value that did not pass through md5 -- deebot-client sends "
                    "that field verbatim, so a plaintext password reads as "
                    "invalid_auth and looks like a typo"
                )
    return findings


def check_the_mqtt_config_keeps_the_library_tls_settings(tree):
    """`create_mqtt_config` is handed no context, or a fully prepared one.

    THE INVERSE OF WHAT THIS CHECK USED TO ASSERT, AND THE REVERSAL COST AN
    OUTAGE. It was added to require an `ssl_context` argument, so the context
    build would happen off the event loop. That is a real concern and it was
    the wrong lever.

    The library's default branch does not merely build a context: it then
    turns off hostname checking and certificate verification, because the
    broker's certificate does not validate. Passing a context takes the other
    branch and skips that, so a context that is merely correct cannot connect
    and the client reconnect-loops -- while REST-based probes keep answering,
    so every entity holds a plausible stale value and nothing looks wrong.

    So: no argument is fine. An argument is fine ONLY if the module also
    prepares the context the way the library would. Anything else is the
    outage again.
    """
    findings = []
    for name, module in _modules(tree).items():
        calls = [
            node
            for node in ast.walk(module)
            if isinstance(node, ast.Call)
            and (getattr(node.func, "id", None) or getattr(node.func, "attr", None))
            == "create_mqtt_config"
            and _keyword(node, "ssl_context") is not None
        ]
        if not calls:
            continue
        prepared = {
            target.attr
            for node in ast.walk(module)
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Attribute)
        }
        missing = {"check_hostname", "verify_mode"} - prepared
        for call in calls:
            if missing:
                findings.append(
                    f"{name}.py line {call.lineno}: create_mqtt_config is handed an "
                    f"ssl_context but the module never sets {', '.join(sorted(missing))} "
                    "-- the library's own default branch does, and skipping it is what "
                    "reconnect-looped the broker"
                )
    return findings


CHECKS = (
    ("everything parses", check_everything_parses),
    ("the pure layer imports neither HA nor the vendor library", check_pure_layer_stays_pure),
    ("the seed layer knows nothing about Home Assistant", check_seed_layer_has_no_home_assistant),
    ("every probed capability has a read command", check_every_probed_capability_has_a_command),
    ("every entity description names a real capability", check_descriptions_name_a_real_capability),
    ("no capability is measured and then unreachable", check_no_capability_is_unreachable),
    ("the platform list matches the platform files", check_platform_list_matches_the_files),
    ("translations and code agree", check_translations_and_code_agree),
    ("the suite imports only the standard library", check_suite_imports_only_the_standard_library),
    ("the manifest agrees with the code", check_manifest_agrees_with_the_code),
    ("the password is hashed before it is sent", check_the_password_is_hashed_before_it_is_sent),
    ("the mqtt config keeps the library's tls settings", check_the_mqtt_config_keeps_the_library_tls_settings),
)

TREE = read_tree()


def _case(check):
    return lambda: {"findings": check(TREE)}


def _broken_case(check, path, old, new):
    def run():
        return {"findings": check(mutate(TREE, path, old, new))}

    return run


CASES = [(name, _case(check), {"findings": []}) for name, check in CHECKS]

# Each mutation below is a real defect of the class its check exists to
# catch, and each must be FOUND -- expecting no findings from a broken tree
# is the wrong answer this proves the check refuses.
FAIL_CASES = [
    (
        "self-test: a syntax error must be found",
        _broken_case(
            check_everything_parses,
            "probe.py",
            "    states = dict(previous)",
            "    states = dict(previous",
        ),
        {"findings": []},
    ),
    (
        "self-test: homeassistant in the pure layer must be found",
        _broken_case(
            check_pure_layer_stays_pure,
            "classify.py",
            "from .const import ProbeOutcome",
            "from homeassistant.core import callback\nfrom .const import ProbeOutcome",
        ),
        {"findings": []},
    ),
    (
        "self-test: homeassistant in the seed layer must be found",
        _broken_case(
            check_seed_layer_has_no_home_assistant,
            "seed.py",
            "from .discovery import PROBED_KEYS",
            "from homeassistant.core import callback\nfrom .discovery import PROBED_KEYS",
        ),
        {"findings": []},
    ),
    (
        "self-test: a probed capability with no command must be found",
        _broken_case(
            check_every_probed_capability_has_a_command,
            "seed.py",
            '"battery": GetBattery,',
            "",
        ),
        {"findings": []},
    ),
    (
        "self-test: a misspelled capability must be found",
        _broken_case(
            check_descriptions_name_a_real_capability,
            "sensor.py",
            'capability="battery",',
            'capability="batery",',
        ),
        {"findings": []},
    ),
    (
        "self-test: an entity description with no capability must be found",
        _broken_case(
            check_descriptions_name_a_real_capability,
            "sensor.py",
            'capability="network",\n        key="wifi_rssi",',
            'key="wifi_rssi",',
        ),
        {"findings": []},
    ),
    (
        "self-test: a capability no platform reads must be found",
        _broken_case(
            check_no_capability_is_unreachable,
            "sensor.py",
            'capability="clean_log",',
            'capability="battery",',
        ),
        {"findings": []},
    ),
    (
        "self-test: a platform with no module must be found",
        _broken_case(
            check_platform_list_matches_the_files,
            "__init__.py",
            "Platform.VACUUM,",
            "Platform.VACUUM,\n    Platform.LAWN_MOWER,",
        ),
        {"findings": []},
    ),
    (
        "self-test: a translation key with no string must be found",
        _broken_case(
            check_translations_and_code_agree,
            "switch.py",
            'translation_key="child_lock",',
            'translation_key="childlock",',
        ),
        {"findings": []},
    ),
    (
        "self-test: an undeclared suite dependency must be found",
        _broken_case(
            check_suite_imports_only_the_standard_library,
            "tests/test_classify.py",
            "import os\nimport sys",
            "import os\nimport sys\n\nimport yaml",
        ),
        {"findings": []},
    ),
    (
        "self-test: a plaintext password must be found",
        _broken_case(
            check_the_password_is_hashed_before_it_is_sent,
            "coordinator.py",
            "rest_config, data[CONF_USERNAME], md5(data[CONF_PASSWORD])",
            "rest_config, data[CONF_USERNAME], data[CONF_PASSWORD]",
        ),
        {"findings": []},
    ),
    (
        "self-test: a bare ssl_context on the mqtt config must be found",
        _broken_case(
            check_the_mqtt_config_keeps_the_library_tls_settings,
            "coordinator.py",
            "create_mqtt_config(device_id=device_id, country=country), authenticator",
            "create_mqtt_config(device_id=device_id, country=country, "
            "ssl_context=ssl_context), authenticator",
        ),
        {"findings": []},
    ),
    (
        "self-test: a manifest domain that drifted must be found",
        _broken_case(
            check_manifest_agrees_with_the_code,
            "manifest.json",
            '"domain": "deebot_discovery"',
            '"domain": "deebot_estate"',
        ),
        {"findings": []},
    ),
]


def main():
    return run_suite(CASES, FAIL_CASES)


if __name__ == "__main__":
    sys.exit(main())
