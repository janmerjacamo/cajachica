from odoo import models, fields, api, _
from odoo.exceptions import UserError, ValidationError

class CajaChica(models.Model):
    _name = 'caja.chica'
    _description = 'Caja Chica'

    name = fields.Char(string='Referencia', required=True, copy=False, readonly=True, default='Nuevo')
    date = fields.Date(string='Fecha', default=fields.Date.context_today, required=True)
    supervisor_id = fields.Many2one('res.users', string='Supervisor')
    concept = fields.Selection([
        ('operaciones', 'Operaciones'),
        ('administracion', 'Administración'),
        ('depreciacion', 'Depreciación'),
        ('reintegro_ventas', 'Reintegro de Ventas'),
        ('reintegro_gerencia', 'Reintegro de Gerencia')
    ], string='Concepto', default='operaciones')
    line_ids = fields.One2many('caja.chica.line', 'caja_id', string='Documentos', copy=True)
    account_expense_id = fields.Many2one('account.account', string='Cuenta de gasto', domain="[('deprecated','=',False)]")
    account_iva_id = fields.Many2one('account.account', string='Cuenta IVA (Crédito Fiscal)', domain="[('deprecated','=',False)]")
    account_idp_id = fields.Many2one('account.account', string='Cuenta IDP (opcional)', domain="[('deprecated','=',False)]")
    account_cash_id = fields.Many2one('account.account', string='Cuenta caja/provisión', domain="[('deprecated','=',False)]")
    journal_id = fields.Many2one('account.journal', string='Diario (opcional)', domain="[('company_id','=',company_id)]")
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)
    total_amount = fields.Monetary(string='Total', compute='_compute_totals', store=True, currency_field='currency_id')
    total_iva = fields.Monetary(string='Total IVA', compute='_compute_totals', store=True, currency_field='currency_id')
    total_idp = fields.Monetary(string='Total IDP', compute='_compute_totals', store=True, currency_field='currency_id')
    move_id = fields.Many2one('account.move', string='Asiento contable generado', readonly=True)
    state = fields.Selection([('draft','Borrador'), ('confirmed','Confirmado'), ('liquidated','Liquidado')], default='draft', string='Estado')

    @api.model_create_multi
    def create(self, vals_list):
        records = super(CajaChica, self).create(vals_list)
        seq = self.env['ir.sequence'].search([('code','=','xim.caja.chica')], limit=1)
        for rec in records:
            if rec.name in (False, '', 'Nuevo'):
                rec.name = seq.next_by_id() if seq else self.env['ir.sequence'].sudo().next_by_code('xim.caja.chica') or 'CC/00000'
        return records

    @api.depends('line_ids.total_line')
    def _compute_totals(self):
        for rec in self:
            rec.total_amount = sum(rec.line_ids.mapped('amount')) or 0.0
            rec.total_iva = sum(rec.line_ids.mapped('iva')) or 0.0
            rec.total_idp = sum(rec.line_ids.mapped('idp')) or 0.0

    def action_confirm(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('Solo se puede confirmar desde estado Borrador.'))
            rec.state = 'confirmed'

    def action_liquidar(self, post_move=False):
        for rec in self:
            if rec.state != 'confirmed':
                raise UserError(_('Solo se puede liquidar desde estado Confirmado.'))
            if not rec.line_ids:
                raise ValidationError(_('No hay líneas para liquidar.'))
            # Require accounts
            if not (rec.account_expense_id and rec.account_iva_id and rec.account_cash_id):
                raise ValidationError(_('Seleccione las cuentas: gasto, IVA y caja/provisión.'))

            # Compute totals
            total_gasto = sum(rec.line_ids.mapped('amount')) or 0.0
            total_iva = sum(rec.line_ids.mapped('iva')) or 0.0
            total_idp = sum(rec.line_ids.mapped('idp')) or 0.0
            total_debit = total_gasto + total_iva + total_idp
            total_credit = total_debit

            # Choose journal
            journal = rec.journal_id or self.env['account.journal'].search([('type','in',('cash','bank','general')),('company_id','=',rec.company_id.id)], limit=1)
            if not journal:
                raise UserError(_('No se encontró un diario válido. Configure uno o seleccione un diario.'))

            lines = []
            # Gasto (debit) - we split expense vs taxes for clarity
            lines.append((0,0,{
                'name': _('Gasto Caja Chica %s') % (rec.name),
                'account_id': rec.account_expense_id.id,
                'debit': total_gasto,
                'credit': 0.0,
                'company_id': rec.company_id.id,
            }))
            # IVA (debit)
            if total_iva > 0.0:
                lines.append((0,0,{
                    'name': _('IVA Crédito Fiscal %s') % (rec.name),
                    'account_id': rec.account_iva_id.id,
                    'debit': total_iva,
                    'credit': 0.0,
                    'company_id': rec.company_id.id,
                }))
            # IDP (debit) if configured and present
            if total_idp > 0.0:
                acc_idp = rec.account_idp_id.id if rec.account_idp_id else rec.account_iva_id.id
                lines.append((0,0,{
                    'name': _('IDP %s') % (rec.name),
                    'account_id': acc_idp,
                    'debit': total_idp,
                    'credit': 0.0,
                    'company_id': rec.company_id.id,
                }))
            # Provision / Cash (credit)
            lines.append((0,0,{
                'name': _('Provision/Caja %s') % (rec.name),
                'account_id': rec.account_cash_id.id,
                'debit': 0.0,
                'credit': total_credit,
                'company_id': rec.company_id.id,
            }))

            move_vals = {
                'move_type': 'entry',
                'journal_id': journal.id,
                'date': rec.date,
                'ref': _('Liquidación %s') % (rec.name),
                'company_id': rec.company_id.id,
                'line_ids': lines,
            }
            Move = self.env['account.move'].with_context(check_move_validity=False)
            move = Move.create(move_vals)
            # Optionally post immediately if configured or requested
            if post_move:
                try:
                    move.action_post()
                except Exception:
                    # keep it in draft if posting fails
                    pass
            rec.move_id = move.id
            rec.state = 'liquidated'

    def action_print(self):
        # Placeholder for report action - implement a QWeb report and return its action here
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Imprimir Provisión'),
                'message': _('Función de impresión no implementada en el paquete.'),
                'sticky': False,
            }
        }


