from __future__ import annotations

from unittest.mock import Mock


def test_server_pool_restores_missing_priority_from_config_order(monkeypatch) -> None:
    from updater import server_pool

    configured_servers = [
        {
            "id": "primary",
            "name": "Primary",
            "host": "192.0.2.1",
            "https_port": 888,
            "http_port": 887,
            "weight": 2,
        },
        {
            "id": "reserve",
            "name": "Reserve",
            "host": "192.0.2.2",
            "https_port": 888,
            "http_port": 887,
            "weight": 1,
        },
    ]
    monkeypatch.setattr(server_pool, "VPS_SERVERS", configured_servers)
    monkeypatch.setattr(
        server_pool.settings_store,
        "get_updater_settings",
        lambda: {},
    )
    save_settings = Mock()
    monkeypatch.setattr(
        server_pool.settings_store,
        "set_updater_settings",
        save_settings,
    )
    monkeypatch.setattr(server_pool.random, "uniform", lambda _start, _end: 0)

    pool = server_pool.ServerPool()

    assert [server["priority"] for server in pool.servers] == [1, 2]
    pool.selected_server = pool.servers[0]
    pool._switch_to_next_server()
    assert pool.selected_server["id"] == "reserve"


def test_server_pool_keeps_explicit_numeric_priority(monkeypatch) -> None:
    from updater import server_pool

    monkeypatch.setattr(
        server_pool,
        "VPS_SERVERS",
        [
            {
                "id": "reserve",
                "name": "Reserve",
                "host": "192.0.2.2",
                "https_port": 888,
                "http_port": 887,
                "weight": 1,
                "priority": "7",
            }
        ],
    )
    monkeypatch.setattr(
        server_pool.settings_store,
        "get_updater_settings",
        lambda: {},
    )
    monkeypatch.setattr(
        server_pool.settings_store,
        "set_updater_settings",
        lambda _settings: None,
    )

    pool = server_pool.ServerPool()

    assert pool.servers[0]["priority"] == 7
