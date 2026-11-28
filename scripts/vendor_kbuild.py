#!/usr/bin/env python3
"""vendor_kbuild.py: Kbuild files and a build order for OnePlus' modules, from bzl_eval.py's record.

usage: vendor_kbuild.py <modules.json> <tree> <wanted lists> <have dirs> <plan out>
  tree: the build copy of the source root (holds vendor/oplus/...); each module gets
        <package>/Kbuild.<name> there, and the package's Kbuild includes the one make asks for
        (OPMOD=<name>), so modules of one package share the sources but each builds with its own
        flags into its own output directory (make MO=)
  wanted lists: stock module lists (colon-separated); a module is built when the stock phone loads
        it, or when one that is built needs it
  have dirs: directories of modules built already (colon-separated): those are not built again,
        their Module.symvers serve the ones that need them
  plan out: one line per module, in dependency order: name|package|out base|needed names

As Kleaf's ddk_module: a module's include directories (includes, linux_includes) and those of the
header targets it uses apply to it and to every module that depends on it; local_defines and copts
to it alone; its defconfig's options are defined for it (CONFIG_X=1, CONFIG_X_MODULE=1, or the value).
"""
import json
import os
import re
import sys

MODS, TREE, WANT, HAVE, PLAN = sys.argv[1:6]
d = json.load(open(MODS))
mods = d["modules"]
headers = d["headers"]


def norm(n):
	n = os.path.basename(n)
	if n.endswith(".ko"):
		n = n[:-3]
	return n.replace("-", "_")


wanted = set()
for f in WANT.split(":"):
	if os.path.isfile(f):
		for line in open(f):
			line = line.split("#")[0].strip()
			if line:
				wanted.add(norm(line.split()[0]))
have = set()
for dd in HAVE.split(":"):
	if os.path.isdir(dd):
		have |= {norm(x) for x in os.listdir(dd) if x.endswith(".ko")}

# Bazel twins of modules the stock phone has under another name (OnePlus built those from the
# Makefiles next to the same sources): built, or taken as built, under the stock name, so no two
# modules export the same symbols
ALIAS = {"oplus_bsp_dft_olc": "olc", "oplus_bsp_dft_kernel_fb": "kernel_fb"}
for m in mods:
	if norm(m["out"]) in ALIAS:
		m["out"] = ALIAS[norm(m["out"])] + ".ko"

# OnePlus modules not built because the kernel has what they are for: out of the plan, and out of
# the dependencies of the modules that name them. oplus_bsp_zsmalloc is 6.6's mm/zsmalloc.c with its
# functions renamed; hybridswap's zram uses the kernel's own zsmalloc (scripts/oplus_fix.py)
REPLACED = {"oplus_bsp_zsmalloc"}
gone = {"%s:%s" % (m["package"], m["name"]) for m in mods if norm(m["out"]) in REPLACED}
mods = [m for m in mods if norm(m["out"]) not in REPLACED]
for m in mods:
	m["deps"] = [x for x in m["deps"] if x not in gone]
wanted -= REPLACED

by_label = {"%s:%s" % (m["package"], m["name"]): m for m in mods}
by_out = {}
for m in mods:
	by_out.setdefault(norm(m["out"]), m)


def exported_includes(m, seen=None):
	"""the include directories a module gives itself and its dependents (absolute, in TREE)."""
	seen = seen if seen is not None else set()
	key = "%s:%s" % (m["package"], m["name"])
	if key in seen:
		return []
	seen.add(key)
	out = [os.path.normpath(os.path.join(TREE, m["package"], i))
		for i in m["includes"] + m["linux_includes"]]
	for dep in m["deps"] + m["hdrs"]:
		if dep in headers:
			out += header_includes(dep, seen)
		elif dep in by_label:
			out += exported_includes(by_label[dep], seen)
	return out


def header_includes(label, seen):
	"""a header target's directories, and those of the header targets it gathers."""
	if label in seen or label not in headers:
		return []
	seen.add(label)
	h = headers[label]
	out = [os.path.join(TREE, i) for i in h["includes"] + h["linux_includes"]]
	for t in h.get("targets", []):
		out += header_includes(t, seen)
	return out


# what to build: the wanted ones (not built already), then what they need
todo, order, state = {}, [], {}


planned = set()		# output names in the plan: one module of a name, however many packages define it


