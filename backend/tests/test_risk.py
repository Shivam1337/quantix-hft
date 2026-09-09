from app.domain.risk import RiskConfig, RiskEngine


def test_basis_breach_is_immediate_unwind():
    decision = RiskEngine(RiskConfig(basis_threshold_bps=50)).evaluate(100, 51, 0)
    assert decision.should_unwind
    assert decision.basis_breach
    assert not decision.funding_flip


def test_funding_flip_requires_two_negative_hours():
    engine = RiskEngine(RiskConfig(negative_hours_to_unwind=2))
    assert not engine.evaluate(-1, 0, 1).should_unwind
    decision = engine.evaluate(-1, 0, 2)
    assert decision.should_unwind
    assert decision.funding_flip


def test_unconfirmed_funding_does_not_trigger_flip():
    decision = RiskEngine(RiskConfig(negative_hours_to_unwind=2)).evaluate(None, 0, 2)
    assert not decision.should_unwind
    assert not decision.funding_flip


def test_alert_only_mode_does_not_unwind():
    decision = RiskEngine(RiskConfig(auto_unwind=False)).evaluate(100, 100, 0)
    assert decision.basis_breach
    assert not decision.should_unwind
