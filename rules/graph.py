"""Jawne węzły konsultacji; efekty gry nadal wykonuje silnik kroków 3–6."""
from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt
from agents.graph import await_roll, await_narrator, route_resolved
from story.graph import await_story
from .models import RulesTurnState, RulesRetry
from .routing import after_intent, after_recovery, after_consult, after_prepared


def await_rules(state):
    value = interrupt({'kind': 'rules_retry', 'message': 'Nie udało się zakończyć konsultacji. '
        'Nie wykonano działania.', 'error': state['rules_error']})
    return {'rules_decision': RulesRetry.model_validate(value).model_dump()}


def build_graph(nodes, checkpointer):
    g = StateGraph(RulesTurnState)
    for name in ('load_context', 'interpret', 'recover', 'prepare', 'resolve', 'fixed_reply',
                 'narrate', 'fallback', 'finish', 'plan_story', 'commit_story', 'cancel_story',
                 'consult_rules', 'reconsider', 'rules_reply', 'stop_rules'):
        g.add_node(name, getattr(nodes, name))
    for name, fn in [('await_roll', await_roll), ('await_story', await_story),
                     ('await_narrator', await_narrator), ('await_rules', await_rules)]:
        g.add_node(name, fn)
    g.add_edge(START, 'load_context')
    g.add_edge('load_context', 'interpret')
    g.add_conditional_edges('interpret', after_intent, ['prepare', 'narrate', 'recover', 'consult_rules', 'rules_reply'])
    g.add_conditional_edges('recover', after_recovery, ['prepare', 'narrate', 'fixed_reply', 'consult_rules', 'rules_reply'])
    g.add_conditional_edges('consult_rules', after_consult, ['await_rules', 'rules_reply', 'reconsider'])
    g.add_conditional_edges('await_rules', lambda s: 'consult_rules' if s['rules_decision']['choice'] == 'retry'
        else 'stop_rules', ['consult_rules', 'stop_rules'])
    g.add_edge('stop_rules', 'rules_reply')
    g.add_edge('rules_reply', 'finish')
    g.add_conditional_edges('reconsider', lambda s: s['rules_after'], ['prepare', 'recover', 'rules_reply'])
    g.add_conditional_edges('prepare', after_prepared,
        ['recover', 'fixed_reply', 'narrate', 'await_roll', 'await_story', 'plan_story', 'rules_reply'])
    g.add_edge('await_roll', 'resolve')
    g.add_conditional_edges('resolve', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('await_story', lambda s: 'plan_story' if s['story_decision']['choice'] == 'confirm'
        else 'cancel_story', ['plan_story', 'cancel_story'])
    g.add_edge('plan_story', 'commit_story')
    g.add_conditional_edges('commit_story', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('cancel_story', route_resolved, ['narrate', 'fixed_reply'])
    g.add_conditional_edges('narrate', lambda s: 'await_narrator' if s.get('narration_error') else 'finish',
        ['await_narrator', 'finish'])
    g.add_conditional_edges('await_narrator', lambda s: 'narrate' if s['narration_decision']['choice'] == 'retry'
        else 'fallback', ['narrate', 'fallback'])
    for name in ('fixed_reply', 'fallback'):
        g.add_edge(name, 'finish')
    g.add_edge('finish', END)
    return g.compile(checkpointer=checkpointer)
