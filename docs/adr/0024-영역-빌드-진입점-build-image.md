# 0024. 이미지 빌드는 계약 진입점 `scripts/build-image.sh`로 한다

## 상태
채택 (2026-10-30, 헤드 브리프 U42, 계약 boundary-contracts v2.5.0 `BuildImageRequest` / `BuildImageResult`, OPEN_GAPS U42).

## 맥락
deploy의 `build-images.sh`가 이 영역의 Dockerfile 인자 이름(`BC_SPEC`, `BC_VERSION`, `REVISION`), 빌드 컨텍스트에 만들어 두는 폴더(`vendor-wheels`, `bc-src`), wheel을 만드는 방법을 직접 알고 있었다. 영역이 서로의 구조를 모른다는 규칙(D104 규칙 1)에 어긋난다.

## 결정
- `bash scripts/build-image.sh <요청 JSON>`이 진입점이다. 요청은 계약 `BuildImageRequest`로 검증하고(모르는 키·필수 키 없음은 종료 2), 결과는 표준 출력 마지막 줄의 `BuildImageResult` JSON 한 줄이다. 진행 기록은 표준 오류.
- 이 영역이 쓰는 키는 `versions.boundary`와 `dependency_sources.boundary`다. 없으면 실패하고 기본값은 영역 안에 두지 않는다(기준표와 어긋난 버전이 조용히 빌드되지 않게). model-registry 이미지는 model-runtime, 문진 스키마, turn-engine을 쓰지 않으므로 해당 키는 무시하고 라벨은 `n/a`다. `variant`는 모르는 값이라 실패한다.
- 빌드 중 네트워크(SSH) 대신 `dependency_sources.boundary` 소스 트리에서 wheel을 만들어 Dockerfile의 `wheels` 컨텍스트로 준다. wheel 파일 이름의 버전이 `versions.boundary`와 다르면 실패한다.
- 결과의 라벨은 `docker image inspect`로 읽은 실제 라벨이고, 계약 `ImageLabels`(`BuildImageResult` 안)로 검증하며 `revision`·`contracts.boundary`는 요청 값과 대조한다.
- 같은 요청이 같은 이미지가 되도록 wheel 빌드에 `SOURCE_DATE_EPOCH=0`을 준다와 BuildKit 출처 증명을 끈다(`--provenance=false`; 끄기 전에는 같은 요청 3회가 모두 다른 `image_id`였다). 시험이 두 번 빌드해 `image_id`가 같은 것을 확인한다.

## 왜
계약이 진입점을 정했다(U42). 인자 이름과 산출 파일은 영역이 정하고 deploy는 요청과 결과 스키마만 안다. 이미지 안은 바뀌지 않는다(같은 Dockerfile).

## 검토한 대안과 버리는 이유
- deploy의 옛 블록 유지: 영역 구조를 deploy가 아는 상태가 계속된다.
- Python으로 구현: 요청 검증 외에는 docker 호출뿐이라 bash가 짧다. 검증은 계약 validator를 부르는 Python 조각을 쓴다.

## 결과
- 얻는 것: deploy가 이 영역의 Dockerfile 구조를 몰라도 된다. 시험 `tests/test_build_image.py`(요청 검증 6건, 실제 빌드 1건).
- 감수: 요청 검증과 wheel 빌드에 `boundary_contracts`와 `pip`가 있는 Python(`PYTHON`, 기본 `.venv/bin/python`, 없으면 `python3`)이 필요하다. 옛 `scripts/smoke_registry.sh`는 자기 wheel 로직을 그대로 쓴다(개발용 스모크).

## 되돌리는 조건
계약의 진입점 형식이 바뀌면 따른다.
