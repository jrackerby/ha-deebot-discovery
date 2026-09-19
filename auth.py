"""The login that Ecovacs still accepts: account credentials, not the password.

Ecovacs answers code 1013 ("Please update to the latest version to continue")
to EVERY password login (`user/login`) from an affected account, verified
device id or not -- measured here on 2026-09-18 (GH-35): the re-auth flow
completed, password, emailed code and robot list, and the coordinator's own
password login with the same device id was refused 400 ms later
(DeebotUniverse/client.py#1777, home-assistant/core#177870, #178405).

What the cloud does accept is the `uid` + `accessToken` that `user/login` and
`user/verifyDevice` return: they mint portal credentials through `getAuthCode`
+ `loginByItToken`, with no password, for as long as Ecovacs honours them. So
the flow keeps that pair, the entry persists it (`CONF_ACCOUNT`), and this
authenticator renews from it before ever touching the password endpoint.

THIS IS deebot-client's OWN UNRELEASED FIX (DeebotUniverse/client.py#1743),
carried here because 18.6.0 does not have it and the robot is down without
it. It reaches `_AuthClient`'s name-mangled methods -- the same four in
18.5.1 and 18.6.0, checked at import so a library that moved them fails
setup with their names rather than with a 1013 that looks like this bug
again. When #1743 ships: delete this module, build
`Authenticator(account_credentials=...)` and use
`subscribe_account_credentials()`.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from deebot_client import authentication as _authentication
from deebot_client.authentication import Authenticator, RestConfiguration
from deebot_client.exceptions import AuthenticationError

from .const import LOGGER

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from deebot_client.models import Credentials

#: `{"uid": ..., "accessToken": ...}` as the cloud returns it; the shape
#: `_AuthClient.__complete_login` consumes, kept verbatim so it is handed
#: straight back.
AccountCredentials = dict[str, str]

_PRIVATE = ("call_login_api", "call_private_api", "complete_login", "encrypt_account")


def _private(client: Any, name: str) -> Any:
    return getattr(client, f"_AuthClient__{name}")


for _name in _PRIVATE:
    if not hasattr(_authentication._AuthClient, f"_AuthClient__{_name}"):
        msg = (
            f"deebot_client._AuthClient has no {_name}: this deebot-client is not the "
            "18.x auth.py was written against -- if it ships client.py#1743, delete "
            "auth.py for Authenticator(account_credentials=...)"
        )
        raise ImportError(msg)


def account_from(response: Any) -> AccountCredentials | None:
    """The account credentials a login or verification response carries."""
    if not isinstance(response, dict):
        return None
    try:
        return {
            "uid": str(response["uid"]),
            "accessToken": str(response["accessToken"]),
        }
    except KeyError:
        return None


class AccountAuthenticator(Authenticator):
    """An Authenticator that logs in from account credentials while it has them.

    `authenticate()` renews from the stored account credentials first and only
    falls back to the password login when that renewal fails; both the
    password login and `verify_device()` capture the account credentials their
    response carries and report them to `on_account`, which is how the config
    entry learns a new pair.
    """

    def __init__(
        self,
        config: RestConfiguration,
        account_id: str,
        password_hash: str,
        *,
        account: AccountCredentials | None = None,
        on_account: Callable[[AccountCredentials], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__(config, account_id, password_hash)
        self._account = dict(account) if account else None
        self._on_account = on_account

    @property
    def account(self) -> AccountCredentials | None:
        """The account credentials the last login or verification returned."""
        return dict(self._account) if self._account else None

    async def authenticate(self, *, force: bool = False) -> Credentials:
        """Authenticate, from the account credentials when there are any."""
        async with self._lock:
            credentials = self._credentials
            if credentials is None or force or credentials.expires_at < time.time():
                credentials = await self._login()
                self._set_credentials(credentials)
            return credentials

    async def verify_device(self, verification_code: str) -> Credentials:
        """Verify the device with the emailed code, keeping what it returns."""
        client = self._auth_client
        async with self._lock:
            encrypted_account = await _private(client, "encrypt_account")(
                client._account_id
            )
            response = await _private(client, "call_private_api")(
                "user/verifyDevice",
                {
                    "encryptAccount": encrypted_account,
                    "backUpEmail": "",
                    "verifyCode": verification_code.strip(),
                    "model": _authentication._ANDROID_MODEL,
                    "system": _authentication._ANDROID_SYSTEM,
                },
            )
            credentials = await _private(client, "complete_login")(
                response, "Invalid verifyDevice response"
            )
            await self._remember(response)
            self._set_credentials(credentials)
            return credentials

    async def _login(self) -> Credentials:
        client = self._auth_client
        if self._account:
            try:
                LOGGER.debug("Renewing the portal login from the account credentials")
                return await _private(client, "complete_login")(
                    dict(self._account), "Invalid account credentials"
                )
            except AuthenticationError:
                # The pair expired or was revoked. The password login below is
                # the one path left, and on an affected account it answers
                # 1013 -- which the coordinator turns into a re-auth, whose
                # verification step mints a new pair.
                LOGGER.debug(
                    "The account credentials did not renew; trying the password login",
                    exc_info=True,
                )
        LOGGER.debug("Logging in with the password")
        response = await _private(client, "call_login_api")(
            client._account_id,
            client._password_hash,
        )
        credentials = await _private(client, "complete_login")(
            response, "Invalid login response"
        )
        await self._remember(response)
        return credentials

    async def _remember(self, response: Any) -> None:
        account = account_from(response)
        if account is None or account == self._account:
            return
        self._account = account
        if self._on_account is not None:
            await self._on_account(dict(account))
