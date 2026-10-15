# model-lab 경계

상태: 구현됨(0.1.0). 정본은 `../boundary-contracts`의 `INTERFACES.md`·`CONVENTIONS.md`다.

## 소유 저장소
모델 레지스트리(불변 패키지). I3 `ModelRegistry`. 쓰기는 이 영역만 한다. 구현: `model_lab.registry.FileModelRegistry`(디스크), `MemoryModelRegistry`(메모리). 둘 다 `boundary_contracts.sdk.ModelRegistry` Protocol을 만족하고 `tests/test_registry.py`의 같은 적합성 시험을 통과한다(오류 코드·멱등·해시 검증 포함).

## 제공 (I3)
`publish`, `get_manifest`, `list_ids`, `list_versions`(오래된 것부터), `open_adapter`(해시 검증), `get_eval_report`. 생산 계약: `ModelPackageManifest`(역할마다 하나, 서빙 설정은 역할별). 평가 리포트는 `eval_report_ref`로 가져오는 JSON 바이트이고 형식은 이 영역이 정한다(`docs/design.md` 6절). 게이트는 해석하지 않는다.
읽기 전용 SDK를 따로 만들지 않았다: 소비자(release-gate)는 같은 Protocol의 읽기 메서드를 쓴다. 읽기 토큰·원격 구현은 게이트와 배포 방식이 정해진 뒤 [미정].

## 소비
| 대상 | 방법 | 버전 |
| --- | --- | --- |
| 문진 스키마 | `schema_dir`(`.../schema/schemas`) 경로. `CONTRACT_VERSION`이 데이터셋의 `runtime_contract_version`과 다르면 `UNSUPPORTED_VERSION` | 정수 1 |
| boundary-contracts | `canonical`(모든 해시), `validator`(쓰기 전 검증), `sdk` Protocol, `BoundaryError` | v1.1.0 |
| turn-engine | 계보 기록(`ENGINE_VERSION`). 평가의 프로토콜 경로 바깥에서 직접 호출하지 않는다 | v0.2.0 |
| model-runtime | `render_prompt`, `load`, `parse_raw/generate_raw/parse/generate`, `validate_serving_config`, `serving_config_hash`, `RUNTIME_VERSION` | v0.3.0 |
| I2 데이터셋 읽기 | `DatasetStore` Protocol(`get_manifest`, `list_versions`, `open_file`)만 사용. 실제 SDK 진입점은 CR-L004 | 경계 계약 1.1 |
| I1, I4, I7 | 이 단계에서 쓰지 않음 | |

의존은 Git 태그로 고정한다(`requirements.txt`). 개발은 `pip install -e ../boundary-contracts ../turn-engine ../model-runtime`.

## 경계 오류
경계 호출(I2·I3)의 실패는 `BoundaryError`다(`NOT_FOUND`, `INVALID_RECORD`, `CONFLICT`, `INTEGRITY_ERROR`, `UNSUPPORTED_VERSION`…). 재시도는 `UNAVAILABLE`일 때만이며 `run-once`가 종료 코드 3으로 알린다. model-runtime의 예외(`ServingConfigMismatch` 등)는 `BoundaryError`가 아니라 설정·환경 오류로 그대로 전달된다. 파이프라인 내부 오류는 `PipelineError`(종료 코드 2).

## 장애 영향
- 이 영역이 멈추면 새 모델 패키지가 생기지 않을 뿐 서비스에는 영향이 없다. 서비스는 릴리스(I4)만 읽고 레지스트리를 읽지 않는다.
- 게시는 매니페스트를 마지막에 쓰므로 중단되어도 반쯤 게시된 패키지는 보이지 않는다. 다시 실행하면 이어서 끝낸다.
- 레지스트리 파일이 깨지면 읽기가 `INTEGRITY_ERROR`로 실패한다(변조를 조용히 통과시키지 않는다).
- I2가 멈추면 `run-once`는 종료 코드 3(`UNAVAILABLE`)이고 상태는 바뀌지 않는다.

## 하지 않는 것
릴리스 승인(게이트), 통과 임계치·지표 선정(O11, 사용자). 보고서에 합격 여부를 쓰지 않는다.
