"""Prawdziwy StateGraph + SqliteSaver; atrapami są wyłącznie odpowiedzi LLM."""
import pytest
pytest.importorskip('langgraph', reason='Integracja wymaga langgraph z requirements.txt.')
pytest.importorskip('langgraph.checkpoint.sqlite', reason='Integracja wymaga langgraph-checkpoint-sqlite.')
from chat_service import ChatError
from agents.runtime import GraphController
from agents.repository import GraphRepository, WorkflowError


def controller(repo, nodes):
    return GraphController(repo, nodes.gm, nodes.narrator)


def pending(c):
    view = c.start('demo','Wyważam drzwi',turn_id='graph1')
    assert view['run']['status']=='waiting'
    assert view['interrupts'][0]['payload']['kind']=='roll'
    return view


def test_full_graph_restart_and_idempotence(repo,nodes):
    c=controller(repo,nodes); view=pending(c)
    fresh=controller(GraphRepository(repo.db_path),nodes)
    restored=fresh.view('demo','graph1')
    assert restored['interrupts']==view['interrupts']
    done=fresh.resume('demo','graph1',view['interrupts'][0]['id'],{'choice':'manual','dice':[10]})
    assert done['run']['status']=='done' and '10 +5 = 15' in done['answer']
    assert repo.progress('demo')['elapsed_seconds']==60
    again=fresh.resume('demo','graph1',view['interrupts'][0]['id'],{'choice':'manual','dice':[10]})
    assert again['answer']==done['answer']
    assert repo.progress('demo')['elapsed_seconds']==60
    assert len(repo.bundle4('demo')[3])==1


def test_graph_no_roll_and_question(repo,nodes):
    c=controller(repo,nodes)
    assert c.start('demo','Rozglądam się',turn_id='look')['run']['status']=='done'
    assert c.start('demo','Ile mam HP?',turn_id='hp')['run']['status']=='done'
    assert repo.progress('demo')['elapsed_seconds']==0
    assert len(repo.recent_actions('demo'))==1


@pytest.mark.parametrize('text',['Atakuję goblina','Otwieram drzwi'])
def test_graph_refuses_or_clarifies(repo,nodes,text):
    view=controller(repo,nodes).start('demo',text,turn_id='nonaction')
    assert view['run']['status']=='done' and not view['interrupts']
    assert repo.recent_actions('demo')==[]


def test_bad_resume_does_not_destroy_checkpoint(repo,nodes):
    c=controller(repo,nodes); view=pending(c); iid=view['interrupts'][0]['id']
    with pytest.raises(ValueError): c.resume('demo','graph1',iid,{'choice':'manual','dice':[99]})
    assert c.view('demo','graph1')['interrupts'][0]['id']==iid
    assert c.resume('demo','graph1',iid,{'choice':'manual','dice':[9]})['run']['status']=='done'
    assert not repo.load('demo').state.doors['tower_door'].is_open


def test_wrong_interrupt_id_rejected(repo,nodes):
    c=controller(repo,nodes); pending(c)
    with pytest.raises(WorkflowError): c.resume('demo','graph1','stale',{'choice':'app'})
    assert repo.progress('demo')['elapsed_seconds']==0


def test_narrator_retry_after_restart_does_not_reroll(repo,nodes):
    class BrokenOnce:
        def __init__(self): self.calls=0
        def narrate(self,*_):
            self.calls+=1
            if self.calls==1: raise ChatError('test: brak opisu')
            return 'Drzwi otwierają się.'
    actor=BrokenOnce(); c=GraphController(repo,nodes.gm,actor); view=pending(c)
    wait=c.resume('demo','graph1',view['interrupts'][0]['id'],{'choice':'manual','dice':[10]})
    assert wait['interrupts'][0]['payload']['kind']=='narration_retry'
    assert repo.load('demo').state.doors['tower_door'].is_open
    fresh=GraphController(GraphRepository(repo.db_path),nodes.gm,actor)
    result=fresh.resume('demo','graph1',wait['interrupts'][0]['id'],{'choice':'retry'})
    assert result['run']['status']=='done' and actor.calls==2
    assert repo.progress('demo')['elapsed_seconds']==60


def test_resume_failed_node_after_committed_roll(repo,nodes):
    c=controller(repo,nodes); view=pending(c)
    original=c.nodes.resolve
    def fault(state):
        original(state)  # transakcja mechaniki się zatwierdziła
        raise RuntimeError('symulacja awarii przed checkpointem')
    c.nodes.resolve=fault
    with pytest.raises(WorkflowError):
        c.resume('demo','graph1',view['interrupts'][0]['id'],{'choice':'manual','dice':[10]})
    assert repo.progress('demo')['elapsed_seconds']==60
    fresh=controller(GraphRepository(repo.db_path),nodes)
    done=fresh.retry('demo','graph1')
    assert done['run']['status']=='done'
    assert repo.progress('demo')['elapsed_seconds']==60


def test_cancel_does_not_roll(repo,nodes):
    c=controller(repo,nodes); view=pending(c)
    done=c.resume('demo','graph1',view['interrupts'][0]['id'],{'choice':'cancel'})
    assert done['run']['status']=='done'
    assert repo.progress('demo')['elapsed_seconds']==0


def test_classifier_error_recoverable(repo,nodes):
    class GM:
        def __init__(self): self.calls=0
        def interpret(self,*args):
            self.calls+=1
            if self.calls==1: raise ChatError('test: MG offline')
            return nodes.gm.interpret(*args)
    gm=GM(); c=GraphController(repo,gm,nodes.narrator)
    with pytest.raises(WorkflowError): c.start('demo','Wyważam drzwi',turn_id='graph1')
    assert repo.get_run('demo','graph1')['status']=='failed'
    result=c.retry('demo','graph1')
    assert result['interrupts'][0]['payload']['kind']=='roll' and gm.calls==2
