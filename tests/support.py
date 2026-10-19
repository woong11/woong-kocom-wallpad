"""Minimal HA boundary doubles; protocol and gateway code are imported unchanged.

These tests do not simulate Home Assistant's entity platform lifecycle.
"""
import enum
import importlib.util
import logging
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parents[1] / 'custom_components' / 'kocom_wallpad'

def module(name, **values):
    result = types.ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result

class Names:
    def __getattr__(self, key):
        return key

class HomeAssistantError(Exception):
    pass

Platform = enum.Enum('Platform', 'LIGHT SWITCH CLIMATE FAN SENSOR BINARY_SENSOR')
HVACMode = enum.Enum('HVACMode', 'HEAT OFF COOL FAN_ONLY DRY AUTO')
module('homeassistant')
module('homeassistant.exceptions', HomeAssistantError=HomeAssistantError)
module('homeassistant.core', HomeAssistant=object, Event=object, callback=lambda f: f)
module('homeassistant.config_entries', ConfigEntry=object)
module('homeassistant.helpers', entity_registry=Names(), restore_state=Names())
module('homeassistant.helpers.dispatcher', async_dispatcher_send=lambda *args: None)
module('homeassistant.const', Platform=Platform, UnitOfTemperature=Names())
module('homeassistant.components')
module('homeassistant.components.sensor', SensorDeviceClass=Names())
module('homeassistant.components.binary_sensor', BinarySensorDeviceClass=Names())
module('homeassistant.components.switch', SwitchDeviceClass=Names())
module('homeassistant.components.climate')
module('homeassistant.components.climate.const', HVACMode=HVACMode,
       PRESET_NONE='none', PRESET_AWAY='away', FAN_LOW='low', FAN_MEDIUM='medium',
       FAN_HIGH='high', FAN_AUTO='auto')
module('serialx')
package = module('kocom_test')
package.__path__ = [str(ROOT)]
for name in ('const', 'models', 'transport', 'controller', 'gateway'):
    spec = importlib.util.spec_from_file_location(f'kocom_test.{name}', ROOT / f'{name}.py')
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = loaded
    spec.loader.exec_module(loaded)
logging.getLogger('kocom_test').disabled = True

from kocom_test.const import DeviceType, SubType
from kocom_test.models import DeviceKey
from kocom_test.gateway import KocomGateway

def gateway(host='127.0.0.1', port=8899):
    return KocomGateway(None, types.SimpleNamespace(entry_id=host), host, port)

def key(kind=DeviceType.LIGHT, room=1, index=0):
    return DeviceKey(kind, room, index, SubType.NONE)

def packet(code=0x0E, payload=(255,), command=0, room=1):
    body = bytes([0x30, 0xBC, 0, 1, 0, code, room, command]) + bytes(payload).ljust(8, b'\0')
    return b'\xaa\x55' + body + bytes([sum(body) % 256]) + b'\r\r'
