# OnePlus 13 · Linux 6.18

An Android common kernel **6.18** port for the **OnePlus 13** (Snapdragon 8 Elite, **SM8750** / `sun`), with the vendor modules rebuilt for 6.18.

> **Status: boots to ColorOS** (test-56, 2026-11-25: the first 6.18 kernel to do so on the OnePlus 13; latest: test-66, 2026-11-28, with the OnePlus 13's own display driver and scheduler modules). Still a test kernel: only run it with `fastboot boot` (see below); nothing here flashes your phone.

| | |
|:--|:--|
| Device | OnePlus 13, SM8750 |
| Target ROM | ColorOS 16.0.10 CN (stock kernel `6.6.118-android15-8`) |
| Kernel | ACK `android17-6.18` at 6.18.32 (`ff4a6c3b`), built with LLVM 22 |
| Builds | [GitHub Actions](../../actions); test images on [Releases](../../releases) |

## What works

| | |
|:--|:--|
| Works | Boot to ColorOS, display and touch, GPU, adb, Wi-Fi, fingerprint (FOD), camera, flashlight and face unlock (test-58) |
| Display | The OnePlus 13's own display driver with OnePlus' display extension (`display-drivers/oplus/SM8750`, [`scripts/build_display.sh`](scripts/build_display.sh), [`scripts/disp_op13_fix.py`](scripts/disp_op13_fix.py)): `/dev/oplus_display` and `/sys/kernel/oplus_display` are there, brightness and AOD (full-screen AOD too) work (test-66). The OnePlus 15's driver, without the extension, is the build's fallback if it does not build |
| Known issues | Battery drains faster than on the stock kernel (being looked into) |
| Root | KernelSU built in: [BakaSU](https://github.com/Baka-SU/BakaSU), pinned in [`scripts/fetch.sh`](scripts/fetch.sh) (test-59) |
| Scheduler | WALT, Qualcomm's scheduler and its `walt` CPU governor ([`scripts/build_walt.sh`](scripts/build_walt.sh)), works (test-60), on the OnePlus 13's own scheduler modules (`sched_assist` and its group: built test-62, booting test-64 once ported to 6.18's delayed dequeue of sleeping tasks, [`scripts/oplus_fix.py`](scripts/oplus_fix.py)), with OnePlus' CPU close loop (test-61): only `boot-full` loads them; without them the vendor's switch to `walt` gets `schedutil` |
| Not built | OnePlus' zsmalloc fork and what uses it (hybridswap, zram tuning: its page-migration code predates 6.17's rework), GEAS (needs OnePlus' hooks in the SoC's memlat/bwmon), and modules OnePlus has not published |

Each release's notes say what changed and what is known to be broken.

## How it works

A 6.6 kernel's vendor modules cannot load on 6.18, so the port is mostly about the modules (712 of them, every versioned import checked against its provider at build time):

1. **Kernel** — Google's ACK 6.18 with the edits the vendor drivers need ([`tools/prep_common.sh`](tools/prep_common.sh): vendor hooks they call, behaviour of 6.6 they rely on, boot diagnostics) and `gki_defconfig` plus a few options ([`tools/config_gki.sh`](tools/config_gki.sh)).
2. **SoC drivers** — clocks, pinctrl, interconnects, RPMh, SMMU, UFS, USB, PCIe, … from OnePlus' SM8850 (OnePlus 15) kernel tree, which also carries the SM8750 `sun` target, with the Xiaomi 13 port's 6.12 → 6.18 adaptation ([`scripts/build_soc.sh`](scripts/build_soc.sh), [`configs/op13_overrides.bzl`](configs/op13_overrides.bzl)).
3. **Techpacks** — Qualcomm's multimedia and connectivity drivers built for `sun` ([`scripts/build_tp.sh`](scripts/build_tp.sh)): display, GPU, DSPs, audio, camera, video, Wi-Fi, data, secure world, … with this port's fixes for the OnePlus 13's device tree and user space ([`scripts/tp_op13_fix.py`](scripts/tp_op13_fix.py)).
4. **OnePlus drivers** — built from OnePlus' own Bazel definitions ([`scripts/build_vendor.sh`](scripts/build_vendor.sh)): the OnePlus 13's 6.6 sources first, the OnePlus 15's 6.12 sources for what does not build from them, with the 6.18 API changes applied ([`scripts/vendor_fix618.py`](scripts/vendor_fix618.py), [`scripts/oplus_fix.py`](scripts/oplus_fix.py)). The display driver is then rebuilt from the OnePlus 13's own release with OnePlus' display extension, and replaces the techpacks' one ([`scripts/build_display.sh`](scripts/build_display.sh)).
5. **Boot image** ([`scripts/build_bootimg.sh`](scripts/build_bootimg.sh)) — the modules and their load lists are built into the kernel as an initramfs unpacked *after* the stock ramdisks, so they replace the 6.6 modules without touching `vendor_boot`, `init_boot` or `vendor_dlkm`. That is what lets a single `fastboot boot` test the whole thing. First-stage init loads the phone's own first-stage list; the rest are loaded right after it, one child process each, by a batch loader (`k618_load`), which names a module whose load takes over 10 s.

The stock device tree (in `vendor_boot`) stays in use, so the drivers have to match OnePlus' SM8750 bindings.

## Building

Every push to `main` builds in GitHub Actions (LLVM 22, ccache; about 45 minutes with a warm cache). Locally, on an x86_64 or arm64 Linux host:

```bash
bash scripts/fetch.sh        # all sources at pinned revisions (src/, ref_8550/)
bash scripts/get_clang.sh    # Google's clang (x86_64 hosts; otherwise clang from PATH)
bash scripts/build_gki.sh    # kernel -> out/gki
bash scripts/build_soc.sh    # SoC modules for sun -> src/soc/**/*.ko
```

[`tools/remote_payload.py`](tools/remote_payload.py) pulls single partitions out of an OTA zip over HTTP without downloading the whole package (`list`, or `extract OUTDIR boot vendor_boot ...`).

## Root

KernelSU is part of the kernel (BakaSU, its tracepoint hooks; nothing to patch in `init_boot`). Install the BakaSU manager app from its [releases](https://github.com/Baka-SU/BakaSU/releases), matching the version the release notes name. A KernelSU module (LKM) built for the stock 6.6 kernel cannot load on 6.18; an `init_boot` patched with one is not needed for root here (it still roots the stock kernel).

## Testing

Download from [Releases](../../releases), then:

```bash
adb reboot bootloader
fastboot boot boot-full-testNN.img
```

`fastboot boot` runs the image once from RAM; the next reboot is your installed kernel again. **Never `fastboot flash` these images.**

Releases carry `boot-full-testNN.img`: 6.18 with every driver loaded. Each CI run also makes two images for narrowing problems down (in its artifacts, not released):

| Image | What it is |
|:--|:--|
| `boot.img` | The same modules, but the drivers new to the port (audio, camera, Wi-Fi, charger, …) are held back, not loaded. It stops at the boot animation (no sound card). |
| `diag-nolayer.img` | The bare 6.18 kernel without our modules: it reaches first-stage init and reboots on its own after ~15 s. |

### When it stops

A boot that has not started zygote after 60 s logs where every blocked task and every CPU is, and does so again every 60 s (`op13_deadline=60`). Neither a panic nor a restart that Android asks for (`op13_hold_reboot=1`) restarts the phone: it stays on the screen it was on, and **you reset it with Power+VolUp** (10–15 s) — the phone's own restart would lose the log, the button reset keeps it. Wait at least 70 s before the reset if it hangs while booting.

6.18's kernel log is kept in the same RAM area as the stock kernel's ([`tools/prep_common.sh`](tools/prep_common.sh) puts its ramoops at `0x880000000`). Read it on the stock system (needs root there), before rebooting again:

```bash
adb shell su -c 'cat /sys/fs/pstore/console-ramoops*' > log.txt
```

The log is a 256 KB ring (the stock kernel's layout, so the stock kernel can read it): its first 96 KB keep the start of the boot, the rest the newest lines. Look for `op13:` lines (the deadline dump) and `k618_load:` lines (the batch loader). If adb works on 6.18, `adb logcat -b all` too.

## Credits

- **[JiaqiLi404/android_kernel_618_for_sm8550](https://github.com/JiaqiLi404/android_kernel_618_for_sm8550)** — the Xiaomi 13 Pro (SM8550) 6.18 port this project is based on: its 6.12 → 6.18 SoC integration, the vendor-module build approach, the GKI edits and the boot-image method are reused here.
- **[OnePlusOSS](https://github.com/OnePlusOSS)** — [android_kernel_oneplus_sm8850](https://github.com/OnePlusOSS/android_kernel_oneplus_sm8850), [android_kernel_modules_and_devicetree_oneplus_sm8850](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8850) and [android_kernel_modules_and_devicetree_oneplus_sm8750](https://github.com/OnePlusOSS/android_kernel_modules_and_devicetree_oneplus_sm8750).
- **[LineageOS/android_kernel_oneplus_sm8650-modules](https://github.com/LineageOS/android_kernel_oneplus_sm8650-modules)** — OnePlus' published display extension for the OnePlus 12, read for how the composer's brightness property is meant to work.
- **[Baka-SU/BakaSU](https://github.com/Baka-SU/BakaSU)** — the KernelSU built into the kernel.
- **Qualcomm** — the SoC and techpack drivers in those trees.
- **Google** — [Android Common Kernel](https://android.googlesource.com/kernel/common), [clang prebuilts](https://android.googlesource.com/platform/prebuilts/clang/host/linux-x86) and [mkbootimg](https://android.googlesource.com/platform/system/tools/mkbootimg).
- **[palazik/android_kernel_common_oneplus_sm8750](https://github.com/palazik/android_kernel_common_oneplus_sm8750)** — the OnePlus 13 6.6 kernel this port starts from.

## License

GPL-2.0, as the kernel sources it builds — see [LICENSE](LICENSE).
