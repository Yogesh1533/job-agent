import pytest

from job_agent import llm


@pytest.fixture(autouse=True)
def isolated_llm_env(monkeypatch):
    """Tests never touch real keys, a real .env, the network, or sleep."""
    for var in ("LLM_PROVIDER", "GOOGLE_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_MODEL",
                "ANTHROPIC_MODEL", "MODEL", "GEMINI_MAX_RETRIES"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "test-key-123")
    monkeypatch.setenv("GEMINI_MIN_INTERVAL", "0")
    monkeypatch.setattr(llm, "_sleep", lambda seconds: None)
    monkeypatch.setattr("job_agent.main.load_dotenv", lambda *a, **k: None)
