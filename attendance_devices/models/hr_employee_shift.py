from odoo import models, fields, api


class HrEmployeeAttendanceExt(models.Model):
    _inherit = 'hr.employee'

    shift_config_id = fields.Many2one(
        comodel_name='attendance.shift.config',
        string='Applied Shift',
        ondelete='set null',
        help='Automatically assigned based on department type and shift.',
    )

    department_shift_count = fields.Integer(
        string='Available Shifts',
        compute='_compute_department_shift_count',
        help="How many shifts are configured for this employee's department "
             "type. The shift picker is shown only when there is more than one.",
    )

    @api.depends('department_type')
    def _compute_department_shift_count(self):
        ShiftConfig = self.env['attendance.shift.config']
        for emp in self:
            emp.department_shift_count = ShiftConfig.search_count(
                [('department_type', '=', emp.department_type)]
            ) if emp.department_type else 0

    @api.onchange('department_type', 'shift_type')
    def _onchange_shift_config(self):
        self.shift_config_id = self._get_matching_shift_config()

    def _get_matching_shift_config(self):
        ShiftConfig = self.env['attendance.shift.config']

        if not self.department_type:
            return False

        if self.department_type == 'warehouse':
            shift_name = self.shift_type or 'day'
            config = ShiftConfig.search([
                ('department_type', '=', 'warehouse'),
                ('shift_name',      '=', shift_name),
            ], limit=1)
            if not config:
                config = ShiftConfig.search([
                    ('department_type', '=', 'warehouse'),
                ], limit=1)
        else:
            config = ShiftConfig.search([
                ('department_type', '=', self.department_type),
            ], limit=1)

        return config or False

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for emp in records:
            if not emp.shift_config_id:
                config = emp._get_matching_shift_config()
                if config:
                    emp.write({'shift_config_id': config.id})
        return records

    def write(self, vals):
        result = super().write(vals)
        if 'department_type' in vals or 'shift_type' in vals:
            for emp in self:
                # Respect an explicit shift the user picked in this same save,
                # as long as it belongs to the (new) department type.
                if 'shift_config_id' in vals and emp.shift_config_id \
                        and emp.shift_config_id.department_type == emp.department_type:
                    continue
                config = emp._get_matching_shift_config()
                emp.write({'shift_config_id': config.id if config else False})
        return result