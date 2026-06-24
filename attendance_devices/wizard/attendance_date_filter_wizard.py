from odoo import models, fields, api


class AttendanceDateFilterWizard(models.TransientModel):
    _name        = 'attendance.date.filter.wizard'
    _description = 'Filter Attendance by Date'

    date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.context_today,
    )

    def action_apply(self):
        self.ensure_one()
        date_str = self.date.strftime('%Y-%m-%d')
        return {
            'type':      'ir.actions.act_window',
            'name':      f'Attendances — {date_str}',
            'res_model': 'hr.attendance',
            'view_mode': 'list,form',
            'domain': [
                ('check_in', '>=', date_str + ' 00:00:00'),
                ('check_in', '<=', date_str + ' 23:59:59'),
            ],
            'context': {
                'create':                       False,
                'search_default_group_status':  1,
            },
            'target': 'current',
        }