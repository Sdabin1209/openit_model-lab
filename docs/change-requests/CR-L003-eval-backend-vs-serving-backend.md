# CR-L003 평가 백엔드와 서빙 백엔드가 다르다 (수신: 헤드, model-runtime)

- 현황: model-runtime v0.3.0에는 실제 `ConstraintBackend`가 없다(`BackendNotAvailable`). 그래서 model-lab이 transformers+peft로 만든 `hf-transformers` 백엔드(제약 디코딩 없음, `constraint_mode=none`)로 `parse_raw/generate_raw`와 `parse/generate`를 돌린다. 렌더 -> 검증 -> 재시도 -> 보호는 서빙과 같은 model-runtime 코드를 지나지만 **생성 단계는 서빙(vLLM 계획)과 다르다.**
- 영향: 이 평가 수치는 서빙 동작을 완전히 재현하지 않는다(제약 디코딩이 있는 서빙은 형식 위반이 더 적을 것이다). 매니페스트의 `serving_config.constraint.backend`는 `hf-transformers`로 기록되어, 게이트가 서빙과 같은 설정으로 다시 로드하려면 서빙 백엔드용 설정으로 새 패키지(또는 다른 serving_config)가 필요하다.
- 요청: model-runtime의 첫 실제 백엔드가 나오면 평가 백엔드를 그것으로 바꾼다(설정의 `evaluation.backend.kind`). 서빙용 `serving_config`를 누가 어느 시점에 정하는지(model-lab이 프로파일로 정하는지, model-runtime이 제공하는지) 알려 달라. 현재는 설정 파일(`serving:`)에서 역할별로 받는다.

## 추가 (D48 반영)
- 어댑터는 PEFT 디렉터리 형식(`adapter_config.json`의 `r`, `base_model_name_or_path`)으로 나온다(확인: GPU 실행 산출물). LoRA 순위는 프로파일 `training.lora.r` 한 곳에서 정한다.
- 엔진 상한 `max_lora_rank`는 serving_config가 아니라 엔진 프로파일(model-runtime)에 있다(헤드 정정). model-lab은 프로파일 `checks.max_lora_rank`(gpu-dev: 16)에 같은 값을 적고, 패키징 단계가 어댑터의 `adapter_config.json`의 `r`을 대조한다(넘으면 중단). 값은 model-runtime의 엔진 프로파일과 손으로 맞춘다 [미정: 자동 연동].
- 평가는 serving_config의 온도 0에서만 돈다(아니면 중단). 보고서 `overall.protection_interventions`와 `raw_valid`가 보호 전 유효율·보호 개입 건수다.
- model-runtime v0.4.0이 나왔다. 파이프라인이 안정되면 올린다(MAJOR.MINOR가 달라 릴리스의 model_runtime_version이 바뀐다). 지금은 v0.3.0 고정.
