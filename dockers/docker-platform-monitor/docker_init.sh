#!/usr/bin/env bash

# Generate supervisord config file and the start.sh scripts
mkdir -p /etc/supervisor/conf.d/

SENSORS_CONF_FILE="/usr/share/sonic/platform/sensors.conf"
FANCONTROL_CONF_FILE="/usr/share/sonic/platform/fancontrol"
SUPERVISOR_CONF_TEMPLATE="/usr/share/sonic/templates/docker-pmon.supervisord.conf.j2"
SUPERVISOR_CONF_FILE="/etc/supervisor/conf.d/supervisord.conf"
PMON_DAEMON_CONTROL_FILE="/usr/share/sonic/platform/pmon_daemon_control.json"

HAVE_SENSORS_CONF=0
HAVE_FANCONTROL_CONF=0
# Default use python2 version
SONIC_PLATFORM_API_PYTHON_VERSION=2

declare -r EXIT_SUCCESS="0"

mkdir -p /var/sonic
echo "# Config files managed by sonic-config-engine" > /var/sonic/config_status

# If this platform has synchronization script, run it
if [ -e /usr/share/sonic/platform/platform_wait ]; then
    /usr/share/sonic/platform/platform_wait
    EXIT_CODE="$?"
    if [ "${EXIT_CODE}" != "${EXIT_SUCCESS}" ]; then
        exit "${EXIT_CODE}"
    fi
fi

# If the Python 2 sonic-platform package is not installed, try to install it
python2 -c "import sonic_platform" > /dev/null 2>&1 || pip2 show sonic-platform > /dev/null 2>&1
if [ $? -ne 0 ]; then
    SONIC_PLATFORM_WHEEL="/usr/share/sonic/platform/sonic_platform-1.0-py2-none-any.whl"
    echo "sonic-platform package not installed, attempting to install..."
    if [ -e ${SONIC_PLATFORM_WHEEL} ]; then
       pip2 install ${SONIC_PLATFORM_WHEEL}
       if [ $? -eq 0 ]; then
          echo "Successfully installed ${SONIC_PLATFORM_WHEEL}"
       else
          echo "Error: Failed to install ${SONIC_PLATFORM_WHEEL}"
       fi
    else
       echo "Error: Unable to locate ${SONIC_PLATFORM_WHEEL}"
    fi
fi

# If the Python 3 sonic-platform package is not installed, try to install it
python3 -c "import sonic_platform" > /dev/null 2>&1 || pip3 show sonic-platform > /dev/null 2>&1
if [ $? -ne 0 ]; then
    SONIC_PLATFORM_WHEEL="/usr/share/sonic/platform/sonic_platform-1.0-py3-none-any.whl"
    echo "sonic-platform package not installed, attempting to install..."
    if [ -e ${SONIC_PLATFORM_WHEEL} ]; then
       pip3 install ${SONIC_PLATFORM_WHEEL}
       if [ $? -eq 0 ]; then
          echo "Successfully installed ${SONIC_PLATFORM_WHEEL}"
          SONIC_PLATFORM_API_PYTHON_VERSION=3
       else
          echo "Error: Failed to install ${SONIC_PLATFORM_WHEEL}"
       fi
    else
       echo "Error: Unable to locate ${SONIC_PLATFORM_WHEEL}"
    fi
else
    SONIC_PLATFORM_API_PYTHON_VERSION=3

if [ -e $SENSORS_CONF_FILE ]; then
    HAVE_SENSORS_CONF=1
    mkdir -p /etc/sensors.d
    /bin/cp -f $SENSORS_CONF_FILE /etc/sensors.d/
fi

if [ -e $FANCONTROL_CONF_FILE ]; then
    HAVE_FANCONTROL_CONF=1
    rm -f /var/run/fancontrol.pid
    /bin/cp -f $FANCONTROL_CONF_FILE /etc/
fi

confvar="{\"HAVE_SENSORS_CONF\":$HAVE_SENSORS_CONF, \"HAVE_FANCONTROL_CONF\":$HAVE_FANCONTROL_CONF, \"API_VERSION\":$SONIC_PLATFORM_API_PYTHON_VERSION}"

if [ -e $PMON_DAEMON_CONTROL_FILE ];
then
    sonic-cfggen -j $PMON_DAEMON_CONTROL_FILE -a "$confvar" -t $SUPERVISOR_CONF_TEMPLATE > $SUPERVISOR_CONF_FILE
else
    sonic-cfggen -a "$confvar" -t $SUPERVISOR_CONF_TEMPLATE > $SUPERVISOR_CONF_FILE
fi

exec /usr/bin/supervisord
