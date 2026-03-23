import pytest
from app.services.intent_scoring import (
    calculate_intent_score,
    get_score_description,
    should_trigger_alert,
)


class TestIntentScoring:
    """Tests for the intent scoring engine (Module 05)."""

    def test_cold_prospect_no_signals(self):
        """Prospect with no signals should score 0 (cold)."""
        result = calculate_intent_score()
        assert result["score"] == 0.0
        assert result["tier"] == "cold"

    def test_funding_signal_adds_40(self):
        """Funding signal should add 40 points (Series A/B) = warm tier."""
        result = calculate_intent_score(funding_signal=True)
        assert result["score"] == 40.0
        assert result["tier"] == "warm"  # 40 falls in warm range (20-49)

    def test_funding_plus_hiring_is_hot(self):
        """Funding + hiring = hot tier."""
        result = calculate_intent_score(funding_signal=True, hiring_signal=True)
        assert result["score"] == 60.0  # 40 + 20
        assert result["tier"] == "hot"

    def test_warm_tier_threshold(self):
        """Score 20-49 = warm tier."""
        result = calculate_intent_score(
            hiring_signal=True,
            linkedin_pain=True,
        )
        assert result["score"] == 30.0  # 20 + 10
        assert result["tier"] == "warm"

    def test_switching_intent_review_adds_25(self):
        """Switching intent in reviews adds 25 points."""
        result = calculate_intent_score(
            review_signal=True,
            review_switching_intent=True,
        )
        assert result["score"] == 25.0  # review_switching takes priority

    def test_frustrated_linkedin_adds_15(self):
        """Frustrated LinkedIn sentiment adds 15 points."""
        result = calculate_intent_score(linkedin_frustrated=True)
        assert result["score"] == 15.0
        assert result["tier"] == "cold"

    def test_website_visit_adds_30(self):
        """Website visit is the highest quality signal (+30)."""
        result = calculate_intent_score(website_visit=True)
        assert result["score"] == 30.0
        assert result["tier"] == "warm"

    def test_technographic_gap_adds_5(self):
        """Technographic gap adds 5 points."""
        result = calculate_intent_score(technographic_signal=True)
        assert result["score"] == 5.0
        assert result["tier"] == "cold"

    def test_all_signals_max_score(self):
        """All signals combined = maximum score."""
        result = calculate_intent_score(
            funding_signal=True,
            hiring_signal=True,
            review_signal=True,
            review_switching_intent=True,
            linkedin_frustrated=True,
            technographic_signal=True,
            website_visit=True,
        )
        # funding(40) + hiring(20) + review_switching(25) + linkedin_frustrated(15) + tech(5) + website(30)
        assert result["score"] == 135.0
        assert result["tier"] == "hot"

    def test_score_trend_rising(self):
        """Score increasing = rising trend."""
        result = calculate_intent_score(
            funding_signal=True,
            existing_score=10.0,
        )
        assert result["trend"] == "rising"

    def test_score_trend_falling(self):
        """Score decreasing = falling trend."""
        result = calculate_intent_score(
            existing_score=50.0,
        )
        assert result["trend"] == "falling"

    def test_score_breakdown_recorded(self):
        """Score breakdown should track each signal."""
        result = calculate_intent_score(
            funding_signal=True,
            hiring_signal=True,
        )
        assert result["score_breakdown"]["funding"] == 40
        assert result["score_breakdown"]["hiring"] == 20
        assert result["score_breakdown"]["review"] == 0


class TestScoreDescriptions:
    """Tests for score descriptions."""

    def test_description_with_funding(self):
        """Description should mention funding signal."""
        breakdown = {"funding": 40, "hiring": 0, "linkedin": 0}
        desc = get_score_description(40, breakdown)
        assert "Raised funding" in desc
        assert "+40" in desc

    def test_description_no_signals(self):
        """No signals should show a monitoring message."""
        desc = get_score_description(0, {})
        assert "No strong signals" in desc

    def test_description_multiple_signals(self):
        """Should list all active signals."""
        breakdown = {"funding": 40, "hiring": 20, "review": 15}
        desc = get_score_description(75, breakdown)
        assert "funding" in desc.lower()
        assert "hiring" in desc.lower()
        assert "competitor" in desc.lower()  # "competitor issues" is the description


class TestAlertTriggers:
    """Tests for alert triggering logic."""

    def test_tier_change_to_hot_triggers_alert(self):
        """Moving to hot tier should trigger alert."""
        result = should_trigger_alert(prev_score=40, new_score=55, tier_changed=True)
        assert result["should_alert"] is True
        assert any(t["type"] == "tier_hot" for t in result["triggers"])

    def test_tier_change_to_warm_triggers_alert(self):
        """Moving to warm tier should trigger alert."""
        result = should_trigger_alert(prev_score=10, new_score=25, tier_changed=True)
        assert result["should_alert"] is True
        assert any(t["type"] == "tier_warm" for t in result["triggers"])

    def test_score_spike_triggers_alert(self):
        """Score jump of 15+ triggers spike alert."""
        result = should_trigger_alert(prev_score=20, new_score=40, tier_changed=False)
        assert result["should_alert"] is True
        assert any(t["type"] == "score_spike" for t in result["triggers"])

    def test_small_change_no_alert(self):
        """Small score changes shouldn't trigger alerts."""
        result = should_trigger_alert(prev_score=30, new_score=32, tier_changed=False)
        assert result["should_alert"] is False
        assert len(result["triggers"]) == 0
