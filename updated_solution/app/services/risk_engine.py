"""
Risk assessment engine.

The risk engine takes a ``Position`` object and determines the current
risk state based on configurable health factor thresholds. It returns
an enumeration representing the severity and an optional human‑readable
recommendation for how to mitigate the risk.
"""

from enum import Enum
from typing import Optional, Tuple

from app.core.types import Position


class RiskLevel(str, Enum):
    """Enumeration of risk severities."""

    NONE = "NONE"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    LIQUIDATION_RISK = "LIQUIDATION_RISK"


class RiskEngine:
    """Evaluate a position and assign a risk level based on thresholds."""

    def __init__(
        self,
        warning_threshold: float = 1.20,
        critical_threshold: float = 1.10,
        liquidation_threshold: float = 1.00,
    ) -> None:
        self.warning_threshold = warning_threshold
        self.critical_threshold = critical_threshold
        self.liquidation_threshold = liquidation_threshold

    def assess(self, position: Position) -> Tuple[RiskLevel, Optional[str]]:
        """Assess the position and return a risk level and recommendation.

        This method implements the following logic:

        - If the address has no debt (``debt_value_usd <= 0``), the health
          factor is not relevant and the position is considered safe. A
          ``RiskLevel.NONE`` result suppresses alerts.
        - If the collateral value is zero but debt exists, this is an
          anomalous and highly risky state (no collateral to back the
          borrow). A ``RiskLevel.CRITICAL`` alert is emitted with an
          appropriate message.
        - If the health factor is missing (``None``) despite debt being
          present, treat the situation conservatively as a warning. The
          lack of a health factor could indicate an RPC/API error and
          warrants user attention.
        - Otherwise, use the configured thresholds for liquidation,
          critical and warning levels based on the health factor.

        Parameters
        ----------
        position: Position
            The aggregated position to evaluate.

        Returns
        -------
        Tuple[RiskLevel, Optional[str]]
            A tuple containing the risk level and an optional action
            recommendation. ``RiskLevel.NONE`` indicates that no alert
            should be sent.
        """
        # No debt: nothing to assess
        if position.debt_value_usd <= 0:
            return RiskLevel.NONE, None
        # Debt but zero collateral: anomalous state
        if position.collateral_value_usd <= 0 and position.debt_value_usd > 0:
            return (
                RiskLevel.CRITICAL,
                "Обнаружена аномалия: залог отсутствует, но есть долг. Стабилизируйте позицию как можно скорее.",
            )
        # Debt exists but health factor missing
        if position.health_factor is None:
            return (
                RiskLevel.WARNING,
                "Не удалось определить health factor при наличии долга. Проверьте данные или соединение.",
            )
        hf = position.health_factor
        if hf < self.liquidation_threshold:
            return (
                RiskLevel.LIQUIDATION_RISK,
                "Позиция близка к ликвидации. Добавьте залог или погасите часть долга.",
            )
        if hf < self.critical_threshold:
            return (
                RiskLevel.CRITICAL,
                "Уровень риска критический. Рекомендуется увеличить залог или частично погасить долг.",
            )
        if hf < self.warning_threshold:
            return (
                RiskLevel.WARNING,
                "Уровень риска повышенный. Подумайте о добавлении залога или снижении долга.",
            )
        return RiskLevel.NONE, None