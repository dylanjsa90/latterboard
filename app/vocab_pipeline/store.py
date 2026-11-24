"""Draft files and wanted words, and approving a draft into a shipped word.

`vocab/drafts/<slug>.json` holds one draft each, with a status. `vocab/wanted.json` maps a
word that shipped entries want as an alternative to those entries, so approving it links
it back. Every file is written with `dump_entry`, so diffs stay small and stable.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal, TypedDict, cast

from app.game.vocab_validation import MAX_ALTERNATIVES, entry_errors, list_errors
from app.game.vocab_words import (
    ENTRIES_DIR,
    VocabWord,
    dump_entry,
    entry_filename,
    load_words,
    word_from_json,
    word_to_json,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DraftStatus = Literal["queued", "pending", "invalid", "rejected"]
Source = Literal["generated", "wanted", "backfill"]


class Draft(TypedDict):
    slug: str
    word: str
    # None until drafted for "wanted" drafts.
    pos: str | None
    difficulty: str | None
    source: Source
    status: DraftStatus
    attempts: int
    errors: list[str]
    # Why the reviewer rejected it.
    note: str
    # The proposed shipped entry; it has "id" only for a backfill.
    entry: dict[str, Any] | None


class WantedWord(TypedDict):
    word: str
    # Shipped slugs that want this word as an alternative.
    referrers: list[str]


class ApprovalError(Exception):
    """A draft that can't ship; nothing was written."""


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_entry(data), encoding="utf-8")


@dataclass(frozen=True)
class Store:
    entries_dir: Path = ENTRIES_DIR
    root: Path = REPO_ROOT / "vocab"

    def shipped(self) -> tuple[VocabWord, ...]:
        return load_words(self.entries_dir)

    def draft_path(self, slug: str) -> Path:
        return self.root / "drafts" / f"{slug}.json"

    def drafts(self) -> list[Draft]:
        paths = sorted((self.root / "drafts").glob("*.json"))
        return [cast(Draft, _read_json(p, None)) for p in paths]

    def draft(self, slug: str) -> Draft:
        path = self.draft_path(slug)
        if not path.exists():
            raise KeyError(f"no draft for {slug}")
        return cast(Draft, _read_json(path, None))

    def save_draft(self, d: Draft) -> None:
        _write(self.draft_path(d["slug"]), d)

    def reviewable(self) -> list[Draft]:
        return [
            d
            for d in self.drafts()
            if d["status"] == "pending"
            or (d["status"] == "invalid" and d["entry"] is not None)
        ]

    def known_slugs(self) -> set[str]:
        return {w.slug for w in self.shipped()} | {d["slug"] for d in self.drafts()}

    def wanted(self) -> dict[str, WantedWord]:
        return cast(dict[str, WantedWord], _read_json(self.root / "wanted.json", {}))

    def save_wanted(self, wanted: Mapping[str, WantedWord]) -> None:
        _write(
            self.root / "wanted.json",
            {
                slug: {"word": w["word"], "referrers": sorted(w["referrers"])}
                for slug, w in sorted(wanted.items())
            },
        )

    def _word(self, d: Draft, shipped: Sequence[VocabWord]) -> VocabWord:
        """The draft's entry as the word it would ship as. A backfill keeps its stored
        id; anything else takes the next one."""
        data = d["entry"]
        assert data is not None
        word_id = data.get("id") if d["source"] == "backfill" else len(shipped)
        return word_from_json({**data, "id": word_id})

    def draft_errors(self, d: Draft) -> list[str]:
        if d["entry"] is None:
            return ["no entry yet"]
        s = d["slug"]
        shipped = self.shipped()
        try:
            w = self._word(d, shipped)
        except (ValueError, TypeError, KeyError) as e:
            return [f"{s}: {e}"]
        errors = entry_errors(w, strict=True)
        if w.slug != s:
            errors.append(f"{s}: entry slug is {w.slug}")
        if d["source"] == "backfill":
            if not (
                isinstance(w.id, int)
                and 0 <= w.id < len(shipped)
                and shipped[w.id].slug == s
            ):
                errors.append(f"{s}: backfill id {w.id} is not the shipped {s}")
        else:
            if any(x.slug == w.slug for x in shipped):
                errors.append(f"{s}: already shipped")
            if any(x.word.casefold() == w.word.casefold() for x in shipped):
                errors.append(f"{s}: duplicate word {w.word}")
        return errors

    def approve(self, slug: str) -> list[Path]:
        """Ship a reviewable draft and return the entry files written. Unshipped
        alternatives wait in wanted.json; if this word was wanted, it is linked into its
        referrers. Raises ApprovalError, writing nothing, if anything fails validation."""
        try:
            d = self.draft(slug)
        except KeyError:
            raise ApprovalError(f"no draft for {slug}") from None
        if d not in self.reviewable():
            raise ApprovalError(f"{slug} is {d['status']}, not ready for review")
        errors = self.draft_errors(d)
        if errors:
            raise ApprovalError("\n".join(errors))

        shipped = self.shipped()
        by_slug = {w.slug: w for w in shipped}
        new = self._word(d, shipped)
        wanted = self.wanted()
        keep = []
        for alt in new.alternatives:
            if alt in by_slug:
                keep.append(alt)
                continue
            want = wanted.setdefault(alt, {"word": alt, "referrers": []})
            if slug not in want["referrers"]:
                want["referrers"] = sorted([*want["referrers"], slug])
        changed = {new.id: replace(new, alternatives=tuple(keep))}
        for ref in wanted.pop(slug, {"word": slug, "referrers": []})["referrers"]:
            w = by_slug.get(ref)
            if (
                w
                and slug not in w.alternatives
                and len(w.alternatives) < MAX_ALTERNATIVES
            ):
                changed[w.id] = replace(w, alternatives=(*w.alternatives, slug))

        final = [changed.get(w.id, w) for w in shipped]
        if new.id == len(shipped):
            final.append(changed[new.id])
        errors = list_errors(final)
        if errors:
            raise ApprovalError("\n".join(errors))

        paths = []
        for word_id in sorted(changed):
            w = changed[word_id]
            path = self.entries_dir / entry_filename(w.id, w.slug)
            _write(path, word_to_json(w))
            paths.append(path)
        self.draft_path(slug).unlink()
        self.save_wanted(wanted)
        return paths

    def reject(self, slug: str, note: str) -> None:
        """Keep the draft as rejected, so the word is never drafted again."""
        self.save_draft({**self.draft(slug), "status": "rejected", "note": note})

    def requeue(self, slug: str) -> None:
        self.save_draft(
            {
                **self.draft(slug),
                "status": "queued",
                "attempts": 0,
                "entry": None,
                "errors": [],
            }
        )

    def fmt(self) -> list[Path]:
        """Rewrite shipped files that aren't canonical, such as after a hand edit."""
        rewritten = []
        for path in sorted(self.entries_dir.glob("*.json")):
            text = path.read_text(encoding="utf-8")
            canonical = dump_entry(word_to_json(word_from_json(json.loads(text))))
            if text != canonical:
                path.write_text(canonical, encoding="utf-8")
                rewritten.append(path)
        return rewritten