class CajaChicaLine(models.Model):
    _name = 'caja.chica.line'
    _description = 'Líneas de Caja Chica'

    caja_id = fields.Many2one('caja.chica', string='Caja Chica', required=True, ondelete='cascade')
    date = fields.Date(string='Fecha')
    doc_type = fields.Selection([('factura','Factura'),('nota_credito','Nota de crédito'),('recibo','Recibo')], string='Tipo')
    series = fields.Char(string='Serie')
    number = fields.Char(string='Número')
    concept = fields.Selection([('bien','Bien'),('servicio','Servicio'),('combustible','Combustible')], string='Concepto')
    amount = fields.Monetary(string='Monto', currency_field='currency_id')
    iva = fields.Monetary(string='IVA', compute='_compute_impuestos', store=True, currency_field='currency_id')
    idp = fields.Monetary(string='IDP', compute='_compute_impuestos', store=True, currency_field='currency_id')
    total_line = fields.Monetary(string='Total línea', compute='_compute_impuestos', store=True, currency_field='currency_id')
    company_id = fields.Many2one('res.company', string='Compañía', default=lambda self: self.env.company)
    currency_id = fields.Many2one('res.currency', related='company_id.currency_id', readonly=True)

    @api.depends('amount','concept')
    def _compute_impuestos(self):
        for rec in self:
            if not rec.amount:
                rec.iva = 0.0
                rec.idp = 0.0
                rec.total_line = 0.0
                continue
            iva_rate = 0.12
            idp_rate = 0.05 if rec.concept == 'combustible' else 0.0
            rec.iva = rec.amount * iva_rate
            rec.idp = rec.amount * idp_rate
            rec.total_line = rec.amount + rec.iva + rec.idp
