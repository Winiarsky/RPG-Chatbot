"""Jawna lista dopuszczonych pól. Nigdy nie wysyłamy całego snapshotu do LLM."""
from schemas import CampaignSnapshot


def build_public_context(snapshot: CampaignSnapshot) -> dict:
    state = snapshot.state
    scene = next(s for s in snapshot.scenario.scenes if s.id == state.scene_id)
    character = state.character
    # Celowo tworzymy nowy obiekt, zamiast usuwać gm_only z pełnego model_dump().
    return {
        "ruleset_id": state.ruleset_id,
        "revision": snapshot.revision,
        "character": {
            "id": character.id,
            "name": character.name,
            "class": character.character_class,
            "level": character.level,
            "hp_current": character.hp_current,
            "hp_max": character.hp_max,
            "armor_class": character.armor_class,
            "speed_ft": character.speed_ft,
            "proficiency_bonus": character.proficiency_bonus,
            "abilities": character.abilities.model_dump(),
            "skill_proficiencies": list(character.skill_proficiencies),
            "inventory": [item.model_dump() for item in character.inventory],
            "conditions": list(character.conditions),
        },
        "scene": {
            "id": scene.id,
            "name": scene.name,
            "description": scene.public_description,
            "doors": [
                {"id": door.id, "name": door.name,
                 "is_open": state.doors[door.id].is_open,
                 "description": (door.visible_when_open
                                 if state.doors[door.id].is_open
                                 else door.visible_when_closed)}
                for door in scene.doors
            ],
        },
    }
