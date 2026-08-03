"""Authorization-first orchestration for deterministic lease calculations."""

from __future__ import annotations

from pms_common.security import AuthorizationContext, AuthorizationService, Permission

from pms_rule_engine.engine import RuleCalculationEngine
from pms_rule_engine.models import (
    CalculationResult,
    LeaseCalculationRequest,
    ReplayCalculationRequest,
)
from pms_rule_engine.repository import PostgresRuleRepository


class RuleCalculationService:
    """Calculate only from approved rules and persist the complete evidence trace."""

    def __init__(
        self,
        repository: PostgresRuleRepository,
        context: AuthorizationContext,
        engine: RuleCalculationEngine,
    ) -> None:
        self._repository = repository
        self._context = context
        self._engine = engine

    def calculate(self, request: LeaseCalculationRequest) -> CalculationResult:
        AuthorizationService().require_permission(
            self._context,
            Permission.RULE_CALCULATION,
        )
        rules = self._repository.load_approved_rules(request)
        result = self._engine.calculate(request, rules)
        return self._repository.save(request, result)

    def replay(
        self,
        calculation_id: str,
        *,
        replay: ReplayCalculationRequest,
    ) -> CalculationResult:
        original = self._repository.load_input(calculation_id)
        request = original.model_copy(
            update={
                "mode": replay.mode,
                "as_recorded_at": replay.as_recorded_at,
            }
        )
        rules = self._repository.load_approved_rules(request)
        result = self._engine.calculate(
            request,
            rules,
            replay_of_calculation_id=calculation_id,
        )
        return self._repository.save(request, result)
