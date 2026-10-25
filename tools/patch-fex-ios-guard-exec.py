#!/usr/bin/env python3
"""ARM64EC: executing a guard page raises STATUS_GUARD_PAGE_VIOLATION at the jump (SOSOalqawe12 fork).

ONE PIECE Bounty Rush (log 2026-10-25, build 20) allocates a page with PAGE_EXECUTE_READ |
PAGE_GUARD (prot 0x120) and jumps into it. On Windows the instruction fetch trips the guard:
the guard bit is cleared and STATUS_GUARD_PAGE_VIOLATION (0x80000001) is raised at that RIP
with ExceptionInformation = {EXECUTE_FAULT (8), RIP}; the program's handler expects exactly
that. On ARM64EC the tracker took the page as plainly executable (the execute bit is set), the
decoder read it to translate it, and the guard tripped on that read, in host code: the fault
was dispatched from the translator's own frame ("Exception frame is not in stack limits") and
the process ended with NtTerminateProcess(0x80000001).

The patch keeps the executable pages that were given PAGE_GUARD in their own interval list
(from the allocation/protection notifications; a later protection without the guard bit, or a
free, drops them). When the decoder asks about an address in one, the page is checked with
VirtualQuery (a data access may have cleared the guard already, which the kernel does without
a notification), and while the guard is set the address is reported as not executable and
remembered: the decoder then emits its NoExec fault for that RIP, as it does for any
non-executable target. A range that runs into a guard page is cut short at it, so no block is
decoded across one. When that NoExec fault is turned into the guest's exception record and
its RIP is the remembered page, the guard bit is cleared (VirtualProtect, which also drops the
NoExec block through the protection notification; the page is invalidated directly too, in
case the notification does not come back for the emulator's own call) and the record becomes
STATUS_GUARD_PAGE_VIOLATION with {EXECUTE_FAULT, RIP}, the Windows record. Executing the page
again then runs it, as on Windows.

MADEIRA_FEX_GUARD_EXEC=0 at launch turns it off. Idempotent. Apply after
patch-fex-ios-exec-views.py.
Usage: patch-fex-ios-guard-exec.py FEX/Source/Windows
"""
import os
import sys

d = sys.argv[1]
MARKER = "madeira-bcd: guard-exec"


def patch(name, edits):
    path = os.path.join(d, name)
    src = open(path).read()
    if MARKER in src:
        print(f"{name}: already patched")
        return
    for old, new in edits:
        if src.count(old) != 1:
            sys.exit(f"patch-fex-ios-guard-exec: anchor not found once in {name}:\n{old}")
        src = src.replace(old, new)
    open(path, "w").write(src)
    print(f"{name}: patched")


patch("Common/InvalidationTracker.h", [
    ("""  FEXCore::HLE::ExecutableRangeInfo QueryExecutableRange(uint64_t Address);
""",
     """  FEXCore::HLE::ExecutableRangeInfo QueryExecutableRange(uint64_t Address);
#if defined(ARCHITECTURE_arm64ec)
  /* madeira-bcd: guard-exec (tools/patch-fex-ios-guard-exec.py). True when Rip is on the guard page the
   * decoder last refused; the guard bit is then cleared and the page's code invalidated, and the caller
   * raises STATUS_GUARD_PAGE_VIOLATION. Must be called without IntervalsLock or CodeInvalidationMutex. */
  bool TakeGuardExecute(uint64_t Rip);
#endif
"""),
    ("""private:
#if defined(ARCHITECTURE_arm64ec)
""",
     """private:
#if defined(ARCHITECTURE_arm64ec)
  // madeira-bcd: guard-exec. The table lookup QueryExecutableRange wraps.
  FEXCore::HLE::ExecutableRangeInfo QueryExecutableRangeUnguarded(uint64_t Address);
  // Executable pages that were given PAGE_GUARD. NOTE: IntervalsLock guards it.
  FEXCore::IntervalList<uint64_t> GuardXIntervals;
  // The page the decoder last refused because its guard bit was set (0: none).
  std::atomic<uint64_t> PendingGuardPage {0};
"""),
])

