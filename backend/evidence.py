"""Curated, citable feedback.

No runtime web search and no model-generated references: every tip maps
to a fixed source below. That is the difference between "science-backed"
and a hallucinated bibliography.
"""
from __future__ import annotations

from typing import Any

REFS: dict[str, dict[str, str]] = {
    "wa100": {
        "id": "wa100",
        "title": "IAAF Sprint Start Research Project: Is the 100 ms limit still valid?",
        "publisher": "World Athletics",
        "url": "https://worldathletics.org/news/news/iaaf-sprint-start-research-project-is-the-100",
        "claim": "Responses faster than 100 ms are treated as anticipation rather than reaction.",
        "year": "2010",
    },
    "sleep": {
        "id": "sleep",
        "title": "A meta-analysis of the impact of short-term sleep deprivation on cognitive variables",
        "publisher": "Psychological Bulletin (Lim & Dinges)",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC3290659/",
        "claim": "Short-term sleep loss reliably slows reaction time and increases variability.",
        "year": "2010",
    },
    "caffeine": {
        "id": "caffeine",
        "title": "ISSN position stand: caffeine and exercise performance",
        "publisher": "Journal of the International Society of Sports Nutrition",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7777221/",
        "claim": "3-6 mg/kg about 60 min before exercise is the documented ergogenic window; effects on simple reaction time are less consistent than on vigilance.",
        "year": "2021",
    },
    "caffeine_rt": {
        "id": "caffeine_rt",
        "title": "Caffeine and cognitive functions in sports: a systematic review and meta-analysis",
        "publisher": "Nutrients (Grgic et al.)",
        "url": "https://www.mdpi.com/2072-6643/13/3/868",
        "claim": "Across pooled trials, caffeine did not significantly change simple or choice reaction time in athletes.",
        "year": "2021",
    },
    "hick": {
        "id": "hick",
        "title": "On the rate of gain of information",
        "publisher": "Quarterly Journal of Experimental Psychology (Hick)",
        "url": "https://doi.org/10.1080/17470215208416600",
        "claim": "Reaction time grows roughly logarithmically with the number of response choices.",
        "year": "1952",
    },
    "fitts": {
        "id": "fitts",
        "title": "The information capacity of the human motor system in controlling the amplitude of movement",
        "publisher": "Journal of Experimental Psychology (Fitts)",
        "url": "https://doi.org/10.1037/h0055392",
        "claim": "Pushing for speed trades against accuracy; speed-accuracy is one coupled system.",
        "year": "1954",
    },
    "reliability": {
        "id": "reliability",
        "title": "Reliability and validity of simple and complex reaction speed tests in mixed martial arts athletes using the BlazePod system",
        "publisher": "Baltic Journal of Sport and Health Sciences",
        "url": "https://www.balticsportscience.com/cgi/viewcontent.cgi?article=2028&context=journal",
        "claim": "Light-based reaction devices show moderate reliability, so compare within an athlete instead of across devices or norms.",
        "year": "2023",
    },
    "welford": {
        "id": "welford",
        "title": "Reaction Times",
        "publisher": "Academic Press (Welford)",
        "url": "https://www.sciencedirect.com/book/97801274450",
        "claim": "Reaction time is a composite of stimulus processing, decision and motor execution, each affected differently by fatigue and practice.",
        "year": "1980",
    },
}

DISCLAIMER = (
    "Single-athlete data. Device reliability is moderate and this is not a "
    "clinical measurement. Compare your own median across sessions, not a "
    "single rep and not a population norm."
)


def _tip(headline: str, body: str, *ref_ids: str) -> dict[str, Any]:
    return {
        "headline": headline,
        "body": body,
        "refs": [REFS[r] for r in ref_ids if r in REFS],
        "disclaimer": DISCLAIMER,
    }


def feedback(stats: dict[str, Any], rep: dict[str, Any] | None = None,
             new_pb: bool = False, session_stats: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pick one evidence-backed tip from the athlete's own numbers."""
    rep = rep or {}
    session_stats = session_stats or {}
    grade = rep.get("grade")
    rt = rep.get("rt_ms")

    if grade == "F":
        return _tip(
            "That was anticipation, not reaction",
            "Anything under 100 ms is treated as a false start in athletics because the "
            "visual-to-motor chain cannot complete that fast. Reset, breathe, and wait for "
            "the lights to actually go out.",
            "wa100",
        )

    if grade == "M":
        return _tip(
            "No response inside the window",
            "A miss is information: it usually means the stimulus was not attended to. "
            "Reaction time is a vigilance task, and short sleep or a long session both "
            "degrade it before speed does.",
            "sleep",
            "welford",
        )

    n = int(stats.get("n") or 0)
    median_rt = stats.get("median_ms")
    spread = stats.get("mad_ms")
    trend = session_stats.get("trend_pct")

    if n >= 5 and spread is not None and spread > 60:
        return _tip(
            "Your consistency is the thing to train",
            f"Your median is {median_rt:.0f} ms but the median absolute deviation is "
            f"{spread:.0f} ms. Variability rises with fatigue and time-on-task. Shorten "
            "sets to 8-10 reps and rest 60-90 s between them.",
            "sleep",
            "welford",
        )

    if trend is not None and trend > 15:
        return _tip(
            "Fatigue signature: you slowed down inside the set",
            f"The last reps were {trend:.0f}% slower than the first ones. That is a "
            "within-session fatigue pattern, not a talent ceiling. Stop the set and come "
            "back after a real rest.",
            "welford",
            "reliability",
        )

    if new_pb:
        return _tip(
            "New personal best - now make it repeatable",
            "A single fast rep is a sample, not a level. Keep the same rest, stance and "
            "camera distance so the next session measures the same thing. Light-based "
            "reaction devices are only moderately reliable across setups.",
            "reliability",
        )

    if median_rt is not None and median_rt > 350:
        return _tip(
            "Slow band: check arousal before adding reps",
            "Above roughly 350 ms on a simple visual task, the usual lever is state, not "
            "skill. Short sleep slows reaction time with a moderate effect, and it raises "
            "variability at the same time. Test at the same time of day.",
            "sleep",
            "welford",
        )

    if median_rt is not None:
        return _tip(
            "Compare you to you",
            f"Median {median_rt:.0f} ms over {n} reps. The useful signal is the direction "
            "of this number across sessions with the same setup, not how it compares to a "
            "population norm. Absolute values include display and camera latency.",
            "reliability",
        )

    return _tip(
        "Warm up before you test",
        "Do a few minutes of light aerobic work and a handful of submaximal reps before "
        "the set. Reaction time is state-dependent; a cold first rep is measuring the "
        "warm-up, not the athlete.",
        "welford",
    )


def references() -> list[dict[str, str]]:
    return list(REFS.values())
