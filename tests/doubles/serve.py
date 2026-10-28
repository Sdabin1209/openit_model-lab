"""model-registry 서버를 스레드에서 띄우는 시험 도우미. 서버 구현이 바뀌어도 시험은 이 모양만 안다."""
import threading

from model_lab.service.app import drain, make_server


def serve(svc, host="127.0.0.1"):
    """(srv, port). 서버는 daemon 스레드에서 돈다."""
    srv = make_server(svc, host, 0)
    threading.Thread(target=lambda: (srv.run(), srv.close()), daemon=True).start()
    return srv, srv.effective_port


def stop(srv):
    drain(srv, 0.0)       # 연결을 닫고 srv.run()이 끝나게 한다(소켓은 run이 끝난 스레드가 닫는다)
