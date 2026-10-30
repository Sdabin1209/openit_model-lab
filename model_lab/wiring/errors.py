"""model-lab 내부 오류. 경계 호출의 실패는 BoundaryError를 그대로 쓴다."""
from __future__ import annotations

from boundary_contracts import BoundaryError  # noqa: F401  (재노출)


class PipelineError(Exception):
    """파이프라인 내부 문제(설정 위반, 단계 선행 조건, 학습 환경 없음 등). 경계 호출 실패가 아니다."""


class ConfigError(PipelineError):
    """설정 파일이 형식을 어겼다."""


class TrainerUnavailable(PipelineError):
    """학습·추론 환경(torch 등)이 없다. 코드나 설정의 결함이 아니다."""


class ConversionError(PipelineError):
    """변환할 수 없는 줄. 발화 원문을 메시지에 담지 않는다(줄 번호와 이유만)."""
