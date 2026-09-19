"""Config and reauthentication flows.

TEST BEFORE CONFIGURE, ON THE CHANNEL THAT WILL BE USED. The flow does not
stop at "the password was accepted": it authenticates and then lists the
account's robots over the same REST path the integration reads every day, and
refuses to create an entry unless a robot is actually there. A setup check
that exercises a different channel than the one that will be used certifies
nothing, and it certifies nothing GREEN, which is worse than no check.

WHY THE ROBOT LIST IS BUILT FROM `not_supported` FIRST. That is the list this
robot lands in -- deebot-client sorts a device into it precisely when no
hardware table matches its class, which is the condition this integration
exists for. A flow that offered only `mqtt` devices would show an empty list
to exactly the person who needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from deebot_client.api_client import ApiClient
from deebot_client.authentication import create_rest_config
from deebot_client.exceptions import (
    DeviceVerificationRequiredError,
    InvalidAuthenticationError,
    InvalidVerificationCodeError,
)
from deebot_client.util import md5
import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    CountrySelector,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.util.uuid import random_uuid_hex

from .auth import AccountAuthenticator, AccountCredentials
from .const import (
    CONF_ACCOUNT,
    CONF_COUNTRY,
    CONF_DEVICE_ID,
    CONF_DID,
    CONF_VERIFICATION_CODE,
    DOMAIN,
    LOGGER,
)

if TYPE_CHECKING:
    from deebot_client.models import ApiDeviceInfo


_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): TextSelector(
            TextSelectorConfig(type=TextSelectorType.EMAIL, autocomplete="username")
        ),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(
                type=TextSelectorType.PASSWORD, autocomplete="current-password"
            )
        ),
        vol.Required(CONF_COUNTRY): CountrySelector(),
    }
)

_VERIFICATION_SCHEMA = vol.Schema({vol.Required(CONF_VERIFICATION_CODE): str})


class DeebotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for deebot_discovery."""

    VERSION = 1

    def __init__(self) -> None:
        self._input: dict[str, Any] = {}
        self._device_id: str = ""
        self._robots: list[ApiDeviceInfo] = []
        self._account: AccountCredentials | None = None

    # -- the account --------------------------------------------------------

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take the Ecovacs account and find its robots."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._input = dict(user_input)
            # Minted once, here, and carried into the entry: the verification
            # step below verifies THIS id with the cloud.
            self._device_id = md5(random_uuid_hex())
            try:
                self._robots = await self._async_list_robots()
            except DeviceVerificationRequiredError:
                return await self.async_step_verification()
            except InvalidAuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error while reaching the Ecovacs cloud")
                errors["base"] = "cannot_connect"
            else:
                if not self._robots:
                    errors["base"] = "no_robots"
                else:
                    return await self.async_step_robot()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                _USER_SCHEMA, user_input or {}
            ),
            errors=errors,
        )

    async def async_step_verification(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Verify this client id with the code Ecovacs emails."""
        errors: dict[str, str] = {}
        if user_input is None:
            try:
                await self._async_request_verification_code()
            except Exception:  # noqa: BLE001
                LOGGER.exception("Could not request a device verification code")
                errors["base"] = "cannot_connect"
        else:
            try:
                self._robots = await self._async_list_robots(
                    verification_code=user_input[CONF_VERIFICATION_CODE]
                )
            except InvalidVerificationCodeError:
                errors["base"] = "invalid_verification_code"
            except InvalidAuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error while verifying the device")
                errors["base"] = "cannot_connect"
            else:
                if not self._robots:
                    errors["base"] = "no_robots"
                elif self.source == SOURCE_REAUTH:
                    return self._async_finish_reauth()
                else:
                    return await self.async_step_robot()

        return self.async_show_form(
            step_id="verification", data_schema=_VERIFICATION_SCHEMA, errors=errors
        )

    # -- the robot ----------------------------------------------------------

    async def async_step_robot(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick which robot this entry is for. One entry, one robot."""
        if len(self._robots) == 1 and user_input is None:
            user_input = {CONF_DID: self._robots[0]["did"]}

        if user_input is not None:
            did = user_input[CONF_DID]
            robot = next(r for r in self._robots if r["did"] == did)
            await self.async_set_unique_id(did)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=_robot_title(robot),
                data={
                    **self._input,
                    CONF_DEVICE_ID: self._device_id,
                    CONF_DID: did,
                    CONF_ACCOUNT: self._account,
                },
            )

        return self.async_show_form(
            step_id="robot",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DID): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(
                                    value=robot["did"], label=_robot_title(robot)
                                )
                                for robot in self._robots
                            ]
                        )
                    )
                }
            ),
        )

    # -- reauthentication ---------------------------------------------------

    async def async_step_reauth(
        self, entry_data: dict[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth. The account and country are kept; the password is not."""
        self._input = {
            CONF_USERNAME: entry_data[CONF_USERNAME],
            CONF_COUNTRY: entry_data[CONF_COUNTRY],
        }
        # The SAME client id, deliberately: reauthenticating is not a new
        # client, and minting a fresh id here would trigger a device
        # verification the owner has already done once.
        self._device_id = entry_data[CONF_DEVICE_ID]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Take a new password and prove it on the channel that will use it."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._input[CONF_PASSWORD] = user_input[CONF_PASSWORD]
            try:
                self._robots = await self._async_list_robots()
            except DeviceVerificationRequiredError:
                return await self.async_step_verification()
            except InvalidAuthenticationError:
                errors["base"] = "invalid_auth"
            except Exception:  # noqa: BLE001
                LOGGER.exception("Unexpected error while reaching the Ecovacs cloud")
                errors["base"] = "cannot_connect"
            else:
                return self._async_finish_reauth()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(
                            type=TextSelectorType.PASSWORD,
                            autocomplete="current-password",
                        )
                    )
                }
            ),
            errors=errors,
            description_placeholders={CONF_USERNAME: self._input[CONF_USERNAME]},
        )

    def _async_finish_reauth(self) -> ConfigFlowResult:
        # The account credentials this flow's login or verification just
        # returned travel with the password: they are what the coordinator
        # logs in with (auth.py); the password is its fallback.
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(),
            data_updates={**self._input, CONF_ACCOUNT: self._account},
        )

    # -- the channel both flows prove --------------------------------------

    def _authenticator(self) -> AccountAuthenticator:
        session = async_get_clientsession(self.hass)
        config = create_rest_config(
            session,
            device_id=self._device_id,
            alpha_2_country=self._input[CONF_COUNTRY],
        )
        # THE THIRD PARAMETER IS `password_hash`, NOT THE PASSWORD. deebot-client
        # puts whatever it is given straight into the login call's `password`
        # field, so a plaintext password there is sent verbatim and the cloud
        # answers `invalid_auth` -- indistinguishable, from the flow, from the
        # owner mistyping it. The md5 is the vendor's WIRE FORMAT and nothing
        # else: it is not protection, and it is not a reason to store the
        # digest instead of the password, which reauth and the Ecovacs app
        # both still need.
        return AccountAuthenticator(
            config, self._input[CONF_USERNAME], md5(self._input[CONF_PASSWORD])
        )

    async def _async_request_verification_code(self) -> None:
        authenticator = self._authenticator()
        try:
            await authenticator.request_device_verification_code()
        finally:
            await authenticator.teardown()

    async def _async_list_robots(
        self, *, verification_code: str | None = None
    ) -> list[ApiDeviceInfo]:
        """Authenticate and list the account's robots.

        Both lists are returned, `not_supported` first: that is where a robot
        whose class no table carries ends up, and it is the whole reason this
        integration exists. A robot upstream DOES recognise is offered too --
        this integration works for it, and a class added upstream later must
        not make an existing entry's robot vanish from the flow.
        """
        authenticator = self._authenticator()
        try:
            if verification_code is not None:
                await authenticator.verify_device(verification_code)
            devices = await ApiClient(authenticator).get_devices()
        finally:
            # Kept whichever way the call went: a pair that came back with a
            # robot list is a pair the coordinator can log in with.
            self._account = authenticator.account or self._account
            await authenticator.teardown()

        return [*devices.not_supported, *(d.api for d in devices.mqtt)]


def _robot_title(robot: ApiDeviceInfo) -> str:
    """What the owner called it, falling back to what the account calls it."""
    name = robot.get("nick") or robot.get("name") or robot["did"]
    model = robot.get("deviceName")
    return f"{name} ({model})" if model else name
