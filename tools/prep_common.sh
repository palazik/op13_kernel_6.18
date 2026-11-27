#!/bin/bash
# prep_common.sh: the minimum of the reference's (ref_8550/kernel-6.18/k618_gki.sh) edits to ACK 6.18
# that the vendor modules and the boot method need. Its KernelSU/SUSFS, scheduler and memory
# tuning are left out for the first boot. Idempotent.
set -e
ROOT=$(cd "$(dirname "$0")/.." && pwd)
C=$ROOT/src/common
REF=$ROOT/ref_8550/kernel-6.18

# display driver needs the DP/HDCP/HDMI helpers and the bridge connector built in
KG=$C/init/Kconfig.gki
if ! grep -q 'select DRM_DISPLAY_HELPER' $KG; then
	sed -i 's/^\tselect DRM_CLIENT if (DRM)$/\tselect DRM_CLIENT if (DRM)\n\tselect DRM_DISPLAY_HELPER if (DRM)\n\tselect DRM_DISPLAY_DP_HELPER if (DRM)\n\tselect DRM_DISPLAY_HDCP_HELPER if (DRM)\n\tselect DRM_DISPLAY_HDMI_HELPER if (DRM)\n\tselect DRM_BRIDGE_CONNECTOR if (DRM)/' $KG
	grep -q 'select DRM_DISPLAY_HELPER' $KG || { echo "display helpers: edit failed"; exit 1; }
	echo "display helpers added to GKI_HIDDEN_DRM_CONFIGS"
fi

# a thermal zone without a sensor in the vendor DT is skipped instead of failing the whole parse
TO=$C/drivers/thermal/thermal_of.c
if ! grep -q 'k618: a zone without a sensor' $TO; then
	perl -0pi -e 's|\t\tif \(count <= 0\) \{\n\t\t\tpr_err\("%pOFn: missing thermal sensor\\n", child\);\n\t\t\treturn ERR_PTR\(-EINVAL\);\n\t\t\}|\t\tif (count <= 0) {\n\t\t\t/* k618: a zone without a sensor is skipped, as older kernels did */\n\t\t\tpr_debug("%pOFn: missing thermal sensor\\n", child);\n\t\t\tcontinue;\n\t\t}|' $TO
	grep -q 'k618: a zone without a sensor' $TO || { echo "thermal_of: edit failed"; exit 1; }
	echo "thermal zones without a sensor skipped"
fi

# netlink extended permissions off: the ROM's sepolicy was written for 6.6, which had none
SE=$C/security/selinux/include/security.h
if ! grep -q 'k618: the old netlink checks' $SE; then
	perl -0pi -e 's|static inline bool selinux_policycap_netlink_xperm\(void\)\n\{\n\treturn READ_ONCE\(\n\t\tselinux_state.policycap\[POLICYDB_CAP_NETLINK_XPERM\]\);\n\}|static inline bool selinux_policycap_netlink_xperm(void)\n{\n\t/* k618: the old netlink checks, as 6.6 */\n\treturn false;\n}|' $SE
	grep -q 'k618: the old netlink checks' $SE || { echo "selinux: edit failed"; exit 1; }
	echo "netlink extended permissions off"
fi

# vendor SMMU fault handlers on domains that already carry the DMA API's cookie
IO=$C/drivers/iommu/iommu.c
if ! grep -q 'k618: the handler of a domain' $IO; then
	perl -0pi -e 's#\tif \(WARN_ON\(!domain \|\| domain->cookie_type != IOMMU_COOKIE_NONE\)\)\n\t\treturn;\n\n\tdomain->cookie_type = IOMMU_COOKIE_FAULT_HANDLER;#\tif (WARN_ON(!domain))\n\t\treturn;\n\n\t/*\n\t * k618: the handler of a domain that already carries a cookie (the DMA API\x27s, for every\n\t * vendor driver that asks for its device\x27s domain) goes into the Android vendor data;\n\t * report_iommu_fault() looks there too\n\t */\n\tif (domain->cookie_type != IOMMU_COOKIE_NONE) {\n\t\tdomain->android_vendor_data1[0] = (u64)(uintptr_t)handler;\n\t\tdomain->android_vendor_data1[1] = (u64)(uintptr_t)token;\n\t\treturn;\n\t}\n\n\tdomain->cookie_type = IOMMU_COOKIE_FAULT_HANDLER;#' $IO
	perl -0pi -e 's#(static void iommu_domain_init\(struct iommu_domain \*domain, unsigned int type,\n\t\t\t      const struct iommu_ops \*ops\)\n\{\n)#$1\tandroid_init_vendor_data(domain, 1);\t/* k618: no stale fault handler in a new domain */\n#' $IO
	perl -0pi -e 's#\tif \(domain->cookie_type == IOMMU_COOKIE_FAULT_HANDLER &&\n\t    domain->handler\)\n\t\tret = domain->handler\(domain, dev, iova, flags,\n\t\t\t\t\t\tdomain->handler_token\);\n#\tif (domain->cookie_type == IOMMU_COOKIE_FAULT_HANDLER &&\n\t    domain->handler)\n\t\tret = domain->handler(domain, dev, iova, flags,\n\t\t\t\t\t\tdomain->handler_token);\n\telse if (domain->android_vendor_data1[0])\t/* k618, see iommu_set_fault_handler() */\n\t\tret = ((iommu_fault_handler_t)(uintptr_t)domain->android_vendor_data1[0])(\n\t\t\tdomain, dev, iova, flags,\n\t\t\t(void *)(uintptr_t)domain->android_vendor_data1[1]);\n#' $IO
	grep -q 'k618: the handler of a domain' $IO && grep -q 'k618, see iommu_set_fault_handler' $IO \
		&& grep -q 'k618: no stale fault handler' $IO || { echo "iommu: edit failed"; exit 1; }
	echo "vendor SMMU fault handler kept in the domain's vendor data"
fi

