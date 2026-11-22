"""Shared test fixtures for the Legrand RFLC integration."""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Mapping
from typing import Any, Final
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import CONF_AUTHENTICATION, CONF_HOST, CONF_MAC
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.legrand_rflc.const import DOMAIN, LegrandRFLCData
from custom_components.legrand_rflc.hub import (
    Authenticator,
    Composer,
    Connector,
    Hub,
    Receiver,
)

INIT_PATCH: Final = "custom_components.legrand_rflc"

# Give a MockHub response attribute this value to make the hub never reply.
NO_REPLY: Final = object()

MOCK_HOST: Final = "192.168.1.100"
MOCK_MAC: Final = "001122334455"
MOCK_KEY_HEX: Final = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let Home Assistant load the custom integration in every test."""


class MockHub:
    """Mock Hub with real event emitting for testing."""

    # Mirror real event constants.
    EVENT_CONNECTED: Final = Connector.EVENT_CONNECTED
    EVENT_DISCONNECTED: Final = Connector.EVENT_DISCONNECTED
    EVENT_AUTHENTICATED: Final = Authenticator.EVENT_AUTHENTICATED
    EVENT_UNAUTHENTICATED: Final = Authenticator.EVENT_UNAUTHENTICATED
    EVENT_ZONE_ADDED: Final = f"{Receiver.SERVICE}:ZoneAdded"
    EVENT_ZONE_DELETED: Final = f"{Receiver.SERVICE}:ZoneDeleted"
    EVENT_ZONE_PROPERTIES_CHANGED: Final = (
        f"{Receiver.SERVICE}:ZonePropertiesChanged"
    )

    # Mirror composer key constants.
    SERVICE: Final = Composer.SERVICE
    ZID: Final = Composer.ZID
    ZONE_LIST: Final = Composer.ZONE_LIST
    PROPERTY_LIST: Final = Composer.PROPERTY_LIST
    NAME: Final = Composer.NAME
    POWER: Final = Composer.POWER
    POWER_LEVEL: Final = Composer.POWER_LEVEL
    RAMP_RATE: Final = Composer.RAMP_RATE
    DEVICE_TYPE: Final = Composer.DEVICE_TYPE
    DIMMER: Final = Composer.DIMMER
    SWITCH: Final = Composer.SWITCH

    StatusError = Receiver.StatusError

    def __init__(
        self,
        host: str = MOCK_HOST,
        zones: list[dict[str, Any]] | None = None,
    ) -> None:
        self._host = host
        self._connected = True
        self._authenticated = True
        # Copied: the mock changes zone properties like the real hub does.
        self._zones: list[dict[str, Any]] = copy.deepcopy(zones or [])
        self._handlers: dict[str, list[Any]] = {}
        self._set_zone_response: Any = None
        self._ramp_all_response: Any = None
        # Every message passed to handle_send, in order.
        self.sent: list[dict[str, Any]] = []
        # Set once loop() has emitted its events and their handlers are done.
        self.ready = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        """Mirror any other constant of the real Hub."""
        if name.isupper():
            return getattr(Hub, name)
        raise AttributeError(name)

    def host(self) -> str:
        return self._host

    @property
    def connected(self) -> bool:
        return self._connected

    @connected.setter
    def connected(self, value: bool) -> None:
        self._connected = value

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    @authenticated.setter
    def authenticated(self, value: bool) -> None:
        self._authenticated = value

    # --- Event emitter ---

    def on(self, name: str, handler: Any) -> MockHub:
        self._handlers.setdefault(name, []).append(handler)
        return self

    def off(self, name: str, handler: Any) -> MockHub:
        if name not in self._handlers:
            raise ValueError(name)
        self._handlers[name].remove(handler)
        if not self._handlers[name]:
            del self._handlers[name]
        return self

    def once(self, name: str, handler: Any) -> MockHub:
        async def wrapper(*args: Any) -> None:
            self.off(name, wrapper)
            await handler(*args)

        self.on(name, wrapper)
        return self

    async def emit(self, name: str, *event: Any) -> None:
        if name in self._handlers:
            for handler in list(self._handlers[name]):
                await handler(*event)

    # --- Hub lifecycle ---

    async def cancel(self) -> None:
        pass

    async def loop(self) -> str:
        # A real hub needs network round trips before it authenticates, so the
        # config entry is loaded by then. Home Assistant starts tasks eagerly:
        # yield once to get the same order of events here.
        await asyncio.sleep(0)
        self._connected = True
        self._authenticated = True
        await self.emit(self.EVENT_CONNECTED)
        await self.emit(self.EVENT_AUTHENTICATED, self._host)
        self.ready.set()
        return self._host

    # --- Compose helpers ---

    def compose_list_zones(self) -> dict[str, Any]:
        return {self.SERVICE: "ListZones"}

    def compose_report_zone_properties(self, zid: int) -> dict[str, Any]:
        return {self.SERVICE: "ReportZoneProperties", self.ZID: zid}

    def compose_set_zone_properties(
        self, zid: int, **kwargs: Any
    ) -> dict[str, Any]:
        return {self.SERVICE: "SetZoneProperties", self.ZID: zid, **kwargs}

    def compose_report_system_properties(self) -> dict[str, Any]:
        return {self.SERVICE: "ReportSystemProperties"}

    def compose_trigger_ramp_all(self, power_level: int) -> dict[str, Any]:
        return {
            self.SERVICE: "TriggerRampAllCommand",
            self.POWER_LEVEL: power_level,
        }

    async def _ramp_all(self, level: int) -> None:
        """Do what the LC7001 firmware does in HandleTargetValueAll().

        Update every zone, then broadcast one ZonePropertiesChanged per zone.
        """
        for zone in self._zones:
            properties = zone["properties"]
            if properties[self.DEVICE_TYPE] == self.SWITCH:
                # A switch is on when the level is above 49.
                properties[self.POWER] = level > 49
            else:
                properties[self.POWER] = level > 0
                # A dimmer keeps its stored level unless the level is 100.
                if level == 100:
                    properties[self.POWER_LEVEL] = level
            changed = {self.POWER: properties[self.POWER]}
            if self.POWER_LEVEL in properties:
                changed[self.POWER_LEVEL] = properties[self.POWER_LEVEL]
            await self.emit(
                f"{self.EVENT_ZONE_PROPERTIES_CHANGED}:{zone['zid']}",
                {
                    "ID": 0,
                    self.SERVICE: "ZonePropertiesChanged",
                    self.ZID: zone["zid"],
                    self.PROPERTY_LIST: changed,
                    "Status": "Success",
                },
            )

    # --- Simulated request/response ---

    async def handle_send(
        self, handler: Any, message: dict[str, Any]
    ) -> None:
        service = message.get(self.SERVICE, "")
        self.sent.append(message)

        if service == "ListZones":
            await handler(
                {
                    "Status": "Success",
                    self.ZONE_LIST: [
                        {self.ZID: z["zid"]} for z in self._zones
                    ],
                }
            )
        elif service == "ReportZoneProperties":
            zid = message[self.ZID]
            for z in self._zones:
                if z["zid"] == zid:
                    await handler(
                        {
                            "Status": "Success",
                            self.ZID: zid,
                            self.PROPERTY_LIST: z["properties"],
                        }
                    )
                    return
            await handler(
                {
                    "Status": "Error",
                    "ErrorCode": "1",
                    "ErrorText": "Zone not found",
                }
            )
        elif service == "SetZoneProperties":
            if self._set_zone_response is NO_REPLY:
                return
            if self._set_zone_response is not None:
                await handler(self._set_zone_response)
            else:
                await handler({"Status": "Success"})
        elif service == "TriggerRampAllCommand":
            if self._ramp_all_response is NO_REPLY:
                return
            if self._ramp_all_response is not None:
                await handler(self._ramp_all_response)
            else:
                await self._ramp_all(message[self.POWER_LEVEL])
                await handler({"Status": "Success"})
        else:
            await handler({"Status": "Success"})

    async def send(self, message: Any) -> None:
        pass


def make_zone(
    zid: int,
    name: str = "Test Zone",
    device_type: str = "Dimmer",
    power: bool = True,
    power_level: int = 50,
) -> dict[str, Any]:
    """Create a zone dict for MockHub."""
    props: dict[str, Any] = {
        Composer.NAME: name,
        Composer.DEVICE_TYPE: device_type,
        Composer.POWER: power,
    }
    if device_type == "Dimmer":
        props[Composer.POWER_LEVEL] = power_level
    return {"zid": zid, "properties": props}


# ---- Fixtures ----

DEFAULT_ZONES: Final = [
    make_zone(0, "Living Room", "Dimmer", True, 75),
    make_zone(1, "Kitchen", "Switch", False),
]


@pytest.fixture
def mock_hub() -> MockHub:
    """Return a MockHub with two default zones."""
    return MockHub(zones=list(DEFAULT_ZONES))


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Return a bare config entry (no auth)."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=MOCK_HOST,
        data={CONF_HOST: MOCK_HOST, CONF_MAC: MOCK_MAC},
        unique_id=MOCK_HOST,
    )


@pytest.fixture
def mock_config_entry_with_auth() -> MockConfigEntry:
    """Return a config entry with authentication."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=MOCK_HOST,
        data={
            CONF_HOST: MOCK_HOST,
            CONF_MAC: MOCK_MAC,
            CONF_AUTHENTICATION: MOCK_KEY_HEX,
        },
        unique_id=MOCK_HOST,
    )


async def async_setup_with_hub(
    hass: HomeAssistant, entry: MockConfigEntry, hub: MockHub
) -> None:
    """Set up a config entry on a MockHub, with its platforms."""
    entry.add_to_hass(hass)

    with (
        patch(f"{INIT_PATCH}.Connector") as mock_conn_cls,
        patch(f"{INIT_PATCH}.Hub", return_value=hub),
    ):
        mock_conn_cls.return_value.loop = AsyncMock(return_value=MOCK_MAC)
        await hass.config_entries.async_setup(entry.entry_id)
        # The integration starts hub.loop() as a plain asyncio task, which
        # async_block_till_done() does not track: wait for it explicitly.
        await asyncio.wait_for(hub.ready.wait(), 5)
        await hass.async_block_till_done()


@pytest.fixture
async def setup_integration(
    hass: HomeAssistant,
    mock_config_entry: MockConfigEntry,
    mock_hub: MockHub,
) -> MockHub:
    """Set up the integration on a MockHub.

    Returns the MockHub instance so tests can emit events on it.
    """
    await async_setup_with_hub(hass, mock_config_entry, mock_hub)
    return mock_hub
