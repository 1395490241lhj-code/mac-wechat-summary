"""Identity resolution: exact lookup, fixed precedence, ambiguity refused.

The resolver is handed candidate evidence -- who a sender identifier is called,
by what kind of name, and in which room -- and produces the two mappings the
leaf parser already accepts, plus one number. Resolution is a lookup and never
an inference: there is no fuzzy match, no edit distance, no tokenisation, no
case-folding, no trimming, and no heuristic. Exact strings are evidence.

For one identifier the applicable kinds are considered in a fixed order, and
the first kind that has any applicable evidence decides. One distinct name
resolves. Two or more distinct names are ambiguity, and ambiguity is refused
rather than resolved: the identifier stays absent from the mapping, so the
parser falls back to the identifier itself, and the unresolved count goes up
by one. An ambiguous stronger kind never falls through to a weaker one, because
that would silently bypass an unresolved stronger source.

Identity is not coverage. A missing or ambiguous name leaves a message present
and unnamed; it changes nothing about what a read is entitled to claim, and
this module knows nothing of those claims. The session-name mapping arrives
already in the parser's own shape and is preserved as a copy, never derived,
parsed or hashed here. Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

# -- kinds of name, strongest first ------------------------------------------

NAME_ROOM_MEMBER = "room_member"
NAME_CONTACT_REMARK = "contact_remark"
NAME_CONTACT_NICKNAME = "contact_nickname"

NAME_PRECEDENCE = (
    NAME_ROOM_MEMBER,
    NAME_CONTACT_REMARK,
    NAME_CONTACT_NICKNAME,
)


@dataclass(frozen=True, slots=True)
class NameCandidate:
    """One piece of evidence: an identifier is called ``name``, by ``kind``.

    A room-member name carries the room it applies in and applies nowhere
    else; a contact remark or nickname is global and carries no room. Strings
    are taken exactly as given: a whitespace-only name is still a name.
    """

    identifier: str
    kind: str
    name: str
    room: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.identifier, str) or not self.identifier:
            raise ValueError("a name candidate needs an identifier")
        if self.kind not in NAME_PRECEDENCE:
            raise ValueError("name kind is not in the closed vocabulary")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("a name candidate needs a name")
        if self.kind == NAME_ROOM_MEMBER:
            if not isinstance(self.room, str) or not self.room:
                raise ValueError("a room member name needs its room")
        elif self.room is not None:
            raise ValueError("a contact name has no room")


@dataclass(frozen=True, slots=True)
class ResolvedIdentities:
    """Exactly what one resolution produced, and nothing else.

    ``session_names`` is the parser-shaped mapping the resolver was given;
    ``display_names`` maps each resolved identifier to its one exact name;
    ``unresolved`` counts the identifiers whose strongest applicable kind was
    ambiguous in this call. Both mappings are fresh copies.
    """

    session_names: Mapping[str, str]
    display_names: Mapping[str, str]
    unresolved: int


class IdentityResolver:
    """Resolves display names from candidates, per call, without guessing."""

    def __init__(
        self,
        candidates: Sequence[NameCandidate],
        *,
        session_names: Mapping[str, str] | None = None,
    ) -> None:
        # Snapshots, so a caller mutating its own containers later changes
        # nothing here.
        self._candidates = tuple(candidates)
        self._session_names = dict(session_names or {})

    def resolve(self, *, room: str | None = None) -> ResolvedIdentities:
        """Names for this room, and how many identifiers were ambiguous.

        A room-member candidate is applicable only when ``room`` is that
        candidate's room; contact names are always applicable. The count is
        computed for this call alone and is never carried across calls.
        """
        applicable: dict[str, dict[str, set[str]]] = {}
        for candidate in self._candidates:
            if candidate.kind == NAME_ROOM_MEMBER and (room is None or candidate.room != room):
                continue
            by_kind = applicable.setdefault(candidate.identifier, {})
            by_kind.setdefault(candidate.kind, set()).add(candidate.name)

        display_names: dict[str, str] = {}
        unresolved = 0
        for identifier, by_kind in applicable.items():
            for kind in NAME_PRECEDENCE:
                names = by_kind.get(kind)
                if not names:
                    continue
                if len(names) == 1:
                    display_names[identifier] = next(iter(names))
                else:
                    unresolved += 1
                break  # the first applicable kind decides; nothing weaker is consulted

        return ResolvedIdentities(
            session_names=dict(self._session_names),
            display_names=display_names,
            unresolved=unresolved,
        )
