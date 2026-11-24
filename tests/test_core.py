from __future__ import annotations

from llvm_digest.models import Candidate, Classification, Domain, Item
from llvm_digest.format import render_markdown, render_slack
from llvm_digest.state import StateStore
from llvm_digest.summarize import ExtractiveSummarizer, impact_hints
from llvm_digest.text import html_to_text, markdown_to_text, prose, slack_escape, truncate

from .conftest import make_item


def candidate(item: Item, domain: Domain = Domain.SANITIZERS) -> Candidate:
    return Candidate(item=item, classification=Classification(primary=domain, domains=(domain,), reasons=("label:x",)))


def test_state_dedupe_and_posted_is_sticky() -> None:
    with StateStore(":memory:") as state:
        assert not state.is_known("github:1")
        state.mark("github:1", "posted", "t", "u")
        state.mark("github:1", "skipped")
        assert state.status("github:1") == "posted"
        state.mark("discourse:2", "skipped")
        assert state.counts() == {"posted": 1, "skipped": 1}
        assert state.mark_posted(["a", "b"]) == 2
        assert state.is_known("a")


def test_state_persists_to_disk(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = str(tmp_path / "sub" / "s.db")
    with StateStore(path) as s1:
        s1.mark("github:9", "posted")
    with StateStore(path) as s2:
        assert s2.is_known("github:9")


def test_state_prune_only_removes_old_skipped() -> None:
    with StateStore(":memory:") as state:
        state.mark("a", "skipped")
        state.mark("b", "posted")
        assert state.prune(older_than_days=0) in (0, 1)
        assert state.status("b") == "posted"


def test_html_to_text_drops_quotes_and_scripts() -> None:
    html = "<p>Real &amp; true</p><aside>quoted</aside><script>alert(1)</script><ul><li>one</li><li>two</li></ul>"
    text = html_to_text(html)
    assert "Real & true" in text and "quoted" not in text and "alert" not in text
    assert "one" in text and "two" in text


def test_markdown_to_text_strips_noise() -> None:
    md = "# Title\n<!-- template -->\n![img](http://x/y.png)\nSee [the docs](http://a.b) and http://raw.url\n```\nint x;\n```\n`inline`"
    text = markdown_to_text(md)
    assert "template" not in text and "img" not in text and "http" not in text and "int x" not in text
    assert "the docs" in text and "inline" in text


def test_prose_drops_headings_and_leadins() -> None:
    assert prose("Description\nThe following code:\nThe compiler crashes on valid input.") == "The compiler crashes on valid input."


def test_truncate_on_word_boundary() -> None:
    assert truncate("alpha beta gamma", 12) == "alpha beta\u2026"
    assert truncate("short", 12) == "short"


def test_slack_escape_neutralises_mentions_and_markup() -> None:
    out = slack_escape("<!channel> hi @here & <b>x</b>")
    assert "<" not in out and "&lt;" in out and "&amp;" in out
    assert "@here" not in out and "@channel" not in out


def test_impact_hints_from_labels_and_text() -> None:
    item = make_item(labels=("backend:AArch64", "regression:22"), title="Wrong code", body="Seen with clang 21.1 on Linux, reproduces on trunk")
    hint = impact_hints(item)
    assert "Regression since LLVM 22" in hint and "aarch64" in hint and "21.1" in hint and "trunk" in hint
    assert impact_hints(make_item()) == "No specific platform or version stated."


def test_extractive_summary_shape() -> None:
    item = make_item(title="ASan false positive", body="ASan reports a bug where there is none. It only happens at -O2. Another detail here.", labels=("compiler-rt:asan",))
    summary = ExtractiveSummarizer().summarize(candidate(item))
    assert summary.problem.startswith("ASan reports a bug")
    assert 2 <= len(summary.bullets) <= 3
    assert summary.impact


def test_extractive_summary_handles_empty_body() -> None:
    summary = ExtractiveSummarizer().summarize(candidate(make_item(title="Only a title")))
    assert summary.problem == "Only a title"
    assert len(summary.bullets) >= 2


def test_slack_payload_is_escaped_and_within_limits() -> None:
    item = make_item(title="<!channel> | evil", body="x " * 5000, author="@a<b>")
    summary = ExtractiveSummarizer().summarize(candidate(item))
    payload = render_slack(summary)
    flat = str(payload)
    assert "<!channel>" not in flat and "@channel" not in flat
    for block in payload["blocks"]:
        if block["type"] == "section":
            assert len(block["text"]["text"]) <= 3000
    assert payload["blocks"][0]["text"]["text"].startswith("*[Sanitizers]*")
    assert payload["unfurl_links"] is False


def test_markdown_render_contains_required_fields() -> None:
    item = make_item(title="Crash", body="It crashes badly.", labels=("backend:X86",))
    md = render_markdown(ExtractiveSummarizer().summarize(candidate(item, Domain.LLC)))
    assert "[LLC Backend]" in md and "Key problem" in md and "Impact/context" in md
    assert "https://github.com/llvm/llvm-project/issues/1" in md and "2026-11-24" in md and "@someone" in md