# op13_modload_tolerate=1 on the command line: a module that fails to load is logged and answered as
# loaded. Android's first-stage init ends the boot at the first module it cannot insmod, and with
# androidboot.load_modules_parallel (this phone's bootconfig) a tolerating module of its own could
# not be guaranteed to load first; in the kernel it is there from the start.
MM=$C/kernel/module/main.c
if ! grep -q 'op13_modload_tolerate' $MM; then
	perl -0pi -e 's#(SYSCALL_DEFINE3\(init_module, void __user \*, umod,\n)#/*\n * op13: op13_modload_tolerate=1 -- a failed load is reported and answered as loaded, so that a\n * bring-up boot gets past modules that do not load yet (tools/prep_common.sh)\n */\nstatic bool op13_modload_tolerate;\ncore_param(op13_modload_tolerate, op13_modload_tolerate, bool, 0644);\n\nstatic int op13_tolerate(int err, struct file *file)\n{\n\tif (err >= 0 \|\| err == -EEXIST \|\| !op13_modload_tolerate)\n\t\treturn err;\n\tif (err == -ENOEXEC)\t/* built for another kernel: the loader names it already */\n\t\treturn 0;\n\tif (file)\n\t\tpr_emerg("op13: %pD did not load (%d), answered as loaded\\n", file, err);\n\telse\n\t\tpr_emerg("op13: init_module did not load (%d), answered as loaded\\n", err);\n\treturn 0;\n}\n\n$1#' $MM
	perl -0pi -e 's#\treturn load_module\(&info, uargs, 0\);\n\}\n\nstruct idempotent \{#\treturn op13_tolerate(load_module(&info, uargs, 0), NULL);\n}\n\nstruct idempotent {#' $MM
	perl -0pi -e 's#\treturn idempotent_init_module\(fd_file\(f\), uargs, flags\);\n\}#\treturn op13_tolerate(idempotent_init_module(fd_file(f), uargs, flags), fd_file(f));\n}#' $MM
	[ "$(grep -c 'op13_tolerate(' $MM)" = 3 ] || { echo "module load tolerance: edit failed"; exit 1; }
	echo "module load tolerance (op13_modload_tolerate) added"
fi
# A module built for another kernel (the phone's 6.6 vendor_dlkm ones, which Android's second stage
# tries again for every device) is named the first 20 times, then counted: 1,700 such lines pushed
# the first 2.7 s of test-26's boot out of the 256 KB ramoops log. Ours are built against this
# kernel (the CRC check in build_bootimg.sh), so this message is never about one of them.
if ! grep -q 'op13_foreign_modules' $MM; then
	python3 -I - "$MM" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
old = '''	if (shdr->sh_size != sizeof(struct module)) {
		pr_err("module %s: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time\\n",
		       info->name ?: "(missing .modinfo section or name field)");
		return -ENOEXEC;
	}'''
new = '''	if (shdr->sh_size != sizeof(struct module)) {
		static atomic_t op13_foreign_modules;	/* op13: tools/prep_common.sh */
		int n = atomic_inc_return(&op13_foreign_modules);

		if (n <= 20 || !(n % 500))
			pr_err("module %s: .gnu.linkonce.this_module section size must match the kernel's built struct module size at run time (%d so far%s)\\n",
			       info->name ?: "(missing .modinfo section or name field)", n,
			       n == 20 ? ", next ones counted only" : "");
		return -ENOEXEC;
	}'''
assert s.count(old) == 1, "struct module size check"
open(p, "w").write(s.replace(old, new))
PY
	grep -q 'op13_foreign_modules' $MM || { echo "foreign module message: edit failed"; exit 1; }
	echo "foreign module message capped"
fi

# op13_ramoops=<size>@<address>: that RAM kept out of the kernel's hands from the start, for the
# built-in ramoops (ramoops.mem_address= etc. on the command line). The stock 6.6 kernel's ramoops
# region is allocated at boot (ramoops-region in the DT has no fixed address) and lands at
# 0x880000000 there; a test kernel writing its log to the same place, with the same layout, leaves it
# for the stock kernel to show in /sys/fs/pstore after the next boot -- from the first printk on,
# not only once the first-stage modules load.
RO=$C/fs/pstore/ram.c
if ! grep -q 'op13_ramoops' $RO; then
	perl -0pi -e 's|(#include <linux/module.h>\n)|$1#include <linux/memblock.h>\n|' $RO
	cat >> $RO <<'EOF'

/* op13: see tools/prep_common.sh */
static int __init op13_ramoops_reserve(char *s)
{
	phys_addr_t size, base;

	if (!s)
		return -EINVAL;
	size = memparse(s, &s);
	if (*s != '@' || !size)
		return -EINVAL;
	base = memparse(s + 1, &s);
	if (memblock_is_region_reserved(base, size))
		pr_warn("op13_ramoops: %pa+%pa is reserved already\n", &base, &size);
	return memblock_reserve(base, size);
}
early_param("op13_ramoops", op13_ramoops_reserve);
EOF
	grep -q '#include <linux/memblock.h>' $RO && grep -q 'early_param("op13_ramoops"' $RO \
		|| { echo "ramoops reservation: edit failed"; exit 1; }
	echo "op13_ramoops= reservation added"
fi

# ramoops.console_pin=<bytes>: the console zone's first <bytes> are kept, the rest is the ring. With
# nothing filtered, the 256 KB the stock layout gives the console held only the last ~80 s and lost
# the boot's start (test-40's log began at 2.35 s). Once the zone is full, a write wraps to <bytes>
# instead of 0: the boot's start stays, the end keeps coming. The zone's header is the stock one, so
# the stock kernel shows it as before -- ring (older part), the pinned start, ring (newest part).
RC=$C/fs/pstore/ram_core.c
RI=$C/fs/pstore/ram_internal.h
if ! grep -q 'op13: pinned start' $RC; then
	python3 -I - "$RC" "$RI" "$RO" <<'PY'
import sys
rc, ri, ro = sys.argv[1:4]
def edit(path, old, new, n=1):
	t = open(path).read()
	assert t.count(old) == n, (path, old, t.count(old))
	open(path, "w").write(t.replace(old, new))
edit(ri, "\tchar *old_log;\n\tsize_t old_log_size;\n};",
     "\tchar *old_log;\n\tsize_t old_log_size;\n\tsize_t pin;\t/* op13: pinned start (tools/prep_common.sh) */\n};")
