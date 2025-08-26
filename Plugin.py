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

def imprimir_ticket_win32(data):
    factura = data.get("factura", {})
    productos = data.get("detalle", [])

    empresa = {
        "nombre": "Farmacia Sexta Avenida",
        "direccion": "C. 4, Heredia, Los Lagos",
        "telefono": "2222-3333"
    }

    fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
    cliente = factura.get("NombreCliente", "Consumidor Final")
    identificacion = factura.get("IdentificacionCliente", "")
    metodo_pago = "Efectivo"  # Ajusta si tienes esa información
    total = factura.get("PrecioTotal", 0)


    contenido = ""
    contenido += "\x1B\x74\x12"  # ESC t 18 → Latin America (page code 18)
    contenido += f"{empresa['nombre']}\n"
    contenido += f"{empresa['direccion']}\n"
    contenido += f"Tel: {empresa['telefono']}\n"
    contenido += f"Fecha: {fecha}\n"
    contenido += "-" * 32 + "\n"
    contenido += f"Cliente: {cliente}\n"
    if identificacion:
        contenido += f"ID: {identificacion}\n"
    contenido += "-" * 32 + "\n"

    subtotal = 0
    impuestos_totales = 0

    for prod in productos:
        nombre = prod.get("Nombre", "")[:20]
        cantidad = prod.get("Cantidad", 1)
        precio_unitario = prod.get("PrecioUnitario", 0)
        descuento = prod.get("Descuento", 0)
        precio_total = prod.get("PrecioTotal", 0)
        impuestos = prod.get("Impuestos", 0)
        es_boni = prod.get("EsBonificacion", False)

        # Calcular subtotal acumulado sin impuestos
        subtotal += (precio_total / (1 + impuestos / 100)) if not es_boni else 0
        impuestos_totales += (precio_total - (precio_total / (1 + impuestos / 100))) if not es_boni else 0

        boni_txt = " (Bonif.)" if es_boni else ""
        contenido += f"{nombre:20} x{cantidad}\n"
        contenido += f"  {precio_unitario:.2f}  Desc: c{descuento:.2f}{boni_txt}\n"
        contenido += f"  Total: {precio_total:.2f}\n"

    contenido += "-" * 32 + "\n"
    contenido += f"Subtotal: c{subtotal:.2f}\n"
    contenido += f"IVA: {impuestos_totales:.2f}\n"
    contenido += "-" * 32 + "\n"
    contenido += f"Total: \x9E{total:.2f}\n"
    contenido += f"Pago: {metodo_pago}\n"
    contenido += "-" * 32 + "\n"
    contenido += "¡Gracias por su compra!\n"
    contenido += "\n" * 4
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
