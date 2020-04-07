from sonic_platform_base.sonic_thermal_control.thermal_action_base import ThermalPolicyActionBase
from sonic_platform_base.sonic_thermal_control.thermal_json_object import thermal_json_object


class SetFanSpeedAction(ThermalPolicyActionBase):
    """
    Base thermal action class to set speed for fans
    """
    # JSON field definition
    JSON_FIELD_SPEED = 'speed'

    def __init__(self):
        """
        Constructor of SetFanSpeedAction which actually do nothing.
        """
        self.speed = None

    def load_from_json(self, json_obj):
        """
        Construct SetFanSpeedAction via JSON. JSON example:
            {
                "type": "fan.all.set_speed"
                "speed": "100"
            }
        :param json_obj: A JSON object representing a SetFanSpeedAction action.
        :return:
        """
        if SetFanSpeedAction.JSON_FIELD_SPEED in json_obj:
            speed = float(json_obj[SetFanSpeedAction.JSON_FIELD_SPEED])
            if speed < 0 or speed > 100:
                raise ValueError('SetFanSpeedAction invalid speed value {} in JSON policy file, valid value should be [0, 100]'.
                                 format(speed))
            self.speed = float(json_obj[SetFanSpeedAction.JSON_FIELD_SPEED])
        else:
            raise ValueError('SetFanSpeedAction missing mandatory field {} in JSON policy file'.
                             format(SetFanSpeedAction.JSON_FIELD_SPEED))


@thermal_json_object('fan.all.set_speed')
class SetAllFanSpeedAction(SetFanSpeedAction):
    """
    Action to set speed for all fans
    """
    def execute(self, thermal_info_dict):
        """
        Set speed for all fans
        :param thermal_info_dict: A dictionary stores all thermal information.
        :return:
        """
        from .thermal_infos import FanInfo
        if FanInfo.INFO_NAME in thermal_info_dict and isinstance(thermal_info_dict[FanInfo.INFO_NAME], FanInfo):
            fan_info_obj = thermal_info_dict[FanInfo.INFO_NAME]
            for fan in fan_info_obj.get_presence_fans():
                fan.set_speed(self.speed)


@thermal_json_object('fan.all.check_and_set_speed')
class CheckAndSetAllFanSpeedAction(SetAllFanSpeedAction):
    """
    Action to check thermal zone temperature and recover speed for all fans
    """
    def execute(self, thermal_info_dict):
        """
        Check thermal zone and set speed for all fans
        :param thermal_info_dict: A dictionary stores all thermal information.
        :return:
        """
        from .thermal import Thermal
        if Thermal.check_thermal_zone_temperature():
            SetAllFanSpeedAction.execute(self, thermal_info_dict)
        

@thermal_json_object('thermal_control.control')
class ControlThermalAlgoAction(ThermalPolicyActionBase):
    """
    Action to control the thermal control algorithm
    """
    # JSON field definition
    JSON_FIELD_STATUS = 'status'

    def __init__(self):
        self.status = True

    def load_from_json(self, json_obj):
        """
        Construct ControlThermalAlgoAction via JSON. JSON example:
            {
                "type": "thermal_control.control"
                "status": "true"
            }
        :param json_obj: A JSON object representing a ControlThermalAlgoAction action.
        :return:
        """
        if ControlThermalAlgoAction.JSON_FIELD_STATUS in json_obj:
            status_str = json_obj[ControlThermalAlgoAction.JSON_FIELD_STATUS].lower()
            if status_str == 'true':
                self.status = True
            elif status_str == 'false':
                self.status = False
            else:
                raise ValueError('Invalid {} field value, please specify true of false'.
                                 format(ControlThermalAlgoAction.JSON_FIELD_STATUS))
        else:
            raise ValueError('ControlThermalAlgoAction '
                             'missing mandatory field {} in JSON policy file'.
                             format(ControlThermalAlgoAction.JSON_FIELD_STATUS))

    def execute(self, thermal_info_dict):
        """
        Disable thermal control algorithm
        :param thermal_info_dict: A dictionary stores all thermal information.
        :return:
        """
        from .thermal_infos import FanInfo
        from .thermal import Thermal
        from .fan import Fan
        Thermal.set_thermal_algorithm_status(self.status, False)
        if self.status:
            # Check thermal zone temperature, if all thermal zone temperature
            # back to normal and FAN speed is still 100%, set it to minimum allowed speed to
            # save power
            if Thermal.check_thermal_zone_temperature():
                fan_info_obj = thermal_info_dict[FanInfo.INFO_NAME]
                for fan in fan_info_obj.get_presence_fans():
                    if fan.get_target_speed() != 100:
                        break
                    fan.set_speed(Fan.min_cooling_level * 10)


class ChangeMinCoolingLevelAction(ThermalPolicyActionBase):
    UNKNOWN_SKU_COOLING_LEVEL = 6
    def execute(self, thermal_info_dict):
        from .device_data import DEVICE_DATA
        from .fan import Fan
        from .thermal_infos import ChassisInfo
        from .thermal_conditions import MinCoolingLevelChangeCondition

        chassis = thermal_info_dict[ChassisInfo.INFO_NAME].get_chassis()
        if chassis.sku_name not in DEVICE_DATA or 'thermal' not in DEVICE_DATA[chassis.sku_name] or 'minimum_table' not in DEVICE_DATA[chassis.sku_name]['thermal']:
            Fan.min_cooling_level = ChangeMinCoolingLevelAction.UNKNOWN_SKU_COOLING_LEVEL
        else:
            air_flow_dir = MinCoolingLevelChangeCondition.air_flow_dir
            trust_state = MinCoolingLevelChangeCondition.trust_state
            temperature = MinCoolingLevelChangeCondition.temperature
            minimum_table = DEVICE_DATA[chassis.sku_name]['thermal']['minimum_table']['{}_{}'.format(air_flow_dir, trust_state)]

            for key, cooling_level in minimum_table.items():
                temp_range = key.split(':')
                temp_min = int(temp_range[0]) * 1000
                temp_max = int(temp_range[1]) * 1000
                if temp_min <= temperature <= temp_max:
                    Fan.min_cooling_level = cooling_level - 10
                    break
        
        current_cooling_level = Fan.get_cooling_level()
        if current_cooling_level < Fan.min_cooling_level:
            Fan.set_cooling_level(Fan.min_cooling_level)
