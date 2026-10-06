import pytest
import threading

@pytest.fixture(autouse=True, scope="function")
def global_isolate_db(tmp_path, monkeypatch):
    """
    Ensure every test automatically uses a fresh, isolated SQLite database.
    This prevents test data (e.g., FinOps mocked values or audit logs) from bleeding
    into the developer's real app.db.
    """
    db_file = tmp_path / "test_global.db"
    monkeypatch.setenv("SQLITE_DB_PATH", str(db_file))
    
    # Enable test auth mode so existing tests pass
    monkeypatch.setenv("AUTH_MODE", "local_dev")
    monkeypatch.setenv("DEV_AUTH_USER", "test-admin:PlatformAdmin")
    
    import src.db as db
    db.DB_PATH = db_file
    db._local = threading.local()
    
    # Initialize the fresh test database schema
    db.init_db()
    
    yield db_file
    
    # Clean up connection if any
    if hasattr(db._local, "conn"):
        try:
            db._local.conn.close()
        except Exception:
            pass
