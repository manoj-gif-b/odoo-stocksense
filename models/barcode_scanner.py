# -*- coding: utf-8 -*-
"""StockSense - barcode scanning.

A lightweight, JavaScript-free scanning wizard (Odoo 16/17 compatible):

* the barcode input is the first field of the form, so a USB/Bluetooth scanner
  - which types the code and sends ``Enter`` - drives the whole flow;
* ``action_scan`` either redirects to the form of the scanned product or adds
  the scanned product as a line of a receipt / delivery, then re-opens the
  scanner so the operator can keep scanning;
* the code is matched on the product barcode first, then on the internal
  reference, then on the StockSense SKU.

No JavaScript asset is required, which keeps the feature strictly compatible
with both Odoo 16 and 17.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

#: Scanner target -> ``stock.picking.type.code``.
_TARGET_PICKING_TYPE_CODE = {
    'receipt': 'incoming',
    'delivery': 'outgoing',
}


class StockSenseBarcodeScanner(models.TransientModel):
    """Scan a barcode and dispatch it to a product or an operation."""

    _name = 'stocksense.barcode.scanner'
    _description = 'StockSense Barcode Scanner'

    barcode = fields.Char(
        string='Barcode', required=True,
        help="Scan (or type) the barcode, the internal reference or the "
             "StockSense SKU of a product, then press Enter.")
    target = fields.Selection(
        [('product', 'Product'),
         ('receipt', 'Receipt'),
         ('delivery', 'Delivery')],
        string='Scan Into', default='product', required=True,
        help="Product: open the product form. Receipt / Delivery: add the "
             "scanned product as a line of the selected operation.")
    picking_id = fields.Many2one(
        'stock.picking', string='Receipt / Delivery',
        domain="[('state', 'not in', ['done', 'cancel']),"
               " ('picking_type_code', 'in', ['incoming', 'outgoing'])]",
        help="Operation receiving the scanned products. Leave empty to let "
             "StockSense create a new draft operation on the default warehouse.")
    quantity = fields.Float(
        string='Quantity', default=1.0, required=True,
        digits='Product Unit of Measure',
        help="Quantity added to the operation for every scan.")
    product_id = fields.Many2one(
        'product.product', string='Scanned Product', readonly=True)
    scanned_count = fields.Integer(string='Scanned Lines', readonly=True)
    last_message = fields.Char(string='Last Scan', readonly=True)

    @api.onchange('barcode')
    def _onchange_barcode(self):
        """Resolve the scanned code immediately, to give instant feedback."""
        self.product_id = self._ss_find_product()

    def _ss_find_product(self):
        """Product matching the scanned code (barcode, reference or SKU)."""
        self.ensure_one()
        code = (self.barcode or '').strip()
        product_model = self.env['product.product']
        if not code:
            return product_model
        for domain in (
                [('barcode', '=', code)],
                [('default_code', '=', code)],
                [('ss_sku', '=', code)],
        ):
            product = product_model.search(domain, limit=1)
            if product:
                return product
        return product_model

    def _ss_product_action(self, product):
        """Redirect the operator to the form of the scanned product."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': product.display_name,
            'res_model': 'product.product',
            'res_id': product.id,
            'view_mode': 'form',
            'views': [(False, 'form')],
            'target': 'current',
        }

    def _ss_create_picking(self):
        """Create the draft operation of the default warehouse for the target."""
        self.ensure_one()
        picking_code = _TARGET_PICKING_TYPE_CODE[self.target]
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        picking_type = (warehouse.in_type_id if picking_code == 'incoming'
                        else warehouse.out_type_id)
        if not picking_type:
            raise UserError(_(
                "No %(operation)s operation type is configured on the "
                "warehouse.") % {
                    'operation': dict(self._fields['target'].selection)[self.target],
                })
        values = {
            'picking_type_id': picking_type.id,
            'origin': _('StockSense barcode scanner'),
        }
        if picking_type.default_location_src_id:
            values['location_id'] = picking_type.default_location_src_id.id
        if picking_type.default_location_dest_id:
            values['location_dest_id'] = picking_type.default_location_dest_id.id
        return self.env['stock.picking'].create(values)

    def _ss_add_line(self, picking, product):
        """Add (or merge) the scanned product as a line of ``picking``."""
        self.ensure_one()
        move = picking.move_ids.filtered(
            lambda line: line.product_id == product
            and line.state not in ('done', 'cancel'))[:1]
        if move:
            move.product_uom_qty += self.quantity
        else:
            move = self.env['stock.move'].create({
                'name': product.display_name,
                'product_id': product.id,
                'product_uom_qty': self.quantity,
                'product_uom': product.uom_id.id,
                'picking_id': picking.id,
                'location_id': picking.location_id.id,
                'location_dest_id': picking.location_dest_id.id,
                'company_id': picking.company_id.id,
            })
            if picking.state != 'draft':
                move._action_confirm()
        picking.message_post(body=_(
            "%(quantity)s x %(product)s added by the StockSense barcode "
            "scanner.") % {
                'quantity': self.quantity,
                'product': product.display_name,
            })
        return move

    def _ss_scanner_action(self, picking=None, product=None, message=None):
        """Re-open the scanner, keeping the operator selections."""
        self.ensure_one()
        if message is None:
            message = _('%(product)s -> %(picking)s') % {
                'product': product.display_name,
                'picking': picking.display_name,
            }
        context = {
            'default_target': self.target,
            'default_quantity': self.quantity,
            'default_scanned_count': self.scanned_count + (1 if product else 0),
            'default_last_message': message,
        }
        if picking:
            context['default_picking_id'] = picking.id
        return {
            'type': 'ir.actions.act_window',
            'name': _('Barcode Scanner'),
            'res_model': 'stocksense.barcode.scanner',
            'view_mode': 'form',
            'target': 'current',
            'context': context,
        }

    def action_scan(self):
        """Main button of the scanner: dispatch the scanned code."""
        self.ensure_one()
        product = self._ss_find_product()
        if not product:
            # Do not block the operator with a modal: the scanner stays open and
            # reports the unknown code right next to the input.
            return self._ss_scanner_action(
                picking=self.picking_id,
                message=_('No product found for the code "%s".')
                        % (self.barcode or ''))
        if self.target == 'product':
            return self._ss_product_action(product)
        picking = self.picking_id or self._ss_create_picking()
        expected_code = _TARGET_PICKING_TYPE_CODE[self.target]
        if picking.picking_type_code != expected_code:
            raise UserError(_(
                "\"%(picking)s\" is not a %(operation)s.") % {
                    'picking': picking.display_name,
                    'operation': dict(self._fields['target'].selection)[self.target],
                })
        self._ss_add_line(picking, product)
        return self._ss_scanner_action(picking=picking, product=product)
