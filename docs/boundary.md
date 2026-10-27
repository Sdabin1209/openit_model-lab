# model-lab 경계

상태: 구현됨(0.1.0). 2.0.0 이전 중(헤드 지시서 `head/docs/migration-2.0.0.md`): 계약 v2.0.2 의존(`requirements.txt`), 변환기·패키징(system_prompt)까지 반영. 파이프라인의 변환·평가·회귀는 model-runtime v0.7.3 / turn-engine v0.4.0을 쓴다(`requirements.txt`; 이력은 `docs/STATUS.md` 10차, `CHANGELOG.md` 0.2.2). 학습기는 정본 결정 대기. 정본은 `../boundary-contracts`의 `INTERFACES.md`·`CONVENTIONS.md`다.

## 소유 저장소
모델 레지스트리(불변 패키지). I3 `ModelRegistry`. 쓰기는 이 영역만 한다. 구현: `model_lab.registry.FileModelRegistry`(디스크), `MemoryModelRegistry`(메모리). 둘 다 `boundary_contracts.sdk.ModelRegistry` Protocol을 만족하고 `tests/test_registry.py`의 같은 적합성 시험을 통과한다(오류 코드·멱등·해시 검증 포함).

## 제공 (I3)
`publish`, `get_manifest`, `list_ids`, `list_versions`(오래된 것부터), `open_adapter`(해시 검증), `get_eval_report`. 생산 계약: `ModelPackageManifest`(역할마다 하나, 서빙 설정은 역할별). 2.0.0부터 `system_prompt{path, sha256}`가 필수이고 파일은 어댑터 디렉터리 안에 있다(`adapter_hash`에 포함, D75). `publish`는 파일이 있고 해시가 맞는지 확인한다(아니면 `INTEGRITY_ERROR`). 프롬프트의 출처는 설정의 `roles.<역할>.system_prompt_file`이다[제안, CR-L007]. 평가 리포트는 `eval_report_ref`(`eval-<package_id>.<version>`)로 가져오는 바이트이며 `EvaluationReport`(boundary-contracts v2.0.2, 지표 체계, 합격 판정·임계치 없음)다(`docs/design.md` 6-1). 이전의 자세한 보고서는 `ext."model-lab".detail`에 있다.
읽기 전용 SDK를 따로 만들지 않았다: 소비자(release-gate)는 같은 Protocol의 읽기 메서드를 쓴다. 읽기 토큰·원격 구현은 게이트와 배포 방식이 정해진 뒤 [미정].

## 서비스 형태 (D49): model-registry
같은 I3를 HTTP로 낸다(`../boundary-contracts/HTTP.md` I3 절): `GET /v1/packages`, `/v1/packages/{id}/versions`, `/v1/packages/{id}/versions/{version}`, `.../adapter`(tar), `/v1/eval-reports/{ref}`, `POST /v1/packages`(tar: `manifest.json`, `adapter/…`, `eval_report`; 201 새로 / 200 같은 내용 / 409 다른 내용). 읽기 토큰(`MODEL_REGISTRY_READ_TOKENS`, 예: `release-gate`)과 쓰기 토큰(`MODEL_REGISTRY_WRITE_TOKENS`, 역할 `model-lab`만)을 분리하고, 둘 다 `boundary_contracts.read_secret`로 읽는다(`…_FILE` 경로 우선, 없으면 환경 변수, D84). 학습 서버가 만든 어댑터를 패키지로 얹는 길은 `docs/runbook.md` 맨 끝. 읽기 토큰의 쓰기 호출은 403. 어댑터는 서버가 해시를 검증한 뒤에만 내고 클라이언트도 다시 계산한다. 상세는 `docs/runbook.md` 맨 위.
클라이언트는 `boundary_contracts.clients.HttpModelRegistry`(v1.2.0)다. `tests/test_service.py::test_i3_server_conformance_with_real_client`가 서버 적합성 시험이다(게시 tar -> 조회 -> 어댑터 내려받기 -> 해시 검증, 200/201/409/403).

