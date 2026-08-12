"""Judge orchestration: views on disk → verdicts on disk.

Re-runnable by design. `evalkit judge <campaign>` skips attempts that already
carry a verdict from the current rubric version, so re-judging a campaign with a
new rubric is one command and costs only the attempts it actually re-grades —
the Fase 5e re-judge experiment, as a first-class operation.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Iterable, Sequence

from ..config import Config, load_llm_credentials
from ..deterministic import check_turn
from ..normalize import normalize_attempt
from ..schemas import (
    AttemptRef,
    AttemptVerdict,
    AttemptView,
    CampaignManifest,
    JudgeSettings,
    VoteRecord,
)
from ..store import CampaignStore, utcnow
from .llm import JudgeLLM, JudgeLLMError
from .rubric import RUBRIC_VERSION, build_vote_prompt
from .vote import CRITERIA, aggregate_attempt, aggregate_turn

ProgressFn = Callable[[dict], None]


def load_view(store: CampaignStore, ref: AttemptRef, campaign_id: str) -> AttemptView | None:
    """Assemble the normalized view for one attempt from what is on disk."""
    result = store.read_result(ref.scenario, ref.round)
    if not isinstance(result, dict):
        return None
    return normalize_attempt(
        campaign=campaign_id,
        scenario=ref.scenario,
        short=ref.short,
        round_=ref.round,
        result=result,
        trace=store.read_trace(ref.scenario, ref.round),
        activity=store.read_activity(ref.scenario, ref.round),
    )


async def judge_attempt(
    view: AttemptView,
    llm: JudgeLLM,
    *,
    votes: int,
    required: Iterable[str] = CRITERIA,
    reasoning_effort: str = "medium",
    llm_semaphore: asyncio.Semaphore | None = None,
    include_author_notes: bool = True,
    pass_threshold: float = 4.0,
    min_criterion_score: int = 3,
) -> AttemptVerdict:
    """Grade every turn of one attempt with `votes` independent votes."""
    settings = JudgeSettings(
        model=llm.model,
        votes=votes,
        reasoning_effort=reasoning_effort,
        rubric_version=RUBRIC_VERSION,
        required_criteria=list(required),
        pass_threshold=pass_threshold,
        min_criterion_score=min_criterion_score,
    )
    started = time.monotonic()
    turn_verdicts = []

    for turn in view.turns:
        checks = check_turn(turn)
        system_prompt, user_prompt = build_vote_prompt(
            view, turn, checks, include_author_notes=include_author_notes
        )

        async def one_vote(index: int) -> VoteRecord:
            from ..schemas import VoteVerdictV4  # local import keeps the schema in one place

            if llm_semaphore is not None:
                async with llm_semaphore:
                    return await _run_vote(llm, system_prompt, user_prompt, VoteVerdictV4, index)
            return await _run_vote(llm, system_prompt, user_prompt, VoteVerdictV4, index)

        records = await asyncio.gather(*(one_vote(index) for index in range(votes)))
        turn_verdicts.append(
            aggregate_turn(
                turn.index,
                list(records),
                checks,
                required=required,
                pass_threshold=pass_threshold,
                min_criterion_score=min_criterion_score,
            )
        )

    return aggregate_attempt(
        campaign=view.campaign,
        scenario=view.scenario,
        short=view.short,
        round_=view.round,
        result_id=view.result_id,
        turns=turn_verdicts,
        judge=settings,
        official_passed=view.official_passed,
        duration_ms=(time.monotonic() - started) * 1000,
    )


async def _run_vote(llm: JudgeLLM, system_prompt: str, user_prompt: str, schema, index: int) -> VoteRecord:
    try:
        outcome = await llm.vote(system_prompt, user_prompt, schema)
    except JudgeLLMError as exc:
        return VoteRecord(index=index, error=str(exc))
    except Exception as exc:  # transport failure after LangChain's own retries
        return VoteRecord(index=index, error=f"{type(exc).__name__}: {exc}")
    return VoteRecord(index=index, verdict=outcome.value, usage=outcome.usage, duration_ms=outcome.duration_ms)


async def judge_campaign(
    store: CampaignStore,
    manifest: CampaignManifest,
    config: Config,
    *,
    scenarios: Sequence[str] | None = None,
    rounds: Sequence[int] | None = None,
    votes: int | None = None,
    model: str | None = None,
    force: bool = False,
    limit: int | None = None,
    progress: ProgressFn | None = None,
) -> dict:
    """Judge (or re-judge) a campaign's attempts. Returns a small summary."""
    votes = votes or config.llm.votes
    model = model or config.llm.model
    credentials = load_llm_credentials(config.llm, model)
    llm = JudgeLLM(
        credentials=credentials,
        model=model,
        reasoning_effort=config.llm.reasoning_effort,
        max_retries=config.llm.max_retries,
        requests_per_second=config.llm.requests_per_second,
        max_output_tokens=config.llm.max_output_tokens,
    )
    llm_semaphore = asyncio.Semaphore(max(1, config.llm.concurrency))

    wanted = [
        ref
        for ref in manifest.attempts
        if (scenarios is None or ref.scenario in scenarios or ref.short in scenarios)
        and (rounds is None or ref.round in rounds)
    ]

    todo: list[AttemptRef] = []
    skipped = 0
    for ref in wanted:
        if not store.attempt_paths(ref.scenario, ref.round).exists():
            continue
        existing = store.read_verdict(ref.scenario, ref.round)
        if existing and not force and existing.rubric_version == RUBRIC_VERSION and existing.judge.votes >= votes:
            skipped += 1
            continue
        todo.append(ref)
    if limit is not None:
        todo = todo[:limit]

    settings = JudgeSettings(
        model=model,
        votes=votes,
        reasoning_effort=config.llm.reasoning_effort,
        rubric_version=RUBRIC_VERSION,
        required_criteria=list(config.required_criteria),
        pass_threshold=config.pass_threshold,
        min_criterion_score=config.min_criterion_score,
    )
    manifest.judge = settings
    store.save(manifest)
    store.append_event(
        {
            "type": "judge_started",
            "attempts": len(todo),
            "skipped": skipped,
            "model": model,
            "votes": votes,
            "rubric": RUBRIC_VERSION,
        }
    )
    if progress:
        progress(
            {
                "event": "judge_started",
                "attempts": len(todo),
                "skipped": skipped,
                "judge": llm.describe(),
                "votes": votes,
            }
        )

    lock = asyncio.Lock()
    state = {"done": 0, "passed": 0, "failed": 0, "errors": 0}

    async def work(ref: AttemptRef) -> AttemptVerdict | None:
        view = load_view(store, ref, manifest.id)
        if view is None:
            return None
        verdict = await judge_attempt(
            view,
            llm,
            votes=votes,
            required=config.required_criteria,
            reasoning_effort=config.llm.reasoning_effort,
            llm_semaphore=llm_semaphore,
            pass_threshold=config.pass_threshold,
            min_criterion_score=config.min_criterion_score,
        )
        async with lock:
            store.write_verdict(verdict)
            ref.our_passed = verdict.passed
            ref.our_score = verdict.score
            ref.status = "judged"
            ref.error = None if not verdict.errors else "; ".join(verdict.errors[:2])
            store.update_attempt(manifest, ref)
            state["done"] += 1
            state["passed"] += 1 if verdict.passed else 0
            state["failed"] += 0 if verdict.passed else 1
            state["errors"] += 1 if verdict.errors else 0
            store.append_event(
                {
                    "type": "attempt_judged",
                    "scenario": ref.scenario,
                    "short": ref.short,
                    "round": ref.round,
                    "passed": verdict.passed,
                    "official_passed": verdict.official_passed,
                    "taxonomy": verdict.taxonomy,
                    "tokens": verdict.tokens.model_dump(),
                }
            )
            if progress:
                progress(
                    {
                        "event": "attempt_judged",
                        "scenario": ref.short,
                        "round": ref.round,
                        "passed": verdict.passed,
                        "official": verdict.official_passed,
                        "explanation": verdict.explanation[:120],
                        "done": state["done"],
                        "total": len(todo),
                    }
                )
        return verdict

    verdicts = await asyncio.gather(*(work(ref) for ref in todo), return_exceptions=True)

    failures = [v for v in verdicts if isinstance(v, BaseException)]
    tokens = None
    for verdict in verdicts:
        if isinstance(verdict, AttemptVerdict):
            tokens = verdict.tokens if tokens is None else tokens + verdict.tokens

    summary = {
        "judged": state["done"],
        "passed": state["passed"],
        "failed": state["failed"],
        "with_errors": state["errors"],
        "skipped": skipped,
        "crashed": len(failures),
        "judge": llm.describe(),
        "votes": votes,
        "rubric_version": RUBRIC_VERSION,
        "tokens": tokens.model_dump() if tokens else None,
        "finished_at": utcnow(),
    }
    store.append_event({"type": "judge_finished", **{k: v for k, v in summary.items() if k != "judge"}})
    return summary
