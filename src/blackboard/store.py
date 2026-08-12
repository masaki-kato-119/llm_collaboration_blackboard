"""Filesystem persistence, locking, and atomic Markdown replacement."""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ValidationError
from .frontmatter import dump, parse
from .models import require_id, validate_document

_IN_PROCESS_PLAN_LOCK = threading.RLock()
DEFAULT_PLAN_ID = "project"


@dataclass(frozen=True)
class Document:
    path: Path
    metadata: dict[str, Any]
    body: str


class MarkdownStore:
    """A single-host Markdown store.

    The lock file and all temporary files live under ``root``. Callers must keep
    the whole Blackboard on one volume; ``os.replace`` is then atomic.
    """

    DIRECTORIES = ("memory", "plan", "state", "event")

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self._event_cache: list[Document] | None = None

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for name in self.DIRECTORIES:
            (self.root / name).mkdir(exist_ok=True)

    def plan_path(self, plan_id: str = DEFAULT_PLAN_ID) -> Path:
        return self.root / "plan" / f"{require_id(plan_id, 'plan_id')}.md"

    def list_plan_ids(self) -> list[str]:
        self.initialize()
        return sorted(path.stem for path in (self.root / "plan").glob("*.md"))

    def list_memory_ids(self) -> list[str]:
        return self._list_document_ids("memory")

    def list_state_ids(self) -> list[str]:
        return self._list_document_ids("state")

    def memory_path(self, document_id: str) -> Path:
        return self.root / "memory" / f"{document_id}.md"

    def state_path(self, document_id: str) -> Path:
        return self.root / "state" / f"{document_id}.md"

    def event_path(self, document_id: str) -> Path:
        return self.root / "event" / f"{document_id}.md"

    def read(self, path: Path, document_type: str) -> Document:
        if not path.exists():
            raise NotFoundError(f"{document_type} document does not exist: {path.name}")
        metadata, body = parse(path.read_text(encoding="utf-8"))
        validate_document(metadata, document_type)
        return Document(path=path, metadata=metadata, body=body)

    def write(self, path: Path, metadata: dict[str, Any], body: str) -> Document:
        self._assert_internal(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        rendered = dump(metadata, body)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", suffix=".md", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.remove(temporary_name)
        if path.parent.name == "event":
            self.invalidate_event_cache()
        return Document(path=path, metadata=metadata, body=body)

    def invalidate_event_cache(self) -> None:
        self._event_cache = None

    @contextlib.contextmanager
    def plan_lock(self, plan_id: str = DEFAULT_PLAN_ID, timeout_seconds: float = 5.0) -> Iterator[None]:
        """Acquire a cross-process advisory lock for Plan compare-and-set."""
        plan_id = require_id(plan_id, "plan_id")
        self.initialize()
        lock_path = self.root / f".plan.{plan_id}.lock"
        with _IN_PROCESS_PLAN_LOCK:
            with open(lock_path, "a+b") as handle:
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                self._lock(handle, timeout_seconds)
                try:
                    yield
                finally:
                    self._unlock(handle)

    def list_events(self) -> list[Document]:
        self.initialize()
        if self._event_cache is not None:
            return list(self._event_cache)
        events = [self.read(path, "event") for path in self.root.joinpath("event").glob("*.md")]
        events = sorted(events, key=lambda item: str(item.metadata["created"]), reverse=True)
        self._event_cache = events
        return list(events)

    def _assert_internal(self, path: Path) -> None:
        try:
            path.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ValidationError("document path must be inside BLACKBOARD_ROOT") from exc

    def _list_document_ids(self, directory: str) -> list[str]:
        self.initialize()
        return sorted(path.stem for path in (self.root / directory).glob("*.md"))

    @staticmethod
    def _lock(handle: Any, timeout_seconds: float) -> None:
        deadline = time.monotonic() + timeout_seconds
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    raise ValidationError("timed out waiting for the Plan lock") from exc
                time.sleep(0.02)

    @staticmethod
    def _unlock(handle: Any) -> None:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
