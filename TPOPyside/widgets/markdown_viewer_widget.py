# markdown_viewer_widget.py
from __future__ import annotations

import enum
import markdown
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QObject, Slot, QTimer, QUrl, Property, QEvent, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QPalette
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolBar, QLineEdit, QWidgetAction, QFileDialog, QApplication, QSplitter, QListWidget, QListWidgetItem, QLabel
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEngineSettings, QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView


# ---- JS bridge for copy buttons ----
class _JsBridge(QObject):
    @Slot(str)
    def copyToClipboard(self, text: str):
        QApplication.clipboard().setText(text)


# ---- Open external links in system browser ----
class _CustomWebEnginePage(QWebEnginePage):
    linkActivated = Signal(QUrl)

    def acceptNavigationRequest(self, url, nav_type, is_main_frame):
        if nav_type == QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.scheme() in ("http", "https"):
                QDesktopServices.openUrl(url)
                return False
            self.linkActivated.emit(url)
            return False
        return super().acceptNavigationRequest(url, nav_type, is_main_frame)


class MDHeadFlags(enum.IntFlag):
    none = 0
    toc = 1 << 0
    search = 1 << 1
    export = 1 << 2
    zoom = 1 << 3
    copy_md = 1 << 4


class MarkdownViewerWidget(QWidget):
    """
    Markdown viewer that renders via QWebEngine.
    TOC is a native Qt side panel (not HTML), hosted in a QSplitter.
    Header/toolbar visibility is controlled with MDHeadFlags.

    Default header is blank (MDHeadFlags.none).
    """

    # Search/result signals for async workflow
    searchResultsChanged = Signal(str, int)   # query, total_hits
    currentHitChanged = Signal(int, int)      # current_index, total_hits

    # TOC navigation signal
    tocItemActivated = Signal(str)            # anchor/id (without #)
    linkActivated = Signal(QUrl)

    def __init__(self, parent=None, *, show_toolbar: bool = True, use_widget_background: bool = True):
        super().__init__(parent)
        self.setObjectName("TPOMarkdownViewer")

        # state
        self.raw_markdown_text: str = ""
        self._show_toolbar_legacy = show_toolbar
        self._use_widget_bg = use_widget_background
        self._last_query: str = ""
        self._hit_count: int = 0
        self._hit_index: int = -1
        self._pending_search: bool = False
        self._is_dark_theme: bool = True
        self._pending_anchor: str = ""

        # explicit head flags (default blank header)
        self._head_flags: MDHeadFlags = MDHeadFlags.none

        # parsed toc data
        self._toc_items: list[tuple[str, str, int]] = []  # (title, anchor, level)

        # palette-derived defaults (can be overridden via qproperty-*)
        pal = self.palette()
        self._font_family: str = self.font().family()
        self._font_size_pt: int = max(10, int(round(self.font().pointSizeF() or 12)))
        self._font_color: str = pal.color(QPalette.Text).name()
        self._header_color: str = pal.color(QPalette.Highlight).name()
        self._link_color: str = pal.color(QPalette.Link).name()
        self._border_color: str = pal.color(QPalette.Mid).name()
        self._code_title_bg: str = pal.color(QPalette.AlternateBase).name()
        self._code_lineno_text: str = pal.color(QPalette.Mid).name()
        self._page_bg: str = pal.color(QPalette.Window).name()
        self._hit_bg: str = "rgba(189,147,249,0.35)"
        self._hit_current: str = "rgba(255,184,108,0.6)"
        self._sync_theme_from_host()

        # web view
        self.web_view = QWebEngineView(self)
        page = _CustomWebEnginePage(self)
        page.linkActivated.connect(self._on_page_link_activated)
        self.web_view.setPage(page)
        self.web_view.setContextMenuPolicy(Qt.NoContextMenu)
        self._apply_webview_background()

        s = self.web_view.settings()
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessFileUrls, True)
        s.setAttribute(QWebEngineSettings.LocalContentCanAccessRemoteUrls, True)
        s.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        s.setAttribute(QWebEngineSettings.ScrollAnimatorEnabled, True)

        # web channel
        self.channel = QWebChannel(self.web_view.page())
        self.bridge = _JsBridge(self)
        self.channel.registerObject("jsBridge", self.bridge)
        self.web_view.page().setWebChannel(self.channel)

        # --- TOC panel (native Qt) ---
        self._toc_panel = QWidget(self)
        self._toc_panel.setObjectName("MarkdownTocPanel")
        toc_lay = QVBoxLayout(self._toc_panel)
        toc_lay.setContentsMargins(8, 8, 8, 8)
        toc_lay.setSpacing(6)

        self._toc_title = QLabel("Table of Contents", self._toc_panel)
        self._toc_title.setObjectName("MarkdownTocTitle")
        toc_lay.addWidget(self._toc_title)

        self._toc_list = QListWidget(self._toc_panel)
        self._toc_list.setObjectName("MarkdownTocList")
        self._toc_list.itemActivated.connect(self._on_toc_item_activated)
        self._toc_list.itemClicked.connect(self._on_toc_item_activated)
        toc_lay.addWidget(self._toc_list, 1)

        # --- Right area (header + web) ---
        self._right_container = QWidget(self)
        right_lay = QVBoxLayout(self._right_container)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(0)

        self.toolbar: Optional[QToolBar] = self._make_toolbar()
        right_lay.addWidget(self.toolbar)
        right_lay.addWidget(self.web_view, 1)

        # --- Splitter ---
        self._splitter = QSplitter(Qt.Horizontal, self)
        self._splitter.setObjectName("MarkdownSplitter")
        self._splitter.addWidget(self._toc_panel)
        self._splitter.addWidget(self._right_container)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setStretchFactor(0, 0)
        self._splitter.setStretchFactor(1, 1)
        self._splitter.setSizes([260, 900])

        # --- Root layout ---
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._splitter, 1)

        # reinject tokens after load
        self.web_view.loadFinished.connect(self._on_load_finished)

        # initialize head UI
        self._apply_head_flags_ui()

    # ---------- qproperty-* (no bgColor here) ----------
    def _trigger_theme_update(self):
        self._apply_theme_tokens()

    def getFontFamily(self) -> str: return self._font_family
    def setFontFamily(self, v: str):
        self._font_family = (v or "").strip() or self._font_family
        self._trigger_theme_update()
    fontFamily = Property(str, getFontFamily, setFontFamily)

    def getFontSizePt(self) -> int: return self._font_size_pt
    def setFontSizePt(self, v: int):
        try:
            self._font_size_pt = max(6, int(v))
        except Exception:
            pass
        self._trigger_theme_update()
    fontSizePt = Property(int, getFontSizePt, setFontSizePt)

    def getFontColor(self) -> str: return self._font_color
    def setFontColor(self, v: str):
        self._font_color = (v or "").strip()
        self._trigger_theme_update()
    fontColor = Property(str, getFontColor, setFontColor)

    def getHeaderColor(self) -> str: return self._header_color
    def setHeaderColor(self, v: str):
        self._header_color = (v or "").strip()
        self._trigger_theme_update()
    headerColor = Property(str, getHeaderColor, setHeaderColor)

    def getLinkColor(self) -> str: return self._link_color
    def setLinkColor(self, v: str):
        self._link_color = (v or "").strip()
        self._trigger_theme_update()
    linkColor = Property(str, getLinkColor, setLinkColor)

    def getBorderColor(self) -> str: return self._border_color
    def setBorderColor(self, v: str):
        self._border_color = (v or "").strip()
        self._trigger_theme_update()
    borderColor = Property(str, getBorderColor, setBorderColor)

    def getCodeTitleBg(self) -> str: return self._code_title_bg
    def setCodeTitleBg(self, v: str):
        self._code_title_bg = (v or "").strip()
        self._trigger_theme_update()
    codeTitleBg = Property(str, getCodeTitleBg, setCodeTitleBg)

    def getCodeLineNoText(self) -> str: return self._code_lineno_text
    def setCodeLineNoText(self, v: str):
        self._code_lineno_text = (v or "").strip()
        self._trigger_theme_update()
    codeLineNoText = Property(str, getCodeLineNoText, setCodeLineNoText)

    def getHitBg(self) -> str: return self._hit_bg
    def setHitBg(self, v: str):
        self._hit_bg = (v or "").strip()
        self._trigger_theme_update()
    hitBg = Property(str, getHitBg, setHitBg)

    def getHitCurrent(self) -> str: return self._hit_current
    def setHitCurrent(self, v: str):
        self._hit_current = (v or "").strip()
        self._trigger_theme_update()
    hitCurrent = Property(str, getHitCurrent, setHitCurrent)

    # compatibility placeholders
    def getBgColor(self) -> str: return ""
    def setBgColor(self, v: str): pass
    bgColor = Property(str, getBgColor, setBgColor)

    def getCodeBlockBg(self) -> str: return ""
    def setCodeBlockBg(self, v: str): pass
    codeBlockBg = Property(str, getCodeBlockBg, setCodeBlockBg)

    def getCodeLineNoBg(self) -> str: return ""
    def setCodeLineNoBg(self, v: str): pass
    codeLineNoBg = Property(str, getCodeLineNoBg, setCodeLineNoBg)

    # palette/style changes
    def event(self, e):
        if e.type() in (QEvent.PaletteChange, QEvent.StyleChange, QEvent.FontChange):
            self._sync_theme_from_host()
            self._apply_webview_background()
            self._trigger_theme_update()
        return super().event(e)

    # ---------- head flags ----------
    def headFlags(self) -> MDHeadFlags:
        return self._head_flags

    def setHeadFlags(self, flags: MDHeadFlags):
        self._head_flags = MDHeadFlags(flags)
        self._apply_head_flags_ui()

    @Slot(bool)
    def setShowToc(self, show: bool):
        flags = self._head_flags
        if show:
            flags |= MDHeadFlags.toc
        else:
            flags &= ~MDHeadFlags.toc
        self.setHeadFlags(flags)

    def _apply_head_flags_ui(self):
        # Header visible only if any flag is on
        any_head = self._head_flags != MDHeadFlags.none
        self.toolbar.setVisible(any_head)

        # Apply action visibility by flag
        self._act_toc.setVisible(bool(self._head_flags & MDHeadFlags.toc))
        self._act_zoom_out.setVisible(bool(self._head_flags & MDHeadFlags.zoom))
        self._act_zoom_reset.setVisible(bool(self._head_flags & MDHeadFlags.zoom))
        self._act_zoom_in.setVisible(bool(self._head_flags & MDHeadFlags.zoom))
        self._act_pdf.setVisible(bool(self._head_flags & MDHeadFlags.export))
        self._act_copy_md.setVisible(bool(self._head_flags & MDHeadFlags.copy_md))
        self._wa_search.setVisible(bool(self._head_flags & MDHeadFlags.search))
        self._act_prev.setVisible(bool(self._head_flags & MDHeadFlags.search))
        self._act_next.setVisible(bool(self._head_flags & MDHeadFlags.search))

        # TOC + splitter behavior:
        # when TOC off => hide splitter and show only right container
        if self._head_flags & MDHeadFlags.toc:
            self._splitter.show()
            self._toc_panel.show()
            # Ensure right container is in splitter second slot (already is)
            if self.layout().indexOf(self._splitter) < 0:
                self.layout().addWidget(self._splitter, 1)
            self._right_container.setParent(self._splitter)
        else:
            self._splitter.hide()
            if self.layout().indexOf(self._right_container) < 0:
                self.layout().addWidget(self._right_container, 1)
            self._right_container.show()

    # ---------- toolbar ----------
    def _make_toolbar(self) -> QToolBar:
        tb = QToolBar("Markdown Viewer", self)
        tb.setMovable(False)

        self._act_toc = QAction("TOC", self)
        self._act_toc.triggered.connect(self.toggle_navigation_panel)
        tb.addAction(self._act_toc)

        tb.addSeparator()

        self._act_zoom_out = QAction("–", self)
        self._act_zoom_out.triggered.connect(self.zoom_out)
        tb.addAction(self._act_zoom_out)

        self._act_zoom_reset = QAction("100%", self)
        self._act_zoom_reset.triggered.connect(self.reset_zoom)
        tb.addAction(self._act_zoom_reset)

        self._act_zoom_in = QAction("+", self)
        self._act_zoom_in.triggered.connect(self.zoom_in)
        tb.addAction(self._act_zoom_in)

        tb.addSeparator()

        self._act_pdf = QAction("PDF", self)
        self._act_pdf.triggered.connect(self._print_to_pdf_dialog)
        tb.addAction(self._act_pdf)

        self._act_copy_md = QAction("Copy MD", self)
        self._act_copy_md.triggered.connect(self.copy_markdown_to_clipboard)
        tb.addAction(self._act_copy_md)

        tb.addSeparator()

        self._search_edit = QLineEdit(self)
        self._search_edit.setPlaceholderText("Search…")
        self._search_edit.returnPressed.connect(self.find_next)
        self._wa_search = QWidgetAction(self)
        self._wa_search.setDefaultWidget(self._search_edit)
        tb.addAction(self._wa_search)

        self._act_prev = QAction("◀", self)
        self._act_prev.triggered.connect(self.find_prev)
        tb.addAction(self._act_prev)

        self._act_next = QAction("▶", self)
        self._act_next.triggered.connect(self.find_next)
        tb.addAction(self._act_next)

        return tb

    # ---------- rendering ----------
    def setMarkdown(self, text: str, base_url: QUrl | None = None):
        self.raw_markdown_text = text or ""

        if base_url is None:
            base_url = QUrl()
        elif base_url.isLocalFile():
            s = base_url.toString()
            if not s.endswith("/"):
                base_url = QUrl(s + "/")

        md = markdown.Markdown(
            extensions=[
                "tables",
                "toc",
                "admonition",
                "pymdownx.details",
                "pymdownx.arithmatex",
                "pymdownx.superfences",
                "pymdownx.highlight",
            ],
            extension_configs={
                "toc": {"title": "Table of Contents", "permalink": False},
                "pymdownx.arithmatex": {"generic": True},
                "pymdownx.superfences": {
                    "custom_fences": [{
                        "name": "mermaid",
                        "class": "mermaid",
                        "format": lambda src, *a, **k: f'<pre class="mermaid">{src}</pre>'
                    }]
                },
                "pymdownx.highlight": {"linenums": True, "css_class": "codehilite", "guess_lang": False},
            },
        )
        md_html = md.convert(self.raw_markdown_text)

        # Build native TOC from markdown library tokens
        self._toc_items.clear()
        try:
            for item in getattr(md, "toc_tokens", []) or []:
                self._collect_toc_tokens(item, level=1)
        except Exception:
            self._toc_items.clear()

        self._rebuild_toc_widget()

        html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.css">
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/katex.min.js"></script>
<script defer src="https://cdn.jsdelivr.net/npm/katex@0.16.9/dist/contrib/auto-render.min.js"></script>
<script type="text/javascript" src="qrc:///qtwebchannel/qwebchannel.js"></script>
<script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
<style>
{self._base_css()}
{self._code_block_css()}
{self._code_theme_css()}
{self._admonition_css()}
</style>
</head>
<body>
<main id="md-root">{md_html}</main>
<script>
var jsBridge;
new QWebChannel(qt.webChannelTransport, (channel) => {{ jsBridge = channel.objects.jsBridge; }});

