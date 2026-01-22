# Plugin.py
from flask import Flask, request, jsonify
from datetime import datetime
from flask_cors import CORS

import os, sys
import ctypes
from ctypes import wintypes, c_void_p

import win32print
import win32ui
import win32con

import logging
from logging.handlers import RotatingFileHandler

try:
    from PIL import Image, ImageDraw, ImageFont, ImageWin
except Exception:
    Image = ImageDraw = ImageFont = ImageWin = None

# ============================================================
# Config
# ============================================================

def resource_path(*relative):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *relative)

# Fuentes locales (privadas, sin instalar)
FONTS_DIR = resource_path("fonts")
FONT_FILES_TRY = [
    os.path.join(FONTS_DIR, "DejaVuSans.ttf"),
    os.path.join(FONTS_DIR, "DejaVuSansMono.ttf"),
    # Si tenés Noto, podés agregarla:
    os.path.join(FONTS_DIR, "NotoSans-Regular.ttf"),
]

PREFERRED_FACE_NAMES = [
    "DejaVu Sans Mono",
    "DejaVu Sans",
    "Segoe UI",
    "Arial Unicode MS",
    "Arial",
]

# Márgenes y columnas (auto-ajuste)
LEFT_MARGIN   = 16
TOP_MARGIN    = 16
RIGHT_MARGIN  = 16
BOTTOM_MARGIN = 16
DEFAULT_TARGET_COLS = 40

# ============================================================
# Perfil recomendado para Epson TM-U220 (impact/dot matrix)
# ============================================================

# Para TM-U220, 40 cols suele forzar letra muy pequeña. 33-35 cols suele verse mejor.
DEFAULT_TARGET_COLS_U220 = 34

# Mínimo de letra para el auto-fit (sube la legibilidad)
AUTO_FONT_MIN_PX = 14
AUTO_FONT_MAX_PX = 34

# “Densidad” por raster (binarización):
# - Más bajo => texto más “delgado/suave” (menos puntos) => suele imprimir más rápido
# - Más alto => más negro (más puntos) => más lento
# Rango recomendado: 60 - 140 (aprox)
DEFAULT_RASTER_THRESHOLD = 95

def _is_tm_u220(printer_name: str) -> bool:
    try:
        return "tm-u220" in (printer_name or "").lower()
    except Exception:
        return False


# Render por defecto: "raster" (recomendado) o "gdi"
RENDER_MODE_DEFAULT = "raster"

# ¿Activar fallback raster cuando GDI falle?
RASTER_FALLBACK_ENABLED = True

# ============================================================
# Logging
# ============================================================
FR_PRIVATE = 0x10

def notify_start():
    try:
        logging.info("FloWin Plugin: servidor corriendo en http://127.0.0.1:5100")
    except Exception:
        pass

def setup_logging():
    try:
        base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        if getattr(sys, "frozen", False):
            base_dir = os.path.dirname(sys.executable)
        log_path = os.path.join(base_dir, "plugin.log")
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        handler.setFormatter(fmt)
        root = logging.getLogger()
        root.setLevel(logging.INFO)
        root.addHandler(handler)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
    except Exception:
        pass

# ============================================================
# Cargar TTF privada (para GDI; Pillow no lo necesita instalado)
# ============================================================
def _load_ttf_private(ttf_path: str) -> bool:
    if not os.path.isfile(ttf_path):
        return False
    AddFontResourceExW = ctypes.windll.gdi32.AddFontResourceExW
    AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, c_void_p]
    AddFontResourceExW.restype = ctypes.c_int
    added = AddFontResourceExW(ttf_path, FR_PRIVATE, None)
    return added > 0

def _ensure_any_font_loaded():
    loaded = False
    for p in FONT_FILES_TRY:
        try:
            if _load_ttf_private(p):
                loaded = True
        except Exception:
            pass
    if not loaded:
        logging.warning("No se pudieron cargar TTF privadas DejaVu/Noto. GDI usará fuentes del sistema. (Raster no requiere instalación)")

