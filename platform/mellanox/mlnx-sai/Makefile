.ONESHELL:
SHELL = /bin/bash
.SHELLFLAGS += -e

MAIN_TARGET = mlnx-sai_1.mlnx.$(MLNX_SAI_VERSION)_amd64.deb
DERIVED_TARGETS = mlnx-sai-dbgsym_1.mlnx.$(MLNX_SAI_VERSION)_amd64.deb

$(addprefix $(DEST)/, $(MAIN_TARGET)): $(DEST)/% :
    wget -c https://github.com/xdp-project/xdp-tools/releases/download/v1.6.0/xdp-tools-1.6.0.tar.gz -O - | tar -xz
	pushd xdp-tools-1.6.0
	FORCE_SUBDIR_LIBBPF=1 ./configure
	make STATIC_CFLAGS="-fPIC" libxdp_install
	cp -r ./lib/libbpf/src/root/include/bpf /usr/local/include/
	cp ./lib/libbpf/src/libbpf.a /usr/local/lib/
	popd

	rm -rf mlnx_sai
	wget -c $(MLNX_SAI_SOURCE_BASE_URL)/$(MLNX_SAI_VERSION).tar.gz -O - | tar -xz
	pushd mlnx_sai

	debuild -e 'make_extra_flags="DEFS=-DACS_OS -DCONFIG_SYSLOG -DENABLE_SOFT_RATE_LIMIT -DCMAKE_SYSTEM_VERSION=$(KVERSION)"' -us -uc -d -b
	popd

	mv $(DERIVED_TARGETS) $* $(DEST)/