function initializeMermaid() {{
  try {{
    const isLight = document.body.classList.contains('light-theme');
    mermaid.initialize({{ startOnLoad:false, theme: isLight ? 'default' : 'dark', securityLevel: 'loose' }});
    mermaid.run({{ nodes: document.querySelectorAll('pre.mermaid') }});
  }} catch(e) {{ console.error('Mermaid error', e); }}
}}
function renderArithmatexWithKaTeX(root = document.body) {{
  const nodes = root.querySelectorAll('span.arithmatex, div.arithmatex');
  nodes.forEach(el => {{
    let tex = (el.textContent || '').trim();
    let display = el.tagName.toLowerCase() === 'div';
    if (tex.startsWith('\\\\(') && tex.endsWith('\\\\)')) {{ tex = tex.slice(2,-2); }}
    else if (tex.startsWith('$$') && tex.endsWith('$$')) {{ tex = tex.slice(2,-2); }}
    else if (tex.startsWith('\\\\[') && tex.endsWith('\\\\]')) {{ tex = tex.slice(2,-2); display = true; }}
    try {{ katex.render(tex, el, {{ displayMode: display, throwOnError: false }}); }}
    catch (e) {{ console.error('KaTeX render error:', e); }}
  }});
}}
document.addEventListener('DOMContentLoaded', () => {{ initializeMermaid(); renderArithmatexWithKaTeX(); }});

