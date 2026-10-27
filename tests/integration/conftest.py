def pytest_configure(config):
    # Registered here as well as in pytest.ini, because the integration-tests container has no pytest.ini.
    config.addinivalue_line("markers", "integration: needs the running stack and real Entra tokens")
