"""Decide whether an item belongs to Sanitizers, LLC backend (PowerPC only) or Clang frontend.

Evidence, strongest first:

1. GitHub labels / Discourse categories and tags (curated by the LLVM community)
2. *Strong* keywords (unambiguous: ``ASan``, ``TableGen``, ``Sema`` ...)
3. *Weak* keywords (``codegen``, ``parser`` ...) - two distinct ones are needed

The LLC Backend domain is deliberately **PowerPC-only**: it needs either PowerPC-specific
evidence on its own (the PowerPC Discourse category, the ``backend:PowerPC`` label) or a generic
backend signal (``llc``, ``TableGen``, ``codegen`` ...) *together with* a PowerPC mention.
Other targets (X86, AArch64, ...) never match on their own.

An item may match several domains; the primary one follows ``DOMAIN_PRIORITY``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .config import Config
from .models import DOMAIN_PRIORITY, Classification, Domain, Item

_FLAGS = re.IGNORECASE

_STRONG: dict[Domain, tuple[re.Pattern[str], ...]] = {
    Domain.SANITIZERS: (
        re.compile(r"\b(?:gwp-?asan|hwasan|asan|msan|tsan|ubsan|lsan|dfsan|dsan|rtsan|tysan|nsan|esan)\b", _FLAGS),
        # no leading \b: CamelCase names such as DoubleFreeSanitizer / RealtimeSanitizer / TypeSanitizer
        re.compile(r"sanitizers?\b", _FLAGS),
        re.compile(r"-fsanitize\b", _FLAGS),
    ),
    Domain.LLC: (
        re.compile(r"\bllc\b", _FLAGS),
        re.compile(r"\btablegen\b", _FLAGS),
        re.compile(r"\b(?:isel|selectiondag|gisel|globalisel|sdag)\b", _FLAGS),
        re.compile(r"\binstruction (?:selection|scheduling|legali[sz]\w+)\b", _FLAGS),
        re.compile(r"\b(?:regalloc|register allocat\w+)\b", _FLAGS),
        re.compile(r"\bmachine ?(?:pass(?:es)?|function|instr\w*|ir|scheduler|verifier|outliner|combiner|code)\b", _FLAGS),
        re.compile(r"\b(?:asmprinter|code emission|legali[sz]er)\b", _FLAGS),
    ),
    Domain.CLANG: (
        re.compile(r"\bsema\b", _FLAGS),
        re.compile(r"\bsemantic analysis\b", _FLAGS),
        re.compile(r"\bclang::", _FLAGS),
        re.compile(r"\bclang[ -](?:frontend|driver|parser|diagnostics?|attributes?|extensions?|cl)\b", _FLAGS),
        re.compile(r"\b(?:libclang|clang static analy[sz]er|clang-analyzer)\b", _FLAGS),
    ),
}

#: A PowerPC mention is mandatory for any keyword-based LLC match.
_POWERPC = re.compile(r"\b(?:powerpc|ppc(?:32|64\w*)?|power(?:8|9|10|11)|pwr(?:8|9|10|11))\b", _FLAGS)
_WEAK: dict[Domain, tuple[re.Pattern[str], ...]] = {
    Domain.LLC: (
        re.compile(r"\b(?:codegen|code generation)\b", _FLAGS),
        re.compile(r"\bbackends?\b", _FLAGS),
        re.compile(r"\b(?:calling convention|scheduling model|lowering)\b", _FLAGS),
        re.compile(r"\b(?:altivec|vsx|xcoff|elfv[12]|mma)\b", _FLAGS),  # PowerPC-specific backend topics
    ),
    Domain.CLANG: (
        re.compile(r"\bclang\b(?!-(?:tidy|format|doc|query|repl|include-cleaner|apply-replacements))", _FLAGS),
        re.compile(r"\b(?:parser|lexer|preprocessor|frontend|driver)\b", _FLAGS),
        re.compile(r"\b(?:ast|diagnostics?)\b", _FLAGS),
        re.compile(r"\b(?:c\+\+\s?(?:2\w|\d\d)|c23|c2y)\b", _FLAGS),
    ),
}

_SANITIZER_LABEL = re.compile(r"^compiler-rt:\w*san(?:itizer)?$", _FLAGS)
_POWERPC_LABEL = "backend:powerpc"
#: Reason prefix for target-agnostic evidence; such evidence alone never selects LLC.
_GENERIC = "generic:"
#: Target-agnostic backend labels: only relevant when the text also mentions PowerPC.
_GENERIC_LLC_LABELS = frozenset({"tools:llc", "llvm:codegen", "llvm:globalisel", "tablegen", "llvm:selectiondag"})
_CLANG_LABELS = frozenset({"clang", "extension:clang", "clang-cl"})
_DISCOURSE_CLANG_TAGS = frozenset({"clang"})


@dataclass
class _Evidence:
    reasons: list[str] = field(default_factory=list)
    strong: bool = False


def label_domains(labels: tuple[str, ...]) -> dict[Domain, list[str]]:
    """Map GitHub labels / Discourse tags to domains.

    ``backend:PowerPC`` is the only label that selects LLC by itself; generic backend labels are
    kept as ``generic:label:<name>`` reasons and filtered by :func:`powerpc_only` against the text.
    Labels of other targets (``backend:X86`` ...) are ignored.
    """
    found: dict[Domain, list[str]] = {}
    for raw in labels:
        label = raw.strip().lower()
        if _SANITIZER_LABEL.match(label):
            found.setdefault(Domain.SANITIZERS, []).append(f"label:{label}")
        elif label == _POWERPC_LABEL:
            found.setdefault(Domain.LLC, []).append(f"label:{label}")
        elif label in _GENERIC_LLC_LABELS:
            found.setdefault(Domain.LLC, []).append(f"{_GENERIC}label:{label}")
        elif label.startswith("clang:") or label in _CLANG_LABELS or label in _DISCOURSE_CLANG_TAGS:
            found.setdefault(Domain.CLANG, []).append(f"label:{label}")
    return found


def keyword_domains(text: str) -> dict[Domain, list[str]]:
    """Scan text for domain keywords. Weak-only matches need two distinct hits.

    LLC additionally requires a PowerPC mention plus one backend signal (strong or weak).
    """
    found: dict[Domain, list[str]] = {}
    for domain in DOMAIN_PRIORITY:
        strong = _hits(_STRONG.get(domain, ()), text)
        weak = _hits(_WEAK.get(domain, ()), text)
        if domain is Domain.LLC:
            ppc = _hits((_POWERPC,), text)
            signals = strong + weak
            if ppc and signals:
                found[domain] = [f"keywords:{'+'.join((ppc + signals)[:3])}"]
        elif strong:
            found[domain] = [f"keyword:{h}" for h in strong[:3]]
        elif len(weak) >= 2:
            found[domain] = [f"keywords:{'+'.join(weak[:3])}"]
    return found


def powerpc_only(found: dict[Domain, list[str]], text: str) -> None:
    """Drop the LLC domain unless the evidence is PowerPC-specific. Mutates ``found``.

    PowerPC-specific: the PowerPC category, the ``backend:PowerPC`` label, or a keyword match
    (already PowerPC-gated). Generic evidence (generic CodeGen categories, ``tools:llc`` ...)
    survives only if the text mentions PowerPC.
    """
    reasons = found.get(Domain.LLC)
    if not reasons:
        return
    if all(r.startswith(_GENERIC) for r in reasons) and not _POWERPC.search(text):
        del found[Domain.LLC]


def _hits(patterns: tuple[re.Pattern[str], ...], text: str) -> list[str]:
    hits: list[str] = []
    for pattern in patterns:
        m = pattern.search(text)
        if m and m.group(0).lower() not in hits:
            hits.append(m.group(0).lower())
    return hits


class Classifier:
    """Applies the configured category/label/keyword rules to :class:`Item` objects."""

    def __init__(self, config: Config) -> None:
        self._cfg = config

    def needs_body_scan(self, item: Item) -> bool:
        """True if a title-only miss on this Discourse item deserves a look at the body."""
        cfg = self._cfg
        return item.source == "discourse" and (
            item.category_id in cfg.body_scan_categories or item.category_id in cfg.llc_generic_categories
        )

    def classify(self, item: Item, *, use_body: bool = False) -> Classification | None:
        """Return the classification, or ``None`` when the item is irrelevant."""
        if item.source == "discourse":
            return self._discourse(item, use_body)
        return self._github(item)

    def _discourse(self, item: Item, use_body: bool) -> Classification | None:
        cfg = self._cfg
        if item.category_id in cfg.excluded_categories:
            return None
        if any(tag.lower() in cfg.excluded_tags for tag in item.labels):
            return None
        found: dict[Domain, list[str]] = {}
        category = item.category_id
        if category in cfg.sanitizer_categories:
            found.setdefault(Domain.SANITIZERS, []).append(f"category:{category}")
        if category in cfg.llc_categories:
            found.setdefault(Domain.LLC, []).append(f"category:{category}")
        elif category in cfg.llc_generic_categories:
            found.setdefault(Domain.LLC, []).append(f"{_GENERIC}category:{category}")
        if category in cfg.clang_categories:
            found.setdefault(Domain.CLANG, []).append(f"category:{category}")
        _merge(found, label_domains(item.labels))
        text = item.title
        if use_body and item.body:
            text = f"{item.title}\n{item.body}"
        _merge(found, keyword_domains(text))
        powerpc_only(found, text)
        return _build(found)

    def _github(self, item: Item) -> Classification | None:
        found = label_domains(item.labels)
        powerpc_only(found, f"{item.title}\n{item.body}")
        if not found:  # freshly filed / non-matching issues: fall back to title keywords
            found = keyword_domains(item.title)
        return _build(found)


def _merge(into: dict[Domain, list[str]], extra: dict[Domain, list[str]]) -> None:
    for domain, reasons in extra.items():
        into.setdefault(domain, []).extend(reasons)


def _build(found: dict[Domain, list[str]]) -> Classification | None:
    domains = tuple(d for d in DOMAIN_PRIORITY if d in found)
    if not domains:
        return None
    reasons = tuple(r for d in domains for r in found[d][:3])
    return Classification(primary=domains[0], domains=domains, reasons=reasons)
