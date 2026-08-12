"""Our rubric — written from scratch, not the platform's.

Three properties the platform judge lacked, and the reason this file exists:

1. **The tool payloads are in the prompt.** The official judge graded grounding
   without ever seeing what the tools returned, which is why its number was
   pessimistic (9/43 blind vs 18/43 once the payload was shown).
2. **Criteria are separate.** Grounding, task resolution, conditions and customer care are
   judged and reported independently, so a majority is taken per criterion
   instead of on one all-or-nothing verdict — the stable way to vote when runs
   vary (σ ≈ 2.3 scenarios per round on this suite).
3. **Every accusation carries evidence.** Each claim is tied to a verbatim quote
   and a locator into the payload, which is what makes a verdict debuggable (and
   what the dashboard links to).

Bump `RUBRIC_VERSION` on any semantic change: verdicts record the version they
were produced with, and reports refuse to mix versions silently.
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..schemas import AttemptView, DeterministicCheck, ToolCallView, TurnView

# v4 (2026-08-12): customer-care answers no longer cite corpus provenance and
# LOB validation is no longer model-facing or graded. Underspecified requests
# are judged conversationally: one focused clarification can be the complete
# answer for that turn. Historical v1-v3 verdicts remain readable on disk.
RUBRIC_VERSION = "v4"

# Per-call payload budget in the prompt. Payloads here run to ~31 KB; the cap is
# generous on purpose and truncation is always announced to the judge.
PAYLOAD_CHAR_CAP = 60_000

SYSTEM_PROMPT = """\
You are the grader in an offline evaluation of a knowledge-base-grounded support agent.
You judge ONE turn: what the user asked, what the agent's tools returned, and what the
agent answered. You return a structured judgement — no prose outside the schema.

## The one rule that outranks the others
The TOOL PAYLOADS section is the agent's entire admissible universe of facts for this
turn. A statement of fact that the payloads do not support is a defect even when it is
true in the real world, plausible, or common knowledge. Conversely, do not penalise
wording, tone, ordering, formatting, or the agent's choice of which tool to call.

## You score, you do not pass/fail
Each criterion gets an integer 1–5. The scale is about **consequence for the person who
asked**, not about how many defects you counted:

- **5** — no defect on this criterion.
- **4** — minor defects that do not change what the user would do next.
- **3** — a real defect, but the answer remains usable.
- **2** — the defect makes the answer useless or misleading on this criterion. An answer
  that invents nothing but also tells the user nothing they asked for lands here: being
  harmless is not the same as being useful.
- **1** — severe: contradicts the payload, fabricates a fact, or does not
  answer at all.

Use the whole range. Reserve 5 for genuinely clean work and 1 for real damage; a middling
answer is a 3, not a 2 out of caution.

## Underspecified requests
Before scoring, decide whether the user supplied the subject needed to choose among
materially different answers. For a message such as "A chi posso scrivere?", "Quanto
costa?" or "Come faccio?" with no usable subject in the conversation, one concise,
focused clarification question is the correct and complete customer-care answer for
that turn. Do not require a tool call, do not force reference facts that assume an
unstated intent, and do not tag the clarification as evasive or incomplete. A short
message that does name its subject (for example "costo cambio SIM") is not ambiguous.

Legacy reference text may still mention source versions, citations, provenance or a
generic "confirm with TIM" LOB disclaimer. Those requirements are obsolete in v4:
exclude them from completeness and clauses, and never reward or penalise them.

## Criteria (score each independently)

1. GROUNDING — decompose the agent's answer into atomic factual claims: procedures,
   channels, names of portals/apps, prices, thresholds, eligibility and dates.
   For each claim set:
   - SUPPORTED: the payload states it. Quote it verbatim in `evidence_quote` and point
     to it in `evidence_locator` (e.g. `search_vera#0.results[3].answer`).
   - PARTIAL: the payload supports part of it, or the agent generalised beyond what is
     written while staying compatible with it.
   - UNSUPPORTED: nothing in the payload supports it. `evidence_quote` must be "".
   - CONTRADICTED: the payload says otherwise. Quote the contradicting text.
   Ignore conversational filler ("posso aiutarti con altro?") and offers to help — those
   are not factual claims. `grounding_score`: 5 when every claim is SUPPORTED; 4 with only
   PARTIAL claims; 3 when one peripheral claim is UNSUPPORTED; 2 when an UNSUPPORTED claim
   is central to the answer; 1 when any claim is CONTRADICTED.

