"""What must not reach a log file: this machine's addresses, and the
account a session belongs to."""
from __future__ import annotations

import logging

from app.broker.angelone import auth
from app.broker.angelone.auth import mask_identifier, redact_secrets


def test_a_failed_request_does_not_write_down_where_this_machine_is():
    """SmartApi logs every header of a request that fails. Three of them
    identify the machine: its public address, its local one and its
    network-card number. Seen in a real log on a connect timeout to the
    historical API, beside a key and a token that were already masked."""
    import logzero

    auth._load_sdk()          # installs the redaction; no network, no login
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    handler = Capture()
    logzero.logger.addHandler(handler)
    try:
        headers = {"Content-type": "application/json", "X-ClientLocalIP": "192.168.1.23",
                   "X-ClientPublicIP": "203.0.113.77", "X-MACAddress": "0a:1b:2c:3d:4e:5f",
                   "Accept": "application/json", "X-PrivateKey": "APIKEY-9f3c",
                   "X-UserType": "USER", "X-SourceID": "WEB",
                   "Authorization": "Bearer eyJhbGciOiJIUzUxMiJ9.payload.sig-77"}
        request = {"exchange": "NSE", "symboltoken": "2885", "interval": "FIVE_MINUTE",
                   "fromdate": "2015-11-02 00:00", "todate": "2015-12-02 00:00"}
        url = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"
        logzero.logger.error(
            f"Error occurred while making a POST request to {url}. Headers: {headers}, "
            f"Request: {request}, Response: HTTPSConnectionPool: connect timeout=7")
    finally:
        logzero.logger.removeHandler(handler)

    assert len(captured) == 1
    line = captured[0]
    for private in ("203.0.113.77", "192.168.1.23", "0a:1b:2c:3d:4e:5f", "APIKEY-9f3c",
                    "sig-77"):
        assert private not in line, "leaked {!r} in: {}".format(private, line)
    # What makes the failure diagnosable survives: which call, for what, and why.
    for kept in ("getCandleData", "'symboltoken': '2885'", "FIVE_MINUTE", "connect timeout=7",
                 "'X-SourceID': 'WEB'"):
        assert kept in line


def test_redaction_reads_the_header_names_however_they_are_cased():
    text = "{'x-clientpublicip': '203.0.113.77', \"X-MACADDRESS\": \"0a:1b:2c:3d:4e:5f\"}"
    cleaned = redact_secrets(text)
    assert "203.0.113.77" not in cleaned and "0a:1b" not in cleaned


def test_an_account_is_named_in_a_log_by_its_last_two_characters():
    assert mask_identifier("K123456") == "*****56"
    assert mask_identifier("AB") == "**" and mask_identifier("") == "**"
    assert mask_identifier(None) == "**"


def test_a_login_does_not_log_the_whole_client_code(monkeypatch, caplog):
    class Sdk:
        def __init__(self, api_key):
            pass

        def generateSession(self, client_code, password, totp):
            return {"status": True, "data": {"feedToken": "f", "refreshToken": "r"}}

    monkeypatch.setattr(auth, "_load_sdk", lambda: Sdk)
    monkeypatch.setattr(auth, "get_settings", lambda: type("Cfg", (), {
        "angel_configured": True, "angel_api_key": "key", "angel_client_code": "K123456",
        "angel_password": "pw", "angel_totp_secret": "JBSWY3DPEHPK3PXP"})())
    service = auth.AngelOneAuthService()
    with caplog.at_level(logging.INFO, logger="meridian.angelone.auth"):
        service.login()
    logged = " ".join(record.getMessage() for record in caplog.records)
    assert "session established" in logged
    assert "K123456" not in logged and "*****56" in logged
