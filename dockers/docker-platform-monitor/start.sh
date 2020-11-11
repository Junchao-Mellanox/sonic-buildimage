#!/usr/bin/env bash

# A flag that indicate whether we need reload the supervisor when detecting python3
# sonic platform API. This value is only specified in python3 version supervisor.conf.
DONT_RELOAD_SUPERVISOR_IF_PYTHON3_DETECTED=$1

# A flag that indicate whether python3 sonic platform API is detected.
PYTHON3_SONIC_PLATFORM_API_DETECTED=0
SUPERVISOR_CONF_FILE="/etc/supervisor/conf.d/supervisord.conf"

# A temporary supervisor configuration that could be used to start PMON 
# daemons using python3.
SUPERVISOR_CONF_PYTHON3_TMP_FILE="/etc/supervisor/conf.d/supervisord.conf.python3"
declare -r EXIT_SUCCESS="0"

mkdir -p /var/sonic
echo "# Config files managed by sonic-config-engine" > /var/sonic/config_status

# If this platform has synchronization script, run it
if [ -e /usr/share/sonic/platform/platform_wait ]; then
    /usr/share/sonic/platform/platform_wait
    EXIT_CODE="$?"
    if [ "${EXIT_CODE}" != "${EXIT_SUCCESS}" ]; then
        supervisorctl shutdown
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
          PYTHON3_SONIC_PLATFORM_API_DETECTED=1
       else
          echo "Error: Failed to install ${SONIC_PLATFORM_WHEEL}"
       fi
    else
       echo "Error: Unable to locate ${SONIC_PLATFORM_WHEEL}"
    fi
else
    PYTHON3_SONIC_PLATFORM_API_DETECTED=1
fi

if [ $PYTHON3_SONIC_PLATFORM_API_DETECTED -eq 1 ]; then
    if [ -z "$DONT_RELOAD_SUPERVISOR_IF_PYTHON3_DETECTED" ] then
        # start.sh is triggered by python2 supervisor.conf, need override it with python3
        # supervisor.conf and reload
        cp $SUPERVISOR_CONF_PYTHON3_TMP_FILE $SUPERVISOR_CONF_FILE
        rm -rf $SUPERVISOR_CONF_PYTHON3_TMP_FILE
        supervisorctl reload
    fi
else
    rm -rf $SUPERVISOR_CONF_PYTHON3_TMP_FILE
fi
