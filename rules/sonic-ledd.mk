# sonic-ledd (SONiC Front-panel LED control daemon) Debian package

# SONIC_LEDD_PY2 package

SONIC_LEDD_PY2 = sonic_ledd-1.1-py2-none-any.whl
$(SONIC_LEDD_PY2)_SRC_PATH = $(SRC_PATH)/sonic-platform-daemons/sonic-ledd
$(SONIC_LEDD_PY2)_DEPENDS = $(SONIC_PY_COMMON_PY2)
$(SONIC_LEDD_PY2)_PYTHON_VERSION = 2
SONIC_PYTHON_WHEELS += $(SONIC_LEDD_PY2)

# SONIC_LEDD_PY3 package

SONIC_LEDD_PY3 = sonic_ledd-1.1-py3-none-any.whl
$(SONIC_LEDD_PY3)_SRC_PATH = $(SRC_PATH)/sonic-platform-daemons/sonic-ledd
$(SONIC_LEDD_PY3)_DEPENDS = $(SONIC_PY_COMMON_PY3) $(SONIC_LEDD_PY2)
$(SONIC_LEDD_PY3)_PYTHON_VERSION = 3
SONIC_PYTHON_WHEELS += $(SONIC_LEDD_PY3)
