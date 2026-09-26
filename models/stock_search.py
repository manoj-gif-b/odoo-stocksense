# -*- coding: utf-8 -*-
# Part of StockSense. See LICENSE file for full copyright and licensing details.
"""StockSense - support fields of the dynamic filters.

The StockSense dynamic filters (``views/stock_search_views.xml``) let the
warehouse team filter and group the stock documents per **warehouse**, per
**location** and per **product category**.

Odoo only groups records on *stored* fields of the searched model: the web
client resolves the ``group_by`` context against the fields of the list model,
and the ORM refuses to group on anything else. Neither ``stock.picking`` nor
``stock.adjustment`` exposes a warehouse or a product category:

* a picking reaches its warehouse through its operation type and its products
  through its moves, so it only carries ``picking_type_id`` and ``move_ids``;
* an adjustment reaches its warehouse through the counted location and its
  products through its lines.

The small stored fields below fill that gap. They are pure reporting helpers:
no workflow uses them, and because they are stored the grouping stays a single
``read_group`` on the model -- the same technique core uses for
``stock.quant.warehouse_id`` / ``stock.quant.product_categ_id``.
"""

from odoo import api, fields, models


class StockPicking(models.Model):
    """Warehouse and product category of an operation, for the list grouping."""

    _inherit = 'stock.picking'

    ss_warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        related='picking_type_id.warehouse_id', store=True, index=True,
        help="Warehouse the operation belongs to, deduced from its operation "
             "type. Stored so the StockSense lists of operations can be "
             "grouped per warehouse.")

    ss_product_categ_id = fields.Many2one(
        'product.category', string='Product Category',
        compute='_compute_ss_product_categ_id', store=True, index=True,
        help="Product category of the products of the operation. Left empty "
             "when the operation holds products of several categories: the "
             "operation is then listed under 'No category'.")

    @api.depends(
        'move_ids',
        'move_ids.product_id',
        'move_ids.product_id.categ_id',
    )
    def _compute_ss_product_categ_id(self):
        """Keep the category only when the whole operation shares one."""
        for picking in self:
            moves = picking.move_ids.filtered(
                lambda move: move.state != 'cancel')
            categories = moves.product_id.categ_id
            picking.ss_product_categ_id = (
                categories[:1] if len(categories) == 1 else False)


class StockAdjustment(models.Model):
    """Warehouse and product category of a count, for the list grouping."""

    _inherit = 'stock.adjustment'

    ss_warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse',
        compute='_compute_ss_warehouse_id', store=True, index=True,
        help="Warehouse the counted location belongs to.")

    ss_product_categ_id = fields.Many2one(
        'product.category', string='Product Category',
        compute='_compute_ss_product_categ_id', store=True, index=True,
        help="Product category of the counted products. Left empty when the "
             "count covers several categories: the adjustment is then listed "
             "under 'No category'.")

    @api.depends('location_id')
    def _compute_ss_warehouse_id(self):
        """Locate the warehouse owning the counted location."""
        warehouse_model = self.env['stock.warehouse']
        for adjustment in self:
            location = adjustment.location_id
            adjustment.ss_warehouse_id = (
                warehouse_model.search(
                    [('view_location_id', 'parent_of', location.id)], limit=1)
                if location else False)

    @api.depends(
        'line_ids',
        'line_ids.product_id',
        'line_ids.product_id.categ_id',
    )
    def _compute_ss_product_categ_id(self):
        """Keep the category only when the whole count shares one."""
        for adjustment in self:
            categories = adjustment.line_ids.product_id.categ_id
            adjustment.ss_product_categ_id = (
                categories[:1] if len(categories) == 1 else False)
