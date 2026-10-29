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
#    왜 기본값이 없나: 기준표(deploy lib.sh)와 어긋난 버전이 조용히 빌드되지 않게 한다. 이 이미지는 model-runtime·문진 스키마·turn-engine을 쓰지 않아 해당 키는 무시한다.
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

# 2. boundary-contracts wheel: 빌드 중 네트워크(SSH) 대신 dependency_sources 소스 트리에서 만든다(Dockerfile의 wheels 컨텍스트).
#    SOURCE_DATE_EPOCH 고정: wheel 안 시각이 달라 같은 요청이 다른 이미지가 되지 않게 한다(멱등, 같은 소스의 wheel 두 번이 바이트까지 같은 것을 확인).
WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
mkdir "$WORK/wheels"
SOURCE_DATE_EPOCH=0 "$PY" -m pip wheel -q --no-deps -w "$WORK/wheels" "$BC_SRC" >&2
WHL=$(basename "$(ls "$WORK"/wheels/boundary_contracts-*.whl | head -1)")
case "$WHL" in boundary_contracts-"${BC_VERSION#v}"-*) ;; *) echo "dependency_sources.boundary($WHL)가 versions.boundary($BC_VERSION)와 다르다" >&2; exit 2;; esac

# 3. 빌드
# 왜 --provenance=false: BuildKit이 붙이는 출처 증명(attestation)에 빌드마다 다른 시각·ID가 들어가 이미지 ID(인덱스 digest)가 매번 달라진다. 같은 요청이 같은 이미지가 되려면 끈다(실측: 끄기 전 3회 모두 다른 ID).
"$DOCKER" build -q --provenance=false -t "$IMAGE_TAG" --build-context "wheels=$WORK/wheels" --build-arg "BC_SPEC=/wheels/$WHL" \
  --build-arg "BC_VERSION=$BC_VERSION" --build-arg "REVISION=$REVISION" "$SOURCE_DIR" >&2

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
out = {"image": image, "image_id": image_id, "labels": labels}
validator("BuildImageResult").validate(out)
print(json.dumps(out, ensure_ascii=False, separators=(",", ":")))
PY
