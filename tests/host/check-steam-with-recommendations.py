#!/usr/bin/env python3
"""Start with Steam's App ID chooses a game's recommended list (SOSOalqawe12 fork).

A Steam copy kept outside a Steam library and started with Steam (Options › Start with Steam, the game file's
`steam-with` line) is matched as the Steam copy: GameRecommendationsApply.recommendation(windowsPath:steamAppID:)
reads the line (steamWithAppID) and GameRecommendationList.match takes the rule of that App ID for a program
outside a Steam library. GTA V Enhanced then gets gta5e-steam, the list proven on a device, not gta5e-other.
Compiles GameRecommendations.swift and GameRecommendationsList.swift with the repository's list (on Linux,
whose Foundation has no CFGetTypeID, JSONRead.bool/int get a stand-in) and checks the matches.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'app/Madeira'
failures = []


def check(ok, what):
    print(('PASS: ' if ok else 'FAIL: ') + what)
    if not ok:
        failures.append(what)


apply_src = (APP / 'GameRecommendationsApply.swift').read_text()
check('let steamAppID = steamAppID ?? steamWithAppID(windowsPath)' in apply_src
      and 'GameProfile.parse(GameProfile(windowsPath: windowsPath).text)["steam-with"]' in apply_src,
      'recommendation(windowsPath:steamAppID:) reads Start with Steam\'s App ID')
match_src = (APP / 'GameRecommendations.swift').read_text()
check("if let steamAppID, steamFolder == nil,\n           let rule = rules.first(where: { $0.match?.steamAppIDs.contains(steamAppID) == true }) {" in match_src,
      'a program outside a Steam library takes the rule of its App ID')

swiftc = os.environ.get('SWIFTC') or shutil.which('swiftc')
if not swiftc:
    print('SKIP: no swiftc for the compiled checks')
else:
    lst = (APP / 'GameRecommendationsList.swift').read_text()
    if sys.platform.startswith('linux'):
        a = lst.index('    static func bool(_ value: Any?, _ path: String) throws -> Bool {')
        b = lst.index('        return Int(result)\n    }', a) + len('        return Int(result)\n    }')
        lst = lst[:a] + '''    static func bool(_ value: Any?, _ path: String) throws -> Bool {   // Linux stand-in (no CFGetTypeID)
        if let n = value as? NSNumber, String(cString: n.objCType) == "c" { return n.boolValue }
        throw RecommendationsError.format("\\(path) is not true or false")
    }

    static func int(_ value: Any?, _ path: String) throws -> Int {   // Linux stand-in
        if let i = value as? Int { return i }
        throw RecommendationsError.format("\\(path) is not a whole number")
    }''' + lst[b:]
    main = r'''import Foundation
enum RecommendationsStore { static func initialList() -> GameRecommendationList { .empty } }
@main struct Main {
    static func main() throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        GameRecommendations.install(try RecommendationsFile.parse(data).list(build: 1000, origin: .builtIn))
        let exists: (String) -> Bool = { $0.lowercased().hasSuffix("gta5_enhanced.exe") }
        func id(_ path: String, _ app: Int?) -> String { GameRecommendations.match(windowsPath: path, steamAppID: app, fileExists: exists)?.id ?? "nil" }
        var bad = 0
        func expect(_ got: String, _ want: String, _ what: String) {
            if got != want { bad += 1; print("FAIL: \(what): \(got)") } else { print("PASS: \(what)") }
        }
        let own = "C:\\Program Files (x86)\\Grand Theft Auto V Enhanced\\PlayGTAV.exe"
        expect(id(own, nil), "gta5e-other", "GTA V Enhanced outside Steam without an App ID: the other list")
        expect(id(own, 3240220), "gta5e-steam", "the same copy with Start with Steam's App ID: the Steam list")
        expect(id("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto V Enhanced\\PlayGTAV.exe", nil), "gta5e-steam",
               "inside a Steam library: the Steam list, as before")
        expect(id("C:\\Program Files (x86)\\Steam\\steamapps\\common\\Grand Theft Auto V Enhanced", 3240220), "gta5e-steam",
               "a library Steam game by its App ID, as before")
        expect(id("C:\\Games\\Some Game\\game.exe", 999999999), "nil", "an App ID no rule names gives no list")
        expect(id("C:\\Games\\RDR2\\PlayRDR2.exe", nil), "rdr2-other", "RDR2 outside Steam, as before")
        expect(id("C:\\Games\\RDR2\\PlayRDR2.exe", 1174180), "rdr2-steam", "RDR2 with Start with Steam: the Steam list")
        exit(bad == 0 ? 0 : 1)
    }
}
'''
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / 'list.swift').write_text(lst)
        (tmp / 'main.swift').write_text(main)
        exe = tmp / 'recs'
        r = subprocess.run([swiftc, '-parse-as-library', str(APP / 'GameRecommendations.swift'), str(tmp / 'list.swift'),
                            str(tmp / 'main.swift'), '-o', str(exe)], capture_output=True, text=True)
        if r.returncode:
            sys.stdout.write(r.stderr[-3000:])
            check(False, 'the matching code compiles on the host')
        else:
            r = subprocess.run([str(exe), str(ROOT / 'recommended/recommendations.json')], capture_output=True, text=True)
            sys.stdout.write(r.stdout + r.stderr[-2000:])
            check(r.returncode == 0, 'the matches')

print('check-steam-with-recommendations:', 'FAIL' if failures else 'PASS')
sys.exit(1 if failures else 0)
