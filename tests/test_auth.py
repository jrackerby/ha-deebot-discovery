#!/usr/bin/env python3
"""The login order auth.py enforces: account credentials first, password last.

WHY THIS SUITE EXISTS. Ecovacs answers 1013 to every PASSWORD login from an
affected account, verified device or not, so the one way the robot stays
signed in is the uid + accessToken pair a login or verification hands back
(GH-35). Every defect this module can have is silent until the next restart:

  * Renew from the pair but ALSO call the password endpoint, and the 1013
    that comes back opens a re-auth for an entry that was signed in.
  * Capture the pair from `user/login` but not from `user/verifyDevice`, and
    the one flow an affected account can complete stores nothing.
  * Persist the pair only when it changes -- and never notice that it did.

deebot-client is not installed where the suites run, so its authentication
module is STUBBED here with the four name-mangled methods auth.py reaches,
recording every call; the stub's cloud is scripted per case. The stub is the
contract auth.py has with 18.5.1 and 18.6.0, whose private surface is the
same; a library that moves it fails auth.py's import check by name.

SELF-TEST, LAW.md §4: the same code is asked to satisfy orders it must not.
"""

import asyncio
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _harness import load, run_suite

# -- a deebot_client just wide enough for auth.py ----------------------------


class AuthenticationError(Exception):
    pass


class DeviceVerificationRequiredError(AuthenticationError):
    pass


class Credentials:
    def __init__(self, token, user_id, expires_at):
        self.token, self.user_id, self.expires_at = token, user_id, expires_at


class _AuthClient:
    """Records calls; `cloud` is the per-case script."""

    def __init__(self, config, account_id, password_hash):
        self._config, self._account_id, self._password_hash = (
            config,
            account_id,
            password_hash,
        )
        self.calls = []

    async def __call_login_api(self, account_id, password_hash):
        self.calls.append(("login", account_id, password_hash))
        return await self._config.cloud("login")

    async def __call_private_api(self, endpoint, params):
        self.calls.append((endpoint, params))
        return await self._config.cloud(endpoint, params)

    async def __encrypt_account(self, account):
        return f"enc({account})"

    async def __complete_login(self, response, error):
        self.calls.append(("complete", response))
        if not isinstance(response, dict):
            raise AuthenticationError(error)
        if response.get("accessToken") == "expired":
            raise AuthenticationError("0004 user login expired")
        return Credentials(
            f"portal-for-{response['accessToken']}", response["uid"], 10**12
        )


class Authenticator:
    def __init__(self, config, account_id, password_hash):
        self._auth_client = _AuthClient(config, account_id, password_hash)
        self._lock = asyncio.Lock()
        self._credentials = None
        self.set_calls = 0

    def _set_credentials(self, credentials):
        self._credentials = credentials
        self.set_calls += 1

    async def teardown(self):
        pass


def install_stub():
    dc = types.ModuleType("deebot_client")
    auth = types.ModuleType("deebot_client.authentication")
    auth._AuthClient = _AuthClient
    auth.Authenticator = Authenticator
    auth.RestConfiguration = object
    auth._ANDROID_MODEL = "Pixel 7"
    auth._ANDROID_SYSTEM = "Android 14"
    exc = types.ModuleType("deebot_client.exceptions")
    exc.AuthenticationError = AuthenticationError
    exc.DeviceVerificationRequiredError = DeviceVerificationRequiredError
    models = types.ModuleType("deebot_client.models")
    models.Credentials = Credentials
    dc.authentication, dc.exceptions, dc.models = auth, exc, models
    for name, module in {
        "deebot_client": dc,
        "deebot_client.authentication": auth,
        "deebot_client.exceptions": exc,
        "deebot_client.models": models,
    }.items():
        sys.modules[name] = module


install_stub()
auth = load("auth")

PAIR = {"uid": "u1", "accessToken": "tok-1"}
PAIR2 = {"uid": "u1", "accessToken": "tok-2"}


class Config:
    def __init__(self, script):
        self.script = script

    async def cloud(self, endpoint, params=None):
        answer = self.script[endpoint]
        if isinstance(answer, Exception):
            raise answer
        return answer


def build(script, account=None):
    stored = []

    async def on_account(pair):
        stored.append(pair)

    a = auth.AccountAuthenticator(
        Config(script), "joel@example", "md5pw", account=account, on_account=on_account
    )
    return a, stored


def endpoints(a):
    return [c[0] for c in a._auth_client.calls]


def run(coro):
    return asyncio.run(coro)


# -- cases --------------------------------------------------------------------


def renews_from_the_pair_without_the_password():
    a, stored = build({"login": DeviceVerificationRequiredError("1013")}, account=PAIR)

    async def go():
        c = await a.authenticate()
        return c

    c = run(go())
    return {
        "endpoints": endpoints(a),
        "user": c.user_id,
        "stored": stored,
        "account": a.account,
    }


