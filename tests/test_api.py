import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

MAC = "AA:BB:CC:DD:EE:FF"


def make_client(tmp_path, **overrides):
    settings = Settings(
        data_dir=tmp_path,
        caldav_url="",
        caldav_username="",
        caldav_password="",
        public_base_url="https://example.test",
        **overrides,
    )
    app = create_app(settings)
    return TestClient(app)


@pytest.fixture
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


# --- setup ---------------------------------------------------------------


def test_setup_requires_the_id_header(client):
    assert client.get("/api/setup").status_code == 400


def test_setup_returns_credentials(client):
    response = client.get("/api/setup", headers={"ID": MAC})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == 200
    assert body["api_key"]
    assert len(body["friendly_id"]) == 6
    assert body["image_url"].startswith("https://example.test/image/")


def test_setup_is_idempotent_for_the_same_device(client):
    first = client.get("/api/setup", headers={"ID": MAC}).json()
    second = client.get("/api/setup", headers={"ID": MAC}).json()
    assert first["api_key"] == second["api_key"]
    assert first["friendly_id"] == second["friendly_id"]


def test_mac_case_does_not_create_a_second_device(client):
    first = client.get("/api/setup", headers={"ID": MAC}).json()
    second = client.get("/api/setup", headers={"ID": MAC.lower()}).json()
    assert first["api_key"] == second["api_key"]


def test_pairing_secret_blocks_unknown_devices(tmp_path):
    with make_client(tmp_path, pairing_secret="s3cret") as client:
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 403
        ok = client.get("/api/setup", headers={"ID": MAC, "X-Pairing-Secret": "s3cret"})
        assert ok.status_code == 200
        # A device that already paired may re-run setup without the secret.
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200


# --- display -------------------------------------------------------------


def test_display_rejects_unknown_devices(client):
    response = client.get("/api/display", headers={"ID": MAC, "Access-Token": "nope"})
    assert response.status_code == 401


def test_display_rejects_a_wrong_token(client):
    client.get("/api/setup", headers={"ID": MAC})
    response = client.get("/api/display", headers={"ID": MAC, "Access-Token": "wrong"})
    assert response.status_code == 401


def test_display_requires_a_token(client):
    client.get("/api/setup", headers={"ID": MAC})
    assert client.get("/api/display", headers={"ID": MAC}).status_code == 401


def test_display_returns_the_firmware_contract(client):
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    response = client.get("/api/display", headers={"ID": MAC, "Access-Token": api_key})
    assert response.status_code == 200
    body = response.json()

    assert body["status"] == 0
    assert body["filename"].endswith(".bmp")
    assert body["filename"] in body["image_url"]
    # 900 during the day, the remaining sleep during quiet hours.
    assert isinstance(body["refresh_rate"], int)
    assert body["refresh_rate"] >= 60
    assert body["update_firmware"] is False
    assert body["reset_firmware"] is False
    assert body["special_function"] == "none"
    assert body["firmware_url"] is None


def test_display_records_telemetry(client, tmp_path):
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    client.get(
        "/api/display",
        headers={
            "ID": MAC,
            "Access-Token": api_key,
            "Battery-Voltage": "4.12",
            "RSSI": "-67",
            "FW-Version": "1.6.2",
        },
    )
    device = client.app.state.store.get_device(MAC)
    assert device["last_battery"] == pytest.approx(4.12)
    assert device["last_rssi"] == -67
    assert device["last_firmware"] == "1.6.2"
    assert device["last_seen"] is not None


def test_malformed_telemetry_does_not_break_the_poll(client):
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    response = client.get(
        "/api/display",
        headers={"ID": MAC, "Access-Token": api_key, "Battery-Voltage": "n/a", "RSSI": ""},
    )
    assert response.status_code == 200


# --- image delivery ------------------------------------------------------


def test_image_requires_the_token(client):
    digest = client.app.state.store.current_digest()
    assert client.get(f"/image/wrong-token/{digest}.bmp").status_code == 404


def test_image_is_served_as_bmp(client):
    token = client.app.state.image_token
    digest = client.app.state.store.current_digest()
    response = client.get(f"/image/{token}/{digest}.bmp")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/bmp"
    assert response.content[:2] == b"BM"


def test_stale_filename_falls_back_to_the_current_screen(client):
    token = client.app.state.image_token
    response = client.get(f"/image/{token}/0000000000000000.bmp")
    assert response.status_code == 200
    assert response.content[:2] == b"BM"


def test_preview_requires_the_token(client):
    assert client.get("/preview/wrong.png").status_code == 404


def test_preview_returns_png(client):
    token = client.app.state.image_token
    response = client.get(f"/preview/{token}.png")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


# --- logging and health --------------------------------------------------


def test_log_endpoint_accepts_firmware_logs(client):
    response = client.post(
        "/api/log",
        headers={"ID": MAC},
        json={"logs": [{"message": "test", "level": "debug", "battery_voltage": 4.1}]},
    )
    assert response.status_code == 204


def test_log_endpoint_survives_a_broken_body(client):
    response = client.post("/api/log", headers={"ID": MAC}, content=b"not json")
    assert response.status_code == 204


def test_healthz_reports_state(client):
    client.get("/api/setup", headers={"ID": MAC})
    body = client.get("/healthz").json()
    assert body["ok"] is True
    assert body["caldav_configured"] is False
    assert len(body["devices"]) == 1


def test_image_token_survives_a_restart(tmp_path):
    with make_client(tmp_path) as first:
        token = first.app.state.image_token
    with make_client(tmp_path) as second:
        assert second.app.state.image_token == token


def test_device_pairing_survives_a_restart(tmp_path):
    with make_client(tmp_path) as first:
        api_key = first.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    with make_client(tmp_path) as second:
        response = second.get("/api/display", headers={"ID": MAC, "Access-Token": api_key})
        assert response.status_code == 200