// Scroll to heading id (used by native Qt TOC)
window.MV_scrollToAnchor = function(anchor) {{
  if (!anchor) return false;
  const el = document.getElementById(anchor);
  if (!el) return false;
  el.scrollIntoView({{ behavior: 'smooth', block: 'start' }});
  return true;
}}

// Code blocks: title + copy
document.addEventListener('DOMContentLoaded', () => {{
  document.querySelectorAll('div.codehilite').forEach((block) => {{
    const preTag = block.querySelector('pre');
    let language = 'code';
    if (preTag && preTag.className) {{
      const langClass = Array.from(preTag.classList).find(c => !['highlight'].includes(c));
      if (langClass) language = langClass;
    }}
    const titleBar = document.createElement('div'); titleBar.className = 'code-title';
    titleBar.innerHTML = `<span>${{language}}</span><button class="copy-btn">Copy</button>`;
    block.insertBefore(titleBar, block.firstChild);

    const contentWrapper = document.createElement('div'); contentWrapper.className = 'code-content';
    const table = block.querySelector('table');
    if (table) {{
      const innerDiv = document.createElement('div');
      innerDiv.appendChild(table);
      contentWrapper.appendChild(innerDiv);
      block.appendChild(contentWrapper);
    }}

    titleBar.addEventListener('click', (e) => {{
      if (e.target.tagName !== 'BUTTON') block.classList.toggle('code-collapsed');
    }});
    titleBar.querySelector('.copy-btn').addEventListener('click', (e) => {{
      e.stopPropagation();
      const codeCell = block.querySelector('td.code');
      const codeToCopy = codeCell ? codeCell.innerText.trimEnd() : '';
      if (jsBridge) {{
        jsBridge.copyToClipboard(codeToCopy);
        e.target.innerText = 'Copied!';
        setTimeout(() => e.target.innerText = 'Copy', 1600);
      }}
    }});
  }});
}});