2. TASK RESOLUTION / COMPLETENESS — use the typed TURN CONTRACT when present; otherwise
   take the REFERENCE FACTS (and, when present, the REFERENCE ANSWER) as the list of things
   this answer owed the user. A `response_mode: clarify` contract means the focused question
   named in `required_obligations` is the asked fact; branch answers and `optional_facts` are
   not owed yet. A `response_mode: answer` contract makes `required_obligations` the gold and
   explicitly excludes `optional_facts` from scoring.
   First separate out meta sentences: pure behaviour phrases carry no fact — legacy LOB
   disclaimers, source/citation phrases, inability statements ("non posso verificare"), greetings,
   offers to help, connectives. List each such reference item in `facts` with a `note`
   that STARTS with `meta:true` and exclude it from every count below; do not let a
   present disclaimer raise the score or an absent one lower it here.
   For each real fact: FULL (conveyed, any wording), PARTIAL (hinted, incomplete, or
   hedged), MISS (absent). `evidence_quote` quotes the agent's own words for FULL/PARTIAL,
   "" for MISS. Exactly one fact is the one the user directly asked for: start its `note`
   with `asked:true`.
   Coverage is proportional over the non-meta facts: FULL counts 1, PARTIAL counts 0.5,
   MISS counts 0; coverage = sum ÷ number of non-meta facts.
   `completeness_coverage` = FULL when every non-meta fact is FULL, MISS when every one is
   MISS, otherwise PARTIAL. `completeness_score` follows the proportion — do NOT cap at 3
   for one secondary MISS:
   - 5 = every non-meta fact is FULL.
   - 4 = coverage ≥ 0.8 AND the asked fact is FULL or PARTIAL.
   - 3 = coverage ≥ 0.5 AND the asked fact is FULL or PARTIAL.
   - 2 = the asked fact is MISS — including a refusal or hedge on a question the payload
     answers — or coverage < 0.5.
   - 1 = nothing the user asked for was conveyed.

3. CONDITIONS / CLAUSES — use `required_conditions` from the typed TURN CONTRACT when
   present; otherwise inspect prose caveats. A clause is a condition, caveat or prerequisite that the
   payload attaches IN PROSE to something the answer actually talked about: validity
   conditions, "only if", required confirmations, costs that apply, who may request it.
   Two hard rules decide whether a clause row is valid at all:
   - Every clause row's `note` MUST start with the payload locator of the text stating the
     condition, e.g. `loc:search_vera#0.results[3].body — …`. A row you cannot anchor with
     a locator is invalid — do not write it.
   - NEVER list a clause about a topic the answer never treated. Omitting a whole topic is
     a completeness defect and is already priced there; punishing the same omission again
     under clauses is double counting, and it is forbidden.
   `pending_channel_validation` is enforced in code and reported under DETERMINISTIC
   FINDINGS — do not re-grade it here. Ignore `needs_lob_validation` entirely.
   Status per clause: PRESENT, PARTIAL, ABSENT, or NOT_REQUIRED when the payload attaches
   no such condition to what was asked. `clauses_score` is anchored to the counts:
   - 5 = no clause was required, or every required clause is PRESENT.
   - 4 = one or more clauses are PARTIAL and none is ABSENT.
   - 3 = exactly one ABSENT clause, of minor consequence (regardless of PARTIALs).
   - 2 = two or more ABSENT clauses, or one ABSENT clause that could cost the user money
     or a failed procedure.
   - 1 = the answer actively contradicts a condition in the payload.
   With zero ABSENT and zero PARTIAL rows the score is 5 — never lower.

