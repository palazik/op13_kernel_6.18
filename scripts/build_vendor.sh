#!/bin/bash
# build_vendor.sh: every OnePlus driver the stock phone loads, from OnePlus' own sources as its Bazel
# build defines them -- not a hand-made list. Two passes:
#
#   pass 1  the OnePlus 13's own 6.6 tree (src/mods8750): OnePlus' modules, Qualcomm's techpacks as
#           that tree has them, Qualcomm's touch-drivers techpack, the NFC chips' drivers, and the
#           driver directories of the OnePlus 13's own vendor kernel (src/msm8750, scripts/fetch.sh)
#   pass 2  what pass 1 could not build, again from OnePlus' 6.12 tree for the OnePlus 15
#           (src/mods8850): the same ColorOS 16 drivers, already ported past most of the kernel
#           changes 6.6 -> 6.12 (the scheduler's vendor hooks, block layer, dma-fence, ...)
#
# Each pass:
#  1. copies the sources into out/vendor/<pass>/tree;
#  2. scripts/bzl_eval.py runs the BUILD.bazel/.bzl files there (Starlark, run as Python with Bazel's
#     built-ins stubbed) and records every module: sources, include directories, defines, copts,
#     defconfig, the modules it links against; then the copy goes through the 6.12 -> 6.18 sweeps
#     (k618_fix618.py, tp_fix.py) and this port's own (oplus_fix.py, vendor_fix618.py);
#  3. scripts/vendor_kbuild.py writes a Kbuild.<module> per module into its package and the build
#     order: the wanted modules and what they need, less what is built already;
#  4. builds each: make -k M=<package> MO=<obj>/<module> OPMOD=<module> (-k: every file that does not
#     compile is in the log, not only the first), against the GKI build, the SoC
#     configuration (forced in, env.sh) and the Module.symvers of everything built before it;
#  5. builds the wanted modules that have only plain Makefiles (scripts/vendor_mkplan.py): each
#     directory once, with the options that switch them on.
#
# A module that fails is left out with the ones that need it; the boot image is made without it.
# Modules in out/vendormods (their names in out/vendormods.list, for mklists.py: they load last,
# probing in the background), a log each in out/vendor-log, the summary in out/vendor-log/summary.txt
# and the modules no pass built in out/vendor-log/failed.txt.
set -e
. "$(dirname "$0")/env.sh"
W=$ROOT/out/vendor
OUTM=$ROOT/out/vendormods
LOG=$ROOT/out/vendor-log
ST=$ROOT/device/op13/stock
[ -f "$S/Module.symvers" ] || { echo "build_vendor: build the SoC modules first"; exit 1; }
rm -rf "$W" "$OUTM" "$LOG" && mkdir -p "$W" "$OUTM" "$LOG"
# one make jobserver for the module builds that run side by side (fd 9, $J tokens): a module builds
# with as many CPUs as are free -- with -j1 each, the big ones (Wi-Fi) compiled one file at a time
# after the rest were done (~11 min of run 45427714881)
mkfifo "$W/jobserver" && exec 9<>"$W/jobserver" && rm -f "$W/jobserver"
printf '%*s' "$J" '' | tr ' ' '+' >&9
JOBSERVER="-j --jobserver-auth=9,9"
CONF=$W/soc-autoconf.h
soc_autoconf "$CONF"
: > "$LOG/summary.txt"
: > "$W/sg-held.txt"	# (the scheduler group's, below)

# what the other builds make already (SoC, techpacks, the kernel's own, OnePlus' boot and touch
# modules): not made again
HAVE=$W/have
mkdir -p "$HAVE"
for f in $(find "$S" "$ROOT/out/gki-staging" "$ROOT/out/tpmods" "$ROOT/out/oplusmods" "$ROOT/out/extramods" -name '*.ko' 2>/dev/null); do
	: > "$HAVE/$(basename "$f")"
done
SYMS="$S/Module.symvers $(ls "$ROOT"/out/tp-log/*.symvers "$ROOT"/out/oplus/*/Module.symvers "$ROOT"/out/extra/*/Module.symvers 2>/dev/null | tr '\n' ' ')"
# the OnePlus 13 vendor kernel's device-tree bindings (the other chipsets' clocks), after every other
# header: only those, its linux/ headers are 6.6's
mkdir -p "$W/inc8750"
[ -d "$ROOT/src/msm8750/include/dt-bindings" ] && ln -sfn "$ROOT/src/msm8750/include/dt-bindings" "$W/inc8750/dt-bindings"
ok=0; bad=0

