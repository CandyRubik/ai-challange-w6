from app.agents.agent import Agent
from app.invariants import InvariantSettings
from app.memory.context import MemoryContext
from app.orchestration.context import OrchestrationContext


class RecordingModel:
    def __init__(self):
        self.calls = []
    def generate(self, *, messages, **kwargs):
        self.calls.append(messages)
        return "Ответ"


def test_orchestration_and_memory_do_not_leak_into_next_call():
    model = RecordingModel()
    agent = Agent(model)
    agent.respond([], "Первый вопрос", orchestration=OrchestrationContext(invariants=InvariantSettings()),
                  memory=MemoryContext(working=({"category": "goal", "content": "Текущая цель"},)))
    agent.respond([], "Второй вопрос")
    assert "PRODUCT_INVARIANTS" in model.calls[0][0]["content"]
    assert "Текущая цель" in model.calls[0][0]["content"]
    assert model.calls[1] == [{"role": "system", "content": Agent.default_system_prompt},
                              {"role": "user", "content": "Второй вопрос"}]


def test_disabled_context_hides_memory_and_history_but_keeps_product_rules():
    model = RecordingModel()
    Agent(model, context_enabled=False).respond(
        [{"role": "user", "content": "Старая переписка"}], "Новый вопрос",
        orchestration=OrchestrationContext(invariants=InvariantSettings()),
        memory=MemoryContext(working=({"category": "goal", "content": "Скрытая цель"},)),
    )
    assert len(model.calls[0]) == 2
    assert "PRODUCT_INVARIANTS" in model.calls[0][0]["content"]
    assert "Скрытая цель" not in str(model.calls[0]) and "Старая переписка" not in str(model.calls[0])


def test_memory_window_keeps_product_rules():
    model = RecordingModel()
    Agent(model).respond([], "Вопрос", orchestration=OrchestrationContext(invariants=InvariantSettings()),
                         memory=MemoryContext(working=tuple(
                             {"category": "constraint", "content": f"Условие-{i:02d}"} for i in range(35))))
    prompt = model.calls[0][0]["content"]
    assert "PRODUCT_INVARIANTS" in prompt and "USER_PROFILE" not in prompt
    assert "Условие-00" not in prompt and "Условие-05" in prompt and "Условие-34" in prompt
