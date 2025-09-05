# server.py
from flask import Flask, request, jsonify
from datetime import datetime
import win32print
from flask_cors import CORS

app = Flask(__name__)
CORS(app, supports_credentials=True, origins=["*"])

@app.route('/PrintTicket', methods=['POST'])
def print_ticket():
    data = request.get_json()

    try:
        imprimir_ticket_win32(data)

        # Crear la respuesta a partir de resultJson y añadir el encabezado CORS
        response = jsonify({"status": "ok", "message": "Ticket enviado a la impresora"})
        response.headers.add("Access-Control-Allow-Origin", "*")
        
        return response
    except Exception as e:
        return jsonify({"status": "error", "message": f"Fallo al imprimir: {str(e)}"})

def feed_lineas(n=1):
    return "\x1B\x64" + chr(n)   # ESC d n

def imprimir_ticket_win32(data):
    factura = data.get("factura", {})
    productos = data.get("detalle", [])

    empresa = {
        "nombre": "FARMACIA SEXTA AVENIDA S.R.L.",
        "direccion": "HEREDIA CENTRO, COSTADO NORTE MERCADO MUNICIPAL",
        "identificacion": "3-102-167724"
    }

    ALIGN_LEFT   = "\x1B\x61\x00"
    ALIGN_CENTER = "\x1B\x61\x01"
    ALIGN_RIGHT  = "\x1B\x61\x02"

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cliente = factura.get("NombreCliente", "Consumidor Final")
    identificacion = factura.get("IdentificacionCliente", "")
    metodo_pago = factura.get("MetodoPago") 
    total = factura.get("PrecioTotal", 0)
    noFactura = factura.get("NoFactura", "")
    vendedor = factura.get("Vendedor", "")

    contenido = ""
    contenido += "\x1B\x74\x12"  # ESC t 18 → Latin America (page code 18)
    contenido += ALIGN_CENTER
    contenido += f"{empresa['nombre']}\n"
    contenido += f"{empresa['identificacion']}\n"
    contenido += f"{empresa['direccion']}\n"
    contenido += ALIGN_LEFT
    contenido += "-" * 32 + "\n"
    contenido += f"FECHA: {fecha}\n"
    contenido += f"CLIENTE: {cliente}\n"
    if identificacion:
        contenido += f"IDENTIFICACION: {identificacion}\n"
    contenido += "-" * 32 + "\n"
    
    contenido += f"VENDEDOR: {vendedor}\n"
    contenido += f"FACTURA NO. : {noFactura}\n"
    
    contenido += "-" * 32 + "\n"
    contenido += f"SR(a). ESTIMADO CLIENTE\n"
    contenido += "-" * 32 + "\n"
    contenido += f"CODIGO\n"
    contenido += f"DESCRIPCION\n"
    ANCHO = 32
    left = 16
    right = ANCHO - left
    contenido += f"{'UNIDADES':<{left}}{'FRACCIONES':>{right}}\n"
    contenido += f"{'PRECIO UNITARIO':<{left}}{'PRECIO FRACCION':>{right}}\n"
    contenido += f"DESCUENTO\n"
    contenido += f"IMPUESTO\n"
    contenido += "-" * 32 + "\n"

    subtotal = 0
    impuestos_totales = 0

    for prod in productos:
        codigo = prod.get("Codigo", "")[:20]
        nombre = prod.get("Nombre", "")[:20]
        unidades = prod.get("Cantidad", 0)
        fracciones = prod.get("CantidadFracciones", 0)
        precio_unitario = prod.get("PrecioUnitario", 0)
        precio_fraccion = prod.get("TotalFraccionario", 0)
        descuento = prod.get("PerDescuento", 0)
        precio_total = prod.get("PrecioTotal", 0)
        precioImpuestos = prod.get("PrecioImpuesto", 0)
        impuestos = prod.get("Impuestos", 0)
        es_boni = prod.get("EsBonificacion", False)

        # Calcular subtotal acumulado sin impuestos
        subtotal += (precio_total / (1 + impuestos / 100)) if not es_boni else 0
        impuestos_totales += (precio_total - (precio_total / (1 + impuestos / 100))) if not es_boni else 0

        boni_txt = " (Bonif.)" if es_boni else ""
        contenido += f"{codigo:20}\n"
        contenido += f"{nombre:20}\n"
        contenido += f"UNID. x{unidades} FRACC. x{fracciones}\n"
        contenido += f"PRECIO UNIT. {precio_unitario:.2f} TOTAL FRACC. {precio_fraccion:.2f}\n"
        contenido += f"DESC. {descuento:.2f}\n"
        contenido += f"I.V.A. {impuestos:.2f}% MONTO I.V.A: {precioImpuestos:.2f}\n"
        contenido += f"  TOTAL: {precio_total:.2f}\n"
        contenido += feed_lineas(2) 

    contenido += "-" * 32 + "\n"
    contenido += f"SUBTOTAL: {subtotal:.2f}\n"
    contenido += f"I.V.A: {impuestos_totales:.2f}\n"
    contenido += "-" * 32 + "\n"
    contenido += f"TOTAL: {total:.2f}\n"
    contenido += f"METODO PAGO: {metodo_pago}\n"
    contenido += "-" * 32 + "\n"
    contenido += "¡GRACIAS POR SU COMPA!\n"
    contenido += "NO SE ACEPTAN DEVOLUCIONES\n"
    contenido += feed_lineas(2) 
    contenido += ALIGN_CENTER
    contenido += "Autrizado mediante resolucion No. DGT-R-\n"  
    contenido += "033-2019 del 20 de Junio del 2019.\n" 
    contenido += "Version 4.3\n"   
    contenido += "\x1D\x56\x42\x00"  # comando ESC/POS para corte


    # Envío a impresora
    printer_name = win32print.GetDefaultPrinter()
    hPrinter = None

    try:
        hPrinter = win32print.OpenPrinter(printer_name)
        hJob = win32print.StartDocPrinter(hPrinter, 1, ("Factura", None, "RAW"))
        win32print.StartPagePrinter(hPrinter)
        win32print.WritePrinter(hPrinter, contenido.encode("latin1"))
        win32print.EndPagePrinter(hPrinter)
        win32print.EndDocPrinter(hPrinter)
    except Exception as e:
        raise Exception(f"Error al imprimir: {str(e)}")
    finally:
        if hPrinter:
            win32print.ClosePrinter(hPrinter)



if __name__ == '__main__':
    app.run(port=5100)