4. CUSTOMER CARE — judge the user-facing delivery independently from factual coverage.
   The answer must sound like a concise, natural TIM customer-care reply in Italian and
   lead with the useful next step. It must never mention sources, versions, documents,
   pages, knowledge bases, corpus rows, retrieval, tools or internal systems. A focused
   clarification question is excellent customer care when the turn contract says clarify.
   `customer_care_score`:
   - 5 = natural, focused and operational; no internal/source framing.
   - 4 = useful but slightly verbose, stiff or repetitive.
   - 3 = noticeably chatbot-like or unfocused, while still usable.
   - 2 = speaks like a research/KB bot, mentions sources or versions, or buries the next step.
   - 1 = wrong language, unprofessional, or dominated by internal/debug content.
   Add `mentions_internal_sources` whenever source/version/document/KB framing appears;
   add `poor_customer_care` when this score is 3 or lower.

## Verdict
There is no pass/fail field — the four scores are the verdict. When evidence is
inconclusive, score down rather than guessing upward: an unverifiable answer is not a
good answer.
`explanation` is at most 30 words naming the decisive defect (or why it scored well).
`suggestion` is one actionable sentence for whoever fixes the agent; "" when every score
is 5. `taxonomy` lists every failure tag that applies, or ["none"] when nothing is wrong.

## Discipline
- Quote only text that literally appears in the payload or the answer. Never paraphrase
  inside `evidence_quote`; never invent a locator.
- Keep quotes short — one sentence is usually enough.
- The DETERMINISTIC FINDINGS section lists checks already decided in code. Do not
  re-litigate them; use them as context.
