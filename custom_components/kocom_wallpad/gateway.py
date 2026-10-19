"""Gateway for Kocom Wallpad."""

from __future__ import annotations

import asyncio
import time
from collections import Counter

from homeassistant.exceptions import HomeAssistantError
from dataclasses import dataclass, field
from typing import Optional, Dict, Tuple, List, Callable

from homeassistant.core import HomeAssistant, Event, callback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import entity_registry as er, restore_state
from homeassistant.const import Platform
from homeassistant.helpers.dispatcher import async_dispatcher_send

from .const import (
    LOGGER,
    DOMAIN,
    IDLE_GAP_SEC,
    SEND_RETRY_MAX,
    SEND_RETRY_GAP,
    DeviceType,
)
from .models import DeviceKey, DeviceState
from .transport import AsyncConnection
from .controller import KocomController


@dataclass(slots=True)
class _CmdItem:
    key: DeviceKey
    action: str
    kwargs: dict
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())
    deadline: float = field(default_factory=lambda: time.monotonic() + 15.0)


class _PendingWaiter:

    __slots__ = ("key", "predicate", "future")

    def __init__(
        self, 
        key: DeviceKey,
        predicate: Callable[[DeviceState], bool],
        loop: asyncio.AbstractEventLoop
    ) -> None:
        self.key = key
        self.predicate = predicate
        self.future: asyncio.Future[DeviceState] = loop.create_future()


class EntityRegistry:
    """In-memory entity registry (for gateway internal use)."""

    def __init__(self) -> None:
        """Initialize the registry."""
        self._states: Dict[Tuple[int, int, int, int], DeviceState] = {}
        self._shadow: Dict[Tuple[int, int, int, int], DeviceState] = {}
        self.by_platform: Dict[Platform, Dict[str, DeviceState]] = {}

    def upsert(self, dev: DeviceState, allow_insert: bool = True) -> tuple[bool, bool]:
        k = dev.key.key
        old = self._states.get(k)
        is_new = old is None

        if is_new and not allow_insert:
            return False, False
        if is_new:
            self._states[k] = dev
            self.by_platform.setdefault(dev.platform, {})[dev.key.unique_id] = dev
            return True, True

        platform_changed = (old.platform != dev.platform)
        state_changed = (old.state != dev.state)
        attr_changed = (old.attribute != dev.attribute)
        changed = platform_changed or state_changed or attr_changed

        if changed:
            if platform_changed:
                self.by_platform.get(old.platform, {}).pop(old.key.unique_id, None)
            self.by_platform.setdefault(dev.platform, {})[dev.key.unique_id] = dev
            self._states[k] = dev
        return False, changed

    def get(self, key: DeviceKey, include_shadow: bool = False) -> Optional[DeviceState]:
        dev = self._states.get(key.key)
        if dev is None and include_shadow:
            return self._shadow.get(key.key)
        return dev

    def promote(self, key: DeviceKey) -> bool:
        """shadow -> real promotion (becomes a target for entity creation)"""
        k = key.key
        dev = self._shadow.pop(k, None)
        if dev is None:
            return False
        self._states[k] = dev
        self.by_platform.setdefault(dev.platform, {})[dev.key.unique_id] = dev
        return True

    def all_by_platform(self, platform: Platform) -> List[DeviceState]:
        return list(self.by_platform.get(platform, {}).values())


