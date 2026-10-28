# 0013. 설정의 package_id(`lora-a-parser`, `lora-b-generator`)를 바꾸지 않는다

## 상태
채택

## 맥락
`configs/*.yaml`의 `roles.<역할>.package_id`는 옛 설계의 역할 이름(parser, generator)에서 왔다. 지금의 이름(LoRA-A 추출, LoRA-B 응답)과 어긋나 보인다.

## 결정
`package_id` 값은 바꾸지 않는다. 이름은 식별자일 뿐 의미를 갖지 않는다.

## 왜
`package_id`는 단순한 라벨이 아니라 이미 만들어진 산출물의 키다.
- 설정 해시(`config_hash`)와 run 키가 설정 내용에서 계산되므로 값을 바꾸면 같은 입력의 run이 새 run으로 취급되어 재실행 skip(멱등)이 깨진다(`docs/design.md` 재현성 절).
- 매니페스트의 `package_id`, `eval_report_ref`(`eval-<package_id>.<version>`)와 게시된 패키지는 불변이다. 이름을 바꾸면 같은 계보가 두 이름으로 갈라지고 release-gate 등 읽는 쪽의 참조가 끊긴다.
- 학습 서버에서 이미 학습한 어댑터를 `package-adapters`로 얹은 매니페스트도 이 이름을 가진다.
표준 관행: 식별자는 의미가 바뀌어도 불변으로 둔다(이름 변경은 새 식별자 도입).

## 검토한 대안과 버리는 이유
- 이름을 바꾸고 기존 패키지를 재게시: 불변 규칙과 기존 해시를 깬다.
- 별칭(alias) 도입: 레지스트리 계약에 없는 개념이고 읽는 쪽이 이중 이름을 알아야 한다.

## 결과
얻는 것: 해시·run·게시물의 연속성. 감수하는 것: 이름이 현재 용어(LoRA-A/B)와 다르다. 설명은 README에 한 줄 둔다.

## 되돌리는 조건
기존 패키지·run이 모두 폐기되거나(예: 레지스트리 초기화) 계약이 별칭을 지원하면 새 ADR로 대체(`Supersedes`)한다.
