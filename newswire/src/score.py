"""Relevance scoring: additive keyword points, two streams, no AI.

Ported from the Circuit newswire, with one structural change: Circuit scores a single
beat, this scores TWO, because eJP's two sections apply genuinely different tests.

    major_gift   did somebody GIVE something?   giving 3 + recipient 2 + form 2 + jewish 2
    watching     did somebody CONVENE something happening now?  convening 3 + today 2 +
                                                                jewish 2 + scale 1

The weightings come from eJP's own archive — 1,102 published "What We're Watching" items
and 435 "Major Gifts" items — not from instinct. Two numbers carry the design:

  * The Jewish axis is worth 2 and cannot admit a story alone. 57% of the real Major
    Gifts section is general philanthropy with no Jewish angle whatsoever: a Gates
    Foundation grant, a Boston Ballet endowment, a $6M gift to a Lompoc theatre. A
    filter that required a Jewish angle would delete more than half the section.

  * The watching threshold is 5 and convening+today is exactly 5. So a scheduled event
    happening today clears, and general news about the same institutions does not. The
    previous collector had no such gate and sent troop deployments and a soccer crowd's
    Nazi salute to the events channel — that class is 0 of 1,102 real items.

An item lands in at most one stream. Gifts win ties: "Federation gala raises $2M" is a
gift story with an event attached, and the desk expects it in the gifts channel.

Same limitation as the original, stated plainly: this is literal word matching. It cannot
tell how central a subject is, cannot tell reporting from commentary about it, and cannot
catch a story that avoids its vocabulary. `judge` in scoring.yaml is the dormant seam.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

CONFIG_FILE = Path(__file__).resolve().parent.parent / "scoring.yaml"

# Term boundaries that tolerate the punctuation in real names: "e&", "AI", "Ma'aden",
# "2PointZero". A plain \b breaks on the ampersand and would match "ai" inside "Dubai".
_LEFT = r"(?<![A-Za-z0-9])"
# A trailing plural is allowed, so the word lists can stay singular. Without this,
# "investments" silently failed to match "investment" — a whole class of quiet misses
# that looked like vocabulary gaps and was really a matcher bug.
# Inflections, not just plurals. The original allowed a trailing s/es, which silently
# missed a whole class: "Hillel is AWARDING 65 students", "JCRC LAUNCHES its election
# series", "the foundation is COMMITTING $100 million". Measured on eJP's archive,
# adding -ed/-ing/-d lifted Major Gifts recall from 71% to 84% and events from 57% to
# 71%, at no cost in noise. A trailing "e" is dropped first so donate -> donating and
# pledge -> pledging match from the singular stem.
_RIGHT = r"(?:e|es|ed|ing|s|d)?(?![A-Za-z0-9])"


def _stem(term: str) -> str:
    """donate -> donat, so the suffix group covers donated/donating/donates."""
    t = str(term).strip()
    if len(t) > 4 and t.endswith("e") and " " not in t:
        return t[:-1]
    return t


@dataclass
class Verdict:
    score: int
    axes: list[str]
    matched: list[str]
    vetoed: str | None = None
    bypass: str | None = None
    # Axes hit by the headline alone. The anchor test uses these, not `axes`.
    title_axes: list[str] = field(default_factory=list)

    @property
    def admitted(self) -> bool:
        return self.vetoed is None and (self.bypass is not None or self.score > 0)


def _compile(terms: list[str]) -> re.Pattern:
    parts = [_LEFT + re.escape(_stem(t)) + _RIGHT for t in terms if str(t).strip()]
    return re.compile("|".join(parts), re.IGNORECASE) if parts else re.compile(r"(?!x)x")


class Scorer:
    """Holds one compiled axis set per stream."""

    STREAMS = ("major_gift", "watching")

    def __init__(self, config: dict | None = None):
        self.config = config if config is not None else yaml.safe_load(CONFIG_FILE.read_text())
        self.money = re.compile(
            self.config.get("money_pattern", r"\$|\bbillion\b|\bmillion\b"), re.IGNORECASE)
        self.money_points = int(self.config.get("money_points", 1))
        self.noise = _compile(self.config.get("noise", []))
        self.bylines = [b.lower() for b in self.config.get("byline_bypass", [])]

        self.axes: dict[str, list[tuple[str, int, re.Pattern]]] = {}
        self.thresholds: dict[str, int] = {}
        self.anchors: dict[str, set[str]] = {}
        for stream, prefix in (("major_gift", "gift"), ("watching", "watching")):
            self.axes[stream] = [
                (a["name"], int(a["points"]), _compile(a.get("terms", [])))
                for a in self.config.get(f"{prefix}_axes", [])
            ]
            self.thresholds[stream] = int(
                self.config.get(f"{prefix}_threshold", self.config.get("default_threshold", 4)))
            self.anchors[stream] = set(self.config.get(f"{prefix}_require_anchor", []) or [])

    @property
    def default_threshold(self) -> int:
        return self.thresholds["major_gift"]

    # ---- the gate -----------------------------------------------------------

    def score_stream(self, stream: str, title: str, body: str = "", author: str = "") -> Verdict:
        """Score one item against one stream's axes."""
        text = f" {title} {body} ".replace("\u2019", "'")
        # The veto reads the headline only. A memorial service for people who were killed
        # is a memorial service; the killing belongs in its description, not its verdict.
        noise_hit = self.noise.search(title)
        if noise_hit:
            return Verdict(0, [], [], vetoed=noise_hit.group(0))

        title_text = f" {title} ".replace("\u2019", "'")
        total, axes_hit, title_axes, matched = 0, [], [], []
        for name, points, pattern in self.axes[stream]:
            hits = pattern.findall(text)
            if hits:
                total += points
                axes_hit.append(name)
                matched += [h if isinstance(h, str) else h[0] for h in hits[:3]]
            if pattern.search(title_text):
                title_axes.append(name)
        if self.money.search(text):
            total += self.money_points
            axes_hit.append("money")

        seen: dict[str, None] = {}
        for m in matched:
            seen.setdefault(m.strip().lower(), None)
        return Verdict(total, axes_hit, list(seen)[:6],
                       bypass=self._byline(author), title_axes=title_axes)

    def admits_stream(self, stream: str, verdict: Verdict,
                      source_threshold: int | None = None) -> bool:
        if verdict.vetoed:
            return False
        if verdict.bypass:
            return True
        anchor = self.anchors[stream]
        # No anchor axis, no story. For gifts that means a giving verb; for events a
        # convening word. Checked on the HEADLINE, so a teaser cannot carry an item in.
        if anchor and not (anchor & set(verdict.title_axes)):
            return False
        threshold = int(source_threshold) if source_threshold is not None \
            else self.thresholds[stream]
        return verdict.score >= threshold

    def route(self, title: str, body: str = "", author: str = "",
              source_threshold: int | None = None) -> tuple[str | None, Verdict | None]:
        """Which channel this item belongs in, if any.

        Scores both streams and returns the admitted one. Gifts win ties — a gala that
        raises money is a gift story with an event attached.
        """
        results = {}
        for stream in self.STREAMS:
            v = self.score_stream(stream, title, body, author)
            if self.admits_stream(stream, v, source_threshold):
                results[stream] = v
        if not results:
            return None, None
        if "major_gift" in results:
            return "major_gift", results["major_gift"]
        return "watching", results["watching"]

    # ---- back-compat shims used by poll.py ---------------------------------

    def score(self, title: str, body: str = "", author: str = "") -> Verdict:
        stream, v = self.route(title, body, author)
        return v or self.score_stream("major_gift", title, body, author)

    def admits(self, verdict: Verdict, source_threshold: int | None = None) -> bool:
        return verdict is not None and verdict.admitted and not verdict.vetoed

    def threshold_for(self, source_threshold: int | None) -> int:
        return int(source_threshold) if source_threshold is not None else self.default_threshold

    def _byline(self, author: str) -> str | None:
        a = (author or "").lower()
        if not a:
            return None
        return next((b for b in self.bylines if b in a), None)

    # ---- categorisation -----------------------------------------------------

    def categorize(self, title: str, body: str = "") -> str:
        """Which channel a story belongs in. Delegates to route()."""
        stream, _ = self.route(title, body)
        return stream or "none"

    # ---- the dormant seam ---------------------------------------------------

    def judge_hook(self, title: str, body: str) -> None:
        """Where a meaning-based relevance judge would go.

        Deliberately unimplemented. `judge: true` in scoring.yaml is refused loudly
        rather than silently ignored, so nobody can believe an AI pass is running when
        it is not. The pattern to copy when the word list starts feeling like a
        treadmill is `ji-govt-watcher/src/classifier.py`: Gemini 2.5 Flash on the free
        tier, structured verdict, no Anthropic key, no per-item cost worth measuring.
        """
        raise NotImplementedError(
            "scoring.yaml sets judge: true, but no judge is implemented. "
            "Set it back to false, or port ji-govt-watcher/src/classifier.py."
        )
