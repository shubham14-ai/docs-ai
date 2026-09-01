"""Test execution reporting: a readable terminal tree plus machine-readable files.

``pytest -q`` answers "did it pass". It does not answer "what ran, at which
level, how long did it take, and is this build releasable" -- which is the
question actually being asked before a release. This plugin sits alongside the
suite (it never changes what runs) and turns the report stream into:

  * a terminal tree grouped SUITE -> FILE -> TEST, with per-test status,
    duration and an ``[edge]`` tag taken from the ``edge`` marker;
  * ``test-results/summary.json``    -- the same data, machine-readable;
  * ``test-results/report.txt``      -- the terminal tree, for CI log artifacts;
  * ``test-results/junit.xml``       -- via pytest's own junitxml plugin;
  * ``test-results/html/index.html`` -- via pytest-html, when installed.

The last two are wired here rather than in ``addopts`` so that a bare
``pytest`` on a machine without ``pytest-html`` still runs instead of dying on
an unrecognised argument.

Suites are derived from the directory (``tests/unit`` -> UNIT), so a new test
file is picked up with no registration step.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pytest

PASSED = "PASSED"
FAILED = "FAILED"
SKIPPED = "SKIPPED"
ERROR = "ERROR"
XFAILED = "XFAILED"
XPASSED = "XPASSED"

_MARKUP = {
    PASSED: {"green": True},
    FAILED: {"red": True, "bold": True},
    ERROR: {"red": True, "bold": True},
    SKIPPED: {"yellow": True},
    XFAILED: {"yellow": True},
    XPASSED: {"yellow": True},
}

_SUITE_ORDER = ["UNIT", "INTEGRATION", "OTHER"]
_SUITE_TITLES = {
    "UNIT": "UNIT TESTS",
    "INTEGRATION": "INTEGRATION TESTS",
    "OTHER": "OTHER TESTS",
}

# A release is blocked by these, and only these. A skip is reported loudly but
# does not fail the build: integration tests skip themselves by design when
# MongoDB or Redis are unreachable, and that is a valid local run.
_BLOCKING = (FAILED, ERROR)

_ASCII_FALLBACK = {
    "─": "-", "│": "|", "├": "+", "└": "+",
    "═": "=", "║": "|", "╔": "+", "╗": "+",
    "╚": "+", "╝": "+", "╠": "+", "╣": "+",
    "—": "-", "✅": "[OK]", "❌": "[X]", "⚠": "[!]",
}

NOT_RUN = "not run"

TICK = "✅"
CROSS = "❌"
WARN = "⚠"


# ---------------------------------------------------------------------------
# Collected data
# ---------------------------------------------------------------------------


@dataclass
class TestRecord:
    """One test case, assembled from its setup/call/teardown reports."""

    nodeid: str
    name: str
    file: str
    suite: str
    edge: bool = False
    status: str = PASSED
    duration: float = 0.0
    message: str = ""
    traceback: str = ""

    def as_dict(self) -> dict:
        payload = {
            "nodeid": self.nodeid,
            "name": self.name,
            "file": self.file,
            "suite": self.suite,
            "edge_case": self.edge,
            "status": self.status,
            "duration_seconds": round(self.duration, 4),
        }
        if self.message:
            payload["message"] = self.message
        if self.traceback:
            payload["traceback"] = self.traceback
        return payload


@dataclass
class Counts:
    """Status tally for a file, a suite or the whole run."""

    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    error: int = 0
    xfailed: int = 0
    xpassed: int = 0
    edge: int = 0
    edge_passed: int = 0
    duration: float = 0.0

    FIELDS = {
        PASSED: "passed",
        FAILED: "failed",
        SKIPPED: "skipped",
        ERROR: "error",
        XFAILED: "xfailed",
        XPASSED: "xpassed",
    }

    def add(self, record: TestRecord) -> None:
        field = self.FIELDS[record.status]
        self.total += 1
        setattr(self, field, getattr(self, field) + 1)
        self.duration += record.duration
        if record.edge:
            self.edge += 1
            if record.status == PASSED:
                self.edge_passed += 1

    @property
    def ok(self) -> bool:
        return self.failed == 0 and self.error == 0

    def as_dict(self) -> dict:
        return {
            "total": self.total,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "error": self.error,
            "xfailed": self.xfailed,
            "xpassed": self.xpassed,
            "edge_cases": self.edge,
            "edge_cases_passed": self.edge_passed,
            "duration_seconds": round(self.duration, 3),
        }


# ---------------------------------------------------------------------------
# Options and wiring
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("docs-ai reporting")
    group.addoption(
        "--results-dir",
        action="store",
        default="test-results",
        metavar="DIR",
        help="Directory for junit.xml, summary.json, report.txt and html/ "
        "(default: test-results, resolved against the rootdir).",
    )
    group.addoption(
        "--no-tree",
        action="store_true",
        default=False,
        help="Suppress the per-test tree; keep the summary and the report files.",
    )
    group.addoption(
        "--no-report-files",
        action="store_true",
        default=False,
        help="Terminal report only: write no junit.xml, summary.json or HTML.",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """Register the collector and point junitxml / pytest-html at the results dir.

    ``tryfirst`` matters: both of those plugins read their option in their own
    ``pytest_configure``, so the paths have to be set before those run. An
    explicit ``--junitxml`` / ``--html`` on the command line always wins.
    """
    plugin = ReportPlugin(config)
    config.pluginmanager.register(plugin, "docs-ai-report")

    if config.getoption("--no-report-files") or hasattr(config, "workerinput"):
        return

    results = plugin.results_dir
    results.mkdir(parents=True, exist_ok=True)

    if not getattr(config.option, "xmlpath", None):
        config.option.xmlpath = str(results / "junit.xml")

    # pytest-html is optional; only configure it when it is actually installed.
    if hasattr(config.option, "htmlpath") and not config.option.htmlpath:
        (results / "html").mkdir(parents=True, exist_ok=True)
        config.option.htmlpath = str(results / "html" / "index.html")
        if hasattr(config.option, "self_contained_html"):
            config.option.self_contained_html = True


# ---------------------------------------------------------------------------
# The plugin
# ---------------------------------------------------------------------------


class ReportPlugin:
    def __init__(self, config: pytest.Config) -> None:
        self.config = config
        self.records: Dict[str, TestRecord] = {}
        self.started = time.time()
        self.finished = self.started
        raw = Path(config.getoption("--results-dir"))
        self.results_dir = raw if raw.is_absolute() else Path(config.rootpath) / raw
        self.write_files = not config.getoption("--no-report-files")
        self.unicode = True

    # -- collection ---------------------------------------------------------

    def pytest_sessionstart(self) -> None:
        self.started = time.time()

    def pytest_sessionfinish(self) -> None:
        self.finished = time.time()

    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        record = self.records.get(report.nodeid)
        if record is None:
            record = self._new_record(report)
            self.records[report.nodeid] = record

        record.duration += report.duration

        if report.when == "setup":
            if report.failed:
                self._mark(record, ERROR, report)
            elif report.skipped:
                self._mark(record, SKIPPED, report)
        elif report.when == "call":
            if report.failed:
                self._mark(record, FAILED, report)
            elif report.skipped:
                self._mark(record, XFAILED, report)
            elif hasattr(report, "wasxfail"):
                self._mark(record, XPASSED, report)
        elif report.when == "teardown" and report.failed and record.status == PASSED:
            self._mark(record, ERROR, report)

    def _new_record(self, report: pytest.TestReport) -> TestRecord:
        path, _, name = report.nodeid.partition("::")
        parts = Path(path).parts
        if "unit" in parts:
            suite = "UNIT"
        elif "integration" in parts:
            suite = "INTEGRATION"
        else:
            suite = "OTHER"
        return TestRecord(
            nodeid=report.nodeid,
            name=name or Path(path).name,
            file=Path(path).name,
            suite=suite,
            edge="edge" in report.keywords,
        )

    @staticmethod
    def _mark(record: TestRecord, status: str, report: pytest.TestReport) -> None:
        # A real skip and an xfail both arrive as `skipped`; only the latter
        # carries `wasxfail`.
        if status == XFAILED and not hasattr(report, "wasxfail"):
            status = SKIPPED

        record.status = status
        if status in _BLOCKING:
            record.traceback = str(report.longrepr) if report.longrepr else ""
            tail = record.traceback.strip().splitlines()
            record.message = tail[-1] if tail else ""
        elif status == SKIPPED and isinstance(report.longrepr, tuple):
            record.message = report.longrepr[2]

    # -- reporting ----------------------------------------------------------

    @pytest.hookimpl(trylast=True)
    def pytest_terminal_summary(self, terminalreporter, exitstatus: int) -> None:
        tw = terminalreporter._tw
        self.unicode = _terminal_supports_unicode(tw)
        lines: List[str] = []

        def emit(text: str = "", **markup) -> None:
            """Write one line to the terminal and to the plaintext artifact."""
            rendered = text if self.unicode else _to_ascii(text)
            lines.append(rendered)
            tw.line(rendered, **markup)

        emit()
        emit(_rule("DOCS AI — TEST EXECUTION REPORT"), bold=True, cyan=True)
        emit()

        if not self.records:
            # Collection failed, or the selection matched nothing. Say so, and
            # let the exit status decide whether that blocks a release.
            emit("No tests ran (collection error, or no test matched the selection).",
                 yellow=True)
            self._print_verdict(emit, Counts(), exitstatus)
            return

        suites = self._group()
        totals = Counts()
        for _, _, counts in suites:
            _accumulate(totals, counts)

        if not self.config.getoption("--no-tree"):
            width = self._name_width()
            for suite, files, counts in suites:
                self._print_suite(emit, suite, files, counts, width)

        self._print_summary(emit, suites, totals)
        self._print_verdict(emit, totals, exitstatus)

        if self.write_files:
            self._write_files(lines, suites, totals, exitstatus)
            tw.line("")
            tw.line("Reports written to %s" % self.results_dir, cyan=True)
            for name in ("junit.xml", "summary.json", "report.txt", "html/index.html"):
                if (self.results_dir / name).exists():
                    tw.line("  %s" % (self.results_dir / name))

    # -- grouping -----------------------------------------------------------

    def _group(self):
        """[(suite, {file: ([records], counts)}, suite_counts)] in suite order."""
        grouped = []
        for suite in _SUITE_ORDER:
            records = [r for r in self.records.values() if r.suite == suite]
            if not records:
                continue
            files: Dict[str, tuple] = {}
            suite_counts = Counts()
            for record in records:
                bucket = files.setdefault(record.file, ([], Counts()))
                bucket[0].append(record)
                bucket[1].add(record)
                suite_counts.add(record)
            grouped.append((suite, dict(sorted(files.items())), suite_counts))
        return grouped

    def _name_width(self) -> int:
        longest = max((len(self._label(r)) for r in self.records.values()), default=28)
        return max(28, min(longest, 64))

    @staticmethod
    def _label(record: TestRecord) -> str:
        return "%s [edge]" % record.name if record.edge else record.name

    # -- printing -----------------------------------------------------------

    def _print_suite(self, emit, suite: str, files: dict, counts: Counts, width: int) -> None:
        emit(
            "%s   %d tests | %d passed | %d failed | %d skipped | %d error | %.2fs"
            % (
                _SUITE_TITLES[suite],
                counts.total,
                counts.passed,
                counts.failed,
                counts.skipped,
                counts.error,
                counts.duration,
            ),
            bold=True,
            cyan=True,
        )

        file_names = list(files)
        for f_index, file_name in enumerate(file_names):
            records, f_counts = files[file_name]
            last_file = f_index == len(file_names) - 1
            branch = "└──" if last_file else "├──"
            spine = "    " if last_file else "│   "

            heading = "%s %s" % (branch, file_name)
            emit(
                "%-*s  %d tests | %d passed | %d failed | %d skipped | %d error | %.3fs"
                % (
                    width + 8,
                    heading,
                    f_counts.total,
                    f_counts.passed,
                    f_counts.failed,
                    f_counts.skipped,
                    f_counts.error,
                    f_counts.duration,
                ),
                bold=True,
            )

            for t_index, record in enumerate(records):
                last_test = t_index == len(records) - 1
                leaf = "└──" if last_test else "├──"
                label = self._label(record)
                if len(label) > width:
                    label = label[: width - 3] + "..."
                dots = "." * max(3, width - len(label) + 2)
                emit(
                    "%s%s %s %s %-8s %8.3fs"
                    % (spine, leaf, label, dots, record.status, record.duration),
                    **_MARKUP.get(record.status, {}),
                )

            if not last_file:
                emit("│")
        emit()

        broken = [
            r for r in self.records.values() if r.suite == suite and r.status in _BLOCKING
        ]
        if broken:
            emit("  FAILURES IN %s" % _SUITE_TITLES[suite], red=True, bold=True)
            for record in broken:
                emit("    %s  [%s]" % (record.nodeid, record.status), red=True, bold=True)
                if record.message:
                    emit("      %s" % record.message, red=True)
            emit()

    def _print_summary(self, emit, suites, totals: Counts) -> None:
        emit("SUMMARY", bold=True)
        emit("─" * 78)
        for suite, _, counts in suites:
            emit(
                "%-20s %3d passed / %3d total    failed %d | skipped %d | error %d | edge %d"
                % (
                    _SUITE_TITLES[suite] + ":",
                    counts.passed,
                    counts.total,
                    counts.failed,
                    counts.skipped,
                    counts.error,
                    counts.edge,
                )
            )
        emit("%-20s %3d passed / %3d total" % ("Total:", totals.passed, totals.total))
        emit("%-20s %3d" % ("Edge cases:", totals.edge))
        emit("%-20s %3d" % ("Failed:", totals.failed))
        emit("%-20s %3d" % ("Errors:", totals.error))
        emit("%-20s %3d" % ("Skipped:", totals.skipped))
        emit("%-20s %.2fs" % ("Duration:", self.finished - self.started))
        emit("─" * 78)
        emit()

    def _print_verdict(self, emit, totals: Counts, exitstatus: int) -> None:
        # exitstatus 5 is "no tests collected" -- not a failure of the code, but
        # not a release signal either; it only reaches here for an empty run.
        ok = totals.ok and exitstatus in (0, 5)

        def mark(good: bool) -> str:
            return TICK if good else CROSS

        unit_value, unit_ok = self._suite_row("UNIT")
        integration_value, integration_ok = self._suite_row("INTEGRATION")

        # Three states, not two. WARN is for things a reader must notice but
        # which do not block a release: a suite that was not selected, and
        # tests that skipped themselves because a dependency was unreachable.
        rows = [
            ("Unit Tests", unit_value, WARN if unit_value == NOT_RUN else mark(unit_ok)),
            ("Integration Tests", integration_value,
             WARN if integration_value == NOT_RUN else mark(integration_ok)),
            ("Edge Cases", "%d/%d" % (totals.edge_passed, totals.edge),
             mark(totals.edge_passed == totals.edge)),
            ("Failed", str(totals.failed), mark(totals.failed == 0)),
            ("Errors", str(totals.error), mark(totals.error == 0)),
            ("Skipped", str(totals.skipped), TICK if totals.skipped == 0 else WARN),
            ("Total", "%d/%d" % (totals.passed, totals.total), mark(totals.ok)),
        ]

        inner = 52
        markup = {"green": True, "bold": True} if ok else {"red": True, "bold": True}
        emit("╔" + "═" * inner + "╗", **markup)
        emit("║" + "DOCS AI TEST REPORT".center(inner) + "║", **markup)
        emit("╠" + "═" * inner + "╣", **markup)
        for label, value, symbol in rows:
            body = " %-20s %-14s %s" % (label, value, symbol)
            emit("║" + _pad(body, inner, self.unicode) + "║", **markup)
        emit("╠" + "═" * inner + "╣", **markup)
        verdict = "%s  %s" % (mark(ok), "PASS" if ok else "FAIL")
        body = " %-20s %-14s %s" % ("RELEASE STATUS:", "", verdict)
        emit("║" + _pad(body, inner, self.unicode) + "║", **markup)
        emit("╚" + "═" * inner + "╝", **markup)
        emit()

    def _suite_row(self, suite: str):
        counts = Counts()
        for record in self.records.values():
            if record.suite == suite:
                counts.add(record)
        if counts.total == 0:
            return NOT_RUN, True
        return "%d/%d" % (counts.passed, counts.total), counts.ok

    # -- artifacts ----------------------------------------------------------

    def _write_files(self, lines: List[str], suites, totals: Counts, exitstatus: int) -> None:
        self.results_dir.mkdir(parents=True, exist_ok=True)
        (self.results_dir / "report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.finished)),
            "duration_seconds": round(self.finished - self.started, 3),
            "exit_status": exitstatus,
            "release_ready": totals.ok and exitstatus in (0, 5),
            "totals": totals.as_dict(),
            "suites": [
                {
                    "suite": suite,
                    "title": _SUITE_TITLES[suite],
                    "totals": counts.as_dict(),
                    "files": [
                        {
                            "file": file_name,
                            "totals": f_counts.as_dict(),
                            "tests": [r.as_dict() for r in records],
                        }
                        for file_name, (records, f_counts) in files.items()
                    ],
                }
                for suite, files, counts in suites
            ],
        }
        (self.results_dir / "summary.json").write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_COUNT_FIELDS = (
    "total", "passed", "failed", "skipped", "error",
    "xfailed", "xpassed", "edge", "edge_passed", "duration",
)


def _accumulate(target: Counts, other: Counts) -> None:
    for name in _COUNT_FIELDS:
        setattr(target, name, getattr(target, name) + getattr(other, name))


def _rule(title: str, width: int = 78) -> str:
    padded = " %s " % title
    side = max(3, (width - len(padded)) // 2)
    return "═" * side + padded + "═" * max(3, width - side - len(padded))


def _to_ascii(text: str) -> str:
    for char, replacement in _ASCII_FALLBACK.items():
        text = text.replace(char, replacement)
    return text


def _pad(body: str, width: int, unicode_ok: bool) -> str:
    """Pad to `width`, counting the emoji as the two columns they occupy."""
    if not unicode_ok:
        body = _to_ascii(body)
        return body + " " * max(0, width - len(body))
    visible = len(body) + sum(body.count(c) for c in (TICK, CROSS))
    return body + " " * max(0, width - visible)


def _terminal_supports_unicode(tw) -> bool:
    stream = getattr(tw, "_file", None)
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        ("─║" + TICK).encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return False
    return True
