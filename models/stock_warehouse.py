# -*- coding: utf-8 -*-
"""StockSense - data used by the Stock Valuation report."""
from collections import defaultdict

from odoo import fields, models


class StockWarehouse(models.Model):
    """Expose the valuation rows of a warehouse to the QWeb report."""

    _inherit = 'stock.warehouse'

    def _ss_get_valuation_data(self):
        """Return the stock valuation rows of the warehouse.

        The rows are aggregated from the internal quants of the warehouse stock
        location (children included) and valued with the product cost
        (``standard_price``).

        ``search`` + in-Python aggregation is used on purpose: the signature of
        ``_read_group`` differs between Odoo 16 (domain, fields, groupby) and
        Odoo 17 (domain, groupby, aggregates), which would break the promise of
        a single 16/17 compatible code base.

        :return: dict with ``lines``, ``total_quantity``, ``total_value``,
            ``currency`` and ``generated_on``.
        """
        self.ensure_one()
        lines = []
        total_quantity = 0.0
        total_value = 0.0
        if self.lot_stock_id:
            quants = self.env['stock.quant'].search([
                ('location_id', 'child_of', self.lot_stock_id.id),
                ('quantity', '!=', 0.0),
            ])
            quantities = defaultdict(float)
            for quant in quants:
                quantities[quant.product_id] += quant.quantity
            for product, quantity in quantities.items():
                unit_price = product.standard_price
                value = quantity * unit_price
                total_quantity += quantity
                total_value += value
                lines.append({
                    'product': product.display_name,
                    'default_code': product.default_code or '',
                    'warehouse': self.display_name,
                    'quantity': quantity,
                    'uom': product.uom_id.display_name,
                    'unit_price': unit_price,
                    'value': value,
                })
        lines.sort(key=lambda line: line['product'])
        return {
            'lines': lines,
            'total_quantity': total_quantity,
            'total_value': total_value,
            'currency': self.company_id.currency_id,
            'generated_on': fields.Datetime.now(),
        }