# the ring wraps to the pinned start, not to 0
edit(rc, "\twhile (unlikely(new >= prz->buffer_size))\n\t\tnew -= prz->buffer_size;\n\tatomic_set(&prz->buffer->start, new);",
     "\twhile (unlikely(new >= prz->buffer_size))\n\t\tnew -= prz->buffer_size - prz->pin;\t/* op13: pinned start */\n"
     "\tatomic_set(&prz->buffer->start, new);")
edit(rc, "\tif (unlikely(c > prz->buffer_size)) {\n\t\ts += c - prz->buffer_size;\n\t\tc = prz->buffer_size;\n\t}\n\n\tbuffer_size_add(prz, c);",
     "\tif (unlikely(c > prz->buffer_size - prz->pin)) {\n\t\ts += c - (prz->buffer_size - prz->pin);\n"
     "\t\tc = prz->buffer_size - prz->pin;\n\t}\n\n\tbuffer_size_add(prz, c);", 2)	# write and write_user
edit(rc, "\t\ts += rem;\n\t\tc -= rem;\n\t\tstart = 0;\n\t}\n\tpersistent_ram_update(prz, s, start, c);\n\n\tpersistent_ram_update_header_ecc(prz);",
     "\t\ts += rem;\n\t\tc -= rem;\n\t\tstart = prz->pin;\n\t}\n\tpersistent_ram_update(prz, s, start, c);\n\n\tpersistent_ram_update_header_ecc(prz);")
edit(rc, "\t\ts += rem;\n\t\tc -= rem;\n\t\tstart = 0;\n\t}\n\tif (likely(!ret))",
     "\t\ts += rem;\n\t\tc -= rem;\n\t\tstart = prz->pin;\n\t}\n\tif (likely(!ret))")
# the parameter, for the console zone only
edit(ro, "MODULE_PARM_DESC(console_size, \"size of kernel console log\");\n",
     "MODULE_PARM_DESC(console_size, \"size of kernel console log\");\n\n"
     "/* op13: see tools/prep_common.sh */\nstatic ulong op13_console_pin;\n"
     "module_param_named(console_pin, op13_console_pin, ulong, 0400);\n"
     "MODULE_PARM_DESC(console_pin, \"bytes at the console zone's start a full ring does not overwrite\");\n")
edit(ro, "\terr = ramoops_init_prz(\"console\", dev, cxt, &cxt->cprz, &paddr,\n\t\t\t       cxt->console_size, 0);\n\tif (err)\n\t\tgoto fail_init;\n",
     "\terr = ramoops_init_prz(\"console\", dev, cxt, &cxt->cprz, &paddr,\n\t\t\t       cxt->console_size, 0);\n\tif (err)\n\t\tgoto fail_init;\n"
     "\tif (cxt->cprz && op13_console_pin &&\n"
     "\t    op13_console_pin + 4096 <= cxt->cprz->buffer_size) {\t/* op13: pinned start */\n"
     "\t\tcxt->cprz->pin = op13_console_pin;\n"
     "\t\tpr_info(\"op13: console zone: first %lu bytes kept\\n\", op13_console_pin);\n\t}\n")
PY
	grep -q 'op13: pinned start' $RC && grep -q 'size_t pin;' $RI && grep -q 'console_pin' $RO \
		|| { echo "pstore pinned start: edit failed"; exit 1; }
	echo "ramoops.console_pin= added"
fi

# fw_devlink's "Fixed dependency cycle(s) with ..." -- 779 lines, 73 KB of the first 0.74 s on this
# phone's DT (test-31), a note on the DT's structure, the same every boot, which the stock log level
# (6) hides anyway: a debug message, so the console zone holds the boot (ramoops.console_pin above)
DC=$C/drivers/base/core.c
if grep -q 'pr_info("%pfwf: Fixed dependency cycle(s) with %pfwf' $DC; then
	sed -i 's|pr_info("%pfwf: Fixed dependency cycle(s) with %pfwf|pr_debug("%pfwf: Fixed dependency cycle(s) with %pfwf|' $DC
	grep -q 'pr_debug("%pfwf: Fixed dependency cycle(s)' $DC || { echo "fw_devlink cycle note: edit failed"; exit 1; }
	echo "fw_devlink cycle note: debug"
fi

# A BPF program that fails to load: its name, type and error, and the end of the verifier's answer,
# in the kernel log (pstore). Android's loaders log the answer only to logd, which a boot that never
# finishes does not keep: test-43 stopped at the boot animation because OnePlus' oplus-netd.o did not
# load (NetBpfLoad: "non-optional program failed to load") and netd waited for it for ever -- and
# said nothing more. The first 16 failures get the verifier's text, the rest one line each.
BS=$C/kernel/bpf/syscall.c
if ! grep -q 'op13: failed program loads' $BS; then
	python3 -I - "$BS" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
