"""Reusable progress widget for long-running operations.

A thin state machine around a :class:`QProgressBar`. Callers drive it with
:meth:`ProgressWidget.start_operation`, :meth:`~.update_progress`,
:meth:`~.finish_operation` and :meth:`~.fail_operation`. The base look (rounded
surface track, accent fill, centered muted text) comes from the theme's
``QProgressBar`` rule; this widget only layers a semantic danger fill on top for
the failure state, reading the color from :data:`llamagui.gui.theme.COLORS` as
the one place a widget-level color is genuinely needed.

The engine feeds :meth:`update_progress` with ``(done, total, phase, overall)``:

* ``done`` / ``total`` are the bytes of the *current* phase (download or
  extract), shown as ``512 MB / 1.2 GB``.
* ``overall`` is the fraction of the *whole* operation done (0..1) when the
  engine knows it — used for the bar fill so a backend install renders as one
  continuous sweep across download -> extract -> cudart instead of several
  0->100% segments. When ``overall`` is ``None`` (e.g. a download whose server
  omitted Content-Length) the bar falls back to ``done/total`` and goes busy if
  even that is unknown.

Speed (``MB/s``) and an ETA (``2m left``) are derived from the byte deltas
between ticks.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QProgressBar, QVBoxLayout, QWidget

from ..theme import COLORS

# Inline override applied only while the bar is in the failure state. It restyles
# just the chunk (and its text) so the track, border and radius keep coming from
# the theme; setting the bar full means the whole width is the danger color.
_ERROR_CSS = (
    "QProgressBar { color: #FFFFFF; }"
    f"QProgressBar::chunk {{ background-color: {COLORS['danger']}; }}"
)

# Phase keyword -> friendly label. Lower-cased so the original phase keyword
# remains a substring (``download`` is in ``Downloading``), which keeps the
# existing progress-widget tests' substring assertions valid.
_PHASE_LABELS = {
    "download": "Downloading",
    "extract": "Extracting",
    "cache hit": "Using cached",
}

# Smoothing for the byte-rate estimate; lower = snappier, higher = steadier.
_RATE_SMOOTHING = 0.7

# Normalized range used when an ``overall`` fraction drives the bar, so the fill
# animates smoothly between integer percentages.
_OVERALL_RANGE = 1000


def _human(n: float) -> str:
    """Render a byte count in human units (B/KB/MB/GB/TB)."""
    n = float(n)
    if n < 1024:
        return f"{n:.0f} B"
    value = n / 1024
    for unit in ("KB", "MB", "GB", "TB", "PB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def _fmt_duration(seconds: float) -> str:
    """Render a duration as a compact ``1h 2m`` / ``3m 4s`` / ``5s`` string."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    return f"{seconds // 3600}h {seconds % 3600 // 60}m"


class ProgressWidget(QWidget):
    """A self-terminating progress bar for a single operation.

    Hidden until :meth:`start_operation`; indeterminate (busy) until the first
    :meth:`update_progress` carries a known total, then determinate with a
    percentage. :meth:`finish_operation` hides it again; :meth:`fail_operation`
    leaves a full, red error bar visible until the next :meth:`start_operation`
    or an explicit :meth:`reset`.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._bar = QProgressBar()
        self._bar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._bar.setTextVisible(True)
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.hide()
        layout.addWidget(self._bar)
        self._reset_rate()

    # ─── Lifecycle ───────────────────────────────────────────────────────────

    def start_operation(self, label: str = "") -> None:
        """Show the bar in the indeterminate (busy) state for a new operation."""
        self._clear_error_style()
        self._reset_rate()
        self._bar.setRange(0, 0)
        self._bar.setFormat(label)
        self._bar.show()

    def update_progress(
        self,
        done: int,
        total: int,
        phase: str,
        overall: float | None = None,
    ) -> None:
        """Advance the bar; switch to determinate once ``total`` is known."""
        now = time.monotonic()
        if overall is not None:
            frac = max(0.0, min(overall, 1.0))
            self._bar.setRange(0, _OVERALL_RANGE)
            self._bar.setValue(int(frac * _OVERALL_RANGE))
            percent = round(frac * 100)
        elif total > 0:
            clamped = max(0, min(done, total))
            self._bar.setRange(0, total)
            self._bar.setValue(clamped)
            percent = round(clamped * 100 / total)
        else:
            # No total and no overall: keep the busy animation.
            self._bar.setRange(0, 0)
            percent = None

        self._update_rate(done, now)
        self._bar.show()

        label = _PHASE_LABELS.get(phase, phase)
        parts = [label]
        if total > 0:
            parts.append(f"{_human(done)} / {_human(total)}")
        if percent is not None:
            parts.append(f"{percent}%")
        if self._rate and total > 0 and done < total:
            parts.append(f"{_human(self._rate)}/s")
            eta = (total - done) / self._rate
            if eta > 0:
                parts.append(f"{_fmt_duration(eta)} left")
        self._bar.setFormat("  ".join(parts))

    def finish_operation(self) -> None:
        """Mark the operation complete and hide the bar again."""
        self._clear_error_style()
        self._reset_rate()
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._bar.setFormat("done")
        self._bar.hide()

    def fail_operation(self, message: str) -> None:
        """Leave a full, danger-colored bar visible so the failure is obvious."""
        self._reset_rate()
        self._bar.setStyleSheet(_ERROR_CSS)
        self._bar.setRange(0, 100)
        self._bar.setValue(100)
        self._bar.setFormat(f"failed: {message}")
        self._bar.show()

    def reset(self) -> None:
        """Hide the bar and drop any failure styling, back to the idle state."""
        self._clear_error_style()
        self._reset_rate()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        self._bar.setFormat("")
        self._bar.hide()

    # ─── Internals ──────────────────────────────────────────────────────────

    def _reset_rate(self) -> None:
        self._last_done = 0
        self._last_time: float | None = None
        self._rate: float | None = None

    def _update_rate(self, done: int, now: float) -> None:
        if self._last_time is not None and now > self._last_time:
            dt = now - self._last_time
            delta = done - self._last_done
            if dt > 0 and delta > 0:
                inst = delta / dt
                if self._rate is None:
                    self._rate = inst
                else:
                    self._rate = self._rate * _RATE_SMOOTHING + inst * (
                        1 - _RATE_SMOOTHING
                    )
        self._last_done = done
        self._last_time = now

    def _clear_error_style(self) -> None:
        if self._bar.styleSheet():
            self._bar.setStyleSheet("")
