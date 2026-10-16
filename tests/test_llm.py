import sys
import types

import pytest

from job_agent import llm
from job_agent import main as main_mod
from job_agent.drafter import draft_application
from job_agent.models import Job


class Resp:
    def __init__(self, status=200, data=None, headers=None, text=""):
        self.status_code, self._data, self.headers, self.text = status, data, headers or {}, text

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def ok(text="hello"):
    return Resp(200, {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]})


def patch_post(monkeypatch, *responses):
    sent, queue = [], list(responses)

    def fake_post(url, json, headers, timeout):
        sent.append({"url": url, "json": json, "headers": headers})
        return queue.pop(0) if len(queue) > 1 else queue[0]
    monkeypatch.setattr(llm.requests, "post", fake_post)
    return sent


# --- which provider -----------------------------------------------------

def test_provider_selection(monkeypatch):
    assert llm.provider() == "gemini"
    monkeypatch.delenv("GEMINI_API_KEY")
    assert llm.provider() is None and "GEMINI_API_KEY" in llm.config_problem()
    monkeypatch.setenv("GOOGLE_API_KEY", "g")
    assert llm.provider() == "gemini" and llm.config_problem() is None
    monkeypatch.delenv("GOOGLE_API_KEY")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    assert llm.provider() == "anthropic"
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    assert llm.provider() == "gemini"  # Gemini wins when both are set
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm.provider() == "anthropic"


def test_explicit_provider_without_its_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert "ANTHROPIC_API_KEY" in llm.config_problem()
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    assert "must be" in llm.config_problem()


def test_models(monkeypatch):
    assert llm.get_model() == llm.DEFAULT_GEMINI_MODEL == "gemini-3.5-flash-lite"
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.8-flash")
    assert llm.get_model() == "gemini-3.8-flash"
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert llm.get_model() == "claude-sonnet-5-5"
    monkeypatch.setenv("MODEL", "claude-haiku-5-5")  # old .env setting still works for Claude
    assert llm.get_model() == "claude-haiku-5-5"


# --- Gemini requests ----------------------------------------------------

