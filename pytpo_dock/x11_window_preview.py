from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from PySide6.QtGui import QGuiApplication, QImage, QPixmap

from .debug import log_dock_debug


@dataclass(frozen=True, slots=True)
class _PixmapFormat:
    bits_per_pixel: int
    scanline_pad: int


class X11WindowPreviewCapturer:
    backend_name: ClassVar[str] = "xcffib-xcomposite"
    _LSB_FIRST: ClassVar[int] = 0
    _Z_PIXMAP: ClassVar[int] = 2
    _ALL_PLANES: ClassVar[int] = 0xFFFFFFFF

    def __init__(self):
        self._initialized = False
        self._available = False
        self._conn = None
        self._xproto = None
        self._composite = None
        self._image_byte_order = self._LSB_FIRST
        self._format_by_depth: dict[int, _PixmapFormat] = {}

    def is_available(self) -> bool:
        self._ensure_initialized()
        return self._available

    def capture(self, win_id) -> QPixmap:
        self._ensure_initialized()
        if not self._available:
            return QPixmap()

        native_id = self._parse_window_id(win_id)
        if native_id <= 0:
            return QPixmap()

        pixmap_id = 0
        try:
            geometry = self._xproto.GetGeometry(native_id).reply()
            width = int(getattr(geometry, "width", 0))
            height = int(getattr(geometry, "height", 0))
            if width <= 0 or height <= 0:
                return QPixmap()

            pixmap_id = int(self._conn.generate_id())
            self._composite.NameWindowPixmapChecked(native_id, pixmap_id).check()
            image_reply = self._xproto.GetImage(
                self._Z_PIXMAP,
                pixmap_id,
                0,
                0,
                width,
                height,
                self._ALL_PLANES,
            ).reply()
            image = self._qimage_from_reply(image_reply, width=width, height=height)
            if image is None or image.isNull():
                return QPixmap()
            return QPixmap.fromImage(image)
        except Exception as exc:
            log_dock_debug(
                "dock-preview-xcffib-capture-failed",
                win_id=win_id,
                error=repr(exc),
            )
            return QPixmap()
        finally:
            if pixmap_id and self._xproto is not None:
                try:
                    self._xproto.FreePixmap(pixmap_id)
                    self._conn.flush()
                except Exception:
                    pass

    def _ensure_initialized(self):
        if self._initialized:
            return
        self._initialized = True

        app = QGuiApplication.instance()
        platform_name = app.platformName().lower() if app is not None else ""
        if platform_name != "xcb":
            log_dock_debug("dock-preview-xcffib-skipped", platform=platform_name or "unknown")
            return

        try:
            import xcffib
            from xcffib import composite, ffi, lib, xproto
        except Exception as exc:
            log_dock_debug("dock-preview-xcffib-import-failed", error=repr(exc))
            return

        try:
            conn = xcffib.Connection()
        except Exception as exc:
            log_dock_debug("dock-preview-xcffib-no-display", error=repr(exc))
            return

        try:
            extension_data = lib.xcb_get_extension_data(conn._conn, composite.key.c_key)
            if extension_data == ffi.NULL or not bool(extension_data.present):
                log_dock_debug("dock-preview-xcffib-composite-unavailable")
                conn.disconnect()
                return

            xproto_ext = conn.core
            composite_ext = conn(composite.key)
            version = composite_ext.QueryVersion(0, 4).reply()
            self._register_pixmap_formats(conn.get_setup())

            self._conn = conn
            self._xproto = xproto_ext
            self._composite = composite_ext
            self._available = True
            log_dock_debug(
                "dock-preview-xcffib-ready",
                version=(int(version.major_version), int(version.minor_version)),
                platform=platform_name,
            )
        except Exception as exc:
            try:
                conn.disconnect()
            except Exception:
                pass
            log_dock_debug("dock-preview-xcffib-init-failed", error=repr(exc))
            return

    def _register_pixmap_formats(self, setup) -> None:
        self._image_byte_order = int(getattr(setup, "image_byte_order", self._LSB_FIRST))
        format_by_depth: dict[int, _PixmapFormat] = {}
        for item in getattr(setup, "pixmap_formats", []):
            depth = int(getattr(item, "depth", 0))
            if depth <= 0:
                continue
            candidate = _PixmapFormat(
                bits_per_pixel=int(getattr(item, "bits_per_pixel", 0)),
                scanline_pad=max(8, int(getattr(item, "scanline_pad", 0)) or 32),
            )
            existing = format_by_depth.get(depth)
            if existing is None or candidate.bits_per_pixel >= existing.bits_per_pixel:
                format_by_depth[depth] = candidate
        self._format_by_depth = format_by_depth

    def _parse_window_id(self, win_id) -> int:
        text = str(win_id or "").strip()
        if not text:
            return 0
        for base in (0, 16):
            try:
                return int(text, base)
            except ValueError:
                continue
        return 0

    def _qimage_from_reply(self, image_reply, *, width: int, height: int) -> QImage | None:
        if width <= 0 or height <= 0:
            return None
        if self._image_byte_order != self._LSB_FIRST:
            return None

        raw = bytes(getattr(getattr(image_reply, "data", None), "raw", b""))
        if not raw:
            return None

        depth = int(getattr(image_reply, "depth", 0))
        pixmap_format = self._pixmap_format_for_depth(
            depth=depth,
            byte_count=len(raw),
            width=width,
            height=height,
        )
        if pixmap_format is None:
            return None

        bytes_per_line = self._bytes_per_line(
            width=width,
            height=height,
            byte_count=len(raw),
            pixmap_format=pixmap_format,
        )
        if bytes_per_line <= 0:
            return None

        if pixmap_format.bits_per_pixel == 32:
            image_format = QImage.Format.Format_ARGB32 if depth == 32 else QImage.Format.Format_RGB32
        elif pixmap_format.bits_per_pixel == 24:
            image_format = QImage.Format.Format_BGR888
        else:
            return None

        image = QImage(raw, width, height, bytes_per_line, image_format).copy()
        return image if not image.isNull() else None

    def _pixmap_format_for_depth(
        self,
        *,
        depth: int,
        byte_count: int,
        width: int,
        height: int,
    ) -> _PixmapFormat | None:
        format_info = self._format_by_depth.get(depth)
        if format_info is not None:
            return format_info
        if width <= 0 or height <= 0:
            return None

        pixels = width * height
        if pixels <= 0:
            return None
        if byte_count >= pixels * 4:
            return _PixmapFormat(bits_per_pixel=32, scanline_pad=32)
        if byte_count >= pixels * 3:
            return _PixmapFormat(bits_per_pixel=24, scanline_pad=32)
        return None

    def _bytes_per_line(
        self,
        *,
        width: int,
        height: int,
        byte_count: int,
        pixmap_format: _PixmapFormat,
    ) -> int:
        if width <= 0 or height <= 0 or byte_count <= 0:
            return 0

        scanline_pad = max(8, int(pixmap_format.scanline_pad) or 32)
        bits_per_line = width * int(pixmap_format.bits_per_pixel)
        aligned_bits = ((bits_per_line + scanline_pad - 1) // scanline_pad) * scanline_pad
        bytes_per_line = aligned_bits // 8
        if bytes_per_line * height == byte_count:
            return bytes_per_line
        if byte_count % height == 0:
            fallback_bytes_per_line = byte_count // height
            minimum_bytes = (bits_per_line + 7) // 8
            if fallback_bytes_per_line >= minimum_bytes:
                return fallback_bytes_per_line
        return 0

    def __del__(self):
        if self._conn is not None:
            try:
                self._conn.disconnect()
            except Exception:
                pass
