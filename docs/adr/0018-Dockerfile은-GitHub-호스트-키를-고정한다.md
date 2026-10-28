# 0018. Dockerfile은 GitHub 공개 SSH 호스트 키를 고정한다 (처음 본 키를 신뢰하지 않는다)

## 상태
채택 (2026-10-29, 표준성 점검 H19, 헤드 결정표 D106 초안).

## 맥락
빌드 단계는 `ssh-keyscan github.com >> known_hosts || true`로 호스트 키를 얻었다. 빌드 때 네트워크 경로의 공격자가 키를 바꿔 끼우면 그대로 신뢰하고(TOFU), 스캔 실패는 `|| true`로 삼켜져 이후 단계에서 모호하게 실패했다. 이 단계의 SSH는 비공개 태그 의존(boundary-contracts 등)을 받는 데 쓰인다.

## 결정
`docker/github_known_hosts`에 GitHub가 공개한 세 키(ed25519, ecdsa-nistp256, rsa)를 두고 `COPY`한다. 키를 얻은 곳은 `https://api.github.com/meta`의 `ssh_keys`다. 같은 날 `ssh-keyscan`과 `ssh-keygen -lf`로 본 지문 세 개가 meta의 `ssh_key_fingerprints`와 일치하는 것을 확인했다(2026-10-29, 두 경로가 서로 독립). `tests/test_dockerfile_hostkeys.py`가 파일의 키 지문을 공개 지문과 대조하고 Dockerfile에 키 스캔이 없음을 확인한다.

## 왜
- SSH 호스트 키 검증의 요점은 서버의 키를 사전에 알려진 값과 대조하는 것이다. GitHub는 지문을 문서와 meta API로 공개한다.
- 실패를 삼키지 않는다: 키가 없거나 어긋나면 `ssh`가 연결을 거부한다.

## 검토한 대안과 버리는 이유
- 태그 소스를 SSH 없이 받기(HTTPS): 공개 저장소가 아니라 인증 수단이 별도로 필요하고, 워크스페이스 규칙의 설치 명령이 ssh이다. 이번 범위를 넘는다.
- 빌드 때 meta API로 내려받기: 그 호출이 다시 TLS 신뢰에 기대고 빌드가 네트워크 상태에 묶인다. 고정이 더 단순하다.

## 결과
얻는 것: 중간자 방어, 실패가 보임. 감수하는 것: GitHub가 호스트 키를 교체하면(공지됨) 파일과 시험의 지문을 갱신해야 한다. 키가 바뀐 때는 빌드가 실패하므로 조용히 틀리지 않는다. 확인하지 못한 것: 이 변경 뒤 이미지 전체 빌드와 `scripts/smoke_registry.sh`는 통과하지 못했다. 이 PC의 `vendor/wheels`에 `requirements-service.txt`가 요구하는 boundary-contracts 2.1.1 wheel이 없어 pip 설치 단계에서 실패했다(이번 변경 이전부터의 전제). 호스트 키를 복사하는 단계(`COPY`)는 그 앞이고 통과했다.

## 되돌리는 조건
태그 소스를 SSH 없이 받을 수 있게 되면 이 파일과 openssh-client를 없앤다.

---
**2026-10-29 후속 확인**: `scripts/smoke_registry.sh`가 `requirements-service.txt`의 태그에서 `pip wheel --no-deps`로 wheel을 만들도록 고쳤다(이웃 폴더 불사용). 고친 뒤 이 PC(Linux, Docker)에서 끝까지 통과했다: 고정 호스트 키 `COPY`를 거친 이미지 빌드, `healthz: {"status": "ok"}`, `readyz: {"status": "ready"}`, 비root `user: 10001`, 라벨 `io.openit.contracts.boundary` v2.1.1, 게시·조회 클라이언트 `client ok`, 마지막 줄 `SMOKE_OK`(23초). 한 환경에서만 확인했다. 위 "확인하지 못한 것" 문단은 이 확인으로 해소됐다.
