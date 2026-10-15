# 기반 모델 후보 비교 (D60)

**상태: 부분 측정.** 호스트가 한 번 복구되어 후보 1개(qwen2.5-7b, 제로샷)만 측정했고 그 뒤 다시 꺼졌다(아래). 모델 카드로 확인한 부분(라이선스, 구조)은 채웠다. 나머지 후보와 few-shot 판은 호스트가 켜지면 "실행 방법"으로 이어서 하고 표와 추천을 채운다.

## 제약 (D60)
- 서빙: RTX 4090 24GB 한 장에 기반 모델 하나 + LoRA 둘(A 파서, B 생성기) + KV 캐시 여유.
- 학습: A6000 48GB(서빙과 별개). 개발: gpu-dev RTX 4070 SUPER 12GB.
- 가중치만의 산술(추정, 측정 아님): bf16은 파라미터당 2바이트라 7.6~8.2B는 약 15~16GB, 14.8B는 약 30GB다. 14B급 bf16은 24GB 한 장에 올릴 수 없고, 서빙에서도 양자화가 전제다. 4비트는 파라미터당 약 0.5바이트라 8B는 약 4~5GB, 14B는 약 7~9GB(양자화 상수·임베딩 포함 여유, 추정).

## 후보와 라이선스
"확인"은 모델 카드(README)의 메타데이터와 LICENSE 파일 첫머리를 이번 세션에서 직접 읽은 것이다. 읽지 못한 것은 "미확인"이다. LICENSE 전문을 법적으로 검토한 것은 아니다.

| 후보 | Hub ID | 크기 | 라이선스 | 확인 여부 | 한국어 관련 카드 기술 | 구조 |
| --- | --- | --- | --- | --- | --- | --- |
| qwen2.5-7b | `Qwen/Qwen2.5-7B-Instruct` | 7.61B | Apache-2.0 (카드 `license: apache-2.0`, LICENSE는 Apache License 2.0) | 확인(카드·LICENSE 읽음). 상업 이용은 Apache-2.0 조건에 따르며 이 문서는 법률 검토가 아니다 | "29개 이상 언어, 한국어 포함" | Qwen2ForCausalLM, 28층 |
| qwen3-8b | `Qwen/Qwen3-8B` | 8.2B | Apache-2.0 (카드, LICENSE 확인) | 확인 | "100개 이상 언어·방언" (한국어를 따로 언급하지 않음) | Qwen3ForCausalLM, 36층. 추론(thinking) 모드는 끄고 비교한다 |
| kanana-1.5-8b | `kakaocorp/kanana-1.5-8b-instruct-2505` | 8B급(카드의 정확한 수는 읽지 않음) | Apache-2.0 (카드 `license: apache-2.0`, LICENSE 확인) | 확인 | 카드 언어 태그 `en, ko`. 한국어 성능 수치는 읽지 않았다 | LlamaForCausalLM(`trust_remote_code` 불필요) |
| qwen2.5-14b-4bit | `Qwen/Qwen2.5-14B-Instruct` | 14.7B | Apache-2.0 (카드 `license: apache-2.0`) | 확인(카드). LICENSE 본문은 7B와 같은 계열이라 읽지 않았다 | 29개 이상 언어(카드 7B와 같은 문구는 14B 카드에서 확인하지 못했다) | Qwen2ForCausalLM, 48층. 12GB는 4비트로만 가능성이 있다 |

제외한 후보와 사유
- `LGAI-EXAONE/EXAONE-3.5-7.8B-Instruct`: 카드가 `license: other`, "EXAONE AI Model License Agreement 1.1 - NC"(비상업 표기)라 상업 이용 조건에 맞지 않을 가능성이 크고, `auto_map`이 있어 `trust_remote_code` 실행이 필요하다. 이 두 사유로 제외했다.
- `google/gemma-2-9b-it`, `meta-llama/Llama-3.1-8B-Instruct`: 접근 제한(gated)이라 카드를 읽지 못했다. 라이선스 **미확인**, 인증 없이 내려받을 수 없어 이번 비교에서 제외했다. 사용자가 접근을 승인하면 후보에 넣을 수 있다.

## 측정 (부분: 후보 1개만 끝났고 호스트가 다시 꺼졌다)
같은 표본(파서 golden에서 50건: `RISK_SIGNAL` 사례 9건 전부 + 균등 간격), 같은 프롬프트(`render_prompt` prompt-2), 온도 0, 시드 1000, 시도 1회, 4비트 NF4, eager 어텐션, 예시 없음(제로샷). 프롬프트는 채팅 템플릿의 사용자 메시지로 넣는다(Qwen3은 thinking 끔).

| 후보 | 스키마 유효율(`schema_valid_raw`) | 값 정확도(`value_exact_match`) | 위험 신호 재현율(`risk_signal_recall`) | 항목 재현율 | 보호 개입 | 최대 GPU 메모리(4비트) | 소요 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen2.5-7b (제로샷) | 0.00 (n=50) | 0.00 (n=23) | 0.00 (n=9) | 0.00 (n=68) | 50/50 | 5.9GB (5902MiB, 모델 내려받기 제외) | 108초(생성 47회, 새 토큰 3706) |
| qwen3-8b | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |
| kanana-1.5-8b | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |
| qwen2.5-14b-4bit | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |

