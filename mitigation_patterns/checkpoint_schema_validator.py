"""
Checkpoint Schema Validator — Mitigation for AFT-020.

Validates checkpoint dicts against a versioned schema registry.
When a schema mismatch is detected, applies migration functions
in version order rather than silently evolving.

No Databricks or Delta Lake dependencies. Works with any dict-based
checkpoint format.
"""
from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable


class CheckpointSchemaMismatchError(Exception):
    """Raised when a checkpoint's schema doesn't match any known version."""

    def __init__(self, message: str, stored_version: int, expected_version: int, diff: dict[str, Any] | None = None):
        super().__init__(message)
        self.stored_version = stored_version
        self.expected_version = expected_version
        self.diff = diff


class MigrationError(Exception):
    """Raised when a migration function fails."""

    def __init__(self, message: str, from_version: int, to_version: int):
        super().__init__(message)
        self.from_version = from_version
        self.to_version = to_version


@dataclass
class SchemaField:
    """Definition of a field in a checkpoint schema."""
    name: str
    field_type: type | tuple[type, ...]
    required: bool = True
    default: Any = None

    def validate(self, value: Any) -> bool:
        if value is None:
            return not self.required
        return isinstance(value, self.field_type)


@dataclass
class SchemaVersion:
    """A versioned checkpoint schema definition."""
    version: int
    fields: list[SchemaField]
    description: str = ""

    def validate(self, data: dict[str, Any]) -> tuple[bool, list[str]]:
        """Validate a dict against this schema. Returns (is_valid, errors)."""
        errors = []
        for f in self.fields:
            if f.name not in data:
                if f.required:
                    errors.append(f"Missing required field: '{f.name}'")
            elif not f.validate(data[f.name]):
                errors.append(
                    f"Field '{f.name}': expected {f.field_type.__name__ if isinstance(f.field_type, type) else f.field_type}, "
                    f"got {type(data[f.name]).__name__} (value: {data[f.name]!r})"
                )
        return len(errors) == 0, errors

    def diff(self, other: "SchemaVersion") -> dict[str, Any]:
        """Compute the difference between two schema versions."""
        self_fields = {f.name: f for f in self.fields}
        other_fields = {f.name: f for f in other.fields}

        added = [f.name for f in other.fields if f.name not in self_fields]
        removed = [f.name for f in self.fields if f.name not in other_fields]
        changed = []
        for name in self_fields:
            if name in other_fields:
                sf, of = self_fields[name], other_fields[name]
                if sf.field_type != of.field_type or sf.required != of.required:
                    changed.append(name)

        return {"added": added, "removed": removed, "changed": changed}


MigrationFn = Callable[[dict[str, Any]], dict[str, Any]]


