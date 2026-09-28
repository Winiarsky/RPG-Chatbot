"""Test rzeczywistego LangGraph i SQLite z makietami wszystkich modeli, bez API."""
import argparse
import os
from pathlib import Path
from tempfile import TemporaryDirectory


def check(condition, description):
    if not condition:
        raise AssertionError(description)


def scenario_test():
    from langgraph.checkpoint.sqlite import SqliteSaver  # noqa: F401: sprawdzenie realnej zależności
    from chat_service import ChatService
    from config import Settings
    from story5.repository import StoryRepository
    from story5.roles import GameMaster, Narrator, StoryKeeper
    from story5.runtime import StoryController
    root=Path(__file__).resolve().parents[2]
    with TemporaryDirectory(prefix='rpg5-smoke-') as temporary:
        repo=StoryRepository(Path(temporary)/'game.sqlite3')
        repo.initialize()
        repo.create_story('demo5','Test',root/'characters/torin.yaml')
        transport=ChatService(Settings(provider='mock'))
        def make():
            return StoryController(StoryRepository(repo.db_path),GameMaster(transport,mode='function_calling'),
                Narrator(transport),StoryKeeper(transport,mode='function_calling'))
        c=make()
        blocked=c.start('demo5','Wchodzę do wieży',turn_id='blocked')
        check(blocked['run']['status']=='done' and not blocked['interrupts'],'Zablokowane przejście')
        for tid,die in [('failed_roll',9),('passed_roll',10)]:
            wait=c.start('demo5','Próbuję wyważyć drzwi',turn_id=tid)
            check(wait['interrupts'][0]['payload']['kind']=='roll','Oczekiwanie na k20')
            c=make()
            restored=c.view('demo5',tid)
            check(restored['interrupts']==wait['interrupts'],'Checkpoint rzutu po restarcie')
            result=c.resume('demo5',tid,restored['interrupts'][0]['id'],{'choice':'manual','dice':[die]})
            check(result['run']['status']=='done','Zakończenie rzutu')
        check(repo.progress('demo5')=={'elapsed_seconds':120,'noise_events':2},'Koszt rzutów')
        check(repo.load('demo5').state.doors['tower_door'].is_open,'Otwarcie drzwi')
        def execute(text,tid):
            nonlocal c
            wait=c.start('demo5',text,turn_id=tid)
            check(wait['interrupts'][0]['payload']['kind']=='story_confirm','Potwierdzenie działania scenariusza')
            c=make() # nowy kontroler i nowe połączenia za każdą operacją
            restored=c.view('demo5',tid)
            check(restored['interrupts']==wait['interrupts'],'Checkpoint potwierdzenia')
            iid=restored['interrupts'][0]['id']
            done=c.resume('demo5',tid,iid,{'choice':'confirm'})
            check(done['run']['status']=='done','Zakończenie działania scenariusza')
            before=repo.load('demo5')
            again=c.resume('demo5',tid,iid,{'choice':'confirm'})
            check(again['answer']==done['answer'] and repo.load('demo5')==before,'Ponowienie bez skutków')
            return done
        execute('Wchodzę do wieży','enter')
        check(repo.load('demo5').state.scene_id=='vestibule','Zmiana sceny')
        first=execute('Witam Martę','hello')
        check('Jestem Marta' in first['answer'],'Pierwsze spotkanie')
        execute('Pytam Martę o latarnika','hint')
        check(repo.story('demo5')[1].known_facts==['journal_hint'],'Pierwsza wskazówka')
        execute('Idę do archiwum','up')
        c.start('demo5','Rozglądam się',turn_id='look')
        check(repo.story('demo5')[1].known_facts==['journal_hint'],'Oglądanie nie czyta dziennika')
        execute('Czytam dziennik','read')
        check('journal_revelation' in repo.story('demo5')[1].known_facts,'Odkrycie po odczycie')
        execute('Wracam do przedsionka','back')
        repeated=execute('Witam Martę','hello_again')
        check('Pamiętam' in repeated['answer'],'Pamięć NPC po zmianie scen')
        execute('Opowiadam Marcie o dzienniku','share')
        check(repo.story('demo5')[1].known_facts==['journal_hint','journal_revelation','ferryman_mark'],'Warunkowa odpowiedź NPC')
        check(repo.story('demo5')[1].npc_memory['marta'].conversations==4,'Licznik rozmów bez duplikatów')
        check(repo.progress('demo5')=={'elapsed_seconds':330,'noise_events':2},'Końcowy czas i hałas')
        public=repo.bundle4('demo5')[0]
        import json
        check('zapieczętowana skrzynia' not in json.dumps(public),'Brak tajnych notatek w publicznym stanie')


def main():
    for key in ('LANGSMITH_TRACING','LANGCHAIN_TRACING_V2','LANGCHAIN_TRACING'):
        os.environ[key]='false'
    try:
        scenario_test()
        print('OK: rzeczywisty LangGraph + SQLite; rzuty, potwierdzenia, restart, sceny, pamięć NPC i odkrycia.')
        print('Wszystkie modele były makietami. Test użył tymczasowej bazy, bez API i bez zmian w twojej grze.')
        return 0
    except ModuleNotFoundError as exc:
        print(f'BRAK ZALEŻNOŚCI: {exc.name}. Test integracyjny NIE został wykonany.')
        print('Uruchom w środowisku z zależnościami kroku 4.')
        return 1
    except Exception as exc:
        print(f'TEST NIE PRZESZEDŁ: {type(exc).__name__}')
        import traceback
        traceback.print_exc()
        return 1


if __name__=='__main__':
    raise SystemExit(main())