// Search helpers (DOM-level)
(function(){{
  window.MV_clearMarks = function() {{
    document.querySelectorAll('mark.mdhit').forEach(m => {{
      const parent = m.parentNode;
      while (m.firstChild) parent.insertBefore(m.firstChild, m);
      parent.removeChild(m);
      parent.normalize();
    }});
    document.querySelectorAll('mark.mdhit.current').forEach(m => m.classList.remove('current'));
  }}

  window.MV_findAll = function(q) {{
    MV_clearMarks();
    if (!q || !q.trim()) return 0;

    const root = document.getElementById('md-root');
    const ql = q.toLowerCase();
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null);
    const textNodes = [];
    let n;
    while ((n = walker.nextNode())) {{
      // avoid script/style/etc
      const p = n.parentNode;
      if (!p) continue;
      const tag = (p.nodeName || '').toLowerCase();
      if (tag === 'script' || tag === 'style' || tag === 'noscript' || tag === 'mark') continue;
      textNodes.push(n);
    }}

    let hits = 0;
    textNodes.forEach(node => {{
      const t = node.nodeValue || '';
      if (!t.trim()) return;

      let i = 0;
      let idx = -1;
      const frag = document.createDocumentFragment();

      while ((idx = t.toLowerCase().indexOf(ql, i)) !== -1) {{
        const before = t.slice(i, idx);
        if (before) frag.appendChild(document.createTextNode(before));

        const mark = document.createElement('mark');
        mark.className = 'mdhit';
        mark.dataset.hitIndex = String(hits);
        mark.appendChild(document.createTextNode(t.slice(idx, idx + q.length)));
        frag.appendChild(mark);

        i = idx + q.length;
        hits++;
      }}

      if (frag.childNodes.length) {{
        const after = t.slice(i);
        if (after) frag.appendChild(document.createTextNode(after));
        node.parentNode.replaceChild(frag, node);
      }}
    }});

    return hits;
  }}

  window.MV_scrollToHit = function(index) {{
    const el = document.querySelector(`mark.mdhit[data-hit-index="${{index}}"]`);
    if (!el) return false;
    el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
    document.querySelectorAll('mark.mdhit.current').forEach(m => m.classList.remove('current'));
    el.classList.add('current');
    return true;
  }}
}})();
</script>
</body>
</html>
"""
        self.web_view.setHtml(html, baseUrl=base_url)
        self._last_query, self._hit_count, self._hit_index = "", 0, -1
        self._pending_search = False
        self.searchResultsChanged.emit("", 0)
        self.currentHitChanged.emit(-1, 0)

    def clear(self):
        self.setMarkdown("")

    def _on_load_finished(self, ok: bool):
        if ok:
            self._apply_theme_tokens()
            if self._pending_anchor:
                anchor = self._pending_anchor
                self._pending_anchor = ""
                self.web_view.page().runJavaScript(f"MV_scrollToAnchor({self._repr_js(anchor)});")

    # ---------- TOC helpers ----------
    def _collect_toc_tokens(self, token: dict, level: int):
        name = str(token.get("name", "")).strip()
        anchor = str(token.get("id", "")).strip()
        if name and anchor:
            self._toc_items.append((name, anchor, int(token.get("level", level))))
        for child in token.get("children", []) or []:
            self._collect_toc_tokens(child, level=level + 1)

    def _rebuild_toc_widget(self):
        self._toc_list.clear()
        for title, anchor, level in self._toc_items:
            item = QListWidgetItem(("  " * max(0, level - 1)) + title)
            item.setData(Qt.UserRole, anchor)
            self._toc_list.addItem(item)

    @Slot(QListWidgetItem)
    def _on_toc_item_activated(self, item: QListWidgetItem):
        if not item:
            return
        anchor = item.data(Qt.UserRole) or ""
        if not anchor:
            return
        self.tocItemActivated.emit(anchor)
        self.web_view.page().runJavaScript(f"MV_scrollToAnchor({self._repr_js(anchor)});")

    # ---------- public controls ----------
    @Slot()
    def toggle_navigation_panel(self):
        # App-level TOC visibility now, not HTML class toggles
        currently = bool(self._head_flags & MDHeadFlags.toc)
        self.setShowToc(not currently)

    # App controls theme explicitly
    @Slot()
    def setLightTheme(self):
        self._is_dark_theme = False
        self.web_view.page().runJavaScript(
            "document.body.classList.add('light-theme');"
            "if (typeof initializeMermaid!=='undefined') initializeMermaid();"
        )

    @Slot()
    def setDarkTheme(self):
        self._is_dark_theme = True
        self.web_view.page().runJavaScript(
            "document.body.classList.remove('light-theme');"
            "if (typeof initializeMermaid!=='undefined') initializeMermaid();"
        )

    def scroll_to_anchor(self, anchor: str) -> None:
        target = str(anchor or "").strip().lstrip("#")
        if not target:
            return
        self._pending_anchor = target
        self.web_view.page().runJavaScript(f"MV_scrollToAnchor({self._repr_js(target)});")

    @Slot()
    def zoom_in(self):
        self.web_view.setZoomFactor(self.web_view.zoomFactor() + 0.1)

    @Slot()
    def zoom_out(self):
        self.web_view.setZoomFactor(self.web_view.zoomFactor() - 0.1)

    @Slot()
    def reset_zoom(self):
        self.web_view.setZoomFactor(1.0)

    @Slot()
    def copy_markdown_to_clipboard(self):
        if self.raw_markdown_text:
            QApplication.clipboard().setText(self.raw_markdown_text)

    @Slot()
    def _print_to_pdf_dialog(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save as PDF", "", "PDF Files (*.pdf)")
        if path:
            self.print_to_pdf(path)

    def print_to_pdf(self, path: str | Path):
        self.web_view.page().printToPdf(str(path))

    # ---------- search (async-safe) ----------
    # Note: return value no longer trusted as immediate hit count (async).
    def find(self, text: str) -> int:
        self._last_query = text or ""
        self._hit_index = -1
        self._pending_search = True

        if not self._last_query.strip():
            self.clear_search()
            return 0

        js = f"MV_findAll({self._repr_js(self._last_query)});"

        def _cb(count):
            self._pending_search = False
            try:
                self._hit_count = int(count or 0)
            except Exception:
                self._hit_count = 0

            self.searchResultsChanged.emit(self._last_query, self._hit_count)

            if self._hit_count > 0:
                self._hit_index = 0
                self.select_match(self._hit_index)
            else:
                self._hit_index = -1
                self.currentHitChanged.emit(-1, 0)

        self.web_view.page().runJavaScript(js, _cb)

        # kept for compatibility only; caller should use signal/callback semantics
        return self._hit_count

    def clear_search(self):
        self._hit_count, self._hit_index, self._last_query = 0, -1, ""
        self._pending_search = False
        self.web_view.page().runJavaScript("MV_clearMarks();")
        self.searchResultsChanged.emit("", 0)
        self.currentHitChanged.emit(-1, 0)

    @Slot()
    def find_next(self):
        if hasattr(self, "_search_edit") and self._search_edit.text() and self._search_edit.text() != self._last_query:
            self.find(self._search_edit.text())
            return

        if self._pending_search:
            return

        if self._hit_count <= 0:
            if hasattr(self, "_search_edit") and self._search_edit.text():
                self.find(self._search_edit.text())
            return

        self._hit_index = (self._hit_index + 1) % self._hit_count
        self.select_match(self._hit_index)

    @Slot()
    def find_prev(self):
        if hasattr(self, "_search_edit") and self._search_edit.text() and self._search_edit.text() != self._last_query:
            self.find(self._search_edit.text())
            return

        if self._pending_search:
            return

        if self._hit_count <= 0:
            if hasattr(self, "_search_edit") and self._search_edit.text():
                self.find(self._search_edit.text())
            return

        self._hit_index = (self._hit_index - 1) % self._hit_count
        self.select_match(self._hit_index)

    def select_match(self, index: int):
        if index < 0 or self._hit_count <= 0:
            return

        def _cb(ok):
            if ok:
                self.currentHitChanged.emit(index, self._hit_count)

        self.web_view.page().runJavaScript(f"MV_scrollToHit({index});", _cb)

    # ---------- theme token injection ----------
    def _apply_theme_tokens(self):
        tokens = {
            "--page-bg": self._page_bg,
            "--text-color": self._font_color,
            "--header-color": self._header_color,
            "--link-color": self._link_color,
            "--border-color": self._border_color,
            "--code-title-bg": self._code_title_bg,
            "--code-lineno-text": self._code_lineno_text,
            "--hit-bg": self._hit_bg,
            "--hit-current": self._hit_current,
            "--font-family": self._font_family or "system-ui",
            "--font-size": f"{self._font_size_pt}pt",
        }
        js = "\n".join(
            [f"document.documentElement.style.setProperty('{k}','{v}');" for k, v in tokens.items()]
        )
        self.web_view.page().runJavaScript(js)

        # reapply explicit theme class after reload
        if self._is_dark_theme:
            self.setDarkTheme()
        else:
            self.setLightTheme()

    # ---------- CSS (transparent backgrounds) ----------
    def _base_css(self) -> str:
        return """