patch("Common/InvalidationTracker.cpp", [
    # Record and drop guarded executable pages with every protection change.
    ("""#else
    const bool EffectiveExec = HasExec || (DEPDisabled && ProtIsReadable(Prot));""",
     """#else
    // madeira-bcd: guard-exec (tools/patch-fex-ios-guard-exec.py)
    if (HasExec && (Prot & PAGE_GUARD)) {
      GuardXIntervals.Insert(ProtInterval);
    } else {
      GuardXIntervals.Remove(ProtInterval);
    }
    const bool EffectiveExec = HasExec || (DEPDisabled && ProtIsReadable(Prot));"""),
    ("""    RWXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
""",
     """    RWXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
#if defined(ARCHITECTURE_arm64ec)
    GuardXIntervals.Remove({AlignedBase, AlignedBase + AlignedSize});
#endif
"""),
    ("""#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
  std::shared_lock Lock(IntervalsLock);
#endif""",
     """#else
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRangeUnguarded(uint64_t Address) {
  std::shared_lock Lock(IntervalsLock);
#endif"""),
    ("""void InvalidationTracker::DetectMonoBackpatcherBlock(FEXCore::Core::InternalThreadState* Thread, uint64_t HostPc) {""",
     """#if defined(ARCHITECTURE_arm64ec)
static bool GuardExecEnabled() {
  static const bool Enabled = [] {
    const char* Opt = getenv("MADEIRA_FEX_GUARD_EXEC");
    return !(Opt && Opt[0] == '0');
  }();
  return Enabled;
}

static bool PageHasGuard(uint64_t Page, MEMORY_BASIC_INFORMATION& Info) {
  return VirtualQuery(reinterpret_cast<LPCVOID>(Page), &Info, sizeof(Info)) && Info.State == MEM_COMMIT &&
         (Info.Protect & PAGE_GUARD);
}

/* madeira-bcd: guard-exec (tools/patch-fex-ios-guard-exec.py). A guarded executable page is not
 * decoded while its guard bit is set: the decoder gets "not executable" and emits a NoExec fault,
 * which TakeGuardExecute turns into STATUS_GUARD_PAGE_VIOLATION. A range is cut short at the next
 * guarded page so no block is decoded across one. */
FEXCore::HLE::ExecutableRangeInfo InvalidationTracker::QueryExecutableRange(uint64_t Address) {
  auto Info = QueryExecutableRangeUnguarded(Address);
  if (!Info.Size || !GuardExecEnabled() || IntervalsLock.HeldByThisThread()) {
    return Info;
  }
  FEXCore::IntervalList<uint64_t>::QueryResult Guard;
  {
    std::shared_lock Lock(IntervalsLock);
    Guard = GuardXIntervals.Query(Address);
  }
  if (Guard.Enclosed) {
    const uint64_t Page = Address & FEXCore::Utils::FEX_PAGE_MASK;
    MEMORY_BASIC_INFORMATION Mbi;
    if (PageHasGuard(Page, Mbi)) {
      PendingGuardPage.store(Page, std::memory_order_relaxed);
      return {};
    }
    // The guard was already taken by a data access: this page runs. Later pages of the interval are
    // checked when the decoder reaches them.
    {
      std::unique_lock Lock(IntervalsLock);
      GuardXIntervals.Remove({Page, Page + FEXCore::Utils::FEX_PAGE_SIZE});
    }
    const uint64_t End = std::min<uint64_t>(Info.Base + Info.Size, Page + FEXCore::Utils::FEX_PAGE_SIZE);
    Info.Size = End - Info.Base;
    return Info;
  }
  if (Guard.Size && Address + Guard.Size < Info.Base + Info.Size) {
    Info.Size = Address + Guard.Size - Info.Base;
  }
  return Info;
}

bool InvalidationTracker::TakeGuardExecute(uint64_t Rip) {
  const uint64_t Page = Rip & FEXCore::Utils::FEX_PAGE_MASK;
  uint64_t Expected = Page;
  if (!Page || !PendingGuardPage.compare_exchange_strong(Expected, 0, std::memory_order_relaxed)) {
    return false;
  }
  MEMORY_BASIC_INFORMATION Mbi;
  const bool Guarded = PageHasGuard(Page, Mbi);
  if (Guarded) {
    DWORD Old;
    VirtualProtect(reinterpret_cast<LPVOID>(Page), FEXCore::Utils::FEX_PAGE_SIZE, Mbi.Protect & ~PAGE_GUARD, &Old);
  }
  {
    std::unique_lock Lock(IntervalsLock);
    GuardXIntervals.Remove({Page, Page + FEXCore::Utils::FEX_PAGE_SIZE});
  }
  // Drop the NoExec block compiled for the page; the protection notification may not come back for the
  // emulator's own VirtualProtect.
  InvalidateAlignedInterval(Page, FEXCore::Utils::FEX_PAGE_SIZE, false);
  static std::atomic<uint32_t> LogCount {0};
  if (LogCount.fetch_add(1, std::memory_order_relaxed) < 32) {
    LogMan::Msg::EFmt("[guard-exec] {} execute at {:#x} on a guard page (prot={:#x}{}): raising STATUS_GUARD_PAGE_VIOLATION",
                      MARKER_TEXT, Rip, Guarded ? Mbi.Protect : 0, Guarded ? "" : ", guard already gone");
  }
  return true;
}
#endif

void InvalidationTracker::DetectMonoBackpatcherBlock(FEXCore::Core::InternalThreadState* Thread, uint64_t HostPc) {""".replace(
        "MARKER_TEXT", '"' + MARKER + '"')),
])

patch("ARM64EC/Module.cpp", [
    ("""  Args->Rec = FEX::Windows::HandleGuestException(Fault, Rec, Args->Context.Pc, Args->Context.X8, Args->Context.X0);
""",
     """  Args->Rec = FEX::Windows::HandleGuestException(Fault, Rec, Args->Context.Pc, Args->Context.X8, Args->Context.X0);
  /* madeira-bcd: guard-exec (tools/patch-fex-ios-guard-exec.py). The decoder refused a guarded page; on
   * Windows the fetch clears the guard and raises STATUS_GUARD_PAGE_VIOLATION at that RIP. */
  if (Args->Rec.NumberParameters == 2 && Args->Rec.ExceptionInformation[0] == EXCEPTION_EXECUTE_FAULT &&
      Args->Rec.ExceptionInformation[1] == Args->Context.Pc && InvalidationTracker &&
      InvalidationTracker->TakeGuardExecute(Args->Context.Pc)) {
    Args->Rec.ExceptionCode = STATUS_GUARD_PAGE_VIOLATION;
    Args->Rec.ExceptionFlags = 0;
  }
"""),
])
