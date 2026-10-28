# 0016. 기반 모델의 실제 커밋과 캐시 유무는 huggingface_hub 공개 API로 읽는다

## 상태
채택 (2026-10-29, 표준성 점검 T24, 헤드 결정표 D106 초안).

## 맥락
`resolved_revision`은 transformers의 비공개 속성 `config._commit_hash`를 읽고, 없으면 `HF_HOME/hub/models--*/refs/*`를 직접 열었다. `train-doctor`는 `models--*` 디렉터리가 있으면 캐시가 있다고 했다(받은 파일이 없어도 OK). `HF_HUB_CACHE`는 doctor만 존중하고 `resolved_revision`은 무시했다.

## 결정
`model_lab/hf_cache.py`가 `huggingface_hub.snapshot_download(..., local_files_only=True)`의 반환 경로를 쓴다. 경로의 마지막 요소가 커밋 해시이고(HF 캐시 문서의 `snapshots/<commit_hash>/`), 스냅샷에 파일이 하나도 없으면 "없음"이다. `huggingface_hub`가 없으면 doctor는 WARN("확인하지 못했다"), 학습기는 요청한 revision을 기록한다.

## 왜
- 공개 API는 라이브러리가 호환성을 약속하는 면이고 비공개 속성과 디렉터리 배치는 아니다. 캐시 위치 규칙(`HF_HUB_CACHE`, `HF_HOME`)도 라이브러리가 해석한다.
- 헤드 결정표(D106)가 지정한 공개 API 사용.

## 검토한 대안과 버리는 이유
- `HfApi().model_info(id, revision).sha`: 원격의 최신 커밋을 묻는 것이라 네트워크가 필요하고, 실제로 학습에 쓴 로컬 스냅샷과 다를 수 있다. 오프라인 GPU 호스트에도 맞지 않는다.
- 현행 유지: 위 두 불일치를 되풀이한다.

## 결과
얻는 것: 환경 변수와 캐시 규칙을 라이브러리와 같게 읽음, 빈 캐시 디렉터리를 OK로 보지 않음. 감수하는 것: `snapshot_download`가 같은 `revision`의 참조(refs)를 해석하므로 refs가 없는 캐시는 "없음"이 된다. 시험: `tests/test_hf_cache.py`(가짜 캐시 배치로 확인, 실제 모델 내려받기 없음).

## 되돌리는 조건
`huggingface_hub`가 오프라인 조회 공개 API를 바꾸면 이 모듈 한 곳만 고친다.