:root {
  --page-bg: #1f252f;
  --text-color: #222222;
  --header-color: #0b57d0;
  --link-color: #0b57d0;
  --border-color: #d0d0d0;
  --code-title-bg: rgba(0,0,0,0.08);
  --code-lineno-text: #8a8f98;
  --hit-bg: rgba(255,226,143,0.6);
  --hit-current: rgba(250,166,26,0.7);
  --font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --font-size: 12pt;
}
body {
  margin: 0;
  background: var(--page-bg);
  color: var(--text-color);
  font-family: var(--font-family);
  font-size: var(--font-size);
  line-height: 1.6;
  padding: 16px 20px 20px 20px;
  transition: color .2s;
}
h1,h2,h3,h4,h5,h6 {
  color: var(--header-color);
  border-bottom: 1px solid var(--border-color);
  padding-bottom: 5px; margin-top: 24px; margin-bottom: 16px; scroll-margin-top: 10px;
}
a { color: var(--link-color); text-decoration: none; }
blockquote {
  background-color: transparent;
  border-left: 4px solid var(--border-color);
  padding: 10px 15px; margin: 0 0 16px; border-radius: 8px;
}
p>code, li>code {
  background-color: transparent;
  border: 1px solid color-mix(in srgb, var(--border-color) 55%, transparent);
  padding: .15em .35em; border-radius: 4px;
}
main ul, main ol { padding-left: 1.8rem; margin: 0 0 1em; }
main ul ul, main ul ol, main ol ul, main ol ol { padding-left: 1.6rem; margin: .3em 0; }
main ol ol { list-style-type: lower-alpha; } main ol ol ol { list-style-type: lower-roman; }

