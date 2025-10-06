# server.py
from flask import Flask, request, jsonify
from datetime import datetime
from flask_cors import CORS

import os, sys
import ctypes
from ctypes import wintypes

import win32print
import win32ui
import win32con

# ------------------------------------------------------------
# Config
# ------------------------------------------------------------
# Ruta(s) posibles de la fuente. Puedes dejar solo la que tengas.

def resource_path(*relative):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *relative)

# Sustituye tu FONTS_DIR y FONT_FILES_TRY por esto:
FONTS_DIR = resource_path("fonts", "DejaVuSans")
FONT_FILES_TRY = [
    os.path.join(FONTS_DIR, "DejaVuSans.ttf"),
    os.path.join(FONTS_DIR, "DejaVuSansMono.ttf"),
]

# Nombre lógico de la familia que crea el TTF (coincide con el "Font name" interno).
# Si usas DejaVuSansMono.ttf, cambia a "DejaVu Sans Mono".
PREFERRED_FONT_NAME = "DejaVu Sans"        # o "DejaVu Sans Mono"

# Tamaño de letra (en "logical units" negativos = altura en píxeles aprox.)
FONT_HEIGHT = -20  # ~10 pt en muchos drivers térmicos; ajusta a gusto
LINE_SPACING = 24  # separación vertical por línea
LEFT_MARGIN = 20   # margen izquierdo en unidades del DC
TOP_MARGIN = 20    # margen superior
PAGE_WIDTH_CHARS = 32  # si usas monoespaciada, te sirve de referencia

# ------------------------------------------------------------
# Util: cargar fuente TTF en memoria (privada, sin instalar)
# ------------------------------------------------------------
FR_PRIVATE = 0x10

def _load_ttf_private(ttf_path: str) -> bool:
    """Carga una fuente TTF en la sesión actual (privada) sin instalarla en el sistema."""
    if not os.path.isfile(ttf_path):
        return False
    AddFontResourceExW = ctypes.windll.gdi32.AddFontResourceExW
    AddFontResourceExW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.PVOID]
    AddFontResourceExW.restype = wintypes.INT

    added = AddFontResourceExW(ttf_path, FR_PRIVATE, None)
    return added > 0

def _ensure_font_loaded() -> str:
    """
    Intenta cargar alguna de las fuentes definidas en FONT_FILES_TRY.
    Devuelve el nombre de la familia a usar (PREFERRED_FONT_NAME por defecto).
    """
    loaded_any = False
    for p in FONT_FILES_TRY:
        if _load_ttf_private(p):
            loaded_any = True
    # Aunque no podamos verificar el "face name" exacto aquí, si cargó al menos una,
    # intentamos con PREFERRED_FONT_NAME. Si usaste la Mono, ajusta el nombre arriba.
    return PREFERRED_FONT_NAME if loaded_any else PREFERRED_FONT_NAME

def _get_precio_impuestos(prod):
    """
    Devuelve el monto de impuestos del producto.
    Prioriza los campos del payload (PrecioImpuestos / PrecioImpuesto).
    Si no existen o son 0, lo calcula desde PrecioTotal e Impuestos%.
    """
    # 1) Normaliza claves del payload
    precio_imp = prod.get("PrecioImpuestos", None)
    if precio_imp in (None, 0, 0.0):
        precio_imp = prod.get("PrecioImpuesto", None)

    # 2) Si sigue faltando o es 0, lo calculamos
    try:
        impuestos = float(prod.get("Impuestos", 0) or 0)
        es_boni = bool(prod.get("EsBonificacion", False))
        precio_total = float(prod.get("PrecioTotal", 0) or 0)

        if (precio_imp is None or float(precio_imp) == 0.0) and not es_boni and impuestos > 0 and precio_total > 0:
            base_sin_iva = precio_total / (1 + (impuestos / 100.0))
            precio_imp = precio_total - base_sin_iva

        # Última defensa
        if precio_imp is None:
            precio_imp = 0.0
        return float(precio_imp)
    except Exception:
        return 0.0


