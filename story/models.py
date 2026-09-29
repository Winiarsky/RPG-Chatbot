"""Scenariusz jest zamkniętym katalogiem faktów i operacji, nie promptem z SQL."""
from typing import Literal, Self
from pydantic import BaseModel, ConfigDict, Field, model_validator
from schemas import Identifier, Model, ScenarioDefinition, Text
from mechanics.models import ActionPack
from agents.models import TurnState


class Fact(Model):
    id: Identifier
    text: Text


class Beat(Model):
    id: Identifier
    topic: Text
    reply: Text
    reveals: list[Identifier] = Field(default_factory=list)
    requires_facts: list[Identifier] = Field(default_factory=list)
    requires_met: bool | None = None
    min_noise: int = Field(default=0, ge=0, strict=True)
    max_noise: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode='after')
    def range_ok(self) -> Self:
        if len(self.reveals) != len(set(self.reveals)):
            raise ValueError('Odpowiedź nie może ujawniać tego samego faktu dwukrotnie.')
        if self.max_noise is not None and self.max_noise < self.min_noise:
            raise ValueError('Nieprawidłowy zakres hałasu.')
        return self


class NPC(Model):
    id: Identifier
    name: Text
    scene_id: Identifier
    public_description: Text
    gm_notes: Text
    beats: list[Beat] = Field(min_length=1)
    default_beat_id: Identifier


class StoryAction(Model):
    id: Identifier
    label: Text
    kind: Literal['move', 'talk', 'inspect']
    scene_id: Identifier
    target_id: Identifier
    aliases: list[Text] = Field(default_factory=list)
    required_open_door: Identifier | None = None
    result_text: Text
    reveals: list[Identifier] = Field(default_factory=list)
    elapsed_seconds: int = Field(default=0, ge=0, le=3600, strict=True)

    @model_validator(mode='after')
    def coherent(self) -> Self:
        if len(self.reveals) != len(set(self.reveals)):
            raise ValueError('Działanie nie może ujawniać tego samego faktu dwukrotnie.')
        if self.kind == 'talk' and self.reveals:
            raise ValueError('Informacje z rozmowy pochodzą z wybranego beat, nie całej rozmowy.')
        if self.kind != 'move' and self.required_open_door is not None:
            raise ValueError('Bramka drzwi jest obsługiwana tylko dla przejścia.')
        return self


class StoryBook(Model):
    version: Literal[1] = 1
    id: Identifier
    scenario: ScenarioDefinition
    mechanics: ActionPack
    npcs: list[NPC] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)
    actions: list[StoryAction] = Field(default_factory=list)

    @model_validator(mode='after')
    def references(self) -> Self:
        scenes = {s.id: s for s in self.scenario.scenes}
        doors = {d.id for s in self.scenario.scenes for d in s.doors}
        facts = {f.id for f in self.facts}
        npcs = {n.id: n for n in self.npcs}
        if len(facts) != len(self.facts) or len(npcs) != len(self.npcs):
            raise ValueError('Powtórzony identyfikator faktu lub NPC.')
        ids = [a.id for a in self.actions] + [a.id for a in self.mechanics.actions]
        if len(ids) != len(set(ids)):
            raise ValueError('Powtórzony identyfikator działania.')
        if self.mechanics.scenario_id != self.scenario.id or self.mechanics.ruleset_id != self.scenario.ruleset_id:
            raise ValueError('Pakiet mechaniki nie pasuje do scenariusza.')
        for a in self.mechanics.actions:
            if a.scene_id not in scenes or (a.door_id and a.door_id not in {d.id for d in scenes[a.scene_id].doors}):
                raise ValueError('Nieprawidłowe referencje działania mechanicznego.')
        for n in self.npcs:
            if n.scene_id not in scenes:
                raise ValueError('NPC nie ma sceny.')
            bids = {b.id for b in n.beats}
            if len(bids) != len(n.beats) or n.default_beat_id not in bids:
                raise ValueError('Nieprawidłowe identyfikatory odpowiedzi NPC.')
            default = next(b for b in n.beats if b.id == n.default_beat_id)
            if default.requires_facts or default.requires_met is not None or default.min_noise or default.max_noise is not None or default.reveals:
                raise ValueError('Domyślna odpowiedź musi być bezwarunkowa i nie ujawniać faktów.')
            for b in n.beats:
                if not set(b.reveals + b.requires_facts) <= facts:
                    raise ValueError('Odpowiedź wskazuje nieznany fakt.')
        for a in self.actions:
            if a.scene_id not in scenes or not set(a.reveals) <= facts:
                raise ValueError('Działanie wskazuje nieznaną scenę lub fakt.')
            if a.kind == 'move' and (a.target_id not in scenes or a.target_id == a.scene_id):
                raise ValueError('Przejście musi wskazywać inną istniejącą scenę.')
            if a.kind == 'talk' and (a.target_id not in npcs or npcs[a.target_id].scene_id != a.scene_id):
                raise ValueError('Rozmowa wymaga NPC w tej samej scenie.')
            if a.required_open_door and a.required_open_door not in doors:
                raise ValueError('Przejście wskazuje nieznane drzwi.')
        return self


class NPCMemory(Model):
    conversations: int = Field(default=0, ge=0, strict=True)
    discussed_beats: list[Identifier] = Field(default_factory=list)


class StoryMemory(Model):
    visited_scenes: list[Identifier] = Field(default_factory=list)
    known_facts: list[Identifier] = Field(default_factory=list)
    npc_memory: dict[str, NPCMemory] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique(self) -> Self:
        for values in [self.visited_scenes, self.known_facts, *[n.discussed_beats for n in self.npc_memory.values()]]:
            if len(values) != len(set(values)):
                raise ValueError('Pamięć zawiera powtórzone identyfikatory.')
        return self


class BeatSelection(BaseModel):
    """ŻADNEGO swobodnego tekstu od opiekuna do narratora."""
    model_config = ConfigDict(extra='forbid', strict=True)
    beat_id: str | None = Field(description='Dokładny ID jednej dozwolonej odpowiedzi NPC; null poza rozmową.')


class StoryDecision(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    choice: Literal['confirm', 'cancel']


class StoryTurnState(TurnState, total=False):
    story_decision: dict
    keeper_selection: dict
