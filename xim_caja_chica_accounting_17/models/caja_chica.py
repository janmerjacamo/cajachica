from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

class CajaChica(models.Model):
    _name = "caja.chica"
    _description = "Caja Chica"

    name = fields.Char(string="Número", required=True, copy=False, default=lambda self: _('New'))
    date = fields.Date(string="Fecha", required=True)
    concept = fields.Selection([
        ('operaciones','Operaciones'),
        ('administracion','Administración'),
        ('depreciacion','Depreciación de motocicletas'),
        ('reintegro_ventas','Reintegro de ventas'),
        ('reintegro_gerencia','Reintegro de gerencia'),
    ], string="Concepto", default='operaciones')
    supervisor_id = fields.Many2one('res.users', string="Supervisor")
    line_ids = fields.One2many('caja.chica.line','caja_id', string="Documentos")
    total_amount = fields.Monetary(string="Total", currency_field='company_currency_id', store=True, compute='_compute_total')
    total_iva = fields.Monetary(string="Total IVA", currency_field='company_currency_id', store=True, compute='_compute_total')
    total_idp = fields.Monetary(string="Total IDP", currency_field='company_currency_id', store=True, compute='_compute_total')
    state = fields.Selection([('draft','Borrador'),('confirmed','Confirmado'),('liquidated','Liquidado')], default='draft', string="Estado")
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    # Cuentas seleccionables
    account_expense_id = fields.Many2one('account.account', string="Cuenta de gasto", domain="[('deprecated','=',False)]")
    account_iva_id = fields.Many2one('account.account', string="Cuenta IVA (Crédito Fiscal)", domain="[('deprecated','=',False)]")
    account_cash_id = fields.Many2one('account.account', string="Cuenta caja/provisión", domain="[('deprecated','=',False)]")
    journal_id = fields.Many2one('account.journal', string="Diario (opcional)")
    move_id = fields.Many2one('account.move', string="Asiento contable generado", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        seq = self.env['ir.sequence'].search([('name','=','Caja Chica')], limit=1)
        for rec in records:
            if rec.name in (False, 'New'):
                if seq:
                    rec.name = seq.next_by_id()
                else:
                    rec.name = self.env['ir.sequence'].sudo().next_by_code('caja.chica') or 'CC/0000'
        return records

    @api.depends('line_ids.total_line')
    def _compute_total(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('amount')) or 0.0
            rec.total_iva = sum(rec.line_ids.mapped('iva')) or 0.0
            rec.total_idp = sum(rec.line_ids.mapped('idp')) or 0.0

    def action_confirm(self):
        for rec in self:
            rec.state = 'confirmed'

    def action_liquidate(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError('No hay líneas en la caja chica para liquidar.')
            if not rec.account_expense_id or not rec.account_iva_id or not rec.account_cash_id:
                raise ValidationError('Debe seleccionar las cuentas: gasto, IVA y caja/provisión antes de liquidar.')
            if rec.move_id:
                raise ValidationError('Ya existe un asiento contable para esta caja chica.')

            # Totales
            total_gasto = sum(rec.line_ids.mapped('amount'))
            total_iva = sum(rec.line_ids.mapped('iva'))
            total_idp = sum(rec.line_ids.mapped('idp'))
            total_debito = total_gasto + total_iva + total_idp
            total_credito = total_debito

            # Journal: preferido en el registro, sino buscar diario tipo 'cash' o 'bank'
            journal = rec.journal_id
            if not journal:
                journal = self.env['account.journal'].search([('type','in',('cash','bank')), ('company_id','=',rec.company_id.id)], limit=1)
            if not journal:
                raise UserError('No se encontró un diario válido. Por favor, configure un diario o seleccione uno en la caja chica.')

            move_vals = {
                'journal_id': journal.id,
                'date': rec.date,
                'ref': _('Liquidación %s') % (rec.name),
                'company_id': rec.company_id.id,
                'state': 'draft',
                'line_ids': [],
            }

            # Línea gasto (debit)
            move_vals['line_ids'].append((0, 0, {
                'name': _('Gasto caja chica: %s') % (rec.name),
                'account_id': rec.account_expense_id.id,
                'debit': total_gasto,
                'credit': 0.0,
                'company_id': rec.company_id.id,
            }))
            # Línea IVA (debit)
            if total_iva and total_iva > 0.0:
                move_vals['line_ids'].append((0, 0, {
                    'name': _('IVA crédito fiscal: %s') % (rec.name),
                    'account_id': rec.account_iva_id.id,
                    'debit': total_iva,
                    'credit': 0.0,
                    'company_id': rec.company_id.id,
                }))
            # Línea IDP (debit) - if any, we can reuse same account_iva or separate but we'll add to expense for simplicity
            if total_idp and total_idp > 0.0:
                # add as separate line to expense account (or you may create a specific IDP account)
                move_vals['line_ids'].append((0, 0, {
                    'name': _('IDP (Impuesto derivado de petróleo): %s') % (rec.name),
                    'account_id': rec.account_iva_id.id,
                    'debit': total_idp,
                    'credit': 0.0,
                    'company_id': rec.company_id.id,
                }))

            # Línea caja/provisión (credit)
            move_vals['line_ids'].append((0, 0, {
                'name': _('Provision/Caja: %s') % (rec.name),
                'account_id': rec.account_cash_id.id,
                'debit': 0.0,
                'credit': total_credito,
                'company_id': rec.company_id.id,
            }))

            # Crear move
            move = self.env['account.move'].create(move_vals)
            rec.move_id = move
            rec.state = 'liquidated'

class CajaChicaLine(models.Model):
    _name = "caja.chica.line"
    _description = "Líneas de Caja Chica"

    caja_id = fields.Many2one('caja.chica', string="Caja Chica", required=True, ondelete='cascade')
    date = fields.Date(string="Fecha factura")
    doc_type = fields.Selection([('factura','Factura'),('nota_credito','Nota de crédito'),('recibo','Recibo')], string='Tipo de documento', default='factura')
    series = fields.Char(string="Serie")
    number = fields.Char(string="Número")
    concept = fields.Selection([('bien','Bien'),('servicio','Servicio'),('combustible','Combustible')], string='Concepto', default='bien')
    amount = fields.Monetary(string="Monto", currency_field='company_currency_id')
    iva = fields.Monetary(string="IVA", currency_field='company_currency_id', compute='_compute_impuestos', store=True)
    idp = fields.Monetary(string="IDP", currency_field='company_currency_id', compute='_compute_impuestos', store=True)
    total_line = fields.Monetary(string="Total línea", currency_field='company_currency_id', compute='_compute_impuestos', store=True)
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)

    @api.depends('amount','concept')
    def _compute_impuestos(self):
        for line in self:
            if not line.amount:
                line.iva = 0.0
                line.idp = 0.0
                line.total_line = 0.0
                continue
            iva_rate = 0.12
            idp_rate = 0.05 if line.concept == 'combustible' else 0.0
            line.iva = line.amount * iva_rate
            line.idp = line.amount * idp_rate
            line.total_line = line.amount + line.iva + line.idp
