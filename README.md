# model-lab

문진 시스템의 모델 패키지 영역이다. LoRA-A(발화에서 값 추출)·LoRA-B(응답 문장 생성) 어댑터를 불변 "모델 패키지"로 묶어 보관하고 내주는 model-registry 서비스, 학습 서버가 만든 어댑터를 패키지로 얹는 `package-adapters`, 학습 서버 코드를 감싸는 `train`/`train-doctor`를 가진다. 근거: `docs/brief.md`, `docs/design.md`, `docs/runbook.md`.

모델 패키지는 어댑터 파일, 기반 모델, 서빙 설정, 학습 계보(데이터셋 버전 등), 평가 리포트 참조를 한 묶음으로 만든 것이다. 한 번 게시하면 바꿀 수 없다. 이 영역만 레지스트리에 쓴다(`docs/brief.md` 6절).

**하지 않는 것**: 릴리스 승인(release-gate 몫), 통과 임계치·지표 선정·평가셋 구성(O11, 사용자 결정이라 코드와 설정에 값이 없다). 평가 리포트에는 합격 여부를 쓰지 않는다(`docs/boundary.md`).

## 전체 속의 위치

| 방향 | 상대 | 무엇 | 인터페이스 |
| --- | --- | --- | --- |
| 입력 | synthetic-data | 학습·평가 데이터셋 | I2 (읽기 SDK 규약 `DatasetStore`; 실제 진입점은 CR-L004, 이 리포는 독립 파일 읽기 구현 `file_dataset_store` 사용) |
| 입력 | ops-feedback | 확정된 실패 사례(회귀 평가용, 선택) | I7 (`evaluation.regression`, 기본 꺼짐; `docs/runbook.md`) |
| 입력 | question-authoring | 문항 정의 | I1 (설정 자리만 있고 쓰지 않음; `docs/design.md` 10절) |
| 입력 | 학습 서버(영역 밖) | 학습한 어댑터 | 사람이 파일로 가져옴 (`docs/runbook.md` D84 절) |
| 출력 | release-gate | 모델 패키지 조회 | I3 `ModelRegistry`, HTTP: `GET /v1/packages…`, `.../adapter`, `/v1/eval-reports/{ref}`, `POST /v1/packages`(model-lab만 쓰기) |

라이브러리로 boundary-contracts v2.0.2, turn-engine v0.4.0, model-runtime v0.7.0을 태그로 쓴다(`requirements.txt`). 서비스 이미지는 boundary-contracts만 넣는다(`requirements-service.txt`). 판정 조회 I4는 이 단계에서 쓰지 않는다(`docs/boundary.md`).

## 구조

| 경로 | 역할 |
| --- | --- |
| `model_lab/registry.py` | I3 레지스트리의 파일·메모리 구현 (패키지 저장, 해시 검증, 멱등) |
| `model_lab/service/` | model-registry HTTP 서비스 (`/healthz`, `/readyz`, 읽기·쓰기 토큰 분리) |
| `model_lab/attach.py` | `package-adapters`: 학습 서버 어댑터를 패키지로 만들어 게시 (평가 안 함) |
| `model_lab/train_wrap.py` | `train`, `train-doctor`: 학습 서버의 정본 학습 스크립트를 설정으로 감싼다 (D78) |
| `model_lab/pipeline.py` 외 | 설정 하나로 돌리는 파이프라인 `run`/`run-once`/`status` (적재→변환→학습→평가→패키징→게시). `transform.py`, `evaluate.py`, `packager.py`, `regression.py`, `trainers/`, `backends/` 포함 |
| `configs/`, `profiles/`, `prompts/` | 실행 설정, 하드웨어·기반 모델 프로파일, 서버 데이터와 같은 시스템 프롬프트 |
| `scripts/smoke_registry.sh` | 서비스 이미지 스모크 (빌드→빈 포트 기동→healthz/readyz/토큰/게시/조회) |
| `tests/` | 시험 (레지스트리 적합성은 memory/file/http 공통) |
| `Dockerfile` | model-registry 서비스 이미지만. run-once 잡은 이미지 없이 `requirements.lock` 환경 (D22) |
| `docs/` | 아래 "더 자세한 문서" |

