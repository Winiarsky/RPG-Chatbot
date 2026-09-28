"""Tworzenie kampanii z szablonów i idempotentna inicjalizacja demo."""
from game_config import GameSettings
from scenario_loader import load_templates
from storage import CampaignNotFound, GameRepository, StorageError


def ensure_demo(repo: GameRepository, settings: GameSettings):
    try:
        return repo.load("demo")
    except CampaignNotFound:
        character, scenario = load_templates(settings.character_path, settings.scenario_path)
        try:
            return repo.create("demo", "Stara wieża — demo", character, scenario)
        except StorageError:
            # Możliwe równoległe pierwsze uruchomienie dwóch okien.
            return repo.load("demo")


def create_campaign(repo: GameRepository, settings: GameSettings, campaign_id: str, name: str):
    character, scenario = load_templates(settings.character_path, settings.scenario_path)
    return repo.create(campaign_id, name, character, scenario)