# ------------------------------------------------------------
# Construcción del texto del ticket (Unicode, con ₡)
# ------------------------------------------------------------
def _format_crc(value):
    """Formatea número en colones usando símbolo ₡ (Unicode)."""
    try:
        n = float(value or 0)
        return f"₡{n:,.2f}"
    except Exception:
        return "₡0.00"

def _build_ticket_lines(data):
    factura = data.get("factura", {})
    productos = data.get("detalle", [])

    empresa = {
        "nombre": "FARMACIA SEXTA AVENIDA S.R.L.",
        "direccion": "HEREDIA CENTRO, COSTADO NORTE MERCADO MUNICIPAL",
        "identificacion": "3-102-167724"
    }

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cliente = factura.get("NombreCliente", "Consumidor Final")
    identificacion = factura.get("IdentificacionCliente", "")
    metodo_pago = factura.get("MetodoPago", "")
    total = factura.get("PrecioTotal", 0)
    noFactura = factura.get("NoFactura", "")
    vendedor = factura.get("Vendedor", "")

    lines = []
    sep = "-" * PAGE_WIDTH_CHARS

    # Encabezado centrado "a mano": aquí solo alineamos visualmente con espacios si lo deseas.
    # Tip: si usas DejaVu Sans Mono, todo queda proporcional en columnas fijas.
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
    lines.append(f"FACTURA NO. : {noFactura}")
    lines.append(sep)
    lines.append("SR(a). ESTIMADO CLIENTE")
    lines.append(sep)
    lines.append("CODIGO")
    lines.append("DESCRIPCION")

    # Sub-encabezados de columnas (si usas monoespaciada, puedes fijar columnas)
    lines.append(f"{'UNIDADES':<16}{'FRACCIONES':>16}")
    lines.append(f"{'PRECIO UNITARIO':<16}{'PRECIO FRACCION':>16}")
    lines.append("BONIFICACION")
    lines.append("DESCUENTO")
    lines.append("IMPUESTO")
    lines.append(sep)

    subtotal = 0.0
    impuestos_totales = 0.0

    for prod in productos:
        codigo = str(prod.get("Codigo", ""))[:20]
        nombre = str(prod.get("Nombre", ""))[:40]  # puedes ampliar si te cabe
        unidades = prod.get("Cantidad", 0) or 0
        fracciones = prod.get("CantidadFracciones", 0) or 0
        precio_unitario = prod.get("PrecioUnitario", 0) or 0.0
        precio_fraccion = prod.get("TotalFraccionario", 0) or 0.0
        descuento = prod.get("PerDescuento", 0) or 0.0
        precio_descuento = prod.get("Descuento", 0) or 0.0
        precio_total = prod.get("PrecioTotal", 0) or 0.0
        impuestos = float(prod.get("Impuestos", 0) or 0.0)
        es_boni = bool(prod.get("EsBonificacion", False))
        bonificacion = prod.get("BonificacionCalculada", 0) or 0.0
        precioImpuestos = _get_precio_impuestos(prod)

        base_sin_iva = (precio_total / (1 + impuestos / 100)) if (not es_boni and impuestos) else (0 if es_boni else precio_total)
        subtotal += base_sin_iva if not es_boni else 0
        impuestos_totales += (precio_total - base_sin_iva) if not es_boni else 0

        # Detalle
        lines.append(f"{codigo}")
        lines.append(f"{nombre}")
        lines.append(f"UNID. x{unidades} FRACC. x{fracciones}")
        lines.append(f"PRECIO UNIT. {_format_crc(precio_unitario)}  TOTAL FRACC. {_format_crc(precio_fraccion)}")
        lines.append(f"BONIF. x{bonificacion}")
        lines.append(f"DESC. {descuento:.2f}% MONTO DESC: {_format_crc(precio_descuento)}")
        lines.append(f"I.V.A. {impuestos:.2f}%  MONTO I.V.A: {_format_crc(precioImpuestos)}")
        lines.append(f"  TOTAL: {_format_crc(precio_total)}")
        lines.append("")  # línea en blanco (feed)

    lines.append(sep)
    lines.append(f"SUBTOTAL: {_format_crc(subtotal)}")
    lines.append(f"I.V.A: {_format_crc(impuestos_totales)}")
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

