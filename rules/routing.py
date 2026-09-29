"""Czyste przejścia: ich testy nie wymagają ani API, ani LangGraph."""
from improvisation import routing as previous


def after_intent(state):
    if state.get('rules_block'):
        return 'rules_reply'
    if state.get('rules_need') and not state.get('rules_attempted'):
        return 'consult_rules'
    return previous.route_intent(state)


def after_recovery(state):
    if state.get('rules_block'):
        return 'rules_reply'
    if state.get('rules_need') and not state.get('rules_attempted'):
        return 'consult_rules'
    if state.get('rules_record') and state['recovery']['mode'] not in {'map_action', 'interaction'}:
        return 'rules_reply'
    return previous.route_recovery(state)


def after_consult(state):
    if state.get('rules_error'):
        return 'await_rules'
    record = state['rules_record']
    if state['rules_need']['purpose'] == 'question' or state.get('rules_block'):
        return 'rules_reply'
    return 'reconsider' if record['answer']['status'] == 'supported' else 'rules_reply'


def after_prepared(state):
    return 'rules_reply' if state.get('rules_block') else previous.route_prepared(state)
