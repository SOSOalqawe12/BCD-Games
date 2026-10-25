#!/usr/bin/env python3
"""tools/patch-fex-ios-guard-exec.py: executing a guard page raises STATUS_GUARD_PAGE_VIOLATION on ARM64EC.

Checks that the patch applies to the pinned FEX sources after patch-fex-ios-exec-views.py, once
(idempotent); that guarded executable pages are recorded and dropped by the protection
notifications and by a free; that the decoder's query refuses a page whose guard is still set
and cuts a range short at the next guarded page; that the guest record becomes
STATUS_GUARD_PAGE_VIOLATION only for the remembered page, with the guard cleared and the page's
code invalidated outside the locks; that MADEIRA_FEX_GUARD_EXEC=0 turns it off; and that
tools/build-xtajit64.sh applies it right after exec-views in both patch sequences.
"""
import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WIN = ROOT / 'FEX/Source/Windows'
failures = []


def check(ok, what):
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures.append(what)


def run(script, arg):
    spec = importlib.util.spec_from_file_location('p', ROOT / 'tools' / script)
    sys.argv = ['p', str(arg)]
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)


if not (WIN / 'Common/InvalidationTracker.cpp').exists():
    print('SKIP: FEX is not checked out')
    sys.exit(0)

with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'Common').mkdir()
    (tmp / 'ARM64EC').mkdir()
    for name in ('Common/InvalidationTracker.h', 'Common/InvalidationTracker.cpp', 'ARM64EC/Module.cpp'):
        shutil.copy(WIN / name, tmp / name)
    run('patch-fex-ios-launcher-smc.py', tmp / 'ARM64EC/Module.cpp')
    run('patch-fex-ios-exec-views.py', tmp / 'Common')
    run('patch-fex-ios-guard-exec.py', tmp)
    cpp = (tmp / 'Common/InvalidationTracker.cpp').read_text()
    h = (tmp / 'Common/InvalidationTracker.h').read_text()
    mod = (tmp / 'ARM64EC/Module.cpp').read_text()
    run('patch-fex-ios-guard-exec.py', tmp)   # the second run must change nothing
    check((tmp / 'Common/InvalidationTracker.cpp').read_text() == cpp and (tmp / 'ARM64EC/Module.cpp').read_text() == mod,
          'the patch is idempotent')

check(h.count('bool TakeGuardExecute(uint64_t Rip);') == 1 and 'FEXCore::IntervalList<uint64_t> GuardXIntervals;' in h
      and h.index('#if defined(ARCHITECTURE_arm64ec)\n  // madeira-bcd: guard-exec') < h.index('GuardXIntervals;'),
      'the guard list and helpers are declared for the ARM64EC build only')

notify = cpp[cpp.index('void InvalidationTracker::HandleMemoryProtectionNotification('):]
notify = notify[:notify.index('\n}\n')]
check('if (HasExec && (Prot & PAGE_GUARD)) {\n      GuardXIntervals.Insert(ProtInterval);\n    } else {\n      GuardXIntervals.Remove(ProtInterval);' in notify,
      'a protection with execute and guard records the pages, any other protection drops them')
aligned = cpp[cpp.index('void InvalidationTracker::InvalidateAlignedInterval('):]
aligned = aligned[:aligned.index('\n}\n')]
check('GuardXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});' in aligned, 'a free drops the pages')

check('FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRangeUnguarded(uint64_t Address) {\n  std::shared_lock Lock(IntervalsLock);' in cpp
      and 'if (AdoptExecutableView(Address)) {' in cpp[cpp.index('QueryExecutableRangeUnguarded(uint64_t Address) {'):],
      'the table lookup (with exec-views) is kept as QueryExecutableRangeUnguarded')
query = cpp[cpp.index('FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {\n  auto Info = QueryExecutableRangeUnguarded(Address);'):]
query = query[:query.index('\n}\n')]
check('!GuardExecEnabled() || IntervalsLock.HeldByThisThread()' in query, 'off switch and lock-holder early return')
check(query.index('Guard = GuardXIntervals.Query(Address);') < query.index('PageHasGuard(Page, Mbi)')
      and query.index('std::shared_lock Lock(IntervalsLock);') < query.index('\n  }\n  if (Guard.Enclosed)') < query.index('PageHasGuard(Page, Mbi)'),
      'VirtualQuery runs after the shared lock is released')
check('PendingGuardPage.store(Page, std::memory_order_relaxed);\n      return {};' in query, 'a page whose guard is set is refused and remembered')
check('if (Guard.Size && Address + Guard.Size < Info.Base + Info.Size) {\n    Info.Size = Address + Guard.Size - Info.Base;' in query,
      'a range is cut short at the next guarded page')
check('getenv("MADEIRA_FEX_GUARD_EXEC")' in cpp and "Opt[0] == '0'" in cpp, 'MADEIRA_FEX_GUARD_EXEC=0 turns it off')

take = cpp[cpp.index('bool InvalidationTracker::TakeGuardExecute(uint64_t Rip) {'):]
take = take[:take.index('\n}\n')]
check('PendingGuardPage.compare_exchange_strong(Expected, 0' in take, 'only the remembered page is taken, once')
check(take.index('VirtualProtect(') < take.index('std::unique_lock Lock(IntervalsLock);') < take.index('InvalidateAlignedInterval(Page, FEXCore::Utils::FEX_PAGE_SIZE, false);')
      and 'Mbi.Protect & ~PAGE_GUARD' in take, 'the guard is cleared, the page dropped from the list and its code invalidated')
check(take.index('InvalidateAlignedInterval(Page') < take.index('LogMan::Msg::EFmt('), 'the log line comes last, with no lock held')

rethrow = mod[mod.index('static void RethrowGuestException('):]
rethrow = rethrow[:rethrow.index('\n}\n')]
check(rethrow.index('Args->Rec = FEX::Windows::HandleGuestException(') < rethrow.index('InvalidationTracker->TakeGuardExecute(Args->Context.Pc)')
      < rethrow.index('Args->Rec.ExceptionCode = STATUS_GUARD_PAGE_VIOLATION;'),
      'the guest record becomes STATUS_GUARD_PAGE_VIOLATION after it is built')
check('Args->Rec.ExceptionInformation[0] == EXCEPTION_EXECUTE_FAULT &&\n      Args->Rec.ExceptionInformation[1] == Args->Context.Pc' in rethrow,
      'only an execute fault at the reported RIP is converted')

build = (ROOT / 'tools/build-xtajit64.sh').read_text()
pair = ('python3 "$R/tools/patch-fex-ios-exec-views.py" "$R/FEX/Source/Windows/Common"\n'
        '    # SOSOalqawe12 fork: executing a guard page raises STATUS_GUARD_PAGE_VIOLATION (ONE PIECE Bounty Rush).\n'
        '    python3 "$R/tools/patch-fex-ios-guard-exec.py" "$R/FEX/Source/Windows"')
check(build.count(pair) == 2, 'build-xtajit64.sh applies it right after exec-views in both patch sequences')
restore = build[build.rindex('git -C FEX checkout --'):].split('\n')[0]
check(all(f in restore for f in ('Source/Windows/Common/InvalidationTracker.h', 'Source/Windows/Common/InvalidationTracker.cpp',
                                 'Source/Windows/ARM64EC/Module.cpp')),
      'build-xtajit64.sh restores the patched files after the build')

print('check-fex-guard-exec:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
