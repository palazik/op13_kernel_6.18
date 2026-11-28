#!/usr/bin/env python3
"""mklists.py <rd lib/modules dir> <stock dir> <report dir> [new list [hold]]: the module lists of our layer.

  modules.load (+ .recovery, .charger)  first-stage init's list: the phone's own first-stage
                                        order, only the modules we have, then k618_load last
  k618_batch.load                       what k618_load loads, one child process each: the phone's
                                        second stage (vendor_dlkm, then system_dlkm) that we have,
                                        in dependency order -- after switch_root our ramdisk is gone
                                        and the ROM's own second stage only has the 6.6 modules
  modules.softdep                       depmod's, plus k618_load after every first-stage module:
                                        this phone loads its first stage in parallel
                                        (androidboot.load_modules_parallel), where only
                                        dependencies and pre-softdeps order anything
  modules.options                       k618_load's parameters
  modules.blocklist                     the phone's

A module listed but missing ends first-stage init, so every list names only modules in the layer.
EARLY modules go to the front of the first stage whatever list the phone has them in: the RAM
console (pstore) the phone keeps across a reboot is set up by qcom_dynamic_ramoops, which the
stock ROM loads only in its second stage -- a bring-up boot that dies in the first stage would
leave nothing to read. pstore's console replays the log buffer when it registers.
"""

import os
import shutil
import sys


EARLY = ["qcom_dynamic_ramoops"]
# LATE modules go to the end of the batch, whatever list the phone has them in: drivers new to this
# port (scripts/build_extra.sh) that are not needed to boot -- one that hangs or oopses at load then
# holds nothing else up. Their probes also run asynchronously (driver_async_probe= in the cmdline).
LATE = ["oplus_bsp_synaptics_tcm2"]
# ... but not async_probe=1 for these: it makes every driver of the module probe in the background,
# and cnss2 registers its PCI driver inside its own (already asynchronous) platform probe and, when
# no chip probes, unregisters it there: driver_detach() then waits for all background probes, its
# own included, for good (test-55: the first stage stuck, a kworker in cnss_pci_init ->
# pci_unregister_driver -> async_synchronize_full). Stock loads it without the option.
SYNC = {"cnss2"}
# AFTER: a module loaded after another, whatever list the phone has either in. The kernel's zram and
# OnePlus' hybridswap zram (its own zram driver) claim the same CPU hotplug state and zram-control
# class: the first to load is the phone's zram, the other one's init gives up. The phone has
# hybridswap's first (vendor_dlkm before system_dlkm); in our batch it is a new driver, loaded late,
# so the kernel's would win and leave ColorOS without hybridswap. After it, the kernel's zram is
# there only if hybridswap's did not load.
AFTER = {"zram": "oplus_bsp_hybridswap_zram"}
# the new list (optional, one module name a line): every driver scripts/build_vendor.sh brought over
# from OnePlus' 6.6 sources. They are LATE too, also those the phone loads in its first stage (they
# move to the end of the batch), and each gets async_probe=1 in modules.options: its probes run in
# the background and loading it does not wait for them (test-31: a probe that oopsed while the load
# waited stopped the boot). With "hold" after it, they are in no list at all: in the layer for
# modprobe, not loaded (boot.img; boot-full.img loads them).


def canon(name):
    return os.path.basename(name.strip()).removesuffix(".ko").replace("-", "_")


def read_list(path):
    return [line.strip() for line in open(path) if line.strip() and not line.startswith("#")]


