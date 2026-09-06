# SpaceMusic

SpaceMusic is a **declarative v0.1 module package** for the Convx Module Runtime. It is intentionally not a Convx fork, an Android application, or an arbitrary-code plugin.

The repository is the source of the module package and its release metadata. A future Convx runtime can read the JSON contract, validate compatibility, render the declared Settings contribution, and manage the module independently from the Convx APK.

## Repository layout

```text
.
├── manifest.json                    # Package identity and runtime contract entry point
├── module/
│   └── module.json                  # Declarative contributions
├── assets/
│   └── icon.svg                     # Package-relative module icon
├── schemas/
│   ├── manifest.schema.json         # manifest.json contract
│   ├── module.schema.json           # module/module.json contract
│   ├── release.schema.json          # One release descriptor
│   └── release-index.schema.json    # Update feed contract
├── releases/
│   ├── index.json                   # Published release feed (empty until first package)
│   └── 0.1.0/
│       └── release.json             # Draft metadata for the first release
├── docs/
│   └── module-runtime-contract.md   # Runtime-facing rules and lifecycle
└── tools/
    └── validate.py                  # Portable contract validator and mutation checks
```

All content references in `manifest.json` and `module/module.json` are package-relative. The update feed is a URL because it is fetched by the host, not by module code.

## v0.1 contract

1. Convx reads `manifest.json` first.
2. It accepts the package only when `schemaVersion` is `0.1`, `type` is `declarative`, the module id matches, and the Convx version is in range.
3. It then validates and reads the file named by `content.module`.
4. The module declares one Settings section and a host-handled `check-updates` action.
5. Convx supplies the lifecycle controls (enable/disable), installed version, compatibility status, and update UI. Those controls are deliberately not duplicated in the module data.

The example currently requests only the `settings` permission. There is no code entrypoint, DEX/JAR, network permission, player hook, library hook, or navigation hook in v0.1.

## Convx boundary

### Convx owns

- the Android APK, process, and official updater;
- module discovery, JSON/schema validation, and compatibility checks;
- module storage, enable/disable state, migrations, and rendering;
- fetching the release index, downloading packages, checksum verification, and installation policy;
- the stable Module Runtime API exposed to future modules.

### SpaceMusic owns

- its manifest and declarative contribution data;
- its assets and future module-specific settings/content;
- its own version and release cadence;
- migrations for data introduced by a later module version.

SpaceMusic must not copy Convx source, Gradle configuration, resources, playback code, or updater code. Future executable extensions belong behind an explicitly versioned Convx Module API; they are out of scope for this v0.1 package.

## Compatibility

`manifest.json` declares:

- `schemaVersion`: the module contract version (`0.1`);
- `compatibility.minConvxVersion`: the oldest supported Convx version;
- `compatibility.maxConvxVersion`: an optional upper bound (`null` means no declared upper bound).

The example uses `1.5.2` as a **provisional value taken from the proposed architecture**. It has not been verified against a Convx checkout in this empty repository and must be revisited before publishing a real package.

A runtime should reject an incompatible package before installing or rendering it. A module release may narrow compatibility further, but must not silently broaden the manifest's declared range.

## Permissions

Permissions are an allowlist, not a capability request for arbitrary Android APIs. The v0.1 schema permits only:

- `settings`: add a declarative Settings contribution.

Enable/disable, version display, and update checks are host controls. Checking a module's release feed is a Convx updater operation and does not grant the module network access. `navigation`, `player`, `library`, storage, account, and arbitrary network capabilities require a future API and contract revision.

## Independent updates

There are two separate release channels:

```text
Convx updater  →  official Convx APK  →  Convx installation
Module Manager →  SpaceMusic release index  →  SpaceMusic package
```

Updating Convx must not replace or rebuild SpaceMusic. Updating SpaceMusic must not replace the Convx APK. The runtime should install a validated module package atomically under its module data directory and preserve module-owned data separately from the APK.

