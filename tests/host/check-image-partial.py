#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SOSOalqawe12 fork [image-partial]: mprotect_exec never copies a PE image that is not mapped to its end.

ONE PIECE Bounty Rush's XIGNCODE3 loader unmaps its own image and maps a smaller block with a PE header
at the old base; the whole-image pool copy then read past the mapping from host code and hung.
  - ios_range_fully_mapped asks the kernel region by region (submaps descended), stops at a hole,
    never loops without progress, and does not judge protection;
  - mprotect_exec runs it after the backward MZ scan and its range check, before the anonymous-RWX
    path and before the whole-image memcpy, and on a hole drops the image so that path takes it.
"""
import re
import sys
from pathlib import Path

src = (Path(__file__).resolve().parents[2] / 'build/ntdll-unix/virtual_ios.c').read_text()
failures = 0


def check(what, ok):
    global failures
    print(('ok   ' if ok else 'FAIL ') + what)
    failures += not ok


h = src.index('static int ios_range_fully_mapped( const void *p, size_t len )')
helper = src[h:src.index('\n}\n', h)]
check('the helper descends submaps', 'mach_vm_region_recurse(' in helper and 'if (!ri.is_submap) break;' in helper)
check('... reports a hole', 'if (ra > at) return 0;' in helper)
check('... never loops without progress', 'if (ra + rs <= at) return 0;' in helper)
check('... does not judge protection', 'VM_PROT_READ' not in helper)
check('... is defined before mprotect_exec', h < src.index('static inline int mprotect_exec( void *base, size_t size, int unix_prot )'))

guard = src.index('if (image_base && image_size && !ios_range_fully_mapped( image_base, image_size ))')
block = src[guard:src.index('\n            }\n', guard)]
check('the guard drops the image', 'image_base = NULL;' in block and 'image_size = 0;' in block)
check('the guard does not read the image for a name', 'ios_pe_module_name' not in block)
scan = src.index('image_size = *(unsigned int *)(scan + pe_off + 0x50);')
outside = src.index('is OUTSIDE nearest image')
anon = src.index("            if (!image_base || !image_size)\n            {\n                /* iOS-Madeira: anonymous RWX request")
copy = src.index('memcpy((char *)jit_rw_base + offset, image_base, image_size);')
check('order: MZ scan, range check, guard, anonymous path, whole-image copy', scan < outside < guard < anon < copy)

print('PASS' if not failures else 'FAILED: %d' % failures)
sys.exit(1 if failures else 0)
