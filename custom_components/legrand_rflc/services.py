"""Services of the Legrand RFLC integration.

``all_on`` and ``all_off`` use the hub's ``TriggerRampAllCommand``: the LC7001
sends a single RF multicast packet, so every zone of the house switches at the
same time instead of one after another.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Final

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .const import COMMAND_TIMEOUT, DOMAIN
from .hub import Hub

SERVICE_ALL_ON: Final = "all_on"
SERVICE_ALL_OFF: Final = "all_off"

POWER_LEVEL_ON: Final = 100
POWER_LEVEL_OFF: Final = 0

# Neither service takes any field.
SERVICE_SCHEMA: Final = vol.Schema({})


def _async_get_hubs(hass: HomeAssistant) -> list[Hub]:
    """Return the hub of every loaded entry that is ready for commands."""
    hubs: list[Hub] = []
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is not ConfigEntryState.LOADED:
            continue
        hub: Hub = entry.runtime_data.hub
        if hub.connected and hub.authenticated:
            hubs.append(hub)
    return hubs


async def _async_trigger_ramp_all(hub: Hub, power_level: int) -> None:
    """Send TriggerRampAllCommand to one hub and wait for its reply."""
    result: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def handle(message: Mapping) -> None:
        if result.done():
            return
        error = hub.StatusError(message)
        if error:
            result.set_exception(
                HomeAssistantError(
                    translation_domain=DOMAIN,
                    translation_key="ramp_all_failed",
                    translation_placeholders={
                        "host": hub.host(),
                        "error": str(error.args[2] or "hub error"),
                    },
                )
            )
        else:
            result.set_result(None)

    await hub.handle_send(handle, hub.compose_trigger_ramp_all(power_level))
    try:
        await asyncio.wait_for(result, COMMAND_TIMEOUT)
    except TimeoutError as err:
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="ramp_all_timeout",
            translation_placeholders={"host": hub.host()},
        ) from err


async def _async_ramp_all(hass: HomeAssistant, power_level: int) -> None:
    """Ramp every zone of every available hub to power_level."""
    hubs = _async_get_hubs(hass)
    if not hubs:
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="no_hub_available",
        )
    # Entity state is not written here: the hub broadcasts one
    # ZonePropertiesChanged per zone, which the light entities listen to.
    results = await asyncio.gather(
        *(_async_trigger_ramp_all(hub, power_level) for hub in hubs),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            raise result


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services of the integration."""

    async def async_all_on(call: ServiceCall) -> None:
        await _async_ramp_all(hass, POWER_LEVEL_ON)

    async def async_all_off(call: ServiceCall) -> None:
        await _async_ramp_all(hass, POWER_LEVEL_OFF)

    hass.services.async_register(
        DOMAIN, SERVICE_ALL_ON, async_all_on, schema=SERVICE_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_ALL_OFF, async_all_off, schema=SERVICE_SCHEMA
    )