old_case = "\tcase BPF_PROG_LOAD:\n\t\terr = bpf_prog_load(&attr, uattr, size);\n\t\tbreak;\n"
old_fn = "static int __sys_bpf(enum bpf_cmd cmd, bpfptr_t uattr, unsigned int size)\n"
assert t.count(old_case) == 1 and t.count(old_fn) == 1, "bpf report: anchors"
fn = r'''/* op13: failed program loads, in the kernel log (tools/prep_common.sh) */
static void op13_bpf_report(const union bpf_attr *attr, bpfptr_t uattr, int err)
{
	static atomic_t n_reported = ATOMIC_INIT(0);
	const u32 chunk = 2048;
	u32 size = attr->log_size, off = 0, keep = 0, len;
	char *buf, *p, *line;

	pr_err("bpf: program '%.16s' (type %u, attach %u, %u insns) did not load: %d\n",
		attr->prog_name, attr->prog_type, attr->expected_attach_type, attr->insn_cnt, err);
	if (atomic_inc_return(&n_reported) > 16 || uattr.is_kernel || !attr->log_buf ||
	    !attr->log_level || size < 2)
		return;
	buf = kmalloc(2 * chunk + 1, GFP_KERNEL);
	if (!buf)
		return;
	/* walk the log to its end 2 KB at a time, keeping the last two chunks */
	while (off < size) {
		u32 n = min(chunk, size - off);

		if (keep == 2 * chunk) {
			memmove(buf, buf + chunk, chunk);
			keep = chunk;
		}
		if (copy_from_user(buf + keep, u64_to_user_ptr(attr->log_buf + off), n))
			break;
		len = strnlen(buf + keep, n);
		keep += len;
		if (len < n)
			break;
		off += n;
	}
	buf[keep] = 0;
	p = keep > 1536 ? strchrnul(buf + keep - 1536, '\n') : buf;
	if (!keep)
		pr_err("bpf:   (the verifier said nothing)\n");
	while ((line = strsep(&p, "\n")) != NULL)
		if (*line)
			pr_err("bpf:   %s\n", line);
	kfree(buf);
}

'''
t = t.replace(old_fn, fn + old_fn)
old_sc = "SYSCALL_DEFINE3(bpf, int, cmd, union bpf_attr __user *, uattr, unsigned int, size)\n{\n\treturn __sys_bpf(cmd, USER_BPFPTR(uattr), size);\n}\n"
assert t.count(old_sc) == 1, "bpf report: syscall anchor"
t = t.replace(old_sc, """SYSCALL_DEFINE3(bpf, int, cmd, union bpf_attr __user *, uattr, unsigned int, size)
{
	int ret = __sys_bpf(cmd, USER_BPFPTR(uattr), size);

	/* op13: the BPF loaders' failing calls, whatever the command (tools/prep_common.sh) */
	if (ret < 0 && (strstr(current->comm, "BpfLoad") || strstr(current->comm, "bpfload"))) {
		static atomic_t n_calls = ATOMIC_INIT(0);

		if (atomic_inc_return(&n_calls) <= 64)
			pr_err("bpf: %s[%d]: bpf(cmd %d, size %u) = %d\\n", current->comm,
			       task_pid_nr(current), cmd, size, ret);
	}
	return ret;
}
""")
t = t.replace(old_case, "\tcase BPF_PROG_LOAD:\n\t\terr = bpf_prog_load(&attr, uattr, size);\n"
              "\t\tif (err < 0)\n\t\t\top13_bpf_report(&attr, uattr, err);\t/* op13: failed program loads */\n"
              "\t\tbreak;\n")
open(p, "w").write(t)
PY
	grep -q 'op13: failed program loads' $BS || { echo "bpf load report: edit failed"; exit 1; }
	echo "bpf load report added"
fi

# android_vh_thermal_pm_notify_suspend, as android16-6.12 has it: a vendor module may keep a thermal
# zone running through suspend (its interrupt wakes the phone). Qualcomm's qti_thermal_vendor_hooks
# (stock first stage) marks every zone so; without the hook it does not build (the SoC build
# dropped it) and every zone stops at suspend.
TH=$C/include/trace/hooks/thermal.h
TC=$C/drivers/thermal/thermal_core.c
VH=$C/drivers/android/vendor_hooks.c
if ! grep -q 'android_vh_thermal_pm_notify_suspend' $TH; then
	python3 -I - "$TH" "$TC" "$VH" <<'PY'
import sys
th, tc, vh = sys.argv[1:4]
def edit(path, old, new, n=1):
	t = open(path).read()
	assert t.count(old) == n, (path, old, t.count(old))
	open(path, "w").write(t.replace(old, new))
edit(th, "#include <trace/hooks/vendor_hooks.h>\n",
     "#include <trace/hooks/vendor_hooks.h>\n\nstruct thermal_zone_device;\n")
edit(th, "#endif /* _TRACE_HOOK_THERMAL_H */",
     "/* op13: as android16-6.12 (tools/prep_common.sh) */\n"
     "DECLARE_HOOK(android_vh_thermal_pm_notify_suspend,\n"
     "\tTP_PROTO(struct thermal_zone_device *tz, int *irq_wakeable),\n"
     "\tTP_ARGS(tz, irq_wakeable));\n\n#endif /* _TRACE_HOOK_THERMAL_H */")
edit(vh, "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_enable_thermal_genl_check);\n",
     "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_enable_thermal_genl_check);\n"
     "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_thermal_pm_notify_suspend);\t/* op13 */\n")
t = open(tc).read()
if "#include <trace/hooks/thermal.h>" not in t:
	# ahead of CREATE_TRACE_POINTS: vendor_hooks.c defines the hook, this file only calls it
	edit(tc, "#define CREATE_TRACE_POINTS\n#include \"thermal_trace.h\"\n",
	     "#include <trace/hooks/thermal.h>\t/* op13 */\n#define CREATE_TRACE_POINTS\n#include \"thermal_trace.h\"\n")
edit(tc, "\tlist_for_each_entry(tz, &thermal_tz_list, node)\n\t\tthermal_zone_pm_prepare(tz);\n",
     "\tlist_for_each_entry(tz, &thermal_tz_list, node) {\n\t\tint irq_wakeable = 0;\n\n"
     "\t\ttrace_android_vh_thermal_pm_notify_suspend(tz, &irq_wakeable);\t/* op13 */\n"
     "\t\tif (irq_wakeable)\n\t\t\tcontinue;\n\t\tthermal_zone_pm_prepare(tz);\n\t}\n")
edit(tc, "\tlist_for_each_entry(tz, &thermal_tz_list, node)\n\t\tthermal_zone_pm_complete(tz);\n",
     "\tlist_for_each_entry(tz, &thermal_tz_list, node) {\n\t\tint irq_wakeable = 0;\n\n"
     "\t\ttrace_android_vh_thermal_pm_notify_suspend(tz, &irq_wakeable);\t/* op13 */\n"
     "\t\tif (irq_wakeable)\n\t\t\tcontinue;\n\t\tthermal_zone_pm_complete(tz);\n\t}\n")
PY
	grep -q 'android_vh_thermal_pm_notify_suspend' $TH && grep -q 'trace_android_vh_thermal_pm_notify_suspend' $TC \
		|| { echo "thermal pm_notify_suspend hook: edit failed"; exit 1; }
	echo "android_vh_thermal_pm_notify_suspend added"
