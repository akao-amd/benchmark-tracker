"""The working .bta on disk. Mutations replace the file only after they succeed."""

from __future__ import annotations

import copy
import threading
from pathlib import Path

from .archive import Archive, ArchiveError, Conflict


class Workspace:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.Lock()
        self.archive = Archive()
        self.hold = False
        self.load()

    def load(self) -> None:
        if not self.path.is_file():
            return
        try:
            self.archive = Archive.load(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, ArchiveError) as exc:
            bad = self.path.with_name(self.path.name + ".bad")
            try:
                self.path.replace(bad)
            except OSError:
                self.hold = True
                self.archive = Archive()
                self.archive.add_notice(str(self.path), f"working file could not be read ({exc}), and it could not be moved aside. Nothing will be saved over it.")
                return
            self.archive = Archive()
            self.archive.add_notice(str(bad), f"working file could not be read ({exc}). It was moved aside.")

    def save(self) -> None:
        if self.hold:
            raise ArchiveError("the working file could not be moved aside, so it was not overwritten")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".bta.tmp")
        tmp.write_text(self.archive.dumps(), encoding="utf-8")
        tmp.replace(self.path)

    def mutate(self, change) -> Archive:
        with self.lock:
            snapshot = copy.deepcopy(self.archive)
            try:
                change(self.archive)
                self.save()
            except Conflict:
                self.archive = snapshot
                raise
            except Exception:
                self.archive = snapshot
                raise
            return self.archive
