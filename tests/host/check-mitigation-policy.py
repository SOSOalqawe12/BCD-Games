#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SOSOalqawe12 fork: tools/patch-wine-mitigation-policy.py and where its fix reaches games.

  - the patch applies to the pinned wine's kernelbase/process.c, once (a second run changes nothing),
    and the patched GetProcessMitigationPolicy zeroes the buffer and refuses a NULL one;
  - the i386 farm gets it before its build, and process.c is put back after;
  - tools/build-wine-extra-dlls.sh builds kernelbase-aslr.dll and restores process.c;
  - WineProcessBridge.m links it only with MADEIRA_ASLR_FIX=1, after the fiber trace, and unsets it;
  - the game page's Compatibility switch writes env.MADEIRA_ASLR_FIX.
"""
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

root = Path(__file__).resolve().parents[2]
tool = root / 'tools/patch-wine-mitigation-policy.py'
failures = 0


def check(what, ok):
    global failures
    print(('ok   ' if ok else 'FAIL ') + what)
    failures += not ok


src = root / 'wine/dlls/kernelbase/process.c'
if src.is_file():
    with tempfile.TemporaryDirectory(prefix='mitigation-') as tmp:
        dst = Path(tmp) / 'dlls/kernelbase/process.c'
        dst.parent.mkdir(parents=True)
        shutil.copy(src, dst)
        first = subprocess.run([sys.executable, str(tool), tmp], capture_output=True, text=True)
        once = dst.read_text()
        second = subprocess.run([sys.executable, str(tool), tmp], capture_output=True, text=True)
        check('the patch applies to the pinned wine', first.returncode == 0 and 'patched' in first.stdout)
        check('a second run changes nothing', second.returncode == 0 and dst.read_text() == once)
        body = once[once.index('GetProcessMitigationPolicy( HANDLE process'):]
        body = body[:body.index('\n}\n')]
        check('the buffer is zeroed and TRUE returned', 'memset( buffer, 0, length );\n    return TRUE;' in body)
        check('a NULL buffer fails with ERROR_INVALID_PARAMETER', 'if (!buffer || !length)' in body
              and 'SetLastError( ERROR_INVALID_PARAMETER );' in body)
        check('the stub is gone', '): stub\\n"' not in body)
else:
    print('SKIP: wine submodule not checked out')

ci = (root / '.github/workflows/build-ipa.yml').read_text()
farm = ci.index('- name: Build i386 farm (WoW64)')
check('the i386 farm is patched before it is built',
      0 < ci.index('python3 tools/patch-wine-mitigation-policy.py wine') < farm)
check('... and process.c is put back after it',
      ci.index('git -C wine checkout -- dlls/ntdll/env.c dlls/kernelbase/process.c') > farm)

sh = (root / 'tools/build-wine-extra-dlls.sh').read_text()
asl = sh[sh.index('if [ -n "$ASL" ]; then'):]
asl = asl[:asl.index('\nfi\n') + 4]
check('kernelbase-aslr.dll is built, stripped, verified, then shipped',
      'patch-wine-mitigation-policy.py" "$R/wine"' in asl and '--strip-debug "$SHIP/kernelbase-aslr.dll.tmp"' in asl
      and '--verify "$SHIP/kernelbase-aslr.dll.tmp" "$SHIP/kernelbase.dll"' in asl
      and 'mv "$SHIP/kernelbase-aslr.dll.tmp" "$SHIP/kernelbase-aslr.dll"' in asl)
check('... and process.c is restored', 'git -C "$R/wine" checkout -- dlls/kernelbase/process.c' in asl)

bridge = (root / 'app/Madeira/WineProcessBridge.m').read_text()
check('the app links it only with MADEIRA_ASLR_FIX=1 and not over the fiber trace',
      "if (aslrFix && aslrFix[0] == '1' && !(fiberOn && fiberOn[0] == '1')) {" in bridge
      and bridge.index('getenv("MADEIRA_FIBER_TRACE");') < bridge.index('getenv("MADEIRA_ASLR_FIX");'))
check('a game\'s switch is its own', 'unsetenv("MADEIRA_ASLR_FIX");' in bridge)
page = (root / 'app/Madeira/BCDGamePageLive.swift').read_text()
check('Compatibility writes env.MADEIRA_ASLR_FIX', 'case "aslrfix": setLine(game, "env.MADEIRA_ASLR_FIX", on ? "1" : "")' in page)

print('PASS' if not failures else 'FAILED: %d' % failures)
sys.exit(1 if failures else 0)
