# 변경 이력

## 0.1.0 (2026-10-15)
- boundary-contracts v1.2.0, model-runtime v0.4.0로 올림. base_model.id 원래 이름 사용. 평가에서 is_fallback 비교 제거, 보고서에 평가 백엔드, 패키지 ext에 resumed 기록. 서버 적합성 시험을 실제 HttpModelRegistry로 수행. 게시 장애 시 게시만 재시도.
- model-registry 서비스(I3 HTTP 서버), Dockerfile, 스모크 스크립트, 원격 게시 설정(`registry.kind: http|factory`), 이웃 읽기 SDK 팩토리 구조.
- 파이프라인(설정 -> I2 읽기 -> 변환 -> 학습 -> 평가 -> 패키징 -> I3 게시), `run`/`run-once`/`status`, 체크포인트 재개, run 기록.
- I3 ModelRegistry 파일·메모리 구현과 적합성 시험.
- 평가기: 서빙과 같은 경로(model-runtime `*_raw` + 보호 적용), 차원별 쪼개 보기, 보지 못한 문항 세트 따로 보고. 임계치·지표 선정 없음(O11).
- 학습기: transformers+peft LoRA(`hf`), CPU 시험용 `dry-run`. 프로파일 `profiles/gpu-dev-12g.yaml`.
- golden 평가기 시험, 변경 요청 CR-L001~L004.
- 의존: boundary-contracts v1.1.0, turn-engine v0.2.0, model-runtime v0.3.0.