def main(argv):
    rd, stock, report = argv[1], argv[2], argv[3]
    new = set()
    if len(argv) > 4 and os.path.isfile(argv[4]):
        new = {canon(line) for line in open(argv[4]) if line.strip()}
    hold = len(argv) > 5 and argv[5] == "hold"
    # device/op13/bringup-hold.txt: new drivers no image loads yet (OnePlus' failure-recovery ones):
    # held in boot-full.img too
    bh = os.path.join(os.path.dirname(stock.rstrip("/")), "bringup-hold.txt")
    bringup = {canon(l) for l in open(bh) if l.strip() and not l.startswith("#")} if os.path.isfile(bh) else set()
    held_full = (new & bringup) if not hold else set()
    late_names = set(LATE) | new
    have = {canon(f): f for f in os.listdir(rd) if f.endswith(".ko")}
    blocked = {canon(line.split()[1]) for line in read_list(f"{stock}/modules.blocklist")
               if line.startswith("blocklist ")}
    deps = {}
    for line in open(f"{rd}/modules.dep"):
        m, _, d = line.strip().partition(":")
        deps[os.path.basename(m)] = [os.path.basename(x) for x in d.split()]

    first, missing_first = [], []
    for n in read_list(f"{stock}/first_stage.load"):
        k = canon(n)
        (first if k in have else missing_first).append(have.get(k, n))
    moved = [m for m in first if canon(m) in new]
    first = [m for m in first if canon(m) != "k618_load" and canon(m) not in new]
    early = [have[k] for k in EARLY if k in have]
    first = early + [m for m in first if m not in early]

    # everything init loads for the first stage, dependencies included
    in_init, todo = set(), list(first)
    while todo:
        m = todo.pop()
        if m not in in_init:
            in_init.add(m)
            todo += deps.get(m, [])

    # new drivers the phone has in its first stage: to the batch (unless a first-stage one needs them)
    # stock modules with no public source, skipped by decision (device/op13/no-source.txt)
    nosrc_file = os.path.join(os.path.dirname(stock.rstrip("/")), "no-source.txt")
    nosrc = {canon(l) for l in open(nosrc_file) if l.strip() and not l.startswith("#")} \
        if os.path.isfile(nosrc_file) else set()
    wanted, missing_second = [m for m in moved if m not in in_init and not hold
                              and canon(m) not in held_full], []
    for lst in ("vendor_dlkm.load", "system_dlkm.load"):
        for n in read_list(f"{stock}/{lst}"):
            k = canon(n)
            if k not in have:
                if k not in nosrc:
                    missing_second.append(os.path.basename(n))
            elif (hold and k in new) or k in held_full:
                continue
            elif k not in blocked and have[k] not in in_init and have[k] not in wanted:
                wanted.append(have[k])
    # modules no stock list names stay in the layer for modprobe on demand, never loaded blindly:
    # some are self-tests (torture, KUnit) that must not run on a phone
    extra = sorted(f for k, f in have.items() if f not in in_init and f not in wanted
                   and k not in blocked and k != "k618_load" and not (hold and k in new)
                   and k not in held_full)
    ordered, placed = [], set()

    def place(m, path=()):
        if m in placed or m in in_init or m in path:
            return
        for d in deps.get(m, []):
            place(d, path + (m,))
        placed.add(m)
        ordered.append(m)
    late = [m for m in wanted if canon(m) in late_names]
    for m in wanted:
        if m not in late:
            place(m)
    for m in late:
        place(m)
    for name, before in AFTER.items():
        pos = {canon(m): i for i, m in enumerate(ordered)}
        if name in pos and before in pos and pos[name] < pos[before]:
            m = ordered.pop(pos[name])
            # nothing in between may need it (it would load before its dependency)
            if any(m in deps.get(x, []) for x in ordered[pos[name]:pos[before]]):
                ordered.insert(pos[name], m)
            else:
                ordered.insert(pos[before], m)	# right after it (one place up since the pop)

    init_list = first + ["k618_load.ko"]
    for name in ("modules.load", "modules.load.recovery", "modules.load.charger"):
        open(f"{rd}/{name}", "w").write("".join(m + "\n" for m in init_list))
    open(f"{rd}/k618_batch.load", "w").write("".join(m + "\n" for m in ordered))
    with open(f"{rd}/modules.softdep", "a") as f:
        f.write("# op13: the batch loader after the whole first stage\n")
        f.write("softdep k618_load pre: " + " ".join(canon(m) for m in first) + "\n")
    open(f"{rd}/modules.options", "w").write(
        "options k618_load list=/lib/modules/k618_batch.load dir=/lib/modules helper=/k618_umh\n"
        + "".join(f"options {canon(m)} async_probe=1\n" for m in late if canon(m) in new and canon(m) not in SYNC))
    shutil.copy(f"{stock}/modules.blocklist", f"{rd}/modules.blocklist")

    os.makedirs(report, exist_ok=True)
    open(f"{report}/first-stage-missing.txt", "w").write("".join(m + "\n" for m in missing_first))
    open(f"{report}/second-stage-missing.txt", "w").write("".join(m + "\n" for m in missing_second))
    print(f"mklists: first stage {len(first)} of the phone's {len(first) + len(missing_first)} "
          f"(+ k618_load); batch {len(ordered)}; {len(extra)} on demand only; "
          f"second-stage modules we lack: {len(missing_second)}; new drivers at the end: "
          f"{len([m for m in late if canon(m) in new])}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
