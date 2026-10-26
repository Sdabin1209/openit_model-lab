"""학습 서버 데이터셋(복사본) 검증: 모든 줄을 샘플로 바꾸고(계약 스키마 검증) 다시 서버 줄로 되돌려 원본과 글자 단위로 비교한다.

사용: python scripts/check_server_dataset.py <lora_a_*.jsonl, lora_b_*.jsonl이 있는 폴더>
서버에는 아무것도 쓰지 않는다(폴더는 읽기 전용으로 받아 온 복사본). 발화 원문은 출력하지 않는다.
종료 코드 0이면 전부 일치. 일치하지 않으면 줄 번호와 이유만 낸다."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from model_lab.transform import ConversionError, from_server_row, to_server_row  # noqa: E402

SPLITS = ("train", "val", "test")


def main(d: Path) -> int:
    bad = 0
    for role, prefix in (("LORA_A", "lora_a"), ("LORA_B", "lora_b")):
        prompts: set = set()
        scn: dict = {}
        ids: set = set()
        for sp in SPLITS:
            p = d / f"{prefix}_{sp}.jsonl"
            n = same = 0
            for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
                if not line.strip():
                    continue
                n += 1
                row = json.loads(line)
                try:
                    s, sysp = from_server_row(role, row, where=f"{p.name}:{i}")
                except ConversionError as e:
                    print("변환 실패:", e); bad += 1; continue
                prompts.add(sysp)
                scn.setdefault(s["scenario_id"], set()).add(s["split"])
                if s["sample_id"] in ids:
                    print(f"{p.name}:{i} sample_id 중복"); bad += 1
                ids.add(s["sample_id"])
                back = to_server_row(role, s, sysp)
                if json.dumps(back, ensure_ascii=False, sort_keys=True) == json.dumps(row, ensure_ascii=False, sort_keys=True) \
                        and back["messages"] == row["messages"]:
                    same += 1
                else:
                    print(f"{p.name}:{i} 되돌린 줄이 원본과 다르다"); bad += 1
            print(f"{role} {sp}: {n}줄, 글자 단위 일치 {same}")
        leaks = sum(1 for v in scn.values() if len(v) > 1)
        print(f"{role}: 시스템 프롬프트 {len(prompts)}종, 분할 사이에 걸친 scenario_id {leaks}개")
        bad += (len(prompts) != 1) + leaks
    print("OK" if not bad else f"문제 {bad}건")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(Path(sys.argv[1])))
