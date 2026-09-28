"""Jawny graf: nie ma swobodnych rozmów agentów ani agentowych pętli narzędzi."""
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from .models import TurnState, RollDecision, NarrationDecision
from .nodes import Nodes


def await_roll(state: TurnState) -> dict:
    action = state['action']
    # Tutaj nie losujemy i nie zmieniamy DB. Węzeł może rozpocząć się od nowa.
    answer = interrupt({'kind': 'roll', 'action_id': action['action_id'],
                        'label': action['definition']['label'], 'plan': action['plan'],
                        'elapsed_seconds': action['definition']['elapsed_seconds'],
                        'noise_events': action['definition']['noise_events']})
    return {'roll_decision': RollDecision.model_validate(answer).model_dump()}


def await_narrator(state: TurnState) -> dict:
    answer = interrupt({'kind': 'narration_retry', 'error': state['narration_error'],
                        'message': 'Wynik mechaniki, o ile był, jest zapisany. Ponów tylko opis albo go pomiń.'})
    return {'narration_decision': NarrationDecision.model_validate(answer).model_dump()}


def route_intent(state: TurnState) -> str:
    return {'action': 'prepare', 'chat': 'narrate', 'clarify': 'fixed_reply',
            'unsupported': 'fixed_reply'}[state['intent']['kind']]


def route_prepared(state: TurnState) -> str:
    if not state.get('action'):
        return 'finish'
    if state['action']['status'] == 'pending':
        return 'await_roll'
    return route_resolved(state)


def route_resolved(state: TurnState) -> str:
    return 'narrate' if state['action']['status'] == 'resolved' else 'fixed_reply'


def build_graph(nodes: Nodes, checkpointer):
    builder = StateGraph(TurnState)
    for name in ('load_context', 'interpret', 'prepare', 'resolve', 'fixed_reply', 'narrate', 'fallback', 'finish'):
        builder.add_node(name, getattr(nodes, name))
    builder.add_node('await_roll', await_roll)
    builder.add_node('await_narrator', await_narrator)
    builder.add_edge(START, 'load_context')
    builder.add_edge('load_context', 'interpret')
    builder.add_conditional_edges('interpret', route_intent,
                                  ['prepare', 'narrate', 'fixed_reply'])
    builder.add_conditional_edges('prepare', route_prepared,
                                  ['finish', 'await_roll', 'narrate', 'fixed_reply'])
    builder.add_edge('await_roll', 'resolve')
    builder.add_conditional_edges('resolve', route_resolved, ['narrate', 'fixed_reply'])
    builder.add_conditional_edges('narrate', lambda s: 'await_narrator' if s.get('narration_error') else 'finish',
                                  ['await_narrator', 'finish'])
    builder.add_conditional_edges('await_narrator',
                                  lambda s: 'narrate' if s['narration_decision']['choice'] == 'retry' else 'fallback',
                                  ['narrate', 'fallback'])
    for name in ('fixed_reply', 'fallback'):
        builder.add_edge(name, 'finish')
    builder.add_edge('finish', END)
    return builder.compile(checkpointer=checkpointer)
