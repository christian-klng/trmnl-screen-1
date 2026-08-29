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


# --- pairing protection that works with a URL-only device ------------------

OTHER_MAC = "11:22:33:44:55:66"


def test_first_pairing_lock_refuses_a_second_device(tmp_path):
    """The default protection: one device pairs, no other can follow."""
    with make_client(tmp_path) as client:
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200
        assert client.get("/api/setup", headers={"ID": OTHER_MAC}).status_code == 403
        # The paired one keeps working.
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200


def test_first_pairing_lock_can_be_disabled(tmp_path):
    with make_client(tmp_path, lock_after_first_pairing=False) as client:
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200
        assert client.get("/api/setup", headers={"ID": OTHER_MAC}).status_code == 200


def test_mac_allowlist_rejects_everything_else(tmp_path):
    with make_client(tmp_path, allowed_macs=MAC, lock_after_first_pairing=False) as client:
        assert client.get("/api/setup", headers={"ID": OTHER_MAC}).status_code == 403
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200


def test_mac_allowlist_is_case_insensitive(tmp_path):
    with make_client(tmp_path, allowed_macs=MAC.lower()) as client:
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 200


def test_pairing_lock_survives_a_restart(tmp_path):
    with make_client(tmp_path) as first:
        first.get("/api/setup", headers={"ID": MAC})
    with make_client(tmp_path) as second:
        assert second.get("/api/setup", headers={"ID": OTHER_MAC}).status_code == 403


# --- secret URL prefix -----------------------------------------------------


def test_prefix_moves_every_route(tmp_path):
    with make_client(tmp_path, url_prefix="/s/geheim") as client:
        assert client.get("/api/setup", headers={"ID": MAC}).status_code == 404
        assert client.get("/status").status_code == 404

        response = client.get("/s/geheim/api/setup", headers={"ID": MAC})
        assert response.status_code == 200
        assert client.get("/s/geheim/status").status_code == 200


def test_prefix_is_included_in_the_image_url(tmp_path):
    with make_client(tmp_path, url_prefix="/s/geheim") as client:
        body = client.get("/s/geheim/api/setup", headers={"ID": MAC}).json()
        assert body["image_url"].startswith("https://example.test/s/geheim/image/")

        api_key = body["api_key"]
        display = client.get(
            "/s/geheim/api/display", headers={"ID": MAC, "Access-Token": api_key}
        ).json()
        assert "/s/geheim/image/" in display["image_url"]


def test_prefix_is_normalised(tmp_path):
    """A prefix pasted with or without slashes must behave the same."""
    for raw in ("s/geheim", "/s/geheim", "/s/geheim/"):
        with make_client(tmp_path / raw.replace("/", "_"), url_prefix=raw) as client:
            assert client.get("/s/geheim/status").status_code == 200


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
    assert body["filename"].endswith(".png")
    assert body["filename"] in body["image_url"]
    # 900 during the day, the remaining sleep during quiet hours.
    assert isinstance(body["refresh_rate"], int)
    assert body["refresh_rate"] >= 60
    assert body["update_firmware"] is False
    assert body["reset_firmware"] is False
    assert body["special_function"] == "none"
    assert body["firmware_url"] is None
    assert body["maximum_compatibility"] is True  # the first draw of the day
    assert body["temperature_profile"] == "default"


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


# --- full refresh cadence --------------------------------------------------
#
# The device draws a 1-bit image with a partial, flicker-free update. Without a
# black/white wipe every so often the panel washes out over weeks, and the
# firmware's own every-eighth-update wipe never fires for the images we serve.


def poll(client, api_key, mac=MAC):
    return client.get("/api/display", headers={"ID": mac, "Access-Token": api_key}).json()


def redraw(client, digest):
    """Pretend the week changed: a new digest is a new filename."""
    client.app.state.store.save_image(digest, b"not really an image")


def test_the_first_draw_of_the_day_is_a_full_refresh(client):
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    assert poll(client, api_key)["maximum_compatibility"] is True


def test_an_unchanged_screen_never_asks_for_a_full_refresh(client):
    """The device keeps its cached image and does not touch the panel, so a
    full refresh asked for here would simply be thrown away."""
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    poll(client, api_key)  # consumes the daily full refresh

    for _ in range(20):
        assert poll(client, api_key)["maximum_compatibility"] is False

    device = client.app.state.store.get_device(MAC)
    assert device["draws_since_full"] == 0


def test_every_eighth_redraw_is_a_full_refresh(client):
    api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
    poll(client, api_key)  # consumes the daily full refresh

    modes = []
    for i in range(1, 25):
        redraw(client, f"{i:016x}")
        modes.append(poll(client, api_key)["maximum_compatibility"])

    # Seven quiet partials, then the wipe, three times over.
    assert modes == [False] * 7 + [True] + [False] * 7 + [True] + [False] * 7 + [True]


