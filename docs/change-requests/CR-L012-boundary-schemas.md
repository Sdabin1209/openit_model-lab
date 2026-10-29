# CR-L012: 영역 경계를 넘는 형식의 JSON Schema 초안 (작업 지시서 1의 B)

> **처리됨 (U49, 2026-10-30).** 5개 중 `model-package-layout`은 계약 `ModelPackageLayout`으로 승격(D107)되어, `regression-trace-input`은 계약 `OpsFeedbackDeidentifiedTrace`로 대체되어 이 리포의 사본을 지웠다(정본은 boundary-contracts). 나머지 3개(`server-dataset-row`, `server-train-invocation`, `train-wrap-record`)는 학습 서버와의 형식이라 영역 소유로 남기고 `docs/schemas/`로 옮겼다. 아래 본문은 요청 당시의 기록이며 경로는 옮긴 위치로 고쳤다. 분류 표는 `CHANGELOG.md`.

- 상태: 헤드에게 보낸다(2026-10-28, model-lab). **초안이다. 코드는 아직 이 스키마를 쓰지 않는다**(헤드가 계약에 올린 뒤의 다음 단계).
- 스키마: `docs/schemas/*.schema.json` (JSON Schema 2020-12), 예시: `schemas/examples/`. 예시는 실제 코드가 만든 문서다(생성 방법은 형식마다 적음).
- 근거: `docs/audit/structure-rules-2026-10-28.md` 6절 "계약에 없는 경계 형식" 1·2·4·5와 README D-E.

## 1. server-dataset-row (학습 서버 JSONL 한 줄)
- 만드는 쪽/읽는 쪽: 학습 서버(학습 담당)가 만들고, model-lab(`transform.from_server_row`)과 서버 학습 스크립트가 읽는다. 이 영역이 **받는** 형식.
- 예시: `examples/server-dataset-row.valid.json`(A), `.b.valid.json`(B). `transform.to_server_row`가 test 픽스처 샘플로 만든 줄. 거부 예시: messages가 2개.
- 모르는 키: **거부**(`additionalProperties: false`). 이유: 서버 형식 변화(필드 추가)를 조용히 지나치면 학습·평가 입력이 어긋난다. 현재 코드는 모르는 키를 무시하므로(`from_server_row`) 이 정책은 코드보다 엄격하다(제안, 헤드 결정).
- 불변식: messages는 system, user, assistant 순서 3개. 한 파일의 system 프롬프트는 한 종류(스키마로 못 씀, `read_server_file`이 검사). user.content는 문자열화한 계약 입력, assistant.content는 A면 문자열화한 ActionExtraction. `split`은 train/val/test만(내부 TRAIN/VALIDATION/EVAL로 변환).
- 추가 확인: 이 PC의 `runs/_server_ds/*.jsonl`(출처가 학습 서버 사본인지 이 점검에서 확인하지 못함, 6개 파일 합계 10,264줄(546+3982+549+558+4069+560, 위반 0)) 전 줄이 통과했다.

## 2. server-train-invocation (서버 스크립트 인자와 기대 산출물)
- 만드는 쪽/읽는 쪽: model-lab(`train_wrap.plan`, `train --dry-run` 출력)이 만들고 서버 스크립트가 `command` 인자로 받는다. 이 영역이 **내는** 형식.
- 예시: `examples/server-train-invocation.valid.json` = `plan(load_train_config(configs/train.yaml))` 출력. 거부 예시: expected_files에 adapter_config.json이 없음.
- 모르는 키: 거부. 이 문서는 우리가 만들어서 내므로 키가 늘면 의식적으로 스키마를 고쳐야 한다.
- 불변식: command는 셸 없는 인자 목록, 산출물은 expected_adapter_dir 바로 아래 `adapter_model.safetensors`·`adapter_config.json`(**어댑터가 out_dir 바로 아래인지는 서버에서 확인하지 못했다**, `configs/train.yaml` 주석). 인자 이름은 헤드가 서버 코드의 add_argument를 읽어 확인한 것이며 스크립트 `--help` 실행은 하지 않았다.
- 주의: 이 형식 자체가 영역 경계를 넘는지는 헤드가 판단한다. 서버 스크립트의 인자·산출물 규약(이웃이 소유한 쪽)은 학습 담당이 정하는 것이 원칙이다.

## 3. train-wrap-record (`*.train-wrap.json`, record_version 1)
- 만드는 쪽/읽는 쪽: model-lab `train`(학습 서버)이 만들고 model-lab `package-adapters`(`attach.read_train_record`)가 읽는다. 영역 안에서 정의됐지만 다른 호스트·시점 사이를 오간다. 패키지 `ext."model-lab".lineage`로 흘러간다.
- 예시: `examples/train-wrap-record.valid.json` = `tests/test_train_wrap.py`의 가짜 서버 스크립트 실행이 `write_record`로 쓴 파일(진짜 학습 아님). 거부 예시: record_version 2.
- 모르는 키: 거부. 버전을 올려 새 키를 넣게 한다(`record_version`이 그 신호).
- 불변식: exit_code 0일 때만 쓴다. dataset.id·version은 package-adapters 입력과 같아야 하고 content_hash가 있으면 같아야 한다. adapter.files 해시는 지금 어댑터 파일과 같아야 한다(`read_train_record`가 검사). **불일치 발견**: 이 기록의 해시는 접두사 없는 16진인데 계약 해시는 `sha256:`을 붙인다. 지금은 코드가 그렇게 쓰므로 스키마도 그대로 적었다. 계약에 올릴 때 정규화할지 헤드가 정해 달라.

