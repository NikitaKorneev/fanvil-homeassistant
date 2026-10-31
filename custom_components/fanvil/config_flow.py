"""Add and reconfigure a Fanvil panel through the Home Assistant UI."""

import logging
import socket
from typing import Any

import probatio as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo

from .api import (
    FanvilAuthError,
    FanvilClient,
    FanvilConnectionError,
    FanvilError,
    FanvilUnsupportedModel,
    device_unique_id,
    normalize_host,
)
from .const import (
    CONF_ACTION_URL,
    CONF_AUTH_TYPE,
    CONF_CAMERA_AUDIO,
    CONF_HA_URL,
    CONF_POLL_INTERVAL,
    DEFAULT_ACTION_URL,
    DEFAULT_CAMERA_AUDIO,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MODEL,
)

_LOGGER = logging.getLogger(__name__)


def _schema(defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_HOST, default=defaults.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=defaults.get(CONF_PORT, 80)): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Required(CONF_USERNAME, default=defaults.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_SSL, default=defaults.get(CONF_SSL, False)): bool,
            vol.Required(
                CONF_AUTH_TYPE, default=defaults.get(CONF_AUTH_TYPE, "basic")
            ): vol.In(("basic", "digest")),
        }
    )


class FanvilConfigFlow(ConfigFlow, domain=DOMAIN):
    """Verify access and identity before creating or updating an entry."""

    VERSION = 1
    # 1.2 (0.5.0): diagnostic entities are disabled by default.
    MINOR_VERSION = 2

    async def _async_form(
        self, step_id: str, user_input: dict[str, Any] | None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        existing = None
        if step_id == "reconfigure":
            existing = self._get_reconfigure_entry()
        elif step_id == "reauth_confirm":
            existing = self._get_reauth_entry()
        defaults = dict(existing.data) if existing else {}
        if user_input is not None:
            defaults.update(user_input)
            try:
                data = dict(user_input)
                data[CONF_HOST] = normalize_host(data[CONF_HOST])
                client = FanvilClient(
                    async_get_clientsession(self.hass, family=socket.AF_INET),
                    data[CONF_HOST],
                    data[CONF_USERNAME],
                    data[CONF_PASSWORD],
                    port=data[CONF_PORT],
                    use_ssl=data[CONF_SSL],
                    auth_type=data[CONF_AUTH_TYPE],
                )
                info = await client.async_initialize()
                unique_id = device_unique_id(info)
            except ValueError:
                errors[CONF_HOST] = "invalid_host"
            except FanvilAuthError as err:
                errors["base"] = "invalid_auth"
                _LOGGER.warning("Fanvil %s: %s", user_input[CONF_HOST], err)
            except FanvilConnectionError as err:
                errors["base"] = "cannot_connect"
                _LOGGER.warning(
                    "Fanvil %s: %s (%r)", user_input[CONF_HOST], err, err.__cause__
                )
            except FanvilUnsupportedModel as err:
                errors["base"] = "unsupported_model"
                _LOGGER.warning("Fanvil %s: %s", user_input[CONF_HOST], err)
            except FanvilError as err:
                errors["base"] = "invalid_response"
                _LOGGER.warning("Fanvil %s: %s", user_input[CONF_HOST], err)
            except Exception:
                errors["base"] = "unknown"
                _LOGGER.exception("Fanvil %s: unexpected error", user_input[CONF_HOST])
            else:
                if existing:
                    if unique_id != existing.unique_id:
                        errors["base"] = "wrong_device"
                    else:
                        return self.async_update_reload_and_abort(
                            existing, data_updates=data
                        )
                else:
                    await self.async_set_unique_id(unique_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"Fanvil {info[MODEL]}", data=data
                    )
        return self.async_show_form(
            step_id=step_id, data_schema=_schema(defaults), errors=errors
        )

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_form("user", user_input)

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_form("reconfigure", user_input)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        return await self._async_form("reauth_confirm", user_input)

    async def async_step_dhcp(
        self, discovery_info: DhcpServiceInfo
    ) -> ConfigFlowResult:
        """A configured panel got a new address: follow it.

        New devices are not offered: Fanvil phones share the MAC prefix and the
        model cannot be read without credentials.
        """
        mac = discovery_info.macaddress.replace(":", "").lower()
        await self.async_set_unique_id(f"mac_{mac}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: discovery_info.ip})
        return self.async_abort(reason="not_configured")

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> FanvilOptionsFlow:
        return FanvilOptionsFlow()


class FanvilOptionsFlow(OptionsFlowWithReload):
    """Action URL on/off, Home Assistant address for the panel, polling."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input.get(CONF_HA_URL, "").strip()
            if url and not url.startswith(("http://", "https://")):
                errors[CONF_HA_URL] = "invalid_url"
            else:
                return self.async_create_entry(
                    data={**user_input, CONF_HA_URL: url.rstrip("/")}
                )
        entry = self.config_entry
        options = {**entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_ACTION_URL,
                        default=options.get(CONF_ACTION_URL, DEFAULT_ACTION_URL),
                    ): bool,
                    vol.Required(
                        CONF_CAMERA_AUDIO,
                        default=options.get(CONF_CAMERA_AUDIO, DEFAULT_CAMERA_AUDIO),
                    ): bool,
                    vol.Optional(
                        CONF_HA_URL,
                        description={"suggested_value": options.get(CONF_HA_URL, "")},
                    ): str,
                    vol.Required(
                        CONF_POLL_INTERVAL,
                        default=options.get(
                            CONF_POLL_INTERVAL,
                            entry.data.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL),
                        ),
                    ): vol.All(vol.Coerce(int), vol.Range(min=5, max=300)),
                }
            ),
            errors=errors,
        )