fi

# remoteproc auto-boot that finds no firmware tries again: here the SoC's remoteprocs (ADSP, CDSP) are
# loaded in the first stage, before /vendor/firmware_mnt is mounted and ueventd serves firmware --
# "Direct firmware load for adsp.mdt failed with error -2", and the 6.18 core never tries again: the
# DSPs stayed off and adsprpcd/cdsprpcd crashed in a loop (test-43, test-45). The stock kernel loads
# them in the second stage. Retried with rproc_boot() (the stock path), 2 s doubling to 16 s, about
# four minutes, while the processor is still offline.
RP=$C/drivers/remoteproc/remoteproc_core.c
if ! grep -q 'op13_rproc_retry' $RP; then
	python3 -I - "$RP" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
old = ("static void rproc_auto_boot_callback(const struct firmware *fw, void *context)\n{\n"
       "\tstruct rproc *rproc = context;\n\n\trproc_boot(rproc);\n\n\trelease_firmware(fw);\n}\n")
assert t.count(old) == 1, "remoteproc auto boot: anchor"
new = r'''/* op13: auto-boot again once the firmware is there (tools/prep_common.sh) */
struct op13_rproc_retry {
	struct delayed_work work;
	struct rproc *rproc;
	unsigned int delay;
	unsigned int waited;
};

static void op13_rproc_retry_fn(struct work_struct *work)
{
	struct op13_rproc_retry *r = container_of(to_delayed_work(work), struct op13_rproc_retry, work);
	struct rproc *rproc = r->rproc;

	if (rproc->state == RPROC_OFFLINE) {
		if (!rproc_boot(rproc)) {
			dev_info(&rproc->dev, "op13: %s booted after %u s\n", rproc->firmware, r->waited);
		} else if (r->waited < 240) {
			r->waited += r->delay;
			schedule_delayed_work(&r->work, r->delay * HZ);
			if (r->delay < 16)
				r->delay *= 2;
			return;
		} else {
			dev_warn(&rproc->dev, "op13: %s still not loadable after %u s\n", rproc->firmware, r->waited);
		}
	}
	put_device(&rproc->dev);
	kfree(r);
}

static void rproc_auto_boot_callback(const struct firmware *fw, void *context)
{
	struct rproc *rproc = context;

	if (!fw && rproc->state == RPROC_OFFLINE) {
		struct op13_rproc_retry *r = kzalloc(sizeof(*r), GFP_KERNEL);

		if (r) {
			dev_info(&rproc->dev, "op13: %s not there yet, auto-boot again later\n", rproc->firmware);
			r->rproc = rproc;
			r->delay = 2;
			r->waited = 2;
			get_device(&rproc->dev);
			INIT_DELAYED_WORK(&r->work, op13_rproc_retry_fn);
			schedule_delayed_work(&r->work, 2 * HZ);
			return;
		}
	}

	rproc_boot(rproc);

	release_firmware(fw);
}
'''
t = t.replace(old, new)
if "#include <linux/workqueue.h>" not in t:
	t = t.replace("#include <linux/slab.h>\n", "#include <linux/slab.h>\n#include <linux/workqueue.h>\n", 1)
open(p, "w").write(t)
PY
	grep -q 'op13_rproc_retry' $RP || { echo "remoteproc auto-boot retry: edit failed"; exit 1; }
	echo "remoteproc auto-boot retry added"
fi

# op13_deadline=<seconds>: a boot that has not started zygote by then logs where every blocked task
# waits and every CPU's stack, for the ramoops log a Power+VolUp reset keeps. Once zygote has run,
# it does nothing. It repeats every 60 s (5 more times), so the newest dump is at the ring's end.
PA=$C/kernel/panic.c
if ! grep -q 'op13_deadline' $PA; then
	cat >> $PA <<'EOF'

/* op13: see tools/prep_common.sh */
#include <linux/sched/signal.h>
#include <linux/sizes.h>
#include <linux/workqueue.h>

static unsigned int op13_deadline;
core_param(op13_deadline, op13_deadline, uint, 0444);

static bool op13_zygote_seen(void)
{
	struct task_struct *p;
	bool seen = false;

	rcu_read_lock();
	for_each_process(p) {
		if (!strncmp(p->comm, "zygote", 6)) {
			seen = true;
			break;
		}
	}
	rcu_read_unlock();
	return seen;
}

/* init and the module loaders, whatever they wait in (an interruptible wait is not in the D list) */
static void op13_show_boot_tasks(void)
{
	static const char * const names[] = { "init", "modprobe", "insmod", "k618", "ueventd" };
	struct task_struct *g, *t;
	unsigned int i;

	pr_emerg("op13: init and the module loaders:\n");
	rcu_read_lock();
	for_each_process_thread(g, t) {
		for (i = 0; i < ARRAY_SIZE(names); i++) {
			if (!strncmp(t->comm, names[i], strlen(names[i]))) {
				sched_show_task(t);
				break;
			}
		}
	}
	rcu_read_unlock();
}

/* this boot's kernel errors so far, again: pstore's console keeps the start of the boot (its
 * pinned part) and the end, and the middle -- where the drivers load and fail -- is overwritten
 * by then (test-65: why the display never came up was in no log). The kernel's log buffer still
 * has all of it. The kernel's own lines only (user space's /dev/kmsg lines are a facility of their
 * own), error level or worse, and warnings about symbols or probes (a module refused for a symbol,
 * a probe that failed); at most 64 KiB, the earliest first: the first failure is the one that
 * matters. Only what was logged before this dump, so no dump replays an earlier one. */
