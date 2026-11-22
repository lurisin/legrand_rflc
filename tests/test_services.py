"""Tests for the Legrand RFLC all_on / all_off services."""

from __future__ import annotations

from unittest.mock import patch

import pytest
import voluptuous as vol
from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import CONF_HOST, CONF_MAC, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.legrand_rflc.const import DOMAIN

from .conftest import NO_REPLY, MockHub, async_setup_with_hub, make_zone

SERVICES_PATCH = "custom_components.legrand_rflc.services"

RAMP_ALL = "TriggerRampAllCommand"


def _ramp_all_messages(hub: MockHub) -> list[dict]:
    return [m for m in hub.sent if m[hub.SERVICE] == RAMP_ALL]


# ---- Registration ----


async def test_services_registered_without_entry(hass: HomeAssistant) -> None:
    """The services belong to the domain, not to a config entry."""
    assert await async_setup_component(hass, DOMAIN, {})

    assert hass.services.has_service(DOMAIN, "all_on")
    assert hass.services.has_service(DOMAIN, "all_off")


async def test_services_survive_unload(
    hass: HomeAssistant,
    setup_integration: MockHub,
    mock_config_entry: MockConfigEntry,
) -> None:
    """Unloading the entry keeps the services but leaves no hub to use."""
    assert await hass.config_entries.async_unload(mock_config_entry.entry_id)

    assert hass.services.has_service(DOMAIN, "all_off")
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(DOMAIN, "all_off", blocking=True)
    assert exc_info.value.translation_key == "no_hub_available"


async def test_services_take_no_fields(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """Brightness, transition or any other field is rejected."""
    with pytest.raises(vol.Invalid):
        await hass.services.async_call(
            DOMAIN, "all_on", {"brightness": 10}, blocking=True
        )
    assert _ramp_all_messages(setup_integration) == []


# ---- Message sent to the hub ----


@pytest.mark.parametrize(
    ("service", "power_level"), [("all_on", 100), ("all_off", 0)]
)
async def test_service_sends_trigger_ramp_all(
    hass: HomeAssistant,
    setup_integration: MockHub,
    service: str,
    power_level: int,
) -> None:
    """Each service sends one TriggerRampAllCommand and nothing per zone."""
    hub = setup_integration
    hub.sent.clear()

    await hass.services.async_call(DOMAIN, service, blocking=True)

    assert hub.sent == [{"Service": RAMP_ALL, "PowerLevel": power_level}]


# ---- Entity state follows the hub's broadcasts ----


async def test_all_off_updates_entities(hass: HomeAssistant) -> None:
    """After all_off every entity is off; a dimmer keeps its stored level."""
    hub = MockHub(
        zones=[
            make_zone(0, "Living Room", "Dimmer", True, 75),
            make_zone(1, "Kitchen", "Switch", True),
        ]
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: hub.host(), CONF_MAC: "001122334455"}
    )
    await async_setup_with_hub(hass, entry, hub)
    assert hass.states.get("light.living_room").state == STATE_ON
    assert hass.states.get("light.kitchen").state == STATE_ON

    await hass.services.async_call(DOMAIN, "all_off", blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get("light.living_room").state == STATE_OFF
    assert hass.states.get("light.kitchen").state == STATE_OFF
    # The hub leaves the dimmer level alone, so turning it back on restores it.
    assert hub._zones[0]["properties"][hub.POWER_LEVEL] == 75


async def test_all_on_updates_entities(hass: HomeAssistant) -> None:
    """After all_on every entity is on and dimmers are at 100 %."""
    hub = MockHub(
        zones=[
            make_zone(0, "Living Room", "Dimmer", False, 30),
            make_zone(1, "Kitchen", "Switch", False),
        ]
    )
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_HOST: hub.host(), CONF_MAC: "001122334455"}
    )
    await async_setup_with_hub(hass, entry, hub)
    assert hass.states.get("light.living_room").state == STATE_OFF
    assert hass.states.get("light.kitchen").state == STATE_OFF

    await hass.services.async_call(DOMAIN, "all_on", blocking=True)
    await hass.async_block_till_done()

    dimmer = hass.states.get("light.living_room")
    assert dimmer.state == STATE_ON
    assert dimmer.attributes[ATTR_BRIGHTNESS] == 255
    assert hass.states.get("light.kitchen").state == STATE_ON


