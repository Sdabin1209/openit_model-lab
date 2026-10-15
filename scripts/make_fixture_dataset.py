"""golden에서 시험용 데이터셋 저장소를 만든다: python scripts/make_fixture_dataset.py <출력 디렉터리> [dataset_id] [version] [max_cases]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tests.doubles.fixtures import build_dataset  # noqa: E402

dest = Path(sys.argv[1])
m = build_dataset(dest, sys.argv[2] if len(sys.argv) > 2 else "fx-golden", sys.argv[3] if len(sys.argv) > 3 else "v1",
                  max_cases=int(sys.argv[4]) if len(sys.argv) > 4 else None)
print(m["dataset_id"], m["version"], [(f["path"], f["sample_count"]) for f in m["files"]])
