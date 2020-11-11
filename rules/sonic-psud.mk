# sonic-psud (SONiC PSU daemon) Debian package

# SONIC_PSUD_PY2 package

SONIC_PSUD_PY2 = sonic_psud-1.0-py2-none-any.whl
$(SONIC_PSUD_PY2)_SRC_PATH = $(SRC_PATH)/sonic-platform-daemons/sonic-psud
$(SONIC_PSUD_PY2)_DEPENDS = $(SONIC_PY_COMMON_PY2)
$(SONIC_PSUD_PY2)_PYTHON_VERSION = 2
SONIC_PYTHON_WHEELS += $(SONIC_PSUD_PY2)

# SONIC_PSUD_PY3 package

SONIC_PSUD_PY3 = sonic_psud-1.0-py3-none-any.whl
$(SONIC_PSUD_PY3)_SRC_PATH = $(SRC_PATH)/sonic-platform-daemons/sonic-psud
$(SONIC_PSUD_PY3)_DEPENDS = $(SONIC_PY_COMMON_PY3) $(SONIC_PSUD_PY2)
$(SONIC_PSUD_PY3)_PYTHON_VERSION = 3
SONIC_PYTHON_WHEELS += $(SONIC_PSUD_PY3)
