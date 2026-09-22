"""Audio material input validation layer (Task 16).

Decides whether a candidate audio file is a legal, ASR-eligible audio
material.  File-level checks only: existence, regular file, non-empty,
supported extension, readability.  No whole-file reads, no decoding,
no ASR, no language identification, no evidence generation.

Design:
    - Deterministic: same input + same file state -> equal results.
    - Read-only: validation never mutates the filesystem.
    - Structured failures: business failures are carried in
      AudioValidationResult (stable error codes), never raised.
    - Evidence-first: no course content inferred from filenames or
      metadata.
    - Stdlib only: zero new third-party dependencies.
"""

from __future__ import annotations

import os
import stat as _stat
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

from src.models import Material, MaterialType

# Authoritative audio extension set for this layer.
# Mirrors MaterialIndex._EXT_TO_TYPE and
# ClassSessionProcessor._AUDIO_EXTENSIONS (.mp3/.m4a/.wav/.ogg).
SUPPORTED_AUDIO_EXTENSIONS: tuple[str, ...] = (".mp3", ".m4a", ".wav", ".ogg")


class AudioValidationStatus(str, Enum):
    """High-level outcome of audio input validation."""

    VALID = "VALID"
    INVALID = "INVALID"

    @classmethod
    def from_string(cls, value: str) -> AudioValidationStatus:
        for s in cls:
            if s.value.lower() == str(value).strip().lower():
                return s
        return cls.INVALID


class AudioValidationError(str, Enum):
    """Stable, system-independent error codes.

    Ordinary validation failures are business results, not internal
    errors: they are carried inside AudioValidationResult instead of
    being raised as exceptions.
    """

    FILE_NOT_FOUND = "FILE_NOT_FOUND"
    NOT_A_FILE = "NOT_A_FILE"
    UNSUPPORTED_EXTENSION = "UNSUPPORTED_EXTENSION"
    EMPTY_FILE = "EMPTY_FILE"
    UNREADABLE = "UNREADABLE"


