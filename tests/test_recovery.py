import asyncio
import contextlib
import types
import unittest
from unittest.mock import patch

from support import gateway, key, packet, DeviceType, SubType, HomeAssistantError
from kocom_test.gateway import _CmdItem
from kocom_test.transport import AsyncConnection
from kocom_test.models import DeviceKey


class ProtocolTests(unittest.TestCase):
    def test_every_frame_split_and_bytewise(self):
        raw = packet()
        for split in range(1, len(raw)):
            g = gateway()
            g.controller.feed(raw[:split])
            g.controller.feed(raw[split:])
            self.assertTrue(g.registry.get(key()).state, split)
        g = gateway()
        for value in raw:
            g.controller.feed(bytes([value]))
        self.assertTrue(g.registry.get(key()).state)

    def test_noise_bad_checksum_then_valid_frames(self):
        g = gateway()
        bad = bytearray(packet()); bad[18] ^= 1
        g.controller.feed(b'garbage' + bad + packet() + packet(payload=(0,)))
        self.assertFalse(g.registry.get(key()).state)
        self.assertGreater(g.stats['checksum_errors'], 0)

    def test_bad_frame_handler_does_not_drop_next(self):
        g = gateway(); original = g.controller._dispatch_packet
        calls = 0
        def dispatch(raw):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise ValueError('injected')
            original(raw)
        g.controller._dispatch_packet = dispatch
        g.controller.feed(packet() + packet())
        self.assertTrue(g.registry.get(key()).state)
        self.assertEqual(g.stats['packet_errors'], 1)

    def test_invalid_restored_frame_rejected(self):
        with self.assertRaises(ValueError):
            gateway().controller._dispatch_packet(b'\xaa')

    def test_thermostat_updates_in_current_frame(self):
        g = gateway()
        g.controller.feed(packet(0x36, (0x10, 0, 22, 0, 20)))
        g.controller.feed(packet(0x36, (0x10, 0, 24, 0, 21)))
        state = g.registry.get(key(DeviceType.THERMOSTAT)).state
        self.assertEqual((state['target_temp'], state['current_temp']), (24, 21))

    def test_zero_sensor_update(self):
        g = gateway()
        g.controller.feed(packet(0x98, (10, 1, 0, 1, 0, 1, 20, 40)))
        g.controller.feed(packet(0x98, (0, 1, 0, 1, 0, 1, 20, 40)))
        sensor = DeviceKey(DeviceType.AIRQUALITY, 1, 0, SubType.PM10)
        self.assertEqual(g.registry.get(sensor).state, 0)

    def test_unsafe_actions_rejected(self):
        g = gateway()
        for kind, action in ((DeviceType.GASVALVE, 'turn_on'), (DeviceType.ELEVATOR, 'turn_off')):
            with self.assertRaises(ValueError):
                g.controller.generate_command(key(kind), action)
        for temp in (22.5, float('nan'), float('inf'), -1, 41):
            with self.assertRaises(ValueError):
                g.controller.generate_command(key(DeviceType.THERMOSTAT), 'set_temperature', target_temp=temp)

    def test_whole_switch_snapshot_preserved(self):
        g = gateway()
        with self.assertRaises(ValueError):
            g.controller.generate_command(key(), 'turn_on')
        g.controller.feed(packet(payload=(0, 255, 17, 0, 255, 0, 0, 0)))
        raw, _, _ = g.controller.generate_command(key(), 'turn_on')
        self.assertEqual(raw[10:18], bytes((255, 255, 17, 0, 255, 0, 0, 0)))

    def test_gateway_signals_isolated(self):
        self.assertNotEqual(gateway('a').async_signal_device_updated('x'),
                            gateway('b').async_signal_device_updated('x'))

    def test_ventilation_presets_on_first_frame_and_per_room(self):
        g = gateway()
        g.controller.feed(packet(0x48, (0x10, 2, 0x40)))
        g.controller.feed(packet(0x48, (0x10, 1, 0x40), room=2))
        self.assertTrue(g.registry.get(key(DeviceType.VENTILATION)).attribute['feature_preset'])
        self.assertFalse(g.registry.get(key(DeviceType.VENTILATION, room=2)).attribute['feature_preset'])


class AsyncTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writers = []
        self.servers = []
        self.gateways = []

    async def asyncTearDown(self):
        for g in self.gateways:
            await g.async_stop()
        for writer in self.writers:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()
        for server in self.servers:
            server.close()
            await server.wait_closed()

    async def connect(self, peer):
        server = await asyncio.start_server(peer, '127.0.0.1', 0)
        self.servers.append(server)
        g = gateway(port=server.sockets[0].getsockname()[1])
        g.conn.reconnect_backoff = (.01, .02)
        self.gateways.append(g)
        await g.async_start()
        return g

    async def until(self, condition, timeout=1.0):
        async def wait():
            while not condition():
                await asyncio.sleep(.005)
        await asyncio.wait_for(wait(), timeout)

    async def test_eof_reconnect_and_fresh_availability(self):
        async def peer(reader, writer):
            self.writers.append(writer)
            writer.write(packet())
            await writer.drain()
        g = await self.connect(peer)
        await self.until(lambda: g.is_available(key()))
        generation = g.conn.generation
        self.writers[0].close()
        await self.until(lambda: g.conn.generation > generation and g.is_available(key()))
        self.assertEqual(g.stats['reconnections'], 1)
        self.assertFalse(g._task_reader.done())

    async def test_quiet_bus_not_reconnected(self):
        async def peer(reader, writer):
            self.writers.append(writer)
        g = await self.connect(peer)
        await asyncio.sleep(1.05)
        self.assertEqual(g.conn.generation, 1)
        self.assertFalse(g.is_available(key()))

    async def test_ack_registered_before_send_yields(self):
        g = gateway(); self.gateways.append(g)
        g._stopping = False
        g.conn._connected = True
        g.controller.feed(packet(payload=(0,)))
        g.conn._last_activity_mono -= 1
        async def send(raw):
            g.controller.feed(packet(payload=(255,)))
            return len(raw)
        g.conn.send = send
        item = _CmdItem(key(), 'turn_on', {})
        self.assertTrue(await g._execute(item))
        self.assertEqual(g._pendings, [])

    async def test_stop_resolves_current_and_queued_requests(self):
        async def peer(reader, writer):
            self.writers.append(writer)
            writer.write(packet()); await writer.drain()
        g = await self.connect(peer)
        await self.until(lambda: g.is_available(key()))
        callers = [asyncio.create_task(g.async_send_action(key(), 'turn_off')) for _ in range(3)]
        await asyncio.sleep(.02)
        await g.async_stop()
        results = await asyncio.wait_for(asyncio.gather(*callers, return_exceptions=True), 1)
        self.assertTrue(all(isinstance(x, HomeAssistantError) for x in results))
        self.assertEqual(g._tx_queue.qsize(), 0)
        self.assertEqual(g._pendings, [])
        self.assertIsNone(g.conn._writer)

    async def test_dead_reader_does_not_prevent_close(self):
        async def peer(reader, writer): self.writers.append(writer)
        g = await self.connect(peer)
        g._task_reader.cancel()
        with contextlib.suppress(asyncio.CancelledError): await g._task_reader
        async def fail(): raise ValueError('injected')
        g._task_reader = asyncio.create_task(fail())
        await asyncio.sleep(0)
        await g.async_stop()
        self.assertIsNone(g.conn._writer)
        self.assertIsNone(g._task_sender)

    async def test_drain_timeout_is_bounded(self):
        aborted = []
        class Writer:
            transport = types.SimpleNamespace(abort=lambda: aborted.append(True))
            def write(self, data): pass
            def close(self): pass
            async def drain(self): await asyncio.Future()
            async def wait_closed(self): await asyncio.Future()
        conn = AsyncConnection('unused', 1, io_timeout=.01)
        conn._writer = Writer(); conn._connected = True
        with self.assertRaises(asyncio.TimeoutError): await conn.send(b'abc')
        self.assertFalse(conn._is_connected())
        await conn.close()
        self.assertTrue(aborted)

    async def test_invalid_command_does_not_kill_sender(self):
        async def peer(reader, writer):
            self.writers.append(writer); writer.write(packet()); await writer.drain()
        g = await self.connect(peer)
        await self.until(lambda: g.is_available(key()))
        with self.assertRaises(HomeAssistantError): await g.async_send_action(key(), 'unsupported')
        self.assertFalse(g._task_sender.done())

    async def test_queue_limit(self):
        g = gateway(); self.gateways.append(g)
        g._stopping = False; g.conn._connected = True
        g._task_reader = asyncio.create_task(asyncio.sleep(100))
        g._task_sender = asyncio.create_task(asyncio.sleep(100))
        g._live_keys.add(key().key)
        for _ in range(32): g._tx_queue.put_nowait(_CmdItem(key(), 'turn_on', {}))
        with self.assertRaisesRegex(HomeAssistantError, 'full'):
            await g.async_send_action(key(), 'turn_on')

    async def test_expired_and_cancelled_items_not_sent(self):
        g = gateway(); self.gateways.append(g); g._stopping = False
        expired = _CmdItem(key(), 'turn_on', {}); expired.deadline = 0
        cancelled = _CmdItem(key(), 'turn_on', {}); cancelled.future.cancel()
        g._tx_queue.put_nowait(expired); g._tx_queue.put_nowait(cancelled)
        g._task_sender = asyncio.create_task(g._sender_loop())
        await asyncio.wait_for(g._tx_queue.join(), 1)
        self.assertFalse(expired.future.result())
        self.assertEqual(g.stats['transmissions'], 0)

    async def test_retry_uses_new_other_channel_state(self):
        g = gateway(); self.gateways.append(g)
        g._stopping = False; g.conn._connected = True
        g.controller.feed(packet(payload=(0, 0)))
        g.conn._last_activity_mono -= 1
        original = g.controller.generate_command
        def generate(*args, **kwargs):
            raw, predicate, timeout = original(*args, **kwargs)
            return raw, predicate, .01
        g.controller.generate_command = generate
        sent = []
        async def send(raw):
            sent.append(raw)
            if len(sent) == 1:
                g.controller.feed(packet(payload=(0, 255)))
            else:
                g.controller.feed(packet(payload=(255, 255)))
            return len(raw)
        g.conn.send = send
        self.assertTrue(await g._execute(_CmdItem(key(), 'turn_on', {})))
        self.assertEqual(sent[0][11], 0)
        self.assertEqual(sent[1][11], 255)

    async def test_stale_generation_cannot_close_new_connection(self):
        async def peer(reader, writer): self.writers.append(writer)
        g = await self.connect(peer)
        g._connection_lost(g.conn.generation - 1)
        self.assertTrue(g.conn._is_connected())

    async def test_no_confirmation_and_no_rx_requests_reconnect(self):
        g = gateway(); self.gateways.append(g)
        g._stopping = False; g.conn._connected = True
        g.controller.feed(packet())
        g.conn._last_activity_mono -= 1
        original = g.controller.generate_command
        def generate(*args, **kwargs):
            raw, predicate, timeout = original(*args, **kwargs)
            return raw, predicate, .01
        g.controller.generate_command = generate
        async def send(raw): return len(raw)
        g.conn.send = send
        self.assertFalse(await g._execute(_CmdItem(key(), 'turn_off', {})))
        self.assertFalse(g.conn._is_connected())
        self.assertEqual(g.stats['confirmation_timeouts'], 3)
        self.assertEqual(g.controller._switch_payloads, {})

    async def test_repeated_eof_does_not_create_extra_workers(self):
        async def peer(reader, writer):
            self.writers.append(writer)
            if len(self.writers) < 5:
                writer.close()
            else:
                writer.write(packet()); await writer.drain()
        g = await self.connect(peer)
        await self.until(lambda: g.conn.generation == 5 and g.is_available(key()))
        workers = [t for t in asyncio.all_tasks() if t.get_name() in ('kocom-reader', 'kocom-sender')]
        self.assertEqual(len(workers), 2)

    async def test_failed_open_does_not_recurse(self):
        conn = AsyncConnection('unused', 1)
        async def fail(*args): raise OSError('offline')
        with patch('asyncio.open_connection', side_effect=fail) as opened:
            with self.assertRaises(OSError): await conn.open()
            self.assertEqual(opened.call_count, 1)

if __name__ == '__main__': unittest.main()
