import importlib
import pytest


@pytest.fixture()
def app(monkeypatch, tmp_path):
    """
    Configure app for testing:
    - set env vars BEFORE importing the Flask app module
    - isolate DBs to tmp_path so tests don't touch real data/
    """
    monkeypatch.setenv("FLASK_SECRET_KEY", "test-secret")
    monkeypatch.setenv("STAFF_REGISTRATION_CODE", "TESTCODE")
    monkeypatch.setenv("AUTH_DB_PATH", str(tmp_path / "auth.db"))
    monkeypatch.setenv("DAILY_PLAN_DB_PATH", str(tmp_path / "daily_plan.db"))

    # Import AFTER env vars are set
    import src.web.app as app_module
    importlib.reload(app_module)  # ensure module-level paths re-read env vars

    app_module.app.config.update(TESTING=True)

    yield app_module.app


@pytest.fixture()
def client(app):
    return app.test_client()


def register(client, username="u1", password="pass123", code="TESTCODE"):
    return client.post(
        "/register",
        data={
            "username": username,
            "password": password,
            "password2": password,
            "reg_code": code,
        },
        follow_redirects=False,
    )


def login(client, username="u1", password="pass123"):
    return client.post(
        "/login",
        data={"username": username, "password": password, "next": "/residents"},
        follow_redirects=False,
    )


def test_protected_route_redirects_to_login(client):
    res = client.get("/residents", follow_redirects=False)
    assert res.status_code in (302, 308)
    assert "/login" in (res.headers.get("Location") or "")


def test_register_rejects_wrong_staff_code(client):
    res = register(client, username="badcode", code="WRONG")
    # Your register route re-renders the page with a flash message
    assert res.status_code == 200


def test_register_then_login_success(client):
    r = register(client, username="staff1")
    assert r.status_code in (302, 308)  # usually redirects to /login

    l = login(client, username="staff1")
    assert l.status_code in (302, 308)
    assert "/residents" in (l.headers.get("Location") or "")


def test_logout_clears_session(client):
    register(client, username="staff2")
    login(client, username="staff2")

    out = client.post("/logout", follow_redirects=False)
    assert out.status_code in (302, 308)
    assert "/login" in (out.headers.get("Location") or "")

    # After logout, protected route should redirect to login
    res = client.get("/residents", follow_redirects=False)
    assert res.status_code in (302, 308)
    assert "/login" in (res.headers.get("Location") or "")
