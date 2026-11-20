from base64 import b64encode
from fastapi import APIRouter
from app.inputs import Paired, Supplement
from .repository import fetch, history
from .service import run, replay, get, complete

router = APIRouter()

@router.post('/api/runs')
def create(source: Paired):
    return run(source.raw(),source.ledger())

@router.get('/api/runs')
def list_runs():
    return history()

@router.get('/api/runs/{run_id}')
def detail(run_id: str):
    return get(run_id)

@router.post('/api/runs/{run_id}/replay')
def again(run_id: str):
    return replay(run_id)

@router.post('/api/runs/{run_id}/complete')
def supplement(run_id: str, payload: Supplement):
    return complete(run_id,payload.raw())


@router.get('/api/runs/{run_id}/source')
def source_inputs(run_id: str):
    record = fetch(run_id)
    return {'data_base64': b64encode(record['source']).decode(),
            'book_base64': b64encode(record['book']).decode()}