mark.mdhit { background: var(--hit-bg); padding: 0 2px; border-radius: 3px; }
mark.mdhit.current { background: var(--hit-current); }
"""

    def _code_block_css(self) -> str:
        return """
div.codehilite {
  background-color: transparent;
  border: 1px solid var(--border-color);
  border-radius: 8px; margin: 20px 0; overflow: hidden;
}
.code-title {
  display: flex; justify-content: space-between; align-items: center;
  background-color: var(--code-title-bg);
  padding: 8px 15px;
  font-family: "Fira Code", ui-monospace, SFMono-Regular, Menlo, Consolas, "Liberation Mono", monospace;
  font-size: 1.0em; color: var(--header-color);
  border-bottom: 1px solid var(--border-color); user-select: none; cursor: pointer;
}
.code-content { display: grid; grid-template-rows: 1fr; transition: grid-template-rows .3s ease-in-out; }
.code-collapsed .code-content { grid-template-rows: 0fr; }
.code-content>div { overflow: hidden; }
div.codehilite table { width: 100%; border-collapse: collapse; font-family: "Fira Code", ui-monospace, monospace; }
td.linenos {
  color: var(--code-lineno-text); text-align: right; user-select: none;
  border-right: 1px solid var(--border-color);
  background-color: transparent;
  padding: 0; width: 1%; white-space: nowrap;
}
td.linenos pre, td.code pre { margin: 0; padding: .8em; line-height: 1.5; font-variant-ligatures: none; tab-size: 4; }
td.code { padding: 0; }
"""

    def _admonition_css(self) -> str:
        return """
