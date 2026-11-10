from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import participant, run
from app.people import people_search
from app.schemas import PeopleSearchRequest


def window(start=30, end=60, area="Lobby"):
    now = datetime.now(UTC)
    return {"starts_at": (now + timedelta(minutes=start)).isoformat(),
            "ends_at": (now + timedelta(minutes=end)).isoformat(), "area": area}


def test_break_ownership_overlap_removal_and_withdrawal(application):
    a, own = participant(application, "Test A")
    b, other = participant(application, "Test B")
    path = f"/api/people/{other['id']}/overlap"
    assert a.get(path).json()["status"] == "unknown"
    first = window()
    assert a.post('/api/me/break', json=first).status_code == 200
    assert b.get('/api/me/break').json() == {"break": None}
    assert b.post('/api/me/break', json=window(45, 90, "lobby")).status_code == 200
    overlap = a.get(path).json()
    assert overlap['status'] == 'suggested' and overlap['same_area']
    assert overlap['ends_at'] == first['ends_at']
    assert b.post('/api/me/break', json=window(100, 120)).status_code == 200
    assert a.get(path).json()['status'] == 'no_overlap'
    assert a.delete('/api/me/break').status_code == 204
    assert a.get('/api/me/break').json() == {"break": None}

    assert b.delete('/api/me').status_code == 204
    assert a.get(path).status_code == 404
    assert other['id'] not in application.state.store.memory['networking_breaks']


def test_break_validation_and_expiry(application, client):
    assert client.get('/api/me/break').status_code == 401
    assert client.post('/api/me/break', json=window()).status_code == 401
    assert client.delete('/api/me/break').status_code == 401
    assert client.get('/api/people/unknown/overlap').status_code == 401
    a, card = participant(application)
    for invalid in (window(60, 30), window(-60, -30), window(0, 300), window(0, 3), window(area=' '), window(600, 650)):
        assert a.post('/api/me/break', json=invalid).status_code == 422
    naive = window(); naive['starts_at'] = '2026-11-09T12:00:00'
    assert a.post('/api/me/break', json=naive).status_code == 422
    assert a.post('/api/me/break', json=window()).status_code == 200
    application.state.store.memory['networking_breaks'][card['id']]['ends_at'] = window(-60, -30)['ends_at']
    assert a.get('/api/me/break').json() == {"break": None}
    run(application.state.store.purge_expired())
    assert card['id'] not in application.state.store.memory['networking_breaks']


def test_shuffle_filters_and_no_invented_scores(application):
    a, own = participant(application, "Test Viewer")
    _, real = participant(application, "Test Real", formats=['one_to_one'])
    _, fake = participant(application, "Test Synthetic", formats=['one_to_one'])
    application.state.store.memory_profiles[fake['id']]['dataset'] = 'SynthDemo'
    participant(application, "Test Sponsor", roles=['sponsor'], formats=['one_to_one'])
    participant(application, "Test Async")
    hits = a.post('/api/people/search', json={'mode':'shuffle', 'formats':['one_to_one'], 'include_synthetic':True}).json()['hits']
    assert [hit['id'] for hit in hits] == [real['id']]
    assert hits[0]['percent'] is None and hits[0]['similarity'] is None
    store = application.state.store
    store.client = SimpleNamespace(search=AsyncMock(return_value={'hits': {'hits': [
        {'_source': store.memory_profiles[real['id']]}, {'_source': store.memory_profiles[fake['id']]}]}}))
    store.space.affinities = AsyncMock(return_value=({}, 'keyword_affinity'))
    result = run(people_search(store, PeopleSearchRequest(mode='shuffle'), store.memory_profiles[own['id']]))
    assert [hit['id'] for hit in result['hits']] == [real['id']]
    query = store.client.search.call_args.kwargs['query']['function_score']
    assert query['random_score']['field'] == 'id'
    assert {'bool': {'must_not': [{'term': {'dataset': 'SynthDemo'}}]}} in query['query']['bool']['filter']
    store.client = None
