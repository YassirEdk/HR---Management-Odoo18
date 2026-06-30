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
            'type':           'ir.actions.act_window',
            'name':           f'Attendances — {date_str}',
            'res_model':      'hr.attendance',
            'view_mode':      'list,form',
            'search_view_id': [self.env.ref('hr_attendance.hr_attendance_view_filter').id, 'search'],
            'domain': [
                ('check_in', '>=', date_str + ' 00:00:00'),
                ('check_in', '<=', date_str + ' 23:59:59'),
            ],
            'context': {
                'create':                          False,
                'search_default_group_dept_shift': 1,
                'search_default_group_status':     2,
            },
            'target': 'current',
        }