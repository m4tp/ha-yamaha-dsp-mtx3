"""Push updates from device notifications, shared by all entity types."""

from collections.abc import Callable

from homeassistant.core import callback

# Fader moves produce a burst of notifications: refresh once shortly after the last one
PUSH_REFRESH_DELAY = 0.3
PRESET_EVENTS = ("sscurrent", "ssrecall")


class PushUpdateMixin:
    """Refreshes the entity when the device reports a change made elsewhere (MTX-MRX Editor, Wireless DCP, ...).

    Subclasses set self._device and implement _push_parameters(). Values are queried again instead of being
    taken from the notification, because notifications carry raw values while entities may use normalized ones.
    Polling stays enabled as a fallback for notifications lost during a reconnect.
    """

    _refresh_on_preset_recall = True

    def _push_parameters(self) -> list[str]:
        return []

    def _init_push(self) -> None:
        self._unsubscribers: list[Callable[[], None]] = []
        self._refresh_handle = None

    async def async_added_to_hass(self) -> None:
        for parameter in self._push_parameters():
            self._unsubscribers.append(self._device.add_parameter_listener(parameter, self._on_parameter_notify))
        self._unsubscribers.append(self._device.add_event_listener(self._on_event_notify))

    async def async_will_remove_from_hass(self) -> None:
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        self._unsubscribers.clear()
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
            self._refresh_handle = None

    @callback
    def _write_state(self) -> None:
        # Show a value set from Home Assistant right away instead of waiting for the next update
        if self.hass is not None:
            self.async_write_ha_state()

    @callback
    def _on_parameter_notify(self, _value: str) -> None:
        self._schedule_refresh()

    @callback
    def _on_event_notify(self, kind: str) -> None:
        # A preset recall changes every parameter
        if self._refresh_on_preset_recall and kind in PRESET_EVENTS:
            self._schedule_refresh()

    @callback
    def _schedule_refresh(self) -> None:
        if self.hass is None:
            return
        if self._refresh_handle is not None:
            self._refresh_handle.cancel()
        self._refresh_handle = self.hass.loop.call_later(PUSH_REFRESH_DELAY, self._do_refresh)

    @callback
    def _do_refresh(self) -> None:
        self._refresh_handle = None
        self.async_schedule_update_ha_state(force_refresh=True)
