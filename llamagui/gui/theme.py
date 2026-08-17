"""Central design system for the GUI.

A single source of truth for colors and a Qt Style Sheet (QSS) that gives the
whole app a modern, cohesive look across the light / dark / system themes. The
rest of the GUI should not hardcode colors — it styles structure through QSS
and reads semantic colors from :data:`COLORS` when a widget genuinely needs an
inline color (e.g. a status line).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication

# ─── Semantic colors ────────────────────────────────────────────────────────
# Fixed, theme-independent hues chosen to stay legible on both light and dark
# surfaces. Import these instead of inlining hex strings elsewhere.
COLORS = {
    "accent": "#6366F1",  # indigo — brand / primary actions
    "accent_hover": "#4F46E5",
    "success": "#22C55E",  # listening / installed / OK
    "warning": "#F59E0B",  # unavailable / caution
    "danger": "#EF4444",  # error / cannot run
    "muted": "#757575",  # secondary text (works on light & dark)
}


# ─── Per-theme surface tokens ─────────────────────────────────────────────────
class _Tokens:
    """Background / surface / text tokens for one theme."""

    def __init__(
        self,
        *,
        bg: str,
        surface: str,
        surface_hover: str,
        surface_sunken: str,
        border: str,
        text: str,
        text_muted: str,
        accent: str,
        accent_hover: str,
    ) -> None:
        self.bg = bg
        self.surface = surface
        self.surface_hover = surface_hover
        self.surface_sunken = surface_sunken
        self.border = border
        self.text = text
        self.text_muted = text_muted
        self.accent = accent
        self.accent_hover = accent_hover


_LIGHT = _Tokens(
    bg="#F4F5F7",
    surface="#FFFFFF",
    surface_hover="#EEF0F4",
    surface_sunken="#F7F8FA",
    border="#E3E6EC",
    text="#1B1F27",
    text_muted="#6B7280",
    accent=COLORS["accent"],
    accent_hover=COLORS["accent_hover"],
)

_DARK = _Tokens(
    bg="#0F1115",
    surface="#171A21",
    surface_hover="#1E222B",
    surface_sunken="#12151B",
    border="#272B34",
    text="#E6E8EC",
    text_muted="#8A909C",
    accent=COLORS["accent"],
    accent_hover=COLORS["accent_hover"],
)

_THEMES: dict[str, _Tokens] = {"light": _LIGHT, "dark": _DARK}


def _resolve_tokens(theme: str) -> _Tokens:
    if theme in _THEMES:
        return _THEMES[theme]
    # "system" — follow the OS color scheme when Qt reports one.
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
    except Exception:  # noqa: BLE001 - headless / missing hints
        scheme = Qt.ColorScheme.Unknown
    if scheme == Qt.ColorScheme.Dark:
        return _DARK
    if scheme == Qt.ColorScheme.Light:
        return _LIGHT
    return _DARK  # Unknown: dark reads as the more "modern" default


def _qss(t: _Tokens) -> str:
    """Build the full application stylesheet for the given tokens."""
    return f"""
    /* Base ------------------------------------------------------------------ */
    QWidget {{
        background-color: {t.bg};
        color: {t.text};
        font-family: "Segoe UI", "SF Pro Text", "Inter", system-ui, sans-serif;
        font-size: 13px;
    }}

    QWidget#AppRoot {{
        background-color: {t.bg};
    }}

    QLabel {{
        background: transparent;
        color: {t.text};
    }}
    QLabel#PageTitle {{
        background: transparent;
        color: {t.text};
        font-size: 20px;
        font-weight: 700;
        padding-bottom: 4px;
    }}
    QLabel#PageHint {{
        background: transparent;
        color: {t.text_muted};
        font-size: 12px;
    }}

    /* Sidebar navigation ---------------------------------------------------- */
    QWidget#SidebarRoot {{
        background-color: {t.surface};
        border-right: 1px solid {t.border};
    }}
    QLabel#SidebarTitle {{
        background: transparent;
        color: {t.text};
        font-size: 17px;
        font-weight: 700;
        letter-spacing: 0.2px;
    }}
    QLabel#SidebarSubtitle {{
        background: transparent;
        color: {t.text_muted};
        font-size: 11px;
    }}
    QListWidget#Sidebar {{
        background-color: {t.surface};
        border: none;
        border-right: 1px solid {t.border};
        outline: 0;
        padding: 8px 6px;
    }}
    QListWidget#Sidebar::item {{
        padding: 11px 14px;
        border-radius: 9px;
        margin: 3px 6px;
        color: {t.text_muted};
    }}
    QListWidget#Sidebar::item:hover {{
        background-color: {t.surface_hover};
        color: {t.text};
    }}
    QListWidget#Sidebar::item:selected {{
        background-color: {t.accent};
        color: #FFFFFF;
    }}
    QListWidget#Sidebar::item:selected:active {{
        background-color: {t.accent_hover};
    }}

    /* Push buttons ---------------------------------------------------------- */
    QPushButton {{
        background-color: {t.accent};
        color: #FFFFFF;
        border: none;
        border-radius: 8px;
        padding: 8px 16px;
        font-weight: 600;
    }}
    QPushButton:hover {{
        background-color: {t.accent_hover};
    }}
    QPushButton:pressed {{
        background-color: {t.accent_hover};
        padding-top: 9px;
        padding-bottom: 7px;
    }}
    QPushButton:disabled {{
        background-color: {t.surface_hover};
        color: {t.text_muted};
    }}
    QPushButton#GhostButton {{
        background-color: transparent;
        color: {t.accent};
        border: 1px solid {t.border};
    }}
    QPushButton#GhostButton:hover {{
        background-color: {t.surface_hover};
        color: {t.accent_hover};
        border-color: {t.accent};
    }}
    QPushButton#GhostButton:disabled {{
        color: {t.text_muted};
        border-color: {t.border};
    }}

    /* Group boxes (cards) --------------------------------------------------- */
    QWidget#Card {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 14px;
        padding: 14px;
    }}
    QWidget#Card QLabel {{
        background: transparent;
    }}
    QGroupBox {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
        margin-top: 16px;
        padding: 16px 14px 14px;
        font-weight: 600;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin;
        subcontrol-position: top left;
        left: 12px;
        padding: 0 6px;
        color: {t.text};
        background-color: {t.surface};
    }}

    /* Inputs ---------------------------------------------------------------- */
    QLineEdit, QSpinBox, QComboBox, QAbstractSpinBox {{
        background-color: {t.surface_sunken};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 6px 9px;
        color: {t.text};
        min-height: 20px;
    }}
    QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{
        border: 1px solid {t.accent};
    }}
    QLineEdit::placeholder {{
        color: {t.text_muted};
    }}
    QComboBox::drop-down {{
        border: none;
        width: 18px;
    }}
    QComboBox QAbstractItemView {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 8px;
        selection-background-color: {t.accent};
        selection-color: #FFFFFF;
    }}

    QCheckBox {{
        spacing: 8px;
        color: {t.text};
    }}
    QCheckBox::indicator {{
        width: 18px;
        height: 18px;
        border: 1px solid {t.border};
        border-radius: 5px;
        background-color: {t.surface_sunken};
    }}
    QCheckBox::indicator:checked {{
        background-color: {t.accent};
        border-color: {t.accent};
        image: url(data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIxMiIgaGVpZ2h0PSIxMiIgdmlld0JveD0iMCAwIDEyIDEyIj48cGF0aCBmaWxsPSIjZmZmIiBkPSJNMTAuOTMgMy4zYy4zLjMuMy43IDAgMWwtNS4yIDUuMmEuNy43IDAgMCAxLTEgMEwxLjA3IDYuNGEuNy43IDAgMSAxIDEtMUw1LjQzIDguNzdsNC43LTQuN2EuNy43IDAgMCAxIDEtLjA3WiIvPjwvc3ZnPg==);
    }}

    /* Progress bar ---------------------------------------------------------- */
    QProgressBar {{
        background-color: {t.surface_sunken};
        border: 1px solid {t.border};
        border-radius: 7px;
        text-align: center;
        color: {t.text_muted};
        height: 14px;
    }}
    QProgressBar::chunk {{
        background-color: {t.accent};
        border-radius: 6px;
        margin: 1px;
    }}

    /* Tables ---------------------------------------------------------------- */
    QTableWidget {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 12px;
        gridline-color: {t.border};
        color: {t.text};
        alternate-background-color: {t.surface_hover};
        selection-background-color: {t.accent};
        selection-color: #FFFFFF;
    }}
    QHeaderView::section {{
        background-color: {t.surface_hover};
        color: {t.text_muted};
        border: none;
        border-bottom: 1px solid {t.border};
        padding: 8px 10px;
        font-weight: 600;
    }}

    /* Tabs ------------------------------------------------------------------ */
    QTabWidget::pane {{
        border: 1px solid {t.border};
        border-radius: 12px;
        top: -1px;
    }}
    QTabBar::tab {{
        background-color: transparent;
        color: {t.text_muted};
        padding: 9px 16px;
        border-bottom: 2px solid transparent;
    }}
    QTabBar::tab:selected {{
        color: {t.accent};
        border-bottom: 2px solid {t.accent};
    }}
    QTabBar::tab:hover {{
        color: {t.text};
    }}

    /* Text editors ---------------------------------------------------------- */
    QPlainTextEdit, QTextEdit {{
        background-color: {t.surface_sunken};
        border: 1px solid {t.border};
        border-radius: 10px;
        color: {t.text};
        padding: 6px;
        font-family: "Cascadia Code", "JetBrains Mono", "Consolas", monospace;
        font-size: 12px;
    }}

    /* Dialogs --------------------------------------------------------------- */
    QDialog {{
        background-color: {t.bg};
    }}

    /* Tooltips & menus ------------------------------------------------------ */
    QToolTip {{
        background-color: {t.surface};
        color: {t.text};
        border: 1px solid {t.border};
        border-radius: 8px;
        padding: 5px 8px;
    }}
    QMenu {{
        background-color: {t.surface};
        border: 1px solid {t.border};
        border-radius: 10px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 7px 16px;
        border-radius: 6px;
        color: {t.text};
    }}
    QMenu::item:selected {{
        background-color: {t.accent};
        color: #FFFFFF;
    }}

    /* Scrollbars (thin, modern) -------------------------------------------- */
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background-color: {t.border};
        border-radius: 5px;
        min-height: 24px;
    }}
    QScrollBar::handle:vertical:hover {{
        background-color: {t.text_muted};
    }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
        height: 0;
        border: none;
    }}
    QScrollBar:horizontal {{
        background: transparent;
        height: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:horizontal {{
        background-color: {t.border};
        border-radius: 5px;
        min-width: 24px;
    }}
    """


def _build_palette(t: _Tokens) -> QPalette:
    """Mirror the tokens into a QPalette so widgets that ignore QSS still match."""
    p = QPalette()
    surface = QColor(t.surface)
    text = QColor(t.text)
    muted = QColor(t.text_muted)
    accent = QColor(t.accent)
    p.setColor(QPalette.ColorRole.Window, surface)
    p.setColor(QPalette.ColorRole.WindowText, text)
    p.setColor(QPalette.ColorRole.Base, surface)
    p.setColor(QPalette.ColorRole.AlternateBase, QColor(t.surface_hover))
    p.setColor(QPalette.ColorRole.Text, text)
    p.setColor(QPalette.ColorRole.Button, surface)
    p.setColor(QPalette.ColorRole.ButtonText, text)
    p.setColor(QPalette.ColorRole.Highlight, accent)
    p.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    p.setColor(QPalette.ColorRole.ToolTipBase, surface)
    p.setColor(QPalette.ColorRole.ToolTipText, text)
    p.setColor(QPalette.ColorRole.PlaceholderText, muted)
    p.setColor(QPalette.ColorRole.Link, accent)
    return p


def get_stylesheet(theme: str) -> str:
    """Return the QSS string for ``theme`` (light / dark / system)."""
    return _qss(_resolve_tokens(theme))


def apply_theme(app: QApplication, theme: str) -> None:
    """Apply palette + stylesheet for ``theme`` to the running ``QApplication``."""
    tokens = _resolve_tokens(theme)
    app.setPalette(_build_palette(tokens))
    app.setStyleSheet(_qss(tokens))
