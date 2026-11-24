"""Drafting words through `claude -p` on the subscription (app/vocab_pipeline/claude.py).

Nothing here runs `claude`: FakeRunner stands in for it, and subprocess.run is replaced
where run_claude itself is under test.
"""

import io
import json
from types import SimpleNamespace

import pytest

from app.vocab_pipeline import claude
from app.vocab_pipeline.claude import (
    RunError,
    claude_command,
    generate,
    parse_result,
    run_claude,
)
from app.vocab_pipeline.prompts import (
    DRAFT_SCHEMA,
    DRAFT_SYSTEM,
    PROPOSE_SCHEMA,
    PROPOSE_SYSTEM,
)
from tests.vocab_helpers import (
    FakeRunner,
    entry,
    make_store,
    model_output,
    pending_draft,
)

# Shaped like real `claude -p --output-format json` results (Claude Code 2.1.331). An
# error still says subtype "success"; is_error is what marks it.
SUCCESS = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": '{"word":"candid"}',
        "structured_output": {"word": "candid"},
    }
)
ERROR = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": True,
        "api_error_status": 404,
        "result": "There's an issue with the selected model (claude-no-such-model).",
        "structured_output": None,
    }
)
NO_OUTPUT = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "I can't help with that.",
        "structured_output": None,
    }
)


def word(w: str, difficulty: str, pos: str = "adjective") -> dict[str, str]:
    return {"word": w, "pos": pos, "difficulty": difficulty}


def three_words(tmp_path, **outputs):
    """Shipped alpha and beta, wanted delta, and a proposal of candid, alpha (known) and
    wane. allocate gives Intermediate 1, Advanced 1 for the two words still needed."""
    store = make_store(tmp_path, [entry(0, "alpha"), entry(1, "beta")])
    store.save_wanted({"delta": {"word": "delta", "referrers": ["beta"]}})
    fake = FakeRunner(
        proposal=[
            word("candid", "Advanced"),
            word("alpha", "Advanced"),
            word("wane", "Intermediate", "verb"),
        ],
        outputs={
            "delta": model_output("delta"),
            "candid": model_output("candid"),
            "wane": model_output("wane", room_options=3),
            **outputs,
        },
    )
    return store, fake


def test_generate_proposes_dedupes_and_drafts_each_word(tmp_path):
    store, fake = three_words(tmp_path)
    assert generate(fake, store, 3, io.StringIO(), confirm=lambda _: True) == 3
    system, message, schema = fake.calls[0]
    assert (system, schema) == (PROPOSE_SYSTEM, PROPOSE_SCHEMA)
    assert "alpha, beta, delta" in message
    drafting = fake.calls[1:]
    assert [(s, sch) for s, _, sch in drafting] == [(DRAFT_SYSTEM, DRAFT_SCHEMA)] * 3
    assert ['"delta"' in m for _, m, _ in drafting] == [True, False, False]
    assert store.draft("candid")["status"] == "pending"
    delta = store.draft("delta")
    assert delta["status"] == "pending" and delta["pos"] == "adjective"
    wane = store.draft("wane")
    assert wane["status"] == "invalid" and wane["entry"] is not None
    assert any("options: expected 4" in e for e in wane["errors"])
    assert {d["slug"]: d["attempts"] for d in store.drafts()} == {
        "candid": 1,
        "delta": 1,
        "wane": 1,
    }


def test_a_cli_failure_stops_the_run_and_the_next_run_carries_on(tmp_path):
    store, fake = three_words(tmp_path, candid="stop")
    out = io.StringIO()
    assert generate(fake, store, 3, out, confirm=lambda _: True) == 1
    assert "stopped: usage limit reached" in out.getvalue()
    assert "run generate --count 2 to carry on" in out.getvalue()
    assert store.draft("delta")["status"] == "pending"
    assert {
        s: (store.draft(s)["status"], store.draft(s)["attempts"])
        for s in ("candid", "wane")
    } == {
        "candid": ("queued", 0),
        "wane": ("queued", 0),
    }
    fake.outputs["candid"] = model_output("candid")
    assert generate(fake, store, 2, io.StringIO(), confirm=lambda _: True) == 2
    assert sum(schema is PROPOSE_SCHEMA for _, _, schema in fake.calls) == 1
    assert store.draft("candid")["status"] == "pending"


def test_no_structured_output_marks_the_word_invalid_and_carries_on(tmp_path):
    store, fake = three_words(tmp_path, delta="empty")
    assert generate(fake, store, 3, io.StringIO(), confirm=lambda _: True) == 3
    delta = store.draft("delta")
    assert delta["status"] == "invalid" and delta["entry"] is None
    assert delta["attempts"] == 1 and "no structured output" in delta["errors"][0]


def test_declined_confirmation_calls_nothing(tmp_path):
    store, fake = three_words(tmp_path)
    assert generate(fake, store, 3, io.StringIO(), confirm=lambda _: False) == 0
    assert fake.calls == [] and store.drafts() == []


def test_retries_stop_after_max_attempts(tmp_path):
    store = make_store(tmp_path, [entry(0, "alpha")])
    store.save_draft(
        {**pending_draft("candid"), "status": "invalid", "entry": None, "attempts": 3}
    )
    fake = FakeRunner(proposal=[], outputs={})
    assert generate(fake, store, 1, io.StringIO(), confirm=lambda _: True) == 0
    assert store.draft("candid")["attempts"] == 3
    assert [schema for _, _, schema in fake.calls] == [PROPOSE_SCHEMA]


def test_claude_command_runs_a_plain_print_session():
    cmd = claude_command("SYSTEM", {"type": "object"})
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-5-5"
    assert cmd[cmd.index("--effort") + 1] == "high"
    assert cmd[cmd.index("--tools") + 1] == ""
    assert cmd[cmd.index("--output-format") + 1] == "json"
    assert cmd[cmd.index("--system-prompt") + 1] == "SYSTEM"
    assert json.loads(cmd[cmd.index("--json-schema") + 1]) == {"type": "object"}
    assert "--safe-mode" in cmd and "--no-session-persistence" in cmd


def test_run_claude_uses_the_subscription_login(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token-test")
    monkeypatch.setenv("CLAUDECODE", "1")
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return SimpleNamespace(returncode=0, stdout=SUCCESS, stderr="")

    monkeypatch.setattr(claude.subprocess, "run", fake_run)
    assert run_claude("SYSTEM", "message", {"type": "object"}) == {"word": "candid"}
    assert seen["cmd"] == claude_command("SYSTEM", {"type": "object"})
    assert seen["input"] == "message"
    for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CLAUDECODE"):
        assert name not in seen["env"]


def test_parse_result_and_cli_failures(monkeypatch):
    assert parse_result(SUCCESS) == {"word": "candid"}
    with pytest.raises(RunError, match="issue with the selected model"):
        parse_result(ERROR)
    with pytest.raises(ValueError, match="no structured output: I can't help"):
        parse_result(NO_OUTPUT)

    def failed(stdout, stderr):
        return lambda cmd, **kw: SimpleNamespace(
            returncode=1, stdout=stdout, stderr=stderr
        )

    # The CLI's JSON result carries the readable message; stderr is the fallback.
    monkeypatch.setattr(claude.subprocess, "run", failed(ERROR, "[claude-code:x] {}\n"))
    with pytest.raises(RunError, match="issue with the selected model"):
        run_claude("SYSTEM", "message", {"type": "object"})
    monkeypatch.setattr(claude.subprocess, "run", failed("", "Usage limit reached\n"))
    with pytest.raises(RunError, match="Usage limit reached"):
        run_claude("SYSTEM", "message", {"type": "object"})
