# 기반 모델 후보 비교 (D60)

**상태: 측정하지 못했다.** gpu-dev(`100.108.213.38:2222`)가 SSH 연결 시간 초과로 닿지 않았다(처음 시도와 재시도 2회, 모두 `Connection timed out`). 규칙(D47)대로 멈췄다. 이 문서의 표에서 모델 카드로 확인한 부분(라이선스, 구조)은 채웠고, 측정이 필요한 칸(지표, 실제 메모리)은 **미측정**이다. 비교 스크립트와 설정은 준비되어 가짜 백엔드로 경로를 시험했다(`tests/test_compare_base_models.py`). 호스트가 켜지면 아래 "실행 방법"으로 이어서 하고 표를 채운다. 추천 1순위는 측정 전이라 정하지 않았다.

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

## 측정 (미측정)
같은 표본(파서 golden에서 50건: `RISK_SIGNAL` 사례 9건 전부 + 균등 간격), 같은 프롬프트(`render_prompt` prompt-2), 온도 0, 시드 1000, 시도 1회, 4비트 NF4, eager 어텐션. 프롬프트는 채팅 템플릿의 사용자 메시지로 넣는다(Qwen3은 thinking 끔).

| 후보 | 스키마 유효율(`schema_valid_raw`) | 값 정확도(`value_exact_match`) | 위험 신호 재현율(`risk_signal_recall`) | 항목 F(`item_precision`/`item_recall`) | 보호 개입 | 최대 GPU 메모리(4비트) | 소요 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| qwen2.5-7b | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |
| qwen3-8b | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |
| kanana-1.5-8b | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |
| qwen2.5-14b-4bit | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 | 미측정 |

지표는 `runs/base-compare/<후보>.evaluation-report.json`(EvaluationReport, 합격 판정 없음)에 있고 요약은 `summary.json`이다.

## 한계 (측정 후에도 유지)
- 평가 백엔드는 transformers이고 서빙 백엔드(vLLM, 제약 디코딩)가 아니다. 제약 디코딩이 있으면 스키마 유효율은 서빙에서 더 높을 수 있다. 이 비교의 스키마 유효율은 "제약 없이 스스로 지킨 비율"이다.
- 4비트 양자화(NF4)는 서빙의 정밀도(bf16 또는 다른 양자화)와 다르다. 후보 사이의 순서는 대체로 유지되겠지만 확인하지 못했다.
- 학습하지 않은 기반 모델이다. 이 프롬프트는 미세조정을 전제로 쓴 것이라 기반 모델의 zero-shot 점수는 미세조정 뒤의 성능과 다를 수 있다. 여기서 보는 것은 한국어 이해와 JSON 형식 준수의 출발점이다.
- 표본 50건은 순위를 가르기에 작다. 표본 수(`n`)를 같이 보고 차이가 작으면 가르지 않는다.
- 12GB에서 14B(4비트)가 올라가는지는 실행해 봐야 안다(올라가지 않으면 사유만 적고 제외한다).

## 실행 방법 (호스트가 켜진 뒤)
```
rsync -a --exclude .venv --exclude .git --exclude 'runs*' --exclude fixtures ./ gpu-dev:openit-gpu/model-lab/
ssh gpu-dev 'cd ~/openit-gpu/model-lab && (setsid nohup flock -w 1800 ~/openit-gpu/.gpu.lock timeout 7200 \
  .venv/bin/python scripts/compare_base_models.py --config configs/base-model-candidates.yaml \
  --schema-dir vendor/survey-schema/schemas --out runs-base-compare > base-compare.log 2>&1 &)'
# 끝나면
rsync -a gpu-dev:openit-gpu/model-lab/runs-base-compare/ runs/base-compare/
```
후보 하나만: `--only qwen3-8b`. 한 후보가 실패해도(메모리 부족 등) 나머지는 계속하고 `summary.json`에 사유(`error`)를 남긴다. 모델 내려받기는 호스트의 `~/.cache`로만 간다.
