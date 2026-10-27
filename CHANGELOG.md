# 변경 이력

## 0.2.0 (제안, 이전 중; 태그 없음)
- boundary-contracts v2.0.0(D74, D75). v1 레코드는 읽지 않는다.
- 변환기: 학습 서버 messages <-> ActionExtractionSample/ResponseSample(`transform.py`), 검증 스크립트 `scripts/check_server_dataset.py`.
- 패키징: `ModelPackageManifest.system_prompt`(어댑터 디렉터리 안 파일, adapter_hash에 포함), 설정 `roles.<역할>.system_prompt_file`(필수), `prompts/`. 레지스트리가 프롬프트 파일을 확인한다.
- 데이터셋 파일 역할 LORA_A/LORA_B, 샘플 검증은 v2 샘플 스키마.
- model-runtime v0.6.0, turn-engine v0.3.0(태그)로 올림. 서빙 설정 prompt-3 / protect-2.
- model-runtime v0.6.4로 올림. 서빙 설정 relax-2(supported_features에 pattern, minItems 추가; relax-1은 거부). 설정·시험의 relax_policy_version을 relax-2로 바꿈.
- 파이프라인: 학습 데이터 변환은 v0.6.0 `render_prompt`(시스템 프롬프트 포함), 학습 직후 시스템 프롬프트를 어댑터 디렉터리에 넣는다(adapter_hash 포함), 평가는 `extract_raw`(A, 보호 없음) / `respond_raw`·`respond`(B). 샘플에 문항 세트가 없으면 파일의 문항 세트(하나일 때)로 채움.
- 평가·지표: A는 score_a_v2.py 이름과 규칙, B는 끝맺음·다음 질문·새 숫자·금지 표현·문장 수(`text_rules.py`). v1 지표(risk_signal_* 등) 삭제. 상세 보고서 `report_version: 2`.
- 회귀: `expected_kind` LORA_A/LORA_B, 입력은 ActionExtractionInput / ResponseRequest로 검증. trace 입력 키는 추정(미정).
- 시험: 합성 v2 fixture(tests/doubles/fixtures.py), v1 golden 시험 제거, 기반 모델 비교 스크립트를 v2로 이식(few-shot 제외). 간헐 실패하던 캐시 정리 시험 수정.
- 정리: 이전 커밋에서 pipeline.py에 중복된 코드 조각 제거.
- 미이전: 학습기(`trainers/hf.py`, 정본 결정 대기), 서빙-학습 user 글자 불일치(CR-L008).

## 0.1.0 (2026-10-15)
- 용량 보호를 df 대신 사용량 예산(max_footprint_gb)으로 교체, 캐시 정리를 huggingface_hub 삭제 전략으로(scripts/clean_model_cache.py).
- D61: 기반 모델 비교 중단(참고 자료로 보존). 내려받기 전 디스크 확인(disk_guard)과 모델 캐시 정리 스크립트(기본 dry-run) 추가.
- 수정: model-registry가 오류 응답 전에 요청 본문을 비운다(간헐적 UNAVAILABLE 경쟁 조건), 회귀 시험 추가.
- 기반 모델 후보 비교 도구(scripts/compare_base_models.py, configs/compare/base-model-candidates.yaml)와 docs/base-model-comparison.md. gpu-dev 불가로 측정은 미실행.
- EvaluationReport(boundary-contracts v1.3.0) 방출, 회귀 평가의 reviewed_input 우선, eval_report_ref를 패키지 기준으로 변경.
- 데이터셋 파일 저장소 읽기 구현, 회귀 평가(I7), 실제 실행 설정 configs/gpu-dev.yaml, 시험용 설정 이름 변경(gpu-dev-fixture-*), 개발 네트워크 절차.
- boundary-contracts v1.2.0, model-runtime v0.4.0로 올림. base_model.id 원래 이름 사용. 평가에서 is_fallback 비교 제거, 보고서에 평가 백엔드, 패키지 ext에 resumed 기록. 서버 적합성 시험을 실제 HttpModelRegistry로 수행. 게시 장애 시 게시만 재시도.
- model-registry 서비스(I3 HTTP 서버), Dockerfile, 스모크 스크립트, 원격 게시 설정(`registry.kind: http|factory`), 이웃 읽기 SDK 팩토리 구조.
- 파이프라인(설정 -> I2 읽기 -> 변환 -> 학습 -> 평가 -> 패키징 -> I3 게시), `run`/`run-once`/`status`, 체크포인트 재개, run 기록.
- I3 ModelRegistry 파일·메모리 구현과 적합성 시험.
- 평가기: 서빙과 같은 경로(model-runtime `*_raw` + 보호 적용), 차원별 쪼개 보기, 보지 못한 문항 세트 따로 보고. 임계치·지표 선정 없음(O11).
- 학습기: transformers+peft LoRA(`hf`), CPU 시험용 `dry-run`. 프로파일 `profiles/gpu-dev-12g.yaml`.
- golden 평가기 시험, 변경 요청 CR-L001~L004.
- 의존: boundary-contracts v1.1.0, turn-engine v0.2.0, model-runtime v0.3.0.
