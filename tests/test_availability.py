from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from conftest import participant, run
from app.availability import available_people
from app.policy import search_filters
from app.schemas import SearchRequest


def test_availability_explicit_owned_expiring_and_revocable(application, client):
    assert client.get('/api/people/available').status_code == 401
    assert client.post('/api/me/availability', json={'enabled': True}).status_code == 401
    a, own = participant(application, 'Viewer')
    b, other = participant(application, 'Available attendee', availability='now')
    assert a.get('/api/people/available').json()['hits'] == []  # old free text is not presence
    result = b.post('/api/me/availability', json={'enabled': True}).json()
    assert datetime.fromisoformat(result['available_until']) > datetime.now(UTC)
    assert a.get('/api/me').json()['profile']['available_until'] is None
    assert datetime.fromisoformat(b.get('/api/me').json()['profile']['available_until']) == datetime.fromisoformat(result['available_until'])
    assert [p['id'] for p in a.get('/api/people/available').json()['hits']] == [other['id']]
    assert b.get('/api/people/available').json()['hits'] == []
    assert b.post('/api/me/availability', json={'enabled': False}).status_code == 200
    assert a.get('/api/people/available').json()['hits'] == []
    assert b.post('/api/me/availability', json={'enabled': True, 'minutes': 61}).status_code == 422
    b.post('/api/me/availability', json={'enabled': True})
    application.state.store.memory_profiles[other['id']]['available_until'] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    assert a.get('/api/people/available').json()['hits'] == []
    b.post('/api/me/availability', json={'enabled': True})
    assert b.delete('/api/me').status_code == 204
    assert a.get('/api/people/available').json()['hits'] == []
    assert b.post('/api/me/availability', json={'enabled': True}).status_code == 403


def test_availability_filters_and_elastic_parity(application):
    a, own = participant(application, 'Viewer')
    b, other = participant(application, 'Peer')
    b.post('/api/me/availability', json={'enabled': True})
    store = application.state.store
    doc = store.memory_profiles[other['id']]
    for changed in ({'dataset': 'SynthDemo'}, {'event_id': 'another'}, {'consent_to_discover': False},
                    {'state': 'revoked'}, {'roles': ['sponsor']}, {'available_until': None}):
        copy = {**doc, **changed, 'id': 'filtered'}
        store.memory_profiles['filtered'] = copy
        assert [p['id'] for p in a.get('/api/people/available').json()['hits']] == [other['id']]
    request = SearchRequest(question='Python', available_now=True)
    assert {'range': {'available_until': {'gt': 'now'}}} in search_filters(request, store.settings.demo_event_id)
    store.client = SimpleNamespace(search=AsyncMock(return_value={'hits': {'hits': [{'_source': doc}, {'_source': copy}]}}))
    try:
        result = run(available_people(store, store.memory_profiles[own['id']]))
        assert [p['id'] for p in result['hits']] == [other['id']]
        assert {'range': {'available_until': {'gt': 'now'}}} in store.client.search.call_args.kwargs['query']['bool']['filter']
    finally:
        store.client = None


def test_sponsor_catalog_is_separate_and_labeled(client, application):
    data = client.get('/api/sponsors').json()
    assert data['dataset'] == 'SynthDemo'
    assert {b['name'] for b in data['booths']} == {'Elastic', 'Radar', 'Dash0', 'Gitar'}
    assert len({b['booth'] for b in data['booths']}) == 4
    assert not application.state.store.memory_profiles


def test_sponsor_seeding_is_labeled_idempotent_and_searchable(application):
    from app.sponsors import BOOTHS
    from scripts.seed_sponsors import seed_sponsors
    store = application.state.store
    assert run(seed_sponsors(store)) == 4
    first_ids = set(store.memory_profiles)
    assert run(seed_sponsors(store)) == 4
    assert set(store.memory_profiles) == first_ids
    assert {p['display_name'] for p in store.memory_profiles.values()} == {f"{b['name']} Demo Booth" for b in BOOTHS}
    assert all(p['dataset'] == 'SynthDemo' and p['roles'] == ['sponsor'] for p in store.memory_profiles.values())
    viewer, _ = participant(application, 'Viewer')
    query = {'question': 'Kubernetes', 'sponsor_intent': True, 'include_synthetic': True}
    assert any(p['display_name'] == 'Radar Demo Booth' for p in viewer.post('/api/search', json=query).json())
    assert viewer.post('/api/search', json={**query, 'include_synthetic': False}).json() == []
    assert viewer.get('/api/people/available').json()['hits'] == []


def test_availability_write_budget(application):
    client, _ = participant(application, 'Budget attendee')
    responses = [client.post('/api/me/availability', json={'enabled': False}) for _ in range(21)]
    assert [r.status_code for r in responses[:20]] == [200] * 20
    assert responses[-1].status_code == 429
    assert int(responses[-1].headers['Retry-After']) >= 1