```
학습 서버(영역 밖) --어댑터 파일--> package-adapters --+
                                                       v
데이터셋(I2) -> 변환 -> 학습 -> 평가 -> 패키징 -> 게시 --> model-registry (I3, HTTP) --읽기--> release-gate
              (run / run-once 파이프라인)
학습 서버 코드 <-- train / train-doctor (감싸기, 어댑터 산출물 점검)
```

파이프라인의 `train` 단계는 아직 `train` 감싸기에 연결되지 않았다(`CHANGELOG.md` 0.2.3). 파이프라인 기본 학습기 `trainers/hf.py`는 비정본이라 서버와 같은 어댑터를 만들지 못한다(`docs/runbook.md`).

## 실행 방법

전제(문서 근거): Python 3.10 이상(`pyproject.toml`), 의존 설치에 GitHub ssh 접근(태그 설치, `Dockerfile` 머리말), 이미지 스모크에 Docker. 학습·GPU 평가는 GPU 호스트 몫이고 이 README는 접속 방법을 다루지 않는다(`docs/runbook.md`의 GPU 호스트 절). 비밀(토큰)은 설정이나 명령줄에 쓰지 않고 `NAME_FILE`(파일 경로)로 받는다. 파일이 없을 때만 개발용으로 `NAME` 환경 변수를 쓴다(D84). 설정에 비밀을 직접 적으면 시작을 거부한다.

```
# 1. 설치 (이 리포 안에서)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt pytest          # 태그 설치. 이웃 폴더 editable 설치는 docs/runbook.md "환경 (CPU)"
# 2. 시험
.venv/bin/python -m pytest -q
# 3. 모델 없이 파이프라인 끝까지 (CPU)
.venv/bin/python scripts/make_fixture_dataset.py fixtures/store
.venv/bin/python -m model_lab run --config configs/dryrun.yaml
.venv/bin/python -m model_lab status --config configs/dryrun.yaml
# 4. 서비스 이미지 스모크 (Docker 필요, vendor/wheels에 boundary-contracts wheel 필요)
scripts/smoke_registry.sh
# 5. 학습 감싸기 (학습 서버에서): 점검 -> 명령 미리 보기
.venv/bin/python -m model_lab train-doctor --config configs/train.yaml
.venv/bin/python -m model_lab train --config configs/train.yaml --dry-run
# 6. 학습 서버 어댑터 얹기 (게시 안 함; 인자 전체는 docs/runbook.md D84 절)
.venv/bin/python -m model_lab package-adapters --help
```

이 문서를 쓰면서 **돌려 본 것**: `.venv/bin/python -m pytest --collect-only -q`(150건 수집, 실행은 안 함), `.venv/bin/python -m model_lab --help`(하위 명령 `run`, `run-once`, `status`, `package-adapters`, `train`, `train-doctor` 확인). **돌리지 않은 것**: 위 1~6 전부(설치, 시험 실행, 파이프라인, 이미지 빌드·스모크, doctor, train, package-adapters), GPU 관련 전부.

## 현재 상태

확인된 것(근거 문서): CPU에서 가짜 모델로 파이프라인 끝까지, 레지스트리 파일·메모리·HTTP 적합성 시험, 이미지 스모크는 v2.0.0 때 통과(`docs/STATUS.md` 9차). 시험 113건 통과는 10차 때 값(`docs/STATUS.md`)이고 이후 시험이 늘어 지금은 150건이 수집된다(위 수집 결과). 현재 150건이 통과하는지는 **미확인**. GPU 호스트(gpu-dev)에서 작은 시험 데이터로 학습→게시 한 바퀴와 재실행 skip을 확인한 기록이 있으나 오래된 버전 기준이다(`docs/STATUS.md` 2차). 학습 품질과 학습 감싸기 결과는 의미 있는 값이 없다.

