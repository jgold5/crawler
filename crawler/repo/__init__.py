"""Reader/writer for the product owner's XML locator repository (one XML file per page, per UI version).

    repo = LocatorRepo.load(Path("pages"), "aggrid")
    repo.get("settings", "Save").render()
    edit = repo.propose(repo.get("settings", "Save"), "//section[...]//button[normalize-space()='Save']")
    print(diff([edit])); apply([edit])

Tag names are learned from the files (see `_schema`), overridable with `repo.yaml`. Writes are minimal
byte-span edits verified by re-parsing (see `_edit`).
"""
from ._edit import Edit, Expect, Span, apply, diff
from ._model import (AmbiguousTargetError, NameCollisionError, RepoEntry, RepoError, RepoKeyError, StaleEntryError,
                     VerifyError, split_template)
from ._repo import LocatorRepo, RepoWarning
from ._schema import RepoSchema

__all__ = ["LocatorRepo", "RepoEntry", "RepoSchema", "Edit", "Expect", "Span", "split_template", "diff", "apply",
           "RepoError", "RepoKeyError", "AmbiguousTargetError", "NameCollisionError", "StaleEntryError",
           "VerifyError", "RepoWarning"]
