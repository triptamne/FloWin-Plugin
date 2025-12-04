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
    lo, hi = 9, 28
    best = 14
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
    return max(9, min(best, 26))

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
        descuento        = prod.get("PerDescuento", 0) or 0.0
        precio_desc      = prod.get("Descuento", 0) or 0.0
        precio_total     = prod.get("PrecioTotal", 0) or 0.0
        impuestos        = float(prod.get("Impuestos", 0) or 0.0)
        es_boni          = bool(prod.get("EsBonificacion", False))
        bonificacion     = prod.get("BonificacionCalculada", 0) or 0.0
        precioImpuestos  = _get_precio_impuestos(prod)

        base_sin_iva = (precio_total / (1 + impuestos / 100)) if (not es_boni and impuestos) else (0 if es_boni else precio_total)
        subtotal          += base_sin_iva if not es_boni else 0
        impuestos_totales += (precio_total - base_sin_iva) if not es_boni else 0

        lines.append(f"{codigo}")
        lines.append(f"{nombre}")
        lines.append(f"UNID.: x{unidades}    FRACC.: x{fracciones}")
        lines.append(f"PRECIO UNIT.: {_format_crc(precio_unitario)}    TOTAL FRACC.: {_format_crc(precio_fraccion)}")
        lines.append(f"BONIF.: x{bonificacion}")
        lines.append(f"DESC.: {descuento:.2f}%    MONTO DESC.: {_format_crc(precio_desc)}")
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
    # Ajuste binario a columnas usando 'M' * cols como patrón
    Image, ImageDraw, ImageFont, _ = _try_import_pillow()
    if not Image:
        return 14
    ttf = ttf_path or _choose_ttf_for_pillow()
    if not ttf:
        return 14
    lo, hi = 9, 28
    best = 14
    while lo <= hi:
        mid = (lo + hi) // 2
        font = ImageFont.truetype(ttf, mid)
        img = Image.new("RGB", (usable_w, mid + 20), "white")
        draw = ImageDraw.Draw(img)
        test = "M" * int(max(10, target_cols))
        try:
            width = draw.textlength(test, font=font)
        except Exception:
            bbox = draw.textbbox((0,0), test, font=font)
            width = (bbox[2]-bbox[0]) if bbox else usable_w+1
        if width <= usable_w:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return max(9, min(best, 26))

def _print_lines_raster(lines, target_cols=DEFAULT_TARGET_COLS, font_px_override=None):
    # Crear DC de impresora y enviar páginas dibujando cada línea como bitmap con wrap
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

    pw, ph, dpx, dpy = _get_printable_metrics(hDC)
    left   = LEFT_MARGIN
    top    = TOP_MARGIN
    right  = pw - RIGHT_MARGIN
    bottom = ph - BOTTOM_MARGIN
    usable_w = max(1, right - left)

    Image, ImageDraw, ImageFont, ImageWin = _try_import_pillow()
    if not Image:
        raise RuntimeError("Pillow no está disponible para renderizar en raster.")

    ttf = _choose_ttf_for_pillow()
    if not ttf:
        raise RuntimeError("No se encontró ninguna TTF en /fonts (DejaVu/Noto).")

    # Tamaño de fuente
    if isinstance(font_px_override, int) and font_px_override >= 8:
        font_px = font_px_override
    else:
        font_px = _pick_font_height_fit_cols_pillow(usable_w, target_cols, ttf)

    font = ImageFont.truetype(ttf, font_px)

    # Métricas de línea
    try:
        ascent, descent = font.getmetrics()
        line_h = ascent + descent + 2  # pequeño extra
    except Exception:
        line_h = int(font_px * 1.4)

    hDC.StartDoc("Factura")
    hDC.StartPage()

    y = top
    for raw_line in lines:
        # Preparar canvas de medición para wrap
        tmp_img = Image.new("RGB", (usable_w, line_h + 40), "white")
        tmp_draw = ImageDraw.Draw(tmp_img)

        # Detectar si es par "K: V" y tratar de mantener una línea si cabe
        k, v = _maybe_split_kv(raw_line)
        if k and v:
            # Probar si entra en una sola línea con etiqueta izquierda y valor derecha
            # Estrategia simple: medir ambos y dibujar por separado
            # Línea en blanco para altura:
            needed_h = line_h
            if y + needed_h >= bottom:
                hDC.EndPage(); hDC.StartPage(); y = TOP_MARGIN

            # Render etiqueta (izquierda)
            lbl = k
            val = v
            try:
                lbl_w = tmp_draw.textlength(lbl, font=font)
                val_w = tmp_draw.textlength(val, font=font)
            except Exception:
                lbl_w = tmp_draw.textbbox((0,0), lbl, font=font)[2]
                val_w = tmp_draw.textbbox((0,0), val, font=font)[2]

            mid = left + usable_w // 2
            # Dibuja etiqueta
            img_lbl = Image.new("RGB", (mid - left, line_h), "white")
            d_lbl = ImageDraw.Draw(img_lbl)
            d_lbl.text((0, 0), lbl, font=font, fill="black")
            dib_lbl = ImageWin.Dib(img_lbl)
            dib_lbl.draw(hDC.GetHandleOutput(), (left, y, mid, y + line_h))

            # Dibuja valor alineado a la derecha
            img_val = Image.new("RGB", (right - mid, line_h), "white")
            d_val = ImageDraw.Draw(img_val)
            # x para alinear derecha
            x_val = max(0, (right - mid) - val_w)
            d_val.text((x_val, 0), val, font=font, fill="black")
            dib_val = ImageWin.Dib(img_val)
            dib_val.draw(hDC.GetHandleOutput(), (mid, y, right, y + line_h))

            y += needed_h
            continue

        # Word-wrap normal
        wrapped = _wrap_text_by_width(raw_line, tmp_draw, font, usable_w)
        for piece in wrapped:
            if y + line_h >= bottom:
                hDC.EndPage(); hDC.StartPage(); y = TOP_MARGIN
            img_line = Image.new("RGB", (usable_w, line_h), "white")
            d_line = ImageDraw.Draw(img_line)
            d_line.text((0, 0), piece, font=font, fill="black")
            dib = ImageWin.Dib(img_line)
            dib.draw(hDC.GetHandleOutput(), (left, y, right, y + line_h))
            y += line_h

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
            )

        _send_cut_command_raw()

        resp = jsonify({"status": "ok", "message": "Ticket enviado a la impresora", "render": "raster" if use_raster else "gdi"})
        resp.headers.add("Access-Control-Allow-Origin", "*")
        return resp
    except Exception as e:
        logging.exception("Fallo al imprimir")
        return jsonify({"status": "error", "message": f"Fallo al imprimir: {str(e)}"})

@app.route('/test', methods=['GET'])
def test():
    return "running!"

if __name__ == '__main__':
    setup_logging()
    notify_start()
    app.run(host='127.0.0.1', port=5100, debug=False, use_reloader=False)
