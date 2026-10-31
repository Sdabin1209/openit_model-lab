#!/usr/bin/env bash
# 영역 빌드 진입점(계약 BuildImageRequest / BuildImageResult, boundary-contracts 2.5.0, U42, ADR 0024).
# 사용: 영역 리포 루트에서 `bash scripts/build-image.sh <요청 JSON 파일>`. 이미지: model-registry(I3 서버).
# 결과: 표준 출력 마지막 줄에 BuildImageResult JSON 한 줄. 진행 기록은 표준 오류. 환경 변수: DOCKER(기본 docker), PYTHON(요청 검증용, 기본 .venv/bin/python 다음 python3).
# 왜 이 모양: deploy가 Dockerfile 인자 이름과 산출 파일을 알면 구조 규칙 1을 어긴다. 인자·빌드 컨텍스트·라벨은 이 영역이 정한다.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
[ $# -eq 1 ] && [ -f "$1" ] || { echo "사용: bash scripts/build-image.sh <요청 JSON 파일>" >&2; exit 2; }
DOCKER=${DOCKER:-docker}
PY=${PYTHON:-}
[ -n "$PY" ] || { [ -x .venv/bin/python ] && PY=.venv/bin/python || PY=python3; }

# 1. 요청 검증(영역 경계 입력): 계약 validator. 이 영역이 쓰는 키(versions.boundary, dependency_sources.boundary)가 없으면 실패한다.
#    왜 기본값이 없나: 기준표(deploy lib.sh)와 어긋난 버전이 조용히 빌드되지 않게 한다. 이 이미지는 model-runtime·문진 스키마를 쓰지 않아 해당 키는 무시한다.
eval "$("$PY" - "$1" <<'PY'
import json, shlex, sys
from boundary_contracts import validator
req = json.load(open(sys.argv[1], encoding="utf-8"))
errs = sorted(validator("BuildImageRequest").iter_errors(req), key=lambda e: list(map(str, e.absolute_path)))
if errs:
    sys.exit("요청 위반: /" + "/".join(map(str, errs[0].absolute_path)) + ": " + errs[0].message[:160])
if "variant" in req:
    sys.exit("모르는 variant: 이 영역은 이미지를 하나만 낸다")
src = (req.get("dependency_sources") or {}).get("boundary")
if not src:
    sys.exit("dependency_sources.boundary가 필요하다")
for k, v in (("SOURCE_DIR", req["source_dir"]), ("REVISION", req["revision"]), ("BC_VERSION", req["versions"]["boundary"]),
             ("BC_SRC", src), ("IMAGE_TAG", req["image_tag"])):
    print(f"{k}={shlex.quote(v)}")
PY
)" || exit 2
[ -f "$SOURCE_DIR/Dockerfile" ] || { echo "source_dir에 Dockerfile이 없다" >&2; exit 2; }