def test_gemini_request_shape(monkeypatch):
    sent = patch_post(monkeypatch, ok('{"score": 5}'))
    assert llm.generate("Rate this", max_tokens=400, json_mode=True) == '{"score": 5}'
    [req] = sent
    assert req["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.5-flash-lite:generateContent"
    assert "test-key-123" not in req["url"]                    # key stays out of the URL
    assert req["headers"]["x-goog-api-key"] == "test-key-123"
    assert req["json"]["contents"][0]["parts"][0]["text"] == "Rate this"
    cfg = req["json"]["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["maxOutputTokens"] >= 2048
    assert "temperature" not in cfg


def test_no_json_mode_by_default(monkeypatch):
    sent = patch_post(monkeypatch, ok())
    llm.generate("hi")
    assert "responseMimeType" not in sent[0]["json"]["generationConfig"]


def test_thinking_parts_are_ignored(monkeypatch):
    patch_post(monkeypatch, Resp(200, {"candidates": [{"content": {"parts": [
        {"text": "internal reasoning", "thought": True}, {"text": "Final "}, {"text": "answer"}]}}]}))
    assert llm.generate("hi") == "Final answer"


def test_retries_rate_limit_then_succeeds(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    sent = patch_post(monkeypatch, Resp(429, {"error": {"message": "slow down"}}, {"Retry-After": "7"}),
                      Resp(503, {}), ok("done"))
    assert llm.generate("hi") == "done"
    assert len(sent) == 3 and sleeps == [7.0, 10.0]  # Retry-After honoured, then backoff


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "2")
    sent = patch_post(monkeypatch, Resp(429, {"error": {"message": "Quota exceeded for free tier"}}))
    with pytest.raises(llm.LLMError, match="429.*Quota exceeded"):
        llm.generate("hi")
    assert len(sent) == 3


def test_bad_key_fails_fast_without_leaking_key(monkeypatch):
    sent = patch_post(monkeypatch, Resp(400, {"error": {"message": "API key not valid. Please pass a valid API key."}}))
    with pytest.raises(llm.LLMError) as e:
        llm.generate("hi")
    assert len(sent) == 1 and "API key not valid" in str(e.value) and "test-key-123" not in str(e.value)


def test_network_error_message_has_no_key(monkeypatch):
    monkeypatch.setenv("GEMINI_MAX_RETRIES", "0")

    def boom(url, json, headers, timeout):
        raise llm.requests.ConnectionError(f"failed for {url} {headers}")
    monkeypatch.setattr(llm.requests, "post", boom)
    with pytest.raises(llm.LLMError) as e:
        llm.generate("hi")
    assert "ConnectionError" in str(e.value) and "test-key-123" not in str(e.value)


def test_blocked_and_empty_replies(monkeypatch):
    patch_post(monkeypatch, Resp(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(llm.LLMError, match="blocked.*SAFETY"):
        llm.generate("hi")
    patch_post(monkeypatch, Resp(200, {"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}))
    with pytest.raises(llm.LLMError, match="MAX_TOKENS"):
        llm.generate("hi")
    patch_post(monkeypatch, Resp(200, {}))
    with pytest.raises(llm.LLMError, match="no answer"):
        llm.generate("hi")


def test_calls_are_spaced_out_for_the_free_tier(monkeypatch):
    sleeps = []
    monkeypatch.setattr(llm, "_sleep", sleeps.append)
    monkeypatch.setenv("GEMINI_MIN_INTERVAL", "4")
    monkeypatch.setattr(llm, "_last_call", 100.0)
    monkeypatch.setattr(llm.time, "monotonic", lambda: 101.0)
    llm._throttle()
    assert sleeps == [3.0]


def test_generate_without_a_key_explains_how_to_fix(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY")
    with pytest.raises(llm.LLMError, match="aistudio.google.com/apikey"):
        llm.generate("hi")


def test_claude_provider_still_works(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    seen = {}

    class FakeAnthropic:
        class messages:
            @staticmethod
            def create(**kw):
                seen.update(kw)
                return types.SimpleNamespace(content=[types.SimpleNamespace(text="from claude")])
    monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
    assert llm.generate("hi", max_tokens=123) == "from claude"
    assert seen["model"] == "claude-sonnet-5-5" and seen["max_tokens"] == 123


def test_claude_provider_without_package(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a")
    monkeypatch.setitem(sys.modules, "anthropic", None)  # makes `import anthropic` fail
    with pytest.raises(llm.LLMError, match="pip install anthropic"):
        llm.generate("hi")


def test_drafter_uses_llm(monkeypatch):
    job = Job(id="x:1", title="Helpdesk", company="Acme", location="Sydney", url="",
              description="<p>Answer tickets</p>", tags=[])
    prompts = []
    out = draft_application(job, "My resume", generate=lambda p, max_tokens: prompts.append(p) or "  Hi Acme  ")
    assert out == "Hi Acme" and "Helpdesk" in prompts[0] and "Answer tickets" in prompts[0] and "<p>" not in prompts[0]


# --- main: failure handling ----------------------------------------------

def make_job(i):
    return Job(id=f"adzuna:{i}", title=f"Job{i}", company="Acme", location="Sydney", url="",
               description="python", tags=[], source="adzuna")


def run_main(tmp_path, monkeypatch, *extra):
    monkeypatch.setitem(main_mod.sources_mod.SOURCES, "adzuna", lambda **kw: [make_job(i) for i in range(6)])
    resume = tmp_path / "r.txt"
    resume.write_text("resume")
    main_mod.main(["--sources", "adzuna", "--db", str(tmp_path / "j.db"), "--resume", str(resume), *extra])


def test_no_ai_key_still_lists_jobs(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("GEMINI_API_KEY")
    run_main(tmp_path, monkeypatch)
    cap = capsys.readouterr()
    assert "Job0" in cap.out and "[" not in cap.out.split("\n")[0]   # no score shown
    assert "No AI key set" in cap.err and "without AI scoring" in cap.err


def test_scoring_stops_after_repeated_failures(tmp_path, monkeypatch, capsys):
    calls = []

    def always_fail(job, resume):
        calls.append(job.id)
        raise llm.LLMError("Gemini error 429: Quota exceeded")
    monkeypatch.setattr(main_mod, "score_job", always_fail)
    run_main(tmp_path, monkeypatch)
    cap = capsys.readouterr()
    assert len(calls) == 3                           # stopped, not 6
    assert "Stopping AI scoring after 3 failures" in cap.err and "Quota exceeded" in cap.err
    assert "Job5" in cap.out                         # jobs still listed, just unscored


def test_one_scoring_failure_does_not_stop_the_rest(tmp_path, monkeypatch, capsys):
    def flaky(job, resume):
        if job.id == "adzuna:1":
            raise llm.LLMError("blip")
        job.score = 50
    monkeypatch.setattr(main_mod, "score_job", flaky)
    run_main(tmp_path, monkeypatch)
    cap = capsys.readouterr()
    assert "Stopping" not in cap.err and cap.out.count("[50]") == 5
