from __future__ import annotations

from llvm_digest.classify import Classifier
from llvm_digest.config import Config
from llvm_digest.models import Domain

from .conftest import make_item


def classify(**kwargs: object):  # type: ignore[no-untyped-def]
    use_body = bool(kwargs.pop("use_body", False))
    return Classifier(Config()).classify(make_item(**kwargs), use_body=use_body)  # type: ignore[arg-type]


def test_github_sanitizer_label() -> None:
    result = classify(labels=("compiler-rt:asan", "crash"))
    assert result is not None and result.primary is Domain.SANITIZERS


def test_github_backend_label() -> None:
    result = classify(labels=("backend:X86", "miscompilation"))
    assert result is not None and result.primary is Domain.LLC


def test_github_clang_labels() -> None:
    for label in ("clang", "clang:frontend", "clang:diagnostics", "extension:clang"):
        result = classify(labels=(label,))
        assert result is not None and result.primary is Domain.CLANG, label


def test_multi_label_picks_sanitizers_first_and_keeps_secondary() -> None:
    result = classify(labels=("clang:modules", "compiler-rt:asan", "crash"))
    assert result is not None
    assert result.primary is Domain.SANITIZERS
    assert result.secondary == (Domain.CLANG,)


def test_unrelated_labels_are_irrelevant() -> None:
    assert classify(labels=("llvm:instcombine", "llvm:analysis")) is None
    assert classify(labels=("clang-tidy", "clang-format")) is None


def test_unlabelled_github_issue_falls_back_to_title_keywords() -> None:
    result = classify(title="MSan false positive in vector reduction")
    assert result is not None and result.primary is Domain.SANITIZERS


def test_unlabelled_github_issue_without_keywords_is_dropped() -> None:
    assert classify(title="Typo in documentation") is None


def test_discourse_category_match() -> None:
    result = classify(source="discourse", category_id=12, title="Anything")
    assert result is not None and result.primary is Domain.SANITIZERS
    result = classify(source="discourse", category_id=59, title="Anything")
    assert result is not None and result.primary is Domain.LLC
    result = classify(source="discourse", category_id=6, title="Anything")
    assert result is not None and result.primary is Domain.CLANG


def test_discourse_excluded_category_and_tag() -> None:
    assert classify(source="discourse", category_id=75, title="ASan crash report") is None
    assert classify(source="discourse", category_id=5, title="ASan weekly", labels=("llvm-weekly",)) is None


def test_discourse_generic_category_needs_keyword() -> None:
    assert classify(source="discourse", category_id=5, title="Values of LLVM") is None
    result = classify(source="discourse", category_id=5, title="[RFC] DoubleFreeSanitizer (DSan)")
    assert result is not None and result.primary is Domain.SANITIZERS


def test_discourse_body_scan_only_when_enabled() -> None:
    item = make_item(source="discourse", category_id=5, title="Question about lowering", body="we hit an ISel failure in SelectionDAG")
    clf = Classifier(Config())
    assert clf.classify(item) is None
    assert clf.needs_body_scan(item)
    scanned = clf.classify(item, use_body=True)
    assert scanned is not None and scanned.primary is Domain.LLC


def test_weak_keywords_need_two_distinct_hits() -> None:
    assert classify(title="Our x86 build bot is slow") is None
    result = classify(title="Backend crash lowering on x86")
    assert result is not None and result.primary is Domain.LLC


def test_strong_keywords() -> None:
    for title, domain in [
        ("TSan data race in libc++", Domain.SANITIZERS),
        ("TableGen: emit better matchers", Domain.LLC),
        ("Sema crash on invalid template", Domain.CLANG),
        ("-fsanitize=undefined breaks build", Domain.SANITIZERS),
    ]:
        result = classify(title=title)
        assert result is not None and result.primary is domain, title


def test_clang_tidy_is_not_clang_frontend_by_keyword() -> None:
    assert classify(title="clang-tidy check for foo") is None