class CheckpointSchemaValidator:
    """Validates and migrates checkpoint dicts across schema versions.

    Usage:
        validator = CheckpointSchemaValidator(version_key="_schema_version")

        v1 = validator.register_version(1, [
            SchemaField("session_id", str),
            SchemaField("messages", list),
        ])

        v2 = validator.register_version(2, [
            SchemaField("session_id", str),
            SchemaField("messages", list),
            SchemaField("user_tier", str),
        ])

        validator.register_migration(1, 2, lambda state: {**state, "user_tier": "standard"})

        # On checkpoint load:
        migrated = validator.validate_and_migrate(raw_checkpoint)

    Args:
        version_key: The dict key used to store the schema version in checkpoints.
        default_version: Version to assume for checkpoints that lack a version key.
    """

    def __init__(self, version_key: str = "_schema_version", default_version: int = 1):
        self.version_key = version_key
        self.default_version = default_version
        self._schemas: dict[int, SchemaVersion] = {}
        self._migrations: dict[tuple[int, int], MigrationFn] = {}

    @property
    def current_version(self) -> int:
        if not self._schemas:
            raise RuntimeError("No schema versions registered")
        return max(self._schemas.keys())

    def register_version(self, version: int, fields: list[SchemaField], description: str = "") -> SchemaVersion:
        """Register a schema version.

        Args:
            version: Version number (must be unique, typically sequential).
            fields: List of SchemaField definitions.
            description: Human-readable description of what changed in this version.

        Returns:
            The registered SchemaVersion.
        """
        schema = SchemaVersion(version=version, fields=fields, description=description)
        self._schemas[version] = schema
        return schema

    def register_migration(self, from_version: int, to_version: int, fn: MigrationFn) -> None:
        """Register a migration function between two versions.

        Args:
            from_version: Source version.
            to_version: Target version (typically from_version + 1).
            fn: Function that takes a state dict and returns a migrated state dict.
        """
        if from_version not in self._schemas:
            raise ValueError(f"Source version {from_version} not registered")
        if to_version not in self._schemas:
            raise ValueError(f"Target version {to_version} not registered")
        self._migrations[(from_version, to_version)] = fn

    def validate(self, data: dict[str, Any], version: int | None = None) -> tuple[bool, list[str]]:
        """Validate a checkpoint against a specific schema version.

        Args:
            data: The checkpoint dict.
            version: Schema version to validate against. Defaults to current version.

        Returns:
            Tuple of (is_valid, list_of_errors).
        """
        version = version or self.current_version
        schema = self._schemas.get(version)
        if schema is None:
            return False, [f"Unknown schema version: {version}"]
        return schema.validate(data)

    def migrate(self, data: dict[str, Any], from_version: int | None = None, to_version: int | None = None) -> dict[str, Any]:
        """Apply migration functions to move a checkpoint between schema versions.

        Args:
            data: The checkpoint dict (not modified in place).
            from_version: Source version. Detected from data if not provided.
            to_version: Target version. Defaults to current version.

        Returns:
            Migrated checkpoint dict.

        Raises:
            CheckpointSchemaMismatchError: If no migration path exists.
            MigrationError: If a migration function fails.
        """
        data = deepcopy(data)

        if from_version is None:
            from_version = data.get(self.version_key, self.default_version)
        if to_version is None:
            to_version = self.current_version

        if from_version == to_version:
            return data

        current = from_version
        while current < to_version:
            next_ver = current + 1
            fn = self._migrations.get((current, next_ver))
            if fn is None:
                raise CheckpointSchemaMismatchError(
                    f"No migration from v{current} to v{next_ver}. "
                    f"Cannot reach target v{to_version} from v{from_version}.",
                    stored_version=from_version,
                    expected_version=to_version,
                    diff=self._schemas[current].diff(self._schemas[next_ver]) if next_ver in self._schemas else None,
                )
            try:
                data = fn(data)
            except Exception as e:
                raise MigrationError(
                    f"Migration v{current}→v{next_ver} failed: {e}",
                    from_version=current,
                    to_version=next_ver,
                ) from e

            data[self.version_key] = next_ver
            current = next_ver

        return data

    def validate_and_migrate(self, data: dict[str, Any]) -> dict[str, Any]:
        """Validate a checkpoint, migrating if needed, and return the result.

        This is the primary entry point for checkpoint loading.

        Args:
            data: Raw checkpoint dict.

        Returns:
            Validated and migrated checkpoint dict at the current schema version.

        Raises:
            CheckpointSchemaMismatchError: If validation fails after migration.
            MigrationError: If a migration function fails.
        """
        stored_version = data.get(self.version_key, self.default_version)
        target = self.current_version

        if stored_version != target:
            data = self.migrate(data, from_version=stored_version, to_version=target)

        is_valid, errors = self.validate(data, version=target)
        if not is_valid:
            raise CheckpointSchemaMismatchError(
                f"Checkpoint failed validation after migration to v{target}: {errors}",
                stored_version=stored_version,
                expected_version=target,
            )

        return data


if __name__ == "__main__":
    print("Checkpoint Schema Validator — Demo")
    print("=" * 50)

    validator = CheckpointSchemaValidator()

    # Register schema versions
    validator.register_version(1, [
        SchemaField("session_id", str),
        SchemaField("messages", list),
        SchemaField("current_agent", str),
    ], description="Initial schema")

    validator.register_version(2, [
        SchemaField("session_id", str),
        SchemaField("messages", list),
        SchemaField("current_agent", str),
        SchemaField("user_tier", str),
    ], description="Added user_tier for routing")

    validator.register_version(3, [
        SchemaField("session_id", str),
        SchemaField("messages", list),
        SchemaField("current_agent", str),
        SchemaField("user_tier", str),
        SchemaField("context_budget_used", (int, float), required=False),
    ], description="Added optional context budget tracking")

    # Register migrations
    validator.register_migration(1, 2, lambda s: {**s, "user_tier": "standard"})
    validator.register_migration(2, 3, lambda s: {**s, "context_budget_used": 0})

    # Demo 1: Load a v1 checkpoint — should migrate to v3
    print("\n1. Migrating v1 checkpoint to v3:")
    v1_checkpoint = {
        "session_id": "abc123",
        "messages": [{"role": "user", "content": "Hello"}],
        "current_agent": "intake",
    }
    result = validator.validate_and_migrate(v1_checkpoint)
    print(f"   Input:  v1 — fields: {list(v1_checkpoint.keys())}")
    print(f"   Output: v{result['_schema_version']} — fields: {list(result.keys())}")
    print(f"   user_tier: {result['user_tier']!r} (migrated default)")

    # Demo 2: Load a v3 checkpoint — no migration needed
    print("\n2. Loading current-version checkpoint:")
    v3_checkpoint = {
        "_schema_version": 3,
        "session_id": "def456",
        "messages": [],
        "current_agent": "router",
        "user_tier": "enterprise",
        "context_budget_used": 4500,
    }
    result2 = validator.validate_and_migrate(v3_checkpoint)
    print(f"   Loaded v{result2['_schema_version']} — no migration needed")

    # Demo 3: Validation failure
    print("\n3. Detecting invalid checkpoint:")
    bad_checkpoint = {
        "_schema_version": 3,
        "session_id": "ghi789",
        "messages": "not a list",  # Wrong type
        "current_agent": "router",
        "user_tier": "enterprise",
    }
    try:
        validator.validate_and_migrate(bad_checkpoint)
    except CheckpointSchemaMismatchError as e:
        print(f"   Caught: {e}")
