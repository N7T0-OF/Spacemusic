#!/usr/bin/env python3
"""Validate the repository-owned declarative SpaceMusic v0.1 contract."""

from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import re
import stat
import sys
import warnings
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlsplit
from xml.etree import ElementTree

SCHEMA_URL = "https://raw.githubusercontent.com/N7T0-OF/Spacemusic/main/schemas/"
SCHEMA_FILES = ("manifest.schema.json", "module.schema.json", "release.schema.json", "release-index.schema.json")
DATA_SCHEMAS = {"manifest.json": "manifest.schema.json", "module/module.json": "module.schema.json", "releases/index.json": "release-index.schema.json"}
PACKAGE_FILES = frozenset(("manifest.json", "module/module.json", "assets/icon.svg"))
DOS_DIRECTORY_ATTRIBUTE = 0x10
IGNORED_DIRS = frozenset((".freebuff", ".git", "__pycache__"))
EXTERNAL_LINK_DIRS = frozenset(("Convx-main",))
FORBIDDEN_SUFFIXES = frozenset((".kt", ".java", ".gradle", ".kts", ".dex", ".jar", ".class", ".pyc", ".apk", ".aab", ".so"))
ID_RE = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
PATH_RE = re.compile(r"^(?!.*(?:^|/)\.\.?(?:/|$))(?!/)[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*$")
URL_RE = re.compile(r"^https://[^\s/?#]+(?:[/?#][^\s]*)?$")
SHA256_RE = re.compile(r"^[A-Fa-f0-9]{64}$")
SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class ContractError(ValueError):
    """Raised when a document violates the v0.1 contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def ignored(path: Path, root: Path) -> bool:
    relative = path.relative_to(root)
    if IGNORED_DIRS.intersection(relative.parts):
        return True
    # The optional Convx checkout is exposed through a workspace junction; it
    # is a separate repository and must not become SpaceMusic package input.
    if not relative.parts or relative.parts[0] not in EXTERNAL_LINK_DIRS:
        return False
    link = root / relative.parts[0]
    is_junction = getattr(link, "is_junction", lambda: False)
    return link.is_symlink() or is_junction()


def read_json(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ContractError(f"cannot read JSON file {path}: {error}") from error


def parse_version(value: Any, label: str) -> Tuple[int, int, int, Optional[Tuple[str, ...]]]:
    require(isinstance(value, str) and SEMVER_RE.fullmatch(value) is not None, f"{label} must be valid SemVer")
    core = value.split("+", 1)[0]
    numbers, separator, prerelease = core.partition("-")
    return *(int(part) for part in numbers.split(".")), tuple(prerelease.split(".")) if separator else None


def compare_versions(left: Tuple[int, int, int, Optional[Tuple[str, ...]]], right: Tuple[int, int, int, Optional[Tuple[str, ...]]]) -> int:
    if left[:3] != right[:3]:
        return -1 if left[:3] < right[:3] else 1
    left_pre, right_pre = left[3], right[3]
    if left_pre is None or right_pre is None:
        return 0 if left_pre == right_pre else (1 if left_pre is None else -1)
    for left_id, right_id in zip(left_pre, right_pre):
        if left_id == right_id:
            continue
        left_numeric, right_numeric = left_id.isdigit(), right_id.isdigit()
        if left_numeric and right_numeric:
            return -1 if int(left_id) < int(right_id) else 1
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return -1 if left_id < right_id else 1
    return (len(left_pre) > len(right_pre)) - (len(left_pre) < len(right_pre))


def safe_package_path(value: Any) -> bool:
    return isinstance(value, str) and PATH_RE.fullmatch(value) is not None


def https_url(value: Any) -> bool:
    if not isinstance(value, str) or URL_RE.fullmatch(value) is None:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return False
    return parsed.scheme == "https" and bool(parsed.netloc)


def exact_object(value: Any, keys: Sequence[str], label: str) -> Mapping[str, Any]:
    require(isinstance(value, dict) and set(value) == set(keys), f"{label} must contain exactly {sorted(keys)}")
    return value


def without_schema(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: item for key, item in value.items() if key != "$schema"}


def archive_bytes(entries: Sequence[Tuple[str, bytes, Optional[int]]]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload, external_attr in entries:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = ((stat.S_IFREG | 0o644) << 16) if external_attr is None else external_attr
            archive.writestr(info, payload)
    return stream.getvalue()


def read_smod(data: bytes) -> Dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(data), mode="r") as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            require(len(names) == len(set(names)), "package archive contains duplicate paths")
            contents: Dict[str, bytes] = {}
            for info in infos:
                name = info.filename
                require(safe_package_path(name) and name in PACKAGE_FILES, f"unsafe or undeclared package path: {name!r}")
                require(not info.is_dir(), f"package archive contains a directory entry: {name!r}")
                require((info.external_attr & DOS_DIRECTORY_ATTRIBUTE) == 0, f"package archive contains DOS directory metadata: {name!r}")
                mode = (info.external_attr >> 16) & 0xFFFF
                require(stat.S_IFMT(mode) in (0, stat.S_IFREG), f"package archive contains a non-regular entry: {name!r}")
                contents[name] = archive.read(info)
    except ContractError:
        raise
    except (EOFError, KeyError, OSError, RuntimeError, ValueError, zipfile.BadZipFile) as error:
        raise ContractError(f"invalid .smod archive: {error}") from error
    require(set(contents) == PACKAGE_FILES, "package archive does not contain exactly the declared files")
    return contents


def verify_smod(data: bytes, expected: Mapping[str, bytes]) -> None:
    require(read_smod(data) == dict(expected), "package archive contents differ from repository source")


class SchemaBackend:
    """Use jsonschema when installed; direct checks below are the dependency-free fallback."""

    def __init__(self, schemas: Mapping[str, Any]):
        self.schemas = schemas
        self.validator = None
        try:
            from jsonschema import Draft202012Validator, RefResolver
        except (ImportError, AttributeError):
            return
        self.validator, self.resolver = Draft202012Validator, RefResolver
        self.store: Dict[str, Any] = {}
        for name, schema in schemas.items():
            self.store[name] = schema
            self.store[SCHEMA_URL + name] = schema
            if isinstance(schema.get("$id"), str):
                self.store[schema["$id"]] = schema

    @property
    def name(self) -> str:
        return "jsonschema" if self.validator else "dependency-free structural fallback"

    def check_schemas(self) -> None:
        for name, schema in self.schemas.items():
            require(isinstance(schema, dict), f"schema {name} must be an object")
            require(schema.get("$schema") == "https://json-schema.org/draft/2020-12/schema", f"schema {name} must use JSON Schema 2020-12")
            require(schema.get("$id") == SCHEMA_URL + name, f"schema {name} has the wrong $id")
            self._check_refs(schema, name, schema)
            if self.validator:
                try:
                    self.validator.check_schema(schema)
                except Exception as error:
                    raise ContractError(f"schema {name} is invalid: {error}") from error

    def _check_refs(self, node: Any, owner: str, root: Mapping[str, Any]) -> None:
        if isinstance(node, dict):
            reference = node.get("$ref")
            if reference is not None:
                require(isinstance(reference, str), f"schema {owner} has a non-string $ref")
                external, _, fragment = reference.partition("#")
                target = root
                if external:
                    name = external.rsplit("/", 1)[-1]
                    require(name in self.schemas, f"schema {owner} references an unknown schema")
                    target = self.schemas[name]
                if fragment:
                    require(fragment.startswith("/"), f"schema {owner} has an unsupported $ref fragment")
                    for part in fragment[1:].split("/"):
                        part = part.replace("~1", "/").replace("~0", "~")
                        require(isinstance(target, dict) and part in target, f"schema {owner} has an unresolved $ref")
                        target = target[part]
            for child in node.values():
                self._check_refs(child, owner, root)
        elif isinstance(node, list):
            for child in node:
                self._check_refs(child, owner, root)

    def validate(self, document: Any, schema_name: str) -> None:
        if not self.validator:
            return
        try:
            schema = self.schemas[schema_name]
            resolver = self.resolver.from_schema(schema, store=self.store)
            errors = sorted(self.validator(schema, resolver=resolver).iter_errors(document), key=lambda error: list(error.path))
        except Exception as error:
            raise ContractError(f"{schema_name} could not be resolved: {error}") from error
        if errors:
            error = errors[0]
            location = "$" + "".join(f"[{part!r}]" for part in error.path)
            raise ContractError(f"{schema_name} at {location}: {error.message}")


class RepositoryValidator:
    def __init__(self, root: Path, overrides: Optional[Mapping[str, Any]] = None):
        self.root = root.resolve()
        self.overrides = {PurePosixPath(key).as_posix(): copy.deepcopy(value) for key, value in (overrides or {}).items()}
        self.schemas = {name: read_json(self.root / "schemas" / name) for name in SCHEMA_FILES}
        self.backend = SchemaBackend(self.schemas)
        self.json_count = 0
        self.archive_rejections = 0

    def read(self, key: str) -> Any:
        key = PurePosixPath(key).as_posix()
        return copy.deepcopy(self.overrides[key]) if key in self.overrides else read_json(self.root.joinpath(*PurePosixPath(key).parts))

    def document(self, key: str, schema_name: str) -> Mapping[str, Any]:
        document = self.read(key)
        require(isinstance(document, dict), f"{key} must be an object")
        require(document.get("$schema") == SCHEMA_URL + schema_name, f"{key} has the wrong $schema")
        self.backend.validate(document, schema_name)
        return document

    def run(self) -> None:
        self.check_json_inventory()
        self.backend.check_schemas()
        manifest = self.document("manifest.json", "manifest.schema.json")
        self.check_manifest(manifest)
        self.check_package_layout(manifest)
        module = self.document(manifest["content"]["module"], "module.schema.json")
        self.check_module(manifest, module)
        index = self.document("releases/index.json", "release-index.schema.json")
        releases = self.check_releases(manifest)
        self.check_index(manifest, index, releases)
        self.archive_rejections = package_round_trip(self.root)

    def check_json_inventory(self) -> None:
        allowed_schemas = {f"schemas/{name}" for name in SCHEMA_FILES}
        keys = []
        for path in self.root.rglob("*.json"):
            if ignored(path, self.root):
                continue
            key = path.relative_to(self.root).as_posix()
            require(key in DATA_SCHEMAS or key in allowed_schemas or self.is_release_key(key), f"unexpected JSON file: {key}")
            read_json(path)
            keys.append(key)
        require(set(DATA_SCHEMAS).issubset(keys), "required data JSON is missing")
        require({key for key in keys if key.startswith("schemas/")} == allowed_schemas, "schema layout must contain exactly the four supplied schemas")
        self.json_count = len(keys)

    @staticmethod
    def is_release_key(key: str) -> bool:
        parts = PurePosixPath(key).parts
        return len(parts) == 3 and parts[0] == "releases" and parts[2] == "release.json"

    def check_manifest(self, manifest: Mapping[str, Any]) -> None:
        exact_object(manifest, ("$schema", "schemaVersion", "id", "name", "version", "type", "description", "author", "compatibility", "permissions", "content", "updates"), "manifest")
        require(manifest["schemaVersion"] == "0.1" and manifest["type"] == "declarative", "manifest contract version/type is invalid")
        require(manifest["id"] == "spacemusic" and ID_RE.fullmatch(manifest["id"]), "manifest.id must be spacemusic")
        for field, limit in (("name", 80), ("description", 280), ("author", 120)):
            require(isinstance(manifest[field], str) and 0 < len(manifest[field]) <= limit, f"manifest.{field} is invalid")
        parse_version(manifest["version"], "manifest.version")
        self.check_compatibility(manifest["compatibility"], "manifest.compatibility")
        require(manifest["permissions"] == ["settings"], "v0.1 supports exactly the settings permission")
        content = exact_object(manifest["content"], ("module", "icon"), "manifest.content")
        require(safe_package_path(content["module"]) and safe_package_path(content["icon"]), "manifest content paths are unsafe")
        updates = exact_object(manifest["updates"], ("releaseIndex",), "manifest.updates")
        require(https_url(updates["releaseIndex"]), "manifest.updates.releaseIndex must be an HTTPS URL")

    def check_module(self, manifest: Mapping[str, Any], module: Mapping[str, Any]) -> None:
        exact_object(module, ("$schema", "schemaVersion", "moduleId", "contributions"), "module")
        require(module["schemaVersion"] == "0.1" and module["moduleId"] == manifest["id"], "module schema/id is invalid")
        contributions = exact_object(module["contributions"], ("settings",), "module.contributions")
        settings = exact_object(contributions["settings"], ("section", "icon", "actions"), "module Settings")
        require(settings["section"] == "SpaceMusic" and settings["icon"] == manifest["content"]["icon"], "module Settings declaration is invalid")
        actions = settings["actions"]
        require(isinstance(actions, list) and len(actions) == 1, "v0.1 requires exactly one Settings action")
        action = exact_object(actions[0], ("id", "label", "action"), "module Settings action")
        require(action["id"] == action["action"] == "check-updates" and isinstance(action["label"], str) and 0 < len(action["label"]) <= 80 and action["label"].strip(), "v0.1 requires a labelled check-updates action")

    def check_package_layout(self, manifest: Mapping[str, Any]) -> None:
        require({manifest["content"]["module"], manifest["content"]["icon"]} == {"module/module.json", "assets/icon.svg"}, "v0.1 package paths are not the declared layout")
        for path in self.root.rglob("*"):
            if not path.is_file() or ignored(path, self.root):
                continue
            require(path.name.lower() != "androidmanifest.xml" and path.suffix.lower() not in FORBIDDEN_SUFFIXES, f"forbidden Android/plugin artifact: {path}")
        for key in PACKAGE_FILES:
            path = self.root.joinpath(*PurePosixPath(key).parts)
            require(path.is_file() and not path.is_symlink(), f"package file is missing or symlinked: {key}")
        for directory in ("module", "assets"):
            directory_path = self.root / directory
            require(directory_path.is_dir() and not directory_path.is_symlink(), f"package directory is missing or symlinked: {directory}")
            for path in directory_path.rglob("*"):
                require(not path.is_symlink(), f"package path is symlinked: {path}")
                if path.is_file():
                    require(path.relative_to(self.root).as_posix() in PACKAGE_FILES, f"unexpected package file: {path}")
        try:
            ElementTree.parse(self.root / "assets/icon.svg")
        except (OSError, ElementTree.ParseError) as error:
            raise ContractError(f"assets/icon.svg is not well-formed XML: {error}") from error

        releases = self.root / "releases"
        require(releases.is_dir() and not releases.is_symlink(), "releases directory is missing or symlinked")
        for entry in releases.iterdir():
            if entry.name == "index.json":
                require(entry.is_file() and not entry.is_symlink(), "releases/index.json is not a regular file")
                continue
            require(entry.is_dir() and not entry.is_symlink() and SEMVER_RE.fullmatch(entry.name) is not None, f"invalid release directory: {entry.name}")
            files = list(entry.iterdir())
            require(len(files) == 1 and files[0].name == "release.json" and files[0].is_file() and not files[0].is_symlink(), f"release directory {entry.name} must contain only release.json")

    def check_compatibility(self, value: Mapping[str, Any], label: str) -> None:
        exact_object(value, ("minConvxVersion", "maxConvxVersion"), label)
        minimum = parse_version(value["minConvxVersion"], f"{label}.minConvxVersion")
        if value["maxConvxVersion"] is not None:
            require(compare_versions(parse_version(value["maxConvxVersion"], f"{label}.maxConvxVersion"), minimum) >= 0, f"{label} maximum is older than minimum")

    def check_releases(self, manifest: Mapping[str, Any]) -> Dict[str, Mapping[str, Any]]:
        releases: Dict[str, Mapping[str, Any]] = {}
        for key in self.release_keys():
            version = PurePosixPath(key).parts[1]
            parse_version(version, f"{key} directory")
            release = self.document(key, "release.schema.json")
            exact_object(release, ("$schema", "schemaVersion", "moduleId", "version", "status", "compatibility", "artifact", "sha256", "notes"), key)
            require(release["schemaVersion"] == "0.1" and release["version"] == version and release["moduleId"] == manifest["id"], f"{key} identity is inconsistent")
            require(release["status"] in ("draft", "published") and isinstance(release["notes"], str) and len(release["notes"]) <= 500, f"{key} metadata is invalid")
            self.check_compatibility(release["compatibility"], f"{key}.compatibility")
            self.check_release_range(release, manifest, key)
            self.check_artifact_state(release, key)
            require(version not in releases, f"duplicate release version: {version}")
            releases[version] = release
        require(manifest["version"] in releases, f"no release descriptor matches manifest.version {manifest['version']}")
        return releases

    def release_keys(self) -> Sequence[str]:
        releases = self.root / "releases"
        return tuple(sorted(path.relative_to(self.root).as_posix() for path in releases.glob("*/release.json"))) if releases.is_dir() else ()

    def check_release_range(self, release: Mapping[str, Any], manifest: Mapping[str, Any], label: str) -> None:
        manifest_compatibility = manifest["compatibility"]
        release_compatibility = release["compatibility"]
        require(compare_versions(parse_version(release_compatibility["minConvxVersion"], f"{label} minimum"), parse_version(manifest_compatibility["minConvxVersion"], "manifest minimum")) >= 0, f"{label} broadens manifest minimum Convx version")
        manifest_max, release_max = manifest_compatibility["maxConvxVersion"], release_compatibility["maxConvxVersion"]
        if manifest_max is not None:
            require(release_max is not None and compare_versions(parse_version(release_max, f"{label} maximum"), parse_version(manifest_max, "manifest maximum")) <= 0, f"{label} broadens manifest maximum Convx version")

    @staticmethod
    def check_artifact_state(release: Mapping[str, Any], label: str) -> None:
        if release["status"] == "draft":
            require(release["artifact"] is None and release["sha256"] is None, f"draft release {label} must have null artifact and sha256")
            return
        require(https_url(release["artifact"]) and urlsplit(release["artifact"]).path.lower().endswith(".smod"), f"published release {label} must point to an HTTPS .smod artifact")
        require(isinstance(release["sha256"], str) and SHA256_RE.fullmatch(release["sha256"]) is not None, f"published release {label} must have a SHA-256 digest")

    def check_index(self, manifest: Mapping[str, Any], index: Mapping[str, Any], releases: Mapping[str, Mapping[str, Any]]) -> None:
        exact_object(index, ("$schema", "schemaVersion", "moduleId", "releases"), "release index")
        require(index["schemaVersion"] == "0.1" and index["moduleId"] == manifest["id"] and isinstance(index["releases"], list), "release index metadata is invalid")
        indexed: Dict[str, Mapping[str, Any]] = {}
        for number, release in enumerate(index["releases"]):
            label = f"release index entry {number}"
            require(isinstance(release, dict) and release.get("$schema") == SCHEMA_URL + "release.schema.json", f"{label} has the wrong schema reference")
            self.backend.validate(release, "release.schema.json")
            exact_object(release, ("$schema", "schemaVersion", "moduleId", "version", "status", "compatibility", "artifact", "sha256", "notes"), label)
            version = release["version"]
            require(release["moduleId"] == manifest["id"] and release["status"] == "published", f"{label} is not a published matching release")
            require(version not in indexed and version in releases, f"{label} has a duplicate or missing version")
            self.check_compatibility(release["compatibility"], f"{label}.compatibility")
            self.check_release_range(release, manifest, label)
            self.check_artifact_state(release, label)
            require(without_schema(release) == without_schema(releases[version]), f"release index entry {version} differs from its local descriptor")
            indexed[version] = release
        for version, release in releases.items():
            require((release["status"] == "published") == (version in indexed), f"release/index status mismatch for {version}")


def expect_archive_rejected(data: bytes, expected: Mapping[str, bytes], label: str) -> None:
    try:
        verify_smod(data, expected)
    except ContractError:
        return
    raise ContractError(f"negative archive fixture was accepted: {label}")


def package_round_trip(root: Path) -> int:
    source = {key: (root / PurePosixPath(key)).read_bytes() for key in PACKAGE_FILES}
    entries = [(key, source[key], None) for key in sorted(source)]
    first = archive_bytes(entries)
    second = archive_bytes(entries)
    require(first == second, "deterministic .smod packaging produced different bytes")
    verify_smod(first, source)
    verify_smod(second, source)

    for label, unsafe_name in (
        ("path traversal", "../manifest.json"),
        ("absolute path", "/manifest.json"),
        ("backslash path", r"module\\module.json"),
    ):
        unsafe = [(unsafe_name, source["manifest.json"], None)] + [
            (key, source[key], None) for key in sorted(source) if key != "manifest.json"
        ]
        expect_archive_rejected(archive_bytes(unsafe), source, label)

    duplicate = entries + [("manifest.json", source["manifest.json"], None)]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        duplicate_data = archive_bytes(duplicate)
    expect_archive_rejected(duplicate_data, source, "duplicate path")

    extra = entries + [("README.md", b"not part of the installed module", None)]
    expect_archive_rejected(archive_bytes(extra), source, "undeclared path")

    symlink = [
        (key, source[key], ((stat.S_IFLNK | 0o777) << 16) if key == "assets/icon.svg" else None)
        for key in sorted(source)
    ]
    expect_archive_rejected(archive_bytes(symlink), source, "symlink entry")

    dos_directory = [
        (key, source[key], DOS_DIRECTORY_ATTRIBUTE if key == "manifest.json" else None)
        for key in sorted(source)
    ]
    expect_archive_rejected(archive_bytes(dos_directory), source, "DOS directory metadata")

    mismatch = [
        (key, b"not the repository manifest" if key == "manifest.json" else source[key], None)
        for key in sorted(source)
    ]
    expect_archive_rejected(archive_bytes(mismatch), source, "content mismatch")
    return 7


def release_snapshot(root: Path) -> Dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (root / "releases").rglob("*") if path.is_file()
    }


def expect_rejected(root: Path, overrides: Mapping[str, Any], label: str) -> None:
    before = release_snapshot(root)
    try:
        RepositoryValidator(root, overrides).run()
    except ContractError:
        pass
    else:
        raise ContractError(f"negative mutation was accepted: {label}")
    require(release_snapshot(root) == before, f"negative mutation changed releases: {label}")


def mutation_checks(root: Path) -> int:
    manifest = read_json(root / "manifest.json")
    invalid = copy.deepcopy(manifest)
    invalid["content"]["module"] = "../outside.json"
    expect_rejected(root, {"manifest.json": invalid}, "path traversal")

    invalid = copy.deepcopy(manifest)
    invalid["version"] = "0.1"
    expect_rejected(root, {"manifest.json": invalid}, "invalid manifest SemVer")

    invalid = copy.deepcopy(manifest)
    invalid["permissions"] = ["network"]
    expect_rejected(root, {"manifest.json": invalid}, "unsupported permission")

    invalid = copy.deepcopy(manifest)
    invalid["compatibility"]["maxConvxVersion"] = "1.0.0"
    expect_rejected(root, {"manifest.json": invalid}, "inverted compatibility range")

    module = read_json(root / "module/module.json")
    invalid = copy.deepcopy(module)
    invalid["moduleId"] = "different-module"
    expect_rejected(root, {"module/module.json": invalid}, "module ID mismatch")

    release = read_json(root / "releases/0.1.0/release.json")
    invalid = copy.deepcopy(release)
    invalid["status"] = "published"
    expect_rejected(root, {"releases/0.1.0/release.json": invalid}, "published release without artifact")

    index = read_json(root / "releases/index.json")
    invalid = copy.deepcopy(index)
    invalid["releases"] = [copy.deepcopy(release)]
    expect_rejected(root, {"releases/index.json": invalid}, "draft release in index")

    published = copy.deepcopy(release)
    published.update({"status": "published", "artifact": "https://github.com/N7T0-OF/Spacemusic/releases/download/v0.1.0/SpaceMusic-0.1.0.smod", "sha256": "0" * 64})
    valid_index = copy.deepcopy(index)
    valid_index["releases"] = [copy.deepcopy(published)]
    RepositoryValidator(root, {"releases/0.1.0/release.json": published, "releases/index.json": valid_index}).run()

    ordering = ["1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0", "1.0.1"]
    parsed = [parse_version(value, "test version") for value in ordering]
    require(all(compare_versions(left, right) < 0 for left, right in zip(parsed, parsed[1:])), "SemVer precedence ordering failed")
    return 8


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    try:
        root = args.root.resolve()
        validator = RepositoryValidator(root)
        validator.run()
        rejected = mutation_checks(root)
    except (ContractError, OSError, KeyError, TypeError, IndexError, AttributeError) as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1
    print(f"PASS: {validator.json_count} JSON files and {len(SCHEMA_FILES)} schemas validated")
    print(f"PASS: {validator.backend.name}")
    print("PASS: package layout, paths, IDs, versions, compatibility, permissions, and actions")
    print("PASS: release tree/index consistency and draft-versus-published artifact rules")
    print(f"PASS: {rejected} invalid in-memory mutations rejected without release-tree changes")
    print(f"PASS: deterministic .smod round-trip matched {len(PACKAGE_FILES)} repository files")
    print(f"PASS: {validator.archive_rejections} invalid archive fixtures rejected")
    print("PASS: valid published release/index lifecycle accepted in memory")
    print("PASS: SemVer precedence ordering")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
