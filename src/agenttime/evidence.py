"""Content-addressed local fixture evidence, not production durable storage."""

from contextlib import contextmanager
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import uuid


class IntegrityError(ValueError):
    """Stored evidence disagrees with its immutable content identity."""


def canonical_json(value) -> bytes:
    """Encode strict JSON as sorted, compact UTF-8, without coercing keys/types."""
    active = set()

    def validate(item):
        if item is None or type(item) in (str, bool, int, float):
            return
        if type(item) not in (list, dict):
            raise TypeError("Evidence must contain only JSON values")
        if id(item) in active:
            raise ValueError("Circular JSON value")
        active.add(id(item))
        try:
            if type(item) is dict:
                if any(type(key) is not str for key in item):
                    raise TypeError("JSON object keys must be strings")
                children = item.values()
            else:
                children = item
            for child in children:
                validate(child)
        finally:
            active.remove(id(item))

    validate(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


class EvidenceStore:
    """A local store with atomic no-overwrite publication and verified reads.

    The root and objects cannot be symlinks. Ancestor directories are trusted;
    this is not a security boundary against other writers on the local host.
    """

    def __init__(self, root: Path):
        self.root = Path(root)
        if self.root.is_symlink():
            raise IntegrityError("Evidence root cannot be a symlink")
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except FileExistsError as exc:
            raise IntegrityError("Evidence root must be a directory") from exc
        with self._directory():
            pass

    @contextmanager
    def _directory(self):
        try:
            descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        except OSError as exc:
            if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                raise IntegrityError("Evidence root must be a directory, not a symlink") from exc
            raise
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _read(directory: int, digest: str) -> bytes:
        try:
            descriptor = os.open(digest, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=directory)
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise IntegrityError("Evidence object cannot be a symlink") from exc
            raise
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise IntegrityError("Evidence object must be a regular file")
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                data = source.read()
        finally:
            os.close(descriptor)
        if hashlib.sha256(data).hexdigest() != digest:
            raise IntegrityError(f"Evidence hash disagreement: {digest}")
        return data

    def get(self, digest: str) -> bytes:
        """Return verified bytes; missing evidence remains FileNotFoundError."""
        if type(digest) is not str or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise ValueError("Evidence digest must be 64 lowercase hexadecimal characters")
        with self._directory() as directory:
            return self._read(directory, digest)

    def put(self, data: bytes) -> str:
        """Publish complete bytes once; existing objects must verify unchanged."""
        if type(data) is not bytes:
            raise TypeError("Evidence data must be bytes")
        digest = hashlib.sha256(data).hexdigest()
        with self._directory() as directory:
            try:
                self._read(directory, digest)
            except FileNotFoundError:
                pass
            else:
                os.fsync(directory)
                return digest

            temporary = f".pending-{uuid.uuid4().hex}"
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as output:
                    output.write(data)
                    output.flush()
                    os.fsync(output.fileno())
                try:
                    os.link(temporary, digest, src_dir_fd=directory,
                            dst_dir_fd=directory, follow_symlinks=False)
                except FileExistsError:
                    # A concurrent publisher won. Its bytes still have to verify.
                    self._read(directory, digest)
            finally:
                os.unlink(temporary, dir_fd=directory)
            os.fsync(directory)
        return digest