## 4. regression-trace-input (회귀 평가가 읽는 TurnTrace 키)
- 만드는 쪽/읽는 쪽: ops-feedback이 비식별 trace로 내고(I7 `get_trace`) model-lab(`regression.build_set`)이 읽는다. 이 영역이 **받는** 형식 중 읽는 부분집합.
- 키(D98, 문진 스키마 `TurnTrace`가 정본, `../survey-service/schema/schemas/TurnTrace.json`에서 `extract_input`·`response_input` 속성 존재 확인): LORA_A는 `extract_input`, LORA_B는 `response_input`. 출력 키는 읽지 않는다.
- 예시: `examples/regression-trace-input.valid.json`(A), `.b.valid.json`(B) = `tests/test_regression.py` 픽스처의 trace. 거부 예시: 입력 키가 없고 출력 키만 있음.
- 모르는 키: **무시**(`additionalProperties: true`). 이유: trace는 TurnTrace 전체이고 필요한 키는 둘뿐이라, 다른 키를 거부하면 TurnTrace 변경이 회귀 평가를 깬다. 값은 `ActionExtractionInput`/`ResponseRequest`로 model-lab이 다시 검증한다(R7과 별개: 이웃에서 온 입력의 경계 검증).
- 불변식: 입력은 객체여야 한다. 아니면 `trace_input_missing`, 계약 입력 위반이면 `input_invalid`로 건너뛰고 센다.
- 한계: 계약의 TurnTrace 전체 스키마를 $ref하지 않았다(독립 파일 초안). 헤드가 계약에 올릴 때 `TurnTrace`의 부분 스키마로 대체하는 것이 맞다.

## 5. model-package-layout (모델 패키지 어댑터 디렉터리의 파일 배치)
- 만드는 쪽/읽는 쪽: model-lab이 게시하고 release-gate·model-runtime이 어댑터 tar로 받는다(`GET .../adapter`). 이 영역이 **내는** 형식. ModelPackageManifest는 이미 계약에 있고, 이 초안은 그 `adapter_hash`·`system_prompt`가 가리키는 **디렉터리 안의 파일 구성**이다.
- 예시: `examples/model-package-layout.valid.json` = `package_adapters`가 만든 패키지(`tests/test_attach.py` 가짜 어댑터)의 게시된 어댑터 디렉터리를 파일 해시로 나열한 것. 거부 예시: system_prompt.txt 누락.
- 모르는 키/파일: **거부**. 이유: adapter_hash가 디렉터리 전체 해시라 의도하지 않은 파일이 섞이면 같은 입력이 다른 패키지가 된다(`attach.py` 머리말).
- 불변식: 스키마 $defs 설명 참조(adapter_hash, system_prompt.sha256, base_model_name_or_path, r <= max_lora_rank).
- 참고: 레지스트리 서버 내부 배치(`manifests/`, `adapters/`, `eval_reports/`, `ids.json`)는 model-lab의 저장 구조이며 이웃이 알 필요가 없어 스키마에 넣지 않았다(HTTP로만 읽는다).

## 검증 (jsonschema, 이 리포의 .venv)
명령(`/tmp/validate.py`는 아래 스크립트): `.venv/bin/python -I /tmp/validate.py docs/schemas`
```
OK   model-package-layout.invalid.json: rejected ('system_prompt.txt' is a required property)
OK   model-package-layout.valid.json: accepted
OK   regression-trace-input.b.valid.json: accepted
OK   regression-trace-input.invalid.json: rejected ({'extract_output': {'action': 'NEXT'}} is not valid under any of the g)
OK   regression-trace-input.valid.json: accepted
OK   server-dataset-row.b.valid.json: accepted
OK   server-dataset-row.invalid.json: rejected ([{'content': "건강검진 문진 상담에서 환자의 현재 발화 1턴을 읽고, 문진 DB에 저장할 값과 다음 절차를 정해 J)
OK   server-dataset-row.valid.json: accepted
OK   server-train-invocation.invalid.json: rejected (['adapter_model.safetensors', 'adapter_config.json'] was expected)
OK   server-train-invocation.valid.json: accepted
OK   train-wrap-record.invalid.json: rejected (1 was expected)
OK   train-wrap-record.valid.json: accepted
```
검증 스크립트 본문:
```python
import json, sys, glob, os
from jsonschema import Draft202012Validator
d = sys.argv[1]
for sp in sorted(glob.glob(os.path.join(d, "*.schema.json"))):
    name = os.path.basename(sp)[:-len(".schema.json")]
    sch = json.load(open(sp)); Draft202012Validator.check_schema(sch); v = Draft202012Validator(sch)
    for ex in sorted(glob.glob(os.path.join(d, "examples", name + ".*json"))):
        e = os.path.basename(ex)
        if e[len(name):].count(".") and not (e.startswith(name + ".")): continue
        errs = list(v.iter_errors(json.load(open(ex))))
        want = ".invalid." in e
        print(f"{'OK  ' if bool(errs)==want else 'FAIL'} {e}: {'rejected' if errs else 'accepted'}" + (f" ({errs[0].message[:70]})" if errs else ""))
```
