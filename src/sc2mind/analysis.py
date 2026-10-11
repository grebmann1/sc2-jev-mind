"""Evidence-first post-game reviews. Runs after SC2 exits, never in on_step.

Model suggestions remain review artifacts. Only code-derived observations enter
game memory; neither this module nor its reports modify the gameplay policy.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import tempfile
from collections import Counter
from dataclasses import fields
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from .memory import RESULTS, Episode, MatchContext, MemoryStore, general_patterns
from .state import Snapshot

VERSION = 1
MAX_TRACE_BYTES = 32 * 1024 * 1024
INSTRUCTIONS = """Review this completed StarCraft II bot match as an engineering analyst.
Use only the supplied measurements and recorded decisions. Every finding must cite
existing evidence IDs. Distinguish observations from hypotheses; do not invent
Evidence IDs must be EXACT keys from allowed_evidence_ids (the facts dictionary).
Do not prefix them with facts., evidence., or general_patterns.
units lost, idle production, visibility, causal explanations, or baseline comparisons.
Proposed choices, accepted choices, and issued investments are different stages.
High resource banks may reflect reservations; observed army drops are not kill counts.
Recorded plan rationales are model claims, not measurements of floating gas or idle
production. Respect implementation_capabilities: this bot does not swap attached
add-ons. Proposed/applied choice differences do not measure add-on oscillations,
plan churn, or production utilization. Use direct measures, or propose instrumentation.
Game-time answer age is not wall-clock API latency. Missing/low-coverage data is unknown.
Prefer general mechanisms (supply forecasting, spending, scouting, production,
defense, plan stability, provider reliability) over map-, opponent-, or timestamp-specific
build orders. A victory can still expose weaknesses. One match cannot prove improvement.
Prefer changes driven by current capacity, pending work and threats; do not invent
fixed add-on caps, worker caps, timers or resource thresholds from this one game.
Cross-match signals hold engine/controller/policy/difficulty/speed/time limit fixed.
Respect sample diversity and repeated conditions; never upgrade a provisional signal
to a validated lesson. Model suggestions are advisory hypotheses, not instructions.
For each hypothesis propose one reversible change, an ablation against the unchanged
baseline, held-out seeds, at least two maps and all three opponent races, with memory
disabled or frozen identically. Specify success metrics and possible regressions.
Keep the review concise and actionable. Return the requested structured review.
"""


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    category: Literal[
        "supply", "economy", "production", "scouting", "combat", "reasoning", "reliability"
    ]
    priority: Literal["high", "medium", "low"]
    hypothesis: str = Field(min_length=1, max_length=700)
    evidence_ids: list[str] = Field(
        min_length=1,
        max_length=8,
        description="Exact existing keys from allowed_evidence_ids; for example metrics.observed_supply_blocked_s. No added prefixes.",
    )
    proposed_change: str = Field(min_length=1, max_length=700)
    validation: str = Field(min_length=1, max_length=1000)
    uncertainty: str = Field(min_length=1, max_length=500)


class Review(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    summary: str = Field(min_length=1, max_length=1000)
    strengths: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(max_length=5)
    findings: list[Finding] = Field(max_length=6)
    missing_evidence: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(
        max_length=6
    )


def grounded_schema(packet: dict) -> type[Review]:
    """Constrain decoding to actual measured evidence, rather than free-text IDs."""
    ids = Enum("EvidenceId", {f"E{i}": key for i, key in enumerate(packet["facts"])}, type=str)
    finding = create_model(
        "GroundedFinding",
        __base__=Finding,
        evidence_ids=(list[ids], Field(min_length=1, max_length=8)),
    )
    return create_model(
        "GroundedReview", __base__=Review, findings=(list[finding], Field(max_length=6))
    )


def _atomic(path: Path, text: str) -> None:
    name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as f:
            name = f.name
            f.write(text)
            f.flush()
        os.replace(name, path)
    finally:
        if name:
            Path(name).unlink(missing_ok=True)


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def evidence_packet(run_dir: Path, record: dict | None, memory: MemoryStore | None) -> dict:
    summary = json.loads((run_dir / "result.json").read_text())
    if summary.get("result") not in RESULTS:
        raise ValueError("Analysis requires a completed game, not a simulation or failed launch")
    path = run_dir / "trace.jsonl"
    if path.stat().st_size > MAX_TRACE_BYTES:
        raise ValueError("Trace exceeds the 32 MB analysis limit")
    data = path.read_bytes()
    if len(data) > MAX_TRACE_BYTES:
        raise ValueError("Trace exceeds the 32 MB analysis limit")
    rows = [json.loads(line) for line in data.splitlines() if line.strip()]
    if not all(isinstance(row, dict) for row in rows):
        raise ValueError("Invalid trace event")
    counts = Counter(row.get("kind") for row in rows)
    config = next((row for row in rows if row.get("kind") == "config"), {})
    snapshots = [row for row in rows if row.get("kind") == "snapshot"]
    ends = [row for row in rows if row.get("kind") == "end"]
    if not ends or ends[-1].get("result") != summary["result"] or not snapshots:
        raise ValueError("No observed completed match, or trace/result mismatch")
    warnings = [
        "No unit-loss, idle-production, or bot-visibility instrumentation; these cannot be inferred from outcome."
    ]
    match_id = summary.get("match_id", run_dir.name)
    if record is None and memory:
        try:
            record = memory.get(match_id)
        except Exception as error:
            warnings.append(f"Episode retrieval unavailable ({type(error).__name__}).")
    if record is not None and (
        record.get("match_id") != match_id or record.get("result") != summary["result"]
    ):
        raise ValueError("Episode does not match this completed run")
    if record is None:
        # Sparse old traces are useful snapshots, not continuous measurements.
        context = (
            MatchContext(**summary["context"])
            if "context" in summary
            else MatchContext(
                summary.get("map", "Unknown"),
                summary.get("enemy", "Unknown").title(),
                summary.get("difficulty", "Unknown"),
                "unknown-historical-controller",
                config.get("engine", "Unknown"),
                summary.get("realtime", False),
                summary.get("game_time_limit", 1200),
                seed=summary.get("seed"),
            )
        )
        episode = Episode(match_id, context, summary.get("opening", "balanced"))
        names = {field.name for field in fields(Snapshot)}
        for snap in snapshots:
            episode.observe(Snapshot(**{k: v for k, v in snap.items() if k in names}), {})
        record = episode.record(summary["result"], {})
        warnings.append(
            "Metrics reconstructed from sparse trace snapshots; gaps over five seconds are excluded. Supply/bank durations are lower bounds."
        )
    history = []
    if memory:
        try:
            history = memory.review_history(MatchContext(**record["context"]))
        except Exception as error:
            warnings.append(f"Cross-match history unavailable ({type(error).__name__}).")
    history = [record, *(r for r in history if r["match_id"] != record["match_id"])][:50]
    patterns = general_patterns(history)
    metrics = record["metrics"]
    facts = {
        "match.result": summary["result"],
        "sample.matches": patterns["matches"],
        "sample.conditions": patterns["conditions"],
        "sample.maps": patterns["maps"],
        "sample.races": patterns["races"],
        "sample.seeds": patterns["seeds"],
    }
    for key, value in metrics.items():
        if _number(value) or value is None:
            facts[f"metrics.{key}"] = value
    providers = {}
    for name in ("s1", "s2"):
        accepted = [row for row in rows if row.get("kind") == name]
        ages = sorted(
            row["age_s"] for row in accepted if _number(row.get("age_s")) and row["age_s"] >= 0
        )
        providers[name] = {
            "collected_answers": len(accepted),
            "errors": counts[f"{name}_error"],
            "stale_answers": counts[f"{name}_discarded"],
            "p95_game_age_s": round(ages[max(0, math.ceil(0.95 * len(ages)) - 1)], 2)
            if ages
            else None,
            "proposed_applied_differences": sum(
                row.get("applied") != [row.get("invest"), row.get("stance")] for row in accepted
            )
            if name == "s1"
            else None,
        }
        for key, value in providers[name].items():
            facts[f"providers.{name}.{key}"] = value
    for key, signal in patterns["signals"].items():
        facts[f"signals.{key}"] = signal
    investments = Counter(row.get("invest") for row in rows if row.get("kind") == "investment")
    facts["execution.issued_investments"] = dict(investments)
    plans = [
        {
            k: row[k]
            for k in (
                "t",
                "summary",
                "reasoning",
                "aggression",
                "target_workers",
                "attack_at_army_supply",
                "marauder_ratio",
                "investment_priorities",
            )
            if k in row
        }
        for row in rows
        if row.get("kind") == "s2"
    ][-8:]
    for i, plan in enumerate(plans):
        facts[f"plans.{i}"] = plan
    # Point observations are evidence without implying continuous coverage.
    sample_indexes = sorted({round(i * (len(snapshots) - 1) / 5) for i in range(6)})
    snapshot_fields = (
        "time_s",
        "minerals",
        "gas",
        "workers",
        "townhalls",
        "army",
        "army_supply",
        "barracks",
        "techlabs",
        "reactors",
        "supply_used",
        "supply_cap",
        "stim",
    )
    for i, index in enumerate(sample_indexes):
        facts[f"snapshots.{i}"] = {
            k: snapshots[index][k] for k in snapshot_fields if k in snapshots[index]
        }
    all_plans = [row for row in rows if row.get("kind") == "s2"]
    for field in ("target_workers", "attack_at_army_supply", "marauder_ratio"):
        values = [row[field] for row in all_plans if field in row]
        facts[f"planning.{field}_changes"] = sum(
            a != b for a, b in zip(values, values[1:], strict=False)
        )
    if metrics.get("observed_time_s") is None:
        warnings.append(
            "Older episode: observation coverage, mineral-bank duration, and first upgrade/expansion times were not instrumented."
        )
    elif metrics["observed_time_s"] < metrics.get("duration_s", 0) * 0.8:
        warnings.append(
            "Less than 80% of match time has dense observations; durations are lower bounds."
        )
    if patterns["matches"] < 3 or len(patterns["maps"]) < 2 or len(patterns["races"]) < 2:
        warnings.append(
            "Insufficient match diversity for general improvement claims; keep findings provisional."
        )
    if not patterns["matches"]:
        warnings.append(
            "Short match (under 180 seconds): useful for diagnostics, excluded from general gameplay signals."
        )
    return {
        "match_id": record["match_id"],
        "context": record["context"],
        "opening": record["opening"],
        "facts": facts,
        "general_patterns": patterns,
        "providers": providers,
        "recent_observations": record.get("timeline", [])[-12:],
        "implementation_capabilities": {
            "units": ["Marine", "Marauder"],
            "addons": "Constructs Tech Labs/Reactors on eligible bare Barracks; no lifting, swapping or conversion of attached add-ons.",
            "execution": "SCVs/supply/infantry production and defensive overrides run in code; models select legal investments, stance and plan goals.",
            "answer_differences": "Proposed versus accepted choices can differ due to confidence, legality or retained decisions. Accepted stance is recorded before the per-frame defensive override.",
        },
        "warnings": warnings,
        "source_hash": hashlib.sha256(
            data
            + json.dumps(
                {k: v for k, v in summary.items() if k != "analysis_status"}, sort_keys=True
            ).encode()
        ).hexdigest(),
    }


def measured_review(packet: dict) -> Review:
    facts, findings = packet["facts"], []
    experiments = "Compare one change against the unchanged baseline using held-out seeds, two maps and all three races, with the same engine/difficulty/speed and frozen or disabled learning. Track wins, duration, supply blocks, resource banks and provider failures; reject regressions."
    if facts.get("metrics.observed_supply_blocked_s", 0) >= 10:
        findings.append(
            Finding(
                category="supply",
                priority="high",
                hypothesis="Supply interruptions may be delaying continuous worker and infantry production.",
                evidence_ids=["metrics.observed_supply_blocked_s", "signals.supply_blocks"],
                proposed_change="Test forecasting depot needs from current production capacity and pending supply rather than a fixed game timestamp.",
                validation=experiments,
                uncertainty="Observed blocked seconds do not prove how many units were delayed or that changing depot timing improves outcomes.",
            )
        )
    if facts.get("metrics.observed_mineral_bank_s", 0) >= 30:
        findings.append(
            Finding(
                category="economy",
                priority="medium",
                hypothesis="Persistent mineral banks may expose a spending or production-capacity bottleneck.",
                evidence_ids=["metrics.observed_mineral_bank_s", "signals.mineral_bank"],
                proposed_change="Instrument idle barracks and reserved investment costs, then test capacity-based spending after explaining legitimate reservations.",
                validation=experiments,
                uncertainty="A bank of at least 800 minerals can reflect expansion/upgrade reservations; current observations do not establish idle production.",
            )
        )
    if sum(p["errors"] + p["stale_answers"] for p in packet["providers"].values()):
        findings.append(
            Finding(
                category="reliability",
                priority="high",
                hypothesis="Provider errors or stale answers may reduce the useful coverage of model decisions.",
                evidence_ids=[
                    "providers.s1.errors",
                    "providers.s2.errors",
                    "providers.s1.stale_answers",
                    "providers.s2.stale_answers",
                ],
                proposed_change="Measure wall-clock request latency and useful reply rate; compare bounded timeouts/cadence against the current fallback policy.",
                validation=experiments,
                uncertainty="Game-time answer age differs from API wall latency; no causal effect on result has been measured.",
            )
        )
    return Review(
        summary=f"{facts['match.result']}: {len(findings)} measured signals to investigate. Suggestions require controlled evaluation before policy changes.",
        strengths=[],
        findings=findings,
        missing_evidence=packet["warnings"][:6],
    )


def render(report: dict) -> str:
    review = Review.model_validate(report["review"])
    sample = report["evidence"]["general_patterns"]
    lines = [
        "# Post-game analysis",
        "",
        f"Match: {report['match_id']} · Review: {report['status']}",
        "",
        review.summary,
        "",
        "## Evidence and scope",
        "",
        f"{sample['matches']} eligible matches, {sample['conditions']} distinct map/race/seed conditions, {len(sample['maps'])} maps, {len(sample['races'])} races. At most 50 recent compatible matches; games under 180 seconds are excluded from recurring gameplay signals.",
        "",
        "Findings are hypotheses. Candidate status indicates evidence diversity for a controlled test, never a proven improvement. Model suggestions do not change bot code or the opening selector.",
        "",
        "| Evidence ID | Recorded value |",
        "| --- | --- |",
    ]
    for key, value in report["evidence"]["facts"].items():
        display = json.dumps(value, ensure_ascii=False).replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{key}` | {display} |")
    if review.strengths:
        lines += ["", "## Strengths", "", *[f"- {s}" for s in review.strengths]]
    lines += ["", "## Improvement hypotheses", ""]
    lines += [
        "Evaluation protocol: change one mechanism at a time; compare with an unchanged baseline on at least two maps, all three opponent races, and at least three held-out seeds. Keep engine, difficulty, game speed, and memory frozen or disabled equally. Improvement requires repeatable outcome gains with no material regressions; individual model suggestions and diagnostic thresholds are not validated tuning values.",
        "",
    ]
    if not review.findings:
        lines.append(
            "No measured bottleneck met the diagnostic thresholds. More instrumentation and varied matches may reveal one."
        )
    for finding in review.findings:
        lines += [
            f"### {finding.priority.title()} · {finding.category}",
            "",
            finding.hypothesis,
            "",
            f"Evidence: {', '.join(finding.evidence_ids)}.",
            "",
            f"Proposed change: {finding.proposed_change}",
            "",
            f"Validation: {finding.validation}",
            "",
            f"Uncertainty: {finding.uncertainty}",
            "",
        ]
    lines += [
        "## Missing evidence and limits",
        "",
        *[
            f"- {s}"
            for s in dict.fromkeys(report["evidence"]["warnings"] + review.missing_evidence)
        ],
    ]
    if report.get("error_type"):
        lines += [
            "",
            f"Model review unavailable ({report['error_type']}); measured analysis retained.",
        ]
    return "\n".join(lines) + "\n"


async def analyze_match(
    run_dir: Path,
    *,
    record: dict | None = None,
    memory: MemoryStore | None = None,
    offline: bool = False,
    model: str = "gpt-5",
    timeout_s: float = 120,
    client=None,
    force: bool = False,
) -> dict:
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("Analysis timeout must be positive and finite")
    run_dir = Path(run_dir)
    packet = await asyncio.to_thread(evidence_packet, run_dir, record, memory)
    target = run_dir / "analysis.json"
    if target.exists() and not force:
        existing = json.loads(target.read_text())
        if (
            existing.get("schema_version") == VERSION
            and existing.get("source_hash") == packet["source_hash"]
        ):
            return existing
    report = {
        "schema_version": VERSION,
        "match_id": packet["match_id"],
        "source_hash": packet["source_hash"],
        "created_at": datetime.now(UTC).isoformat(),
        "status": "offline" if offline else "pending",
        "model": None if offline else model,
        "evidence": packet,
        "review": measured_review(packet).model_dump(mode="json"),
        "evaluation_protocol": {
            "minimum_maps": 2,
            "opponent_races": ["Terran", "Zerg", "Protoss"],
            "minimum_held_out_seeds": 3,
            "change_one_mechanism": True,
            "baseline": "unchanged code",
            "memory": "frozen or disabled equally",
            "hold_fixed": ["engine", "difficulty", "speed", "time_limit"],
            "require": "Repeatable outcome improvement and no material regressions; hypotheses are not validated tuning values.",
        },
    }

    async def persist():
        await asyncio.to_thread(
            _atomic, target, json.dumps(report, indent=2, allow_nan=False) + "\n"
        )
        await asyncio.to_thread(_atomic, run_dir / "analysis.md", render(report))

    await persist()  # Keep facts even if the provider fails or the process is interrupted.
    if offline:
        return report
    owns_client = client is None
    try:
        async with asyncio.timeout(timeout_s):
            if owns_client:
                from openai import AsyncOpenAI

                client = AsyncOpenAI(timeout=timeout_s, max_retries=0)
            payload = json.dumps(
                {**packet, "allowed_evidence_ids": list(packet["facts"])}, allow_nan=False
            )
            if len(payload.encode()) > 64 * 1024:
                report["failure_reason"] = "input_size_limit"
                raise ValueError("Review input exceeds 64 KB")
            response = await client.responses.parse(
                model=model,
                instructions=INSTRUCTIONS,
                input=payload,
                text_format=grounded_schema(packet),
                max_output_tokens=12000,
                **({"reasoning": {"effort": "low"}} if model.startswith("gpt-5") else {}),
            )
            if response.output_parsed is None:
                report["failure_reason"] = "unparsed_response"
                raise ValueError("No structured post-game review returned")
            review = Review.model_validate(response.output_parsed.model_dump(mode="json"))
            if any(ref not in packet["facts"] for f in review.findings for ref in f.evidence_ids):
                report["failure_reason"] = "evidence_reference_mismatch"
                raise ValueError("Review cited evidence that was not measured")
            report.update(status="complete", review=review.model_dump(mode="json"))
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__)
    finally:
        if owns_client and client is not None:
            try:
                await client.close()
            except Exception:
                pass
    await persist()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Review a completed match without launching SC2.")
    parser.add_argument("run_dir", type=Path)
    parser.add_argument(
        "--offline", action="store_true", help="Measured report only; no model call"
    )
    parser.add_argument(
        "--force", action="store_true", help="Replace an existing review; may call the model again"
    )
    parser.add_argument("--memory-dir", default=os.environ.get("SC2MIND_MEMORY_DIR", "memory"))
    args = parser.parse_args(argv)
    try:
        report = asyncio.run(
            analyze_match(
                args.run_dir,
                memory=MemoryStore(Path(args.memory_dir) / "matches.sqlite3"),
                offline=args.offline,
                model=os.environ.get("OPENAI_MODEL", "gpt-5"),
                timeout_s=float(os.environ.get("ANALYSIS_TIMEOUT_S", "120")),
                force=args.force,
            )
        )
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Cannot analyze match: {error}")
        return 1
    print(f"Post-game analysis: {report['status']} · {args.run_dir / 'analysis.md'}")
    return 1 if report["status"] in {"failed", "pending"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
