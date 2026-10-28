"""H19: Dockerfile은 GitHub 호스트 키를 처음 본 값으로 신뢰(ssh-keyscan)하지 않고, 공개된 키를 고정한다.
기대값(지문)은 GitHub가 https://api.github.com/meta 의 ssh_key_fingerprints 로 공개한 값이며 2026-10-29에 같은 값을 `ssh-keyscan`과
`ssh-keygen -lf`로도 확인했다(두 경로가 일치). 이 시험의 코드가 만든 값이 아니다."""
import base64
import hashlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLISHED = {   # GitHub 공개 SHA256 지문
    "ssh-ed25519": "+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU",
    "ecdsa-sha2-nistp256": "p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM",
    "ssh-rsa": "uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s",
}


def fp(b64: str) -> str:
    return base64.b64encode(hashlib.sha256(base64.b64decode(b64)).digest()).decode().rstrip("=")


def test_dockerfile_does_not_trust_on_first_use():
    text = (ROOT / "Dockerfile").read_text()
    assert "ssh-keyscan" not in text
    assert "docker/github_known_hosts" in text


def test_pinned_known_hosts_match_published_fingerprints():
    lines = [l.split() for l in (ROOT / "docker" / "github_known_hosts").read_text().splitlines() if l.strip() and not l.startswith("#")]
    got = {l[1]: fp(l[2]) for l in lines if l[0] == "github.com"}
    assert got == PUBLISHED
