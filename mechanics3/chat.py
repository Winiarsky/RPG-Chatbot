"""Jawny kontekst kampanii i mechaniki dla ról agentów."""
from public_context import build_public_context


def public_context3(snapshot, mechanics: dict) -> dict:
    context = build_public_context(snapshot)
    pending = mechanics['pending']
    context['mechanics'] = {
        'elapsed_seconds': mechanics['progress']['elapsed_seconds'],
        'noise_events': mechanics['progress']['noise_events'],
        'pending_roll': None if pending is None else {
            'action_id': pending['action_id'], 'label': pending['definition']['label'],
            'plan': pending['plan']},
    }
    return context