async def test_no_optimistic_state(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """Without the hub's broadcasts the entities keep their state."""
    hub = setup_integration
    hub._ramp_all_response = {"Status": "Success"}  # reply only, no broadcast

    await hass.services.async_call(DOMAIN, "all_off", blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get("light.living_room").state == STATE_ON


# ---- Failures ----


async def test_error_reply_raises(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """An error reply from the hub raises HomeAssistantError."""
    hub = setup_integration
    hub._ramp_all_response = {
        "Status": "Error",
        "ErrorCode": "71",
        "ErrorText": "No PowerLevel field",
    }

    with pytest.raises(HomeAssistantError, match="No PowerLevel field") as exc_info:
        await hass.services.async_call(DOMAIN, "all_on", blocking=True)

    assert not isinstance(exc_info.value, ServiceValidationError)
    assert exc_info.value.translation_key == "ramp_all_failed"
    assert exc_info.value.translation_placeholders == {
        "host": hub.host(),
        "error": "No PowerLevel field",
    }


async def test_no_reply_times_out(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """A hub that never replies raises HomeAssistantError after the timeout."""
    hub = setup_integration
    hub._ramp_all_response = NO_REPLY

    with (
        patch(f"{SERVICES_PATCH}.COMMAND_TIMEOUT", 0.01),
        pytest.raises(HomeAssistantError, match="in time") as exc_info,
    ):
        await hass.services.async_call(DOMAIN, "all_off", blocking=True)

    assert not isinstance(exc_info.value, ServiceValidationError)
    assert exc_info.value.translation_key == "ramp_all_timeout"
    assert exc_info.value.translation_placeholders == {"host": hub.host()}


async def test_no_entry_raises_validation_error(hass: HomeAssistant) -> None:
    """Without any config entry the call is refused."""
    assert await async_setup_component(hass, DOMAIN, {})

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(DOMAIN, "all_on", blocking=True)

    assert exc_info.value.translation_key == "no_hub_available"


@pytest.mark.parametrize("attribute", ["connected", "authenticated"])
async def test_unavailable_hub_raises_validation_error(
    hass: HomeAssistant, setup_integration: MockHub, attribute: str
) -> None:
    """A disconnected or unauthenticated hub is not used."""
    hub = setup_integration
    setattr(hub, attribute, False)

    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(DOMAIN, "all_off", blocking=True)

    assert exc_info.value.translation_key == "no_hub_available"
    assert _ramp_all_messages(hub) == []


# ---- More than one hub ----


async def _setup_second_hub(hass: HomeAssistant) -> MockHub:
    hub = MockHub(host="192.168.1.101", zones=[make_zone(0, "Garage", "Switch")])
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_HOST: hub.host(), CONF_MAC: "66778899aabb"},
        unique_id=hub.host(),
    )
    await async_setup_with_hub(hass, entry, hub)
    return hub


async def test_every_hub_is_targeted(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """The command goes to every available hub."""
    first = setup_integration
    second = await _setup_second_hub(hass)

    await hass.services.async_call(DOMAIN, "all_off", blocking=True)
    await hass.async_block_till_done()

    expected = [{"Service": RAMP_ALL, "PowerLevel": 0}]
    assert _ramp_all_messages(first) == expected
    assert _ramp_all_messages(second) == expected
    assert hass.states.get("light.living_room").state == STATE_OFF
    assert hass.states.get("light.garage").state == STATE_OFF


async def test_unavailable_hub_is_skipped(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """A hub that is down does not block the others."""
    first = setup_integration
    second = await _setup_second_hub(hass)
    first.connected = False

    await hass.services.async_call(DOMAIN, "all_off", blocking=True)

    assert _ramp_all_messages(first) == []
    assert _ramp_all_messages(second) == [{"Service": RAMP_ALL, "PowerLevel": 0}]


async def test_failing_hub_does_not_stop_the_others(
    hass: HomeAssistant, setup_integration: MockHub
) -> None:
    """One hub failing is reported, but the other hubs still get the command."""
    first = setup_integration
    second = await _setup_second_hub(hass)
    first._ramp_all_response = {"Status": "Error", "ErrorText": "Busy"}

    with pytest.raises(HomeAssistantError, match="Busy"):
        await hass.services.async_call(DOMAIN, "all_off", blocking=True)
    await hass.async_block_till_done()

    assert hass.states.get("light.garage").state == STATE_OFF