해석 (확인한 것만): qwen2.5-7b는 JSON은 대부분 읽히지만(오류 목록 20건 중 17건이 `items/0`의 키 불일치 `additionalProperties`·`required`, 3건이 JSON 아님) 정의된 항목 스키마를 지키지 못해 50건 모두 스키마 유효율 0이다. 즉 **제로샷으로는 프롬프트의 형식 지시만으로 이 출력 형식을 맞추지 못한다.** 모든 후보가 비슷하게 0 근처일 가능성이 높아 제로샷 숫자는 후보를 가르는 근거가 되기 어렵다. 그래서 앞선 대화 턴으로 정답 예시 2개를 주는 **few-shot 판**(`configs/compare/base-model-candidates-fewshot.yaml`, `fewshot: 2`, sdpa)을 추가했고 후보 4개 전부를 돌릴 계획이었으나, 호스트가 꺼져 실행하지 못했다. 이 판도 미세조정 뒤의 성능이 아니라 형식 준수 출발점만 본다.

호스트 상태: 복구 뒤 후보 1개(제로샷)를 끝냈고, 두 번째 후보를 내려받는 중에 호스트가 SSH 연결 초기화·시간 초과로 다시 닿지 않았다(재시도 2회 실패). 호스트에서 돌던 작업은 중단되었을 수 있으며 그 결과 파일은 가져오지 못했다. 위의 qwen2.5-7b 수치는 작업 로그의 요약 한 줄에서 옮긴 것이다. 호스트의 모델 캐시(`~/.cache/huggingface`)에는 Qwen2.5-7B와 일부 다음 후보가 내려받아져 있을 수 있다.

### 2차 시도 기록
호스트 복구 뒤 점검: `vendor/wheels`에 boundary-contracts 1.3.0 있음, 코드·설정은 폴더별 rsync로 갱신, `runs-base-compare/qwen2.5-7b.evaluation-report.json`은 남아 있어 로컬 `runs/base-compare/`로 가져왔다. 모델 캐시는 Qwen2.5-7B 완료, Qwen3-8B는 내려받기가 중간에 끊겨 `.incomplete` 조각이 남아 있다(이어받기 가능). kanana·14B는 아직 내려받지 않았다. few-shot 판 4개 후보 실행을 시작했고 qwen2.5-7b(few-shot 2)가 끝난 직후 SSH가 `Connection reset by peer`로 끊겼다(시도 3회 실패, 규칙대로 멈춤). 그 한 후보의 few-shot 값은 로그 요약에서만 봤고 **보고서 파일을 가져오지 못해 이 표에 옮기지 않았다.**

## 추천 1순위
**정하지 못했다.** 후보 하나만 측정되었고 그 값(제로샷)은 후보를 가르는 정보가 아니다. 라이선스(모두 Apache-2.0 확인)와 크기만으로는 순위를 정할 근거가 부족하다. 참고로 서빙 24GB 제약에서는 7~8B급이 bf16으로 올라가고(가중치 약 15~16GB, 추정) 14B는 양자화가 전제다. few-shot 판 측정 뒤에 정한다.

## 한계 (측정 후에도 유지)
- 평가 백엔드는 transformers이고 서빙 백엔드(vLLM, 제약 디코딩)가 아니다. 제약 디코딩이 있으면 스키마 유효율은 서빙에서 더 높을 수 있다. 이 비교의 스키마 유효율은 "제약 없이 스스로 지킨 비율"이다.
- 4비트 양자화(NF4)는 서빙의 정밀도(bf16 또는 다른 양자화)와 다르다. 후보 사이의 순서는 대체로 유지되겠지만 확인하지 못했다.
- 학습하지 않은 기반 모델이다. 이 프롬프트는 미세조정을 전제로 쓴 것이라 기반 모델의 zero-shot 점수는 미세조정 뒤의 성능과 다를 수 있다. 여기서 보는 것은 한국어 이해와 JSON 형식 준수의 출발점이다.
- 표본 50건은 순위를 가르기에 작다. 표본 수(`n`)를 같이 보고 차이가 작으면 가르지 않는다.
- 12GB에서 14B(4비트)가 올라가는지는 실행해 봐야 안다(올라가지 않으면 사유만 적고 제외한다).

## 실행 방법 (호스트가 켜진 뒤)
이 환경의 SSH는 `-o KexAlgorithms=curve25519-sha256`이 필요하다(runbook 참고). 호스트의 `vendor/wheels`는 boundary-contracts **1.3.0**이어야 한다(1.2.0이면 `EvaluationReport`가 없어 모든 후보가 오류로 끝난다. 이번에 한 번 겪었다). 리포지토리 루트를 통째로 rsync하면 끊기는 일이 있어 `model_lab configs scripts tests profiles`를 폴더별로 보낸다.
```
for d in model_lab configs scripts tests profiles; do rsync -a -e "ssh -o KexAlgorithms=curve25519-sha256" ./$d/ gpu-dev:openit-gpu/model-lab/$d/; done
ssh -o KexAlgorithms=curve25519-sha256 gpu-dev 'cd ~/openit-gpu/model-lab && (setsid nohup flock -w 1800 ~/openit-gpu/.gpu.lock timeout 7200 \
  .venv/bin/python scripts/compare_base_models.py --config configs/compare/base-model-candidates.yaml \
  --schema-dir vendor/survey-schema/schemas --out runs-base-compare > base-compare.log 2>&1 &)'
# few-shot 판은 --config configs/compare/base-model-candidates-fewshot.yaml (같은 --out, 이름에 -fewshot2가 붙는다)
# 끝나면
rsync -a gpu-dev:openit-gpu/model-lab/runs-base-compare/ runs/base-compare/
```
후보 하나만: `--only qwen3-8b`. 한 후보가 실패해도(메모리 부족 등) 나머지는 계속하고 `summary.json`에 사유(`error`)를 남긴다. 모델 내려받기는 호스트의 `~/.cache`로만 간다.
