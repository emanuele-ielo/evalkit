from __future__ import annotations

import unittest

from evalkit.judge.vote import aggregate_attempt, aggregate_turn, reaggregate_verdict
from evalkit.schemas import (
    DeterministicCheck,
    JudgeSettings,
    TokenUsage,
    VoteRecord,
    VoteVerdictV4,
)


def _votes(scores: tuple[int, int, int, int]) -> list[VoteRecord]:
    grounding, completeness, clauses, customer_care = scores
    return [
        VoteRecord(
            index=index,
            verdict=VoteVerdictV4(
                claims=[],
                grounding_score=grounding,
                facts=[],
                completeness_coverage="FULL",
                completeness_score=completeness,
                clauses=[],
                clauses_score=clauses,
                customer_care_score=customer_care,
                taxonomy=["missing_clause"] if clauses < 4 else ["none"],
                explanation="one condition is missing" if clauses < 4 else "complete",
                suggestion="add the missing condition" if clauses < 4 else "",
            ),
            usage=TokenUsage(input_tokens=10, output_tokens=2),
        )
        for index in range(3)
    ]


class PassPolicyTest(unittest.TestCase):
    def test_two_is_visible_but_not_a_veto_with_current_floor(self) -> None:
        turn = aggregate_turn(0, _votes((5, 5, 2, 5)), [], min_criterion_score=2)

        self.assertEqual(turn.score, 4.25)
        self.assertTrue(turn.passed)
        self.assertEqual(next(c.score for c in turn.criteria if c.name == "clauses"), 2)
        self.assertIn("missing_clause", turn.taxonomy)

    def test_same_scores_fail_under_historical_floor(self) -> None:
        turn = aggregate_turn(0, _votes((5, 5, 2, 5)), [], min_criterion_score=3)

        self.assertEqual(turn.score, 4.25)
        self.assertFalse(turn.passed)

    def test_one_or_a_mechanical_blocker_still_vetoes(self) -> None:
        catastrophic = aggregate_turn(0, _votes((5, 5, 1, 5)), [], min_criterion_score=2)
        blocked = aggregate_turn(
            0,
            _votes((5, 5, 5, 5)),
            [DeterministicCheck(name="answer_present", passed=False, detail="no answer")],
            min_criterion_score=2,
        )

        self.assertEqual(catastrophic.score, 4.0)
        self.assertFalse(catastrophic.passed)
        self.assertEqual(blocked.score, 2.0)
        self.assertFalse(blocked.passed)

    def test_reaggregate_reuses_votes_without_changing_score_or_tokens(self) -> None:
        old_turn = aggregate_turn(0, _votes((5, 5, 2, 5)), [], min_criterion_score=3)
        settings = JudgeSettings(
            model="judge-model",
            votes=3,
            reasoning_effort="medium",
            rubric_version="v4",
            required_criteria=["grounding", "completeness", "clauses", "customer_care"],
            pass_threshold=4,
            min_criterion_score=3,
        )
        old = aggregate_attempt(
            campaign="campaign",
            scenario="scenario",
            short="short",
            round_=1,
            result_id="result",
            turns=[old_turn],
            judge=settings,
            official_passed=False,
            duration_ms=123,
        )
        old.judged_at = "2026-08-12T00:00:00+00:00"

        updated = reaggregate_verdict(old, min_criterion_score=2)

        self.assertFalse(old.passed)
        self.assertTrue(updated.passed)
        self.assertEqual(updated.score, old.score)
        self.assertEqual(updated.tokens, old.tokens)
        self.assertEqual(updated.turns[0].votes, old.turns[0].votes)
        self.assertEqual(updated.judged_at, old.judged_at)
        self.assertEqual(updated.duration_ms, old.duration_ms)
        self.assertEqual(updated.agreement, "ours_pass_official_fail")

    def test_missing_historical_floor_keeps_old_default(self) -> None:
        settings = JudgeSettings.model_validate(
            {
                "model": "judge-model",
                "votes": 3,
                "reasoning_effort": "medium",
                "rubric_version": "v1",
                "required_criteria": ["grounding"],
            }
        )

        self.assertEqual(settings.min_criterion_score, 3)


if __name__ == "__main__":
    unittest.main()
