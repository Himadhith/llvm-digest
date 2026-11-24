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


def test_github_powerpc_label_selects_llc() -> None:
    result = classify(labels=("backend:PowerPC", "miscompilation"))
    assert result is not None and result.primary is Domain.LLC


def test_github_other_backend_labels_are_ignored() -> None:
    for label in ("backend:X86", "backend:AArch64", "backend:RISC-V", "backend:SystemZ", "backend:AMDGPU"):
        assert classify(labels=(label, "crash")) is None, label


def test_generic_llc_label_needs_powerpc_mention() -> None:
    assert classify(labels=("tools:llc",), title="llc crashes on large input") is None
    assert classify(labels=("llvm:codegen",), title="Wrong code", body="Seen on x86 and aarch64") is None
    result = classify(labels=("llvm:codegen",), title="Wrong code", body="Seen on ppc64le with -O2")
    assert result is not None and result.primary is Domain.LLC
    result = classify(labels=("tablegen",), title="TableGen: PowerPC patterns are not emitted")
    assert result is not None and result.primary is Domain.LLC


def test_x86_label_plus_powerpc_label_still_matches_via_powerpc() -> None:
    result = classify(labels=("backend:X86", "backend:PowerPC"))
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
    result = classify(source="discourse", category_id=56, title="Anything")  # PowerPC
    assert result is not None and result.primary is Domain.LLC
    result = classify(source="discourse", category_id=6, title="Anything")
    assert result is not None and result.primary is Domain.CLANG


def test_discourse_other_target_categories_are_ignored() -> None:
    for category in (53, 54, 55, 57, 58, 59, 38):  # AArch64, ARM, Mips, RISCV, WebAssembly, X86, AMDGPU
        assert classify(source="discourse", category_id=category, title="Anything") is None, category


def test_discourse_generic_codegen_category_needs_powerpc_mention() -> None:
    assert classify(source="discourse", category_id=51, title="Rework instruction selection") is None
    result = classify(source="discourse", category_id=51, title="PowerPC: rework instruction selection")
    assert result is not None and result.primary is Domain.LLC
    item = make_item(source="discourse", category_id=52, title="Question", body="Affects ppc64le only")
    clf = Classifier(Config())
    assert clf.classify(item) is None and clf.needs_body_scan(item)
    scanned = clf.classify(item, use_body=True)
    assert scanned is not None and scanned.primary is Domain.LLC


def test_discourse_excluded_category_and_tag() -> None:
    assert classify(source="discourse", category_id=75, title="ASan crash report") is None
    assert classify(source="discourse", category_id=5, title="ASan weekly", labels=("llvm-weekly",)) is None


def test_discourse_generic_category_needs_keyword() -> None:
    assert classify(source="discourse", category_id=5, title="Values of LLVM") is None
    result = classify(source="discourse", category_id=5, title="[RFC] DoubleFreeSanitizer (DSan)")
    assert result is not None and result.primary is Domain.SANITIZERS


def test_discourse_body_scan_only_when_enabled() -> None:
    item = make_item(source="discourse", category_id=5, title="Question about lowering", body="we hit an ISel failure in SelectionDAG on PowerPC")
    clf = Classifier(Config())
    assert clf.classify(item) is None
    assert clf.needs_body_scan(item)
    scanned = clf.classify(item, use_body=True)
    assert scanned is not None and scanned.primary is Domain.LLC


def test_weak_keywords_need_two_distinct_hits() -> None:
    assert classify(title="Our parser is slow") is None
    result = classify(title="Clang parser and lexer rework")
    assert result is not None and result.primary is Domain.CLANG


def test_llc_keywords_require_powerpc() -> None:
    assert classify(title="Backend crash lowering on x86") is None
    assert classify(title="TableGen: emit better matchers") is None
    assert classify(title="ISel failure on AArch64") is None
    result = classify(title="Backend crash lowering on PowerPC")
    assert result is not None and result.primary is Domain.LLC
    result = classify(title="TableGen: emit better matchers for ppc64le")
    assert result is not None and result.primary is Domain.LLC


def test_powerpc_alone_is_not_enough() -> None:
    assert classify(title="PowerPC build bot is slow") is None
    assert classify(title="Power10 availability on the cloud") is None


def test_powerpc_specific_terms_count_as_backend_signals() -> None:
    result = classify(title="AltiVec miscompile on POWER9")
    assert result is not None and result.primary is Domain.LLC


def test_sanitizers_and_clang_unaffected_by_powerpc_gate() -> None:
    result = classify(title="ASan false positive on x86")
    assert result is not None and result.primary is Domain.SANITIZERS
    result = classify(title="Sema crash on aarch64 target")
    assert result is not None and result.primary is Domain.CLANG


def test_sanitizer_and_powerpc_gets_both_domains() -> None:
    result = classify(title="ASan crash in PowerPC ISel")
    assert result is not None and result.primary is Domain.SANITIZERS
    assert result.secondary == (Domain.LLC,)


def test_strong_keywords() -> None:
    for title, domain in [
        ("TSan data race in libc++", Domain.SANITIZERS),
        ("TableGen: emit better matchers for PowerPC", Domain.LLC),
        ("Sema crash on invalid template", Domain.CLANG),
        ("-fsanitize=undefined breaks build", Domain.SANITIZERS),
    ]:
        result = classify(title=title)
        assert result is not None and result.primary is domain, title


def test_clang_tidy_is_not_clang_frontend_by_keyword() -> None:
    assert classify(title="clang-tidy check for foo") is None
