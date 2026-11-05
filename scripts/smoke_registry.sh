#!/bin/sh
# deploy 와 같은 실행 조건으로 띄운다(읽기 전용 루트, /tmp tmpfs, cap_drop ALL, no-new-privileges, 쓸 수 있는 경로는 deploy compose 와 같게): 2026-11-06 E2E 기동에서야 드러난 쓰기 경로 결함을 스모크에서 잡는다
# 이미지를 빌드해 띄우고 /healthz, /readyz, 게시(POST)와 조회를 확인한다. 고정 포트를 쓰지 않는다(Docker가 배정).
# 의존 wheel은 태그에서 만든 것을 vendor/wheels에서 쓴다(ssh 없이). 태그 설치 경로는 Dockerfile 머리말.
set -eu
cd "$(dirname "$0")/.."
# docker 환경 한 곳(사용자 ~/.docker 불변): 호출자가 준 DOCKER_CONFIG는 그대로, 없으면 리포 안 빈 설정(scripts/docker_env.py)
eval "$(python3 scripts/docker_env.py --sh)"
TAG="model-lab-registry:smoke-$$"
NAME="model-lab-registry-smoke-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$NAME-data" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
mkdir -p vendor/wheels
# requirements-service.txt가 고정한 태그에서 wheel을 만든다(pip wheel --no-deps, 이웃 폴더 불사용). 같은 버전 wheel이 이미 있으면 다시 만들지 않는다.
# 왜: 이미지 빌드는 ssh 없이 이 wheel로 설치한다(Dockerfile 머리말). 다른 버전 wheel이 같이 있어도 섞이지 않게 고정 버전만 고른다.
SPEC=$(grep '^boundary-contracts' requirements-service.txt | head -1)
BC_VER=$(printf '%s' "$SPEC" | sed -n 's/.*openit_boundary-contracts.git@v\([0-9.]*\).*/\1/p')
[ -n "$BC_VER" ] || { echo "requirements-service.txt에서 boundary-contracts 태그를 읽지 못했다" >&2; exit 1; }
if ! ls vendor/wheels/boundary_contracts-"$BC_VER"-*.whl >/dev/null 2>&1; then
  PIP=.venv/bin/pip; [ -x "$PIP" ] || PIP="python3 -m pip"
  $PIP wheel --no-deps -q -w vendor/wheels "$SPEC"
fi
WHL=$(ls vendor/wheels/boundary_contracts-"$BC_VER"-*.whl | head -1)
PIP=.venv/bin/pip; [ -x "$PIP" ] || PIP="python3 -m pip"
LINK_SPEC=$(sed -n 's/^openit-link @ \(.*\)$/\1/p' requirements-service.txt | head -1)
LINK_VER=$(printf '%s' "$LINK_SPEC" | sed -n 's/.*@v\([0-9.]*\)$/\1/p')
[ -n "$LINK_VER" ] || { echo "requirements-service.txt에서 openit-link 태그를 읽지 못했다" >&2; exit 1; }
if ! ls vendor/wheels/openit_link-"$LINK_VER"-*.whl >/dev/null 2>&1; then
  $PIP wheel --no-deps -q -w vendor/wheels "openit-link @ $LINK_SPEC"
fi
LINK_WHL=$(ls vendor/wheels/openit_link-"$LINK_VER"-*.whl | head -1)
# 왜: 커밋을 모르면 라벨 revision이 거짓이 된다(계약 ImageLabels는 hex만 받는다). 'unknown'으로 대신하지 않고 실패한다.
REV=$(git rev-parse --short HEAD) || { echo "git 커밋을 읽지 못해 revision 라벨을 정할 수 없다" >&2; exit 1; }
docker build -q --build-context wheels=vendor/wheels --build-arg "BC_SPEC=/wheels/$(basename "$WHL")" --build-arg "LINK_SPEC=/wheels/$(basename "$LINK_WHL")" --build-arg "BC_VERSION=v$BC_VER" \
  --build-arg "REVISION=$REV" -t "$TAG" .
docker volume create "$NAME-data" >/dev/null
# 토큰은 환경 변수가 아니라 파일로 준다(D84, NAME_FILE). 컨테이너 사용자(uid 10001)가 읽도록 0644, 끝나면 지운다.
SECRETS=$(mktemp -d); trap 'rm -rf "$SECRETS"; docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$NAME-data" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
# 서비스는 짧은 토큰이면 시작을 거부한다(32자 이상). 스모크용 무작위 토큰을 만든다(이 값은 시험 전용이고 끝나면 버려진다)
RT=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n'); WT=$(od -An -N24 -tx1 /dev/urandom | tr -d ' \n')
printf 'release-gate=%s\n' "$RT" > "$SECRETS/read"; printf 'model-lab=%s\n' "$WT" > "$SECRETS/write"; chmod 755 "$SECRETS"; chmod 644 "$SECRETS"/*
docker run -d --name "$NAME" --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges:true -p 127.0.0.1::8080 -v "$NAME-data":/data \
  -v "$SECRETS":/run/secrets:ro \
  -e MODEL_REGISTRY_READ_TOKENS_FILE=/run/secrets/read -e MODEL_REGISTRY_WRITE_TOKENS_FILE=/run/secrets/write "$TAG" >/dev/null
PORT=$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')
URL="http://127.0.0.1:$PORT"
for i in $(seq 1 30); do curl -fsS "$URL/healthz" >/dev/null 2>&1 && break; sleep 1; done
echo "healthz: $(curl -fsS "$URL/healthz")"
echo "readyz: $(curl -fsS "$URL/readyz")"
echo "user: $(docker exec "$NAME" id -u)"
# 잡이 이미지에서 돈다: restic이 있고, 입력이 없으면 마지막 줄이 BackupJobResult(failed)이고 종료 코드 1이다(ADR 0035)
docker exec "$NAME" restic version >/dev/null
JOB_OUT=$(docker exec "$NAME" python -m model_lab.service backup 2>/dev/null) && { echo "백업 잡이 입력 없이 성공했다" >&2; exit 1; } || [ $? -eq 1 ]
printf '%s' "$JOB_OUT" | tail -n 1 | grep -q '"status": "failed"'
echo "labels: $(docker inspect -f '{{json .Config.Labels}}' "$TAG")"
# RUNTIME R10.11: /metrics는 인증 없이 200 Prometheus 텍스트
[ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/metrics")" = 200 ]
curl -fsS "$URL/metrics" | grep -q model_registry_http_requests_total
# 읽기 토큰 없이는 401, 있으면 200
[ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/v1/packages")" = 401 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $RT" "$URL/v1/packages")" = 200 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H "Authorization: Bearer $RT" --data-binary x "$URL/v1/packages")" = 403 ]
# 게시와 조회(클라이언트 대역으로)
.venv/bin/python scripts/smoke_client.py "$URL" "$WT" "$RT"
# 비밀 값이 로그에 없다
# 왜 `! 명령 | grep`로 쓰지 않나: set -e는 `!`로 뒤집은 파이프라인의 실패로 끝나지 않는다(POSIX 2.8.1, bash set -e의 예외). 그러면 로그에 토큰이 있어도 통과한다.
# 그래서 로그를 변수에 받아 if로 판정한다(grep -q가 먼저 끝나 파이프가 SIGPIPE로 깨지는 것도 피한다).
LOGS=$(docker logs "$NAME" 2>&1)
if printf '%s' "$LOGS" | grep -q -e "$RT" -e "$WT"; then echo "FAIL: 로그에 비밀 노출" >&2; exit 1; fi
echo SMOKE_OK
