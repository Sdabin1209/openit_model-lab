# CR-L003 평가 백엔드와 서빙 백엔드가 다르다 (수신: 헤드, model-runtime)

- 현황: model-runtime v0.3.0에는 실제 `ConstraintBackend`가 없다(`BackendNotAvailable`). 그래서 model-lab이 transformers+peft로 만든 `hf-transformers` 백엔드(제약 디코딩 없음, `constraint_mode=none`)로 `parse_raw/generate_raw`와 `parse/generate`를 돌린다. 렌더 -> 검증 -> 재시도 -> 보호는 서빙과 같은 model-runtime 코드를 지나지만 **생성 단계는 서빙(vLLM 계획)과 다르다.**
- 영향: 이 평가 수치는 서빙 동작을 완전히 재현하지 않는다(제약 디코딩이 있는 서빙은 형식 위반이 더 적을 것이다). 매니페스트의 `serving_config.constraint.backend`는 `hf-transformers`로 기록되어, 게이트가 서빙과 같은 설정으로 다시 로드하려면 서빙 백엔드용 설정으로 새 패키지(또는 다른 serving_config)가 필요하다.
- 요청: model-runtime의 첫 실제 백엔드가 나오면 평가 백엔드를 그것으로 바꾼다(설정의 `evaluation.backend.kind`). 서빙용 `serving_config`를 누가 어느 시점에 정하는지(model-lab이 프로파일로 정하는지, model-runtime이 제공하는지) 알려 달라. 현재는 설정 파일(`serving:`)에서 역할별로 받는다.