# prepare <pass dir> <modules source> [kernel version it is written for, 6.6]: the build copy of one source tree, its module definitions
# (<pass dir>/modules.json), its fixes and the include directory its OnePlus headers are reached by
prepare() {
	local P=$1 SRC=$2 KV=${3:-6.6} T=$1/tree r d h p
	mkdir -p "$T/vendor/qcom/opensource" "$P/inc/soc/oplus" "$P/inc/linux"
	QREPOS=$(cd "$SRC/vendor/qcom/opensource" && ls -d -- */ | tr -d / | grep -v -- '-devicetree$' | tr '\n' ' ')
	cp -r "$SRC/vendor/oplus" "$T/vendor/oplus"
	# directory links inside the tree become copies, as Bazel's sandbox has them: through a link,
	# "<dir>/../x" resolves next to the link's target (charger/bazel/v2 -> ../v2: v2's
	# <../voocphy/...> found the old v1 voocphy, duplicate definitions in oplus_chg_v2)
	find "$T/vendor/oplus" -type l -xtype d | while read -r l; do
		tgt=$(readlink -f "$l")
		case $tgt in "$T"/*) rm "$l" && cp -r "$tgt" "$l" ;; esac
	done
	for d in st nxp; do [ -d "$SRC/vendor/$d" ] && cp -r "$SRC/vendor/$d" "$T/vendor/"; done
	for r in $QREPOS; do cp -r "$SRC/vendor/qcom/opensource/$r" "$T/vendor/qcom/opensource/"; done
	# the definitions, read before the copy's fixes; OnePlus' macro file from that tree's kernel_platform
	STOCK_CONFIG=$ROOT/out/soc-kconfig/.config:$ROOT/device/op13/oplus-features.config \
	BZL_KVER=$KV BZL_MACROS=$SRC/kernel_platform/oplus/bazel python3 -I "$ROOT/scripts/bzl_eval.py" "$T" "$P/modules.json" \
		vendor/oplus $(for r in $QREPOS; do echo "vendor/qcom/opensource/$r"; done)
	# this port's fixes first as well: some patterns the reference's sweep would take wrongly
	# (an hrtimer callback from a table)
	python3 -I "$ROOT/scripts/vendor_fix618.py" "$T/vendor" > /dev/null
	# the reference's sweep over the whole copy, its files on every CPU (scripts/par_fix618.py: the
	# same per-file fixes; one after another it took ~4 min a pass)
	python3 -I "$ROOT/scripts/par_fix618.py" "$REF" "$J" "$T"/vendor/* "$T"/msm8750 2>&1 \
		| grep -v 'k618_fix618: 0 files changed' || true
	for d in "$T"/vendor/* "$T"/msm8750; do
		[ -d "$d" ] && python3 -I "$ROOT/scripts/oplus_fix.py" "$d"
	done
	# the reference's 6.12 -> 6.18 techpack fixes (not its Xiaomi or 5.15 ones), as build_tp.sh, and
	# the API changes these sources meet (scripts/vendor_fix618.py)
	python3 -I "$ROOT/scripts/tp_fix.py" "$REF" "$T/vendor/qcom/opensource"
	python3 -I "$ROOT/scripts/vendor_fix618.py" "$T/vendor" "$T/msm8750"
	# trace headers name their own directory relative to the kernel's include/trace, in their tree's
	# layout (../../../vendor/..., ../../kernel/oplus_cpu/...): each relative one becomes the absolute
	# directory of the header that defines it -- where the trace file it names is there: OnePlus'
	# camera cam_trace_custom.h says "." for Qualcomm's cam_trace.h in another directory, found
	# through the include path as before (camera: "cam_module_utils/./cam_trace.h not found")
	grep -rlZ --include='*.h' --include='*.c' -E 'define[[:space:]]+[A-Z0-9_]*TRACE_INCLUDE_PATH[[:space:]]+\.' "$T" | while IFS= read -r -d '' h; do
		f=$(sed -n -E 's#^[[:space:]]*\#[[:space:]]*define[[:space:]]+TRACE_INCLUDE_FILE[[:space:]]+([^[:space:]]+).*#\1#p' "$h" | head -1)
		[ -n "$f" ] || f=$(basename "$h" .h)
		[ -e "$(dirname "$h")/$f.h" ] || continue
		# the path goes through the preprocessor: a component GNU C predefines as a macro (linux,
		# unix: 1) would turn into "1" (Wi-Fi: "qca-wifi-host-cmn/qdf/1/src/qdf_tracepoint_defs.h
		# not found"), so such a directory is reached through a link with a plain name
		d=$(dirname "$h")
		if echo "/$d/" | grep -qE '/(linux|unix|arm|aarch64|i386)/'; then
			mkdir -p "$P/tracedirs"
			n=$(ls "$P/tracedirs" | wc -l)
			ln -sfn "$d" "$P/tracedirs/t$n"
			d=$P/tracedirs/t$n
		fi
		sed -i -E "s#(define[[:space:]]+[A-Z0-9_]*TRACE_INCLUDE_PATH[[:space:]]+)\.[^[:space:]]*#\1$d#" "$h"
	done
	# the charger's generated IC tables (its Bazel rule oplus_chg_ic_cfg runs this script), where its
	# header target (oplus_chg_v2_ic_cfg_headers) has them
	local CHG=$T/vendor/oplus/kernel/charger/bazel
	if [ -f "$CHG/v2/scripts/ic_cfg_parse.py" ]; then
		for d in oplus_chg_ic_cfg oplus_chg_v2_ic_cfg; do
			mkdir -p "$CHG/$d"
			python3 "$CHG/v2/scripts/ic_cfg_parse.py" -hd "$CHG/$d/oplus_chg_ic_cfg.h" -s "$CHG/$d/oplus_chg_ic_cfg_str.h" \
				-df "$CHG/$d/oplus_chg_ic_auto_debug.h" "$CHG/v2/config/oplus_chg_ic.json" > "$P/charger-ic-cfg.log" 2>&1 \
				|| echo "build_vendor: the charger's IC tables were not made ($P/charger-ic-cfg.log)"
		done
	fi
	# the headers OnePlus' kernel tree has under include/soc/oplus and include/linux, by those paths;
	# boot's as a whole set in each place (oplus_project.h includes oplus_project_data_ocdt.h next to it)
	local K=$T/vendor/oplus/kernel
	lnk() { [ -e "$2" ] && { mkdir -p "$P/inc/$(dirname "$1")"; ln -sfn "$2" "$P/inc/$1"; }; return 0; }
	for h in "$K"/boot/include/*.h; do
		for p in soc/oplus soc/oplus/boot soc/oplus/system; do lnk "$p/$(basename "$h")" "$h"; done
	done
	for h in olc.h kernel_fb.h; do
		for p in soc/oplus/dft soc/oplus/system; do lnk "$p/$h" "$K/dft/include/$h"; done
	done
	lnk soc/oplus/device_info.h "$K/device_info/device_info/bazel/device_info.h"
	lnk soc/oplus/touchpanel_event_notify.h "$K/touchpanel/touchpanel_notify/bazel/touchpanel_event_notify.h"
	lnk soc/oplus/fpga_notify.h "$K/device_info/oplus_fpga/fpga_notify/fpga_notify.h"
	lnk soc/oplus/magcvr_notify.h "$K/device_info/magnetic_cover/magtransfer/magcvr_notify.h"
	lnk soc/oplus/dfr/oplus_bsp_dfr_ubt.h "$K/dfr/include/oplus_bsp_dfr_ubt.h"
	lnk soc/oplus/dfr/theia_send_event.h "$K/dfr/include/theia_send_event.h"
	lnk soc/oplus/system/oplus_mm_kevent_fb.h "$K/multimedia/feedback/common/oplus_mm_kevent_fb.h"
	lnk soc/oplus/system/oplus_trace_sensor.h "$T/vendor/oplus/sensor/kernel/oplus_trace_sensor/oplus_trace_sensor.h"
	lnk linux/oplus_kevent.h "$K/secureguard/gki2.0/rootguard_new/oplus_kevent.h"
	lnk linux/pogo_common.h "$K/device_info/pogo_keyboard/pogo_common.h"
	lnk soc/oplus/oplus_trackpoint_report.h "$T/vendor/qcom/opensource/display-drivers/oplus/common/trackpoint/oplus_trackpoint_report.h"
	lnk st_uapi.h "$T/vendor/st/opensource/driver/include/uapi/linux/nfc/st_uapi.h"
	# the UFS trace events moved into the UFS core (6.x): by their old path
	lnk trace/events/ufs.h "$C/drivers/ufs/core/ufs_trace.h"
	lnk ufs_trace.h "$C/drivers/ufs/core/ufs_trace.h"
	lnk ufs_trace_types.h "$C/drivers/ufs/core/ufs_trace_types.h"
	lnk pinctrl-msm.h "$S/drivers/pinctrl/qcom/pinctrl-msm.h"
	lnk trace/events/ufs_trace_types.h "$C/drivers/ufs/core/ufs_trace_types.h"
	# zsmalloc is the kernel's (GKI's zsmalloc.ko): its 6.18 header, not the SoC repo's copy of
	# 6.12's (include/linux/zsmalloc.h there, ahead of the kernel's: zs_map_object, 3-argument
	# zs_malloc), which hybridswap's zram calls into (scripts/oplus_fix.py)
	lnk linux/zsmalloc.h "$C/include/linux/zsmalloc.h"
	# OnePlus' cpu tree as its kernel has it (kernel/oplus_cpu): includes of "../kernel/oplus_cpu/..."
	# resolve from the include directory above
	mkdir -p "$P/kernel" && ln -sfn "$K/cpu" "$P/kernel/oplus_cpu"
	# and the rest of what the SoC repo links into OnePlus' tree, from this tree (env.sh)
	oplus_overlay "$P/inc" "$T/vendor/oplus"
}

# mk_loop <plan>: the Makefile directories of a plan (build()'s P, T), each made once
mk_loop() {
	local dir opts names tag MO vars defs o rc n o1 MO1 ko
	while IFS='|' read -r dir opts names; do
		tag=$(basename "$P")-mk-$(echo "$dir" | tr '/' '_')
		MO=$P/obj/$tag
		mkdir -p "$MO"
		# a Qualcomm phone, as OnePlus' kernel configuration says for every OnePlus module
		# (CONFIG_OPLUS_SYSTEM_KERNEL_QCOM=y; their Bazel builds' "qcom" defines): Makefiles choose
		# their sources by it -- cpufreq_bouncing's and kernel_fb's are MediaTek's without it
		vars="CONFIG_OPLUS_SYSTEM_KERNEL_QCOM=y"; defs="-DCONFIG_OPLUS_SYSTEM_KERNEL_QCOM=1"
		# OnePlus' CPU and scheduler modules, as OnePlus built them, for a kernel with WALT
		# (CONFIG_SCHED_WALT=m; the SoC here is configured without): sched_assist's WALT side
		# (qcom_rearrange_pipeline_preferred_cpus, which WALT calls), task_overload's WALT demand. On
		# the stock phone sched_assist, frame_boost and task_overload use only WALT's headers --
		# WALT links against them, not they against it.
		case $dir in vendor/oplus/kernel/cpu/*) defs="$defs -DCONFIG_SCHED_WALT_MODULE=1" ;; esac
		for o in $opts; do vars="$vars $o=m"; defs="$defs -D${o}_MODULE=1"; done
		# the roots the directory's Android Makefile hands its Kbuild (KBUILD_OPTIONS += X_ROOT=
		# $(KERNEL_SRC)/$(M)[/..]): NFC's Kbuild includes $(NFC_ROOT)/config/gki_nfc.conf
		if [ -f "$T/$dir/Makefile" ]; then
			for o in $(sed -n -E 's#^[[:space:]]*KBUILD_OPTIONS[[:space:]]*\+?=[[:space:]]*([A-Za-z0-9_]+)=\$\(KERNEL_SRC\)/\$\(M\)(/[^[:space:]]*)?[[:space:]]*$#\1=\2#p' "$T/$dir/Makefile"); do
				vars="$vars ${o%%=*}=$T/$dir${o#*=}"
			done
		fi
		# the symbol versions of everything built so far, less those of the modules this make makes
		# itself (its Makefile may make again one built already: modpost refuses an export twice) --
		# only those: obj-m and obj-$(CONFIG_X) with X one of ours or on in the kernel's configuration.
		# The rest stay (pinctrl-tuna needs the SoC's pinctrl-msm, which sits in the same Makefile
		# behind an option that is off here).
		python3 -I - "$T/$dir" "$MO/extra.symvers" "$opts" "$O/include/config/auto.conf" $SYMS <<'PY'
import os, re, sys
d, out, opts, autoconf, files = sys.argv[1], sys.argv[2], set(sys.argv[3].split()), sys.argv[4], sys.argv[5:]
on = set(opts)
if os.path.isfile(autoconf):
	on |= {l.split("=")[0] for l in open(autoconf) if re.match(r"CONFIG_\w+=[ym]$", l.strip())}
mine = set()
for f in ("Makefile", "Kbuild"):
	p = os.path.join(d, f)
	if os.path.isfile(p):
		t = open(p, errors="ignore").read().replace("\\\n", " ")
		for m in re.finditer(r"^\s*obj-(m|\$\((CONFIG_\w+)\))\s*[:+]?=\s*(.+)$", t, re.M):
			if m.group(1) == "m" or m.group(2) in on:
				mine |= {o[:-2].replace("-", "_") for o in m.group(3).split() if o.endswith(".o")}
with open(out, "w") as w:
	for f in files:
		if not os.path.isfile(f):
			continue
		for line in open(f, errors="ignore"):
			cols = line.rstrip("\n").split("\t")
			if len(cols) > 2 and os.path.basename(cols[2]).replace("-", "_") in mine:
				continue
			w.write(line)
PY
		set +e
		# shellcheck disable=SC2086
		"${KMAKE[@]}" -k M="$T/$dir" MO="$MO" modules -j"$J" KBUILD_EXTRA_SYMBOLS="$MO/extra.symvers" $vars \
			NOSTDINC_FLAGS="-nostdinc -I$P/inc -I$S/include -I$S/include/uapi -idirafter $W/inc8750 -idirafter $C -idirafter $S" \
			KCFLAGS="-Wno-error -include $CONF $defs" > "$LOG/$tag.log" 2>&1
		rc=$?
		set -e
		# one file that does not build stops the whole directory (msm8750's clocks: tcsrcc-tuna took
		# eleven others with it): each module not made is tried again on its own, with only the
		# option that switches it on
		if [ $rc -ne 0 ] && [ "$(echo $names | wc -w)" -gt 1 ]; then
			for n in $names; do
				[ -n "$(find "$MO" -name "$n.ko" -o -name "$(echo "$n" | tr '_' '-').ko" | head -1)" ] && continue
				o1=$(python3 -I - "$T/$dir" "$n" <<'PY2'
import os, re, sys
d, n = sys.argv[1], sys.argv[2]
for f in ("Makefile", "Kbuild"):
	p = os.path.join(d, f)
	if os.path.isfile(p):
		t = open(p, errors="ignore").read().replace("\\\n", " ")
		for m in re.finditer(r"^\s*obj-\$\((CONFIG_\w+)\)\s*[:+]?=\s*(.+)$", t, re.M):
			if any(o[:-2].replace("-", "_") == n for o in m.group(2).split()):
				print(m.group(1)); sys.exit(0)
PY2
)
				[ -n "$o1" ] || continue
				MO1=$MO-$n; mkdir -p "$MO1"; cp "$MO/extra.symvers" "$MO1/"
				set +e
				"${KMAKE[@]}" -k M="$T/$dir" MO="$MO1" modules -j"$J" KBUILD_EXTRA_SYMBOLS="$MO1/extra.symvers" "$o1=m" \
					CONFIG_OPLUS_SYSTEM_KERNEL_QCOM=y \
					NOSTDINC_FLAGS="-nostdinc -I$P/inc -I$S/include -I$S/include/uapi -idirafter $W/inc8750 -idirafter $C -idirafter $S" \
					KCFLAGS="-Wno-error -include $CONF $defs -D${o1}_MODULE=1" > "$LOG/$tag-$n.log" 2>&1
				set -e
				ko=$(find "$MO1" -name "$n.ko" -o -name "$(echo "$n" | tr '_' '-').ko" | head -1)
				[ -n "$ko" ] && cp "$ko" "$MO/" && [ -f "$MO1/Module.symvers" ] && SYMS="$SYMS $MO1/Module.symvers"
			done
		fi
		for n in $names; do
			ko=$(find "$MO" -name "$n.ko" -o -name "$(echo "$n" | tr '_' '-').ko" | head -1)
			if [ -n "$ko" ]; then
				echo "build_vendor: $n: built ($dir)" | tee -a "$LOG/summary.txt"
				ok=$((ok + 1)); cp "$ko" "$OUTM/"
			else
				echo "build_vendor: $n: FAILED ($dir, make exit $rc)" | tee -a "$LOG/summary.txt"
			fi
		done
		[ $rc -eq 0 ] || grep -E ': (fatal )?error: |^ERROR: modpost' "$LOG/$tag.log" | sed -E "s|$T/||" | head -6 \
			| tee -a "$LOG/summary.txt" || true
		[ -f "$MO/Module.symvers" ] && case " $SYMS " in *" $MO/Module.symvers "*) ;; *) SYMS="$SYMS $MO/Module.symvers" ;; esac
	done < "$1"
	# (not the status of the loop's last test: under set -e a 1 here ended the script -- run
	# 45706583266 stopped after the Makefile retry, before the OnePlus 15 pass)
	return 0
}

# build <pass dir> <wanted lists> <search dirs for Makefile-only modules...>
build() {
	local P=$1 WANT=$2 T=$1/tree name pkg base needs n MO rc dir opts names tag vars defs o ko _l
	shift 2
	python3 -I "$ROOT/scripts/vendor_kbuild.py" "$P/modules.json" "$T" "$WANT" "$HAVE:$OUTM" "$P/plan.txt"
	# in dependency levels (a module after everything it links against), each level's modules side
	# by side, one per CPU: most of them need nothing of one another
	python3 -I - "$P/plan.txt" > "$P/levels.txt" <<'PY'
import sys
plan = [l.rstrip("\n").split("|") for l in open(sys.argv[1]) if l.strip()]
names = {p[0] for p in plan}
lvl = {}
for name, _pkg, _base, needs in plan:		# the plan is in dependency order
	lvl[name] = 1 + max([lvl.get(n, 0) for n in needs.split() if n in names] + [0])
for p in plan:
	print("%d|%s" % (lvl[p[0]], "|".join(p)))
PY
	: > "$P/failed"
	local level max
	max=$(cut -d'|' -f1 "$P/levels.txt" | sort -n | tail -1)
	for level in $(seq 1 "${max:-0}"); do
		while IFS='|' read -r _l name pkg base needs; do
			[ "$_l" = "$level" ] || continue
			for n in $needs; do
				if grep -qx "$n" "$P/failed"; then
					echo "build_vendor: $name: left out ($n failed)" | tee -a "$LOG/summary.txt"
					echo "$name" >> "$P/failed"; continue 2
				fi
			done
			while [ "$(jobs -rp | wc -l)" -ge "$J" ]; do wait -n || true; done
			(
				MO=$P/obj/$name
				mkdir -p "$MO"
				# (the subshell has set -e: a failing make would end it before .result is written,
				# and the module would count as neither built nor failed -- its failure never reached
				# $P/failed or the summary, and nothing could try it again)
				rc=0
				MAKEFLAGS="$JOBSERVER" "${KMAKE[@]}" -k M="$T/$pkg" MO="$MO" OPMOD="$base" modules KBUILD_EXTRA_SYMBOLS="$SYMS" \
					NOSTDINC_FLAGS="-nostdinc -I$P/inc -I$S/include -I$S/include/uapi -idirafter $W/inc8750 -idirafter $C -idirafter $S" \
					KCFLAGS="-Wno-error -include $CONF" > "$LOG/$(basename "$P")-$name.log" 2>&1 || rc=$?
				echo "$rc $MO/$base.ko" > "$MO/.result"
			) < /dev/null &
		done < "$P/levels.txt"
		wait
		# the level's results, in plan order
		while IFS='|' read -r _l name pkg base needs; do
			[ "$_l" = "$level" ] || continue
			MO=$P/obj/$name
			[ -d "$MO" ] || continue
			rc=1; ko=$MO/$base.ko
			[ -f "$MO/.result" ] && read -r rc ko < "$MO/.result"
			if [ "$rc" -ne 0 ] || [ ! -f "$ko" ]; then
				echo "build_vendor: $name: FAILED ($(basename "$P"), make exit $rc)" | tee -a "$LOG/summary.txt"
				grep -E ': (fatal )?error: |^ERROR: modpost' "$LOG/$(basename "$P")-$name.log" | sed -E "s|$T/||" | head -6 \
					| tee -a "$LOG/summary.txt" || true
				echo "$name" >> "$P/failed"
				continue
			fi
			echo "build_vendor: $name: built ($(basename "$P"))" | tee -a "$LOG/summary.txt"
			ok=$((ok + 1))
			SYMS="$SYMS $MO/Module.symvers"
			cp "$ko" "$OUTM/"
		done < "$P/levels.txt"
	done
	# modules that failed only at the link, for a symbol of a module of this pass their Bazel target
	# does not name (the OnePlus 13's tri_key and sensor_feedback call kernel_fb's oplus_kevent_fb,
	# magnetic_cover calls magcvr_notify): tried again in this pass, before its Makefile modules (the
	# hall sensors link against tri_key) and before the next pass takes them -- for the OnePlus 13,
	# while everything exported is the kernel's, the SoC's, the techpacks' or its own
	local r
	for r in 1 2 3; do
		again=0
		retry_round "$(basename "$P") relink round $r" "$P"
		[ "$again" -eq 1 ] || break
	done
	# the wanted modules made from plain Makefiles: each directory built once, with the options that
	# switch its wanted modules on set to m (and defined for the C code)
	python3 -I "$ROOT/scripts/vendor_mkplan.py" "$T" "$WANT" "$HAVE:$OUTM" "$P/mkplan.txt" "$@"
	mk_loop "$P/mkplan.txt"
	mk_again
	return 0
}

# mk_again (build()'s P, T): a Makefile directory whose modules failed only at the link, for a symbol
# a directory built after it exports (the OnePlus 13's oplus_f2fslog_storage and oplus_uprobe:
# pr_storage of oplus_storage_log, a directory later; its scheduler group: sched_assist, then
# frame_boost, then task_overload), built again for the modules still missing -- while that builds
# any. Also once WALT is built (schedinfo links against it).
mk_again() {
	local round before
	for round in 1 2 3; do
		python3 -I - "$P/mkplan.txt" "$P/mkplan2.txt" "$OUTM" "$LOG" "$(basename "$P")" <<'PY'
import os, re, sys
plan, out, outm, log, pas = sys.argv[1:]
have = {f[:-3].replace("-", "_") for f in os.listdir(outm) if f.endswith(".ko")}
keep = []
for line in open(plan):
	d, opts, names = line.rstrip("\n").split("|")
	missing = [n for n in names.split() if n.replace("-", "_") not in have]
	if not missing:
		continue
	tag = pas + "-mk-" + d.replace("/", "_")
	logs = [os.path.join(log, tag + ".log")] + [os.path.join(log, tag + "-" + n + ".log") for n in missing]
	text = "".join(open(l, errors="ignore").read() for l in logs if os.path.isfile(l))
	if re.search(r"^ERROR: modpost: .* undefined!", text, re.M) and not re.search(r": (fatal )?error: ", text):
		keep.append("%s|%s|%s\n" % (d, opts, " ".join(missing)))
open(out, "w").write("".join(keep))
PY
		[ -s "$P/mkplan2.txt" ] || break
		echo "build_vendor: $(wc -l < "$P/mkplan2.txt") Makefile directories again, for symbols built after them (round $round)" | tee -a "$LOG/summary.txt"
		before=$(ls "$OUTM" | wc -l)
		mk_loop "$P/mkplan2.txt"
		[ "$(ls "$OUTM" | wc -l)" -gt "$before" ] || break
	done
	return 0
}

# the wanted modules no pass has built yet (stock lists, less what exists): one name a line
still_wanted() {
	O=$O python3 -I - "$1" "$HAVE" "$OUTM" "$ST/first_stage.load" "$ST/vendor_dlkm.load" "$ST/system_dlkm.load" \
		"$ST/../no-source.txt" <<'PY'
import os, sys
out, have, outm, *lists = sys.argv[1:]
nosrc = lists.pop()
c = lambda n: os.path.basename(n.strip()).removesuffix(".ko").replace("-", "_")
# (and WALT's modules, scripts/build_walt.sh: out/waltmods next to out/vendormods)
waltm = os.path.join(os.path.dirname(outm), "waltmods")
got = {c(f) for d in (have, outm, waltm) if os.path.isdir(d) for f in os.listdir(d)}
# what the kernel has built in (xhci-sideband, drm_display_helper: =y in our GKI)
builtin = os.path.join(os.environ.get("O", ""), "modules.builtin")
if os.path.isfile(builtin):
	got |= {c(l) for l in open(builtin) if l.strip()}
skip = {c(l) for l in open(nosrc) if l.strip() and not l.startswith("#")} if os.path.isfile(nosrc) else set()
want = []
for l in lists:
	for line in open(l):
		n = c(line)
		if n and n not in got and n not in skip and n not in want:
			want.append(n)
open(out, "w").write("".join(n + ".ko\n" for n in want))
print(len(want))
PY
}

# retry_round <label> <pass dirs...>: modules of those passes that failed only because something
# they link against was not built yet when they were tried -- every undefined symbol exported by now
# (SYMS) -- or were left out for a dependency built by now, tried again. Sets again=1 on progress.
retry_round() {
	local label=$1 tag P name pkg base needs missing L MO _l _n
	shift
	tag=$(echo "$label" | tr -c 'A-Za-z0-9\n' '_')
	# every symbol exported by now
	cat $SYMS "$O/Module.symvers" "$S/Module.symvers" 2>/dev/null | awk '{ print $2 }' | sort -u > "$W/exported.txt"
	ls "$OUTM" "$HAVE" "$ROOT/out/waltmods" 2>/dev/null | sed -n 's/\.ko$//p' | sort -u \
		| grep -vxF -f "$W/sg-held.txt" > "$W/built.txt" || true
	again=0
	echo "$label: $(wc -l < "$W/exported.txt") symbols exported, $(wc -l < "$W/built.txt") modules built; passes: $*" >> "$LOG/retry.txt"
	for P in "$@"; do
		[ -f "$P/failed" ] || echo "second chance: no $P/failed" >> "$LOG/retry.txt"
		[ -f "$P/failed" ] && [ -f "$P/levels.txt" ] || continue
		while read -r name; do
			pkg=; base=; needs=
			IFS='|' read -r _l _n pkg base needs < <(awk -F'|' -v n="$name" '$2 == n' "$P/levels.txt") || true
			[ -n "$pkg" ] || { echo "second chance: $name: not in $(basename "$P")'s plan" >> "$LOG/retry.txt"; continue; }
			grep -qx "$base" "$W/built.txt" && continue
			# the scheduler group from sched_assist's tree only (above)
			case " $SCHED_GROUP " in *" $base "*)
				if [ -n "$SG_ONLY_OP13" ]; then [ "$(basename "$P")" = op13 ] || continue
				else [ "$(basename "$P")" != op13 ] || continue; fi ;;
			esac
			L=$LOG/$(basename "$P")-$name.log
			if [ -f "$L" ]; then
				grep -q '^ERROR: modpost: .* undefined!' "$L" || { echo "second chance: $name: not a link failure" >> "$LOG/retry.txt"; continue; }
				grep -qE ': (fatal )?error: ' "$L" && { echo "second chance: $name: compile errors" >> "$LOG/retry.txt"; continue; }
				sed -n -E 's/^ERROR: modpost: "([^"]+)" .* undefined!/\1/p' "$L" | sort -u > "$W/need.txt"
				if [ -n "$(comm -23 "$W/need.txt" "$W/exported.txt")" ]; then
					echo "second chance: $name: still missing $(comm -23 "$W/need.txt" "$W/exported.txt" | head -3 | tr '\n' ' ')" >> "$LOG/retry.txt"
					continue
				fi
			else
				# left out: all it needs built by now
				missing=0
				for d in $needs; do grep -qx "$d" "$W/built.txt" || missing=1; done
				[ "$missing" -eq 0 ] || continue
			fi
			MO=$P/obj/$name
			mkdir -p "$MO"
			set +e
			MAKEFLAGS="$JOBSERVER" "${KMAKE[@]}" -k M="$P/tree/$pkg" MO="$MO" OPMOD="$base" modules KBUILD_EXTRA_SYMBOLS="$SYMS" \
				NOSTDINC_FLAGS="-nostdinc -I$P/inc -I$S/include -I$S/include/uapi -idirafter $W/inc8750 -idirafter $C -idirafter $S" \
				KCFLAGS="-Wno-error -include $CONF" > "$L.$tag" 2>&1
			set -e
			if [ -f "$MO/$base.ko" ]; then
				echo "build_vendor: $name: built ($(basename "$P"), $label)" | tee -a "$LOG/summary.txt"
				cp "$MO/$base.ko" "$OUTM/"; ok=$((ok + 1)); again=1
				echo "$base" >> "$W/built.txt"
				[ -f "$MO/Module.symvers" ] && SYMS="$SYMS $MO/Module.symvers"
			else
				echo "build_vendor: $name: FAILED again ($(basename "$P"), $L.$tag)" | tee -a "$LOG/summary.txt"
				grep -E ': (fatal )?error: |^ERROR: modpost' "$L.$tag" | head -4 | tee -a "$LOG/summary.txt" || true
				# the next round judges it by this attempt
				cp "$L.$tag" "$L"
			fi
		done < "$P/failed"
	done
	return 0
}

# pass 1: the OnePlus 13's own 6.6 tree, with Qualcomm's touch-drivers, the NFC drivers of the
# SM8850 release and the OnePlus 13 vendor kernel's driver directories
P1=$W/op13
prepare "$P1" "$ROOT/src/mods8750"
if [ -d "$ROOT/src/touch-drivers" ]; then
	cp -r "$ROOT/src/touch-drivers" "$P1/tree/vendor/qcom/opensource/touch-drivers" && rm -rf "$P1/tree/vendor/qcom/opensource/touch-drivers/.git"
	BZL_MACROS=$ROOT/src/mods8750/kernel_platform/oplus/bazel python3 -I "$ROOT/scripts/bzl_eval.py" "$P1/tree" \
		"$P1/touch.json" vendor/qcom/opensource/touch-drivers
	python3 -I - "$P1/modules.json" "$P1/touch.json" <<'PY'
import json, sys
a, b = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
a["modules"] += b["modules"]; a["headers"].update(b["headers"])
json.dump(a, open(sys.argv[1], "w"))
PY
	python3 -I "$REF/k618_fix618.py" "$P1/tree/vendor/qcom/opensource/touch-drivers" | grep -v 'k618_fix618: 0 files changed' || true
	python3 -I "$ROOT/scripts/vendor_fix618.py" "$P1/tree/vendor/qcom/opensource/touch-drivers"
fi
mkdir -p "$P1/tree/vendor/oplus8850/kernel" && cp -r "$ROOT/src/mods8850/vendor/oplus/kernel/nfc" "$P1/tree/vendor/oplus8850/kernel/"
python3 -I "$REF/k618_fix618.py" "$P1/tree/vendor/oplus8850" | grep -v 'k618_fix618: 0 files changed' || true
python3 -I "$ROOT/scripts/vendor_fix618.py" "$P1/tree/vendor/oplus8850"
if [ -d "$ROOT/src/msm8750/drivers" ]; then
	mkdir -p "$P1/tree/msm8750" && cp -r "$ROOT/src/msm8750/drivers" "$P1/tree/msm8750/"
	python3 -I "$REF/k618_fix618.py" "$P1/tree/msm8750" | grep -v 'k618_fix618: 0 files changed' || true
	python3 -I "$ROOT/scripts/vendor_fix618.py" "$P1/tree/msm8750"
fi
build "$P1" "$ST/first_stage.load:$ST/vendor_dlkm.load:$ST/system_dlkm.load" \
	vendor/oplus vendor/st vendor/nxp vendor/oplus8850 vendor/qcom/opensource msm8750


# OnePlus' scheduler group shares one per-task struct, allocated by sched_assist (its
# sa_common_struct.h), whose layout differs between the OnePlus 13's tree and the OnePlus 15's (fields
# in between, QOS_SCHED's among them): a member built from the other tree than sched_assist reads and
# writes it at other offsets. So the group comes from one tree: with the OnePlus 13's sched_assist
# built, none of it from the OnePlus 15's (a member that did not build is left out); without it, the
# OnePlus 13's members built so far go and pass 2 builds them with the OnePlus 15's sched_assist.
SCHED_GROUP="oplus_bsp_sched_assist oplus_bsp_frame_boost oplus_bsp_task_overload oplus_bsp_task_sched
	oplus_bsp_schedinfo oplus_bsp_schedtune oplus_bsp_qos_sched oplus_bsp_task_cpustats osml_monitor
	ua_cpu_ioctl oplus_btuart_ux oplus_wq_dynamic_priority oplus_binder_strategy oplus_locking_strategy
	oplus_bsp_uxmem_opt oplus_bsp_dynamic_readahead oplus_bsp_dfr_kp_freeze_detect
	oplus_bsp_dfr_fdleak_check oplus_bsp_hybridswap_zram oplus_lock_torture"
SCHED_GROUP=$(echo $SCHED_GROUP)
SG_ONLY_OP13=
: > "$W/sg-held.txt"
if [ -f "$OUTM/oplus_bsp_sched_assist.ko" ]; then
	echo "build_vendor: the OnePlus 13's sched_assist: its scheduler group only from the OnePlus 13's tree" | tee -a "$LOG/summary.txt"
	SG_ONLY_OP13=1
	# the members not built yet count as had for pass 2 (not built there, nor as another one's
	# dependency); the second chances below still try the OnePlus 13's (schedinfo needs WALT first)
	for m in $SCHED_GROUP; do
		[ -f "$OUTM/$m.ko" ] && continue
		: > "$HAVE/$m.ko"; echo "$m" >> "$W/sg-held.txt"
		echo "build_vendor: $m: not from the OnePlus 15's tree (the OnePlus 13's sched_assist)" | tee -a "$LOG/summary.txt"
	done
else
	for m in $SCHED_GROUP; do
		[ -f "$OUTM/$m.ko" ] || continue
		rm -f "$OUTM/$m.ko"; ok=$((ok - 1))
		echo "build_vendor: $m: the OnePlus 13's set aside (sched_assist is the OnePlus 15's)" | tee -a "$LOG/summary.txt"
	done
fi

# pass 2: what is still missing, from OnePlus' 6.12 tree for the OnePlus 15
n=$(still_wanted "$W/wanted2.txt")
echo "build_vendor: pass 1 built $ok; $n still wanted, from the OnePlus 15's 6.12 tree" | tee -a "$LOG/summary.txt"
if [ "$n" -gt 0 ]; then
	P2=$W/op15
	prepare "$P2" "$ROOT/src/mods8850" 6.12
	build "$P2" "$W/wanted2.txt" vendor/oplus vendor/qcom/opensource
fi

# WALT (scripts/build_walt.sh: sched-walt, sched-penalty, oplus_bsp_close_loop), here: it links against
# OnePlus' scheduler modules, built by now, and what is still to be tried below links against it --
# OnePlus' schedinfo (walt_rq; a second chance) and the SoC's hung_task_enh and cpu_mpam (WALT's task
# data; late SoC modules). Its modules are in out/waltmods; one that does not build is left out.
set +e
bash "$ROOT/scripts/build_walt.sh" > "$LOG/walt.txt" 2>&1
set -e
grep '^build_walt: ' "$LOG/walt.txt" | tee -a "$LOG/summary.txt" || true
WSYMS=
[ -f "$ROOT/out/waltmods/sched-walt.ko" ] && WSYMS=$(find "$ROOT/out/walt" -name Module.symvers | tr '\n' ' ')
SYMS="$SYMS $WSYMS"
# the OnePlus 13's Makefile modules that link against WALT (its schedinfo)
if [ -n "$WSYMS" ]; then
	P=$P1; T=$P1/tree
	mk_again
fi

# late SoC modules: the SoC build drops what does not link, and some SoC modules link against
# OnePlus' (on the OnePlus 13 the charger, oplus_chg_v2, exports qti_battery_charger_get_prop and
# register_hboost_event_notifier, which the flash LEDs and qcom-hv-haptics use) -- built before
# OnePlus' existed. Each dropped one that is still wanted is tried once more, alone, against
# everything built by now (its objects in out/soc-late, the SoC tree's own Kbuild put back after).
still_wanted "$W/wanted-late.txt" > /dev/null
# SoC modules that need WALT itself, not its stubs (struct walt_task_struct)
WALT_USERS="hung_task_enh cpu_mpam"
if [ -s "$ROOT/out/soc-dropped.txt" ] && [ -f "$S/Kbuild" ]; then
	cp "$S/Kbuild" "$W/soc-Kbuild.saved"
	# the SoC tree is built in place: 6.18 refuses a separate-output build (MO=) of a tree with a
	# modules.order ("The external module source tree is not clean": run 45427714881 built none of
	# these), so it is set aside meanwhile
	[ -f "$S/modules.order" ] && mv "$S/modules.order" "$W/soc-modules.order.saved"
	# (not the kernel's own Module.symvers: modpost reads it itself, and twice is "exported twice"
	# for every kernel symbol -- the late pass failed so in test-48, once it ran at all)
	LSYMS="$S/Module.symvers $(find "$W" -name Module.symvers 2>/dev/null | tr '\n' ' ') \
$(find "$ROOT/out/oplus" "$ROOT/out/extra" -name Module.symvers 2>/dev/null | tr '\n' ' ') \
$(ls "$ROOT"/out/tp-log/*.symvers 2>/dev/null | tr '\n' ' ') $WSYMS"
	while read -r m; do
		k=$(basename "$m" | tr '-' '_')
		grep -qx "$k.ko" "$W/wanted-late.txt" || continue
		MO=$ROOT/out/soc-late/$k
		rm -rf "$MO" && mkdir -p "$MO"
		python3 -I "$REF/k618_soc_kbuild.py" kbuild "$S" "$ROOT/out/soc-kconfig/.config" "$O/.config" "$m" \
			> "$LOG/soc-late-$k.log" 2>&1 || continue
		# the SoC is configured without WALT (its modules use walt.h's stubs); those that read WALT's
		# task data have nothing to read without it, so they are built with it once WALT is
		kc=
		case " $WALT_USERS " in *" $k "*) [ -n "$WSYMS" ] && kc="-DCONFIG_SCHED_WALT_MODULE=1" ;; esac
		set +e
		"${KMAKE[@]}" M="$S" MO="$MO" -j"$J" modules KBUILD_EXTRA_SYMBOLS="$LSYMS" ${kc:+KCFLAGS="$kc"} >> "$LOG/soc-late-$k.log" 2>&1
		set -e
		ko=$(find "$MO" -name "$(basename "$m").ko" | head -1)
		if [ -n "$ko" ]; then
			echo "build_vendor: $k: built (SoC, after OnePlus' modules)" | tee -a "$LOG/summary.txt"
			cp "$ko" "$OUTM/"; ok=$((ok + 1))
		else
			echo "build_vendor: $k: FAILED (SoC, late)" | tee -a "$LOG/summary.txt"
			grep -E ': (fatal )?error: |^ERROR: modpost' "$LOG/soc-late-$k.log" | head -4 | tee -a "$LOG/summary.txt" || true
		fi
	done < "$ROOT/out/soc-dropped.txt"
	cp "$W/soc-Kbuild.saved" "$S/Kbuild"
	[ -f "$W/soc-modules.order.saved" ] && mv "$W/soc-modules.order.saved" "$S/modules.order"
fi

# a second chance for what failed only because something it links against was not built yet when
# it was tried: modules whose only errors are undefined symbols that are all exported by now (by a
# module built later in the run, the late SoC modules -- swr_haptics: qcom-hv-haptics'
# register_hboost_event_notifier -- or one built in the other pass: file_read_record and
# mm_osvelte's oplus_read_mm_config), and modules left out for a dependency that is built now.
# Twice: one built here can complete another.
LATE=$(find "$ROOT/out/soc-late" -name Module.symvers 2>/dev/null | tr '\n' ' ')
SYMS="$SYMS $LATE"
for round in 1 2; do
	retry_round "second chance round $round" "$P1" ${P2:+"$P2"}
	[ "$again" -eq 1 ] || break
done

n=$(still_wanted "$LOG/failed.txt")
# the scheduler group's members held out of pass 2 and still not built
for m in $(cat "$W/sg-held.txt"); do
	[ -f "$OUTM/$m.ko" ] || { echo "$m.ko" >> "$LOG/failed.txt"; n=$((n + 1)); }
done
ls "$OUTM" | sed 's/\.ko$//' > "$ROOT/out/vendormods.list"
echo "build_vendor: built $ok; not built: $n (failed.txt)" | tee -a "$LOG/summary.txt"
[ "$n" -eq 0 ]
