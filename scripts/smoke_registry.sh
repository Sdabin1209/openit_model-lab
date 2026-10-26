#!/bin/sh
# 이미지를 빌드해 띄우고 /healthz, /readyz, 게시(POST)와 조회를 확인한다. 고정 포트를 쓰지 않는다(Docker가 배정).
# 의존 wheel은 태그에서 만든 것을 vendor/wheels에서 쓴다(ssh 없이). 태그 설치 경로는 Dockerfile 머리말.
set -eu
cd "$(dirname "$0")/.."
TAG="model-lab-registry:smoke-$$"
NAME="model-lab-registry-smoke-$$"
trap 'docker rm -f "$NAME" >/dev/null 2>&1 || true; docker volume rm "$NAME-data" >/dev/null 2>&1 || true; docker rmi "$TAG" >/dev/null 2>&1 || true' EXIT
mkdir -p vendor/wheels
# requirements-service.txt가 고정한 태그(vX.Y.Z)의 wheel을 쓴다(다른 버전 wheel이 같이 있어도 섞이지 않는다).
BC_VER=$(sed -n 's/.*openit_boundary-contracts.git@v\([0-9.]*\).*/\1/p' requirements-service.txt | head -1)
WHL=$(ls vendor/wheels/boundary_contracts-"$BC_VER"-*.whl | head -1)
docker build -q --build-context wheels=vendor/wheels --build-arg "BC_SPEC=/wheels/$(basename "$WHL")" \
  --build-arg "REVISION=$(git rev-parse --short HEAD 2>/dev/null || echo unknown)" -t "$TAG" .
docker volume create "$NAME-data" >/dev/null
docker run -d --name "$NAME" -p 127.0.0.1::8080 -v "$NAME-data":/data \
  -e MODEL_REGISTRY_READ_TOKENS=release-gate=read-secret -e MODEL_REGISTRY_WRITE_TOKENS=model-lab=write-secret "$TAG" >/dev/null
PORT=$(docker port "$NAME" 8080/tcp | head -1 | sed 's/.*://')
URL="http://127.0.0.1:$PORT"
for i in $(seq 1 30); do curl -fsS "$URL/healthz" >/dev/null 2>&1 && break; sleep 1; done
echo "healthz: $(curl -fsS "$URL/healthz")"
echo "readyz: $(curl -fsS "$URL/readyz")"
echo "user: $(docker exec "$NAME" id -u)"
echo "labels: $(docker inspect -f '{{json .Config.Labels}}' "$TAG")"
# 읽기 토큰 없이는 403, 있으면 200
[ "$(curl -s -o /dev/null -w '%{http_code}' "$URL/v1/packages")" = 403 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Authorization: Bearer read-secret' "$URL/v1/packages")" = 200 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'Authorization: Bearer read-secret' --data-binary x "$URL/v1/packages")" = 403 ]
# 게시와 조회(클라이언트 대역으로)
.venv/bin/python scripts/smoke_client.py "$URL" write-secret read-secret
echo SMOKE_OK