The release flow is:

1. publish a `.smod` package containing `manifest.json`, `module/`, and `assets/`;
2. calculate its SHA-256 digest;
3. fill `releases/0.1.0/release.json` with `status: "published"`, the package URL, and the digest;
4. add that release descriptor to `releases/index.json`;
5. let Convx filter published releases by module id, version, and compatibility before downloading.

The checked-in release descriptor is draft metadata because no package has been built or signed yet. A checksum provides integrity; the host still needs a trusted source/signature policy before treating a community package as authentic.

## SpaceMusic 0.1.0 exit criteria

`0.1.0` must remain `draft` until every gate below has evidence:

1. **Contract gate:** `python -B tools/validate.py` passes, including schema checks, deterministic `.smod` round-trip, archive safety, release/index invariants, and negative mutations.
2. **Host gate:** the Convx `modulehost` JVM tests and compilation pass, including valid-package acceptance and invalid-package rejection.
3. **Android gate:** the real Convx app compiles for the target FOSS debug variant with a configured Android SDK; SDK discovery failure is not a passing result.
4. **Smoke gate:** an APK launches, reaches Settings, opens the declarative module-host destination, reports the host as ready, and produces no crash or route/resource error.
5. **Package gate:** the release archive contains exactly the declared files, its bytes match repository source, and its SHA-256 is recorded.
6. **Metadata gate:** only after gates 1–5 pass may `releases/0.1.0/release.json` become `published` and `releases/index.json` receive the matching descriptor.

These gates prove recognition and host-facing delivery of the declarative package, plus the JVM host lifecycle (see below). They do **not** claim that publisher trust/signatures or playback are implemented; those remain later milestones.

## Convx modulehost lifecycle (JVM, tested)

The `modulehost` Gradle module implements the host-side lifecycle in pure JVM code covered by 20 tests:

- **Storage:** an optional file-backed `ModuleStore` persists the registry and package bytes (`registry.json`, `modules/<id>/current.smod`, `previous.smod`, `staged.smod`); every write is temp-file + atomic-or-replacing move.
- **Atomic install/upgrade:** `install()` verifies an optional SHA-256 digest, validates the package, stages it, and commits only after the bytes are on disk; a failed commit restores the previous package and marks the module `ROLLED_BACK` (or `FAILED` for a fresh install with no previous version).
- **Rollback:** `rollback()` revalidates the stored previous package and restores both manifest and bytes.
- **Trust:** `ModuleIntegrity.verify()` checks a package against a declared SHA-256 (integrity only — it is not publisher authentication).
- **Updates:** `ModuleUpdater` parses the release-index contract, selects the newest `published` release compatible with the running Convx version, and verifies the artifact digest.

Registry transitions (validated → staged → installed → enabled/disabled, plus failed/rolled-back) are guarded; illegal transitions throw. Android integration of this lifecycle remains unproven until the SDK gate passes.

## Validate locally

This repository has no build system by design. Run the portable validator from the repository root:

```bash
python tools/validate.py
```

The validator requires only Python 3.8+; it has no project dependencies.

It parses every repository JSON document and uses `jsonschema` to validate the four data documents against their supplied schemas when that package is already installed. With no third-party dependency, its stdlib-only structural fallback enforces the same v0.1 fields and invariants. It also checks package paths and layout, IDs and versions, SemVer/compatibility ranges, permissions/actions, release/index consistency, artifact rules, and seven in-memory invalid mutations. The packaging check builds deterministic `.smod` bytes in memory from exactly `manifest.json`, `module/module.json`, and `assets/icon.svg`, round-trips them, compares every byte with the repository source, and rejects traversal, absolute/backslash, duplicate, symlink, DOS-directory-metadata, and content-mismatch archive fixtures. No archive is written to the repository, and the mutations never write to `releases/`.

No Android, Kotlin, plugin loader, or Convx source is included until the host contract is implemented and versioned.
