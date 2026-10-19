"""Run against an installed HA package, using a local TCP peer (no real devices).

From repository root: python scripts/ha_smoke.py
HA platform loading/HACS installation and physical RS485 remain separate tests.
"""
import asyncio
import importlib
import logging
from datetime import timedelta
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.helpers import entity_registry
from homeassistant.helpers.entity_platform import EntityPlatform
from custom_components.kocom_wallpad.gateway import KocomGateway


def packet(code, payload, command=0):
    body = bytes([0x30, 0xBC, 0, 1, 0, code, 1, command]) + bytes(payload).ljust(8, b'\0')
    return b'\xaa\x55' + body + bytes([sum(body) % 256]) + b'\r\r'


async def main():
    for name in ('config_flow', 'light', 'switch', 'climate', 'fan', 'sensor', 'binary_sensor', 'diagnostics'):
        importlib.import_module('custom_components.kocom_wallpad.' + name)
    with tempfile.TemporaryDirectory() as config:
        hass = HomeAssistant(config)
        await entity_registry.async_load(hass)
        platforms = []
        writers = []
        async def peer(reader, writer):
            writers.append(writer)
        server = await asyncio.start_server(peer, '127.0.0.1', 0)
        gateway = KocomGateway(hass, SimpleNamespace(entry_id='smoke'), '127.0.0.1', server.sockets[0].getsockname()[1])
        entities = []
        try:
            await gateway.async_start()
            for raw in (packet(0x0e, (255,)), packet(0x3b, (255,)),
                        packet(0x36, (0x10, 0, 24, 0, 22)),
                        packet(0x39, (0x10, 0, 1, 0, 22, 24)),
                        packet(0x48, (0x10, 2, 0x40)), packet(0x60, (), 4)):
                gateway.controller.feed(raw)
            classes = {'light': 'KocomLight', 'switch': 'KocomSwitch', 'climate': 'KocomClimate',
                       'fan': 'KocomFan', 'sensor': 'KocomSensor', 'binary_sensor': 'KocomBinarySensor'}
            for platform in Platform:
                if platform.value not in classes:
                    continue
                module = importlib.import_module('custom_components.kocom_wallpad.' + platform.value)
                entity_platform = EntityPlatform(
                    hass=hass, logger=logging.getLogger(__name__), domain=platform.value,
                    platform_name="kocom_wallpad", platform=module,
                    scan_interval=timedelta(seconds=30), entity_namespace=None,
                )
                platforms.append(entity_platform)
                for i, device in enumerate(gateway.get_devices_from_platform(platform)):
                    entity = getattr(module, classes[platform.value])(gateway, device)
                    entity.entity_id = f'{platform.value}.kocom_smoke_{i}'
                    await entity_platform.async_add_entities([entity])
                    assert hass.states.get(entity.entity_id) is not None
                    assert entity.available
                    entities.append(entity)
            gateway.controller.feed(packet(0x0e, (0,)))
            await asyncio.sleep(0)
            assert hass.states.get('light.kocom_smoke_0').state == 'off'
            await gateway.async_stop()
            await asyncio.sleep(0)
            assert all(hass.states.get(e.entity_id).state == 'unavailable' for e in entities)
            print(f'PASS: {len(entities)} HA entities, state dispatch, disconnect availability, cleanup')
        finally:
            for entity_platform in platforms:
                await entity_platform.async_reset()
            await gateway.async_stop()
            for writer in writers:
                writer.close()
                await writer.wait_closed()
            server.close()
            await server.wait_closed()
            await hass.async_stop()


asyncio.run(main())
