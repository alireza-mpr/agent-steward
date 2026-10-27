def pytest_configure(config):
    # Registered here as well as in pytest.ini, because the end-to-end-tests container has no pytest.ini.
    config.addinivalue_line("markers", "end_to_end: runs the whole guardian against the running stack")