.admonition {
  padding: 15px; margin-bottom: 20px; border-left: 6px solid; border-radius: 8px;
  background-color: transparent;
}
.admonition-title {
  margin: -15px -15px 15px -15px; padding: 10px 15px; font-weight: 600;
  border-top-left-radius: 8px; border-top-right-radius: 8px; color: inherit;
  background-color: transparent;
}
.admonition.note { border-color: #448aff; }
.admonition.warning { border-color: #ff9800; }
.admonition.danger { border-color: #f44336; }
.admonition.tip { border-color: #00bcd4; }
"""

    def _code_theme_css(self) -> str:
        return """
.codehilite .c{color:#6272a4}.codehilite .k{color:#ff79c6}.codehilite .n{color:#f8f8f2}.codehilite .o{color:#ff79c6}
.codehilite .p{color:#f8f8f2}.codehilite .cm{color:#6272a4}.codehilite .cp{color:#ff79c6}.codehilite .c1{color:#6272a4}
.codehilite .cs{color:#ff79c6}.codehilite .kc{color:#ff79c6}.codehilite .kd{color:#8be9fd;font-style:italic}
.codehilite .kn{color:#ff79c6}.codehilite .kp{color:#ff79c6}.codehilite .kr{color:#ff79c6}.codehilite .kt{color:#8be9fd}
.codehilite .m{color:#bd93f9}.codehilite .s{color:#f1fa8c}.codehilite .na{color:#50fa7b}.codehilite .nb{color:#f8f8f2}
.codehilite .nc{color:#50fa7b;font-weight:700}.codehilite .no{color:#bd93f9}.codehilite .nd{color:#ff79c6}
.codehilite .nf{color:#50fa7b}.codehilite .nv{color:#8be9fd;font-style:italic}.codehilite .s2{color:#f1fa8c}
.codehilite .se{color:#bd93f9}.codehilite .si{color:#f1fa8c}
"""

    # ---------- helpers ----------
    @Slot(QUrl)
    def _on_page_link_activated(self, url: QUrl) -> None:
        queued_url = QUrl(url)
        QTimer.singleShot(0, lambda: self.linkActivated.emit(queued_url))

    @staticmethod
    def _qss_props_for_selector(stylesheet: str, selector: str) -> dict[str, str]:
        source = str(stylesheet or "")
        if not source:
            return {}
        source = re.sub(r"/\*.*?\*/", "", source, flags=re.DOTALL)
        pattern = re.compile(rf"{re.escape(selector)}\s*\{{(.*?)\}}", re.DOTALL | re.IGNORECASE)
        match = pattern.search(source)
        if not match:
            return {}
        props: dict[str, str] = {}
        for declaration in match.group(1).split(";"):
            if ":" not in declaration:
                continue
            key, value = declaration.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if not key or not value:
                continue
            props[key] = value
        return props

    @staticmethod
    def _is_dark_color(value: str) -> bool:
        color = QColor(str(value or "").strip())
        if not color.isValid():
            return True
        luminance = (
            0.2126 * color.redF()
            + 0.7152 * color.greenF()
            + 0.0722 * color.blueF()
        )
        return luminance < 0.5

    def _sync_theme_from_host(self) -> None:
        # Prefer app QSS root colors because QWebEngine HTML does not inherit QSS.
        app = QApplication.instance()
        qss = app.styleSheet() if app is not None else ""
        props = self._qss_props_for_selector(qss, "QWidget")

        pal = self.palette()
        self._font_color = str(props.get("color", pal.color(QPalette.Text).name()))
        self._page_bg = str(
            props.get(
                "background",
                props.get("background-color", pal.color(QPalette.Window).name()),
            )
        )
        self._header_color = pal.color(QPalette.Highlight).name()
        self._link_color = pal.color(QPalette.Link).name()
        self._border_color = pal.color(QPalette.Mid).name()
        self._code_title_bg = pal.color(QPalette.AlternateBase).name()
        self._code_lineno_text = pal.color(QPalette.Mid).name()
        self._is_dark_theme = self._is_dark_color(self._page_bg)

    def _apply_webview_background(self) -> None:
        if self._use_widget_bg:
            color = QColor(str(self._page_bg or "").strip())
            if not color.isValid():
                color = QColor(self.palette().color(QPalette.Window))
            if color.isValid():
                self.web_view.page().setBackgroundColor(color)
            self.web_view.setAttribute(Qt.WA_TranslucentBackground, False)
            self.web_view.setStyleSheet("")
            return
        self.web_view.page().setBackgroundColor(Qt.transparent)
        self.web_view.setAttribute(Qt.WA_TranslucentBackground, True)
        self.web_view.setStyleSheet("background: transparent;")

    @staticmethod
    def _repr_js(s: str) -> str:
        return repr(s).replace("</", "<\\/")