static void op13_replay_errors(void)
{
	static char line[1024];
	struct kmsg_dump_iter iter;
	unsigned int n = 0, over = 0;
	size_t len, out = 0;
	u64 stop;

	kmsg_dump_rewind(&iter);
	stop = iter.next_seq;
	pr_emerg("op13: this boot's kernel errors so far:\n");
	while (iter.cur_seq < stop && kmsg_dump_get_line(&iter, true, line, sizeof(line) - 1, &len)) {
		unsigned int prio = 0, i = 1;

		line[len] = '\0';
		if (line[0] != '<')
			continue;
		while (line[i] >= '0' && line[i] <= '9')
			prio = prio * 10 + (line[i++] - '0');
		if (prio > 7)		/* not the kernel's (LOG_KERN is facility 0) */
			continue;
		if (prio > 4 || (prio == 4 && !strstr(line, "symbol") && !strstr(line, "probe")
				 && !strstr(line, "fail") && !strstr(line, "error")))
			continue;
		if (strstr(line, "op13") || strstr(line, "blocked for more than"))
			continue;
		if (out + len > SZ_64K) {
			over++;
			continue;
		}
		out += len;
		n++;
		pr_emerg("op13| %s", line + i + 1);
	}
	pr_emerg("op13: %u error lines replayed, %u more past 64 KiB\n", n, over);
}

static unsigned int op13_deadline_runs;

static void op13_deadline_fn(struct work_struct *work)
{
	if (op13_zygote_seen()) {
		pr_emerg("op13: zygote is up, boot deadline off\n");
		return;
	}
	console_verbose();	/* the task dump is KERN_INFO: past the bootloader's loglevel */
	pr_emerg("op13: no zygote %u s into the boot: its errors, blocked tasks and CPUs follow\n",
		 op13_deadline + 60 * op13_deadline_runs);
	op13_replay_errors();
	show_state_filter(TASK_UNINTERRUPTIBLE);
	op13_show_boot_tasks();
	trigger_all_cpu_backtrace();
	/* no panic: it stops the CPUs, the unfed watchdog then resets the phone and the log is lost
	 * (test-24); left running, a Power+VolUp reset keeps it (test-21) */
	pr_emerg("op13: boot deadline (%u s) reached: reset with Power+VolUp\n", op13_deadline);
	/* again every 60 s while there is no zygote: the newest dump stays at the end of pstore's
	 * ring, the part a full ring still has (test-55: its lines were overwritten) */
	if (++op13_deadline_runs < 6)
		queue_delayed_work(system_highpri_wq, to_delayed_work(work), 60 * HZ);
}
static DECLARE_DELAYED_WORK(op13_deadline_work, op13_deadline_fn);

static int __init op13_deadline_init(void)
{
	if (op13_deadline)
		queue_delayed_work(system_highpri_wq, &op13_deadline_work, op13_deadline * HZ);
	return 0;
}
late_initcall(op13_deadline_init);
EOF
	grep -q 'late_initcall(op13_deadline_init)' $PA || { echo "boot deadline: edit failed"; exit 1; }
	echo "op13_deadline= boot deadline added"
fi

# op13_hold_reboot=1: a restart is held -- logged, and the task that asked sleeps for good -- instead of done. The
# phone's own restart is a cold one that loses the RAM log, a Power+VolUp reset keeps it (test-15),
# and a bring-up boot that gives up restarts on its own (test-23: into recovery after ~15 s): held,
# its log is still in ramoops after the button reset. What userspace asked for (a recovery boot, its
# reason in misc) is already written by then and still happens.
RB=$C/kernel/reboot.c
if ! grep -q 'op13_hold_reboot' $RB; then
	python3 -I - "$RB" <<'PY'
import sys
p = sys.argv[1]
s = open(p).read()
hold = '''/* op13: see tools/prep_common.sh */
#include <linux/moduleparam.h>
#include <linux/sched.h>
static bool op13_hold_reboot;
core_param(op13_hold_reboot, op13_hold_reboot, bool, 0644);

static void op13_hold(const char *cmd)
{
	pr_emerg("op13: restart%s%s held (op13_hold_reboot): reset with Power+VolUp\\n",
		 cmd ? " with command " : "", cmd ? cmd : "");
	dump_stack();
	/* the caller sleeps, the rest keeps running: halted CPUs leave the watchdog unfed, and its
	 * reset loses the log as the phone's own restart does (test-24). TASK_IDLE: no wakeup by a
	 * signal (init gets SIGCHLD), not a hung task either */
	for (;;)
		schedule_timeout_idle(60 * HZ);
}

'''
head = "void kernel_restart(char *cmd)\n{\n"
assert s.count(head) == 1, "kernel_restart"
s = s.replace(head, hold + head + "\tif (op13_hold_reboot)\n\t\top13_hold(cmd);\n", 1)
open(p, "w").write(s)
PY
	grep -q 'op13_hold(cmd);' $RB || { echo "hold reboot: edit failed"; exit 1; }
	echo "op13_hold_reboot= added"
fi

# android_rvh_commit_creds, as android15-6.6 has it (include/trace/hooks/creds.h; 6.18's ACK dropped
# it): OnePlus' schedinfo (oplus_bsp_schedinfo, osi_amu.c) follows a task's uid through it
CH=$C/include/trace/hooks/creds.h
if [ ! -f $CH ]; then
	cat > $CH <<'EOT'
/* SPDX-License-Identifier: GPL-2.0 */
/* op13: as android15-6.6 has it (tools/prep_common.sh) */
#undef TRACE_SYSTEM
#define TRACE_SYSTEM creds
#undef TRACE_INCLUDE_PATH
#define TRACE_INCLUDE_PATH trace/hooks
#if !defined(_TRACE_HOOK_CREDS_H) || defined(TRACE_HEADER_MULTI_READ)
#define _TRACE_HOOK_CREDS_H
#include <trace/hooks/vendor_hooks.h>

struct cred;
struct task_struct;
DECLARE_RESTRICTED_HOOK(android_rvh_commit_creds,
	TP_PROTO(const struct task_struct *task, const struct cred *new),
	TP_ARGS(task, new), 1);

#endif /* _TRACE_HOOK_CREDS_H */
/* This part must be outside protection */
#include <trace/define_trace.h>
EOT
	python3 -I - "$C/kernel/cred.c" "$C/drivers/android/vendor_hooks.c" <<'PY'
import sys
cr, vh = sys.argv[1:3]
def edit(path, old, new):
	t = open(path).read()
	assert t.count(old) == 1, (path, old)
	open(path, "w").write(t.replace(old, new, 1))
