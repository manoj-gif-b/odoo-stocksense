# -*- coding: utf-8 -*-
# Part of StockSense. See LICENSE file for full copyright and licensing details.

from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.osv import expression
from odoo.tools.float_utils import float_compare, float_is_zero


def _quant_key(product_id, location_id, lot_id, package_id, owner_id):
    """Return the identity tuple of a ``stock.quant`` row.

    A quant is unique per (product, location, lot, package, owner). This tuple is
    what ties a counted line back to the row holding the on-hand quantity.
    """
    return (product_id, location_id, lot_id or False, package_id or False, owner_id or False)


class StockAdjustment(models.Model):
    _name = 'stock.adjustment'
    _description = 'Inventory Adjustment'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'
    _check_company_auto = True

    name = fields.Char(
        string='Reference', required=True, copy=False, readonly=True, index=True,
        default=lambda self: _('New'))
    date = fields.Datetime(
        string='Adjustment Date', required=True, copy=False, default=fields.Datetime.now,
        help="Date used for the inventory move and its valuation.")
    user_id = fields.Many2one(
        'res.users', string='Responsible', required=True, index=True,
        default=lambda self: self.env.user)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True, index=True,
        default=lambda self: self.env.company)
    location_id = fields.Many2one(
        'stock.location', string='Inventory Location', required=True,
        domain="[('usage', '=', 'internal')]", check_company=True,
        help="Location the products were counted in. Used as the default "
             "location of the lines.")
    adjustment_type = fields.Selection(
        [('count', 'Inventory Count'),
         ('increase', 'Increase Only'),
         ('decrease', 'Decrease Only')],
        string='Adjustment Type', required=True, default='count',
        help="An Inventory Count accepts both increases and decreases. "
             "Increase Only and Decrease Only reject the lines going the other way.")
    reason = fields.Char(
        string='Reason', tracking=True,
        help="Why the stock was adjusted, e.g. 'Cycle count W34', 'Damaged goods'.")
    state = fields.Selection(
        [('draft', 'Draft'), ('done', 'Applied'), ('cancel', 'Cancelled')],
        string='Status', required=True, default='draft', copy=False, index=True,
        tracking=True)
    line_ids = fields.One2many(
        'stock.adjustment.line', 'adjustment_id', string='Adjustment Lines', copy=True)
    note = fields.Text(string='Notes')
    validated_date = fields.Datetime(string='Applied On', readonly=True, copy=False)

    line_count = fields.Integer(string='Lines', compute='_compute_statistics')
    counted_qty = fields.Float(
        string='Counted Quantity', compute='_compute_statistics',
        digits='Product Unit of Measure')
    difference_qty = fields.Float(
        string='Difference', compute='_compute_statistics',
        digits='Product Unit of Measure')

    @api.depends('line_ids.counted_qty', 'line_ids.difference_qty')
    def _compute_statistics(self):
        for adjustment in self:
            adjustment.line_count = len(adjustment.line_ids)
            adjustment.counted_qty = sum(adjustment.line_ids.mapped('counted_qty'))
            adjustment.difference_qty = sum(adjustment.line_ids.mapped('difference_qty'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('stock.adjustment') or _('New')
        return super().create(vals_list)

    def unlink(self):
        if any(adjustment.state == 'done' for adjustment in self):
            raise UserError(_(
                "Applied inventory adjustments cannot be deleted. "
                "Create a new adjustment to correct the stock instead."))
        return super().unlink()

    def action_validate(self):
        """Apply every counted line to the stock through ``stock.quant``.

        Only the lines whose counted quantity differs from the on-hand quantity
        are pushed to ``stock.quant``, so no empty inventory move is created.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(_(
                "Only a draft inventory adjustment can be applied. '%s' is currently %s.",
                self.name, dict(self._fields['state'].selection).get(self.state)))
        if not self.line_ids:
            raise UserError(_("Add at least one product line before applying the adjustment."))

        quants = self.env['stock.quant']
        for line in self.line_ids:
            rounding = line.product_uom_id.rounding
            difference = line.difference_qty
            if float_is_zero(difference, precision_rounding=rounding):
                continue
            if self.adjustment_type == 'increase' \
                    and float_compare(difference, 0.0, precision_rounding=rounding) < 0:
                raise UserError(_(
                    "'%s' only accepts increases, but line '%s' removes %s.",
                    self.name, line.product_id.display_name, -difference))
            if self.adjustment_type == 'decrease' \
                    and float_compare(difference, 0.0, precision_rounding=rounding) > 0:
                raise UserError(_(
                    "'%s' only accepts decreases, but line '%s' adds %s.",
                    self.name, line.product_id.display_name, difference))
            quants |= line._get_or_create_quant()

        if not quants:
            raise UserError(_(
                "The counted quantities match the on-hand quantities, "
                "there is nothing to apply."))

        # ``action_apply_inventory`` is the very method the Inventory app's
        # "Apply" button calls: it creates the inventory moves and their
        # valuation. ``inventory_name`` labels them with our reference.
        quants.with_context(inventory_name=self.name).action_apply_inventory()

        self.write({
            'state': 'done',
            'validated_date': fields.Datetime.now(),
            'user_id': self.env.user.id,
        })
        self.message_post(body=_(
            "Inventory adjustment applied on %s product(s).", len(quants)))
        return True

    def action_cancel(self):
        """Cancel a draft adjustment.

        An applied one is terminal: its stock moves already happened, so
        cancelling it would leave the document out of sync with the stock (and
        would then make it deletable, losing the traceability of the move).
        """
        if any(adjustment.state != 'draft' for adjustment in self):
            raise UserError(_(
                "Only a draft inventory adjustment can be cancelled. Applied "
                "adjustments are final, create a new adjustment to correct the "
                "stock instead."))
        self.write({'state': 'cancel'})

    def action_draft(self):
        if any(adjustment.state != 'cancel' for adjustment in self):
            raise UserError(_(
                "Only a cancelled inventory adjustment can be reset to draft."))
        self.write({'state': 'draft'})


class StockAdjustmentLine(models.Model):
    _name = 'stock.adjustment.line'
    _description = 'Inventory Adjustment Line'
    _order = 'sequence, id'
    _check_company_auto = True

    adjustment_id = fields.Many2one(
        'stock.adjustment', string='Adjustment', required=True,
        ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        'res.company', related='adjustment_id.company_id', store=True, index=True)
    state = fields.Selection(related='adjustment_id.state')
    date = fields.Datetime(
        related='adjustment_id.date', string='Adjustment Date', readonly=True)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, index=True,
        domain="[('type', '=', 'product')]", check_company=True)
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit of Measure', related='product_id.uom_id', readonly=True)
    location_id = fields.Many2one(
        'stock.location', string='Location', required=True,
        domain="[('usage', '=', 'internal')]", check_company=True)
    lot_id = fields.Many2one(
        'stock.lot', string='Lot/Serial Number', check_company=True,
        domain="[('product_id', '=', product_id)]")
    package_id = fields.Many2one(
        'stock.quant.package', string='Package', check_company=True)
    # No check_company on the owner: partners are commonly shared across
    # companies, and a company-less partner must not block the adjustment.
    owner_id = fields.Many2one('res.partner', string='Owner')
    counted_qty = fields.Float(
        string='Counted Quantity', digits='Product Unit of Measure',
        help="Quantity physically counted in the location.")
    theoretical_qty = fields.Float(
        string='On Hand', compute='_compute_quantities',
        digits='Product Unit of Measure')
    difference_qty = fields.Float(
        string='Difference', compute='_compute_quantities',
        digits='Product Unit of Measure')

    @api.constrains('counted_qty', 'product_id')
    def _check_counted_qty(self):
        for line in self:
            if line.product_id and float_compare(
                    line.counted_qty, 0.0,
                    precision_rounding=line.product_uom_id.rounding) < 0:
                raise ValidationError(_(
                    "The counted quantity of '%s' cannot be negative.",
                    line.product_id.display_name))

    @api.model
    def default_get(self, fields_list):
        """Default the line location to the location of its adjustment."""
        values = super().default_get(fields_list)
        if 'location_id' in fields_list and not values.get('location_id'):
            adjustment = self.env['stock.adjustment'].browse(
                self.env.context.get('default_adjustment_id'))
            if adjustment.location_id:
                values['location_id'] = adjustment.location_id.id
        return values

    @api.depends('product_id', 'location_id', 'lot_id', 'package_id', 'owner_id',
                 'counted_qty')
    def _compute_quantities(self):
        if not self:
            return
        keys = {_quant_key(line.product_id.id, line.location_id.id,
                           line.lot_id.id, line.package_id.id, line.owner_id.id)
                for line in self}
        domain = expression.OR([
            [('product_id', '=', key[0]),
             ('location_id', '=', key[1]),
             ('lot_id', '=', key[2]),
             ('package_id', '=', key[3]),
             ('owner_id', '=', key[4])]
            for key in keys
        ])
        # One query for all the distinct quants of the recordset, instead of one
        # query per line.
        on_hand_by_key = defaultdict(float)
        for quant in self.env['stock.quant'].search(domain):
            on_hand_by_key[_quant_key(quant.product_id.id, quant.location_id.id,
                                      quant.lot_id.id, quant.package_id.id,
                                      quant.owner_id.id)] += quant.quantity
        for line in self:
            on_hand = on_hand_by_key[_quant_key(
                line.product_id.id, line.location_id.id,
                line.lot_id.id, line.package_id.id, line.owner_id.id)]
            line.theoretical_qty = on_hand
            line.difference_qty = line.counted_qty - on_hand

    def _get_quant_domain(self):
        """Locate the unique quant matching this line."""
        self.ensure_one()
        return [
            ('product_id', '=', self.product_id.id),
            ('location_id', '=', self.location_id.id),
            ('lot_id', '=', self.lot_id.id),
            ('package_id', '=', self.package_id.id),
            ('owner_id', '=', self.owner_id.id),
        ]

    def _get_or_create_quant(self):
        """Return the ``stock.quant`` of this line, set to the counted quantity.

        The quant is left flagged with ``inventory_quantity_set``: the core
        ``action_apply_inventory`` filters on that flag and would silently skip
        a quant without it.
        """
        self.ensure_one()
        product = self.product_id
        if product.type != 'product':
            raise UserError(_(
                "'%s' is not a storable product, its stock cannot be adjusted.",
                product.display_name))
        if product.tracking != 'none' and not self.lot_id:
            raise UserError(_(
                "'%s' is tracked, set the Lot/Serial Number before applying.",
                product.display_name))

        values = {
            'inventory_quantity': self.counted_qty,
            'inventory_quantity_set': True,
            'inventory_date': fields.Date.context_today(self),
            'user_id': self.env.user.id,
        }
        Quant = self.env['stock.quant']
        quant = Quant.search(self._get_quant_domain(), limit=1)
        if quant:
            quant.with_context(inventory_mode=True).write(values)
        else:
            quant = Quant.with_context(inventory_mode=True).create(dict(values, **{
                'product_id': product.id,
                'location_id': self.location_id.id,
                'lot_id': self.lot_id.id,
                'package_id': self.package_id.id,
                'owner_id': self.owner_id.id,
            }))
        return quant
