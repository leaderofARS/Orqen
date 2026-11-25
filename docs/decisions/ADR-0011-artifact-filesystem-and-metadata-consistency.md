# ADR-0011: Artifact Filesystem and Metadata Consistency

- **Status:** Accepted
- **Date:** 2026-11-25

## Context

Large binary or text outputs must not bloat SQLite rows, while artifact metadata must remain queryable and consistent with on-disk files.

## Decision

1. **Split Storage:** Store artifact bytes on disk under `<artifacts_root>/<session_id>/<artifact_id>` and store `ArtifactMetadata` (including `sha256` checksum, `size_bytes`, `content_type`, `filename`) in SQLite.
2. **Security:** Reject path-traversal characters and null bytes in filenames; never use user/model-supplied filenames directly as filesystem paths; write atomically via a temporary file + `os.replace`.
3. **Consistency:** Write the file first, then insert metadata into SQLite. If the SQLite insert fails, delete the newly written file as a compensating cleanup. On `delete()`, remove the SQLite metadata first and then unlink the file.

## Consequences

- Path traversal and filename collisions are prevented by design.
- Cross-system atomicity between filesystem and SQLite is best-effort with explicit compensating cleanup (documented honestly).
