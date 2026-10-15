# CR-L002 `ModelPackageManifest.base_model.id`의 Id 패턴이 Hugging Face 저장소 이름을 막는다 (수신: 헤드)

- 현황: `base_model.id`는 `Id`(`^[A-Za-z0-9][A-Za-z0-9._:-]*$`)다. 임시 기반 모델 `Qwen/Qwen2.5-1.5B-Instruct`는 `/`가 있어 통과하지 못한다.
- 임시 처리(model-lab 안에서): `/`를 `--`로 바꿔 `Qwen--Qwen2.5-1.5B-Instruct`를 쓰고, 원래 이름을 `ext."model-lab".base_model_hf_id`에 남긴다(HF 캐시 디렉터리 관례). 게이트가 `base_model.id`를 비교만 한다면 문제없다. 해석하려 하면 어긋난다.
- 요청(택일): (1) `base_model.id`를 자유 문자열로 완화(MINOR, 허용 범위 확대), (2) 위 치환 규칙을 CONVENTIONS에 적는다.
- 같은 문제: `base_model.revision`은 커밋 해시를 기대하지만, 이 환경에서는 `main` 같은 이름도 들어올 수 있다. 실행 때 해석한 커밋을 쓰도록 구현했다(확인한 값만 기록).
