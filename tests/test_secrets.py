"""비밀 파일화(D84): 토큰은 NAME_FILE 경로의 파일이 우선, 없으면 NAME 환경 변수. 값은 로그·응답·오류에 나오지 않는다."""
import threading
import urllib.error
import urllib.request

import pytest

from model_lab.errors import ConfigError
from model_lab.factories import resolve_args
from model_lab.service.app import Service, main, make_server

RT, WT = "READ-SECRET-VALUE-123", "WRITE-SECRET-VALUE-456"


def test_service_main_reads_tokens_from_files_and_prefers_them(tmp_path, monkeypatch, capsys):
    (tmp_path / "r").write_text(f"release-gate={RT}\n")
    (tmp_path / "w").write_text(f"model-lab={WT}\n")
    started = {}
    import model_lab.service.app as app
    monkeypatch.setattr(app, "make_server", lambda svc, host, port: started.setdefault("svc", svc) and (_ for _ in ()).throw(KeyboardInterrupt()))
    env = {"MODEL_REGISTRY_READ_TOKENS_FILE": str(tmp_path / "r"), "MODEL_REGISTRY_WRITE_TOKENS_FILE": str(tmp_path / "w"),
           "MODEL_REGISTRY_READ_TOKENS": "release-gate=ENV-LOSES", "MODEL_REGISTRY_ROOT": str(tmp_path / "root")}
    with pytest.raises(KeyboardInterrupt):
        main(env)
    svc = started["svc"]
    assert svc.read == {RT: "release-gate"} and svc.write == {WT: "model-lab"}


def test_service_main_env_fallback_and_missing_file_error_has_no_value(tmp_path, capsys):
    assert main({"MODEL_REGISTRY_READ_TOKENS_FILE": str(tmp_path / "nope"), "MODEL_REGISTRY_WRITE_TOKENS": f"model-lab={WT}",
                 "MODEL_REGISTRY_ROOT": str(tmp_path / "r")}) == 2
    err = capsys.readouterr().err
    assert "MODEL_REGISTRY_READ_TOKENS_FILE" in err and WT not in err
    (tmp_path / "empty").write_text("\n")
    assert main({"MODEL_REGISTRY_WRITE_TOKENS_FILE": str(tmp_path / "empty"), "MODEL_REGISTRY_ROOT": str(tmp_path / "r")}) == 2


def test_malformed_token_spec_error_does_not_echo_value(tmp_path, capsys):
    assert main({"MODEL_REGISTRY_READ_TOKENS": WT, "MODEL_REGISTRY_ROOT": str(tmp_path / "r")}) == 2
    assert WT not in capsys.readouterr().err


def test_secrets_never_appear_in_logs_readyz_or_error_bodies(tmp_path):
    logs = []
    svc = Service(tmp_path / "srv", {RT: "release-gate"}, {WT: "model-lab"}, log=logs.append)
    srv = make_server(svc)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}"
    bodies = []
    def get(path, token=None, method="GET", data=None):
        r = urllib.request.Request(url + path, method=method, data=data)
        if token:
            r.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(r) as resp:
                bodies.append(resp.read().decode())
        except urllib.error.HTTPError as e:
            bodies.append(e.read().decode())
    try:
        get("/healthz"); get("/readyz"); get("/v1/packages"); get("/v1/packages", "wrong-token")
        get("/v1/packages", RT); get("/v1/packages", RT, "POST", b"x"); get("/v1/packages", WT, "POST", b"not-a-tar")
        get(f"/v1/packages?limit=x", RT)
    finally:
        srv.shutdown()
    text = "\n".join(bodies + logs)
    assert RT not in text and WT not in text and "wrong-token" not in text


def test_factory_args_read_secret_from_file_and_errors_have_no_value(tmp_path):
    (tmp_path / "tok").write_text("FILE-TOKEN\n")
    assert resolve_args({"base_url": "u", "token_env": "T"}, {"T_FILE": str(tmp_path / "tok"), "T": "env"}) == {"base_url": "u", "token": "FILE-TOKEN"}
    assert resolve_args({"token_env": "T"}, {"T": "e"}) == {"token": "e"}
    with pytest.raises(ConfigError) as ei:
        resolve_args({"token_env": "T"}, {"T_FILE": str(tmp_path / "missing"), "T": "SHOULD-NOT-LEAK"})
    assert "SHOULD-NOT-LEAK" not in str(ei.value) and "T_FILE" in str(ei.value)
    with pytest.raises(ConfigError):
        resolve_args({"token_env": "NOPE"}, {})