edit(cr, "#include <linux/uidgid.h>\n", "#include <linux/uidgid.h>\n#include <trace/hooks/creds.h>\t/* op13 */\n")
edit(cr, "\trcu_assign_pointer(task->cred, new);\n",
     "\trcu_assign_pointer(task->cred, new);\n\ttrace_android_rvh_commit_creds(task, new);\t/* op13 */\n")
edit(vh, "#include <trace/hooks/user.h>\n", "#include <trace/hooks/user.h>\n#include <trace/hooks/creds.h>\t/* op13 */\n")
edit(vh, "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_free_user);\n",
     "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_free_user);\nEXPORT_TRACEPOINT_SYMBOL_GPL(android_rvh_commit_creds);\t/* op13 */\n")
PY
	grep -q 'trace_android_rvh_commit_creds' $C/kernel/cred.c && grep -q 'android_rvh_commit_creds' $C/drivers/android/vendor_hooks.c \
		|| { echo "creds hook: edit failed"; exit 1; }
	echo "android_rvh_commit_creds added"
fi

# of_iommu_get_resv_regions(): an "iommu-addresses" entry that does not translate (of_translate_dma_
# region() returns NULL, length left unset) ends that region's list instead of the next entry being
# read through the NULL pointer. test-46 (boot-full): the Wi-Fi chip's PCIe device (cnss2 ->
# msm_pcie_enumerate -> arm_smmu_get_resv_regions) oopsed there in the first stage ("Cannot reserve
# IOVA region of 0 size", then a NULL read), the probe worker died with its locks held and the
# first stage never finished.
OI=$C/drivers/iommu/of_iommu.c
if ! grep -q 'op13: an entry that does not translate' $OI; then
	python3 -I - "$OI" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
old = ("\t\t\t\tphys_addr_t iova;\n\t\t\t\tsize_t length;\n")
new = ("\t\t\t\tphys_addr_t iova;\n\t\t\t\tsize_t length = 0;\n")
assert t.count(old) == 1, "of_iommu length"
t = t.replace(old, new, 1)
old = ("\t\t\t\tmaps = of_translate_dma_region(np, maps, &iova, &length);\n"
       "\t\t\t\tif (length == 0) {\n")
new = ("\t\t\t\tmaps = of_translate_dma_region(np, maps, &iova, &length);\n"
       "\t\t\t\tif (!maps) {\t/* op13: an entry that does not translate ends the list */\n"
       "\t\t\t\t\tdev_warn(dev, \"cannot translate IOVA region in %pOF\\n\", it.node);\n"
       "\t\t\t\t\tbreak;\n\t\t\t\t}\n"
       "\t\t\t\tif (length == 0) {\n")
assert t.count(old) == 1, "of_iommu translate"
t = t.replace(old, new, 1)
open(p, "w").write(t)
PY
	grep -q 'op13: an entry that does not translate' $OI || { echo "of_iommu: edit failed"; exit 1; }
	echo "of_iommu_get_resv_regions: untranslatable entries end the list"
fi

# of_match_bus(): a node without "#address-cells" of its own matched no bus in 6.18 (6.6's "default"
# bus matched every node, with the cells inherited from its parents), so every address below it
# was untranslatable. The OnePlus 13's DT has such nodes: the PCIe root ports (pcie0_rp: reg and
# phandle only) between the Wi-Fi chip (cnss_pci0) and its controller, so the chip's
# "iommu-addresses" did not translate -- the kernel set no reserved IOVA regions for it, and cnss2's
# cnss_pci_get_iommu_addr() looped on the NULL for good in the first stage (test-55: boot-full
# stuck on the logo, CPU 6 in of_translate_dma_region). Such a node gets 6.6's bus again: the
# default one with a flags cell when it inherits 3 address cells, the plain default one otherwise.
OA=$C/drivers/of/address.c
if ! grep -q 'op13: a node without #address-cells' $OA; then
	python3 -I - "$OA" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
old = ("\tfor (i = 0; i < ARRAY_SIZE(of_busses); i++)\n"
       "\t\tif (!of_busses[i].match || of_busses[i].match(np))\n"
       "\t\t\treturn &of_busses[i];\n"
       "\treturn NULL;\n}\n")
new = ("\tfor (i = 0; i < ARRAY_SIZE(of_busses); i++)\n"
       "\t\tif (!of_busses[i].match || of_busses[i].match(np))\n"
       "\t\t\treturn &of_busses[i];\n"
       "\t/* op13: a node without #address-cells inherits its parents' cells, as in 6.6\n"
       "\t * (tools/prep_common.sh): default-flags for 3, default otherwise */\n"
       "\tif (np && !of_property_present(np, \"#address-cells\"))\n"
       "\t\treturn &of_busses[ARRAY_SIZE(of_busses) - (of_bus_n_addr_cells(np) == 3 ? 2 : 1)];\n"
       "\treturn NULL;\n}\n")
assert t.count(old) == 1, "of_match_bus"
t = t.replace(old, new, 1)
assert t.count('.name = "default-flags"') == 1 and t.index('.name = "default-flags"') < t.index('.name = "default",')
open(p, "w").write(t)
PY
	grep -q 'op13: a node without #address-cells' $OA || { echo "of_match_bus: edit failed"; exit 1; }
	echo "of_match_bus: nodes without #address-cells inherit their parents' cells again"
fi

# A NAPI context that was never added (napi->dev NULL) is deleted or disabled as in 6.6: nothing to
# do. 6.18 takes the device's instance lock first (netdev_lock(napi->dev)), so the same call is a
# NULL dereference. IPA deletes the TX NAPI of every producer pipe it tears down, added or not: the
# modem's shutdown at `adb reboot` (ipa3_wwan_remove -> ipa_teardown_sys_pipe -> netif_napi_del)
# oopsed on dev->lock at 0xa70 and froze the phone (log21).
ND=$C/include/linux/netdevice.h
DV=$C/net/core/dev.c
if ! grep -q 'op13: a NAPI never added' $ND; then
	python3 -I - "$ND" "$DV" <<'PY'