# ------------------------------------------------------------
# Impresión GDI (Unicode con fuente TTF)
# ------------------------------------------------------------
def _print_lines_gdi(lines, font_name: str):
    # Abrir DC de impresora predeterminada
    printer_name = win32print.GetDefaultPrinter()
    hDC = win32ui.CreateDC()
    hDC.CreatePrinterDC(printer_name)

    # Iniciar documento/página
    hDC.StartDoc("Factura")
    hDC.StartPage()

    # Crear y seleccionar fuente
    # Nota: 'height' negativo especifica la altura en unidades lógicas (~píxeles)
    font_spec = {
        "name": font_name,
        "height": FONT_HEIGHT,
        "weight": win32con.FW_NORMAL,
        "charset": win32con.DEFAULT_CHARSET,
        "quality": win32con.CLEARTYPE_QUALITY,
    }
    font = win32ui.CreateFont(font_spec)
    hDC.SelectObject(font)

    # Escribir línea a línea
    x = LEFT_MARGIN
    y = TOP_MARGIN

    for line in lines:
        # TextOutW acepta Unicode (Python str)
        hDC.TextOut(x, y, line)
        y += LINE_SPACING

    # Cerrar página/documento
    hDC.EndPage()
    hDC.EndDoc()

    # Limpieza explícita de objetos GDI
    del font
    del hDC

# ------------------------------------------------------------
# Corte de papel (opcional) vía RAW ESC/POS
# ------------------------------------------------------------
def _send_cut_command_raw():
    """
    Envía un job 'RAW' con GS V B 0 para cortar papel.
    Requiere que la impresora soporte ESC/POS y/o que el driver pase RAW.
    Si tu driver ya auto-corta al final del job GDI, no necesitas esto.
    """
    try:
        printer_name = win32print.GetDefaultPrinter()
        hPrinter = win32print.OpenPrinter(printer_name)
        try:
            hJob = win32print.StartDocPrinter(hPrinter, 1, ("Cut", None, "RAW"))
            win32print.StartPagePrinter(hPrinter)
            # GS V B n  -> b'\x1D\x56\x42\x00'  (corte total)
            win32print.WritePrinter(hPrinter, b'\x1D\x56\x42\x00')
            win32print.EndPagePrinter(hPrinter)
            win32print.EndDocPrinter(hPrinter)
        finally:
            win32print.ClosePrinter(hPrinter)
    except Exception:
        # Silencioso: algunos drivers no permiten combinar GDI + RAW
        pass

# ------------------------------------------------------------
# Flask app
# ------------------------------------------------------------
app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["*"])

@app.route('/PrintTicket', methods=['POST'])
def print_ticket():
    data = request.get_json()
    try:
        # 1) Asegurar fuente cargada (privada)
        face = _ensure_font_loaded()

        # 2) Construir contenido (Unicode con ₡)
        lines = _build_ticket_lines(data)

        # 3) Imprimir con GDI
        _print_lines_gdi(lines, font_name=face)

        # 4) (Opcional) Enviar comando de corte como job RAW
        _send_cut_command_raw()

        response = jsonify({"status": "ok", "message": "Ticket enviado a la impresora"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        return response
    except Exception as e:
        return jsonify({"status": "error", "message": f"Fallo al imprimir: {str(e)}"})

@app.route('/test', methods=['GET'])
def test():
    return "running!"

if __name__ == '__main__':
    app.run(port=5100)