def falls_back_to_the_password_when_the_pair_is_dead():
    a, stored = build(
        {"login": {**PAIR2, "extra": 1}},
        account={"uid": "u1", "accessToken": "expired"},
    )
    run(a.authenticate())
    return {"endpoints": endpoints(a), "stored": stored, "account": a.account}


def the_password_login_captures_the_pair():
    a, stored = build({"login": {**PAIR, "verifyDevice": None}})
    run(a.authenticate())
    return {"endpoints": endpoints(a), "stored": stored, "account": a.account}


def a_1013_password_login_propagates_and_stores_nothing():
    a, stored = build({"login": DeviceVerificationRequiredError("1013")})
    try:
        run(a.authenticate())
    except DeviceVerificationRequiredError:
        raised = True
    else:
        raised = False
    return {
        "raised": raised,
        "stored": stored,
        "account": a.account,
        "set": a.set_calls,
    }


def verification_captures_the_pair_and_signs_in():
    a, stored = build({"user/verifyDevice": PAIR})

    async def go():
        c = await a.verify_device(" 123456 ")
        again = await a.authenticate()  # must reuse, not log in
        return c, again

    c, again = run(go())
    verify = a._auth_client.calls[0]
    return {
        "endpoint": verify[0],
        "code": verify[1]["verifyCode"],
        "encrypted": verify[1]["encryptAccount"],
        "endpoints": endpoints(a),
        "user": c.user_id,
        "same": again is c,
        "stored": stored,
        "account": a.account,
    }


def a_valid_login_is_not_repeated():
    a, _ = build({"login": PAIR}, account=PAIR)

    async def go():
        await a.authenticate()
        await a.authenticate()
        await a.authenticate(force=True)

    run(go())
    return {"endpoints": endpoints(a)}


def an_unchanged_pair_is_not_stored_again():
    a, stored = build({"login": PAIR}, account={"uid": "u1", "accessToken": "expired"})
    run(a.authenticate())
    a2, stored2 = build({"login": PAIR}, account=PAIR)
    run(a2.authenticate(force=True))
    return {"stored_once": stored, "stored_same": stored2}


def the_import_check_names_what_moved():
    saved = sys.modules["deebot_client.authentication"]._AuthClient
    try:

        class Moved:  # no mangled methods at all
            pass

        sys.modules["deebot_client.authentication"]._AuthClient = Moved
        sys.modules.pop("deebot_discovery.auth", None)
        try:
            load("auth")
        except ImportError as err:
            message = str(err)
        else:
            message = ""
    finally:
        sys.modules["deebot_client.authentication"]._AuthClient = saved
        sys.modules.pop("deebot_discovery.auth", None)
        sys.modules["deebot_discovery.auth"] = auth
    return {
        "names_first": "call_login_api" in message,
        "points_at_1743": "1743" in message,
    }


CASES = [
    (
        "renews from the pair; the password endpoint is never called",
        renews_from_the_pair_without_the_password,
        {"endpoints": ["complete"], "user": "u1", "stored": [], "account": PAIR},
    ),
    (
        "a dead pair falls back to the password, and the new pair is stored",
        falls_back_to_the_password_when_the_pair_is_dead,
        {
            "endpoints": ["complete", "login", "complete"],
            "stored": [PAIR2],
            "account": PAIR2,
        },
    ),
    (
        "a password login captures the pair it returns",
        the_password_login_captures_the_pair,
        {"endpoints": ["login", "complete"], "stored": [PAIR], "account": PAIR},
    ),
    (
        "a 1013 from the password login propagates, nothing is stored",
        a_1013_password_login_propagates_and_stores_nothing,
        {"raised": True, "stored": [], "account": None, "set": 0},
    ),
    (
        "verification posts the stripped code, captures the pair, signs in once",
        verification_captures_the_pair_and_signs_in,
        {
            "endpoint": "user/verifyDevice",
            "code": "123456",
            "encrypted": "enc(joel@example)",
            "endpoints": ["user/verifyDevice", "complete"],
            "user": "u1",
            "same": True,
            "stored": [PAIR],
            "account": PAIR,
        },
    ),
    (
        "valid credentials are reused; force renews from the pair, not the password",
        a_valid_login_is_not_repeated,
        {"endpoints": ["complete", "complete"]},
    ),
    (
        "a pair is stored when it changes and only then",
        an_unchanged_pair_is_not_stored_again,
        {"stored_once": [PAIR], "stored_same": []},
    ),
    (
        "a deebot-client whose private surface moved fails the import by name",
        the_import_check_names_what_moved,
        {"names_first": True, "points_at_1743": True},
    ),
]

FAIL_CASES = [
    (
        "renewal that also hit the password endpoint",
        renews_from_the_pair_without_the_password,
        {"endpoints": ["login", "complete"]},
    ),
    (
        "a verification that stored nothing",
        verification_captures_the_pair_and_signs_in,
        {"stored": []},
    ),
    (
        "a 1013 that was swallowed",
        a_1013_password_login_propagates_and_stores_nothing,
        {"raised": False},
    ),
]

if __name__ == "__main__":
    sys.exit(run_suite(CASES, FAIL_CASES))
