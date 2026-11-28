#!/usr/bin/env python3
"""oplus_fix.py <dir>: 6.6 -> 6.18 fixes for OnePlus' own drivers (a copy under out/oplus), by file
name, wherever the file is in that copy. Idempotent."""

import os
import sys

# (file name, old, new)
EDITS = [
    # platform_driver.remove returns void since 6.11
    ("bootloader_log.c",
     "static int __exit bootloader_log_remove(struct platform_device *pdev)\n{\n\treturn -EBUSY;\n}",
     "static void bootloader_log_remove(struct platform_device *pdev)\n{\n}"),
    # the kernel's private procfs header by a path from OnePlus' tree layout (drivers/soc/oplus/...);
    # nothing of it is used (proc_create_data and pde_data are public)
    ("device_info.c", '#include "../../../../fs/proc/internal.h"\n', ""),
    # oplus_uprobe calls uprobe_register/uprobe_unregister through addresses it looks up by name. In
    # 6.12+ uprobe_register takes a ref_ctr_offset and returns the struct uprobe, which
    # uprobe_unregister_nosync() then takes, followed by uprobe_unregister_sync() before the consumer
    # may go: the old 3-argument calls would jump into them with the wrong arguments. Each
    # oplus_uprobe keeps its struct uprobe.
    ("oplus_uprobe.c",
     "\tstruct uprobe_consumer  consumer;\n",
     "\tstruct uprobe_consumer  consumer;\n\tstruct uprobe          *k618_up;\t/* 6.12+: what uprobe_register returned */\n"),
    ("oplus_uprobe.c",
     "typedef int (*uprobe_register_t)(struct inode *, loff_t, struct uprobe_consumer *);\n"
     "typedef void (*uprobe_unregister_t)(struct inode *, loff_t , struct uprobe_consumer *);\n",
     "typedef struct uprobe *(*uprobe_register_t)(struct inode *, loff_t, loff_t, struct uprobe_consumer *);\n"
     "typedef void (*uprobe_unregister_t)(struct uprobe *, struct uprobe_consumer *);\t/* _nosync */\n"),
    ("oplus_uprobe.c",
     "\tint ret = 0;\n\n\tret = uprobe_register_funcptr(inode, offset, consumer);\n\n\treturn ret;\n}\n",
     "\tstruct oplus_uprobe *ou = container_of(consumer, struct oplus_uprobe, consumer);\n"
     "\tstruct uprobe *up = uprobe_register_funcptr(inode, offset, 0, consumer);\n\n"
     "\tif (IS_ERR(up))\n\t\treturn PTR_ERR(up);\n\tou->k618_up = up;\n\treturn 0;\n}\n"),
    ("oplus_uprobe.c",
     "{\n\tuprobe_unregister_funcptr(inode, offset, consumer);\n}\n",
     "{\n\tstruct oplus_uprobe *ou = container_of(consumer, struct oplus_uprobe, consumer);\n"
     "\tstatic void (*k618_sync)(void);\n\n"
     "\tif (!ou->k618_up)\n\t\treturn;\n"
     "\tuprobe_unregister_funcptr(ou->k618_up, consumer);\n\tou->k618_up = NULL;\n"
     "\tif (!k618_sync) {\n\t\tstruct kprobe kp = { .symbol_name = \"uprobe_unregister_sync\" };\n\n"
     "\t\tif (!register_kprobe(&kp)) {\n\t\t\tk618_sync = (void (*)(void))kp.addr;\n\t\t\tunregister_kprobe(&kp);\n\t\t}\n\t}\n"
     "\tif (k618_sync)\n\t\tk618_sync();\n}\n"),
    # exit_mm_optimize copies memcontrol.c's private memcg_vmstats_percpu (6.6 layout) to read a
    # memcg's LRU sizes: on 6.18 that layout is different (and MEM_CGROUP_NTARGETS gone). The LRU
    # sizes from the public per-node counters instead, as OnePlus' own commented-out version did
    # (one memory node on this phone); try_to_free_mem_cgroup_pages takes a swappiness (NULL: the
    # memcg's own) since 6.12.
    ("exit_mm_optimize.c",
     "/*\n * FIXME : memcg->vmstats_percpu pointer",
     "#if 0\t/* op13: 6.6's private memcg layout, not 6.18's (scripts/oplus_fix.py) */\n"
     "/*\n * FIXME : memcg->vmstats_percpu pointer"),
    ("exit_mm_optimize.c",
     "\tunsigned long\t\ttargets[MEM_CGROUP_NTARGETS];\n} ____cacheline_aligned;\n",
     "\tunsigned long\t\ttargets[MEM_CGROUP_NTARGETS];\n} ____cacheline_aligned;\n#endif\n"),
    ("exit_mm_optimize.c",
     "\tint idx = LRU_BASE + stat_item;\n\tlong x = 0;\n\tint cpu;\n\n"
     "\tfor_each_possible_cpu(cpu)\n\t\tx += per_cpu(memcg->vmstats_percpu->state[idx], cpu);\n"
     "#ifdef CONFIG_SMP\n\tif (x < 0)\n\t\tx = 0;\n#endif\n\treturn x;\n",
     "\tstruct mem_cgroup_per_node *mz = memcg ? memcg->nodeinfo[0] : NULL;\n"
     "\tunsigned long nr = 0;\n\tint zid;\n\n\tif (!mz)\n\t\treturn 0;\t/* op13: public per-node LRU sizes */\n"
     "\tfor (zid = 0; zid < MAX_NR_ZONES; zid++)\n\t\tnr += READ_ONCE(mz->lru_zone_size[zid][stat_item]);\n"
     "\treturn nr;\n"),
    ("exit_mm_optimize.c",
     "try_to_free_mem_cgroup_pages(memcg, batch, gfp_mask, reclaim_options);",
     "try_to_free_mem_cgroup_pages(memcg, batch, gfp_mask, reclaim_options, NULL);"),
    # hybridswap's zram: the block limits and features go to blk_alloc_disk() (6.10+), as the
    # kernel's own zram does; the blk_queue_* setters and QUEUE_FLAG_NONROT/SYNCHRONOUS are gone
    ("zram_drv.c",
     '\tzram->disk = blk_alloc_disk(NUMA_NO_NODE);\n\tif (!zram->disk) {\n\t\tpr_err("Error allocating disk structure for device %d\\n",\n\t\t\tdevice_id);\n\t\tret = -ENOMEM;\n',
     '\t{\t/* op13: 6.10+ block limits and features, as the kernel\'s own zram (scripts/oplus_fix.py) */\n\t\tstruct queue_limits lim = {\n\t\t\t.logical_block_size\t= ZRAM_LOGICAL_BLOCK_SIZE,\n\t\t\t.physical_block_size\t= PAGE_SIZE,\n\t\t\t.io_min\t\t\t= PAGE_SIZE,\n\t\t\t.io_opt\t\t\t= PAGE_SIZE,\n\t\t\t.discard_granularity\t= PAGE_SIZE,\n\t\t\t.max_hw_discard_sectors\t= UINT_MAX,\n#if ZRAM_LOGICAL_BLOCK_SIZE == PAGE_SIZE\n\t\t\t.max_write_zeroes_sectors = UINT_MAX,\n#endif\n\t\t\t.features\t\t= BLK_FEAT_STABLE_WRITES | BLK_FEAT_SYNCHRONOUS,\n\t\t};\n\n\t\tzram->disk = blk_alloc_disk(&lim, NUMA_NO_NODE);\n\t}\n\tif (IS_ERR(zram->disk)) {\n\t\tpr_err("Error allocating disk structure for device %d\\n",\n\t\t\tdevice_id);\n\t\tret = PTR_ERR(zram->disk);\n'),
    ("zram_drv.c",
     "\t/* zram devices sort of resembles non-rotational disks */\n\tblk_queue_flag_set(QUEUE_FLAG_NONROT, zram->disk->queue);\n\tblk_queue_flag_set(QUEUE_FLAG_SYNCHRONOUS, zram->disk->queue);\n\n\t/*\n\t * To ensure that we always get PAGE_SIZE aligned\n\t * and n*PAGE_SIZED sized I/O requests.\n\t */\n\tblk_queue_physical_block_size(zram->disk->queue, PAGE_SIZE);\n\tblk_queue_logical_block_size(zram->disk->queue,\n\t\t\t\t\tZRAM_LOGICAL_BLOCK_SIZE);\n\tblk_queue_io_min(zram->disk->queue, PAGE_SIZE);\n\tblk_queue_io_opt(zram->disk->queue, PAGE_SIZE);\n\tzram->disk->queue->limits.discard_granularity = PAGE_SIZE;\n\tblk_queue_max_discard_sectors(zram->disk->queue, UINT_MAX);\n\n\t/*\n\t * zram_bio_discard() will clear all logical blocks if logical block\n\t * size is identical with physical block size(PAGE_SIZE). But if it is\n\t * different, we will skip discarding some parts of logical blocks in\n\t * the part of the request range which isn't aligned to physical block\n\t * size.  So we can't ensure that all discarded logical blocks are\n\t * zeroed.\n\t */\n\tif (ZRAM_LOGICAL_BLOCK_SIZE == PAGE_SIZE)\n\t\tblk_queue_max_write_zeroes_sectors(zram->disk->queue, UINT_MAX);\n\n\tblk_queue_flag_set(QUEUE_FLAG_STABLE_WRITES, zram->disk->queue);\n",
     '\t/* op13: non-rotational is the default, the rest is in lim above (blk_alloc_disk) */\n'),
    # hybridswap's zram on the kernel's own zsmalloc, not OnePlus' copy of 6.6's (oplus_bsp_zsmalloc,
    # 6.6's mm/zsmalloc.c with its functions renamed *_oplus and nothing else): 6.18 tells a
    # zsmalloc page by its page type and moves it (compaction, CMA) with the kernel zsmalloc's
    # movable operations (6.17), which a second copy in a module cannot have -- the copy no longer
    # builds (page->index, __ClearPageMovable gone) and could only keep its pages unmovable. The same
    # calls under OnePlus' names, but the object mappings (zs_map_object/zs_unmap_object, gone in
    # 6.16) become reads and writes as the kernel's zram does them: zs_obj_read_begin/end with a
    # buffer for an object across two pages (a compression stream's, as zram's local_copy),
    # zs_obj_write. scripts/vendor_kbuild.py leaves oplus_bsp_zsmalloc out of the build.
    ("hybridswap_zram/zram_drv.h", '#include "../thp_zsmalloc/zsmalloc.h"\n',
     "/* op13: the kernel's zsmalloc under OnePlus' names (scripts/oplus_fix.py) */\n"
     "#include <linux/zsmalloc.h>\n"
     "#define zs_create_pool_oplus\t\tzs_create_pool\n#define zs_destroy_pool_oplus\t\tzs_destroy_pool\n"
     "#define zs_free_oplus\t\t\tzs_free\n#define zs_huge_class_size_oplus\tzs_huge_class_size\n"
     "#define zs_get_total_pages_oplus\tzs_get_total_pages\n#define zs_compact_oplus\t\tzs_compact\n"
     "#define zs_lookup_class_index_oplus\tzs_lookup_class_index\n#define zs_pool_stats_oplus\t\tzs_pool_stats\n"
     "static inline unsigned long zs_malloc_oplus(struct zs_pool *pool, size_t size, gfp_t gfp)\n"
     "{\n\treturn zs_malloc(pool, size, gfp, NUMA_NO_NODE);\n}\n"),
    ("hybridswap/internal.h", '#include "thp_zsmalloc/zsmalloc.h"\n',
     "#include <linux/zsmalloc.h>\t/* op13: the kernel's (scripts/oplus_fix.py) */\n"),
    # reading an object: an incompressible one (PAGE_SIZE) fills a page of its own, never across two
    # (zram's read_incompressible_page passes no buffer either); a compressed one may be, into the
    # decompression stream's buffer, which decompression does not use
    ("hybridswap_zram/zram_drv.c",
     "\tsrc = zs_map_object_oplus(zram->mem_pool, handle, ZS_MM_RO);\n\tif (size == PAGE_SIZE) {\n"
     "\t\tdst = kmap_atomic(page);\n\t\tmemcpy(dst, src, PAGE_SIZE);\n\t\tkunmap_atomic(dst);\n\t\tret = 0;\n"
     "\t} else {\n\t\tdst = kmap_atomic(page);\n\t\tret = zcomp_decompress(zstrm, src, size, dst);\n"
     "\t\tkunmap_atomic(dst);\n\t\tzcomp_stream_put(zram->comps[prio]);\n\t}\n"
     "\tzs_unmap_object_oplus(zram->mem_pool, handle);\n\treturn ret;\n",
     "\t/* op13: zs_obj_read_begin/end (6.16+), as the kernel's zram (scripts/oplus_fix.py) */\n"
     "\tsrc = zs_obj_read_begin(zram->mem_pool, handle, size == PAGE_SIZE ? NULL : zstrm->buffer);\n"
     "\tif (size == PAGE_SIZE) {\n"
     "\t\tdst = kmap_atomic(page);\n\t\tmemcpy(dst, src, PAGE_SIZE);\n\t\tkunmap_atomic(dst);\n\t\tret = 0;\n"
     "\t\tzs_obj_read_end(zram->mem_pool, handle, src);\n"
     "\t} else {\n\t\tdst = kmap_atomic(page);\n\t\tret = zcomp_decompress(zstrm, src, size, dst);\n"
     "\t\tkunmap_atomic(dst);\n\t\tzs_obj_read_end(zram->mem_pool, handle, src);\n"
     "\t\tzcomp_stream_put(zram->comps[prio]);\n\t}\n\treturn ret;\n"),
    # writing one: zs_obj_write() of the compressed buffer, or of the page itself
    ("hybridswap_zram/zram_drv.c",
     "\tdst = zs_map_object_oplus(zram->mem_pool, handle, ZS_MM_WO);\n\n\tsrc = zstrm->buffer;\n"
     "\tif (comp_len == PAGE_SIZE)\n\t\tsrc = kmap_atomic(page);\n\tmemcpy(dst, src, comp_len);\n"
     "\tif (comp_len == PAGE_SIZE)\n\t\tkunmap_atomic(src);\n\n"
     "\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n\tzs_unmap_object_oplus(zram->mem_pool, handle);\n",
     "\t/* op13: zs_obj_write (6.16+), as the kernel's zram (scripts/oplus_fix.py) */\n\tsrc = zstrm->buffer;\n"
     "\tif (comp_len == PAGE_SIZE)\n\t\tsrc = kmap_local_page(page);\n"
     "\tzs_obj_write(zram->mem_pool, handle, src, comp_len);\n"
     "\tif (comp_len == PAGE_SIZE)\n\t\tkunmap_local(src);\n\n"
     "\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"),
    ("hybridswap_zram/zram_drv.c", "\tvoid *src, *dst, *mem;\n", "\tvoid *src, *mem;\t/* op13 */\n"),
    ("hybridswap_zram/zram_drv.c",
     "\tdst = zs_map_object_oplus(zram->mem_pool, handle_new, ZS_MM_WO);\n"
     "\tmemcpy(dst, zstrm->buffer, comp_len_new);\n\tzcomp_stream_put(zram->comps[prio]);\n\n"
     "\tzs_unmap_object_oplus(zram->mem_pool, handle_new);\n",
     "\tzs_obj_write(zram->mem_pool, handle_new, zstrm->buffer, comp_len_new);\t/* op13: 6.16+ */\n"
     "\tzcomp_stream_put(zram->comps[prio]);\n"),
    ("hybridswap_zram/zram_drv.c", "\tu32 num_recomps = 0;\n\tvoid *src, *dst;\n",
     "\tu32 num_recomps = 0;\n\tvoid *src;\t/* op13 */\n"),
    # hybridswap moving an object back from its extent into zram: gathered from the extent's pages
    # into a stream's buffer, then written; zs_malloc() fails with an error value, not 0 (OnePlus'
    # check let a failed allocation through as a handle)
    ("hybridswap/hybridswap.c", "\tint size, i;\n\tu8 *dst = NULL;\n",
     "\tint size, i;\n\tstruct zcomp_strm *zstrm;\t/* op13 */\n"),
    ("hybridswap/hybridswap.c",
     "\tif (!handle) {\n\t\tlog_err(\"alloc handle failed, size = %d\\n\", size);\n\t\treturn -ENOMEM;\n\t}\n"
     "\tdst = zs_map_object_oplus(zram->mem_pool, handle, ZS_MM_WO);\n"
     "\tcopy_from_pages(dst, io_ext->pages, eswpentry, size);\n\tzs_unmap_object_oplus(zram->mem_pool, handle);\n",
     "\tif (IS_ERR_VALUE(handle)) {\t/* op13: an error value, not 0 */\n"
     "\t\tlog_err(\"alloc handle failed, size = %d\\n\", size);\n\t\treturn -ENOMEM;\n\t}\n"
     "\t/* op13: zs_obj_write (6.16+), through a stream's buffer (scripts/oplus_fix.py) */\n"
     "\tzstrm = zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP]);\n"
     "\tcopy_from_pages(zstrm->buffer, io_ext->pages, eswpentry, size);\n"
     "\tzs_obj_write(zram->mem_pool, handle, zstrm->buffer, size);\n"
     "\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"),
    # ... and one going out to its extent: read as zram reads it
    ("hybridswap/hybridswap.c", "\tunsigned long handle;\n\tint size;\n\tu8 *src = NULL;\n",
     "\tunsigned long handle;\n\tint size;\n\tu8 *src = NULL;\n\tstruct zcomp_strm *zstrm;\t/* op13 */\n"),
    ("hybridswap/hybridswap.c",
     "\tsrc = zs_map_object_oplus(zram->mem_pool, handle, ZS_MM_RO);\n"
     "\tcopy_to_pages(src, io_ext->pages, ext_off, size);\n\tzs_unmap_object_oplus(zram->mem_pool, handle);\n",
     "\t/* op13: zs_obj_read_begin/end (6.16+), a stream's buffer for an object across two pages */\n"
     "\tzstrm = zcomp_stream_get(zram->comps[ZRAM_PRIMARY_COMP]);\n"
     "\tsrc = zs_obj_read_begin(zram->mem_pool, handle, zstrm->buffer);\n"
     "\tcopy_to_pages(src, io_ext->pages, ext_off, size);\n\tzs_obj_read_end(zram->mem_pool, handle, src);\n"
     "\tzcomp_stream_put(zram->comps[ZRAM_PRIMARY_COMP]);\n"),
    # hybridswap's hybridmain.c: the same copied 6.6 memcg_vmstats_percpu as exit_mm_optimize (its
    # memcg_page_state_local read swap usage through it) -> the exported memcg_page_state(); and
    # the 6.6 hook android_vh_alloc_pages_slowpath(gfp, order, alloc_start), which 6.18 split into
    # _start/_end -- _end fires where it did (got_pg) with the same gfp/order/start: an adapter
    ("hybridmain.c",
     "/*\n * FIXME : memcg->vmstats_percpu pointer",
     "#if 0\t/* op13: 6.6's private memcg layout, not 6.18's (scripts/oplus_fix.py) */\n"
     "/*\n * FIXME : memcg->vmstats_percpu pointer"),
    ("hybridmain.c",
     "\tunsigned long\t\ttargets[MEM_CGROUP_NTARGETS];\n} ____cacheline_aligned;\n",
     "\tunsigned long\t\ttargets[MEM_CGROUP_NTARGETS];\n} ____cacheline_aligned;\n#endif\n"),
    ("hybridmain.c",
     "\tlong x = 0;\n\tint cpu;\n\n\tfor_each_possible_cpu(cpu)\n\t\tx += per_cpu(memcg->vmstats_percpu->state[idx], cpu);\n"
     "#ifdef CONFIG_SMP\n\tif (x < 0)\n\t\tx = 0;\n#endif\n\treturn x;\n",
     "\treturn memcg_page_state(memcg, idx);\t/* op13: exported in 6.18 */\n"),
    ("hybridmain.c",
     "struct hybridswapd_operations *hybridswapd_ops;\n",
     "struct hybridswapd_operations *hybridswapd_ops;\n\n"
     "/* op13: 6.18's android_vh_alloc_pages_slowpath_end, called where 6.6's _slowpath hook was */\n"
     "static void k618_slowpath_end(void *data, gfp_t *gfp_mask, unsigned int order,\n"
     "\t\tunsigned long alloc_start, u64 stime, unsigned long did_some_progress,\n"
     "\t\tunsigned long pages_reclaimed, int retry_loop_count)\n{\n"
     "\tif (hybridswapd_ops && hybridswapd_ops->vh_alloc_pages_slowpath)\n"
     "\t\thybridswapd_ops->vh_alloc_pages_slowpath(data, *gfp_mask, order, alloc_start);\n}\n"),
    ("hybridmain.c",
     "register_trace_android_vh_alloc_pages_slowpath(hybridswapd_ops->vh_alloc_pages_slowpath, NULL)",
     "register_trace_android_vh_alloc_pages_slowpath_end(k618_slowpath_end, NULL)"),
    # mm_osvelte's ashmem statistics (sys-ashmem.c) read the C ashmem driver's struct ashmem_area;
    # 6.18's ashmem is the Rust driver, whose area is reachable only through accessors that take a
    # mutex -- and this walk runs from an fd iteration in atomic context (GFP_ATOMIC). Left out:
    # the rest of mm_osvelte (memory tracking, lowmem debug, dma-buf statistics) does not need it
    ("Makefile",
     "oplus_bsp_mm_osvelte-$(CONFIG_ASHMEM) += sys-ashmem.o\n",
     "# op13: sys-ashmem.o left out (scripts/oplus_fix.py)\n"),
    ("sys-memstat.c",
     "\tdir_entry = mtrack_procs[MTRACK_ASHMEM];\n\tif (dir_entry)\n\t\tcreate_ashmem_procfs(dir_entry);\n",
     "\t/* op13: no ashmem statistics on 6.18's Rust ashmem (scripts/oplus_fix.py) */\n"),
    # android_vh_do_futex gained the futex address first (6.12 ACK); the handler does not use it
    ("futex.c",
     "static void android_vh_do_futex_handler(void *unused, int cmd,",
     "static void android_vh_do_futex_handler(void *unused, u32 __user *uaddr, int cmd,"),
    # binder_watcher: the kernel's binder_alloc.h has its own binder_alloc_buffer_size() now, and
    # the mapping's start is alloc->vm_start (6.12): the module's copy under its own name
    ("binder_watcher.c", "binder_alloc_buffer_size(", "oplus_binder_alloc_buffer_size("),
    ("binder_watcher.c", "return alloc->buffer + alloc->buffer_size - buffer->user_data;",
     "return alloc->vm_start + alloc->buffer_size - buffer->user_data;"),
    # the fingerprint driver's wake_lock shim: wakeup_source_add/remove are gone (6.18), only
    # registered wakeup sources: the lock holds one
    ("wakelock.h",
     'struct wake_lock {\n    struct wakeup_source ws;\n};',
     'struct wake_lock {\n    struct wakeup_source *ws;\t/* op13: registered (6.18) */\n};'),
    ("wakelock.h",
     '    if (lock) {\n        memset(&lock->ws, 0, sizeof(lock->ws));\n        lock->ws.name = name;\n    }\n    wakeup_source_add(&lock->ws);\n}',
     '    if (lock)\n        lock->ws = wakeup_source_register(NULL, name);\t/* op13: 6.18 */\n}'),
    ("wakelock.h",
     '    wakeup_source_remove(&lock->ws);\n    if (!lock) {\n        return;\n    }\n    __pm_relax(&lock->ws);\n}',
     '    if (!lock || !lock->ws) {\n        return;\n    }\n    __pm_relax(lock->ws);\n    wakeup_source_unregister(lock->ws);\t/* op13 */\n    lock->ws = NULL;\n}'),
    ("wakelock.h",
     '__pm_stay_awake(&lock->ws);',
     '__pm_stay_awake(lock->ws);'),
    ("wakelock.h",
     '__pm_wakeup_event(&lock->ws, jiffies_to_msecs(timeout));',
     '__pm_wakeup_event(lock->ws, jiffies_to_msecs(timeout));'),
    ("wakelock.h",
     '    __pm_relax(&lock->ws);\n}\n\nstatic inline int wake_lock_active',
     '    __pm_relax(lock->ws);\n}\n\nstatic inline int wake_lock_active'),
    ("wakelock.h",
     'return lock->ws.active;',
     'return lock->ws && lock->ws->active;'),
    ("oplus_uprobe.c",
     "static struct kprobe uprobe_unregister_kp = {\n\t.symbol_name = \"uprobe_unregister\"\n};",
     "static struct kprobe uprobe_unregister_kp = {\n\t.symbol_name = \"uprobe_unregister_nosync\"\n};"),
    # sched_assist: its own declaration of the scheduler's set_next_entity() clashes with 6.18's
    # (sched.h: a third argument, first -- true where a pick makes the entity current, as
    # pick_next_task_fair() does)
    ("sa_fair.c",
     "extern void set_next_entity(struct cfs_rq *cfs_rq, struct sched_entity *se);\n", ""),
    # android_rvh_replace_next_task_fair is (rq, &p, prev) in 6.18: pick_next_task_fair() makes the
    # task it is handed current, its entities included (set_next_entity()), itself. WALT owns the hook
    # and calls sched_assist's handler with the replacement flag, as OnePlus' 6.12 port has it
    # (rq, p, repick, prev); the OnePlus 13's (6.6) handler took the entity and the "simple" path too
    # and set the entities next itself. Its 7-argument handler called with WALT's 5 corrupted WALT's
    # stack (test-62: "stack-protector: Kernel stack is corrupted in:
    # walt_cfs_replace_next_task_fair"): the OnePlus 13's handler, its UX, DDL and locking picks take
    # the 6.12 arguments, as OnePlus' 6.12 port does
    ("sa_fair.c",
     "void android_rvh_replace_next_task_fair_handler(void *unused,\n"
     "\t\tstruct rq *rq, struct task_struct **p, struct sched_entity **se, bool *repick, bool simple, struct task_struct *prev)\n"
     "{\n"
     "\toplus_replace_next_task_fair(rq, p, se, repick, simple);\n"
     "#ifdef CONFIG_LOCKING_PROTECT\n"
     "\tif (*repick != true)\n"
     "\t\tLOCKING_CALL_OP(replace_next_task_fair, rq, p, se, repick, simple);\n"
     "#endif\n"
     "\n"
     "#if IS_ENABLED(CONFIG_OPLUS_FEATURE_SCHED_DDL)\n"
     "\tif (*repick != true && global_sched_ddl_enabled)\n"
     "\t\toplus_replace_next_task_ddl(rq, p, se, repick, simple);\n"
     "#endif\n"
     "\n"
     "\t/*\n"
     "\t* NOTE:\n"
     "\t* Because the following code is not merged in kernel-5.15,\n"
     "\t* set_next_entity() will no longer be called to remove the\n"
     "\t* task from the red-black tree when pick_next_task_fair(),\n"
     "\t* so we remove the picked task here.\n"
     "\t*\n"
     "\t* https://android-review.googlesource.com/c/kernel/common/+/1667002\n"
     "\t*/\n"
     "\tif (simple && true == *repick) {\n"
     "\t\tfor_each_sched_entity((*se)) {\n"
     "\t\t\tstruct cfs_rq *cfs_rq = cfs_rq_of(*se);\n"
     "\t\t\tset_next_entity(cfs_rq, *se);\n"
     "\t\t}\n"
     "\t}\n"
     "}\n",
     "/* op13: 6.18's hook, OnePlus' 6.12 arguments (scripts/oplus_fix.py) */\n"
     "void android_rvh_replace_next_task_fair_handler(void *unused,\n"
     "\t\tstruct rq *rq, struct task_struct **p, bool *repick, struct task_struct *prev)\n"
     "{\n"
     "\tstruct task_struct *k618_picked = *p;\n\n"
     "\toplus_replace_next_task_fair(rq, p, repick);\n"
     "#ifdef CONFIG_LOCKING_PROTECT\n"
     "\tif (*repick != true)\n"
     "\t\tLOCKING_CALL_OP(replace_next_task_fair, rq, p, repick);\n"
     "#endif\n"
     "\n"
     "#if IS_ENABLED(CONFIG_OPLUS_FEATURE_SCHED_DDL)\n"
     "\tif (*repick != true && global_sched_ddl_enabled)\n"
     "\t\toplus_replace_next_task_ddl(rq, p, repick);\n"
     "#endif\n"
     "\t/* the picked task's entities: pick_next_task_fair() sets them next */\n"
     "\n"
     "\t/*\n"
     "\t * op13: 6.12+ keeps a task that slept queued until its lag is spent (EEVDF's delayed\n"
     "\t * dequeue). The OnePlus 13's (6.6) UX, DDL and locking lists know nothing of it: a pick of\n"
     "\t * such a task ran a sleeping task, and its next dequeue was a second one (test-63: WALT's\n"
     "\t * \"double dequeue detected\", walt.c). The kernel's own pick stands then.\n"
     "\t */\n"
     "\tif (*p && *p != k618_picked && (*p)->se.sched_delayed) {\n"
     "\t\t*p = k618_picked;\n"
     "\t\t*repick = false;\n"
     "\t}\n"
     "}\n"),
    # (the same, in a tree an older oplus_fix.py edited: set_next_entity()'s third argument)
    ("sa_fair.c",
     "void android_rvh_replace_next_task_fair_handler(void *unused,\n"
     "\t\tstruct rq *rq, struct task_struct **p, struct sched_entity **se, bool *repick, bool simple, struct task_struct *prev)\n"
     "{\n"
     "\toplus_replace_next_task_fair(rq, p, se, repick, simple);\n"
     "#ifdef CONFIG_LOCKING_PROTECT\n"
     "\tif (*repick != true)\n"
     "\t\tLOCKING_CALL_OP(replace_next_task_fair, rq, p, se, repick, simple);\n"
     "#endif\n"
     "\n"
     "#if IS_ENABLED(CONFIG_OPLUS_FEATURE_SCHED_DDL)\n"
     "\tif (*repick != true && global_sched_ddl_enabled)\n"
     "\t\toplus_replace_next_task_ddl(rq, p, se, repick, simple);\n"
     "#endif\n"
     "\n"
     "\t/*\n"
     "\t* NOTE:\n"
     "\t* Because the following code is not merged in kernel-5.15,\n"
     "\t* set_next_entity() will no longer be called to remove the\n"
     "\t* task from the red-black tree when pick_next_task_fair(),\n"
     "\t* so we remove the picked task here.\n"
     "\t*\n"
     "\t* https://android-review.googlesource.com/c/kernel/common/+/1667002\n"
     "\t*/\n"
     "\tif (simple && true == *repick) {\n"
     "\t\tfor_each_sched_entity((*se)) {\n"
     "\t\t\tstruct cfs_rq *cfs_rq = cfs_rq_of(*se);\n"
     "\t\t\tset_next_entity(cfs_rq, *se, true);\n"
     "\t\t}\n"
     "\t}\n"
     "}\n",
     "/* op13: 6.18's hook, OnePlus' 6.12 arguments (scripts/oplus_fix.py) */\n"
     "void android_rvh_replace_next_task_fair_handler(void *unused,\n"
     "\t\tstruct rq *rq, struct task_struct **p, bool *repick, struct task_struct *prev)\n"
     "{\n"
     "\tstruct task_struct *k618_picked = *p;\n\n"
     "\toplus_replace_next_task_fair(rq, p, repick);\n"
     "#ifdef CONFIG_LOCKING_PROTECT\n"
     "\tif (*repick != true)\n"
     "\t\tLOCKING_CALL_OP(replace_next_task_fair, rq, p, repick);\n"
     "#endif\n"
     "\n"
     "#if IS_ENABLED(CONFIG_OPLUS_FEATURE_SCHED_DDL)\n"
     "\tif (*repick != true && global_sched_ddl_enabled)\n"
     "\t\toplus_replace_next_task_ddl(rq, p, repick);\n"
     "#endif\n"
     "\t/* the picked task's entities: pick_next_task_fair() sets them next */\n"
     "\n"
     "\t/*\n"
     "\t * op13: 6.12+ keeps a task that slept queued until its lag is spent (EEVDF's delayed\n"
     "\t * dequeue). The OnePlus 13's (6.6) UX, DDL and locking lists know nothing of it: a pick of\n"
     "\t * such a task ran a sleeping task, and its next dequeue was a second one (test-63: WALT's\n"
     "\t * \"double dequeue detected\", walt.c). The kernel's own pick stands then.\n"
     "\t */\n"
     "\tif (*p && *p != k618_picked && (*p)->se.sched_delayed) {\n"
     "\t\t*p = k618_picked;\n"
     "\t\t*repick = false;\n"
     "\t}\n"
     "}\n"),
    # (a tree an earlier oplus_fix.py edited: the handler as e80fb3f wrote it gains the guard)
    ("sa_fair.c",
     "\t\tstruct rq *rq, struct task_struct **p, bool *repick, struct task_struct *prev)\n{\n"
     "\toplus_replace_next_task_fair(rq, p, repick);\n",
     "\t\tstruct rq *rq, struct task_struct **p, bool *repick, struct task_struct *prev)\n{\n"
     "\tstruct task_struct *k618_picked = *p;\n\n"
     "\toplus_replace_next_task_fair(rq, p, repick);\n"),
    ("sa_fair.c",
     "\t/* the picked task's entities: pick_next_task_fair() sets them next */\n}\n",
     "\t/* the picked task's entities: pick_next_task_fair() sets them next */\n"
     "\n"
     "\t/*\n"
     "\t * op13: 6.12+ keeps a task that slept queued until its lag is spent (EEVDF's delayed\n"
     "\t * dequeue). The OnePlus 13's (6.6) UX, DDL and locking lists know nothing of it: a pick of\n"
     "\t * such a task ran a sleeping task, and its next dequeue was a second one (test-63: WALT's\n"
     "\t * \"double dequeue detected\", walt.c). The kernel's own pick stands then.\n"
     "\t */\n"
     "\tif (*p && *p != k618_picked && (*p)->se.sched_delayed) {\n"
     "\t\t*p = k618_picked;\n"
     "\t\t*repick = false;\n"
     "\t}\n"
     "}\n"),
    # EEVDF's delayed dequeue (6.12+) in the OnePlus 13's (6.6) scheduler group: the checks OnePlus'
    # 6.12 port (the OnePlus 15's tree) makes, at the same places. A delayed task has slept: it is
    # no UX, DDL or balance candidate. Without them the OnePlus 13's lists held sleeping tasks and
    # picked them to run (test-63: WALT's "double dequeue detected" at the task's next dequeue)
    ("sched/sched_assist/sa_common.c",
     "\t} else if (task_on_rq_queued(t)) {\n",
     "\t} else if (task_on_rq_queued(t) && !t->se.sched_delayed) {\t/* op13: 6.12+ (OnePlus' 6.12 port) */\n"),
    ("sched/sched_assist/sa_common.c",
     "static void enqueue_ux_thread(struct rq *rq, struct task_struct *p)\n",
     "static void enqueue_ux_thread(struct rq *rq, struct task_struct *p, int flags)\t/* op13: 6.12's */\n"),
    ("sched/sched_assist/sa_common.c",
     "\tif (unlikely(!global_sched_assist_enabled))\n\t\treturn;\n\n\toplus_set_enqueue_time(p, rq->clock);\n",
     "\tif (unlikely(!global_sched_assist_enabled))\n\t\treturn;\n\n"
     "\t/* op13: a delayed task is enqueued again only by its wakeup (ENQUEUE_DELAYED; OnePlus' 6.12 port) */\n"
     "\tif (p->se.sched_delayed && !(flags & ENQUEUE_DELAYED))\n\t\treturn;\n\n"
     "\toplus_set_enqueue_time(p, rq->clock);\n"),
    ("sched/sched_assist/sa_common.c",
     "void queue_ux_thread(struct rq *rq, struct task_struct *p, int enqueue)\n{\n\tif (enqueue)\n\t\tenqueue_ux_thread(rq, p);\n",
     "void queue_ux_thread(struct rq *rq, struct task_struct *p, int enqueue, int flags)\t/* op13: 6.12's */\n{\n\tif (enqueue)\n\t\tenqueue_ux_thread(rq, p, flags);\n"),
    ("sched/sched_assist/sa_common.c", "\tqueue_ux_thread(rq, p, 1);\n", "\tqueue_ux_thread(rq, p, 1, flags);\n"),
    ("sched/sched_assist/sa_common.c", "\tqueue_ux_thread(rq, p, 0);\n", "\tqueue_ux_thread(rq, p, 0, flags);\n"),
    ("sched/sched_assist/sa_common.h",
     "void queue_ux_thread(struct rq *rq, struct task_struct *p, int enqueue);\n",
     "void queue_ux_thread(struct rq *rq, struct task_struct *p, int enqueue, int flags);\t/* op13: 6.12's */\n"),
    ("sched/sched_assist/sa_balance.c",
     "int can_migrate_task(struct task_struct *p, struct lb_env *env)\n{\n\t/*\n\t * del by oplus.\n\t * int can_migrate = 1;\n\t */\n\n\tlockdep_assert_rq_held(env->src_rq);\n",
     "int can_migrate_task(struct task_struct *p, struct lb_env *env)\n{\n\t/*\n\t * del by oplus.\n\t * int can_migrate = 1;\n\t */\n\n\tlockdep_assert_rq_held(env->src_rq);\n\n"
     "\tif (p->se.sched_delayed)\t/* op13: 6.12+ (OnePlus' 6.12 port) */\n\t\treturn 0;\n",
     "\tif (p->se.sched_delayed)\n\t\treturn 0;\n"),
    ("sched/sched_assist/sa_balance.c",
     "\tif (READ_ONCE((tsk)->__state) != TASK_RUNNING)\n\t\treturn false;\n\n\treturn ((tsk->on_rq == TASK_ON_RQ_QUEUED) && !smp_load_acquire(&tsk->on_cpu));\n",
     "\tif (READ_ONCE((tsk)->__state) != TASK_RUNNING)\n\t\treturn false;\n\n"
     "\tif (tsk->se.sched_delayed)\t/* op13: 6.12+ (OnePlus' 6.12 port) */\n\t\treturn false;\n\n"
     "\treturn ((tsk->on_rq == TASK_ON_RQ_QUEUED) && !smp_load_acquire(&tsk->on_cpu));\n"),
    ("sched/sched_assist/sa_balance.c",
     "\t\tif (p == prime_pipeline_task)\n\t\t\tcontinue;\n\n\t\tpull_me = p;\n",
     "\t\tif (p == prime_pipeline_task)\n\t\t\tcontinue;\n\n"
     "\t\tif (p->se.sched_delayed)\t/* op13: 6.12+ (OnePlus' 6.12 port) */\n\t\t\tcontinue;\n\n"
     "\t\tpull_me = p;\n"),
    ("sched/sched_assist/sa_ddl.c",
     "\t} else if (task->se.on_rq && !task_on_cpu(rq, task)) {\n",
     "\t} else if (task->se.on_rq && !task_on_cpu(rq, task) && !task->se.sched_delayed) {\t/* op13: 6.12+ */\n"),
    ("sched/sched_assist/sa_ddl.c",
     "\t\tif (IS_ERR_OR_NULL(ots_p))\n\t\t\treturn;\n",
     "\t\tif (IS_ERR_OR_NULL(ots_p) || p->se.sched_delayed)\t/* op13: 6.12+ */\n\t\t\treturn;\n"),
    ("sched/sched_assist/sa_ddl.c",
     "\tif (!p || !oplus_get_task_ddl(p) || IS_ERR_OR_NULL(ots))\n\t\treturn;\n",
     "\tif (!p || !oplus_get_task_ddl(p) || IS_ERR_OR_NULL(ots)\n\t\t\t|| p->se.sched_delayed)\t/* op13: 6.12+ */\n\t\treturn;\n"),
    ("sched/sched_assist/sa_ddl.c",
     "\t\tots = __node_2_ots(node);\n\n\t\tif (IS_ERR_OR_NULL(ots)) {\n\t\t\tWARN_ON_ONCE(1);\n\t\t\tgoto out_leftmost;\n\t\t}\n",
     "\t\tots = __node_2_ots(node);\n\n\t\tif (IS_ERR_OR_NULL(ots)) {\n\t\t\tWARN_ON_ONCE(1);\n\t\t\tgoto out_leftmost;\n\t\t}\n\n"
     "\t\tif (ots->task && ots->task->se.sched_delayed)\t/* op13: 6.12+ */\n\t\t\tcontinue;\n",
     "if (ots->task && ots->task->se.sched_delayed)\n"),
    ("sa_fair.c",
     "void oplus_replace_next_task_fair(struct rq *rq, struct task_struct **p, struct sched_entity **se, bool *repick, bool simple)\n",
     "void oplus_replace_next_task_fair(struct rq *rq, struct task_struct **p, bool *repick)\t/* op13: 6.12's */\n"),
    ("sa_fair.c", "\t\t\t*p = candidate;\n\t\t\t*se = &candidate->se;\n", "\t\t\t*p = candidate;\t/* op13: its entity is the kernel's to set */\n"),
    ("sched/sched_assist/sa_fair.h",
     "void oplus_replace_next_task_fair(struct rq *rq, struct task_struct **p, struct sched_entity **se, bool *repick, bool simple);\n",
     "void oplus_replace_next_task_fair(struct rq *rq, struct task_struct **p, bool *repick);\t/* op13: 6.12's */\n"),
    ("sched/sched_assist/sa_fair.h",
     "void android_rvh_replace_next_task_fair_handler(void *unused, struct rq *rq, struct task_struct **p, struct sched_entity **se,\n"
     "\tbool *repick, bool simple, struct task_struct *prev);\n",
     "void android_rvh_replace_next_task_fair_handler(void *unused, struct rq *rq, struct task_struct **p,\n"
     "\tbool *repick, struct task_struct *prev);\t/* op13: 6.12's (WALT calls it) */\n"),
    ("sched/sched_assist/sa_ddl.c",
     "void oplus_replace_next_task_ddl(struct rq *rq, struct task_struct **p,\n\tstruct sched_entity **se, bool *repick, bool simple)\n",
     "void oplus_replace_next_task_ddl(struct rq *rq, struct task_struct **p,\n\tbool *repick)\t/* op13: 6.12's */\n"),
    ("sched/sched_assist/sa_ddl.c", "\t\t*p = ots->task;\n\t\t*se = &ots->task->se;\n", "\t\t*p = ots->task;\t/* op13: its entity is the kernel's to set */\n"),
    ("sched/sched_assist/sa_ddl.h",
     "void oplus_replace_next_task_ddl(struct rq *rq, struct task_struct **p,\n\tstruct sched_entity **se, bool *repick, bool simple);\n",
     "void oplus_replace_next_task_ddl(struct rq *rq, struct task_struct **p,\n\tbool *repick);\t/* op13: 6.12's */\n"),
    ("sched/sched_assist/sa_common.h",
     "\tvoid (*replace_next_task_fair)(struct rq *rq,\n\t\t\tstruct task_struct **p, struct sched_entity **se, bool *repick, bool simple);\n",
     "\tvoid (*replace_next_task_fair)(struct rq *rq,\n\t\t\tstruct task_struct **p, bool *repick);\t/* op13: 6.12's */\n"),
    ("synchronize/sched_assist_locking.c",
     "void replace_next_task_fair_locking(struct rq *rq, struct task_struct **p,\n\t\t\t\t\tstruct sched_entity **se, bool *repick, bool simple)\n",
     "void replace_next_task_fair_locking(struct rq *rq, struct task_struct **p,\n\t\t\t\t\tbool *repick)\t/* op13: 6.12's (scripts/oplus_fix.py) */\n"),
    ("synchronize/sched_assist_locking.c", "\tif (!rq || !p || !se)\n\t\treturn;\n", "\tif (!rq || !p)\t/* op13: no entity argument */\n\t\treturn;\n"),
    ("synchronize/sched_assist_locking.c", "\t\t\t*p = key_task;\n\t\t\t*se = key_se;\n", "\t\t\t*p = key_task;\t/* op13: its entity is the kernel's to set */\n"),
    # 6.12+ (EEVDF's delayed dequeue): an entity that slept but is kept queued until its lag is spent
    # is no task to run; OnePlus' 6.12 port skips it here
    ("synchronize/sched_assist_locking.c",
     "\t\tkey_se = &key_task->se;\n\n\t\tif (!test_task_is_fair(key_task) || !task_inlock(key_ots)\n",
     "\t\tkey_se = &key_task->se;\n\n"
     "\t\t/* op13: a delayed-dequeue entity is not runnable (6.12+; OnePlus' 6.12 port) */\n"
     "\t\tif (key_se->sched_delayed)\n\t\t\tcontinue;\n\n"
     "\t\tif (!test_task_is_fair(key_task) || !task_inlock(key_ots)\n"),
    # frame_boost: android_vh_binder_restore_priority has no task argument in 6.18; binder calls it
    # from the replying thread itself (binder_transaction(), before binder_restore_priority(thread))
    ("frame_group.c",
     "\tregister_trace_android_vh_binder_restore_priority(fbg_binder_restore_priority_hook, NULL);\n",
     "\tregister_trace_android_vh_binder_restore_priority(k618_fbg_binder_restore_priority_hook, NULL);\n"),
    ("frame_group.c",
     "\n/*\n * fbg_binder_restore_priority_hook - check if the binder thread",
     "\nstatic void fbg_binder_restore_priority_hook(void *unused, struct binder_transaction *t,\n"
     "\tstruct task_struct *task);\n\n"
     "/* op13: the 6.18 hook (scripts/oplus_fix.py) -- the task is the replying binder thread */\n"
     "static void k618_fbg_binder_restore_priority_hook(void *unused, struct binder_transaction *t)\n"
     "{\n\tfbg_binder_restore_priority_hook(unused, t, current);\n}\n"
     "\n/*\n * fbg_binder_restore_priority_hook - check if the binder thread"),
    # binder_strategy: the same 6.18 android_vh_binder_restore_priority, without the task (the
    # replying thread: current)
    ("binder_sched.c",
     "\tregister_trace_android_vh_binder_restore_priority(\n\t\tandroid_vh_binder_restore_priority_handler, NULL);\n",
     "\tregister_trace_android_vh_binder_restore_priority(\n\t\tk618_binder_restore_priority_handler, NULL);\n"),
    ("binder_sched.c",
     "\nvoid register_binder_sched_vendor_hooks(void)\n{\n",
     "\n/* op13: the 6.18 hook (scripts/oplus_fix.py) -- the task is the replying binder thread */\n"
     "static void k618_binder_restore_priority_handler(void *unused, struct binder_transaction *t)\n"
     "{\n\tandroid_vh_binder_restore_priority_handler(unused, t, current);\n}\n"
     "\nvoid register_binder_sched_vendor_hooks(void)\n{\n"),
    # memleak_detect's slub copy: mm/slab.h has slub_debug_orig_size() itself in 6.18, and the slab
    # lock is SL_locked (= PG_locked) on the slab's memdesc_flags_t, taken and released as 6.18's
    # slub.c does (bit_spin_unlock: the other bits of those flags change concurrently)
    ("slub_track.c",
     "static inline bool slub_debug_orig_size(struct kmem_cache *s)\n{\n\treturn (kmem_cache_debug_flags(s, SLAB_STORE_USER) &&\n\t\t\t\t(s->flags & SLAB_KMALLOC));\n}\n",
     ""),
    ("slub_track.c",
     "\tbit_spin_lock(PG_locked, &page->flags);\n",
     "\tbit_spin_lock(SL_locked, &slab->flags.f);\t/* op13: as 6.18's slub.c */\n"),
    ("slub_track.c",
     "\t__bit_spin_unlock(PG_locked, &page->flags);\n",
     "\tbit_spin_unlock(SL_locked, &slab->flags.f);\t/* op13: as 6.18's slub.c */\n"),
    # charger v2: <../voocphy/...> and <../charger_ic/...> mean v2's own directories (found through
    # v2/include in Bazel's sandbox, where nothing above the package is visible); here the package
    # directory comes first and its parent holds the v1 charger, whose voocphy headers clash with
    # v2's enums (oplus_chg_v2: "redefinition of enumerator 'TRACK_CP_ERR_DEFAULT'"). Relative to
    # the file instead, which lands in v2.
    ("oplus_hal_adsp.c", "#include <../voocphy/oplus_adsp_voocphy.h>", '#include "../voocphy/oplus_adsp_voocphy.h"'),
    ("oplus_hal_sm8450.c", "#include <../voocphy/oplus_adsp_voocphy.h>", '#include "../voocphy/oplus_adsp_voocphy.h"'),
    ("oplus_adsp_voocphy.c", "#include <../charger_ic/oplus_hal_sm8450.h>", '#include "../charger_ic/oplus_hal_sm8450.h"'),
    ("oplus_adsp_voocphy.c", "#include <../charger_ic/oplus_hal_adsp.h>", '#include "../charger_ic/oplus_hal_adsp.h"'),
    ("oplus_chglib.c", "#include <../voocphy/oplus_voocphy.h>", '#include "../oplus_voocphy.h"'),
    # oplus_locking / sched_assist_locking: the lock-time hooks 6.18's ACK dropped, on the ones it has
    ("oplus_locking.c", "#include <trace/hooks/dtask.h>\n", "#include <trace/hooks/dtask.h>\n#include <trace/hooks/rwsem.h>\n\n/*\n * op13: android16-6.12's android_vh_record_{mutex,rtmutex,rwsem,pcpu_rwsem}_*starttime(lock,\n * jiffies) on 6.18's lock acquired/released hooks (scripts/oplus_fix.py): the handler gets jiffies\n * when the lock is taken and 0 when it is let go, as the 6.12 hooks called it\n */\n#define K618_LOCKTIME(name, type, acq, rel)\t\t\t\t\t\t\\\nstatic void (*k618_##name##_fn)(void *, type *, unsigned long);\t\t\t\\\nstatic void *k618_##name##_data;\t\t\t\t\t\t\t\\\nstatic void k618_##name##_acq(void *u, type *l)\t\t\t\t\t\\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn(k618_##name##_data, l, jiffies);\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic void k618_##name##_rel(void *u, type *l)\t\t\t\t\t\\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn(k618_##name##_data, l, 0);\t\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic inline int register_trace_android_vh_##name(void (*fn)(void *, type *, unsigned long), void *data) \\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tint ret;\t\t\t\t\t\t\t\t\t\\\n\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn = fn;\t\t\t\t\t\t\t\t\\\n\tk618_##name##_data = data;\t\t\t\t\t\t\t\\\n\tret = register_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n\tif (ret)\t\t\t\t\t\t\t\t\t\\\n\t\treturn ret;\t\t\t\t\t\t\t\t\\\n\tret = register_trace_android_vh_##rel(k618_##name##_rel, NULL);\t\t\\\n\tif (ret)\t\t\t\t\t\t\t\t\t\\\n\t\tunregister_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n\treturn ret;\t\t\t\t\t\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic inline int unregister_trace_android_vh_##name(void (*fn)(void *, type *, unsigned long), void *data) \\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tunregister_trace_android_vh_##rel(k618_##name##_rel, NULL);\t\t\t\\\n\treturn unregister_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n}\nK618_LOCKTIME(record_mutex_lock_starttime, struct mutex, mutex_lock_acquired, mutex_lock_released)\nK618_LOCKTIME(record_rtmutex_lock_starttime, struct rt_mutex, rtmutex_lock_acquired, rtmutex_lock_released)\n#ifdef CONFIG_PCPU_RWSEM_LOCKING_PROTECT\nK618_LOCKTIME(record_pcpu_rwsem_starttime, struct percpu_rw_semaphore, pcpu_rwsem_lock_acquired,\n\tpcpu_rwsem_lock_released)\n#endif\n\n/* an rwsem: taken by a reader or the writer, let go by either */\nstatic void (*k618_rwsem_fn)(void *, struct rw_semaphore *, unsigned long);\nstatic void *k618_rwsem_data;\nstatic void k618_rwsem_racq(void *u, struct rw_semaphore *s, struct list_head *w)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, jiffies);\n}\nstatic void k618_rwsem_wacq(void *u, struct rw_semaphore *s)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, jiffies);\n}\nstatic void k618_rwsem_rel(void *u, struct rw_semaphore *s)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, 0);\n}\nstatic inline int unregister_trace_android_vh_record_rwsem_lock_starttime(\n\tvoid (*fn)(void *, struct rw_semaphore *, unsigned long), void *data)\n{\n\tunregister_trace_android_vh_clear_rwsem_writer_owned(k618_rwsem_rel, NULL);\n\tunregister_trace_android_vh_clear_rwsem_reader_owned(k618_rwsem_rel, NULL);\n\tunregister_trace_android_vh_record_rwsem_writer_owned(k618_rwsem_wacq, NULL);\n\treturn unregister_trace_android_vh_record_rwsem_reader_owned(k618_rwsem_racq, NULL);\n}\nstatic inline int register_trace_android_vh_record_rwsem_lock_starttime(\n\tvoid (*fn)(void *, struct rw_semaphore *, unsigned long), void *data)\n{\n\tint ret;\n\n\tk618_rwsem_fn = fn;\n\tk618_rwsem_data = data;\n\tret = register_trace_android_vh_record_rwsem_reader_owned(k618_rwsem_racq, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_record_rwsem_writer_owned(k618_rwsem_wacq, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_clear_rwsem_reader_owned(k618_rwsem_rel, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_clear_rwsem_writer_owned(k618_rwsem_rel, NULL);\n\tif (ret)\n\t\tunregister_trace_android_vh_record_rwsem_lock_starttime(fn, data);\n\treturn ret;\n}\n"),
    ("sched_assist_locking.c", "#include <trace/hooks/dtask.h>\n", "#include <trace/hooks/dtask.h>\n#include <trace/hooks/rwsem.h>\n\n/*\n * op13: android16-6.12's android_vh_record_{mutex,rtmutex,rwsem,pcpu_rwsem}_*starttime(lock,\n * jiffies) on 6.18's lock acquired/released hooks (scripts/oplus_fix.py): the handler gets jiffies\n * when the lock is taken and 0 when it is let go, as the 6.12 hooks called it\n */\n#define K618_LOCKTIME(name, type, acq, rel)\t\t\t\t\t\t\\\nstatic void (*k618_##name##_fn)(void *, type *, unsigned long);\t\t\t\\\nstatic void *k618_##name##_data;\t\t\t\t\t\t\t\\\nstatic void k618_##name##_acq(void *u, type *l)\t\t\t\t\t\\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn(k618_##name##_data, l, jiffies);\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic void k618_##name##_rel(void *u, type *l)\t\t\t\t\t\\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn(k618_##name##_data, l, 0);\t\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic inline int register_trace_android_vh_##name(void (*fn)(void *, type *, unsigned long), void *data) \\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tint ret;\t\t\t\t\t\t\t\t\t\\\n\t\t\t\t\t\t\t\t\t\t\t\\\n\tk618_##name##_fn = fn;\t\t\t\t\t\t\t\t\\\n\tk618_##name##_data = data;\t\t\t\t\t\t\t\\\n\tret = register_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n\tif (ret)\t\t\t\t\t\t\t\t\t\\\n\t\treturn ret;\t\t\t\t\t\t\t\t\\\n\tret = register_trace_android_vh_##rel(k618_##name##_rel, NULL);\t\t\\\n\tif (ret)\t\t\t\t\t\t\t\t\t\\\n\t\tunregister_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n\treturn ret;\t\t\t\t\t\t\t\t\t\\\n}\t\t\t\t\t\t\t\t\t\t\t\\\nstatic inline int unregister_trace_android_vh_##name(void (*fn)(void *, type *, unsigned long), void *data) \\\n{\t\t\t\t\t\t\t\t\t\t\t\\\n\tunregister_trace_android_vh_##rel(k618_##name##_rel, NULL);\t\t\t\\\n\treturn unregister_trace_android_vh_##acq(k618_##name##_acq, NULL);\t\t\\\n}\nK618_LOCKTIME(record_mutex_lock_starttime, struct mutex, mutex_lock_acquired, mutex_lock_released)\nK618_LOCKTIME(record_rtmutex_lock_starttime, struct rt_mutex, rtmutex_lock_acquired, rtmutex_lock_released)\n#ifdef CONFIG_PCPU_RWSEM_LOCKING_PROTECT\nK618_LOCKTIME(record_pcpu_rwsem_starttime, struct percpu_rw_semaphore, pcpu_rwsem_lock_acquired,\n\tpcpu_rwsem_lock_released)\n#endif\n\n/* an rwsem: taken by a reader or the writer, let go by either */\nstatic void (*k618_rwsem_fn)(void *, struct rw_semaphore *, unsigned long);\nstatic void *k618_rwsem_data;\nstatic void k618_rwsem_racq(void *u, struct rw_semaphore *s, struct list_head *w)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, jiffies);\n}\nstatic void k618_rwsem_wacq(void *u, struct rw_semaphore *s)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, jiffies);\n}\nstatic void k618_rwsem_rel(void *u, struct rw_semaphore *s)\n{\n\tk618_rwsem_fn(k618_rwsem_data, s, 0);\n}\nstatic inline int unregister_trace_android_vh_record_rwsem_lock_starttime(\n\tvoid (*fn)(void *, struct rw_semaphore *, unsigned long), void *data)\n{\n\tunregister_trace_android_vh_clear_rwsem_writer_owned(k618_rwsem_rel, NULL);\n\tunregister_trace_android_vh_clear_rwsem_reader_owned(k618_rwsem_rel, NULL);\n\tunregister_trace_android_vh_record_rwsem_writer_owned(k618_rwsem_wacq, NULL);\n\treturn unregister_trace_android_vh_record_rwsem_reader_owned(k618_rwsem_racq, NULL);\n}\nstatic inline int register_trace_android_vh_record_rwsem_lock_starttime(\n\tvoid (*fn)(void *, struct rw_semaphore *, unsigned long), void *data)\n{\n\tint ret;\n\n\tk618_rwsem_fn = fn;\n\tk618_rwsem_data = data;\n\tret = register_trace_android_vh_record_rwsem_reader_owned(k618_rwsem_racq, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_record_rwsem_writer_owned(k618_rwsem_wacq, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_clear_rwsem_reader_owned(k618_rwsem_rel, NULL);\n\tif (!ret)\n\t\tret = register_trace_android_vh_clear_rwsem_writer_owned(k618_rwsem_rel, NULL);\n\tif (ret)\n\t\tunregister_trace_android_vh_record_rwsem_lock_starttime(fn, data);\n\treturn ret;\n}\n"),
    # sched_assist's copies of fair.c: 6.18's sched.h has attach_task()/attach_one_task() itself
    # (static inline) and declares sysctl_sched_migration_cost (const_debug is gone): the copies
    # out of the way, the module's migration cost its own variable
    ("sa_balance.c",
     "const_debug unsigned int sysctl_sched_migration_cost\t= 500000UL;\n",
     "/* op13: its own copy; sched.h declares the kernel's (scripts/oplus_fix.py) */\n"
     "static unsigned int k618_sched_migration_cost = 500000UL;\n"
     "#define sysctl_sched_migration_cost k618_sched_migration_cost\n"),
    ("sa_balance.c",
     "static void attach_task(struct rq *rq, struct task_struct *p)\n{\n",
     "static void __maybe_unused k618_fair_attach_task(struct rq *rq, struct task_struct *p)\n{\n"),
    ("sa_balance.c",
     "static void attach_one_task(struct rq *rq, struct task_struct *p)\n{\n",
     "static void __maybe_unused k618_fair_attach_one_task(struct rq *rq, struct task_struct *p)\n{\n"),
    # Wi-Fi (qcacld sme_api.c, OnePlus' extended latency levels): its two ini items are missing from
    # struct cfg_values in this build ("no member named '__CFG_LATENCY_FLAGS_LOW_EXT_internal'"):
    # their defaults (cfg_mlme_fe_wlm.h's CFG_DEFAULT_*_EXT_FLAGS) instead of the ini lookup
    ("sme_api.c", "cfg_get(mac->psoc, CFG_LATENCY_FLAGS_LOW_EXT)", "\"0x184c03\" /* op13 */"),
    ("sme_api.c", "cfg_get(mac->psoc, CFG_LATENCY_FLAGS_ULTLOW_EXT)", "\"0x180c03\" /* op13 */"),
    # the wireless pen (built from its Makefile) uses oplus_project.h's boot modes (FACTORY_MODE),
    # which exist under OnePlus' Qualcomm platform switch, as its kernel config had it
    ("oplus_cps8601.c",
     "#ifndef CONFIG_DISABLE_OPLUS_FUNCTION\n#include <soc/oplus/system/oplus_project.h>",
     "#ifndef CONFIG_OPLUS_SYSTEM_KERNEL_QCOM\t/* op13: scripts/oplus_fix.py */\n#define CONFIG_OPLUS_SYSTEM_KERNEL_QCOM 1\n#endif\n"
     "#ifndef CONFIG_DISABLE_OPLUS_FUNCTION\n#include <soc/oplus/system/oplus_project.h>"),
    # cnss2 walks the chip's "iommu-addresses" trusting of_translate_dma_region() to move on: on an
    # entry that does not translate it returns NULL, and the loop went on from NULL for good
    # (test-55: the first stage stuck, CPU 6 in it). Such an entry ends the walk, as in the kernel's
    # of_iommu (tools/prep_common.sh, which also makes these entries translate again)
    ("cnss2/pci_qcom.c",
     "\t\tmaps = of_translate_dma_region(pci_dev->dev.of_node, maps,\n\t\t\t\t\t       &iova, &length);\n",
     "\t\tmaps = of_translate_dma_region(pci_dev->dev.of_node, maps,\n\t\t\t\t\t       &iova, &length);\n"
     "\t\tif (!maps) {\t/* op13: does not translate (scripts/oplus_fix.py) */\n"
     "\t\t\tof_node_put(of_node);\n\t\t\treturn -EINVAL;\n\t\t}\n"),
    # The OnePlus 13's EVA (camera vision engine), video and camera drivers have to be its own: CamX
    # and the media stack speak their interfaces. Two 6.18 changes kept EVA and video from building
    # (the OnePlus 15's were used, test-57: camera black, the provider stuck in CamX's open).
    # EVA looks a dma-buf fd up in its client's file table: files_lookup_fd_rcu() and the old
    # get_file_rcu() are gone; the same with 6.18's helpers (as fs/file.c's __fget_files_rcu: take
    # a reference, then check the slot still holds that file)
    ("eva/msm_cvp_buf.c",
     "loop:\n#if (LINUX_VERSION_CODE < KERNEL_VERSION(5, 13, 0))\n\tfile = fcheck_files(files, fd);\n#else\n"
     "\tfile = files_lookup_fd_rcu(files, fd);\n#endif\n\tif (file) {\n"
     "\t\t/* File object ref couldn't be taken.\n"
     "\t\t * dup2() atomicity guarantee is the reason\n"
     "\t\t * we loop to catch the new file (or NULL pointer)\n\t\t */\n"
     "\t\tif (file->f_mode & mask)\n\t\t\tfile = NULL;\n"
     "\t\telse if (!get_file_rcu(file))\n\t\t\tgoto loop;\n\t}\n",
     "loop:\n"
     "\t/* op13: 6.18's lookup, as fs/file.c's __fget_files_rcu (scripts/oplus_fix.py) */\n"
     "\tfile = files_lookup_fd_raw(files, fd);\n"
     "\tif (file && (file->f_mode & mask)) {\n\t\tfile = NULL;\n"
     "\t} else if (file) {\n"
     "\t\tif (!file_ref_get(&file->f_ref))\n\t\t\tgoto loop;\n"
     "\t\tif (unlikely(files_lookup_fd_raw(files, fd) != file)) {\n"
     "\t\t\tfput(file);\n\t\t\tgoto loop;\n\t\t}\n\t}\n"),
    # video: devm_pm_opp_attach_genpd() is gone (6.13): the rails' power domains attached and linked
    # with devm_pm_domain_attach_list(), as the OnePlus 15's driver does
    ("vidc/src/resources.c",
     "\t/* populate opp power domains(for rails) */\n"
     "\trc = devm_pm_opp_attach_genpd(&core->pdev->dev, opp_tbl, &opp_vdevs);\n"
     "\trc = -EINVAL;\n\tif (rc)\n\t\treturn rc;\n\n"
     "\t/* create device_links b/w consumer(dev) and multiple suppliers(mx, mmcx) */\n"
     "\tfor (cnt = 0; cnt < opp_count; cnt++) {\n"
     "\t\trc = devm_opp_dl_get(&core->pdev->dev, opp_vdevs[cnt]);\n"
     "\t\tif (rc) {\n"
     "\t\t\td_vpr_e(\"%s: failed to create dl: %s\\n\",\n"
     "\t\t\t\t__func__, dev_name(opp_vdevs[cnt]));\n"
     "\t\t\treturn rc;\n\t\t}\n\t}\n",
     "#if (KERNEL_VERSION(6, 13, 0) <= LINUX_VERSION_CODE)\t/* op13 (scripts/oplus_fix.py) */\n"
     "\t{\n"
     "\t\tstruct dev_pm_domain_attach_data k618_pd = {\n"
     "\t\t\t.pd_names = opp_tbl,\n\t\t\t.num_pd_names = opp_count,\n"
     "\t\t\t.pd_flags = PD_FLAG_DEV_LINK_ON,\n\t\t};\n"
     "\t\tstruct dev_pm_domain_list *k618_pds;\n\n"
     "\t\trc = devm_pm_domain_attach_list(&core->pdev->dev, &k618_pd, &k618_pds);\n"
     "\t\tif (rc < 0)\n\t\t\treturn rc;\n\t\trc = 0;\n\t}\n"
     "#else\n"
     "\t/* populate opp power domains(for rails) */\n"
     "\trc = devm_pm_opp_attach_genpd(&core->pdev->dev, opp_tbl, &opp_vdevs);\n"
     "\trc = -EINVAL;\n\tif (rc)\n\t\treturn rc;\n\n"
     "\t/* create device_links b/w consumer(dev) and multiple suppliers(mx, mmcx) */\n"
     "\tfor (cnt = 0; cnt < opp_count; cnt++) {\n"
     "\t\trc = devm_opp_dl_get(&core->pdev->dev, opp_vdevs[cnt]);\n"
     "\t\tif (rc) {\n"
     "\t\t\td_vpr_e(\"%s: failed to create dl: %s\\n\",\n"
     "\t\t\t\t__func__, dev_name(opp_vdevs[cnt]));\n"
     "\t\t\treturn rc;\n\t\t}\n\t}\n#endif\n"),
    # charger v2's chg_err: each call site at most 3 lines per 10 s. Its init works retry every
    # 100 ms until the ADSP's gauge/buck appear (second stage) and printed 2 errors each time, which
    # overwrote pstore's whole 160 KB ring in ~40 s (test-55: the stuck first stage's log lost)
    ("v2/include/oplus_chg.h",
     '#define chg_err(fmt, ...)                                                      \\\n'
     '\t({                                                                     \\\n'
     '\t\tif (oplus_log_level >= LOG_LEVEL_ERR)                          \\\n'
     '\t\t\tprintk(KERN_ERR "[ERROR]: OPLUS_CHG" pr_fmt(fmt),      \\\n',
     '/* op13: rate-limited per call site (scripts/oplus_fix.py) */\n'
     '#define chg_err(fmt, ...)                                                      \\\n'
     '\t({                                                                     \\\n'
     '\t\tstatic DEFINE_RATELIMIT_STATE(k618_chg_rs, 10 * HZ, 3);       \\\n'
     '\t\tif (oplus_log_level >= LOG_LEVEL_ERR && __ratelimit(&k618_chg_rs)) \\\n'
     '\t\t\tprintk(KERN_ERR "[ERROR]: OPLUS_CHG" pr_fmt(fmt),      \\\n'),
    # locking_strategy keeps its per-mutex count in struct mutex's OEM data: an array (u64[2]) in
    # 6.6/6.12, so lock->android_oem_data1 was its address; one u64 in 6.18, so the same cast read
    # the value (0) as the pointer and every contended mutex oopsed (test-54 boot-full, deadlocked)
    ("synchronize/mutex.h",
     "return (struct oplus_mutex *)lock->android_oem_data1;",
     "return (struct oplus_mutex *)&lock->android_oem_data1;\t/* op13: a u64 in 6.18 (scripts/oplus_fix.py) */"),
    # locking_strategy's rwsem.c keeps its own rwsem_owner()/is_rwsem_reader_owned(), which 6.18's
    # linux/rwsem.h now declares (non-static): its copies under their own names
    ("rwsem.c",
     "static inline struct task_struct *rwsem_owner(struct rw_semaphore *sem)\n",
     "/* op13: 6.18's rwsem.h declares these (scripts/oplus_fix.py) */\n"
     "#define rwsem_owner k618_rwsem_owner\n#define is_rwsem_reader_owned k618_is_rwsem_reader_owned\n"
     "static inline struct task_struct *rwsem_owner(struct rw_semaphore *sem)\n"),
    # cnss2 (the OnePlus 13's, which its DT matches) for the OnePlus 15's Wi-Fi driver, which calls
    # two 6.12 platform functions it lacks: the chip name is not needed by this cnss2 (it picks
    # its firmware itself), and the OnePlus 13 has no TSF GPIO in its DT
    ("cnss2/main.c",
     "EXPORT_SYMBOL(cnss_remove_pm_qos);\n",
     "EXPORT_SYMBOL(cnss_remove_pm_qos);\n\n"
     "/* op13: what the OnePlus 15's qcacld calls (scripts/oplus_fix.py) */\n"
     "struct cnss_wlan_host_param;\n"
     "int cnss_set_host_param(struct device *dev, struct cnss_wlan_host_param *param)\n"
     "{\n\treturn 0;\n}\nEXPORT_SYMBOL(cnss_set_host_param);\n\n"
     "int cnss_get_wlan_tsf_gpio(struct device *dev)\n{\n\treturn -EINVAL;\n}\n"
     "EXPORT_SYMBOL(cnss_get_wlan_tsf_gpio);\n",
     "cnss_set_host_param"),
    # ... and kiwi's qcacld also asks for the PCIe IOVA range by device, as the OnePlus 15's cnss2
    # exports it (the same wrapper over cnss_pci_get_iova(), which this cnss2 has)
    ("cnss2/pci.c",
     "int cnss_pci_get_iova_ipa(struct cnss_pci_data *pci_priv, u64 *addr, u64 *size)\n",
     "/* op13: as the OnePlus 15's cnss2 (scripts/oplus_fix.py) */\n"
     "int cnss_pci_get_iova_info(struct device *dev, u64 *addr, u64 *size)\n{\n"
     "\treturn cnss_pci_get_iova(cnss_get_pci_priv(to_pci_dev(dev)), addr, size);\n}\n"
     "EXPORT_SYMBOL(cnss_pci_get_iova_info);\n\n"
     "int cnss_pci_get_iova_ipa(struct cnss_pci_data *pci_priv, u64 *addr, u64 *size)\n",
     "cnss_pci_get_iova_info"),
    # the OnePlus 13's charger (oplus_chg_v2) as OnePlus published it does not compile: in
    # hl7603_hardware_init() the "ic_dev is NULL" check lacks its closing brace, and every function
    # after it was "not allowed here" -- so the OnePlus 15's charger was used (its copy has the brace)
    ("charger_ic/oplus_hal_hl7603.c",
     "\t\tchg_err(\"chip->ic_dev is NULL\\n\");\n\t\treturn 0;\n\t\n\trc = hl7603_read(chip, DEV_ID_REV_REG,",
     "\t\tchg_err(\"chip->ic_dev is NULL\\n\");\n\t\treturn 0;\n\t}\t/* op13: the brace OnePlus' source lacks (scripts/oplus_fix.py) */\n\n\trc = hl7603_read(chip, DEV_ID_REV_REG,"),
    # the OnePlus 13's btfm_slim_codec (OnePlus' BT timeout/init-error counters, which the OnePlus
    # 15's copy lacks) ends its slimbus ID table without the empty entry: 6.18's modpost refuses it
    # ("btfm_slim_id[] is not terminated with a NULL entry"), and a match would read past it
    ("bt-kernel/slimbus/btfm_slim.c",
     "\t.prod_code = 0x220,\n\t.dev_index = 0x1,\n\t.instance = 0x0,\n\t}\n};\n",
     "\t.prod_code = 0x220,\n\t.dev_index = 0x1,\n\t.instance = 0x0,\n\t},\n"
     "\t{ }\t/* op13: the table's end (scripts/oplus_fix.py) */\n};\n"),
    # kp_freeze_detect's trace event lacks a ';' after one __assign_str() (6.6's macro was a block)
    ("kp_freeze_detect/kp_freeze_trace.h",
     "\t\t__assign_str(event_id)\n\t\t__assign_str(comm);\n",
     "\t\t__assign_str(event_id);\t/* op13: the missing ';' (scripts/oplus_fix.py) */\n\t\t__assign_str(comm);\n"),
    # WALT's governor (sched-walt, scripts/build_walt.sh) sets up cpufreq_bouncing for each policy it
    # takes over (cb_stuff_init(), as the OnePlus 15's copy exports it; the OnePlus 13's has the same
    # function, guarded against a second call, without the export)
    ("cpufreq_bouncing/cpufreq_bouncing.c",
     "\t\tinit_complete = true;\n\t}\n}\n\n\nstatic int cpufreq_bouncing_register_callback(",
     "\t\tinit_complete = true;\n\t}\n}\nEXPORT_SYMBOL_GPL(cb_stuff_init);\t/* op13: for WALT (scripts/oplus_fix.py) */\n\n\n"
     "static int cpufreq_bouncing_register_callback(",
     "EXPORT_SYMBOL_GPL(cb_stuff_init);"),
    # --- the OnePlus 13's scheduler group (sched_assist and the modules on it), as OnePlus' own 6.12
    # port (the OnePlus 15's tree) adapted the same code ---
    # OnePlus' 6.6 kernel had a hook of its own in account_process_tick() (android_vh_account_process_
    # tick_gran), which sched_assist forwarded as a hook it defined itself, android_vh_account_task_time
    # with a tick count. 6.18 (as 6.12) has android_vh_account_task_time in the kernel, fired from
    # there, one tick a call, without the count: sched_assist defines and fires nothing, its consumers
    # take the kernel's hook (cputime.h now includes it), a tick each
    ("sched/sched_assist/cputime.h",
     "DECLARE_HOOK(android_vh_account_task_time,\n"
     "\tTP_PROTO(struct task_struct *p, struct rq *rq, int user_tick, int ticks),\n"
     "\tTP_ARGS(p, rq, user_tick, ticks));\n",
     "/* op13: the kernel's own since 6.12: 3 arguments, one tick a call (scripts/oplus_fix.py) */\n"
     "#include <trace/hooks/sched.h>\n"),
    ("sched/sched_assist/sa_common.c",
     '#define CREATE_TRACE_POINTS\n#include "cputime.h"\n#undef CREATE_TRACE_POINTS\n',
     '#include "cputime.h"\t/* op13: the kernel\'s hook, not created here (scripts/oplus_fix.py) */\n'),
    ("sched/sched_assist/sa_common.c",
     "\tif (*ticks == 0)\n\t\treturn;\n\ttrace_android_vh_account_task_time(p, rq, user_tick, *ticks);\n}\n"
     "EXPORT_TRACEPOINT_SYMBOL_GPL(android_vh_account_task_time);\n",
     "\t/* op13: the kernel fires android_vh_account_task_time itself (scripts/oplus_fix.py) */\n}\n"),
    ("sched/sched_assist/sched_assist.c",
     "\tREGISTER_TRACE_VH(android_vh_account_process_tick_gran, android_vh_account_process_tick_gran_handler);\n",
     "\t/* op13: no android_vh_account_process_tick_gran in 6.18 (scripts/oplus_fix.py) */\n"),
    # (sched_info takes android_vh_irqtime_account_process_tick: CONFIG_IRQ_TIME_ACCOUNTING=y, 4 arguments still)
    ("sched/task_cpustats/task_cpustats.c",
     "static void account_task_time_handler(void *data, struct task_struct *p, struct rq *rq, int user_tick, int ticks)\n{\n",
     "static void account_task_time_handler(void *data, struct task_struct *p, struct rq *rq, int user_tick)\n{\n"
     "\tconst int ticks = 1;\t/* op13: the kernel's hook, a tick a call (scripts/oplus_fix.py) */\n"),
    ("sched/task_sched/task_sched_info.c",
     "static void ctp_send_message_handler(void *data, struct task_struct *p, struct rq *rq, int user_tick, int ticks)\n",
     "static void ctp_send_message_handler(void *data, struct task_struct *p, struct rq *rq, int user_tick)\t/* op13 */\n"),
    ("detection/task_overtime.c",
     "void get_task_time_handler(void *data, struct task_struct *task, struct rq *rq, int tick, int ticks)\n{\n",
     "void get_task_time_handler(void *data, struct task_struct *task, struct rq *rq, int tick)\t/* op13: 6.18's hook */\n{\n"
     "\tconst int ticks = 1;\t/* op13: a tick a call (scripts/oplus_fix.py) */\n"),
    # (task_struct's cpu is thread_info's on arm64 since 5.16)
    ("detection/task_overtime.c", "cpufreq_quick_get(task->cpu)", "cpufreq_quick_get(task_cpu(task))"),
    # the sched_stat_runtime tracepoint has no vruntime since 6.8
    ("sched/sched_assist/sa_priority.c",
     "void android_vh_sched_stat_runtime_handler(void *unused, struct task_struct *task, u64 delta_exec, u64 vruntime)\n",
     "void android_vh_sched_stat_runtime_handler(void *unused, struct task_struct *task, u64 delta_exec)\t/* op13: 6.8+ */\n"),
    ("sched/sched_assist/sa_priority.h",
     "void android_vh_sched_stat_runtime_handler(void *unused, struct task_struct *task, u64 delta_exec, u64 vruntime);\n",
     "void android_vh_sched_stat_runtime_handler(void *unused, struct task_struct *task, u64 delta_exec);\t/* op13: 6.8+ */\n"),
    # 6.18's android_vh_reweight_entity runs inside reweight_entity() and hands the weight to set;
    # OnePlus' handler reweights the entity itself, as their 6.6 kernel's hook let it. OnePlus' 6.12
    # port leaves it unregistered ("until the gki image contains the android_vh_reweight_entity newest
    # hook"): so here
    ("sched/sched_assist/sched_assist.c",
     "#ifdef CONFIG_OPLUS_SCHED_GROUP_OPT\n\tREGISTER_TRACE_VH(android_vh_reweight_entity, android_vh_reweight_entity_handler);\n#endif\n",
     "#if 0\t/* op13: 6.18's hook sets the weight inside reweight_entity(), as OnePlus' 6.12 port (scripts/oplus_fix.py) */\n"
     "\tREGISTER_TRACE_VH(android_vh_reweight_entity, android_vh_reweight_entity_handler);\n#endif\n"),
    # task_is_runnable() is the kernel's own since 6.12 (on the runqueue, not delayed): OnePlus' (on
    # the runqueue, TASK_RUNNING, not running) under its own name, as their 6.12 port has it
    ("sched/sched_assist/sa_common.c",
     "bool task_is_runnable(struct task_struct *task)\n{\n",
     "bool oplus_task_is_runnable(struct task_struct *task)\t/* op13: the kernel has task_is_runnable() */\n{\n"),
    ("sched/sched_assist/sa_common.h",
     "bool task_is_runnable(struct task_struct *task);\n",
     "bool oplus_task_is_runnable(struct task_struct *task);\t/* op13 */\n"),
    ("sched/sched_assist/sa_balance.c",
     "if (!task_is_runnable(task))",
     "if (!oplus_task_is_runnable(task))"),
    # android_vh_dm_bufio_shrink_scan_bypass is not in 6.18 (nor in 6.12): OnePlus' block-I/O UX boost
    # of dm-bufio's shrinker has nothing to attach to
    ("sched/sched_assist/sa_blockio.c",
     "\tret = register_trace_android_vh_dm_bufio_shrink_scan_bypass(\n\t\t\tdm_bufio_shrink_scan_bypass_handler, NULL);\n",
     "\t(void)dm_bufio_shrink_scan_bypass_handler;\t/* op13: no such hook in 6.18 (scripts/oplus_fix.py) */\n"),
    ("sched/sched_assist/sa_blockio.c",
     "\tunregister_trace_android_vh_dm_bufio_shrink_scan_bypass(\n\t\t\tdm_bufio_shrink_scan_bypass_handler, NULL);\n",
     ""),
    # binder's hook for releasing a process's extra work list is android_vh_binder_check_special_work
    # in 6.18 (the same arguments, the same place in binder_deferred_release(); OnePlus' 6.12 code
    # uses it): without it the foreground list's work would not be released with its process
    ("ipc/binder_sched.c",
     "register_trace_android_vh_binder_release_special_work(",
     "register_trace_android_vh_binder_check_special_work(\t/* op13: 6.18's name */"),
    # f2fs_write_checkpoint's tracepoint passes the phase (fs/f2fs/f2fs.h enum f2fs_cp_phase) since
    # 6.13, not its message: the message back from it
    ("io_metrics/f2fs_metrics.c",
     "#else\nstatic void cb_f2fs_write_checkpoint(void *ignore, struct super_block *sb,\n"
     "                                               int reason, const char *msg)\n"
     "#endif /* LINUX_VERSION_CODE <= KERNEL_VERSION(5, 15, 0) */\n{\n",
     "#else\nstatic void cb_f2fs_write_checkpoint(void *ignore, struct super_block *sb,\n"
     "                                               int reason, u16 phase)\t/* op13: 6.13+ */\n"
     "#endif /* LINUX_VERSION_CODE <= KERNEL_VERSION(5, 15, 0) */\n{\n"
     "    /* op13: CP_PHASE_START_BLOCK_OPS, _FINISH_BLOCK_OPS, _FINISH_CHECKPOINT (scripts/oplus_fix.py) */\n"
     "    const char *msg = phase == 0 ? \"start block_ops\" : phase == 2 ? \"finish checkpoint\" : \"finish block_ops\";\n"),
    # nxp-nci asks an unpublished OnePlus module for the NFC chip's ID (get_nfc_id); OnePlus' own
    # earlier way, left commented out under it, reads the ID GPIO in this driver
    ("nfc/i2c_drv.c",
     '                    gpio_value = get_nfc_id();\n                    pr_info("%s, final gpio_value = %d\\n", __func__, gpio_value);\n                    if (gpio_value == INVALID_ID) {\n                        for (int delay = 0; delay < 6; delay++) {\n                            msleep(500);\n                            gpio_value = get_nfc_id();\n                            if(gpio_value != INVALID_ID) {\n                                pr_info("retry times = %d\\n",delay);\n                                break;\n                            }\n                        }\n                        if(gpio_value == INVALID_ID) {\n                            err = -EINVAL;\n                        }\n                    }\n                    //err = nxp_nfc_get_gpio_value_three(dev, &gpio_value);\n',
     '                    /* op13: the ID read from its GPIO here, as OnePlus did before get_nfc_id(),\n                     * whose OnePlus module is not published (scripts/oplus_fix.py) */\n                    err = nxp_nfc_get_gpio_value_three(dev, &gpio_value);\n                    pr_info("%s, final gpio_value = %d\\n", __func__, gpio_value);\n'),
    ("nfc/i2c_drv.c",
     "#if 0\nstatic int nxp_nfc_get_gpio_value(struct device_node *np, int *gpio_value)\n",
     "#if 1\t/* op13: OnePlus' ID-GPIO readers, used again (scripts/oplus_fix.py) */\n"
     "static int nxp_nfc_get_gpio_value(struct device_node *np, int *gpio_value)\n"),
]


def main(argv):
    n = 0
    for dp, _dn, fns in os.walk(argv[1]):
        for edit in EDITS:
            name, old, new = edit[:3]
            unless = edit[3] if len(edit) > 3 else None
            # a name with a directory part matches files whose path ends with it
            base = os.path.basename(name)
            if base not in fns or ("/" in name and not os.path.join(dp, base).endswith("/" + name)):
                continue
            path = os.path.join(dp, base)
            text = open(path, encoding="utf-8", errors="surrogateescape").read()
            if new and new in text:
                continue		# already applied (some edits keep the old text inside the new)
            if unless and unless in text:
                continue		# the file has it already (the OnePlus 15's copy)
            if old in text:
                open(path, "w", encoding="utf-8", errors="surrogateescape", newline="").write(text.replace(old, new))
                n += 1
    if n:
        print(f"oplus_fix: {n} changes in {argv[1]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
