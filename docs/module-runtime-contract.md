# Convx Module Runtime contract — v0.1

This document is normative for the files in this repository. It describes data that a future Convx Module Runtime can consume; it does not implement the runtime.

## Package boundary

A SpaceMusic package contains only:

```text
manifest.json
module/module.json
assets/icon.svg
```

`releases/` is repository-side update metadata and is not required inside the installed `.smod` archive. There is no Kotlin/Java entrypoint, DEX/JAR, Android manifest, Gradle project, or copied Convx source.

All package paths are relative to the package root and must not escape it. `updates.releaseIndex` is an HTTPS URL owned by the module publisher and is fetched by Convx's updater.

## Validation sequence

The host should validate in this order:

1. Parse `manifest.json` as JSON.
2. Validate it with `schemas/manifest.schema.json`.
3. Require `schemaVersion == "0.1"` and `type == "declarative"`.
4. Check the Convx version against `compatibility.minConvxVersion` and the optional maximum.
5. Resolve `content.module` and `content.icon` within the package; reject absolute paths and traversal.
6. Validate `module/module.json` with `schemas/module.schema.json`.
7. Require `moduleId` to equal the manifest `id`.
8. Render only contribution types supported by the declared permissions.

A failed check makes the package unavailable; it must not partially install or execute fallback code.

## v0.1 contribution

The only supported permission is `settings`. The example contributes:

- a Settings section titled `SpaceMusic`;
- the package icon;
- a host-handled `check-updates` action.

Convx owns the surrounding module row and lifecycle controls. It should expose enable/disable state, installed version, compatibility status, and update status without requiring SpaceMusic to redeclare them.

The action named `check-updates` means “ask the host Module Manager to check this module's feed.” It is not a callback and does not execute module code.

## Permissions and ownership

| Capability | v0.1 | Owner |
| --- | --- | --- |
| Settings contribution | allowed | SpaceMusic declares; Convx renders |
| Enable/disable | host control | Convx |
| Installed version | host control | Convx reads manifest |
| Update check/install | host control | Convx updater |
| Navigation | not available | future API |
| Player/library hooks | not available | future API |
| Arbitrary network/storage/account access | not available | future policy/API |

The manifest's permission list must be an allowlist. Unknown permissions are rejected rather than ignored.

## Compatibility

Compatibility is evaluated twice:

- the installed manifest states the module's supported Convx range;
- each release descriptor may narrow that range for a particular artifact.

The release must not be offered when its minimum is newer than the running Convx version or its maximum is older than it. The provisional minimum `1.5.2` comes from the project proposal and is not verified in this empty checkout.

## Independent update protocol

Convx and SpaceMusic have separate version and installation channels. A host update replaces only the Convx APK. A module update replaces only the validated module package and its module-owned files.

For a published release, `releases/index.json` contains release descriptors with:

- matching `moduleId`;
- a semver `version`;
- `status: "published"`;
- compatibility bounds;
- an HTTPS artifact URL;
- a 64-character SHA-256 digest.

The Convx `modulehost` Gradle module implements this flow in JVM code (covered by its test suite): it fetches nothing itself but parses the feed, ignores drafts and incompatible releases, selects the newest supported version (`ModuleUpdater`), verifies the declared SHA-256 digest (`ModuleIntegrity`), validates the package, and installs it atomically with a rollback point (`ModuleStore` + `install`/`rollback`). Digest verification is integrity checking, not publisher authentication; a trusted source or signature policy remains a host responsibility and is not implemented.

The checked-in `releases/0.1.0/release.json` is intentionally `draft` and has no artifact or digest. It is a template for the first real package release.

## Versioning boundary

`schemaVersion` identifies the data contract. `version` identifies SpaceMusic content. A future executable Module API must have its own explicit API version and compatibility rules; adding a code loader must not be inferred from this declarative contract.