def test_the_cadence_is_configurable(tmp_path):
    with make_client(tmp_path, full_refresh_every=3) as client:
        api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
        poll(client, api_key)

        modes = []
        for i in range(1, 7):
            redraw(client, f"{i:016x}")
            modes.append(poll(client, api_key)["maximum_compatibility"])
        assert modes == [False, False, True, False, False, True]


def test_every_redraw_can_be_forced_full(tmp_path):
    """FULL_REFRESH_EVERY=1 is the setting for scrubbing an already washed-out
    panel back to contrast over a day."""
    with make_client(tmp_path, full_refresh_every=1) as client:
        api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
        poll(client, api_key)
        for i in range(1, 5):
            redraw(client, f"{i:016x}")
            assert poll(client, api_key)["maximum_compatibility"] is True


def test_the_counter_survives_a_restart(tmp_path):
    """It lives on the volume with everything else, so a redeploy in the middle
    of the day must not restart the cadence."""
    with make_client(tmp_path) as client:
        api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
        poll(client, api_key)
        for i in range(1, 8):
            redraw(client, f"{i:016x}")
            poll(client, api_key)

    with make_client(tmp_path) as client:
        redraw(client, "00000000000000ff")
        assert poll(client, api_key)["maximum_compatibility"] is True


def test_the_temperature_profile_is_sent_on_every_response(tmp_path):
    """The firmware overwrites its stored profile from each response, so
    leaving the field out would pin the device to the default forever."""
    with make_client(tmp_path, temperature_profile="a") as client:
        api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
        assert poll(client, api_key)["temperature_profile"] == "a"
        assert poll(client, api_key)["temperature_profile"] == "a"


# --- image delivery ------------------------------------------------------


def test_image_requires_the_token(client):
    digest = client.app.state.store.current_digest()
    assert client.get(f"/image/wrong-token/{digest}.png").status_code == 404


def test_image_is_served_as_png(client):
    token = client.app.state.image_token
    digest = client.app.state.store.current_digest()
    response = client.get(f"/image/{token}/{digest}.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_image_can_still_be_served_as_bmp(tmp_path):
    """The escape hatch, in case a firmware ever mishandles the PNG path."""
    with make_client(tmp_path, image_format="bmp") as client:
        token = client.app.state.image_token
        api_key = client.get("/api/setup", headers={"ID": MAC}).json()["api_key"]
        body = client.get("/api/display", headers={"ID": MAC, "Access-Token": api_key}).json()
        assert body["filename"].endswith(".bmp")

        digest = client.app.state.store.current_digest()
        response = client.get(f"/image/{token}/{digest}.bmp")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/bmp"
        assert response.content[:2] == b"BM"


def test_stale_filename_falls_back_to_the_current_screen(client):
    token = client.app.state.image_token
    response = client.get(f"/image/{token}/0000000000000000.png")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_a_filename_from_the_previous_format_still_resolves(client):
    """After the BMP-to-PNG switch the device asks once for its cached name."""
    token = client.app.state.image_token
    digest = client.app.state.store.current_digest()
    response = client.get(f"/image/{token}/{digest}.bmp")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


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


def test_status_reports_state(client):
    client.get("/api/setup", headers={"ID": MAC})
    body = client.get("/status").json()
    assert body["ok"] is True
    assert body["caldav_configured"] is False
    assert len(body["devices"]) == 1


# --- the platform healthcheck ---------------------------------------------
#
# Regression: /healthz used to live on the prefixed router, so setting
# URL_PREFIX moved it and Railway's healthcheck got a 404 on every attempt.


def test_healthz_stays_at_the_root_without_a_prefix(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_healthz_stays_at_the_root_with_a_prefix(tmp_path):
    with make_client(tmp_path, url_prefix="/s/geheim") as client:
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json() == {"ok": True}


def test_healthz_leaks_nothing(tmp_path):
    """It is reachable without the secret prefix, so it must stay empty."""
    with make_client(tmp_path, url_prefix="/s/geheim") as client:
        client.get("/s/geheim/api/setup", headers={"ID": MAC})
        body = client.get("/healthz").json()
        assert set(body) == {"ok"}


def test_detailed_status_stays_behind_the_prefix(tmp_path):
    with make_client(tmp_path, url_prefix="/s/geheim") as client:
        assert client.get("/status").status_code == 404
        assert client.get("/s/geheim/status").status_code == 200


def test_railway_healthcheck_path_matches_a_real_route(tmp_path):
    """railway.toml and the code have to agree — they did not, and the deploy
    failed on every healthcheck attempt."""
    import re
    from pathlib import Path

    config = (Path(__file__).resolve().parent.parent / "railway.toml").read_text()
    match = re.search(r'healthcheckPath\s*=\s*"([^"]+)"', config)
    assert match, "railway.toml must declare a healthcheckPath"
    path = match.group(1)

    # It has to answer both with and without a prefix configured.
    with make_client(tmp_path / "plain") as client:
        assert client.get(path).status_code == 200
    with make_client(tmp_path / "prefixed", url_prefix="/s/geheim") as client:
        assert client.get(path).status_code == 200


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