- Answer content may be in any language; judge meaning, not translation.
"""


_INTERNAL_PAYLOAD_KEYS = {
    "source",
    "source_version",
    "source_doc",
    "source_page",
    "source_page_or_sheet",
    "source_sheet",
    "needs_lob_validation",
    "logs",
}
_OBSOLETE_NOTE = re.compile(r"needs_lob_validation|cita(?:re)?\b|source\.version|dato da confermare con TIM", re.IGNORECASE)
_LEGACY_REFERENCE_TAIL = re.compile(r"\s*Provenance expectation\b.*", re.IGNORECASE | re.DOTALL)
_LEGACY_SOURCE_SENTENCE = re.compile(
    r"\s*(?:Dato valido secondo.*?fonti TIM|(?:È|E|Sono|Dato|Dati)[^.]*?da confermare con TIM)\.?",
    re.IGNORECASE,
)


def _customer_payload(node: Any) -> Any:
    """Remove legacy ingestion/provenance metadata before the v4 judge sees it."""
    if isinstance(node, dict):
        cleaned: dict[str, Any] = {}
        for key, value in node.items():
            if key in _INTERNAL_PAYLOAD_KEYS:
                continue
            if key in {"notes", "agent_notes"} and isinstance(value, list):
                cleaned[key] = [note for note in value if not (isinstance(note, str) and _OBSOLETE_NOTE.search(note))]
            else:
                cleaned[key] = _customer_payload(value)
        return cleaned
    if isinstance(node, list):
        return [_customer_payload(value) for value in node]
    return node


def _customer_reference(text: str) -> str:
    """Project historical encyclopedic gold into the customer-facing v4 contract."""
    text = _LEGACY_REFERENCE_TAIL.sub("", text)
    return _LEGACY_SOURCE_SENTENCE.sub("", text).strip()


def _customer_author_notes(text: str) -> str:
    if "Provenance is graded" in text or "needs_lob_validation" in text:
        return ""
    return text


def _format_payload(call: ToolCallView) -> str:
    payload = call.output if call.output is not None else call.output_raw
    if isinstance(payload, str):
        try:
            decoded = json.loads(payload)
        except (TypeError, ValueError):
            decoded = payload
        payload = _customer_payload(decoded)
    else:
        payload = _customer_payload(payload)
    try:
        text = json.dumps(payload, ensure_ascii=False, indent=1)
    except (TypeError, ValueError):
        text = str(payload)
    note = ""
    if len(text) > PAYLOAD_CHAR_CAP:
        text = text[:PAYLOAD_CHAR_CAP]
        note = f"\n… [TRUNCATED for the prompt at {PAYLOAD_CHAR_CAP} chars of {call.output_bytes} total]"
    if call.truncated:
        note += "\n… [the platform itself marked this payload as truncated]"
    args = json.dumps(call.args or {}, ensure_ascii=False)
    header = f"[{call.index}] tool `{call.name}` called with {args}"
    if call.declared_mock:
        header += " (payload was mocked by the scenario"
        if call.matches_mock is False:
            header += ", and differs from the frozen mock"
        header += ")"
    return f"{header}\npayload:\n{text}{note}"


def _format_deterministic(checks: list[DeterministicCheck]) -> str:
    if not checks:
        return "(none)"
    lines = []
    for check in checks:
        state = "PASS" if check.passed else "FAIL"
        hits = f" hits={check.hits}" if check.hits else ""
        blocking = "" if check.blocking else " (advisory)"
        lines.append(f"- {check.name}: {state}{blocking} — {check.detail}{hits}")
    return "\n".join(lines)


def build_vote_prompt(
    view: AttemptView,
    turn: TurnView,
    checks: list[DeterministicCheck],
    *,
    include_author_notes: bool = True,
) -> tuple[str, str]:
    """Return `(system_prompt, user_prompt)` for one vote on one turn."""
    parts: list[str] = []

    scenario_line = f"scenario `{view.scenario}` (round {view.round})"
    if view.scenario_name:
        scenario_line += f" — {view.scenario_name}"
    parts.append(f"## CONTEXT\n{scenario_line}\nchannel: {view.channel or 'chat'}\nturn: {turn.index + 1} of {len(view.turns)}")

    if turn.index > 0:
        history: list[str] = []
        for previous in view.turns[: turn.index]:
            if previous.user_message:
                history.append(f"user: {previous.user_message}")
            if previous.agent_text:
                history.append(f"agent: {previous.agent_text}")
        if history:
            parts.append("## EARLIER TURNS (context only, not graded here)\n" + "\n".join(history))

    parts.append(f"## USER MESSAGE\n{turn.user_message or '(none recorded)'}")

    if turn.tool_calls:
        payloads = "\n\n".join(_format_payload(call) for call in turn.tool_calls)
    else:
        payloads = "(the agent called no tool in this turn — nothing supports any factual claim)"
    parts.append(f"## TOOL PAYLOADS (the only admissible source of facts)\n{payloads}")

    parts.append(f"## AGENT ANSWER (graded)\n{turn.agent_text or '(empty answer)'}")

    contract = turn.expected.metadata.get("evalkit_v4")
    if isinstance(contract, dict):
        parts.append("## TURN CONTRACT (authoritative v4 obligations)\n" + json.dumps(contract, ensure_ascii=False, indent=1))
    if turn.expected.reference_response:
        parts.append(
            "## REFERENCE ANSWER (a good answer, not the only one)\n"
            + _customer_reference(turn.expected.reference_response)
        )
    if turn.expected.expected_output:
        parts.append(
            "## REFERENCE FACTS (what the answer owed the user)\n"
            + _customer_reference(turn.expected.expected_output)
        )
    if include_author_notes and turn.expected.turn_prompt:
        author_notes = _customer_author_notes(turn.expected.turn_prompt)
        if author_notes:
            parts.append(
                "## SCENARIO AUTHOR NOTES (domain context — the criteria in your instructions take precedence)\n"
                + author_notes
            )

    parts.append(f"## DETERMINISTIC FINDINGS (already decided in code)\n{_format_deterministic(checks)}")
    parts.append("Return only the structured judgement.")

    return SYSTEM_PROMPT, "\n\n".join(parts)


def prompt_fingerprint(system_prompt: str, user_prompt: str) -> dict[str, Any]:
    """Small record of what a vote was asked, for the verdict's audit trail."""
    return {
        "rubric_version": RUBRIC_VERSION,
        "system_chars": len(system_prompt),
        "user_chars": len(user_prompt),
    }
