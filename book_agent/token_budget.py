"""Context-window budgeting utilities."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenBudget:
    working_limit: int
    prompt_tokens: int
    glossary_tokens: int
    source_tokens: int
    expected_output_tokens: int
    reserve_tokens: int

    @property
    def allocated_tokens(self) -> int:
        """Return the total allocation within the working limit."""
        return (
            self.prompt_tokens
            + self.glossary_tokens
            + self.source_tokens
            + self.expected_output_tokens
            + self.reserve_tokens
        )

    @property
    def unallocated_tokens(self) -> int:
        """Return unused tokens inside the working limit."""
        return self.working_limit - self.allocated_tokens


def calculate_source_budget(
    working_limit: int,
    prompt_tokens: int,
    glossary_tokens: int,
    expected_output_tokens: int,
    reserve_tokens: int,
) -> int:
    """Calculate source capacity after all other allocations are reserved."""
    values = {
        "working_limit": working_limit,
        "prompt_tokens": prompt_tokens,
        "glossary_tokens": glossary_tokens,
        "expected_output_tokens": expected_output_tokens,
        "reserve_tokens": reserve_tokens,
    }
    if any(value < 0 for value in values.values()):
        raise ValueError("token allocations cannot be negative")
    source_tokens = working_limit - sum(
        value for name, value in values.items() if name != "working_limit"
    )
    if source_tokens <= 0:
        raise ValueError("token reservations leave no capacity for source text")
    return source_tokens


def validate_budget(budget: TokenBudget, model_context: int) -> None:
    """Validate a complete working budget against the model context."""
    if model_context <= 0:
        raise ValueError("model_context must be positive")
    if budget.working_limit <= 0:
        raise ValueError("working_limit must be positive")
    allocations = (
        budget.prompt_tokens,
        budget.glossary_tokens,
        budget.source_tokens,
        budget.expected_output_tokens,
        budget.reserve_tokens,
    )
    if any(value < 0 for value in allocations):
        raise ValueError("token allocations cannot be negative")
    if budget.allocated_tokens > budget.working_limit:
        raise ValueError("token allocations exceed the working limit")
    if budget.working_limit > model_context:
        raise ValueError("working limit exceeds the model context")