import sys
nd, dv = sys.argv[1], sys.argv[2]
t = open(nd).read()
old = ("static inline void __netif_napi_del(struct napi_struct *napi)\n{\n"
       "\tnetdev_lock(napi->dev);\n")
new = ("static inline void __netif_napi_del(struct napi_struct *napi)\n{\n"
       "\tif (unlikely(!napi->dev))\t/* op13: a NAPI never added, as in 6.6 (tools/prep_common.sh) */\n"
       "\t\treturn;\n"
       "\tnetdev_lock(napi->dev);\n")
assert t.count(old) == 1, "__netif_napi_del"
open(nd, "w").write(t.replace(old, new, 1))
t = open(dv).read()
old = ("void napi_disable(struct napi_struct *n)\n{\n"
       "\tnetdev_lock(n->dev);\n")
new = ("void napi_disable(struct napi_struct *n)\n{\n"
       "\tif (unlikely(!n->dev))\t/* op13: a NAPI never added, as in 6.6 (tools/prep_common.sh) */\n"
       "\t\treturn;\n"
       "\tnetdev_lock(n->dev);\n")
assert t.count(old) == 1, "napi_disable"
open(dv, "w").write(t.replace(old, new, 1))
PY
	grep -q 'op13: a NAPI never added' $ND && grep -q 'op13: a NAPI never added' $DV \
		|| { echo "napi never added: edit failed"; exit 1; }
	echo "napi: a NAPI never added is deleted/disabled as in 6.6"
fi

# The governor "walt" is schedutil here. The stock vendor_boot command line boots with
# cpufreq.default_governor=performance, and the vendor's post-boot script switches every policy to
# "walt" (Qualcomm's WALT, which this kernel does not build). That write failed, so every core
# stayed at its highest frequency for good: the phone ran warm doing nothing (test-58). A write
# of "walt" now gets the scheduler-driven governor the kernel has.
CF=$C/drivers/cpufreq/cpufreq.c
if ! grep -q 'op13: "walt" is schedutil' $CF; then
	python3 -I - "$CF" <<'PY'
import sys
p = sys.argv[1]
t = open(p).read()
old = ("\tif (request_module(\"cpufreq_%s\", str_governor))\n\t\treturn NULL;\n\n"
       "\treturn get_governor(str_governor);\n}\n")
new = ("\tif (!request_module(\"cpufreq_%s\", str_governor)) {\n"
       "\t\tt = get_governor(str_governor);\n\t\tif (t)\n\t\t\treturn t;\n\t}\n\n"
       "\t/* op13: \"walt\" is schedutil -- WALT is not built, and the vendor's post-boot\n"
       "\t * script asks for it to leave the boot-time \"performance\" (tools/prep_common.sh) */\n"
       "\tif (!strcasecmp(str_governor, \"walt\")) {\n"
       "\t\tpr_info_once(\"cpufreq: governor walt requested, using schedutil\\n\");\n"
       "\t\treturn get_governor(\"schedutil\");\n\t}\n"
       "\treturn NULL;\n}\n")
assert t.count(old) == 1, "cpufreq_parse_governor"
open(p, "w").write(t.replace(old, new, 1))
PY
	grep -q 'op13: "walt" is schedutil' $CF || { echo "cpufreq walt: edit failed"; exit 1; }
	echo "cpufreq: governor walt -> schedutil"
fi

# a 4096-byte command line: the bootloader appends its own (msm_drm.dsi_display0=<panel>,
# oplus_bsp_tp_custom.dsi_display0=<panel> ...) after the boot image's, and with ours the whole was
# cut at 2048 (test-46: 2045 bytes kept, oplus_bsp_tp_custom.dsi_display0 gone -- the touch driver
# found no panel, "commandline not match", and did not probe)
CL=$C/arch/arm64/include/uapi/asm/setup.h
if ! grep -q 'COMMAND_LINE_SIZE	4096' $CL; then
	sed -i 's/^#define COMMAND_LINE_SIZE\t2048$/#define COMMAND_LINE_SIZE\t4096\t\/* op13: the bootloader appends after a long one *\//' $CL
	grep -q 'COMMAND_LINE_SIZE	4096' $CL || { echo "command line size: edit failed"; exit 1; }
	echo "command line size 4096"
fi

# KernelSU built in: BakaSU (src/KernelSU, scripts/fetch.sh pins it) as drivers/kernelsu, wired the
# way its kernel/setup.sh does (a link, its Makefile line, its Kconfig in the drivers menu).
# Tracepoint syscall hooks (its default for GKI 2.0 kernels), so no kernel source hooks; the
# option itself in tools/config_gki.sh. The link keeps BakaSU's own git tree, which its Kbuild
# reads its version from.
KSU_SRC=$ROOT/src/KernelSU/kernel
[ -f "$KSU_SRC/Kbuild" ] || { echo "KernelSU: $KSU_SRC missing (scripts/fetch.sh)"; exit 1; }
ln -sfn ../../KernelSU/kernel "$C/drivers/kernelsu"
[ -f "$C/drivers/kernelsu/Kbuild" ] || { echo "KernelSU: drivers/kernelsu link broken"; exit 1; }
grep -q 'kernelsu/' "$C/drivers/Makefile" || printf '\nobj-$(CONFIG_KSU) += kernelsu/\n' >> "$C/drivers/Makefile"
grep -q 'source "drivers/kernelsu/Kconfig"' "$C/drivers/Kconfig" \
	|| sed -i '/^endmenu/i source "drivers/kernelsu/Kconfig"' "$C/drivers/Kconfig"
[ "$(grep -c 'source "drivers/kernelsu/Kconfig"' "$C/drivers/Kconfig")" = 1 ] \
	|| { echo "KernelSU: Kconfig edit failed"; exit 1; }
echo "KernelSU: BakaSU $(git -C "$ROOT/src/KernelSU" rev-parse --short HEAD) as drivers/kernelsu"

# built-in initramfs (our module layer) unpacked after the bootloader's ramdisks, so it wins
python3 -I - "$REF" "$C" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import k618_ports as p
for name, edits in p.CHANGES:
    if name == 'initramfs_last':
        print('initramfs_last:', p.apply_change(sys.argv[2], name, edits))
PY
