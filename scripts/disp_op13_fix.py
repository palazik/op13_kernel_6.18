#!/usr/bin/env python3
"""disp_op13_fix.py <display-drivers>: the OnePlus 13's own display driver (its SM8750 release,
Qualcomm's display techpack with OnePlus' display extension, oplus/SM8750) made to build on 6.18
as msm_drm, with the extension in it. scripts/build_display.sh runs it on build_vendor.sh's fixed
copy of the OnePlus 13's tree (the generic 6.6 -> 6.18 sweeps are applied there already).

1. The kernel API changes of 6.7 .. 6.13 this 6.6-era release meets, ported from Qualcomm's own
   handling of them in the OnePlus 15's release of the same driver (its 6.12 tree): the DRM fields
   that went away (output_poll_changed, lastclose -- now run from postclose on the last close, as
   there --, date), the file operations' FOP_UNSIGNED_OFFSET (6.12: drm_open() refuses a device
   without it), iommu_present() (gone in 6.13: whether a platform device sits behind an IOMMU,
   as there), the DP MST payload helper's 6.10 arguments, of_property_for_each_u32()'s 6.11 ones,
   vmap()'s header, and the void platform remove() the sweeps gave its definition but not its
   prototype.
2. OnePlus' extension in the module, as OnePlus builds it (display_modules.bzl: the sources of the
   options targets/sun.bzl turns on; their includes, BUILD.bazel's oplus_drm_headers). Qualcomm's
   Kbuild leaves it out; config/gki_sundispconf.h defines the options except OPLUS_FEATURE_APDMR.

Idempotent; a change whose text is missing is an error (the tree it was written for changed)."""

import os
import re
import sys

IOMMU_DEF = """
/* op13: iommu_present() is gone (6.13): whether a platform device sits behind an IOMMU, as the
 * OnePlus 15's release of this driver asks it (scripts/disp_op13_fix.py) */
static int k618_iommu_mapped(struct device *dev, void *data)
{
	if (device_iommu_mapped(dev)) {
		*(bool *)data = true;
		return 1;
	}
	return 0;
}

bool k618_iommu_present(void)
{
	static int known;	/* 0: not asked yet, 1: present, -1: not */
	bool present = false;

	if (!known) {
		bus_for_each_dev(&platform_bus_type, NULL, &present, k618_iommu_mapped);
		known = present ? 1 : -1;
	}
	return known > 0;
}
"""

EDITS = [
    # iommu_present()
    ("msm/msm_drv.h", "#endif /* __MSM_DRV_H__ */",
     "bool k618_iommu_present(void);\t/* op13 (scripts/disp_op13_fix.py) */\n\n#endif /* __MSM_DRV_H__ */"),
    ("msm/msm_drv.c", "static void msm_postclose(struct drm_device *dev, struct drm_file *file)\n{",
     IOMMU_DEF.lstrip("\n") + "\nstatic void msm_lastclose(struct drm_device *dev);\n\n"
     "static void msm_postclose(struct drm_device *dev, struct drm_file *file)\n{"),
    # lastclose: gone from struct drm_driver (6.12); Qualcomm runs it on the last file's postclose
    ("msm/msm_drv.c", "\tcontext_close(ctx);\n}\n",
     "\tcontext_close(ctx);\n\n"
     "\t/* op13: drm_driver.lastclose is gone (6.12): the last close, as Qualcomm's 6.12 release */\n"
     "\tif (atomic_read(&dev->open_count) == 1)\n\t\tmsm_lastclose(dev);\n}\n"),
    ("msm/msm_drv.c", "\t.lastclose          = msm_lastclose,\n", ""),
    ("msm/msm_drv.c", "\t.output_poll_changed = msm_fb_output_poll_changed,\n",
     "#if (KERNEL_VERSION(6, 12, 0) > LINUX_VERSION_CODE)\n"
     "\t.output_poll_changed = msm_fb_output_poll_changed,\n#endif\n"),
    ("msm/msm_drv.c", "\t.date               = \"20130625\",\n",
     "#if (KERNEL_VERSION(6, 13, 0) > LINUX_VERSION_CODE)\n"
     "\t.date               = \"20130625\",\n#endif\n"),
    # drm_open() refuses (-EINVAL, a WARN once) a DRM device whose file operations do not say their
    # offsets are unsigned (6.12: FOP_UNSIGNED_OFFSET instead of the FMODE flag drm_open set itself),
    # as Qualcomm's 6.12 release has it. Without it /dev/dri/card0 is there and no one can open it:
    # the composer's "Failed to acquire DRMMaster instance 0", no SurfaceFlinger (test-65)
    ("msm/msm_drv.c",
     "static const struct file_operations fops = {\n\t.owner              = THIS_MODULE,\n",
     "static const struct file_operations fops = {\n\t.owner              = THIS_MODULE,\n"
     "#if (KERNEL_VERSION(6, 12, 0) <= LINUX_VERSION_CODE)\n"
     "\t.fop_flags          = FOP_UNSIGNED_OFFSET,\n#endif\n"),
    # vmap()
    ("msm/msm_gem.c", "#include <linux/spinlock.h>\n",
     "#include <linux/spinlock.h>\n#include <linux/vmalloc.h>\n"),
    # of_property_for_each_u32(): no prop/cur (6.11)
    ("msm/sde_io_util.c",
     "\tof_property_for_each_u32(pdev->dev.of_node, \"qcom,pmic-arb-address\",\n\t\t\tprop, cur, val) {",
     "\tof_property_for_each_u32(pdev->dev.of_node, \"qcom,pmic-arb-address\", val) {"),
    # platform remove() is void (6.11): the prototype, as the sweeps made the definition
    ("msm/dsi/dsi_display.h", "int dsi_display_dev_remove(struct platform_device *pdev);",
     "void dsi_display_dev_remove(struct platform_device *pdev);"),
    # drm_dp_add_payload_part2() without the atomic state (6.10)
    ("msm/dp/dp_mst_drm.c",
     "\tint (*update_payload_part2)(struct drm_dp_mst_topology_mgr *mgr,\n"
     "\t\t\tstruct drm_atomic_state *state,\n"
     "\t\t\tstruct drm_dp_mst_atomic_payload *payload);\n",
     "#if (KERNEL_VERSION(6, 10, 0) <= LINUX_VERSION_CODE)\n"
     "\tint (*update_payload_part2)(struct drm_dp_mst_topology_mgr *mgr,\n"
     "\t\t\tstruct drm_dp_mst_atomic_payload *payload);\n#else\n"
     "\tint (*update_payload_part2)(struct drm_dp_mst_topology_mgr *mgr,\n"
     "\t\t\tstruct drm_atomic_state *state,\n"
     "\t\t\tstruct drm_dp_mst_atomic_payload *payload);\n#endif\n"),
    ("msm/dp/dp_mst_drm.c",
     "\tmst->mst_fw_cbs->update_payload_part2(&mst->mst_mgr, mst_state->base.state, payload);\n",
     "#if (KERNEL_VERSION(6, 10, 0) <= LINUX_VERSION_CODE)\n"
     "\tmst->mst_fw_cbs->update_payload_part2(&mst->mst_mgr, payload);\n#else\n"
     "\tmst->mst_fw_cbs->update_payload_part2(&mst->mst_mgr, mst_state->base.state, payload);\n#endif\n"),
    # the reference's tp_fix.py adds the eDP PLLs to the Kbuild for its tree's dp_pll.c, which calls
    # into them; the OnePlus 13's calls neither (nor does OnePlus build them: display_modules.bzl),
    # and has no edp_pll_4nm.c ("No rule to make target")
    ("msm/Kbuild",
     "\t\t\t\tdp/dp_pll_3nm.o \\\n\t\t\t\tdp/edp_pll_4nm.o \\\n\t\t\t\tdp/edp_pll_5nm.o\n",
     "\t\t\t\tdp/dp_pll_3nm.o\n# op13: no eDP PLLs here (scripts/disp_op13_fix.py)\n"),
]