def need(m):
	key = "%s:%s" % (m["package"], m["name"])
	if state.get(key) == 2:
		return
	if norm(m["out"]) in planned:
		# another package's module of the same name (oplus_audio_netlink: OnePlus' audio package
		# and Qualcomm's audio-kernel both define it) is in the plan already: building both had
		# modpost see every export twice
		state[key] = 2
		return
	if state.get(key) == 1:
		print("vendor_kbuild: dependency loop at %s" % key)
		return
	state[key] = 1
	for dep in m["deps"]:
		if dep in by_label and norm(by_label[dep]["out"]) not in have:
			need(by_label[dep])
	state[key] = 2
	planned.add(norm(m["out"]))
	order.append(m)


for name in sorted(wanted):
	if name in by_out and name not in have:
		need(by_out[name])

plan = open(PLAN, "w")
for m in order:
	base = norm(m["out"])
	pkgdir = os.path.join(TREE, m["package"])
	objs = []
	for s in m["srcs"]:
		if s.endswith(".c") or s.endswith(".S"):
			objs.append(s[:-2] + ".o")
	flags = ["-I$(src)"] + ["-I" + i for i in exported_includes(m)]
	for dfn in m["local_defines"]:
		# trace headers' directories handed in as defines, relative to the kernel's include/trace in
		# Qualcomm's layout (GSI_TRACE_INCLUDE_PATH=../../../../vendor/qcom/...): absolute, in TREE
		tm = re.match(r"(\w*TRACE_INCLUDE_PATH)=(.*)$", dfn)
		if tm and "vendor/" in tm.group(2):
			dfn = "%s=%s" % (tm.group(1), os.path.join(TREE, tm.group(2)[tm.group(2).index("vendor/"):]))
		flags.append("-D" + dfn)
	# a ddk_module sees the configuration of the modules it depends on, as Kleaf passes it down
	# (Wi-Fi gets dataipa's CONFIG_IPA3 through its dep on ipam; without it qcacld took IPA's stubs)
	opts, seen, todo = {}, set(), [x for x in m["deps"] if x in by_label]
	while todo:
		d = todo.pop()
		if d in seen:
			continue
		seen.add(d)
		for opt, val in by_label[d]["defconfig_options"]:
			opts.setdefault(opt, val)
		todo += [x for x in by_label[d]["deps"] if x in by_label]
	opts.update(dict(m["defconfig_options"]))
	for opt, val in opts.items():
		if val == "y":
			flags.append("-D%s=1" % opt)
		elif val == "m":
			flags.append("-D%s_MODULE=1" % opt)
		elif val not in ("n", ""):
			flags.append("-D%s=%s" % (opt, val if val.lstrip("-").isdigit() or val.startswith("0x")
				else '\\"%s\\"' % val))
	# Bazel's $(location ...) means nothing to make: such a copt goes, with the -include/-I before it
	# (camera's generated banner header: its source has defaults when it is not there)
	copts = [c for c in m["copts"] if isinstance(c, str)]
	for i, c in enumerate(copts):
		if "$(location" in c:
			copts[i] = None
			if i and copts[i - 1] in ("-include", "-I", "-isystem", "-iquote"):
				copts[i - 1] = None
	flags += [c for c in copts if c is not None]
	with open(os.path.join(pkgdir, "Kbuild.%s" % base), "w") as k:
		k.write("# from %s:%s (OnePlus' Bazel definition)\n" % (m["package"], m["name"]))
		k.write("obj-m += %s.o\n" % base)
		if objs != [base + ".o"]:
			k.write("%s-y := %s\n" % (base, " ".join(objs)))
		k.write("ccflags-y += %s\n" % " ".join(f.replace("$(src)", "$(src)") for f in flags))
	with open(os.path.join(pkgdir, "Kbuild"), "w") as k:
		k.write("# OPMOD=<module> picks the module (scripts/vendor_kbuild.py)\ninclude $(src)/Kbuild.$(OPMOD)\n")
	needs = [norm(by_label[x]["out"]) for x in m["deps"] if x in by_label]
	plan.write("%s|%s|%s|%s\n" % (base, m["package"], base, " ".join(needs)))
plan.close()
print("vendor_kbuild: %d modules to build (%d wanted by stock lists have a Bazel definition; %d built already)"
	% (len(order), len([n for n in wanted if n in by_out]), len([n for n in wanted if n in by_out and n in have])))
