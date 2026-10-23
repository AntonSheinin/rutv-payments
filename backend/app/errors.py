"""Expected reconciliation failures distinguished from unexpected programming defects."""


class ReconciliationError(ValueError):
    """Expected provider-data or payment-state inconsistency requiring review."""
