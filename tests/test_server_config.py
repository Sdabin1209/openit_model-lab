"""model-registry 서버 면에서 '우리 코드'가 하는 일만 본다(U422, ADR 0029). 서버 라이브러리(waitress)·신호·link의 시간 동작은 그 라이브러리의 몫이라 시험하지 않는다.
이 영역의 책임은 둘이다. (1) 라이브러리에 올바른 값을 넘겼는가 (2) 우리 처리 로직(실패 제한, drain, 종료 연결)이 맞는가.
어느 시험도 실제 시간을 기다리거나 재지 않는다. 시계와 sleep은 주입한 가짜이고, 시간은 시험이 직접 흘린다."""
import threading
import types
from wsgiref.util import setup_testing_defaults

import model_lab.service.app as app
from model_lab.components import link_registry_serve as lrs
from tests.conftest import HANG_GUARD_SECONDS

RT, WT = "9f2c4e1ab7d03856" * 2, "5d8e1c7a0b3f2946" * 2


class FakeClock:
    """시험이 움직일 때만 흐르는 시계. sleep(초)은 시계만 앞으로 보낸다(실제로 기다리지 않는다)."""

    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def make_service(tmp_path, clock=None, **kw):
    clock = clock or FakeClock()
    return app.Service(tmp_path / "srv", {RT: "release-gate"}, {WT: "model-lab"}, log=lambda s: None, clock=clock,
                       utc_now=lambda: "2026-10-31T00:00:00Z", **kw)


# ---------- (1) 올바른 값을 넘겼는가 ----------
def test_make_server_passes_the_configured_limits_to_waitress(tmp_path, monkeypatch):
    seen = {}
    monkeypatch.setattr(lrs.waitress, "create_server", lambda wsgi, **kw: seen.update(kw, wsgi=wsgi))
    svc = make_service(tmp_path, socket_timeout=7.9, max_connections=5, max_body=123456)
    lrs.make_server(svc, "127.0.0.1", 0, threads=9)
    assert seen["threads"] == 9
    assert seen["connection_limit"] == 5 + 2          # waitress는 리스너와 트리거도 연결 수에 센다(ADR 0023)
    assert seen["channel_timeout"] == 7                # int(socket_timeout), 최소 1
    assert seen["cleanup_interval"] == 5               # min(5, int(timeout)), 최소 1
    assert seen["max_request_body_size"] == 123456
    assert seen["clear_untrusted_proxy_headers"] is True and seen["ident"] == "model-registry"
    lrs.make_server(make_service(tmp_path / "t", socket_timeout=0.2), "127.0.0.1", 0)
    assert seen["channel_timeout"] == 1 and seen["cleanup_interval"] == 1


def test_routes_declare_streaming_publish_with_the_body_limit_and_roles(tmp_path):
    svc = make_service(tmp_path, max_body=777)
    routes = {(r.method, r.pattern): r for r in lrs.make_routes(svc)}
    post = routes[("POST", "/v1/packages")]
    assert post.stream_body is True and post.max_body == 777 and post.roles == frozenset({"model-lab"})
    for key, r in routes.items():
        if key[0] == "GET":
            assert r.roles == frozenset({"release-gate", "model-lab"}), key


class FakeSrv:
    def __init__(self):
        self.effective_port = 4321
        self.ran = self.closed = False
        self.task_dispatcher = types.SimpleNamespace(shutdown=lambda: None)

    def run(self):
        self.ran = True

    def close(self):
        self.closed = True


def test_main_passes_environment_values_to_the_service_and_server(tmp_path, monkeypatch):
    captured, handlers = {}, {}

    def fake_make_server(svc, host, port, **kw):
        captured.update(svc=svc, host=host, port=port, kw=kw, srv=FakeSrv())
        return captured["srv"]

    monkeypatch.setattr(app, "make_server", fake_make_server)
    monkeypatch.setattr(app.signal, "signal", lambda sig, h: handlers.setdefault(sig, h))
    env = {"MODEL_REGISTRY_ROOT": str(tmp_path / "r"), "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + WT, "MODEL_REGISTRY_READ_TOKENS": "rg=" + RT,
           "MODEL_REGISTRY_HOST": "127.0.0.9", "MODEL_REGISTRY_PORT": "8123", "MODEL_REGISTRY_THREADS": "3", "MODEL_REGISTRY_MAX_BODY": "4096",
           "MODEL_REGISTRY_SOCKET_TIMEOUT": "11", "MODEL_REGISTRY_MAX_CONNECTIONS": "6", "MODEL_REGISTRY_MAX_PUBLISHES": "2",
           "MODEL_REGISTRY_AUTH_FAIL_LIMIT": "4", "MODEL_REGISTRY_MIN_FREE_GB": "0.5", "MODEL_REGISTRY_SHUTDOWN_GRACE": "3"}
    assert app.main(env) == 0
    svc = captured["svc"]
    assert (captured["host"], captured["port"], captured["kw"]) == ("127.0.0.9", 8123, {"threads": 3})
    assert (svc.max_body, svc.socket_timeout, svc.max_connections, svc.auth_fail_limit, svc.min_free_gb, svc.shutdown_grace) == (4096, 11.0, 6, 4, 0.5, 3.0)
    assert svc.publish_slots._value == 2
    assert captured["srv"].ran and captured["srv"].closed and not (tmp_path / "r" / ".tmp").exists()      # 끝나면 임시 폴더를 지운다
    # 종료 신호 처리기: 등록되어 있고, 부르면 설정한 유예(shutdown_grace)로 drain을 시작한다
    called, ev = {}, threading.Event()
    monkeypatch.setattr(app, "drain", lambda srv, grace, log=None: (called.update(srv=srv, grace=grace), ev.set()))
    handlers[app.signal.SIGTERM](app.signal.SIGTERM, None)
    assert ev.wait(HANG_GUARD_SECONDS)      # 매달림 감시: drain을 부르는 스레드가 시작되지 않을 때만 걸린다(정상은 즉시)
    assert called == {"srv": captured["srv"], "grace": 3.0}