class KocomGateway:
    """Connection/Receive Loop/Transmission Queue/Entity Registry Management Hub."""

    def __init__(
        self, 
        hass: HomeAssistant, 
        entry: ConfigEntry,
        host: str,
        port: int | None
    ) -> None:
        """Initialize the gateway."""
        self.hass = hass
        self.entry = entry
        self.host = host
        self.port = port
        self.device_id: str | None = None
        self.conn = AsyncConnection(host=host, port=port)
        self.controller = KocomController(self)
        self.registry = EntityRegistry()
        self._tx_queue: asyncio.Queue[_CmdItem] = asyncio.Queue(maxsize=32)
        self._task_reader: asyncio.Task | None = None
        self._task_sender: asyncio.Task | None = None
        self._pendings: list[_PendingWaiter] = []
        self._last_rx_monotonic: float = 0.0
        self._last_tx_monotonic: float = 0.0
        self._restore_mode: bool = False
        self._force_register_uid: str | None = None
        self._stopping = True
        self._active_item: _CmdItem | None = None
        self._live_keys: set[tuple] = set()
        self._last_valid_rx: float | None = None
        self.stats: Counter = Counter()

    async def async_start(self) -> None:
        if not self._stopping:
            return
        await self.conn.open()  # Setup retries belong to Home Assistant.
        self._stopping = False
        self._task_reader = asyncio.create_task(self._read_loop(), name="kocom-reader")
        self._task_sender = asyncio.create_task(self._sender_loop(), name="kocom-sender")

    def _fail_commands(self) -> None:
        if self._active_item and not self._active_item.future.done():
            self._active_item.future.set_result(False)
        while not self._tx_queue.empty():
            item = self._tx_queue.get_nowait()
            if not item.future.done():
                item.future.set_result(False)
            self._tx_queue.task_done()
        for pending in self._pendings:
            if not pending.future.done():
                pending.future.set_result(None)
        self._pendings.clear()

    def _connection_lost(self, generation: int) -> None:
        if generation != self.conn.generation:
            return
        self.conn.invalidate(generation)
        self._live_keys.clear()
        self.controller._rx_buf.clear()
        self.controller._switch_payloads.clear()
        self._fail_commands()
        async_dispatcher_send(self.hass, self.async_signal_availability())

    async def async_stop(self, event: Event | None = None) -> None:
        self._stopping = True
        self._connection_lost(self.conn.generation)
        tasks = [t for t in (self._task_reader, self._task_sender) if t is not None]
        for task in tasks:
            task.cancel()
        try:
            if tasks:
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for result in results:
                    if isinstance(result, Exception):
                        LOGGER.error("Background task failed during shutdown: %r", result)
        finally:
            await self.conn.close()
            self._task_reader = self._task_sender = None

    def is_idle(self) -> bool:
        return self.conn.idle_since() >= IDLE_GAP_SEC

    @callback
    def async_signal_availability(self) -> str:
        return f"{DOMAIN}_availability_{self.entry.entry_id}"

    def is_available(self, key: DeviceKey) -> bool:
        return (not self._stopping and self.conn._is_connected()
                and self._task_reader is not None and not self._task_reader.done()
                and self._task_sender is not None and not self._task_sender.done()
                and key.key in self._live_keys)

    async def _read_loop(self) -> None:
        delay_min, delay_max = self.conn.reconnect_backoff
        delay = delay_min
        try:
            while not self._stopping:
                if not self.conn._is_connected():
                    self._connection_lost(self.conn.generation)
                    await self.conn.close()
                    await asyncio.sleep(delay)
                    try:
                        await self.conn.open()
                    except Exception as exc:
                        self.stats["reconnect_failures"] += 1
                        LOGGER.warning("Reconnect failed; retry in %.1fs: %s", delay, exc)
                        delay = min(delay * 2, delay_max)
                        continue
                    self.stats["reconnections"] += 1
                    LOGGER.info("Connection restored (generation %d)", self.conn.generation)
                    delay = delay_min
                    self.controller._rx_buf.clear()
                generation = self.conn.generation
                try:
                    chunk = await self.conn.recv(512, 1.0)
                    if chunk and generation == self.conn.generation and self.conn._is_connected():
                        self._last_rx_monotonic = time.monotonic()
                        self.controller.feed(chunk)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.stats["read_failures"] += 1
                    LOGGER.warning("Receive failed; reconnecting: %s", exc, exc_info=True)
                    self._connection_lost(generation)
        finally:
            self._connection_lost(self.conn.generation)

    async def async_send_action(self, key: DeviceKey, action: str, **kwargs) -> bool:
        if not self.is_available(key):
            raise HomeAssistantError("Kocom device is not connected or has no fresh state")
        item = _CmdItem(key=key, action=action, kwargs=kwargs)
        try:
            self._tx_queue.put_nowait(item)
        except asyncio.QueueFull as exc:
            raise HomeAssistantError("Kocom command queue is full") from exc
        try:
            result = await asyncio.wait_for(item.future, max(0.01, item.deadline - time.monotonic()))
        except asyncio.TimeoutError as exc:
            self.stats["command_timeouts"] += 1
            raise HomeAssistantError("Kocom command exceeded its time limit") from exc
        if not result:
            raise HomeAssistantError("Kocom command failed or connection was lost")
        return True

    def on_device_state(self, dev: DeviceState) -> None:
        if not self._restore_mode:
            self._last_valid_rx = time.monotonic()
            self.stats["device_updates"] += 1
            became_live = dev.key.key not in self._live_keys
            self._live_keys.add(dev.key.key)
        else:
            became_live = False
        allow_insert = True
        if dev.key.device_type in (DeviceType.LIGHT, DeviceType.OUTLET):
            allow_insert = bool(getattr(dev, "_is_register", True))
            if getattr(self, "_force_register_uid", None) == dev.key.unique_id:
                allow_insert = True

        is_new, changed = self.registry.upsert(dev, allow_insert=allow_insert)
        if is_new:
            LOGGER.info("New device has been detected. Register -> %s", dev.key)
            async_dispatcher_send(
                self.hass,
                self.async_signal_new_device(dev.platform),
                [dev],
            )
            self._notify_pendings(dev)
            return

        if not allow_insert and self.registry.get(dev.key) is None:
            # OFF channels never discovered as entities have no subscribers.
            self._notify_pendings(dev)
            return
        if changed or became_live:
            LOGGER.debug("Device state has been changed. Update -> %s", dev.key)
            async_dispatcher_send(
                self.hass,
                self.async_signal_device_updated(dev.key.unique_id),
                dev,
            )
        self._notify_pendings(dev)

    @callback
    def async_signal_new_device(self, platform: Platform) -> str:
        return f"{DOMAIN}_new_{platform.value}_{self.entry.entry_id}"

    @callback
    def async_signal_device_updated(self, unique_id: str) -> str:
        return f"{DOMAIN}_updated_{self.entry.entry_id}_{unique_id}"

    def get_devices_from_platform(self, platform: Platform) -> list[DeviceState]:
        return self.registry.all_by_platform(platform)

    async def _async_put_entity_dispatch_packet(self, entity_id: str) -> None:
        state = restore_state.async_get(self.hass).last_states.get(entity_id)
        if not (state and state.extra_data):
            return
        packet = state.extra_data.as_dict().get("packet")
        if not packet:
            return
        ent_reg = er.async_get(self.hass)
        ent_entry = ent_reg.async_get(entity_id)
        if ent_entry and ent_entry.unique_id:
            self._force_register_uid = ent_entry.unique_id.split(":")[0]
        LOGGER.debug("Restore state -> packet: %s", packet)
        try:
            device_storage = state.extra_data.as_dict().get("device_storage", {})
            if isinstance(device_storage, dict):
                self.controller._device_storage.update(device_storage)
            self.controller._dispatch_packet(bytes.fromhex(packet))
        except (ValueError, TypeError, IndexError, KeyError):
            LOGGER.warning("Ignoring invalid restored state for %s", entity_id, exc_info=True)
        finally:
            self._force_register_uid = None

    async def async_get_entity_registry(self) -> None:
        self._restore_mode = True
        try:
            entity_registry = er.async_get(self.hass)
            entities = er.async_entries_for_config_entry(entity_registry, self.entry.entry_id)
            for entity in entities:
                await self._async_put_entity_dispatch_packet(entity.entity_id)
        finally:
            self._restore_mode = False

    def _notify_pendings(self, dev: DeviceState) -> None:
        if not self._pendings:
            return
        hit: list[_PendingWaiter] = []
        for p in self._pendings:
            try:
                if p.key.key == dev.key.key and p.predicate(dev):
                    hit.append(p)
            except Exception:
                # predicate 내부 오류 방어
                continue
        if hit:
            for p in hit:
                if not p.future.done():
                    p.future.set_result(dev)
                try:
                    self._pendings.remove(p)
                except ValueError:
                    pass

    async def _execute(self, item: _CmdItem) -> bool:
        generation = self.conn.generation
        # A call is not an idempotent setting: never auto-repeat an elevator call.
        retries = 1 if item.key.device_type == DeviceType.ELEVATOR else SEND_RETRY_MAX
        valid_before = self._last_valid_rx
        for attempt in range(retries):
            if item.future.done() or self._stopping or not self.conn._is_connected():
                return False
            if generation != self.conn.generation:
                return False
            t0 = time.monotonic()
            while not self.is_idle() and time.monotonic() - t0 < 1.0:
                # Sleep until the current idle deadline, then recheck new traffic.
                remaining = min(IDLE_GAP_SEC - self.conn.idle_since(),
                                1.0 - (time.monotonic() - t0))
                await asyncio.sleep(max(0, remaining))
                if item.future.done() or generation != self.conn.generation:
                    return False
            # Preserve upstream timing until actual RS485 bus timings are known.
            packet, predicate, timeout = self.controller.generate_command(
                item.key, item.action, **item.kwargs
            )
            waiter = _PendingWaiter(item.key, predicate, asyncio.get_running_loop())
            self._pendings.append(waiter)  # Register BEFORE send can yield.
            try:
                await self.conn.send(packet)
                self._last_tx_monotonic = time.monotonic()
                self.stats["transmissions"] += 1
                result = await asyncio.wait_for(waiter.future, timeout)
                if result is not None:
                    return True
                return False
            except asyncio.TimeoutError:
                if not self.conn._is_connected():
                    self._connection_lost(generation)
                    return False
                self.stats["confirmation_timeouts"] += 1
            except asyncio.CancelledError:
                raise
            except (OSError, ConnectionError, RuntimeError):
                self._connection_lost(generation)
                raise
            finally:
                if waiter in self._pendings:
                    self._pendings.remove(waiter)
                if not waiter.future.done():
                    waiter.future.cancel()
            if attempt + 1 < retries:
                await asyncio.sleep(SEND_RETRY_GAP)
        # No arbitrary idle-bus reconnect: require a failed command AND no valid RX.
        if valid_before == self._last_valid_rx:
            self._connection_lost(generation)
        return False

    async def _sender_loop(self) -> None:
        try:
            while not self._stopping:
                item = await self._tx_queue.get()
                self._active_item = item
                try:
                    remaining = item.deadline - time.monotonic()
                    if item.future.done() or remaining <= 0:
                        continue
                    success = await asyncio.wait_for(self._execute(item), remaining)
                    if not item.future.done():
                        item.future.set_result(success)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.stats["command_failures"] += 1
                    LOGGER.warning("Command %s failed: %s", item.action, exc, exc_info=True)
                finally:
                    if not item.future.done():
                        item.future.set_result(False)
                    self._active_item = None
                    self._tx_queue.task_done()
        finally:
            self._fail_commands()

    def diagnostics(self) -> dict:
        now = time.monotonic()
        return {
            "connected": self.conn._is_connected(),
            "generation": self.conn.generation,
            "reader_alive": self._task_reader is not None and not self._task_reader.done(),
            "sender_alive": self._task_sender is not None and not self._task_sender.done(),
            "queue_size": self._tx_queue.qsize(),
            "pending_confirmations": len(self._pendings),
            "last_valid_rx_age": None if self._last_valid_rx is None else now - self._last_valid_rx,
            "counters": dict(self.stats),
        }
