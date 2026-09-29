"""Jawna lista możliwości implementacji, NIE lista wszystkich reguł D&D."""
CAPABILITIES = {
    'force_door': frozenset({'ability_check', 'athletics', 'proficiency'}),
    'observe_scene': frozenset({'observation'}),
    'move': frozenset({'scripted_movement'}),
    'talk': frozenset({'scripted_dialogue'}),
    'inspect': frozenset({'scripted_inspection'}),
    'signal': frozenset({'scene_signal'}),
}
SUPPORTED = frozenset().union(*CAPABILITIES.values())


def missing(requirements, kind=None):
    available = SUPPORTED if kind is None else CAPABILITIES.get(kind, frozenset())
    return sorted(set(requirements) - available)


def unsupported_text(requirements):
    return ('Znalezienie reguły nie dodaje jej obsługi do programu. Brak zaimplementowanej obsługi: '
            + ', '.join(requirements) + '. Nie wykonano działania ani nie zmieniono jego parametrów. '
            'To ograniczenie aplikacji, nie zakaz w świecie D&D.')
