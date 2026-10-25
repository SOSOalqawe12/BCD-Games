#!/usr/bin/env python3
"""tools/patch-wineserver-getctx-log.py: the get_thread_context request names why it refuses.

Applies the patch to a copy of wine/server/thread.c twice (idempotent) and checks that each of the
three refusal paths prints a capped "[srv-getctx-err]" line before it returns, that nothing else in
the handler changes, and that the set_thread_context handler is untouched.
"""
import importlib.util
import re
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'wine/server/thread.c'
failures = []


def check(ok, what):
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures.append(what)


if not SRC.exists():
    print('SKIP: wine is not checked out')
    sys.exit(0)

spec = importlib.util.spec_from_file_location('p', ROOT / 'tools/patch-wineserver-getctx-log.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
with tempfile.TemporaryDirectory() as tmp:
    path = Path(tmp) / 'thread.c'
    shutil.copy(SRC, path)
    check(module.main(str(path)) == 0, 'the patch applies')
    once = path.read_text()
    check(module.main(str(path)) == 0 and path.read_text() == once, 'the patch is idempotent')

orig = SRC.read_text()
handler = once[once.index('DECL_HANDLER(get_thread_context)'):once.index('DECL_HANDLER(set_thread_context)')]
check(handler.count('[srv-getctx-err]') == 3, 'three refusal paths are named')
check(handler.index('[srv-getctx-err] handle=') < handler.index('            return;\n        }\n        if (req->machine'),
      'a handle without THREAD_GET_CONTEXT is logged with its granted access, then refused as before')
check(handler.index('the capture left an error') < handler.index('        release_object( thread );\n        if (!thread_context) return;'),
      'a capture error is logged while the thread is still referenced')
check(handler.index('from the context object') < handler.index('        set_error( thread_context->status );'),
      'a context object that ended is logged before its status is returned')
check(all(n in handler for n in ('logged++ < 64', 'logged_capture++ < 64', 'logged_status++ < 64')), 'each line is capped')
stripped = re.sub(r'\n {8}\{\n {12}/\* SOSOalqawe12.*?\n {12}return;\n {8}\}\n', ' return;\n', handler, flags=re.S)
stripped = re.sub(r' {8}if \(!thread_context && get_error\(\)\)\n {8}\{.*?\n {8}\}\n', '', stripped, flags=re.S)
stripped = re.sub(r' {8}if \(thread_context->status != STATUS_PENDING\)\n {8}\{.*?\n {8}\}\n', '', stripped, flags=re.S)
check(stripped == orig[orig.index('DECL_HANDLER(get_thread_context)'):orig.index('DECL_HANDLER(set_thread_context)')],
      'nothing else in the handler changes')
check(once[once.index('DECL_HANDLER(set_thread_context)'):] == orig[orig.index('DECL_HANDLER(set_thread_context)'):],
      'set_thread_context and the rest of the file are untouched')

print('check-wineserver-getctx-log:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