# every call of iommu_present() on the platform bus
SWEEP = ("iommu_present(&platform_bus_type)", "k618_iommu_present()")

# the options targets/sun.bzl turns on that have sources (display_modules.bzl's config_srcs)
EXT_OPTIONS = ("OPLUS_FEATURE_DISPLAY", "OPLUS_FEATURE_DISPLAY_ADFR", "OPLUS_FEATURE_AP_UIR_DIMMING",
               "OPLUS_FEATURE_DISPLAY_ONSCREENFINGERPRINT", "OPLUS_FEATURE_TP_BASIC")
KBUILD_MARK = "# op13: OnePlus' display extension"


def kbuild(root):
    bzl = open(os.path.join(root, "display_modules.bzl"), encoding="utf-8").read()
    sun = open(os.path.join(root, "targets/sun.bzl"), encoding="utf-8").read()
    objs = []
    for opt in EXT_OPTIONS:
        if '"%s"' % opt not in sun:
            sys.exit("disp_op13_fix: targets/sun.bzl no longer turns on " + opt)
        m = re.search(r'"%s" : \[(.*?)\]' % opt, bzl, re.S)
        if not m:
            sys.exit("disp_op13_fix: display_modules.bzl has no sources for " + opt)
        objs += ["../" + s[:-2] + ".o" for s in re.findall(r'"([^"]+\.c)"', m.group(1))]
    path = os.path.join(root, "msm/Kbuild")
    text = open(path, encoding="utf-8").read()
    if KBUILD_MARK in text:
        return 0
    text += ("\n%s (scripts/disp_op13_fix.py): its sources as display_modules.bzl lists them\n"
             "ccflags-y += -I$(DISPLAY_ROOT)/oplus/SM8750/include -I$(DISPLAY_ROOT)/oplus/SM8750"
             " -DOPLUS_FEATURE_APDMR\nmsm_drm-y += %s\n" % (KBUILD_MARK, " ".join(objs)))
    open(path, "w", encoding="utf-8").write(text)
    return len(objs)


def main(argv):
    root = argv[1].rstrip("/")
    changed = bad = 0
    for rel, old, new in EDITS:
        path = os.path.join(root, rel)
        text = open(path, encoding="utf-8").read()
        if new and new in text or (not new and old not in text and "op13" in text):
            continue
        if text.count(old) != 1:
            print("disp_op13_fix: %s: text to change %s" % (rel, "not found" if old not in text else "not unique"))
            bad += 1
            continue
        open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
        changed += 1
    for dirpath, _dirs, files in os.walk(os.path.join(root, "msm")):
        for f in files:
            if not f.endswith(".c"):
                continue
            path = os.path.join(dirpath, f)
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            if SWEEP[0] in text:
                open(path, "w", encoding="utf-8", errors="surrogateescape").write(text.replace(*SWEEP))
                changed += 1
    n = kbuild(root)
    print("disp_op13_fix: %d changes, %d not found; OnePlus' extension: %s" %
          (changed, bad, "%d objects added" % n if n else "in the Kbuild already"))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
