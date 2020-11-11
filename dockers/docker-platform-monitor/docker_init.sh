#!/usr/bin/env bash

# Generate supervisord config file and the start.sh scripts
mkdir -p /etc/supervisor/conf.d/

SENSORS_CONF_FILE="/usr/share/sonic/platform/sensors.conf"
FANCONTROL_CONF_FILE="/usr/share/sonic/platform/fancontrol"

HAVE_SENSORS_CONF=0
HAVE_FANCONTROL_CONF=0

SUPERVISOR_CONF_PYTHON2_TEMPLATE="/usr/share/sonic/templates/docker-pmon.supervisord.conf.j2"
SUPERVISOR_CONF_PYTHON3_TEMPLATE="/usr/share/sonic/templates/docker-pmon.supervisord.conf.python3.j2"

# Default supervisor.conf point to python2 version as most vendor supports python2 now
SUPERVISOR_CONF_TEMPLATE=$SUPERVISOR_CONF_PYTHON2_TEMPLATE
SUPERVISOR_CONF_FILE="/etc/supervisor/conf.d/supervisord.conf"

# A temporary supervisor.conf.python3 that start PMON daemons using python3.
# This file will be used by start.sh if start.h detects the python3 version sonic 
# platform API at run time. start.sh will override the python2 version 
# supervisor.conf with this file and reload supervisord.
SUPERVISOR_CONF_PYTHON3_TMP_FILE="/etc/supervisor/conf.d/supervisord.conf.python3"
PMON_DAEMON_CONTROL_FILE="/usr/share/sonic/platform/pmon_daemon_control.json"

# A flag that indicate whether to generate $SUPERVISOR_CONF_PYTHON3_TMP_FILE. Default 1
# means $SUPERVISOR_CONF_PYTHON3_TMP_FILE will be generated.
CREATE_TEMP_SUPERVISOR_CONF_PYTHON3=1

if [ -e $SENSORS_CONF_FILE ]; then
    HAVE_SENSORS_CONF=1
fi

if [ -e $FANCONTROL_CONF_FILE ]; then
    HAVE_FANCONTROL_CONF=1
fi

python3 -c "import sonic_platform" > /dev/null 2>&1 || pip3 show sonic-platform > /dev/null 2>&1
if [ $? -eq 0 ]; then
    # Python3 verison sonic platform API detected, override $SUPERVISOR_CONF_TEMPLATE with python3 version
    SUPERVISOR_CONF_TEMPLATE=/usr/share/sonic/templates/docker-pmon.supervisord.conf.python3.j2
    # Don't generate $SUPERVISOR_CONF_PYTHON3_TMP_FILE as the supervior.conf is already the python3 version
    CREATE_TEMP_SUPERVISOR_CONF_PYTHON3=0
fi

confvar="{\"HAVE_SENSORS_CONF\":$HAVE_SENSORS_CONF, \"HAVE_FANCONTROL_CONF\":$HAVE_FANCONTROL_CONF}"

if [ -e $PMON_DAEMON_CONTROL_FILE ];
then
    sonic-cfggen -j $PMON_DAEMON_CONTROL_FILE -a "$confvar" -t $SUPERVISOR_CONF_TEMPLATE > $SUPERVISOR_CONF_FILE
    if [ $CREATE_TEMP_SUPERVISOR_CONF_PYTHON3 -eq 1]; then
        sonic-cfggen -j $PMON_DAEMON_CONTROL_FILE -a "$confvar" -t $SUPERVISOR_CONF_PYTHON3_TEMPLATE > $SUPERVISOR_CONF_PYTHON3_TMP_FILE
    fi
else
    sonic-cfggen -a "$confvar" -t $SUPERVISOR_CONF_TEMPLATE > $SUPERVISOR_CONF_FILE
    if [ $CREATE_TEMP_SUPERVISOR_CONF_PYTHON3 -eq 1]; then
        sonic-cfggen -a "$confvar" -t $SUPERVISOR_CONF_PYTHON3_TEMPLATE > $SUPERVISOR_CONF_PYTHON3_TMP_FILE
    fi
fi

# If this platform has an lm-sensors config file, copy it to its proper place
if [ $HAVE_SENSORS_CONF -eq 1 ]; then
    mkdir -p /etc/sensors.d
    /bin/cp -f $SENSORS_CONF_FILE /etc/sensors.d/
fi

# If this platform has a fancontrol config file, copy it to its proper place
if [ $HAVE_FANCONTROL_CONF -eq 1 ]; then
    # Remove stale pid file if it exists
    rm -f /var/run/fancontrol.pid

    /bin/cp -f $FANCONTROL_CONF_FILE /etc/
fi

exec /usr/bin/supervisord
