#!/usr/bin/env python3
"""tools/patch-fex-ios-exec-views.py: ARM64EC runs executable section views it was never told about.

Checks that the patch applies to the pinned FEX sources once (idempotent), that the miss path
asks VirtualQuery outside IntervalsLock and adopts only committed MEM_MAPPED/MEM_IMAGE views with
an execute protection (no guard, no no-access), that the WOW64 build is untouched, that
MADEIRA_FEX_EXEC_VIEWS=0 turns it off, and that tools/build-xtajit64.sh applies it in both of its
patch sequences and restores the files afterwards.
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / 'FEX/Source/Windows/Common'
failures = []


def check(ok, what):
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures.append(what)


if not (COMMON / 'InvalidationTracker.cpp').exists():
    print('SKIP: FEX is not checked out')
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    for name in ('InvalidationTracker.h', 'InvalidationTracker.cpp'):
        shutil.copy(COMMON / name, Path(tmp) / name)
    spec = importlib.util.spec_from_file_location('p', ROOT / 'tools/patch-fex-ios-exec-views.py')
    sys.argv = ['p', tmp]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    cpp = (Path(tmp) / 'InvalidationTracker.cpp').read_text()
    h = (Path(tmp) / 'InvalidationTracker.h').read_text()
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)   # the second run must change nothing
    check((Path(tmp) / 'InvalidationTracker.cpp').read_text() == cpp, 'the patch is idempotent')

adopt = cpp[cpp.index('bool InvalidationTracker::AdoptExecutableView(uint64_t Address) {'):]
adopt = adopt[:adopt.index('\n}\n') + 3]
check('bool AdoptExecutableView(uint64_t Address);' in h and h.index('#if defined(ARCHITECTURE_arm64ec)\n  /* madeira-bcd: exec-views') < h.index('bool AdoptExecutableView'),
      'the helper is declared for the ARM64EC build only')
query = cpp[cpp.index('#else\nFEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {'):]
check(query.index('if (!XResult.Enclosed) {') < query.index('#if defined(ARCHITECTURE_arm64ec)') < query.index('Lock.unlock();')
      < query.index('if (AdoptExecutableView(Address)) {') < query.index('std::shared_lock Again(IntervalsLock);'),
      'the miss path drops the shared lock before adopting and queries again under a new one')
check(adopt.index('VirtualQuery(') < adopt.index('std::unique_lock Lock(IntervalsLock);') < adopt.index('LogMan::Msg::EFmt('),
      'VirtualQuery runs before IntervalsLock is taken and the log line after it is released')
check('Info.State != MEM_COMMIT || (Info.Type != MEM_MAPPED && Info.Type != MEM_IMAGE)' in adopt
      and '(Info.Protect & (PAGE_GUARD | PAGE_NOACCESS)) || !ProtHasExec(Info.Protect)' in adopt,
      'only committed section views with an execute protection are adopted')
check('if (ProtIsWritable(Info.Protect)) {\n      RWXIntervals.Insert(ViewInterval);' in adopt, 'a writable view is also an SMC interval')
check('getenv("MADEIRA_FEX_EXEC_VIEWS")' in adopt and "Opt[0] == '0'" in adopt and 'IntervalsLock.HeldByThisThread()' in adopt,
      'MADEIRA_FEX_EXEC_VIEWS=0 turns it off, and a thread holding the lock never adopts')
check('if (Address < ViewInterval.Offset || Address >= ViewInterval.End) {' in adopt, 'the adopted region contains the address asked about')

build = (ROOT / 'tools/build-xtajit64.sh').read_text()
check(build.count('python3 "$R/tools/patch-fex-ios-exec-views.py" "$R/FEX/Source/Windows/Common"') == 2,
      'build-xtajit64.sh applies it in both patch sequences')
restore = build[build.rindex('git -C FEX checkout --'):].split('\n')[0]
check('Source/Windows/Common/InvalidationTracker.h' in restore and 'Source/Windows/Common/InvalidationTracker.cpp' in restore,
      'build-xtajit64.sh restores the patched files after the build')

print('check-fex-exec-views:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
