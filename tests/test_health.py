def test_health_is_process_liveness():
    from api.main import app
    from fastapi.testclient import TestClient

    response = TestClient(app).get('/health')

    assert response.status_code == 200
    assert response.json() == {'status': 'ok'}
