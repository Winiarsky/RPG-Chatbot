"""Jawna ścieżka recover przed zakończeniem unsupported/clarify."""
from langgraph.graph import StateGraph, START, END
from agents.graph import await_roll, await_narrator, route_resolved
from story.graph import await_story
from .models import RecoveryTurnState


from .routing import route_intent, route_recovery, route_prepared


def build_graph(nodes, checkpointer):
    g = StateGraph(RecoveryTurnState)
    for name in ('load_context', 'interpret', 'recover', 'prepare', 'resolve', 'fixed_reply',
                 'narrate', 'fallback', 'finish', 'plan_story', 'commit_story', 'cancel_story'):
        g.add_node(name, getattr(nodes, name))
    g.add_node('await_roll', await_roll)
    g.add_node('await_story', await_story)
    g.add_node('await_narrator', await_narrator)
    g.add_edge(START, 'load_context')
    g.add_edge('load_context', 'interpret')
    g.add_conditional_edges('interpret', route_intent, ['prepare', 'narrate', 'recover'])
    g.add_conditional_edges('recover', route_recovery, ['prepare', 'narrate', 'fixed_reply'])
    g.add_conditional_edges('prepare', route_prepared,
        ['recover', 'fixed_reply', 'narrate', 'await_roll', 'await_story', 'plan_story'])
    g.add_edge('await_roll', 'resolve')
    g.add_conditional_edges('resolve', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('await_story',
        lambda s: 'plan_story' if s['story_decision']['choice'] == 'confirm' else 'cancel_story',
        ['plan_story', 'cancel_story'])
    g.add_edge('plan_story', 'commit_story')
    g.add_conditional_edges('commit_story', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('cancel_story', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('narrate', lambda s: 'await_narrator' if s.get('narration_error') else 'finish',
        ['await_narrator', 'finish'])
    g.add_conditional_edges('await_narrator',
        lambda s: 'narrate' if s['narration_decision']['choice'] == 'retry' else 'fallback', ['narrate', 'fallback'])
    for name in ('fixed_reply', 'fallback'):
        g.add_edge(name, 'finish')
    g.add_edge('finish', END)
    return g.compile(checkpointer=checkpointer)
