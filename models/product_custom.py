# -*- coding: utf-8 -*-
"""StockSense Core - product master data.

This file contains:

* ``stocksense.reorder.rule``: minimum / maximum replenishment rule, optionally
  scoped to a warehouse and to a source location.
* ``product.template`` (inherited): StockSense SKU, reorder rules and initial
  stock fields.
* ``product.category`` (inherited): default reorder thresholds reused when a
  new reorder rule is created on a product of the category.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression


class StockSenseReorderRule(models.Model):
    """Minimum / maximum quantity rule used by StockSense to replenish a product."""

    _name = 'stocksense.reorder.rule'
    _description = 'StockSense Reorder Rule'
    _order = 'sequence, id'

    name = fields.Char(
        string='Rule', compute='_compute_name', store=True, readonly=True,
        help="Automatically computed as '<Product> @ <Warehouse>'.")
    active = fields.Boolean(default=True)
    sequence = fields.Integer(
        default=10, help="Rules are evaluated in increasing sequence order.")
    product_tmpl_id = fields.Many2one(
        'product.template', string='Product', required=True, index=True,
        ondelete='cascade', help="Product the reorder rule applies to.")
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', index=True, check_company=True,
        help="Warehouse the rule applies to. Leave empty to apply the rule to "
             "every warehouse of the company.")
    location_id = fields.Many2one(
        'stock.location', string='Source Location', check_company=True,
        domain="[('usage', 'in', ['internal', 'transit'])]",
        help="Location whose quantity is compared to the reorder point. Leave "
             "empty to use the stock location of the warehouse.")
    uom_id = fields.Many2one(
        'uom.uom', string='Unit of Measure',
        related='product_tmpl_id.uom_id', readonly=True)
    min_qty = fields.Float(
        string='Reorder Point', digits='Product Unit of Measure', default=0.0,
        help="When the available quantity drops below this value, StockSense "
             "flags the product for replenishment.")
    max_qty = fields.Float(
        string='Maximum Quantity', digits='Product Unit of Measure', default=0.0,
        help="Target quantity when replenishing the product. Keep it to 0 to "
             "replenish up to the reorder point only.")
    qty_multiple = fields.Float(
        string='Multiple Quantity', digits='Product Unit of Measure', default=1.0,
        help="Replenishment quantities are rounded up to a multiple of this value.")
    trigger = fields.Selection(
        [('auto', 'Automatic'), ('manual', 'Manual')], string='Trigger',
        default='auto', required=True,
        help="Automatic: StockSense computes the replenishment proposal. "
             "Manual: the rule is only used for reporting and alerts.")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        index=True, required=True, help="Company the rule belongs to.")

    # ------------------------------------------------------------------
    # Compute / defaults
    # ------------------------------------------------------------------
    @api.depends('product_tmpl_id.name', 'warehouse_id.name')
    def _compute_name(self):
        for rule in self:
            rule.name = '%s @ %s' % (
                rule.product_tmpl_id.display_name or _('New Product'),
                rule.warehouse_id.display_name or _('All Warehouses'),
            )

    @api.model
    def default_get(self, fields_list):
        """Propose the default thresholds configured on the product category."""
        res = super().default_get(fields_list)
        product_tmpl_id = res.get('product_tmpl_id') or self.env.context.get(
            'default_product_tmpl_id')
        if not product_tmpl_id:
            return res
        template = self.env['product.template'].browse(product_tmpl_id)
        defaults = {
            'min_qty': template.categ_id.ss_default_reorder_point,
            'max_qty': template.categ_id.ss_default_reorder_max_qty,
            'company_id': template.company_id.id or self.env.company.id,
        }
        for field_name, value in defaults.items():
            if field_name in fields_list and field_name not in res:
                res[field_name] = value
        return res

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('min_qty', 'max_qty')
    def _check_thresholds(self):
        for rule in self:
            if rule.min_qty < 0.0 or rule.max_qty < 0.0:
                raise ValidationError(_(
                    "The reorder point and the maximum quantity of \"%s\" cannot "
                    "be negative.") % rule.display_name)
            if rule.max_qty and rule.max_qty < rule.min_qty:
                raise ValidationError(_(
                    "The maximum quantity of \"%s\" must be greater than or equal "
                    "to its reorder point.") % rule.display_name)

    @api.constrains('qty_multiple')
    def _check_qty_multiple(self):
        for rule in self:
            if rule.qty_multiple <= 0.0:
                raise ValidationError(_(
                    "The multiple quantity of \"%s\" must be strictly positive."
                ) % rule.display_name)

    @api.constrains('product_tmpl_id', 'warehouse_id', 'company_id', 'active')
    def _check_unicity(self):
        for rule in self.filtered('active'):
            duplicate = self.search_count([
                ('id', '!=', rule.id),
                ('active', '=', True),
                ('product_tmpl_id', '=', rule.product_tmpl_id.id),
                ('warehouse_id', '=', rule.warehouse_id.id or False),
                ('company_id', '=', rule.company_id.id),
            ])
            if duplicate:
                raise ValidationError(_(
                    "An active reorder rule already exists for \"%(product)s\" on "
                    "%(warehouse)s.") % {
                        'product': rule.product_tmpl_id.display_name,
                        'warehouse': rule.warehouse_id.display_name or _('all warehouses'),
                    })


class ProductTemplate(models.Model):
    """StockSense master data of a product: SKU, reorder rules, initial stock."""

    _inherit = 'product.template'

    ss_sku = fields.Char(
        string='StockSense SKU', index=True, copy=False, tracking=True,
        help="Internal StockSense reference of the product. Unique per company "
             "when set; it is used by the replenishment engine and the reports.")
    ss_reorder_rule_ids = fields.One2many(
        'stocksense.reorder.rule', 'product_tmpl_id', string='Reorder Rules',
        copy=True)
    ss_reorder_rule_count = fields.Integer(
        string='Reorder Rules', compute='_compute_ss_reorder_rule_count')
    ss_reorder_point = fields.Float(
        string='Reorder Point', compute='_compute_ss_reorder_thresholds', store=True,
        digits='Product Unit of Measure',
        help="Lowest reorder point defined by the active reorder rules of the product.")
    ss_reorder_max_qty = fields.Float(
        string='Max Quantity', compute='_compute_ss_reorder_thresholds', store=True,
        digits='Product Unit of Measure',
        help="Highest maximum quantity defined by the active reorder rules of the product.")
    ss_initial_stock = fields.Float(
        string='Initial Stock', digits='Product Unit of Measure', default=0.0,
        copy=False,
        help="Quantity to put on hand the first time the product is introduced "
             "in a warehouse. Apply it with the 'Apply Initial Stock' button.")
    ss_initial_location_id = fields.Many2one(
        'stock.location', string='Initial Stock Location', copy=False,
        domain="[('usage', 'in', ['internal', 'transit'])]",
        help="Location receiving the initial stock. Leave empty to use the stock "
             "location of the company's warehouse.")
    ss_initial_stock_applied = fields.Boolean(
        string='Initial Stock Applied', default=False, copy=False, readonly=True,
        help="Set once the initial stock has been applied on the stock location.")
    stock_status = fields.Char(
        string='Stock Status', compute='_compute_stock_status',
        search='_search_stock_status',
        help="Readable stock status of the product, computed from the quantity "
             "on hand:\n"
             "- Out of Stock: nothing on hand,\n"
             "- Low Stock: quantity on hand below or equal to the reorder point,\n"
             "- In Stock: quantity on hand above the reorder point.\n"
             "A product without any reorder rule is never flagged as low stock.")

    # ------------------------------------------------------------------
    # Compute
    # ------------------------------------------------------------------
    @api.depends('ss_reorder_rule_ids')
    def _compute_ss_reorder_rule_count(self):
        for template in self:
            template.ss_reorder_rule_count = len(template.ss_reorder_rule_ids)

    @api.depends('ss_reorder_rule_ids.min_qty', 'ss_reorder_rule_ids.max_qty',
                 'ss_reorder_rule_ids.active')
    def _compute_ss_reorder_thresholds(self):
        for template in self:
            rules = template.ss_reorder_rule_ids.filtered('active')
            template.ss_reorder_point = min(rules.mapped('min_qty')) if rules else 0.0
            template.ss_reorder_max_qty = max(rules.mapped('max_qty')) if rules else 0.0

    @api.depends('qty_available', 'ss_reorder_point')
    def _compute_stock_status(self):
        """Flag the products that are out of stock or below their reorder point."""
        for template in self:
            if template.qty_available <= 0.0:
                template.stock_status = 'Out of Stock'
            elif (template.ss_reorder_point
                    and template.qty_available <= template.ss_reorder_point):
                template.stock_status = 'Low Stock'
            else:
                template.stock_status = 'In Stock'

    # ------------------------------------------------------------------
    # Stock status search (the field is not stored)
    # ------------------------------------------------------------------
    @api.model
    def _ss_stock_status_domains(self):
        """Return the search domain of the products in each stock status."""
        low_stock_ids = self._ss_low_stock_ids()
        return {
            'Out of Stock': [('qty_available', '<=', 0.0)],
            'Low Stock': [('id', 'in', low_stock_ids)],
            'In Stock': ['&', ('qty_available', '>', 0.0),
                         ('id', 'not in', low_stock_ids)],
        }

    @api.model
    def _ss_low_stock_ids(self):
        """Ids of the products on hand but at or below their reorder point."""
        candidates = self.search([
            ('qty_available', '>', 0.0),
            ('ss_reorder_point', '>', 0.0),
        ])
        return candidates.filtered(
            lambda template: template.qty_available <= template.ss_reorder_point,
        ).ids

    @api.model
    def _search_stock_status(self, operator, value):
        """Allow searching / filtering the products on ``stock_status``.

        The field is not stored, so Odoo delegates the search to this method
        (the same mechanism as ``_search_qty_available`` in stock).
        """
        if isinstance(value, (list, tuple, set)):
            values = [str(item).lower() for item in value]
        else:
            values = [str(value).lower()]
        domains = self._ss_stock_status_domains()
        partial = operator in ('ilike', 'not ilike', 'like', 'not like')
        matching = [
            status for status in domains
            if any(status.lower() == item
                   or (partial and item in status.lower())
                   for item in values)
        ]
        if operator in ('!=', 'not in', 'not ilike', 'not like'):
            selected = [status for status in domains if status not in matching]
        else:
            selected = matching
        if not selected:
            return [('id', '=', 0)]
        return expression.OR([domains[status] for status in selected])

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------
    @api.constrains('ss_sku', 'company_id')
    def _check_ss_sku_unicity(self):
        for template in self.filtered('ss_sku'):
            domain = [('id', '!=', template.id), ('ss_sku', '=ilike', template.ss_sku)]
            if template.company_id:
                domain = [('company_id', 'in', [template.company_id.id, False])] + domain
            if self.search_count(domain):
                raise ValidationError(_(
                    "The StockSense SKU \"%s\" is already used by another product."
                ) % template.ss_sku)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def action_ss_open_reorder_rules(self):
        """Open the reorder rules of the product."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reorder Rules'),
            'res_model': 'stocksense.reorder.rule',
            'view_mode': 'tree,form',
            'domain': [('product_tmpl_id', '=', self.id)],
            'context': {'default_product_tmpl_id': self.id},
        }

    def action_ss_apply_initial_stock(self):
        """Set the initial stock of the products on the target location."""
        for template in self:
            if template.ss_initial_stock_applied:
                raise UserError(_(
                    "The initial stock of \"%s\" has already been applied."
                ) % template.display_name)
            if template.ss_initial_stock <= 0.0:
                raise UserError(_(
                    "Define a positive initial stock on \"%s\" before applying it."
                ) % template.display_name)
            if template.tracking in ('lot', 'serial'):
                raise UserError(_(
                    "\"%s\" is tracked by lot or serial number: use an inventory "
                    "adjustment to define its initial stock."
                ) % template.display_name)
            product = template.product_variant_id
            if not product:
                raise UserError(_(
                    "No product variant is defined for \"%s\"."
                ) % template.display_name)
            location = template.ss_initial_location_id or template._ss_get_default_location()
            if not location:
                raise UserError(_(
                    "No internal stock location was found. Configure a warehouse "
                    "before applying the initial stock of \"%s\"."
                ) % template.display_name)
            self.env['stock.quant']._update_available_quantity(
                product, location, template.ss_initial_stock)
            template.write({
                'ss_initial_stock_applied': True,
                'ss_initial_location_id': location.id,
            })
            template.message_post(body=_(
                "Initial stock applied: %(qty)s %(uom)s on %(location)s."
            ) % {
                'qty': template.ss_initial_stock,
                'uom': template.uom_id.display_name or '',
                'location': location.display_name,
            })
        return True

    def _ss_get_default_location(self):
        """Return the location used when no initial stock location is set."""
        self.ensure_one()
        company = self.company_id or self.env.company
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', company.id)], limit=1)
        if warehouse.lot_stock_id:
            return warehouse.lot_stock_id
        return self.env['stock.location'].search([
            ('usage', '=', 'internal'),
            '|', ('company_id', '=', False), ('company_id', '=', company.id),
        ], limit=1)


class ProductCategory(models.Model):
    """Default StockSense replenishment thresholds defined per category."""

    _inherit = 'product.category'

    ss_default_reorder_point = fields.Float(
        string='Default Reorder Point', digits='Product Unit of Measure',
        help="Proposed as reorder point of the new reorder rules created on "
             "products of this category.")
    ss_default_reorder_max_qty = fields.Float(
        string='Default Maximum Quantity', digits='Product Unit of Measure',
        help="Proposed as maximum quantity of the new reorder rules created on "
             "products of this category.")

    @api.constrains('ss_default_reorder_point', 'ss_default_reorder_max_qty')
    def _check_ss_default_thresholds(self):
        for category in self:
            if (category.ss_default_reorder_max_qty
                    and category.ss_default_reorder_max_qty < category.ss_default_reorder_point):
                raise ValidationError(_(
                    "The default maximum quantity of \"%s\" must be greater than "
                    "or equal to its default reorder point."
                ) % category.display_name)
