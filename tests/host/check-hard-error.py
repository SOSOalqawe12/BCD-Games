#!/usr/bin/env python3
"""A game's fatal-error box, from Wine to the library's exit report (SOSOalqawe12 fork).

tools/patch-wine-hard-error.py makes NtRaiseHardError (wine/dlls/ntdll/unix/system.c) print a
STATUS_SERVICE_NOTIFICATION box as "[hard-error] caption=L"..." text=L"..."" (debugstr_us), and
LibraryModel.exitReport quotes it through LibraryModel.hardErrorWords. Checks:
  - the patch applies to the tree's system.c once (idempotent) and keeps the stub's return value;
  - the exit report reads the line (gameMessage) and quotes it in the generic message;
  - hardErrorWords undoes debugstr_w's escapes (\\n \\r \\t \\" \\\\ and \\HHHH), drops a cut string's "...",
    treats (null) as no string and caps the result at 300 characters (compiled with swiftc when there is one).
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
failures = []


def check(ok, what):
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures.append(what)


def body(text, header):
    i = text.index(header)
    a = text.index('{', i)
    n, b = 1, a + 1
    while n:
        n += (text[b] == '{') - (text[b] == '}')
        b += 1
    return text[i:b]


spec = importlib.util.spec_from_file_location('patch', ROOT / 'tools/patch-wine-hard-error.py')
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
system_c = ROOT / 'wine/dlls/ntdll/unix/system.c'
if system_c.exists():
    src = system_c.read_text()
    out, what = patch.patch(src)   # a tree the build already patched stays as it is
    again, what2 = patch.patch(out)
    check((out != src or patch.MARKER in src) and patch.MARKER in out and again == out and what2 == 'already patched',
          'the patch applies to system.c once and is idempotent')
    stub = body(out, 'NTSTATUS WINAPI NtRaiseHardError(')
    check('STATUS_SERVICE_NOTIFICATION' in stub and 'debugstr_us( text )' in stub and stub.rstrip().endswith('return STATUS_NOT_IMPLEMENTED;\n}'),
          'the stub prints caption and text and still returns STATUS_NOT_IMPLEMENTED')
else:
    print('SKIP: wine/dlls/ntdll/unix/system.c is not in this tree')
workflow = (ROOT / '.github/workflows/build-ipa.yml').read_text()
check(workflow.index('tools/patch-wine-hard-error.py wine/dlls/ntdll/unix/system.c') < workflow.index('bash build/ntdll-unix/build.sh'),
      'the build patches system.c before ntdll is compiled')

lib = (ROOT / 'app/Madeira/Library.swift').read_text()
report = body(lib, 'private func exitReport() -> String? {')
check('Self.gameMessage(LogStore.shared.logFileURL)' in report and "The game's own message:" in report,
      'the exit report quotes the game\'s own message')
check('last(where: { $0.contains("[hard-error] caption=") })' in body(lib, 'static func gameMessage(_ log: URL) -> String? {'),
      'gameMessage takes the last [hard-error] line')

swiftc = os.environ.get('SWIFTC') or shutil.which('swiftc')
if not swiftc:
    print('SKIP: no swiftc for the behaviour checks')
else:
    harness = 'import Foundation\nenum T {\n' + body(lib, 'static func hardErrorWords(_ line: String) -> String? {') + r'''
}
var bad = 0
func expect(_ got: String?, _ want: String?, _ what: String) {
    if got != want { bad += 1; print("FAIL: \(what): \(got ?? "nil")") } else { print("PASS: \(what)") }
}
expect(T.hardErrorWords(#"03f4:err:ntdll:NtRaiseHardError [hard-error] caption=L"Grand Theft Auto V" text=L"Unable to launch game,\nplease verify your game data.""#),
       "Grand Theft Auto V: Unable to launch game, please verify your game data.", "caption and text, a line break as a space")
expect(T.hardErrorWords(#"[hard-error] caption=L"Error" text=L"Quote \"x\" and back\\slash \00e9t\00e9""#),
       "Error: Quote \"x\" and back\\slash \u{e9}t\u{e9}", "quotes, backslashes and \\HHHH")
expect(T.hardErrorWords(#"[hard-error] caption=(null) text=L"Only text""#), "Only text", "no caption")
expect(T.hardErrorWords(#"[hard-error] caption=L"" text=L"Cut here"..."#), "Cut here", "a cut string")
expect(T.hardErrorWords("[hard-error] caption=L\"A\" text=L\"B\"\r"), "A: B", "a carriage return at the end")
expect(T.hardErrorWords("[hard-error] nothing"), nil, "no fields")
expect(T.hardErrorWords("[hard-error] caption=(null) text=(null)"), nil, "no strings at all")
let long = T.hardErrorWords("[hard-error] caption=L\"C\" text=L\"" + String(repeating: "x", count: 400) + "\"")
expect(long.map { String($0.count) }, "301", "at most 300 characters and an ellipsis")
exit(bad == 0 ? 0 : 1)
'''
    with tempfile.TemporaryDirectory() as tmp:
        src_path = Path(tmp) / 'hard.swift'
        src_path.write_text(harness)
        exe = Path(tmp) / 'hard'
        r = subprocess.run([swiftc, str(src_path), '-o', str(exe)], capture_output=True, text=True)
        if r.returncode:
            sys.stdout.write(r.stderr[-3000:])
            check(False, 'hardErrorWords compiles')
        else:
            r = subprocess.run([str(exe)], capture_output=True, text=True)
            sys.stdout.write(r.stdout)
            check(r.returncode == 0, 'hardErrorWords behaves')

print('check-hard-error:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
