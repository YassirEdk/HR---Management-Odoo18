# -*- coding: utf-8 -*-
from odoo import models, fields, api, _
from odoo.exceptions import UserError


class HrLeaveHolidayWarningWizard(models.TransientModel):
    _name        = 'hr.leave.holiday.warning.wizard'
    _description = 'Avertissement Jours Fériés'

    leave_id      = fields.Many2one('hr.leave', string="Congé", required=True)
    warning_message = fields.Text(string="Avertissement", readonly=True)

    def action_confirm_anyway(self):
        """Bypass the warning and confirm the leave."""
        self.ensure_one()
        self.leave_id.sudo().write({'x_holiday_warning_acknowledged': True})
        self.leave_id.action_confirm_employee()
        return {'type': 'ir.actions.act_window_close'}

    def action_cancel(self):
        """Cancel — reset the flag and close."""
        self.ensure_one()
        self.leave_id.sudo().write({'x_holiday_warning_acknowledged': False})
        return {'type': 'ir.actions.act_window_close'}