@dataclass
class AudioValidationResult:
    """Structured outcome of validating one audio material candidate.

    Comparison is by business fields only, so repeated calls against
    an unchanged file compare equal (determinism requirement).
    """

    valid: bool = False
    status: AudioValidationStatus = field(
        default_factory=lambda: AudioValidationStatus.INVALID
    )
    errors: tuple[AudioValidationError, ...] = field(default_factory=tuple)
    warnings: tuple[str, ...] = field(default_factory=tuple)
    path: str = ""
    extension: str = ""
    file_size: int = 0
    modified_time: float = 0.0
    material: Optional[Material] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "status": self.status.value,
            "errors": [e.value for e in self.errors],
            "warnings": list(self.warnings),
            "path": self.path,
            "extension": self.extension,
            "file_size": self.file_size,
            "modified_time": self.modified_time,
            "material": (
                self.material.to_dict() if self.material is not None else None
            ),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioValidationResult:
        known = set(AudioValidationError._value2member_map_)
        errors = tuple(
            AudioValidationError(code)
            for code in data.get("errors", [])
            if code in known
        )
        mat = data.get("material")
        return cls(
            valid=bool(data.get("valid", False)),
            status=AudioValidationStatus.from_string(
                str(data.get("status", "INVALID"))
            ),
            errors=errors,
            warnings=tuple(str(w) for w in data.get("warnings", [])),
            path=str(data.get("path", "")),
            extension=str(data.get("extension", "")),
            file_size=int(data.get("file_size", 0)),
            modified_time=float(data.get("modified_time", 0.0)),
            material=(
                Material.from_dict(mat) if isinstance(mat, dict) else None
            ),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class AudioInput:
    """Validated audio material, ready for a future ASR provider.

    Carries only file-level identity: which file, its format, its
    Material association, and file-level metadata.  It deliberately
    does NOT carry transcript, segments, speaker, language, or
    confidence fields - those are outputs of the Transcription layer,
    not inputs.
    """

    path: str = ""
    extension: str = ""
    material: Optional[Material] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def material_id(self) -> Optional[str]:
        """Material id when the input was validated from a Material."""
        return self.material.material_id if self.material is not None else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "extension": self.extension,
            "material": (
                self.material.to_dict() if self.material is not None else None
            ),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AudioInput:
        mat = data.get("material")
        return cls(
            path=data.get("path", ""),
            extension=data.get("extension", ""),
            material=(
                Material.from_dict(mat) if isinstance(mat, dict) else None
            ),
            metadata=dict(data.get("metadata", {})),
        )


def _fail(code: AudioValidationError, path: str = "") -> AudioValidationResult:
    return AudioValidationResult(valid=False, errors=(code,), path=path)


class AudioMaterialValidator:
    """Validates candidate audio material files for the ASR pipeline.

    Usage::

        validator = AudioMaterialValidator()
        result = validator.validate(path)        # path-like input
        result = validator.validate(material)    # existing Material

    All check failures are returned inside the AudioValidationResult;
    only internal programmer errors raise exceptions.  Validation is
    strictly read-only and deterministic.
    """

    supported_extensions: tuple[str, ...] = SUPPORTED_AUDIO_EXTENSIONS

    def validate(
        self,
        material_or_path: Union[Material, str, "os.PathLike[str]"],
    ) -> AudioValidationResult:
        """Validate a single audio material candidate.

        When a Material is passed, its indexed metadata (size, sha256,
        mtime, relative_path) is reused rather than recomputed, and
        its material_id is carried into the result.  When a raw path
        is passed, the check is performed directly on the filesystem.
        The input is never mutated.
        """
        material: Optional[Material] = None
        if isinstance(material_or_path, Material):
            material = material_or_path
            path_str: str = material.path or material.filename
            metadata = dict(material.metadata)
        else:
            path_str = os.fspath(material_or_path)
            metadata = {}

        if not path_str:
            return _fail(AudioValidationError.FILE_NOT_FOUND)

        return self._validate_path(path_str, metadata, material)

    def to_audio_input(self, result: AudioValidationResult) -> AudioInput:
        """Build an AudioInput from a VALID result.

        Raises:
            ValueError: if the result is not valid.
        """
        if not result.valid:
            raise ValueError(
                "Cannot build AudioInput from an invalid result: "
                + ", ".join(e.value for e in result.errors)
            )
        return AudioInput(
            path=result.path,
            extension=result.extension,
            material=result.material,
            metadata=dict(result.metadata),
        )

    # ------------------------------------------------------------------

    def _validate_path(
        self,
        path_str: str,
        metadata: dict[str, Any],
        material: Optional[Material],
    ) -> AudioValidationResult:
        p = Path(path_str)

        # 1. Existence
        try:
            st = p.stat()
        except OSError:
            return _fail(AudioValidationError.FILE_NOT_FOUND, path_str)

        errors: list[AudioValidationError] = []

        # 2. Regular file (directories are rejected even if named *.mp3)
        if not _stat.S_ISREG(st.st_mode):
            errors.append(AudioValidationError.NOT_A_FILE)

        # 3. Extension - case-insensitive; original path is never mutated
        ext = p.suffix.lower()
        if ext not in self.supported_extensions:
            errors.append(AudioValidationError.UNSUPPORTED_EXTENSION)

        # 4. Non-empty
        if st.st_size == 0:
            errors.append(AudioValidationError.EMPTY_FILE)

        # 5. Readable (lightweight access check; contents are NOT read)
        if not os.access(p, os.R_OK):
            errors.append(AudioValidationError.UNREADABLE)

        status = (
            AudioValidationStatus.INVALID
            if errors
            else AudioValidationStatus.VALID
        )
        return AudioValidationResult(
            valid=not errors,
            status=status,
            errors=tuple(errors),
            warnings=(),
            path=path_str,
            extension=ext,
            file_size=st.st_size,
            modified_time=st.st_mtime,
            material=material,
            metadata=metadata,
        )
