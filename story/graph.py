"""Nowy graf ma odrębny thread prefix; nie wznawia checkpointów wersji 4."""
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from agents.graph import await_roll, await_narrator, route_intent, route_resolved
from .models import StoryTurnState, StoryDecision
from .repository import STORY_KINDS


def await_story(state):
    definition = state['action']['definition']
    # Żadnego wyniku ani sekretu w formularzu zatwierdzenia.
    value = interrupt({'kind': 'story_confirm', 'action_id': state['turn_id'],
        'label': definition['label'], 'elapsed_seconds': definition['elapsed_seconds'],
        'message': 'Sprawdź rozpoznany zamiar. Potwierdzenie wykona jedno działanie, bez rzutu.'})
    return {'story_decision': StoryDecision.model_validate(value).model_dump()}


def route_prepared(state):
    action = state.get('action')
    if not action:
        return 'finish'
    if action['status'] == 'pending':
        return 'await_story' if action['definition']['kind'] in STORY_KINDS else 'await_roll'
    return route_resolved(state)


def build_graph(nodes, checkpointer):
    builder = StateGraph(StoryTurnState)
    for name in ('load_context','interpret','prepare','resolve','fixed_reply','narrate','fallback','finish',
                 'plan_story','commit_story','cancel_story'):
        builder.add_node(name, getattr(nodes, name))
    builder.add_node('await_roll', await_roll)
    builder.add_node('await_narrator', await_narrator)
    builder.add_node('await_story', await_story)
    builder.add_edge(START, 'load_context')
    builder.add_edge('load_context', 'interpret')
    builder.add_conditional_edges('interpret', route_intent, ['prepare','narrate','fixed_reply'])
    builder.add_conditional_edges('prepare', route_prepared, ['finish','await_roll','await_story','narrate','fixed_reply'])
    builder.add_edge('await_roll', 'resolve')
    builder.add_conditional_edges('resolve', route_resolved, ['narrate','fixed_reply'])
    builder.add_conditional_edges('await_story',
        lambda s: 'plan_story' if s['story_decision']['choice'] == 'confirm' else 'cancel_story',
        ['plan_story','cancel_story'])
    builder.add_edge('plan_story','commit_story')
    builder.add_conditional_edges('commit_story', route_resolved, ['narrate','fixed_reply'])
    builder.add_conditional_edges('cancel_story', route_resolved, ['narrate','fixed_reply'])
    builder.add_conditional_edges('narrate', lambda s: 'await_narrator' if s.get('narration_error') else 'finish',
                                  ['await_narrator','finish'])
    builder.add_conditional_edges('await_narrator',
        lambda s: 'narrate' if s['narration_decision']['choice']=='retry' else 'fallback', ['narrate','fallback'])
    builder.add_edge('fixed_reply','finish')
    builder.add_edge('fallback','finish')
    builder.add_edge('finish',END)
    return builder.compile(checkpointer=checkpointer)
