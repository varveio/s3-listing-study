"""``receipt.md`` and ``run.meta``, byte for byte against the shell's own output.

Golden regeneration from a committed ``run.meta`` is not possible and never was:
``run.meta`` carries neither the invocation, nor the box spec, nor the payload
byte sizes, nor the emulation note, nor the registry shape — all of which the
receipt states. See :func:`test_run_meta_cannot_regenerate_the_receipt`, which
pins that gap rather than leaving it as a claim.

So the acceptance bar is a fixture instead: ``tests/fixtures/receipt/*.json``
are synthetic run facts, and the ``*.receipt.md`` / ``*.run.meta`` beside them
were produced by the pre-port shell renderer over exactly those values. The
``hostile`` case carries every branch the ``plain`` one does not — a timeout
kill, truncation of both streams, an external payload, a missing tool version,
a set prefix, an unavailable measurement, and ``&``, ``<``, ``>``, ``|`` and
backticks in values that must come out as entities.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from s3_listing_study.receipt.errors import ReceiptError
from s3_listing_study.receipt.meta import RunFacts
from s3_listing_study.receipt.meta import fields as meta_fields
from s3_listing_study.receipt.meta import render as render_meta
from s3_listing_study.receipt.redact import Payload
from s3_listing_study.receipt.render import (
    VERDICT_PLACEHOLDER,
    ReceiptBlockError,
    html_escape,
    md_safe_block,
    md_safe_inline,
    render,
)
from s3_listing_study.verify.report import PLACEHOLDER

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "receipt"
CASES = ("plain", "hostile")


def _case(name: str) -> tuple[RunFacts, Payload, Payload]:
    doc = json.loads((FIXTURES / f"{name}.json").read_text())
    return (
        RunFacts(**doc["facts"]),
        Payload(**doc["payloads"]["stdout"]),
        Payload(**doc["payloads"]["stderr"]),
    )


@pytest.mark.parametrize("name", CASES)
def test_receipt_matches_the_shell_renderer(name: str) -> None:
    facts, stdout, stderr = _case(name)
    assert render(facts, stdout, stderr) == (FIXTURES / f"{name}.receipt.md").read_bytes()


@pytest.mark.parametrize("name", CASES)
def test_run_meta_matches_the_shell_emitter(name: str) -> None:
    facts, stdout, stderr = _case(name)
    assert render_meta(facts, stdout, stderr) == (FIXTURES / f"{name}.run.meta").read_bytes()


def test_development_receipt_does_not_claim_production_evidence() -> None:
    facts, stdout, stderr = _case("plain")
    development = RunFacts(
        **{
            **facts.__dict__,
            "security_profile": "local-development-unisolated",
            "security_provider": "local-development",
            "docker_network": "bridge",
            "firewall_policy_sha256": "not-checked-development",
        }
    )
    receipt = render(development, stdout, stderr).decode()
    assert "not evidentiary or correctness-verified" in receipt
    assert "ordinary Docker bridge" in receipt
    assert "Firewall policy | not checked" in receipt
    assert "Development log limit | 1 GiB" in receipt
    assert "registry expectation only; file not checked" in receipt
    assert "verified against the file before this run" not in receipt
    assert "user-defined bridge" not in receipt


def test_verdict_placeholder_is_the_one_the_verifier_splices() -> None:
    """The receipt writes the slot; the verifier writes over it. One string."""
    assert VERDICT_PLACEHOLDER == PLACEHOLDER


def test_run_meta_cannot_regenerate_the_receipt() -> None:
    """The honest statement of what ``run.meta`` does not carry.

    Regenerating ``receipt.md`` from a committed ``run.meta`` was plausible and
    turns out to be impossible. This pins the gap so a future reader does not
    have to rediscover it, and so the day someone widens ``run.meta`` they are
    told which fields closed.
    """
    facts, stdout, stderr = _case("plain")
    carried = {key for key, _ in meta_fields(facts, stdout, stderr)}
    # `emulated` is deliberately NOT in this set: it is derivable from
    # image_arch/host_arch, both of which run.meta does carry.
    missing = {
        "invocation",
        "arch",
        "cores",
        "ram_gb",
        "host_kernel",
        "env_note",
        "timeout",
        "manifest_keys",
        "shape",
    }
    assert not (missing & carried)
    # The payload byte sizes the receipt states are absent too: run.meta records
    # each stream's path and digest, never its length. Asserted on the rendered
    # bytes — a key name that was never a candidate would prove nothing — over
    # the hostile case, whose sizes are distinctive enough for their absence to
    # mean something.
    facts, stdout, stderr = _case("hostile")
    assert f"{stdout.size} bytes".encode() in render(facts, stdout, stderr)
    rendered_meta = render_meta(facts, stdout, stderr)
    for payload in (stdout, stderr):
        assert str(payload.size).encode() not in rendered_meta


def test_html_escape_covers_every_cell_breaking_character() -> None:
    assert html_escape("&<>|`") == "&amp;&lt;&gt;&#124;&#96;"


def test_inline_value_refuses_a_control_byte() -> None:
    """An object key must not be able to forge a later field."""
    with pytest.raises(ReceiptError):
        md_safe_inline("PREFIX", "a\nb")
    with pytest.raises(ReceiptError):
        md_safe_inline("PREFIX", "a\tb")


def test_block_value_keeps_line_feeds_and_refuses_the_rest() -> None:
    assert md_safe_block("invocation", "a\nb") == "a\nb"
    with pytest.raises(ReceiptBlockError):
        md_safe_block("invocation", "a\tb")


def test_a_forged_run_meta_field_is_refused() -> None:
    facts, stdout, stderr = _case("plain")
    forged = RunFacts(**{**facts.__dict__, "tool_version": "1.0\nredaction_changed_bytes=no"})
    with pytest.raises(ReceiptError):
        render_meta(forged, stdout, stderr)


def test_unavailable_memory_is_not_rendered_as_zero() -> None:
    facts, stdout, stderr = _case("hostile")
    receipt = render(facts, stdout, stderr).decode()
    assert "| `peak_rss` | unavailable MB |" in receipt
    assert "0.0 MB | `VmHWM`" not in receipt


@pytest.mark.parametrize(
    "field", ["timeout", "docker_control_timeout_s", "docker_cleanup_timeout_s"]
)
def test_timeout_fields_use_the_single_markdown_escaper(field: str) -> None:
    facts, stdout, stderr = _case("hostile")
    forged = RunFacts(**{**facts.__dict__, field: "1|2"})
    assert b"1&#124;2" in render(forged, stdout, stderr)


@pytest.mark.parametrize(
    "field", ["timeout", "docker_control_timeout_s", "docker_cleanup_timeout_s"]
)
def test_timeout_fields_reject_controls(field: str) -> None:
    facts, stdout, stderr = _case("hostile")
    forged = RunFacts(**{**facts.__dict__, field: "1\n2"})
    with pytest.raises(ReceiptError):
        render(forged, stdout, stderr)