def test_main_gives_the_service_the_real_clock(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(app, "make_server", lambda svc, host, port, **kw: captured.update(svc=svc) or FakeSrv())
    monkeypatch.setattr(app.signal, "signal", lambda sig, h: None)
    assert app.main({"MODEL_REGISTRY_ROOT": str(tmp_path / "r"), "MODEL_REGISTRY_WRITE_TOKENS": "model-lab=" + WT}) == 0
    assert captured["svc"].clock is app.time.monotonic
    assert len(captured["svc"].utc_now()) == len("2026-10-31T00:00:00Z") and captured["svc"].utc_now().endswith("Z")


# ---------- (2) 우리 처리 로직 ----------
def wsgi_call(wsgi, method, path, token=None, body=b""):
    env = {}
    setup_testing_defaults(env)
    env.update(REQUEST_METHOD=method, PATH_INFO=path, REMOTE_ADDR="10.0.0.1", CONTENT_LENGTH=str(len(body)))
    if token:
        env["HTTP_AUTHORIZATION"] = "Bearer " + token
    got = {}
    b"".join(wsgi(env, lambda status, headers: got.update(status=int(status.split()[0]))))
    return got["status"]


def test_repeated_bad_tokens_are_throttled_but_valid_tokens_always_pass(tmp_path):
    clock = FakeClock()
    svc = make_service(tmp_path, clock, auth_fail_limit=3, auth_fail_window=60.0)
    wsgi = lrs.make_app(svc)
    assert [wsgi_call(wsgi, "GET", "/v1/packages", "bad") for _ in range(3)] == [401, 401, 401]
    assert wsgi_call(wsgi, "GET", "/v1/packages", "bad") == 503 and svc.auth_blocked("10.0.0.1")        # 틀린 토큰은 제한된다
    assert wsgi_call(wsgi, "GET", "/v1/packages", RT) == 200 and wsgi_call(wsgi, "GET", "/v1/packages", WT) == 200   # 정상 토큰은 영향이 없다
    before = len(svc._fails["10.0.0.1"])
    assert wsgi_call(wsgi, "POST", "/v1/packages", RT, b"z") == 403                                      # 역할 부족 403은 실패 횟수에 넣지 않는다
    assert len(svc._fails["10.0.0.1"]) == before
    clock.sleep(61.0)                                                                                    # 시험이 시간을 흘린다: 창이 지나면 다시 401
    assert wsgi_call(wsgi, "GET", "/v1/packages", "bad") == 401
    assert not svc.auth_blocked("10.0.0.1")


class FakeChannel:
    """waitress 채널 흉내. busy_polls번째 pull_trigger에서 진행 중이던 요청이 끝난다(None이면 영원히 진행 중)."""

    def __init__(self, busy_polls=0):
        self.requests, self.request = ([object()] if busy_polls is None or busy_polls > 0 else []), None
        self.busy_polls, self.close_when_flushed, self.will_close = busy_polls, False, False


class FakeWaitress:
    def __init__(self, *channels):
        self.accepting = True
        self._map = {0: object(), **{i + 1: c for i, c in enumerate(channels)}}      # 0은 리스너(채널이 아님)
        self.polls = 0
        self.trigger = types.SimpleNamespace(del_channel=lambda: None)

    def pull_trigger(self):
        self.polls += 1
        for k, ch in list(self._map.items()):
            if isinstance(ch, FakeChannel):
                if ch.busy_polls is not None and ch.requests and self.polls >= ch.busy_polls:
                    ch.requests = []                       # 진행 중이던 요청이 끝났다
                if ch.close_when_flushed or ch.will_close:
                    del self._map[k]                       # 닫힌 연결은 사라진다

    def del_channel(self):
        pass


def drain(srv, grace, clock=None):
    clock = clock or FakeClock()
    lrs.drain(srv, grace, clock=clock, sleep=clock.sleep)      # 실제로 기다리지 않는다: sleep은 가짜 시계를 앞으로 보낸다
    return clock


def test_drain_closes_idle_connections_and_stops_accepting():
    idle = FakeChannel(0)
    srv = FakeWaitress(idle)
    drain(srv, 8.0)
    assert srv.accepting is False and idle.close_when_flushed is True and not idle.will_close


def test_drain_waits_for_a_request_in_flight_then_closes_it():
    busy = FakeChannel(busy_polls=3)
    srv = FakeWaitress(busy)
    clock = drain(srv, 8.0)
    assert srv.polls >= 3 and busy.requests == [] and busy.close_when_flushed is True and not busy.will_close
    assert clock.now < 1000.0 + 8.0              # 유예(주입한 시계로 8초)가 끝나기 전에 요청이 끝나 돌아왔다


def test_drain_cuts_connections_still_busy_when_the_grace_is_over():
    stuck = FakeChannel(busy_polls=None)          # 끝나지 않는 요청
    srv = FakeWaitress(stuck)
    clock = drain(srv, 8.0)
    assert stuck.will_close is True and clock.now >= 1000.0 + 8.0     # 가짜 시계가 유예를 넘긴 뒤에야 끊는다