# ============================================================
# Helpers de impresión / medición (GDI)
# ============================================================
def _get_printable_metrics(hDC):
    HORZRES    = hDC.GetDeviceCaps(8)
    VERTRES    = hDC.GetDeviceCaps(10)
    LOGPIXELSX = hDC.GetDeviceCaps(88)
    LOGPIXELSY = hDC.GetDeviceCaps(90)
    return HORZRES, VERTRES, LOGPIXELSX, LOGPIXELSY

def _make_font(height_px, face_name):
    return win32ui.CreateFont({
        "name": face_name,
        "height": -int(max(8, height_px)),
        "weight": win32con.FW_NORMAL,
        "charset": win32con.DEFAULT_CHARSET,
        "quality": win32con.CLEARTYPE_QUALITY,
    })

def _measure_text_width(hDC, text: str) -> int:
    size = hDC.GetTextExtent(text)
    return size[0]

# Detector real de glifos usando GetGlyphIndicesW: marca 0xFFFF si falta
def _has_glyphs_using_gdi(hDC, text: str) -> bool:
    try:
        GGI_MARK_NONEXISTING_GLYPHS = 0x0001
        GetGlyphIndicesW = ctypes.windll.gdi32.GetGlyphIndicesW
        GetGlyphIndicesW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int, ctypes.POINTER(wintypes.WORD), wintypes.DWORD]
        GetGlyphIndicesW.restype  = ctypes.c_uint

        count = len(text)
        arr = (wintypes.WORD * count)()
        res = GetGlyphIndicesW(int(hDC.GetSafeHdc()), text, count, arr, GGI_MARK_NONEXISTING_GLYPHS)
        if res == 0xFFFFFFFF:
            return False
        return all(g != 0xFFFF for g in arr)
    except Exception:
        return False

def _face_can_render_sample(hDC, face_name, px=16) -> bool:
    sample = "áéíóú ÁÉÍÓÚ ñ Ñ ₡"
    try:
        f = _make_font(px, face_name)
        old = hDC.SelectObject(f)
        try:
            return _has_glyphs_using_gdi(hDC, sample)
        finally:
            hDC.SelectObject(old)
            del f
    except Exception:
        return False

def _select_face_that_renders_unicode(hDC):
    for face in PREFERRED_FACE_NAMES:
        if _face_can_render_sample(hDC, face, 18):
            return face
    return "Arial"

def _pick_font_height_fit_cols_gdi(hDC, face_name, printable_width_px, left_margin, right_margin, target_cols=DEFAULT_TARGET_COLS):
    usable = max(40, printable_width_px - (left_margin + right_margin))

    lo, hi = AUTO_FONT_MIN_PX, AUTO_FONT_MAX_PX
    best = max(AUTO_FONT_MIN_PX, 18)

    while lo <= hi:
        mid = (lo + hi) // 2
        font = _make_font(mid, face_name)
        old = hDC.SelectObject(font)
        try:
            test_str = "M" * int(max(10, target_cols))
            width = _measure_text_width(hDC, test_str)
        finally:
            hDC.SelectObject(old)
            del font

        if width <= usable:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    return max(AUTO_FONT_MIN_PX, min(best, AUTO_FONT_MAX_PX))


def _draw_wrapped_gdi(hDC, text, left, top, right, bottom, extra_spacing=1):
    calc_rect = (left, top, right, bottom)
    hDC.DrawText(text, calc_rect, win32con.DT_LEFT | win32con.DT_WORDBREAK | win32con.DT_CALCRECT | win32con.DT_NOPREFIX)
    used_rect = (left, top, right, calc_rect[3])
    hDC.DrawText(text, used_rect, win32con.DT_LEFT | win32con.DT_WORDBREAK | win32con.DT_NOPREFIX)
    return calc_rect[3] + extra_spacing

