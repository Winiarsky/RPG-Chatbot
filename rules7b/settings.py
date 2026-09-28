"""Dodatkowe ustawienia; nie zmieniają klienta ani .env istniejącego projektu."""
import os
from dataclasses import dataclass
from dotenv import dotenv_values
from library7a.settings import ROOT


@dataclass(frozen=True)
class IntegrationSettings:
    search_mode: str = 'lexical'
    generation: str = 'auto'
    allow_embeddings_api: bool = False

    def __post_init__(self):
        if self.search_mode not in {'lexical', 'hybrid'}:
            raise ValueError('RPG7B_SEARCH_MODE: lexical albo hybrid.')
        if self.generation not in {'auto', 'off'}:
            raise ValueError('RPG7B_GENERATION: auto albo off.')
        if self.search_mode == 'hybrid' and not self.allow_embeddings_api:
            raise ValueError('Tryb hybrid wymaga jawnego RPG7B_ALLOW_EMBEDDINGS_API=true.')

    def generate(self, provider):
        return self.generation == 'auto' and provider == 'openai'


def load_integration_settings():
    vals = {**dotenv_values(ROOT / '.env'), **os.environ}
    def value(key, default):
        x = vals.get(key)
        return str(default if x is None else x).strip().lower()
    consent = value('RPG7B_ALLOW_EMBEDDINGS_API', 'false')
    if consent not in {'true', 'false'}:
        raise ValueError('RPG7B_ALLOW_EMBEDDINGS_API: true albo false.')
    return IntegrationSettings(value('RPG7B_SEARCH_MODE', 'lexical'),
        value('RPG7B_GENERATION', 'auto'), consent == 'true')