확인 못 한 것: 실제 서버 학습 스크립트의 인자 이름·산출물 위치(서버 접속 불가였음, `args_verified: false`), 진짜 어댑터의 서빙, 실제 모델 평가 값(지금까지 오라클 가짜 모델), 재개한 학습의 비트 단위 재현(`docs/design.md` 4절), 현재 버전(boundary-contracts v2.0.2, turn-engine v0.4.0, model-runtime v0.7.0)에서의 이미지 스모크와 새 환경 설치(`docs/STATUS.md` 11차의 pip 해결 미시도 기록). 두 번째 독립 머신 확인은 U12로 열려 있다.

## 이 영역의 열린 항목

출처: `head/docs/OPEN_GAPS.md` (읽기 전용으로 읽음).

| ID | 요약 | 막고 있는 것 |
| --- | --- | --- |
| G06 | 학습 정본은 서버 코드(D78). 감싸기는 만들었으나 서버 인자 확인과 파이프라인 연결이 남음 | GPU·학습 서버 접근 |
| H3 | 학습 파이프라인 실제 실행 (G06 관련) | GPU |
| G09 | 대장은 `부분`. 이 리포는 `tests/golden/`을 제거했다(커밋 b1f57e3, 디렉터리 없음 확인). 대장 갱신은 헤드 몫 | 헤드(대장 정리) |
| U10 | 평가가 가짜 모델로만 돌았다. 실제 모델 평가 값 없음 | GPU, 학습된 어댑터 |
| U11 | A 출력 의미 위반율 지표(`validate_extraction` 기반)를 평가에 넣지 않음 | 헤드 허락·학습 담당 확인 |
| D-C | `METRICS.md`가 v1 지표 기준이라 v2 평가 이름과 어긋남 | 헤드+학습 담당 |
| D-E | 회귀 평가가 읽는 v2 TurnTrace 입력 키 이름이 추정 | survey-service 확정 |
| H2 / U08 | 학습된 LoRA-A/B를 얹은 한 턴 통과, 어댑터 교체 시험 | GPU, 다른 영역(서빙) |
| O11 | 지표·통과 임계치·평가셋 구성 (`docs/brief.md` 7절; 대장 번호는 없음) | 사용자 결정 |

## 더 자세한 문서

| 문서 | 내용 |
| --- | --- |
| `CLAUDE.md` | 이 영역 에이전트 지침 |
| `docs/brief.md` | 영역 목적, 소유·소비 계약, 수용 기준, GPU 호스트 규칙 (헤드 작성) |
| `docs/boundary.md` | 소유 저장소, 제공 I3, 소비 버전, 장애 영향 |
| `docs/design.md` | 내부 설계: 컴포넌트, 설정 스키마, 재현성, 평가 리포트, 패키징 |
| `docs/runbook.md` | 환경 변수, 실행, GPU 호스트 절차, 학습 감싸기, 어댑터 얹기 |
| `docs/STATUS.md` | 세션별 진행 기록(최신이 위) |
| `docs/change-requests/` | 헤드에게 보낸 변경 요청 CR-L001~L008 |
| `docs/rules/` | 업무 규칙 (헤드 전달, 출처 `SOURCE.md`) |
| `docs/base-model-comparison.md` | 기반 모델 후보 비교 참고 자료 (비교는 중단됨) |
| `CHANGELOG.md` | 변경 이력 |

## 문서끼리 어긋난 곳 (이 문서를 쓰며 발견)

- 시험 개수: `docs/runbook.md` 80건, `docs/STATUS.md` 113건(10차), 실제 수집 150건.
- 버전: `pyproject.toml` 0.1.0, `CHANGELOG.md` 0.2.3(태그 없음). `docs/boundary.md` 머리말·EvaluationReport는 v1.3.0 언급, 소비 표는 v2.0.2.
- `docs/boundary.md` 소비 표는 I7을 "쓰지 않음"이라 하나 `docs/runbook.md`·`docs/design.md`는 회귀 평가(I7)를 구현했다고 쓴다.
- `docs/runbook.md` GPU 절의 wheel 예시(turn-engine v0.2.0, model-runtime v0.4.0)는 현재 requirements와 다르다.