# 노출 선언(U47): 리포의 deploy-exposure.json을 계약 Exposure로 검증해 이미지 라벨 io.openit.exposure(JSON 문자열)로 단다.
# 왜 정렬·공백 없는 JSON: 같은 표는 같은 라벨 값이어야 같은 요청이 같은 이미지가 된다(멱등).
EXPOSURE=$("$PY" - "$SOURCE_DIR/deploy-exposure.json" <<'PY'
import json, sys
from boundary_contracts import validator
doc = json.load(open(sys.argv[1], encoding="utf-8"))
validator("Exposure").validate(doc)
print(json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
PY
) || { echo "deploy-exposure.json이 계약 Exposure를 어긴다" >&2; exit 2; }

# 2. boundary-contracts wheel: 빌드 중 네트워크(SSH) 대신 dependency_sources 소스 트리에서 만든다(Dockerfile의 wheels 컨텍스트).
#    SOURCE_DATE_EPOCH 고정: wheel 안 시각이 달라 같은 요청이 다른 이미지가 되지 않게 한다(멱등, 같은 소스의 wheel 두 번이 바이트까지 같은 것을 확인).
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/wheels"
SOURCE_DATE_EPOCH=0 "$PY" -m pip wheel -q --no-deps -w "$WORK/wheels" "$BC_SRC" >&2
WHL=$(basename "$(ls "$WORK"/wheels/boundary_contracts-*.whl | head -1)")
# link(D118) wheel: requirements-service.txt가 고정한 태그에서 만든다(요청 스키마에 link 소스 자리가 없다. 호스트의 git+ssh 접근을 쓴다). 의존은 이미 있는 계약 wheel이라 --no-deps.
LINK_SPEC=$(sed -n 's/^openit-link @ \(.*\)$/\1/p' "$SOURCE_DIR/requirements-service.txt" | head -1)
[ -n "$LINK_SPEC" ] || { echo "requirements-service.txt에서 openit-link 고정을 읽지 못했다" >&2; exit 2; }
SOURCE_DATE_EPOCH=0 "$PY" -m pip wheel -q --no-deps -w "$WORK/wheels" "openit-link @ $LINK_SPEC" >&2
LINK_WHL=$(basename "$(ls "$WORK"/wheels/openit_link-*.whl | head -1)")
# U50 라벨 정직성: 라벨 값은 요청 문자열이 아니라 실제로 설치할 wheel의 버전에서 만든다. 요청·소스·이 영역의 고정 버전이 하나라도 다르면 이미지를 만들지 않고 실패한다.
WHL_VERSION="v$(printf '%s' "$WHL" | sed -n 's/^boundary_contracts-\([0-9][0-9.]*\)-.*/\1/p')"
PINNED=$(sed -n 's/^boundary-contracts @ .*@\(v[0-9][0-9.]*\)$/\1/p' "$SOURCE_DIR/requirements-service.txt" | head -1)
[ -n "$PINNED" ] || { echo "requirements-service.txt에서 boundary-contracts 고정 버전을 읽지 못했다" >&2; exit 2; }
[ "$BC_VERSION" = "$PINNED" ] || { echo "요청 versions.boundary($BC_VERSION)가 이 영역의 고정 버전($PINNED, requirements-service.txt)과 다르다. 라벨을 찍지 않는다" >&2; exit 2; }
[ "$WHL_VERSION" = "$BC_VERSION" ] || { echo "dependency_sources.boundary의 실제 버전($WHL_VERSION)이 요청 versions.boundary($BC_VERSION)와 다르다. 라벨을 찍지 않는다" >&2; exit 2; }

# 3. 빌드
# 왜 --provenance=false: BuildKit이 붙이는 출처 증명(attestation)에 빌드마다 다른 시각·ID가 들어가 이미지 ID(인덱스 digest)가 매번 달라진다. 같은 요청이 같은 이미지가 되려면 끈다(실측: 끄기 전 3회 모두 다른 ID).
"$DOCKER" build -q --provenance=false --label "io.openit.exposure=$EXPOSURE" -t "$IMAGE_TAG" --build-context "wheels=$WORK/wheels" --build-arg "BC_SPEC=/wheels/$WHL" --build-arg "LINK_SPEC=/wheels/$LINK_WHL" \
  --build-arg "BC_VERSION=$WHL_VERSION" --build-arg "REVISION=$REVISION" "$SOURCE_DIR" >&2

# 설치된 것 확인: 이미지 안에서 실제로 읽은 boundary-contracts 버전이 라벨과 같아야 한다(다르면 이미지를 지우고 실패).
INSTALLED=$("$DOCKER" run --rm --entrypoint python "$IMAGE_TAG" -c "import importlib.metadata as m; print(m.version('boundary-contracts'))")
[ "v$INSTALLED" = "$WHL_VERSION" ] || { "$DOCKER" rmi -f "$IMAGE_TAG" >/dev/null 2>&1 || true; echo "이미지에 설치된 boundary-contracts(v$INSTALLED)가 라벨($WHL_VERSION)과 다르다" >&2; exit 1; }

# 4. 결과: 이미지에 실제로 붙은 라벨을 docker inspect로 읽어 계약으로 검증하고 요청 값과 대조한다.
IMAGE_ID=$("$DOCKER" image inspect -f '{{.Id}}' "$IMAGE_TAG")
LABELS=$("$DOCKER" image inspect -f '{{json .Config.Labels}}' "$IMAGE_TAG")
"$PY" - "$IMAGE_TAG" "$IMAGE_ID" "$LABELS" "$REVISION" "$BC_VERSION" <<'PY'
import json, sys
from boundary_contracts import validator
image, image_id, labels, revision, bc = sys.argv[1:]
labels = {k: v for k, v in json.loads(labels).items() if k.startswith("io.openit.")}
if labels.get("io.openit.revision") != revision or labels.get("io.openit.contracts.boundary") != bc:
    sys.exit("이미지 라벨이 요청 값과 다르다")
validator("ImageLabels").validate(labels)      # 내보내기 전에 이미지 라벨을 계약 ImageLabels로 직접 검증한다(R7). BuildImageResult도 같은 스키마를 가리키지만 위반 위치를 라벨로 알리려고 먼저 본다
out = {"image": image, "image_id": image_id, "labels": labels}
validator("BuildImageResult").validate(out)
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
PY
