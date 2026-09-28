"""Czyste reguły przejść, testowalne bez importowania LangGraph."""
from story5.repository import STORY_KINDS


def route_intent(state):
    kind = state['intent']['kind']
    return 'prepare' if kind == 'action' else 'narrate' if kind == 'chat' else 'recover'


def route_recovery(state):
    mode = state['recovery']['mode']
    return 'prepare' if mode in {'map_action', 'interaction'} else 'narrate' if mode == 'narrate' else 'fixed_reply'


def route_prepared(state):
    action = state.get('action')
    if not action:
        return 'fixed_reply' if state.get('recovery_attempted') else 'recover'
    if action['status'] != 'pending':
        return 'narrate' if action['status'] == 'resolved' else 'fixed_reply'
    kind = action['definition']['kind']
    if kind == 'signal':
        # Deklaracja gracza już upoważnia do sygnału, lecz nie do wejścia.
        return 'plan_story'
    return 'await_story' if kind in STORY_KINDS else 'await_roll'
