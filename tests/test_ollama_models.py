"""Which models the demo may offer.

An agent that cannot call tools cannot demonstrate anything about a policy
layer that rules on tool calls. Ollama reports the capability, so the picker
asks rather than guessing from a hardcoded list that would go stale the first
time a model is pulled.
"""

from enrolment.agent.ollama import installed_models


def fake_transport(tags, capabilities):
    def get(url, timeout):
        return {"models": [{"name": n} for n in tags]}

    def post(url, body, timeout):
        return {"capabilities": capabilities[body["model"]]}

    return get, post


def test_it_offers_only_models_that_can_call_tools():
    get, post = fake_transport(
        ["qwen2.5:14b", "nomic-embed-text", "deepseek-coder"],
        {
            "qwen2.5:14b": ["completion", "tools"],
            "nomic-embed-text": ["embedding"],
            "deepseek-coder": ["completion"],
        },
    )

    assert installed_models(get=get, post=post) == ["qwen2.5:14b"]


def test_a_model_whose_capabilities_cannot_be_read_is_left_out():
    """Offering a model the picker could not verify means a demo that fails in
    front of an audience for a reason nobody in the room can see."""
    def get(url, timeout):
        return {"models": [{"name": "broken"}]}

    def post(url, body, timeout):
        raise ConnectionError("no such model")

    assert installed_models(get=get, post=post) == []
