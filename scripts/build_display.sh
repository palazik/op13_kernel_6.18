#!/bin/bash
# build_display.sh: the display driver, msm_drm, as the OnePlus 13 has it -- its own release of
# Qualcomm's display techpack (SM8750) with OnePlus' display extension (display-drivers/oplus/SM8750:
# /dev/oplus_display, /sys/kernel/oplus_display, the panel's brightness, PWM and AOD modes, the
# ultra-low-power AOD ColorOS' always-on full-screen AOD needs, ...). Its 6.6-era interface (uapi:
# sde_drm.h, colour blobs) is the one the OnePlus 13's composer speaks.
#
# build_tp.sh builds msm_drm from the OnePlus 15's release, without the extension (its OnePlus 15
# sources are not the OnePlus 13's panels), with tp_op13_fix.py's stand-ins for what the composer
# and the panels need from it. That one stays the fallback: this one replaces it in out/tpmods
# when it builds.
#
# Run after build_vendor.sh: the extension links against OnePlus' modules (projectinfo, FPGA notify,
# touch notify, trackpoint; built there and by build_extra.sh), and nothing links against msm_drm.
# Sources: build_vendor.sh's copy of the OnePlus 13's tree (out/vendor/op13/tree), with the 6.6 ->
# 6.18 sweeps it applied to every OnePlus 13 source, then this driver's own fixes and the
# extension's Kbuild lines (scripts/disp_op13_fix.py). Headers of the techpacks it calls into
# (hardware fences, synx, ...) are build_tp.sh's, which built them. Log in out/display-log.
set -e
. "$(dirname "$0")/env.sh"
P1=$ROOT/out/vendor/op13
SRC=$P1/tree/vendor/qcom/opensource/display-drivers
TP=$ROOT/src/vendor/qcom/opensource
B=$ROOT/out/display
LOG=$ROOT/out/display-log
OUTM=$ROOT/out/tpmods
rm -rf "$B" "$LOG" && mkdir -p "$B" "$LOG"
: > "$ROOT/out/display.txt"
[ -f "$SRC/msm/Kbuild" ] || { echo "build_display: no OnePlus 13 tree from build_vendor.sh ($SRC)"; exit 1; }
[ -f "$OUTM/msm_drm.ko" ] && [ -f "$ROOT/out/tp-log/display.symvers" ] \
	|| { echo "build_display: build_tp.sh's msm_drm is missing"; exit 1; }
# next to the techpacks it includes from by relative paths (../mm-drivers/...), as in build_tp.sh
D=$B/display-drivers
cp -r "$SRC" "$D"
for r in "$TP"/*; do
	[ "$(basename "$r")" = display-drivers ] || ln -sfn "$r" "$B/$(basename "$r")"
done
python3 -I "$ROOT/scripts/disp_op13_fix.py" "$D"
MMINC="$TP/synx-kernel/msm/synx $TP/synx-kernel/include $TP/synx-kernel/include/uapi
       $TP/synx-kernel/include/uapi/synx/media $TP/mm-drivers/hw_fence/include
       $TP/mm-drivers/hfi_core/inc $TP/mm-drivers/msm_ext_display/include
       $TP/mm-drivers/sync_fence/include $TP/securemsm-kernel/hdcp
       $TP/securemsm-kernel $TP/securemsm-kernel/include $TP/securemsm-kernel/include/linux
       $TP/dsp-kernel/include"
EI=; for i in $MMINC; do EI="$EI -I$i"; done
TPCONF=$ROOT/out/tp-autoconf.h
[ -f "$TPCONF" ] || soc_autoconf "$TPCONF" techpacks
# the modules' symbols as build_walt.sh takes them, but the OnePlus 15 msm_drm's (exported twice
# otherwise); not the kernel's own Module.symvers (modpost reads it itself)
SYMS="$S/Module.symvers $(ls "$ROOT"/out/tp-log/*.symvers 2>/dev/null | grep -v '/display\.symvers$' | tr '\n' ' ') \
$(find "$ROOT/out/vendor" "$ROOT/out/oplus" "$ROOT/out/extra" -name Module.symvers 2>/dev/null | tr '\n' ' ')"
start=$(date +%s)
set +e
# OnePlus' headers by OnePlus' paths (<soc/oplus/...>) as build_vendor.sh made them for this tree
# shellcheck disable=SC2086
"${KMAKE[@]}" M="$D" modules -j"$J" KERNEL_SRC="$C" KERNEL_ROOT="$C" \
	CONFIG_ARCH_SUN=y KBUILD_EXTRA_SYMBOLS="$SYMS" \
	NOSTDINC_FLAGS="-nostdinc -I$P1/inc -I$S/include -I$S/include/uapi$EI" \
	KCFLAGS="-Wno-error -include $TPCONF" DISPLAY_ROOT="$D" CONFIG_DRM_MSM=m > "$LOG/display.log" 2>&1
rc=$?
set -e
ko=$D/msm/msm_drm.ko
if [ $rc -ne 0 ] || [ ! -f "$ko" ]; then
	echo "build_display: msm_drm (OnePlus 13, with OnePlus' extension): FAILED (make exit $rc); the OnePlus 15's stays"
	grep -E ': (fatal )?error: |^ERROR: modpost|No rule to make target' "$LOG/display.log" \
		| sed -E "s|$D/||" | awk -F: '!seen[$1$2]++' | head -40 || true
	echo op15 > "$ROOT/out/display.txt"
	exit 1
fi
cp "$OUTM/msm_drm.ko" "$LOG/msm_drm-op15.ko"
cp "$ROOT/out/tp-log/display.symvers" "$LOG/display-op15.symvers"
cp "$ko" "$OUTM/msm_drm.ko"
cp "$D/Module.symvers" "$ROOT/out/tp-log/display.symvers"
echo op13 > "$ROOT/out/display.txt"
echo "build_display: msm_drm (OnePlus 13, with OnePlus' extension): built in $(( $(date +%s) - start )) s ($(grep -c ' warning: ' "$LOG/display.log") warnings); the OnePlus 15's set aside"
