#############################################################################
# Mellanox
#
# implementation of new platform api
#############################################################################

try:
    from sonic_platform_base.platform_base import PlatformBase
    from .chassis import Chassis, ModularChassis
    from .device_data import DeviceDataManager
except ImportError as e:
    raise ImportError(str(e) + "- required module not found")

class Platform(PlatformBase):
    def __init__(self):
        PlatformBase.__init__(self)
        if DeviceDataManager.get_linecard_count() == 0:
            self._chassis = Chassis()
        else:
            self._chassis = ModularChassis()