def _draw_kv_singleline(hDC, label, value, left, top, right, bottom, gap_px=8):
    measure = (left, top, right, bottom)
    hDC.DrawText("Xg", measure, win32con.DT_LEFT | win32con.DT_SINGLELINE | win32con.DT_CALCRECT)
    line_bottom = measure[3]
    rect_left  = (left, top, (right - gap_px) // 2, line_bottom)
    rect_right = ((right + gap_px) // 2, top, right, line_bottom)
    hDC.DrawText(label, rect_left,  win32con.DT_LEFT  | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX)
    hDC.DrawText(value, rect_right, win32con.DT_RIGHT | win32con.DT_SINGLELINE | win32con.DT_NOPREFIX)
    return line_bottom + 1

def _maybe_split_kv(line: str):
    if ":" in line:
        k, v = line.split(":", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            return k, v
    return None, None

# ============================================================
# Construcción del contenido
# ============================================================
def _format_crc(value):
    try:
        n = float(value or 0)
        return f"₡{n:,.2f}"
    except Exception:
        return "₡0.00"

def _get_precio_impuestos(prod):
    precio_imp = prod.get("PrecioImpuestos", None)
    if precio_imp in (None, 0, 0.0):
        precio_imp = prod.get("PrecioImpuesto", None)
    try:
        impuestos    = float(prod.get("Impuestos", 0) or 0)
        es_boni      = bool(prod.get("EsBonificacion", False))
        precio_total = float(prod.get("PrecioTotal", 0) or 0)
        if (precio_imp is None or float(precio_imp) == 0.0) and not es_boni and impuestos > 0 and precio_total > 0:
            base_sin_iva = precio_total / (1 + (impuestos / 100.0))
            precio_imp = precio_total - base_sin_iva
        if precio_imp is None:
            precio_imp = 0.0
        return float(precio_imp)
    except Exception:
        return 0.0

def _build_ticket_lines(data):
    factura   = data.get("factura", {}) or {}
    productos = data.get("detalle", []) or []

    empresa = {
        "nombre": "FARMACIA SEXTA AVENIDA S.R.L.",
        "direccion": "HEREDIA CENTRO, COSTADO NORTE MERCADO MUNICIPAL",
        "identificacion": "3-102-167724",
    }

    fecha          = datetime.now().strftime("%Y-%m-%d %H:%M")
    cliente        = factura.get("NombreCliente", "Consumidor Final")
    identificacion = factura.get("IdentificacionCliente", "")
    metodo_pago    = factura.get("MetodoPago", "")
    total          = factura.get("PrecioTotal", 0)
    noFactura      = factura.get("NoFactura", "")
    vendedor       = factura.get("Vendedor", "")

    lines = []
    sep = "-" * DEFAULT_TARGET_COLS

    lines.append(empresa["nombre"])
    lines.append(empresa["identificacion"])
    lines.append(empresa["direccion"])
    lines.append(sep)
    lines.append(f"FECHA: {fecha}")
    lines.append(f"CLIENTE: {cliente}")
    if identificacion:
        lines.append(f"IDENTIFICACION: {identificacion}")
    lines.append(sep)
    lines.append(f"VENDEDOR: {vendedor}")
    lines.append(f"FACTURA NO.: {noFactura}")
    lines.append(sep)
    lines.append("SR(a). ESTIMADO CLIENTE")
    lines.append(sep)

    subtotal = 0.0
    impuestos_totales = 0.0

    for prod in productos:
        codigo           = str(prod.get("Codigo", ""))[:60]
        nombre           = str(prod.get("Nombre", ""))[:180]
        unidades         = prod.get("Cantidad", 0) or 0
        fracciones       = prod.get("CantidadFracciones", 0) or 0
        precio_unitario  = prod.get("PrecioUnitario", 0) or 0.0
        precio_fraccion  = prod.get("TotalFraccionario", 0) or 0.0
        descuento_pct    = prod.get("PerDescuento", 0) or 0.0
        monto_desc       = prod.get("Descuento", 0) or 0.0
        precio_total     = prod.get("PrecioTotal", 0) or 0.0
        impuestos        = float(prod.get("Impuestos", 0) or 0.0)
        es_boni          = bool(prod.get("EsBonificacion", False))
        bonificacion     = prod.get("BonificacionCalculada", 0) or 0.0
        precioImpuestos  = _get_precio_impuestos(prod)

        # --- Totales generales (como lo tenías) ---
        base_sin_iva = (precio_total / (1 + impuestos / 100)) if (not es_boni and impuestos) else (0 if es_boni else precio_total)
        subtotal          += base_sin_iva if not es_boni else 0
        impuestos_totales += (precio_total - base_sin_iva) if not es_boni else 0

        # --- NUEVO: total con descuento aplicado por ítem ---
        try:
            total_con_desc = float(precio_total or 0) - float(monto_desc or 0)
            if total_con_desc < 0:
                total_con_desc = 0.0
        except Exception:
            total_con_desc = 0.0

        lines.append(f"{codigo}")
        lines.append(f"{nombre}")
        lines.append(f"UNID.: x{unidades}    FRACC.: x{fracciones}")
        lines.append(f"PRECIO UNIT.: {_format_crc(precio_unitario)}    TOTAL FRACC.: {_format_crc(precio_fraccion)}")
        lines.append(f"BONIF.: x{bonificacion}")
        lines.append(f"DESC.: {float(descuento_pct):.2f}%    MONTO DESC.: {_format_crc(monto_desc)}")

        # ✅ Línea nueva solicitada
        lines.append(f"TOTAL CON DESC.: {_format_crc(total_con_desc)}")

        lines.append(f"I.V.A.: {impuestos:.2f}%   MONTO I.V.A.: {_format_crc(precioImpuestos)}")
        lines.append(f"TOTAL ÍTEM: {_format_crc(precio_total)}")
        lines.append("")

    lines.append(sep)
    lines.append(f"SUBTOTAL: {_format_crc(subtotal)}")
    lines.append(f"I.V.A.: {_format_crc(impuestos_totales)}")
    lines.append(sep)
    lines.append(f"TOTAL: {_format_crc(total)}")
    lines.append(f"METODO PAGO: {metodo_pago}")
    lines.append(sep)
    lines.append("¡GRACIAS POR SU COMPRA!")
    lines.append("NO SE ACEPTAN DEVOLUCIONES")
    lines.append("")
    lines.append("Autorizado mediante resolucion No. DGT-R-")
    lines.append("033-2019 del 20 de Junio del 2019.")
    lines.append("Version 4.3")
    return lines


# ============================================================
# Raster con Pillow (Unicode‐safe)
# ============================================================
def _try_import_pillow():
    global Image, ImageDraw, ImageFont, ImageWin
    if Image is None or ImageDraw is None or ImageFont is None or ImageWin is None:
        try:
            from PIL import Image as _Image, ImageDraw as _ImageDraw, ImageFont as _ImageFont, ImageWin as _ImageWin
            Image, ImageDraw, ImageFont, ImageWin = _Image, _ImageDraw, _ImageFont, _ImageWin
        except Exception as e:
            logging.exception("Error importando Pillow")
            return None, None, None, None
    return Image, ImageDraw, ImageFont, ImageWin


def _choose_ttf_for_pillow():
    for p in FONT_FILES_TRY:
        if os.path.isfile(p):
            return p
    return None  # Pillow puede usar default bitmap, pero mejor abortar si no hay TTF

def _wrap_text_by_width(text, draw, font, max_width):
    # Word-wrap midiendo ancho real en píxeles
    if not text:
        return [""]
    words = text.split(" ")
    lines = []
    cur = ""
    for w in words:
        test = (cur + " " + w).strip()
        try:
            width = draw.textlength(test, font=font)
        except Exception:
            # fallback usando bbox
            bbox = draw.textbbox((0,0), test, font=font)
            width = (bbox[2]-bbox[0]) if bbox else 0
        if width <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
                cur = w
            else:
                # palabra más larga que el ancho: quebrar por caracteres
                tmp = ""
                for ch in w:
                    t2 = tmp + ch
                    try:
                        w2 = draw.textlength(t2, font=font)
                    except Exception:
                        bbox = draw.textbbox((0,0), t2, font=font)
                        w2 = (bbox[2]-bbox[0]) if bbox else 0
                    if w2 <= max_width:
                        tmp = t2
                    else:
                        if tmp:
                            lines.append(tmp)
                        tmp = ch
                cur = tmp
    if cur:
        lines.append(cur)
    return lines

def _pick_font_height_fit_cols_pillow(usable_w, target_cols=DEFAULT_TARGET_COLS, ttf_path=None):
    Image, ImageDraw, ImageFont, _ = _try_import_pillow()
    if not Image:
        return max(AUTO_FONT_MIN_PX, 14)

    ttf = ttf_path or _choose_ttf_for_pillow()
    if not ttf:
        return max(AUTO_FONT_MIN_PX, 14)

    lo, hi = AUTO_FONT_MIN_PX, AUTO_FONT_MAX_PX
    best = max(AUTO_FONT_MIN_PX, 18)

    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(ttf, mid)
        img = Image.new("L", (usable_w, mid + 30), 255)
        draw = ImageDraw.Draw(img)

        test = "M" * int(max(10, target_cols))
        try:
            width = draw.textlength(test, font=font)
        except Exception:
            bbox = draw.textbbox((0, 0), test, font=font)
            width = (bbox[2] - bbox[0]) if bbox else usable_w + 1

        if width <= usable_w:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1

    return max(AUTO_FONT_MIN_PX, min(best, AUTO_FONT_MAX_PX))


def _print_lines_raster(lines, target_cols=DEFAULT_TARGET_COLS, font_px_override=None, raster_threshold=None):
    """
    Raster optimizado:
    - Renderiza texto a imágenes por página (no por línea)
    - Controla “intensidad” con binarización (threshold)
    """
    printer_name = win32print.GetDefaultPrinter()

    # Perfil TM-U220: menos columnas por default => letra más grande
    if _is_tm_u220(printer_name) and target_cols == DEFAULT_TARGET_COLS:
        target_cols = DEFAULT_TARGET_COLS_U220

    hDC = win32ui.CreateDC()
    hDC.CreatePrinterDC(printer_name)

    try:
        hDC.SetGraphicsMode(2)  # GM_ADVANCED
    except Exception:
        pass
    try:
        hDC.SetMapMode(win32con.MM_TEXT)
    except Exception:
        pass
    try:
        hDC.SetBkMode(win32con.TRANSPARENT)
    except Exception:
        pass

    pw, ph, dpx, dpy = _get_printable_metrics(hDC)
    left   = LEFT_MARGIN
    top    = TOP_MARGIN
    right  = pw - RIGHT_MARGIN
    bottom = ph - BOTTOM_MARGIN
    usable_w = max(1, right - left)
    usable_h = max(1, bottom - top)

    Image, ImageDraw, ImageFont, ImageWin = _try_import_pillow()
    if not Image:
        raise RuntimeError("Pillow no está disponible para renderizar en raster.")

    ttf = _choose_ttf_for_pillow()
    if not ttf:
        raise RuntimeError("No se encontró ninguna TTF en /fonts (DejaVu/Noto).")

    # Threshold (intensidad): default recomendado para TM-U220
    if raster_threshold is None:
        raster_threshold = DEFAULT_RASTER_THRESHOLD
    try:
        raster_threshold = int(raster_threshold)
    except Exception:
        raster_threshold = DEFAULT_RASTER_THRESHOLD
    raster_threshold = max(40, min(200, raster_threshold))

    # Tamaño de fuente
    if isinstance(font_px_override, int) and font_px_override >= 8:
        font_px = max(AUTO_FONT_MIN_PX, min(font_px_override, AUTO_FONT_MAX_PX))
    else:
        font_px = _pick_font_height_fit_cols_pillow(usable_w, target_cols, ttf)

    font = ImageFont.truetype(ttf, font_px)

    # Métricas de línea
    try:
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + 2
    except Exception:
        line_h = int(font_px * 1.35)

    # --- Paso 1: “expandir” lines => wrapped lines reales en píxeles ---
    # Creamos un canvas temporal para medir wraps
    tmp_img = Image.new("L", (usable_w, line_h + 40), 255)
    tmp_draw = ImageDraw.Draw(tmp_img)

    expanded = []
    for raw_line in lines:
        if raw_line is None:
            raw_line = ""

        # Mantener KV en una sola “fila lógica” (la dibujamos luego como 2 columnas)
        k, v = _maybe_split_kv(str(raw_line))
        if k and v:
            expanded.append(("KV", k, v))
            continue

        wrapped = _wrap_text_by_width(str(raw_line), tmp_draw, font, usable_w)
        for piece in wrapped:
            expanded.append(("TXT", piece))

    # --- Paso 2: paginar por altura ---
    # Reservamos un poco por seguridad de corte
    max_lines_per_page = max(1, usable_h // line_h)

    pages = []
    cur = []
    for item in expanded:
        # Cada item consume 1 línea visual (KV o TXT)
        if len(cur) >= max_lines_per_page:
            pages.append(cur)
            cur = []
        cur.append(item)
    if cur:
        pages.append(cur)

    # --- Paso 3: imprimir páginas ---
    hDC.StartDoc("Factura")

    for page in pages:
        # Render página completa (L = grayscale)
        page_img = Image.new("L", (usable_w, usable_h), 255)
        d = ImageDraw.Draw(page_img)

        y = 0
        for item in page:
            if item[0] == "KV":
                _, k, v = item
                mid = usable_w // 2

                # izquierda
                d.text((0, y), k, font=font, fill=0)

                # derecha alineado a la derecha
                try:
                    vw = d.textlength(v, font=font)
                except Exception:
                    bbox = d.textbbox((0, 0), v, font=font)
                    vw = (bbox[2] - bbox[0]) if bbox else 0
                x_val = max(mid + 8, usable_w - int(vw))
                d.text((x_val, y), v, font=font, fill=0)

            else:
                _, txt = item
                d.text((0, y), txt, font=font, fill=0)

            y += line_h
            if y >= usable_h:
                break

        # Binarizar para TM-U220:
        # threshold más bajo => menos “bordes negros” => más suave y rápido
        bw = page_img.point(lambda p: 0 if p < raster_threshold else 255, mode="1")

        hDC.StartPage()
        dib = ImageWin.Dib(bw)
        dib.draw(hDC.GetHandleOutput(), (left, top, right, bottom))
        hDC.EndPage()

    hDC.EndDoc()
    del hDC


# ============================================================
# Impresión GDI (opcional)
# ============================================================
def _print_lines_gdi(lines, target_cols=DEFAULT_TARGET_COLS, font_px_override=None, force_raster=False):
    # Si nos fuerzan raster (o default es raster), desviamos a raster directo
    if force_raster or RENDER_MODE_DEFAULT.lower() == "raster":
        _print_lines_raster(lines, target_cols=target_cols, font_px_override=font_px_override)
        return

    printer_name = win32print.GetDefaultPrinter()
    hDC = win32ui.CreateDC()
    hDC.CreatePrinterDC(printer_name)

    try:
        hDC.SetGraphicsMode(2)  # GM_ADVANCED
    except Exception:
        pass
    try:
        hDC.SetMapMode(win32con.MM_TEXT)
    except Exception:
        pass
    try:
        hDC.SetBkMode(win32con.TRANSPARENT)
    except Exception:
        pass

    hDC.StartDoc("Factura")
    hDC.StartPage()

    pw, ph, dpx, dpy = _get_printable_metrics(hDC)
    left   = LEFT_MARGIN
    top    = TOP_MARGIN
    right  = pw - RIGHT_MARGIN
    bottom = ph - BOTTOM_MARGIN

    # Elegir familia que sí pinta acentos y ₡ (si el driver respeta TrueType)
    face = _select_face_that_renders_unicode(hDC)

    # Altura de fuente
    if isinstance(font_px_override, int) and font_px_override >= 8:
        font_px = font_px_override
    else:
        font_px = _pick_font_height_fit_cols_gdi(hDC, face, pw, LEFT_MARGIN, RIGHT_MARGIN, target_cols)

    font = _make_font(font_px, face)
    old = hDC.SelectObject(font)

    unicode_ok = _face_can_render_sample(hDC, face, font_px)
    raster_fallback = bool(not unicode_ok and RASTER_FALLBACK_ENABLED)

    if raster_fallback:
        logging.warning("GDI no garantizó Unicode; activando fallback raster por línea.")
    y = top
    for line in lines:
        if y >= (bottom - 4):
            hDC.EndPage(); hDC.StartPage(); y = TOP_MARGIN

        if not line:
            measure = (left, y, right, bottom)
            hDC.DrawText("Xg", measure, win32con.DT_LEFT | win32con.DT_SINGLELINE | win32con.DT_CALCRECT)
            y = measure[3] + 1
            continue

        if raster_fallback:
            # Render con Pillow por línea (si llegara a fallar Pillow, caemos a GDI)
            try:
                _print_lines_raster([line], target_cols=target_cols, font_px_override=font_px)
                # la llamada anterior abre/cierra doc… mejor no anidar.
                # En lugar de mezclar páginas, hacemos wrap GDI si Pillow no se usa aquí.
                # Por simplicidad, si estamos en fallback, NO mezclamos y seguimos con GDI:
                pass
            except Exception:
                pass

        k, v = _maybe_split_kv(line)
        if k and v:
            y = _draw_kv_singleline(hDC, k, v, left, y, right, bottom)
        else:
            y = _draw_wrapped_gdi(hDC, line, left, y, right, bottom, extra_spacing=1)

    hDC.SelectObject(old)
    hDC.EndPage()
    hDC.EndDoc()

    del font
    del hDC

# ============================================================
# Corte ESC/POS (opcional)
# ============================================================
def _send_cut_command_raw():
    try:
        printer_name = win32print.GetDefaultPrinter()
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            win32print.StartDocPrinter(hPrinter, 1, ("Cut", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            win32print.WritePrinter(hPrinter, b'\x1D\x56\x42\x00')  # GS V B 0
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception:
        pass

def _build_anular_factura_lines(payload):
    # Soporta ambos formatos:
    # 1) { "IdFactura":..., "ReferenciaFactElectronica":..., "Motivo":..., "config": {...} }
    # 2) { "data": { ... }, "config": {...} }  (tu caso actual con Axios)
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload.get("data") or {}
    else:
        data = payload or {}

    id_factura = data.get("IdFactura", "")
    ref_nc     = data.get("ReferenciaFactElectronica", "")
    motivo     = data.get("Motivo", "")

    # Si alguno viene vacío imprime en blanco
    id_factura = "" if id_factura is None else str(id_factura)
    ref_nc     = "" if ref_nc is None else str(ref_nc)
    motivo     = "" if motivo is None else str(motivo)

    sep = "*" * DEFAULT_TARGET_COLS

    lines = [
        sep,
        "Anulación Factura",
        f"Id: {id_factura}",
        f"Referencia nota crédito: {ref_nc}",
        f"Motivo: {motivo}",
        sep,
    ]
    return lines

# ============================================================
# Flask
# ============================================================
app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["*"])

@app.route('/PrintTicket', methods=['POST'])
def print_ticket():
    data = request.get_json() or {}
    try:
        _ensure_any_font_loaded()  # solo afecta a GDI

        cfg = data.get("config", {}) if isinstance(data.get("config", {}), dict) else {}
        target_cols = DEFAULT_TARGET_COLS
        if "cols" in cfg:
            try:
                target_cols = max(24, min(60, int(cfg["cols"])))
            except Exception:
                pass
        font_px_override = None
        if "font_px" in cfg:
            try:
                font_px_override = int(cfg["font_px"])
            except Exception:
                font_px_override = None
        
        raster_threshold = None
        if "raster_threshold" in cfg:
            try:
                raster_threshold = int(cfg["raster_threshold"])
            except Exception:
                raster_threshold = None


        # Nuevo: elegir render explícito
        render = str(cfg.get("render", RENDER_MODE_DEFAULT)).lower().strip()
        force_raster = bool(cfg.get("force_raster", False))
        use_raster = force_raster or (render == "raster")

        lines = _build_ticket_lines(data)

        if use_raster:
            _print_lines_raster(
                lines,
                target_cols=target_cols,
                font_px_override=font_px_override
            )
        else:
            _print_lines_gdi(
                lines,
                target_cols=target_cols,
                font_px_override=font_px_override,
                force_raster=False,  # en GDI sólo cae a raster si falla Unicode
                raster_threshold=raster_threshold
            )

        _send_cut_command_raw()

        resp = jsonify({"status": "ok", "message": "Ticket enviado a la impresora", "render": "raster" if use_raster else "gdi"})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp
    except Exception as e:
        logging.exception("Fallo al imprimir")
        return jsonify({"status": "error", "message": f"Fallo al imprimir: {str(e)}"})

@app.route('/PrintAnularFactura', methods=['POST'])
def print_anular_factura():
    payload = request.get_json() or {}
    try:
        _ensure_any_font_loaded()  # solo afecta a GDI

        # ✅ config puede venir en root del payload (como tu PrintTicket) o dentro de payload.data
        cfg = {}
        if isinstance(payload.get("config"), dict):
            cfg = payload.get("config") or {}
        elif isinstance(payload.get("data"), dict) and isinstance(payload["data"].get("config"), dict):
            cfg = payload["data"].get("config") or {}

        target_cols = DEFAULT_TARGET_COLS
        if "cols" in cfg:
            try:
                target_cols = max(24, min(60, int(cfg["cols"])))
            except Exception:
                pass

        font_px_override = None
        if "font_px" in cfg:
            try:
                font_px_override = int(cfg["font_px"])
            except Exception:
                font_px_override = None

        render = str(cfg.get("render", RENDER_MODE_DEFAULT)).lower().strip()
        force_raster = bool(cfg.get("force_raster", False))
        use_raster = force_raster or (render == "raster")

        # ✅ ahora pasamos el payload completo, el builder ya sabe sacar payload.data si existe
        lines = _build_anular_factura_lines(payload)

        if use_raster:
            _print_lines_raster(lines, target_cols=target_cols, font_px_override=font_px_override)
        else:
            _print_lines_gdi(lines, target_cols=target_cols, font_px_override=font_px_override, force_raster=False)

        _send_cut_command_raw()

        resp = jsonify({"status": "ok", "message": "Ticket de anulación enviado a la impresora", "render": "raster" if use_raster else "gdi"})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp
    except Exception as e:
        logging.exception("Fallo al imprimir anulación")
        return jsonify({"status": "error", "message": f"Fallo al imprimir anulación: {str(e)}"})

@app.route('/test', methods=['GET'])
def test():
    return "running!"

if __name__ == '__main__':
    setup_logging()
    notify_start()
    app.run(host='127.0.0.1', port=5100, debug=False, use_reloader=False)
