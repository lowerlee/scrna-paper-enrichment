import json
import os
import glob
import anthropic

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def _load_prompt():
    """Load the highest-versioned prompt file and return (prompt_text, version_string)."""
    pattern = os.path.join(os.path.dirname(__file__), "..", "prompts", "classifier_v*.txt")
    files = sorted(glob.glob(pattern))
    if not files:
        raise FileNotFoundError("No classifier prompt found in prompts/")
    path = files[-1]
    version = os.path.basename(path).replace("classifier_", "").replace(".txt", "")
    with open(path) as f:
        return f.read(), version


_PROMPT_CACHE: tuple[str, str] | None = None


def _prompt():
    global _PROMPT_CACHE
    if _PROMPT_CACHE is None:
        _PROMPT_CACHE = _load_prompt()
    return _PROMPT_CACHE


VERDICT_VALUES = {"RELEVANT", "NOT_RELEVANT"}
CONFIDENCE_VALUES = {"HIGH", "MEDIUM", "LOW"}


def _parse_response(text: str) -> dict:
    data = json.loads(text)
    if data.get("verdict") not in VERDICT_VALUES:
        raise ValueError(f"Invalid verdict: {data.get('verdict')}")
    if data.get("confidence") not in CONFIDENCE_VALUES:
        raise ValueError(f"Invalid confidence: {data.get('confidence')}")
    if not isinstance(data.get("reason"), str):
        raise ValueError("Missing reason string")
    return data


def classify(title: str, abstract: str) -> dict:
    """
    Classify a paper given its title and abstract.

    Returns dict with keys: verdict, confidence, reason, prompt_version.
    Raises on unrecoverable API or parse failure.
    """
    prompt_text, version = _prompt()
    user_content = f"Title: {title}\n\nAbstract: {abstract}"

    def _call() -> str:
        response = _get_client().messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=256,
            system=[
                {
                    "type": "text",
                    "text": prompt_text,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_content}],
        )
        return response.content[0].text.strip()

    raw = _call()
    try:
        result = _parse_response(raw)
    except (json.JSONDecodeError, ValueError):
        raw = _call()
        result = _parse_response(raw)

    result["prompt_version"] = version
    return result
