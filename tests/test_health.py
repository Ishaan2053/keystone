def test_health_is_process_liveness():
    from fastapi.testclient import TestClient

    from api.main import app

    response = TestClient(app).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
