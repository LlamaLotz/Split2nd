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
    "boxing_biomech": {
        "id": "boxing_biomech",
        "title": ("Biomechanical Analysis of the Cross, Hook, and Uppercut in "
                  "Junior vs. Elite Boxers: Implications for Training and "
                  "Talent Identification"),
        "publisher": "Frontiers in Sports and Active Living (Dinu & Louis)",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC7739747/",
        "claim": ("At impact the elbow contributes most to the cross while the "
                  "shoulder contributes most to the hook and uppercut; the cross "
                  "also showed a lead/rear-foot ground-reaction-force imbalance."),
        "year": "2020",
    },
    "punch_classification": {
        "id": "punch_classification",
        "title": ("An active machine learning framework for automatic boxing "
                  "punch recognition and classification using upper limb "
                  "kinematics"),
        "publisher": "PLOS ONE (Manoharan et al.)",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12061147/",
        "claim": ("Video-only punch classification is limited by motion blur and "
                  "occlusion; synchronized IMU plus video reached roughly 92% "
                  "classification. A single-camera pose signal is a gross-action "
                  "label, not a punch-force measurement."),
        "year": "2025",
    },
    "boxing_fatigue": {
        "id": "boxing_fatigue",
        "title": ("The Role of Lower Limb Kinetics in Boxing Punches and the "
                  "Impact of Fatigue on Biomechanical Performance"),
        "publisher": "Bioengineering (Stewart et al.)",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC12729554/",
        "claim": ("A lower-body fatigue circuit reduced punch force across six "
                  "punch types (about 4.3% overall, up to about 8% in the cross "
                  "and left hook) and altered ground reaction forces; the weakest "
                  "punches declined most."),
        "year": "2025",
    },
    "anticipation": {
        "id": "anticipation",
        "title": ("A comparison of perceptual anticipation in combat sports "
                  "between experts and non-experts: A systematic review and "
                  "meta-analysis"),
        "publisher": "Frontiers in Psychology (Zhang et al.)",
        "url": "https://pmc.ncbi.nlm.nih.gov/articles/PMC9650920/",
        "claim": ("Across combat sports, experts responded faster and more "
                  "accurately to opponents' attacks. The boxing subgroup was "
                  "small and not individually significant, so treat this as a "
                  "training principle rather than a boxing-specific effect size."),
        "year": "2022",
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


BOXING_DISCLAIMER = (
    "Single-camera gross-action label. Pose geometry infers what the body did; "
    "it is not punch force, a validated punch classifier, or a head-impact "
    "measurement. Compare your own sessions, not another athlete's absolute "
    "numbers."
)


def _boxing_tip(headline: str, body: str, *ref_ids: str) -> dict[str, Any]:
    return {
        "headline": headline,
        "body": body,
        "refs": [REFS[r] for r in ref_ids if r in REFS],
        "disclaimer": BOXING_DISCLAIMER,
    }


def _action_accuracy(actions: dict[str, Any], names: tuple[str, ...]) -> float | None:
    rows = [actions[name] for name in names if name in actions]
    cues = sum(int(r.get("cues") or 0) for r in rows)
    if not cues:
        return None
    correct = sum(int(r.get("correct") or 0) for r in rows)
    return correct / cues


def boxing_feedback(stats: dict[str, Any],
                    result: dict[str, Any] | None = None) -> dict[str, Any]:
    """Pick one boxing coaching tip from the athlete's own numbers.

    Rules are ordered by how actionable they are.  Every branch carries a
    real source, and the boxing-specific caveat is explicit: this is a
    gross-action pose label, not a punch-force or head-impact measure.
    """
    stats = stats or {}
    result = result or {}
    actions = stats.get("actions") or {}
    n = int(stats.get("n") or stats.get("cues") or 0)
    accuracy = float(stats.get("accuracy") or 0.0)
    median_rt = stats.get("median_rt_ms")
    fatigue = stats.get("fatigue") or {}
    fatigue_pct = fatigue.get("pct")
    guard_drops = int(stats.get("guard_drops") or 0)
    combo_breaks = int(stats.get("combo_breaks") or 0)
    max_combo = int(stats.get("max_combo") or 0)

    cross = actions.get("cross") or {}
    cross_rt = cross.get("median_rt_ms")
    cross_acc = cross.get("accuracy")
    cross_weak = (
        (cross_rt is not None and median_rt is not None and cross_rt > median_rt + 35)
        or (cross_acc is not None and cross_acc < max(0.0, accuracy - 0.15))
        or (result.get("expected_action") == "cross" and not result.get("correct"))
    )
    if cross_weak and (cross or result.get("expected_action") == "cross"):
        rt_text = f" Your cross median is {cross_rt} ms." if cross_rt else ""
        return _boxing_tip(
            "The cross is a rear-foot punch, not just an arm punch",
            "The cross is elbow-led at impact, but the force starts with the "
            "rear-foot drive and the hip turn. Slow or inaccurate crosses are "
            "usually a sequencing problem: drive the rear foot, turn the hip, "
            "extend the elbow, then recover the hand." + rt_text,
            "boxing_biomech",
            "boxing_fatigue",
        )

    rotation_acc = _action_accuracy(
        actions, ("hook_l", "hook_r", "uppercut_l", "uppercut_r")
    )
    if (rotation_acc is not None and rotation_acc < 0.75) or (
        result.get("expected_action") in ("hook_l", "hook_r", "uppercut_l", "uppercut_r")
        and not result.get("correct")
    ):
        acc_text = (f" You landed {rotation_acc:.0%} of the hook/uppercut cues."
                    if rotation_acc is not None else "")
        return _boxing_tip(
            "Hooks and uppercuts are rotation-led",
            "At impact the shoulder contributes most to the hook and uppercut, "
            "while the elbow dominates the cross. Keep the elbow path quiet and "
            "turn the shoulder and trunk through the target instead of throwing "
            "the arm." + acc_text,
            "boxing_biomech",
        )

    if fatigue_pct is not None and fatigue_pct > 8:
        return _boxing_tip(
            "Fatigue signature: the last third got heavier",
            f"The last third of the set was {fatigue_pct:.0f}% slower than the "
            "first. A lower-body fatigue protocol reduced punch force and "
            "altered ground reaction forces across six punch types, and the "
            "weakest punches lost the most. Stop the set, rest, and keep the "
            "last third honest instead of grinding it.",
            "boxing_fatigue",
            "welford",
        )

    if median_rt is not None and median_rt > 500:
        return _boxing_tip(
            "Slow reactions: check state before adding reps",
            f"Your boxing median is {median_rt} ms. Short sleep slows reaction "
            "time and raises variability; more response choices add decision "
            "time. In combat sports, experts also pick up earlier cues and "
            "respond more accurately, but the boxing subgroup in that "
            "meta-analysis was small. Test at the same time of day and keep the "
            "cue set predictable while you rebuild speed.",
            "sleep",
            "hick",
            "anticipation",
        )

    if guard_drops >= 2:
        return _boxing_tip(
            "The guard drops when the cue changes",
            f"You lost {guard_drops} defensive cues. Keep both wrists above the "
            "shoulders and inside the head line, and watch the lead shoulder "
            "instead of the fist. Expert combat athletes pick up earlier cues "
            "and respond more accurately, which is a scanning habit before it is "
            "a speed habit.",
            "anticipation",
        )

    if (combo_breaks >= 2 or (n >= 6 and max_combo < 2)):
        return _boxing_tip(
            "Chunk the combination",
            f"You broke the run {combo_breaks} times. Two-punch chunks reduce "
            "the number of decisions per sequence: call the pair, throw the "
            "pair, reset. Reaction time grows with the number of response "
            "choices, so a smaller chunk is usually faster and cleaner.",
            "hick",
            "welford",
        )

    if n == 0:
        return _boxing_tip(
            "Warm up before the first cue",
            "Do a few minutes of light work and a handful of submaximal punches "
            "before the set. Reaction time is state-dependent; the first cue is "
            "measuring the warm-up, not the athlete.",
            "welford",
        )

    if accuracy >= 0.8:
        return _boxing_tip(
            "Accuracy first, then speed",
            f"You hit {accuracy:.0%} of {n} cues. Keep the same cue set and "
            "camera distance so the next session measures the same thing. "
            "Speed and accuracy trade against each other; adding pace before the "
            "guard is stable just moves the errors around.",
            "fitts",
            "reliability",
        )

    return _boxing_tip(
        "Build the guard before the highlight punch",
        f"You hit {accuracy:.0%} of {n} cues. Keep the action set small and the "
        "guard high. A single camera can tell a gross action from pose "
        "geometry, but it cannot see punch force; use the accuracy and reaction "
        "numbers as a training signal, not a scoreboard for power.",
        "punch_classification",
        "reliability",
    )


def references() -> list[dict[str, str]]:
    return list(REFS.values())
