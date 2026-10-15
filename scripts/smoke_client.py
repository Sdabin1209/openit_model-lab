"""스모크: 실행 중인 서비스에 패키지를 게시하고 읽어 온다."""
import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from boundary_contracts import canonical
from boundary_contracts.clients import HttpModelRegistry
from tests.test_registry import make

url, wt, rt = sys.argv[1:4]
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    m, ad, rep = make(td)
    w, r = HttpModelRegistry(url, wt), HttpModelRegistry(url, rt)
    assert w.publish(m, ad, rep) == m and w.publish(m, ad, rep) == m          # 멱등
    assert r.get_manifest("pkg-a", "v1") == m and r.list_ids() == (["pkg-a"], None)
    out = r.open_adapter("pkg-a", "v1", dest=td / "o")
    assert canonical.directory_hash(out) == m["adapter_hash"] and r.get_eval_report("eval-1") == rep
print("client ok")
