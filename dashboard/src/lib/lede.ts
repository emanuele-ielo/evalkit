/**
 * Turns a verdict into a one-line diagnosis for the top of the inspector.
 *
 * Every headline is gated on the exact condition it describes, so the label is
 * a summary of the data and never a guess about it: "the agent never queried
 * its tools" only appears when the `tool_call_present` check actually failed.
 * The prose underneath is always the judge's own explanation — this file adds a
 * title, not content.
 */

import type { AttemptVerdict, TurnVerdict } from '../types'

export interface Lede {
  tone: 'good' | 'warn' | 'bad'
  kicker: string
  headline: string
}

/** Mechanical failures name themselves; these are the phrasings. */
const MECHANICAL: Record<string, { kicker: string; headline: string }> = {
  tool_call_present: { kicker: 'bad tool use', headline: 'The agent never queried its tools' },
  tools_allowed: { kicker: 'bad tool use', headline: 'The agent called a tool it was not allowed to use' },
  declared_mocks_called: { kicker: 'mock never hit', headline: 'A declared mock was never called' },
  must_not_contain: { kicker: 'forbidden content', headline: 'The answer contains a string the scenario forbids' },
  answer_present: { kicker: 'no answer', headline: 'The agent produced no user-facing answer' },
  provenance_literals: { kicker: 'provenance', headline: 'A cited version does not appear in the payload' },
  mock_payload_matches: { kicker: 'mock drift', headline: 'The tool returned something other than the declared mock' },
}

const CRITERION: Record<string, { kicker: string; low: string; mid: string }> = {
  grounding: {
    kicker: 'ungrounded',
    low: 'Claims the payload does not support',
    mid: 'Some claims reach past the payload',
  },
  completeness: {
    kicker: 'half an answer',
    low: 'The payload answered it — the answer did not',
    mid: 'Part of what was asked went unanswered',
  },
  clauses: {
    kicker: 'missing conditions',
    low: 'Required conditions were never stated',
    mid: 'A required condition is only half stated',
  },
  customer_care: {
    kicker: 'customer care',
    low: 'The reply sounds internal or research-like, not like TIM support',
    mid: 'The reply is useful but not focused customer care',
  },
  provenance: {
    kicker: 'provenance',
    low: 'Sources that do not exist in the payload',
    mid: 'The version cited does not match the row',
  },
}

/** The turn that dragged the attempt down — what the lede should describe. */
export function worstTurn(verdict: AttemptVerdict): TurnVerdict | undefined {
  if (verdict.turns.length === 0) return undefined
  return verdict.turns.reduce((worst, turn) => (turn.score < worst.score ? turn : worst))
}

export function deriveLede(verdict: AttemptVerdict): Lede {
  const turn = worstTurn(verdict)

  // 1. A blocking mechanical check overrides everything: it capped the score.
  const blocking = turn?.deterministic.find((check) => !check.passed && check.blocking)
  if (blocking) {
    const known = MECHANICAL[blocking.name]
    return {
      tone: 'bad',
      kicker: known?.kicker ?? blocking.name.replace(/_/g, ' '),
      headline: known?.headline ?? `Mechanical check failed: ${blocking.name}`,
    }
  }

  const required = (turn?.criteria ?? []).filter((criterion) => criterion.required)
  const weakest = required.length > 0 ? required.reduce((low, c) => (c.score < low.score ? c : low)) : null

  // 2. Hedging on an answerable question — the judge tagged it and completeness fell.
  if (verdict.taxonomy.includes('hedging_without_answer') && weakest && weakest.score <= 3) {
    return { tone: weakest.score <= 2 ? 'bad' : 'warn', kicker: 'hedged', headline: 'Hedged on a question the payload answers' }
  }

  // 3. Otherwise the weakest required criterion is the story.
  if (weakest && weakest.score < 4) {
    const known = CRITERION[weakest.name]
    return {
      tone: weakest.score <= 2 ? 'bad' : 'warn',
      kicker: known?.kicker ?? weakest.name.replace(/_/g, ' '),
      headline: known ? (weakest.score <= 2 ? known.low : known.mid) : `${weakest.name} scored ${weakest.score}/5`,
    }
  }

  // 4. Nothing scored below 4.
  const perfect = required.length > 0 && required.every((criterion) => criterion.score === 5)
  return {
    tone: 'good',
    kicker: 'clean',
    headline: perfect ? 'Nothing the rubric can fault' : 'Grounded, complete and correctly sourced',
  }
}
