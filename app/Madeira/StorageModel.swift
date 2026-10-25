//
//  StorageModel.swift
//  Madeira
//
//  madeira-bcd: Settings > Storage -- what the runtime keeps on the phone and a
//  way to take back the space. Frame captures (the overlay's CAP), session logs
//  and the shader caches all grow without the user ever seeing them; a phone
//  that fills up kills the game mid-session in ways that look like our bugs.
//

import SwiftUI

struct StorageItem: Identifiable {
    let id: String
    let title: String
    let detail: String
    let deletable: Bool
    let urls: () -> [URL]
}

final class StorageModel: ObservableObject {
    @Published private(set) var sizes: [String: Int64] = [:]
    @Published private(set) var busy = false

    static var documents: URL? { FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first }

    /// C:\users\<name>\AppData\Local\BCD\ShaderCache for every profile.
    static func d3d12ShaderCaches() -> [URL] {
        guard let users = GameLibrary.driveC?.appendingPathComponent("users"),
              let names = try? FileManager.default.contentsOfDirectory(atPath: users.path) else { return [] }
        return names.map {
            users.appendingPathComponent($0).appendingPathComponent("AppData/Local/BCD/ShaderCache", isDirectory: true)
        }.filter { FileManager.default.fileExists(atPath: $0.path) }
    }

    /// SOSOalqawe12 fork: what is inside each Windows temporary folder (the folders themselves stay,
    /// programs expect them): C:\windows\temp and every profile's AppData\Local\Temp.
    static func windowsTemp() -> [URL] {
        guard let drive = GameLibrary.driveC else { return [] }
        var folders = [drive.appendingPathComponent("windows/temp", isDirectory: true)]
        if let names = try? FileManager.default.contentsOfDirectory(atPath: drive.appendingPathComponent("users").path) {
            folders += names.map { drive.appendingPathComponent("users/\($0)/AppData/Local/Temp", isDirectory: true) }
        }
        return children(folders)
    }

    /// SOSOalqawe12 fork: crash dumps Windows programs leave: Steam's dumps folder and every profile's
    /// AppData\Local\CrashDumps (the folders stay).
    static func crashDumps() -> [URL] {
        guard let drive = GameLibrary.driveC else { return [] }
        var folders = [drive.appendingPathComponent("Program Files (x86)/Steam/dumps", isDirectory: true)]
        if let names = try? FileManager.default.contentsOfDirectory(atPath: drive.appendingPathComponent("users").path) {
            folders += names.map { drive.appendingPathComponent("users/\($0)/AppData/Local/CrashDumps", isDirectory: true) }
        }
        return children(folders)
    }

    /// SOSOalqawe12 fork: what is inside the rebuildable web caches of Rockstar's launcher (Social Club's
    /// Chromium: Cache, Code Cache, GPUCache and the Dawn caches of every profile's Launcher\Renderer\Default).
    /// Cookies, local storage and settings are left alone, so the Rockstar sign-in stays.
    static func launcherCaches() -> [URL] {
        guard let drive = GameLibrary.driveC else { return [] }
        let users = (try? FileManager.default.contentsOfDirectory(atPath: drive.appendingPathComponent("users").path)) ?? []
        let caches = ["Cache", "Code Cache", "GPUCache", "DawnCache", "DawnGraphiteCache", "DawnWebGPUCache"]
        return children(users.flatMap { user in
            caches.map {
                drive.appendingPathComponent("users/\(user)/Documents/Rockstar Games/Social Club/Launcher/Renderer/Default/\($0)",
                                             isDirectory: true)
            }
        })
    }

    private static func children(_ folders: [URL]) -> [URL] {
        folders.flatMap { folder in
            ((try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []).map { folder.appendingPathComponent($0) }
        }
    }

    static let items: [StorageItem] = [
        StorageItem(id: "capture", title: "Frame captures",
                    detail: "Written by the overlay's CAP button", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("capture", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "logs", title: "Session logs",
                    detail: "Earlier games' logs (the newest 40 are kept)", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("logs", isDirectory: true),
                             StorageModel.documents?.appendingPathComponent("bcd-log.prev.txt")].compactMap { $0 } }),
        StorageItem(id: "sc12", title: "D3D12 shader cache",
                    detail: "Converted shaders; deleting it makes the next start convert them again", deletable: true,
                    urls: { StorageModel.d3d12ShaderCaches() }),
        StorageItem(id: "sc11", title: "D3D11 shader cache",
                    detail: "DXMT's compiled shaders", deletable: true,
                    urls: { [StorageModel.documents?.appendingPathComponent("shadercache", isDirectory: true)].compactMap { $0 } }),
        StorageItem(id: "wintemp", title: "Windows temporary files",
                    detail: "Left behind by installers and games; clear it while no game runs", deletable: true,
                    urls: { StorageModel.windowsTemp() }),
        StorageItem(id: "dumps", title: "Crash dumps",
                    detail: "Written when a Windows program crashes (Steam's dumps and CrashDumps folders)", deletable: true,
                    urls: { StorageModel.crashDumps() }),
        StorageItem(id: "webcache", title: "Rockstar launcher web cache",
                    detail: "Social Club's browser cache, rebuilt when the launcher opens; the sign-in stays. Clear it while no game runs", deletable: true,
                    urls: { StorageModel.launcherCaches() }),
        StorageItem(id: "drive", title: "Windows drive (C:)",
                    detail: "Games, saves and the Windows files", deletable: false,
                    urls: { [GameLibrary.driveC].compactMap { $0 } }),
    ]

    func refresh() {
        guard !busy else { return }
        busy = true
        DispatchQueue.global(qos: .utility).async {
            var out: [String: Int64] = [:]
            for item in StorageModel.items {
                out[item.id] = item.urls().reduce(Int64(0)) { $0 + StorageModel.size(of: $1) }
            }
            DispatchQueue.main.async {
                self.sizes = out
                self.busy = false
            }
        }
    }

    func clear(_ item: StorageItem) {
        guard item.deletable else { return }
        DispatchQueue.global(qos: .utility).async {
            for u in item.urls() { try? FileManager.default.removeItem(at: u) }
            DispatchQueue.main.async { self.refresh() }
        }
    }

    /// Allocated bytes under `url` (a file or a directory; symlinks are not followed).
    static func size(of url: URL) -> Int64 {
        let fm = FileManager.default
        let keys: [URLResourceKey] = [.isRegularFileKey, .totalFileAllocatedSizeKey, .fileAllocatedSizeKey]
        var isDir: ObjCBool = false
        guard fm.fileExists(atPath: url.path, isDirectory: &isDir) else { return 0 }
        if !isDir.boolValue {
            let v = try? url.resourceValues(forKeys: Set(keys))
            return Int64(v?.totalFileAllocatedSize ?? v?.fileAllocatedSize ?? 0)
        }
        var total: Int64 = 0
        if let e = fm.enumerator(at: url, includingPropertiesForKeys: keys, options: [], errorHandler: { _, _ in true }) {
            for case let f as URL in e {
                guard let v = try? f.resourceValues(forKeys: Set(keys)), v.isRegularFile == true else { continue }
                total += Int64(v.totalFileAllocatedSize ?? v.fileAllocatedSize ?? 0)
            }
        }
        return total
    }
}