## 소비
| 대상 | 방법 | 버전 |
| --- | --- | --- |
| 문진 스키마 | `schema_dir`(`.../schema/schemas`) 경로. `CONTRACT_VERSION`이 데이터셋의 `runtime_contract_version`과 다르면 `UNSUPPORTED_VERSION` | 정수 1 |
| boundary-contracts | `canonical`(모든 해시), `validator`(쓰기 전 검증), `sdk` Protocol, `BoundaryError`. 데이터셋 샘플은 `ActionExtractionSample`(LORA_A)·`ResponseSample`(LORA_B) | v2.0.2 |
| turn-engine | 계보 기록(`ENGINE_VERSION`). 평가에서 `validate_extraction`은 쓰지 않았다(제안만) | v0.4.0 |
| model-runtime | `render_prompt`(system_prompt 인자), `load`(system_prompts 인자), `extract_raw/respond_raw/respond`, `validate_serving_config`, `serving_config_hash`, `RUNTIME_VERSION`. 서빙 설정은 prompt-4·protect-2 | v0.7.0 |
| I2 데이터셋 읽기 | `DatasetStore` Protocol(`get_manifest`, `list_versions`, `open_file`)만 사용. 실제 SDK 진입점은 CR-L004 | 경계 계약 1.1 |
| I7 실패 사례 읽기 | 회귀 평가(`evaluation.regression`, 기본 꺼짐)를 켜면 `inputs.failure_store`(`boundary_contracts.clients:HttpFailureStore`)로 읽는다(`model_lab/regression.py`, `configs/gpu-dev.yaml`, `docs/runbook.md` 회귀 평가 절) | v2.0.2 |
| I1, I4 | 이 단계에서 쓰지 않음(I1은 설정에 클라이언트를 고르는 자리만 있다) | |

의존은 Git 태그로 고정한다(`requirements.txt`). 개발은 `pip install -e ../boundary-contracts ../turn-engine ../model-runtime`.

## 게시자 기록과 한계 (ML-6, ML-14)
- 게시 때 서버가 {토큰 역할, 접속 주소, 시각, content_hash}를 `manifests/<패키지>/<버전>.published.json`에 남긴다. 계약의 매니페스트는 바뀌지 않는다. 서명은 없다(사용자·헤드 결정, `docs/runbook.md`에 제안). 전송은 평문 HTTP이고 TLS는 deploy·사용자 결정이다.
- 패키지 `ext."model-lab".lineage`에 학습 기록 요약이 들어갈 수 있다(`package-adapters --train-record-*`). 비계약 필드다.

## 토큰 요건과 실패 제한 (ADR 0009)
- 서비스는 토큰이 32자 미만이거나 단조로우면 시작을 거부한다. 무차별 대입 방어는 토큰 엔트로피다. 인증 실패 제한(유효하지 않은 토큰에만, 주소별)은 로그 스팸·CPU 보호이며, 유효한 토큰은 막지 않는다(같은 주소의 정상 클라이언트 보호).
- 큰 업로드(1MiB 초과)를 거절하면 서버가 본문을 읽지 않고 연결을 닫아 클라이언트가 응답 대신 연결 초기화를 볼 수 있다(`UNAVAILABLE`로 보임). 한계로 둔다.

## 경계 오류
경계 호출(I2·I3)의 실패는 `BoundaryError`다(`NOT_FOUND`, `INVALID_RECORD`, `CONFLICT`, `INTEGRITY_ERROR`, `UNSUPPORTED_VERSION`…). 재시도는 `UNAVAILABLE`일 때만이며 `run-once`가 종료 코드 3으로 알린다. model-runtime의 예외(`ServingConfigMismatch` 등)는 `BoundaryError`가 아니라 설정·환경 오류로 그대로 전달된다. 파이프라인 내부 오류는 `PipelineError`(종료 코드 2).

## 장애 영향
- 이 영역이 멈추면 새 모델 패키지가 생기지 않을 뿐 서비스에는 영향이 없다. 서비스는 릴리스(I4)만 읽고 레지스트리를 읽지 않는다.
- 게시는 매니페스트를 마지막에 쓰므로 중단되어도 반쯤 게시된 패키지는 보이지 않는다. 다시 실행하면 이어서 끝낸다.
- 레지스트리 파일이 깨지면 읽기가 `INTEGRITY_ERROR`로 실패한다(변조를 조용히 통과시키지 않는다).
- I2가 멈추면 `run-once`는 종료 코드 3(`UNAVAILABLE`)이고 상태는 바뀌지 않는다.

## 하지 않는 것
릴리스 승인(게이트), 통과 임계치·지표 선정(O11, 사용자). 보고서에 합격 여부를 쓰지 않는다.
