#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""SOSOalqawe12/BCD-Games: sign recommended/recommendations.json.

    tools/sign-recommendations.py          write recommended/recommendations.json.sig
                                           (the Ed25519 seed, base64, from $RECS_SIGNING_SEED, else from the
                                           file tools/recommendations-signing.seed, which git ignores: the
                                           seed is never committed; keep it as the repository secret
                                           RECS_SIGNING_SEED)
    tools/sign-recommendations.py --check  only check that the .sig is valid for the file under the app's key

The app (GameRecommendationsSource.swift) downloads the list from this repository's raw address and takes it
only with a valid Ed25519 signature over its exact bytes under the public key compiled into it, a higher
"version" than the list in use, and every rule kept at no lower version. So after changing the list:
raise its "version", run this, commit both files; installed apps then pick the list up without a new build.
Ed25519 is tools/publish-recommendations.py's (RFC 8032, checked by tests/host/check-remote-recommendations.py).
"""
import base64
import importlib.util
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JSON = ROOT / 'recommended/recommendations.json'
SIG = ROOT / 'recommended/recommendations.json.sig'
SEED = ROOT / 'tools/recommendations-signing.seed'
SOURCE = ROOT / 'app/Madeira/GameRecommendationsSource.swift'

spec = importlib.util.spec_from_file_location('publish_recommendations', ROOT / 'tools/publish-recommendations.py')
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)

app_key = base64.b64decode(re.search(r'static let publicKey = "([A-Za-z0-9+/=]+)"', SOURCE.read_text()).group(1))
data = JSON.read_bytes()

if sys.argv[1:] == ['--check']:
    sig = tool.signature_from_file(SIG.read_bytes()) if SIG.exists() else None
    ok = bool(sig) and tool.verify(app_key, data, sig)
    print(('ok   ' if ok else 'FAIL ') + 'recommendations.json.sig is valid for this list under the app\'s key')
    sys.exit(0 if ok else 1)

import os
text = os.environ.get('RECS_SIGNING_SEED') or (SEED.read_text() if SEED.exists() else '')
lines = [l.strip() for l in text.splitlines() if l.strip() and not l.startswith('#')]
if not lines:
    sys.exit('no seed: set RECS_SIGNING_SEED or put it in tools/recommendations-signing.seed (never commit it)')
seed = base64.b64decode(lines[0])
if tool.public_key(seed) != app_key:
    sys.exit('the seed\'s public key is not the one in GameRecommendationsSource.swift')
tool.strict_json(data)
signature = tool.sign(seed, data)
assert tool.verify(app_key, data, signature)
SIG.write_bytes(tool.signature_file(signature))
print('signed recommendations.json (version %s)' % re.search(rb'"version": (\d+)', data).group(1).decode())
