# -*- coding: utf-8 -*-
# Part of StockSense. See LICENSE file for full copyright and licensing details.
{
    'name': 'StockSense - Dashboard & Adjustments',
    'version': '17.0.1.0.0',
    'summary': 'StockSense: inventory KPI dashboard, inventory adjustments and low stock alerts.',
    'description': """
StockSense :: Dashboard & Adjustments
=====================================
Adds the operational cockpit of StockSense on top of Odoo Inventory:

* **KPI Dashboard** (``stocksense.dashboard``): Total Products, Low Stock,
  Pending Receipts and Pending Deliveries, each with a drill-down action.
* **Inventory Adjustments** (``stock.adjustment`` / ``stock.adjustment.line``):
  documented, multi-line inventory count documents validated through
  ``stock.quant`` so that valuation, moves and traceability stay intact.
* **Low Stock Alerts**: scheduled action (``ir.cron``) that detects products
  below their reordering rule, emails the Inventory managers and keeps a
  warning activity on the affected products.

Assumptions: Odoo 17.0 Community, depends on ``stock`` and ``mail``.
    """,
    'author': 'StockSense',
    'website': 'https://github.com/manoj-gif-b/odoo-stocksense',
    'category': 'Inventory/Inventory',
    'license': 'LGPL-3',
    'depends': [
        'stock',
        'mail',
    ],
    'data': [
        'security/ir.model.access.csv',
        'data/stock_adjustment_data.xml',
        'views/stock_adjustment_views.xml',
        'views/dashboard_views.xml',
        'data/ir_cron_data.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
}
