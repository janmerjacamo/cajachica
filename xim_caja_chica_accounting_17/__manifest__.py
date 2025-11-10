{
    "name": "Caja Chica - Contabilidad (XIM) - Odoo 17",
    "summary": "Gestión de caja chica con control de facturas, impuestos y generación automática de asiento contable al liquidar",
    "description": "Módulo para registrar cajas chicas, agregar facturas, calcular IVA/IDP y generar automáticamente un account.move al liquidar. Diseñado para Odoo 17 y despliegue en Odoo.sh.",
    "author": "XIM Technology / Generated",
    "website": "https://example.com",
    "category": "Accounting",
    "version": "17.0.1.0.0",
    "depends": ["base", "account"],
    "data": [
        "data/ir_sequence_data.xml",
        "security/ir.model.access.csv",
        "views/caja_chica_views.xml"
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
