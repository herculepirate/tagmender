"""gui/style.py - The visual system: spacing and type tokens, the light and dark palettes, status
symbols, words, icons and colours, and the app's stylesheet. `apply(app)` puts it all in place."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

from .i18n import N_


# ---- spacing (an 8-pt grid) and sizes -------------------------------------
SPACE = {"xs": 4, "s": 8, "m": 16, "l": 24}
MARGIN = 20                         # page content margins
SIDEBAR = (220, 180, 320)           # width, min, max
INSPECTOR = (320, 280, 480)
TOOLBAR = 44
ROW = 28
CORNER = 8
CONTROL = 6

# ---- type (the system UI font) -------------------------------------------
TYPE = {"title": (20, 600), "headline": (13, 600), "body": (13, 400), "caption": (11, 400),
        "glyph": (16, 400)}         # (symbols set as text: the rating stars)

# ---- colour: every text pair is at least 4.5:1 ----------------------------
# selection: a selected row (deeper than the accent in dark, as Music and Finder do, so it never
# outshines the content); focus_ring: the ring inside a focused accent button (at least 3:1 on it)
LIGHT = {"window": "#F6F7F9", "base": "#FFFFFF", "alt": "#F2F4F7", "sidebar": "#EEF0F3", "ink": "#1C2430",
         "muted": "#5D6B7A", "faint": "#B9C1CA", "separator": "rgba(0,0,0,0.10)", "line": "#DDE1E6",
         "accent": "#0F7C80", "on_accent": "#FFFFFF", "identified": "#2E7D4F", "attention": "#925F11",
         "ignored": "#5D6B7A", "error": "#B3261E", "raised": "#FFFFFF", "disabled": "#9AA4AF",
         "selection": "#0F7C80", "on_selection": "#FFFFFF", "focus_ring": "#FFFFFF"}
DARK = {"window": "#1E2228", "base": "#16191E", "alt": "#1B1F25", "sidebar": "#1C222A", "ink": "#E6EAF0",
        "muted": "#9AA5B1", "faint": "#4A535E", "separator": "rgba(255,255,255,0.10)", "line": "#323943",
        "accent": "#4FB3B5", "on_accent": "#0B1F20", "identified": "#6CC08E", "attention": "#E3B25C",
        "ignored": "#9AA5B1", "error": "#F08A80", "raised": "#262B32", "disabled": "#6B7480",
        "selection": "#1F6F72", "on_selection": "#FFFFFF", "focus_ring": "#0B1F20"}

# status -> (symbol, word, light colour, dark colour, icon)
STATUS = {
    "ready": ("●", N_("Ready"), LIGHT["accent"], DARK["accent"], "circle-dot"),
    "done": ("✓", N_("Done"), LIGHT["identified"], DARK["identified"], "circle-check"),
    "unchanged": ("✓", N_("Up to date"), LIGHT["identified"], DARK["identified"], "circle-check"),
    "unresolved": ("!", N_("Unresolved"), LIGHT["attention"], DARK["attention"], "circle-alert"),
    "excluded": ("⊘", N_("Excluded"), LIGHT["ignored"], DARK["ignored"], "ban"),
    "skipped": ("⊘", N_("Skipped"), LIGHT["ignored"], DARK["ignored"], "ban"),
    "blocked": ("✕", N_("Blocked"), LIGHT["error"], DARK["error"], "circle-x"),
    "failed": ("✕", N_("Failed"), LIGHT["error"], DARK["error"], "circle-x"),
    "conflict": ("✕", N_("Changed since preview"), LIGHT["error"], DARK["error"], "circle-x"),
    "listed": ("○", N_("Not looked up"), LIGHT["ignored"], DARK["ignored"], "circle-dot"),
}

# identification tiers below an exact release (plan.TIERS), as the status column shows them
TIER_LABELS = {"album_only": N_("Album only"), "recording": N_("Recording only"), "partial": N_("Partial")}

_forced = None                      # True/False when Preferences > Appearance says Dark/Light


def is_dark():
    if _forced is not None:
        return _forced
    try:
        return QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
    except Exception:
        return QApplication.palette().color(QPalette.Window).lightness() < 128


def tokens(dark=None) -> dict:
    return DARK if (is_dark() if dark is None else dark) else LIGHT


def color(role, dark=None) -> str:
    """A palette colour by role (ink, muted, accent, ... or a status key)."""
    t = tokens(dark)
    if role in t:
        return t[role]
    s = STATUS.get(role)
    if s:
        return s[3] if (is_dark() if dark is None else dark) else s[2]
    return t["muted"]


def status_color(key):
    s = STATUS.get(key)
    return QColor((s[3] if is_dark() else s[2]) if s else color("muted"))


def status_icon(key) -> str:
    s = STATUS.get(key)
    return s[4] if s else "circle-dot"


def font(role="body") -> QFont:
    px, weight = TYPE.get(role, TYPE["body"])
    f = QFont(QApplication.font())
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight))
    return f


def mono() -> QFont:
    f = QFont("monospace")
    f.setStyleHint(QFont.Monospace)
    f.setPixelSize(12)
    return f


# ---- contrast (WCAG 2) ----------------------------------------------------
def _lum(c: QColor) -> float:
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * ch(c.red()) + 0.7152 * ch(c.green()) + 0.0722 * ch(c.blue())


def contrast(a, b) -> float:
    la, lb = sorted((_lum(QColor(a)), _lum(QColor(b))), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def palette(dark=None) -> QPalette:
    t = tokens(dark)
    p = QPalette()
    c = lambda k: QColor(t[k])  # noqa: E731
    for group in (QPalette.Active, QPalette.Inactive):
        p.setColor(group, QPalette.Window, c("window"))
        p.setColor(group, QPalette.WindowText, c("ink"))
        p.setColor(group, QPalette.Base, c("base"))
        p.setColor(group, QPalette.AlternateBase, c("alt"))
        p.setColor(group, QPalette.Text, c("ink"))
        p.setColor(group, QPalette.Button, c("base"))
        p.setColor(group, QPalette.ButtonText, c("ink"))
        p.setColor(group, QPalette.BrightText, c("on_accent"))
        p.setColor(group, QPalette.Highlight, c("selection"))
        p.setColor(group, QPalette.HighlightedText, c("on_selection"))
        p.setColor(group, QPalette.PlaceholderText, c("muted"))
        p.setColor(group, QPalette.ToolTipBase, c("base"))
        p.setColor(group, QPalette.ToolTipText, c("ink"))
        p.setColor(group, QPalette.Link, c("accent"))
        p.setColor(group, QPalette.LinkVisited, c("accent"))
        p.setColor(group, QPalette.Mid, c("faint"))
        p.setColor(group, QPalette.Midlight, c("line"))
        p.setColor(group, QPalette.Light, c("base"))
        p.setColor(group, QPalette.Dark, c("faint"))
        p.setColor(group, QPalette.Shadow, c("faint"))
    for role in (QPalette.WindowText, QPalette.Text, QPalette.ButtonText):
        p.setColor(QPalette.Disabled, role, c("faint") if not (is_dark() if dark is None else dark)
                   else QColor("#6B7480"))
    for role, k in ((QPalette.Window, "window"), (QPalette.Base, "base"), (QPalette.Button, "base"),
                    (QPalette.Highlight, "faint"), (QPalette.HighlightedText, "ink"),
                    (QPalette.PlaceholderText, "faint")):
        p.setColor(QPalette.Disabled, role, c(k))
    return p


def stylesheet():
    t = tokens()
    accent, side, ink, muted, faint, line, base, win = (t[k] for k in ("accent", "sidebar", "ink", "muted", "faint",
                                                                      "line", "base", "window"))
    on, raised, off = t["on_accent"], t["raised"], t["disabled"]
    sel, on_sel, ring = t["selection"], t["on_selection"], t["focus_ring"]
    from . import icons                                     # (the images the stylesheet draws)
    tick, tick_off = icons.file("check", "on_accent", 14, 3), icons.file("check", "base", 14, 3)
    menu_tick, menu_tick_on = icons.file("check", "ink", 14, 2.5), icons.file("check", "on_selection", 14, 2.5)
    blank = icons.file("", size=14)                         # (an unticked menu item shows nothing, as on a Mac)
    arrows, up, down = icons.file("chevrons-up-down", "muted", 12), icons.file("chevron-up", "muted", 10, 2.5), \
        icons.file("chevron-down", "muted", 10, 2.5)
    arrows_off = icons.file("chevrons-up-down", "disabled", 12)  # (a dimmed pop-up keeps its chevron)
    err = t["error"]
    return f"""
    QMainWindow, QDialog {{ background: {win}; }}
    QDialog[sheet="true"] {{ background: {raised}; border: 1px solid {line}; }}
    QToolBar#toolbar {{ background: {win}; border: none; border-bottom: 1px solid {line};
                        padding: 4px 12px; spacing: 6px; }}
    QToolBar#toolbar QToolButton {{ border: 1px solid transparent; border-radius: {CONTROL}px;
                                    padding: 4px 10px 4px 8px; color: {ink}; }}
    QToolBar#toolbar QToolButton:hover {{ background: {line}; }}
    QToolBar#toolbar QToolButton:disabled {{ color: {faint}; }}
    QToolBar#toolbar QToolButton:checked {{ background: {line}; }}
    QToolBar#toolbar QToolButton[role="primary"] {{ background: {accent}; color: {on}; font-weight: 600; }}
    QToolBar#toolbar QToolButton[role="primary"]:disabled {{ background: {line}; color: {off};
                                                             border: 1px solid transparent; }}
    QToolBar#toolbar QToolButton:focus {{ border: 2px solid {accent}; padding: 3px 9px 3px 7px; }}
    QToolBar#toolbar QToolButton[role="primary"]:focus {{ border: 2px solid {ring}; }}
    QToolBar#prefsbar {{ background: {win}; border: none; border-bottom: 1px solid {line}; padding: 4px; }}
    QToolBar#prefsbar QToolButton {{ border: 1px solid transparent; border-radius: {CONTROL}px; padding: 4px 12px;
                                     color: {ink}; }}
    QToolBar#prefsbar QToolButton:checked {{ background: {line}; }}
    QToolBar#prefsbar QToolButton:focus {{ border: 2px solid {accent}; padding: 3px 11px; }}
    QToolButton[role="tool"] {{ border: 1px solid transparent; border-radius: {CONTROL}px; padding: 3px;
                                color: {ink}; }}
    QToolButton[role="tool"]:hover {{ background: {line}; }}
    QToolButton[role="tool"]:disabled {{ color: {off}; }}
    QToolButton[role="tool"]:focus {{ border: 2px solid {accent}; padding: 2px; }}
    QListWidget#nav {{ background: {side}; border: none; border-right: 1px solid {line}; border-radius: 0;
                       padding: 8px 8px; outline: 0; }}
    QListWidget#profileList {{ background: transparent; border: none; border-right: 1px solid {line};
                               border-radius: 0; padding: 0 8px 0 0; outline: 0; }}
    QListWidget#profileList::item {{ height: {ROW}px; padding: 0 8px; border-radius: {CONTROL}px; color: {ink}; }}
    QListWidget#profileList::item:selected {{ background: {line}; color: {ink}; }}
    QListWidget#profileList::item:selected:focus {{ background: {sel}; color: {on_sel}; }}
    QDockWidget#inspector {{ color: {ink}; }}
    QMainWindow::separator {{ background: {line}; width: 1px; height: 1px; }}
    QWidget#inspectorBody {{ background: {base}; border-left: 1px solid {line}; }}
    QLabel[role="title"] {{ font-size: 20px; font-weight: 600; color: {ink}; }}
    QLabel[role="headline"] {{ font-size: 13px; font-weight: 600; color: {ink}; }}
    QLabel[role="hint"], QLabel[role="caption"] {{ color: {muted}; }}
    QLabel[role="caption"] {{ font-size: 11px; }}
    QLabel[role="mono"], QComboBox[role="mono"], QComboBox[role="mono"] QLineEdit {{ font-family: monospace; }}
    QFrame[role="separator"] {{ background: {line}; border: none; max-height: 1px; min-height: 1px; }}
    QFrame#strip {{ background: transparent; border: none; }}
    QPushButton {{ background: {base}; color: {ink}; border: 1px solid {line}; border-radius: {CONTROL}px;
                   padding: 5px 12px; min-height: 18px; }}
    QPushButton:hover {{ border-color: {faint}; }}
    QPushButton:disabled {{ color: {faint}; }}
    QPushButton[role="primary"] {{ background: {accent}; color: {on}; border: 1px solid {accent};
                                  font-weight: 600; }}
    QPushButton[role="primary"]:disabled {{ background: {line}; color: {off}; border: 1px solid {line}; }}
    QPushButton[role="destructive"] {{ color: {err}; }}
    QComboBox, QLineEdit {{ background: {base}; color: {ink}; border: 1px solid {line};
                   border-radius: {CONTROL}px; padding: 3px 8px; min-height: 18px; }}
    QComboBox {{ padding-right: 24px; }}
    QComboBox:disabled, QLineEdit:disabled, QAbstractSpinBox:disabled {{ color: {off}; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; border: none;
                            width: 22px; }}
    QComboBox::down-arrow {{ image: url({arrows}); width: 12px; height: 12px; }}
    QComboBox::down-arrow:disabled {{ image: url({arrows_off}); }}
    QComboBox[role="tool"] {{ background: transparent; border: 1px solid transparent; padding: 3px 16px 3px 6px; }}
    QComboBox[role="tool"]:hover {{ background: {line}; }}
    QComboBox[role="tool"]::drop-down {{ width: 14px; }}
    QAbstractSpinBox {{ background: {base}; color: {ink}; border: 1px solid {line}; border-radius: {CONTROL}px;
                        padding: 3px 22px 3px 8px; min-height: 18px; }}
    QAbstractSpinBox::up-button, QAbstractSpinBox::down-button {{ subcontrol-origin: border; width: 20px;
                                                                  border: none; background: transparent; }}
    QAbstractSpinBox::up-button {{ subcontrol-position: top right; }}
    QAbstractSpinBox::down-button {{ subcontrol-position: bottom right; }}
    QAbstractSpinBox::up-arrow {{ image: url({up}); width: 10px; height: 10px; }}
    QAbstractSpinBox::down-arrow {{ image: url({down}); width: 10px; height: 10px; }}
    QListWidget, QPlainTextEdit {{ background: {base}; color: {ink};
                   border: 1px solid {line}; border-radius: {CORNER}px; selection-background-color: {sel};
                   selection-color: {on_sel}; }}
    QTableView, QTableWidget, QTreeView {{ background: {base}; color: {ink}; border: 1px solid {line};
                   border-radius: {CORNER}px; selection-background-color: {sel}; selection-color: {on_sel}; }}
    QTableView, QTableWidget, QTreeView, QListView {{ outline: 0; }}
    QTableView::item {{ padding: 0 8px; }}
    QTreeWidget#jobTree::item {{ height: {ROW}px; padding: 0 4px; }}
    QFrame[role="panel"] {{ background: {base}; border: 1px solid {line}; border-radius: {CORNER}px; }}
    QFrame[role="group"] {{ background: {base}; border: 1px solid {line}; border-radius: {CORNER}px; }}
    QFrame[role="group"] QFrame {{ background: transparent; border: none; }}
    QFrame[role="group"] QFrame[role="separator"] {{ background: {line}; margin-left: 16px; }}
    QLabel[role="grouptitle"] {{ font-size: 11px; font-weight: 600; color: {muted}; }}
    QLabel[role="error"] {{ font-size: 11px; color: {err}; }}
    QLabel[role="warn"] {{ font-size: 11px; color: {t["attention"]}; }}
    QLabel[role="attention"] {{ color: {t["attention"]}; }}
    QLabel[role="changed"] {{ font-size: 9px; color: {accent}; }}
    QPushButton[role="segment"] {{ border-radius: 0; padding: 4px 12px; margin: 0; }}
    QPushButton[role="segment"][pos="first"] {{ border-top-left-radius: {CONTROL}px;
                                               border-bottom-left-radius: {CONTROL}px; }}
    QPushButton[role="segment"][pos="last"] {{ border-top-right-radius: {CONTROL}px;
                                              border-bottom-right-radius: {CONTROL}px; border-left: none; }}
    QPushButton[role="segment"][pos="middle"] {{ border-left: none; }}
    QPushButton[role="segment"][pos="only"] {{ border-radius: {CONTROL}px; }}
    QPushButton[role="segment"]:checked {{ background: {accent}; color: {on}; border-color: {accent}; }}
    QToolButton[role="disclosure"] {{ border: 1px solid transparent; border-radius: {CONTROL}px; padding: 3px 6px;
                                      color: {muted}; font-weight: 600; }}
    QToolButton[role="disclosure"]:hover {{ background: {line}; }}
    QToolButton[role="sectionfold"] {{ border: 1px solid transparent; border-radius: {CONTROL}px; padding: 2px 6px 2px 0;
                                       font-size: 20px; font-weight: 600; color: {ink}; }}
    QToolButton[role="sectionfold"]:hover {{ background: {line}; }}
    QToolButton[role="sectionfold"]:focus {{ border: 2px solid {accent}; }}
    QToolButton[role="disclosure"]:focus {{ border: 2px solid {accent}; padding: 2px 5px; }}
    QHeaderView::section {{ background: {base}; color: {muted}; border: none; border-bottom: 1px solid {line};
                            padding: 4px 8px; font-weight: 600; }}
    QCheckBox {{ spacing: 6px; }}
    QCheckBox:focus {{ outline: none; }}
    QCheckBox::indicator, QAbstractItemView::indicator {{ width: 14px; height: 14px; border: 1px solid {muted};
                                                          border-radius: 4px; background: {base}; }}
    QCheckBox::indicator:checked, QAbstractItemView::indicator:checked {{ background: {accent};
                                                                          border-color: {accent};
                                                                          image: url({tick}); }}
    QCheckBox::indicator:disabled {{ background: {win}; border-color: {faint}; }}
    QCheckBox::indicator:checked:disabled {{ background: {faint}; border-color: {faint}; image: url({tick_off}); }}
    QCheckBox::indicator:focus {{ border: 2px solid {accent}; width: 12px; height: 12px; }}
    QMenu {{ background: {raised}; color: {ink}; border: 1px solid {line}; padding: 4px 0; icon-size: 0px; }}
    QMenu::item {{ background: transparent; padding: 4px 20px 4px 16px; border-radius: 4px; margin: 0 4px; }}
    QMenu[checks="true"]::item {{ padding-left: 4px; }}
    QMenu::item:selected {{ background: {sel}; color: {on_sel}; }}
    QMenu::item:disabled {{ color: {off}; background: transparent; }}
    QMenu::separator {{ height: 1px; background: {line}; margin: 4px 10px; }}
    QMenu::indicator {{ width: 14px; height: 14px; left: 6px; image: url({blank}); }}
    QMenu::indicator:checked {{ image: url({menu_tick}); }}
    QMenu::indicator:checked:selected {{ image: url({menu_tick_on}); }}
    QTabWidget::pane {{ border: none; border-top: 1px solid {line}; }}
    QTabBar::tab {{ padding: 5px 12px; color: {muted}; border: none; border-bottom: 2px solid transparent; }}
    QTabBar::tab:selected {{ color: {ink}; border-bottom: 2px solid {accent}; }}
    QTabBar::tab:focus {{ color: {ink}; background: {line}; border-top-left-radius: {CONTROL}px;
                          border-top-right-radius: {CONTROL}px; }}
    QStatusBar {{ background: {win}; color: {muted}; border-top: 1px solid {line}; }}
    QProgressBar {{ border: none; background: {line}; border-radius: 3px; max-height: 6px; text-align: center; }}
    QProgressBar::chunk {{ background: {accent}; border-radius: 3px; }}
    QScrollArea {{ border: none; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ width: 10px; background: transparent; border: none; margin: 0; }}
    QScrollBar:horizontal {{ height: 10px; background: transparent; border: none; margin: 0; }}
    QScrollBar::handle:vertical {{ background: {faint}; border-radius: 3px; min-height: 24px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {faint}; border-radius: 3px; min-width: 24px; margin: 2px; }}
    QScrollBar::handle:hover {{ background: {muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; border: none; background: none; }}
    QScrollBar::add-page, QScrollBar::sub-page {{ background: none; }}
    QSplitter#pageSplit::handle {{ background: transparent; }}
    QSplitter#scriptSplit::handle {{ background: {line}; }}
    QToolTip {{ background: {base}; color: {ink}; border: 1px solid {line}; padding: 4px; }}
    QPushButton:focus, QComboBox:focus, QLineEdit:focus, QAbstractSpinBox:focus, QListWidget:focus,
    QPlainTextEdit:focus {{ border: 2px solid {accent}; }}
    QTableView:focus, QTableWidget:focus, QTreeView:focus {{ border: 1px solid {accent}; }}
    QPushButton[role="primary"]:focus {{ border: 2px solid {ring}; }}
    QListWidget#nav:focus {{ border: none; border-right: 1px solid {line}; border-radius: 0; }}
    QListWidget#profileList:focus {{ border: none; border-right: 1px solid {line}; border-radius: 0; }}
    """


def apply(app=None, appearance=None):
    """Palette, stylesheet and icons for the current scheme. appearance: "system", "light" or
    "dark" (Preferences); None keeps the one chosen."""
    global _forced
    app = app or QApplication.instance()
    if appearance is not None:
        _forced = {"light": False, "dark": True}.get(appearance)
        try:
            app.styleHints().setColorScheme({"light": Qt.ColorScheme.Light, "dark": Qt.ColorScheme.Dark}.get(
                appearance, Qt.ColorScheme.Unknown))
        except Exception:                                    # (Qt before 6.8)
            pass
    f = QFont(app.font())
    f.setPixelSize(TYPE["body"][0])
    app.setFont(f)
    app.setAttribute(Qt.AA_DontShowIconsInMenus, True)    # (no icon column: one narrow check column)
    app.setPalette(palette())
    app.setStyleSheet(stylesheet())
    from . import icons
    icons.clear()
    for w in app.topLevelWidgets():                        # (icons drawn in the old colours)
        if hasattr(w, "retint"):
            w.retint()
