#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SOSOalqawe12 fork: GetProcessMitigationPolicy answers "no mitigation forced".

Some games stop at start with "Please disable Mandatory ASLR" (Windows Security ›
Exploit protection › Force randomization for images). They ask
GetProcessMitigationPolicy(ProcessASLRPolicy) and refuse to run when
EnableForceRelocateImages is set. Wine's kernelbase has it as a stub that logs a
FIXME and returns TRUE WITHOUT WRITING THE BUFFER, so the game reads whatever its
own stack held there: any set bit 1 reads as "Mandatory ASLR on", and other
policies (DEP, CFG, dynamic code, ...) read as random too.

The patched function zeroes the caller's buffer and returns TRUE: every policy
reads as not forced, as on a Windows PC with exploit protection at its defaults
for a program without its own mitigation settings. A NULL buffer or a zero
length fails with ERROR_INVALID_PARAMETER, as Windows does.

Where it reaches games:
  - the i386 farm (32-bit games): .github/workflows/build-ipa.yml patches the
    tree right before the farm is built and puts process.c back after it, as it
    does for tools/patch-wine-x64-machine.py;
  - 64-bit games: tools/build-wine-extra-dlls.sh builds kernelbase-aslr.dll from
    the patched tree NEXT TO the shipped kernelbase.dll, which stays; a game with
    env.MADEIRA_ASLR_FIX = 1 (Compatibility › "Fix 'disable Mandatory ASLR'")
    runs it as kernelbase.dll (app/Madeira/WineProcessBridge.m).

Usage:
  patch-wine-mitigation-policy.py WINE_SRC              patch the tree (idempotent)
  patch-wine-mitigation-policy.py --verify DLL SHIPPED  check a built DLL: it
      carries the fix and exports exactly the shipped kernelbase's names
"""
import importlib.util
import os
import sys

MARKER = "mitigation-policy-v1 sosoalqawe12"
FILE = "dlls/kernelbase/process.c"

OLD = """BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy( HANDLE process, PROCESS_MITIGATION_POLICY policy,
                                                          void *buffer, SIZE_T length )
{
    FIXME( "(%p, %u, %p, %Iu): stub\\n", process, policy, buffer, length );
    return TRUE;
}
"""

NEW = """/* """ + MARKER + """ (tools/patch-wine-mitigation-policy.py): the stub returned TRUE without
 * writing the buffer, so a game read its own stack as the policy ("Please disable Mandatory ASLR").
 * Every policy now reads as not forced. */
static const char madeira_mitigation_marker[] __attribute__((used)) = \"""" + MARKER + """\";

BOOL WINAPI /* DECLSPEC_HOTPATCH */ GetProcessMitigationPolicy( HANDLE process, PROCESS_MITIGATION_POLICY policy,
                                                          void *buffer, SIZE_T length )
{
    static LONG logged;

    if (logged < 8)
    {
        InterlockedIncrement( &logged );
        FIXME( "%s: (%p, %u, %p, %Iu): no mitigation forced\\n", madeira_mitigation_marker, process, policy, buffer, length );
    }
    if (!buffer || !length)
    {
        SetLastError( ERROR_INVALID_PARAMETER );
        return FALSE;
    }
    memset( buffer, 0, length );
    return TRUE;
}
"""


def patch(wine):
    path = os.path.join(wine, FILE)
    if not os.path.isfile(path):
        sys.exit("patch-wine-mitigation-policy: no " + path)
    text = open(path).read()
    if MARKER in text:
        print("already patched")
        return
    if text.count(OLD) != 1:
        sys.exit("patch-wine-mitigation-policy: the GetProcessMitigationPolicy stub was found %d times in %s"
                 % (text.count(OLD), path))
    open(path, "w").write(text.replace(OLD, NEW))
    print("patched " + path)


def verify(path, shipped):
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location("fibertrace", os.path.join(here, "patch-wine-kernelbase-fibertrace.py"))
    fibertrace = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fibertrace)
    d, machine, names = fibertrace.pe_exports(path)
    _, smachine, snames = fibertrace.pe_exports(shipped)
    bad = []
    if MARKER.encode() not in d:
        bad.append("no '%s' string: not built from the patched tree" % MARKER)
    if machine != smachine:
        bad.append("machine %#x, the shipped kernelbase.dll's is %#x" % (machine, smachine))
    if names != snames:
        bad.append("exports differ from the shipped kernelbase.dll: only here %s, only there %s"
                   % (sorted(names - snames)[:8], sorted(snames - names)[:8]))
    if bad:
        sys.exit("verify %s:\n  %s" % (path, "\n  ".join(bad)))
    print("verify %s: mitigation fix present, the %d exports of %s" % (path, len(names), shipped))


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "--verify":
        verify(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 2:
        patch(sys.argv[1])
    else:
        sys.exit(__doc__)
