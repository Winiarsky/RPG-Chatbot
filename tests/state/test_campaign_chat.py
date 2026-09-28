from copy import deepcopy
from types import SimpleNamespace

from campaign_chat import ContextualModel


class FakeModel:
    def __init__(self):
        self.calls = []
    def invoke(self, messages):
        self.calls.append(deepcopy(messages))
        return SimpleNamespace(text="Widzę wieżę i drzwi.")


def test_context_does_not_mutate_input_messages():
    model = FakeModel()
    adapter = ContextualModel(model, "new prompt")
    messages = [{"role": "system", "content": "old prompt"}, {"role": "user", "content": "hej"}]
    original = deepcopy(messages)
    adapter.invoke(messages)
    assert messages == original
    assert model.calls[0][0]["content"] == "new prompt"
