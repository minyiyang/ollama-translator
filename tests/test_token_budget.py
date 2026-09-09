import pytest

from book_agent.token_budget import TokenBudget, calculate_source_budget, validate_budget


class TokenBudgetTests:
    def test_budget_properties(self) -> None:
        budget = TokenBudget(100_000, 10_000, 5_000, 40_000, 40_000, 5_000)
        assert budget.allocated_tokens == 100_000
        assert budget.unallocated_tokens == 0

    def test_calculate_source_budget(self) -> None:
        assert calculate_source_budget(100_000, 10_000, 5_000, 40_000, 5_000) == 40_000

    def test_calculate_source_budget_rejects_negative_values(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            calculate_source_budget(100_000, -1, 5_000, 40_000, 5_000)

    def test_calculate_source_budget_requires_positive_capacity(self) -> None:
        with pytest.raises(ValueError, match="no capacity"):
            calculate_source_budget(10, 3, 3, 3, 1)

    def test_validate_budget_accepts_default_budget(self) -> None:
        budget = TokenBudget(100_000, 10_000, 5_000, 40_000, 40_000, 5_000)
        validate_budget(budget, 131_072)

    def test_validate_budget_rejects_overallocation(self) -> None:
        budget = TokenBudget(100, 30, 30, 30, 30, 0)
        with pytest.raises(ValueError, match="exceed"):
            validate_budget(budget, 200)

    def test_validate_budget_rejects_context_overflow(self) -> None:
        budget = TokenBudget(100, 20, 20, 20, 20, 20)
        with pytest.raises(ValueError, match="model context"):
            validate_budget(budget, 99)

    def test_validate_budget_rejects_nonpositive_limits(self) -> None:
        budget = TokenBudget(0, 0, 0, 0, 0, 0)
        with pytest.raises(ValueError, match="model_context"):
            validate_budget(budget, 0)
        with pytest.raises(ValueError, match="working_limit"):
            validate_budget(budget, 1)

    def test_validate_budget_rejects_negative_allocation(self) -> None:
        budget = TokenBudget(100, -1, 10, 10, 10, 10)
        with pytest.raises(ValueError, match="negative"):
            validate_budget(budget, 100)


