from odoo import models, fields, api
from odoo.exceptions import ValidationError


class AttendanceShiftConfig(models.Model):
    _name        = 'attendance.shift.config'
    _description = 'Attendance Shift Configuration'
    _order       = 'department_type, shift_name'
    _rec_name    = 'shift_name'

    department_type_id = fields.Many2one(
        'hr.department.type',
        string='Department',
        ondelete='cascade',
        index=True,
        help='Relational link used to manage shifts from the department form.',
    )
    department_type = fields.Selection(
        selection=lambda self: self.env['hr.department.type'].get_selection(),
        string='Department Type',
        help='Stored technical code used by the attendance engine. Kept in '
             'sync with Department. Auto-filled from Department when a shift is '
             'added from the department form.',
    )

    @api.constrains('department_type', 'department_type_id')
    def _check_department_set(self):
        for rec in self:
            if not rec.department_type and not rec.department_type_id:
                raise ValidationError(
                    "A shift must belong to a department type."
                )

    # ── Keep department_type (code) and department_type_id (m2o) in sync ───────
    @api.onchange('department_type_id')
    def _onchange_department_type_id(self):
        if self.department_type_id:
            self.department_type = self.department_type_id.code

    @api.model
    def _sync_dept_vals(self, vals):
        """Fill whichever of department_type / department_type_id is missing."""
        Types = self.env['hr.department.type']
        if vals.get('department_type_id') and not vals.get('department_type'):
            vals['department_type'] = Types.browse(vals['department_type_id']).code
        elif vals.get('department_type') and not vals.get('department_type_id'):
            t = Types.search([('code', '=', vals['department_type'])], limit=1)
            if t:
                vals['department_type_id'] = t.id
        return vals

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sync_dept_vals(vals)
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('department_type_id') or vals.get('department_type'):
            self._sync_dept_vals(vals)
        return super().write(vals)

    def init(self):
        # Backfill department_type_id on existing rows so they show up under
        # their department type's shift list.
        self.env.cr.execute(
            """
            UPDATE attendance_shift_config s
               SET department_type_id = t.id
              FROM hr_department_type t
             WHERE t.code = s.department_type
               AND s.department_type_id IS NULL
            """
        )

    shift_name = fields.Char(
        string='Shift',
        required=True,
        help='e.g. "Morning", "Night", "Standard"',
    )

    start_time = fields.Float(
        string='Start',
        required=True,
        default=8.5,
        aggregator=None,
    )
    end_time = fields.Float(
        string='End',
        required=True,
        default=17.0,
        aggregator=None,
    )
    break_start = fields.Float(
        string='Break Start',
        default=12.0,
        aggregator=None,
    )
    break_end = fields.Float(
        string='Break End',
        default=14.0,
        aggregator=None,
    )

    late_tolerance_minutes = fields.Integer(
        string='Late Tolerance (min)',
        default=30,
        aggregator=None,
        help='Minutes after shift start before employee is marked Late. e.g. 30 = late after work_start + 30min.',
    )
    max_extra_hours = fields.Float(
        string='Max Extra Hours',
        default=2.0,
        aggregator=None,
        help='Maximum hours after shift end before auto-closing open attendance. '
             'e.g. 2.0 = close if no checkout by shift_end + 2h.',
    )

    # ── Status timing thresholds (editable) ──────────────────────────────────
    absence_morning_delay = fields.Float(
        string='Absence Matinale After (h)',
        default=2.5,
        aggregator=None,
        help='Hours after shift start: a check-in later than this is Absence matinale. '
             'e.g. 2.5 = after start + 2h30.',
    )
    absence_afternoon_grace = fields.Float(
        string='Absence Après-midi Grace (h)',
        default=2.0,
        aggregator=None,
        help='If someone leaves and this many hours later the shift is still running, '
             'it is Absence après-midi. e.g. 2.0 = 2h grace.',
    )
    break_max_duration = fields.Float(
        string='Break Max Duration (h)',
        default=1.25,
        aggregator=None,
        help='Maximum allowed break length. If the break runs longer: the employee '
             'is marked Absence après-midi when he never punches back, or Retard de '
             'pause when he does. e.g. 1.25 = 1h15.',
    )

    # ── Saturday schedule (optional) ──────────────────────────────────────────
    works_saturday = fields.Boolean(
        string='Works Saturday',
        default=False,
        help='Tick if this shift works on Saturdays. When set, Saturday uses the '
             'start/end times below instead of the weekday ones.',
    )
    saturday_start_time = fields.Float(
        string='Saturday Start',
        default=8.5,
        aggregator=None,
    )
    saturday_end_time = fields.Float(
        string='Saturday End',
        default=13.0,
        aggregator=None,
    )

    def effective_times(self, day):
        """Return (start_time, end_time) for the given date, using the Saturday
        schedule when it is a Saturday and this shift works Saturdays."""
        self.ensure_one()
        if day and day.weekday() == 5 and self.works_saturday:
            return self.saturday_start_time, self.saturday_end_time
        return self.start_time, self.end_time

    # ── Clock display fields ─────────────────────────────────────────────────
    start_time_display  = fields.Char(string='Start (clock)',        compute='_compute_display_times', store=False)
    end_time_display    = fields.Char(string='End (clock)',          compute='_compute_display_times', store=False)
    break_start_display = fields.Char(string='Break Start (clock)',  compute='_compute_display_times', store=False)
    break_end_display   = fields.Char(string='Break End (clock)',    compute='_compute_display_times', store=False)

    break_duration = fields.Float(string='Break (h)', compute='_compute_break_duration', aggregator=None)
    worked_hours   = fields.Float(string='Net Hours', compute='_compute_break_duration', aggregator=None)

    @staticmethod
    def _float_to_hhmm(value):
        value = (value or 0.0) % 24
        total_minutes = int(round(value * 60))
        hours   = total_minutes // 60
        minutes = total_minutes % 60
        return f"{hours:02d}:{minutes:02d}"

    @staticmethod
    def _shift_duration(start, end):
        if end >= start:
            return end - start
        return (24.0 - start) + end

    @api.depends('start_time', 'end_time', 'break_start', 'break_end')
    def _compute_display_times(self):
        for rec in self:
            rec.start_time_display  = rec._float_to_hhmm(rec.start_time)
            rec.end_time_display    = rec._float_to_hhmm(rec.end_time)
            rec.break_start_display = rec._float_to_hhmm(rec.break_start)
            rec.break_end_display   = rec._float_to_hhmm(rec.break_end)

    @api.depends('start_time', 'end_time', 'break_start', 'break_end')
    def _compute_break_duration(self):
        for rec in self:
            brk  = max(0.0, rec.break_end - rec.break_start)
            span = rec._shift_duration(rec.start_time, rec.end_time)
            rec.break_duration = brk
            rec.worked_hours   = max(0.0, span - brk)

    @api.constrains('start_time', 'end_time', 'break_start', 'break_end')
    def _check_times(self):
        for rec in self:
            for fname, val in [
                ('start_time',  rec.start_time),
                ('end_time',    rec.end_time),
                ('break_start', rec.break_start),
                ('break_end',   rec.break_end),
            ]:
                if not (0.0 <= val < 24.0):
                    raise ValidationError(
                        f"'{fname}' must be between 00:00 and 23:59. "
                        f"Got: {rec._float_to_hhmm(val)}"
                    )

            duration = rec._shift_duration(rec.start_time, rec.end_time)
            if duration <= 0:
                raise ValidationError(
                    f"Shift duration must be greater than 0. "
                    f"Start: {rec._float_to_hhmm(rec.start_time)}, "
                    f"End: {rec._float_to_hhmm(rec.end_time)}"
                )
            if duration > 24:
                raise ValidationError(
                    f"Shift duration cannot exceed 24 hours. "
                    f"Got: {duration:.2f}h"
                )

    _sql_constraints = [
        (
            'unique_dept_shift',
            'UNIQUE(department_type, shift_name)',
            'A shift with this name already exists for that department type.',
        )
    ]

    @api.model
    def get_for_employee(self, employee):
        if not employee:
            return False
        dept   = employee.department_type
        shift  = employee.shift_type or 'day'
        config = self.search(
            [('department_type', '=', dept), ('shift_name', '=', shift)],
            limit=1,
        )
        if not config:
            config = self.search(
                [('department_type', '=', dept)],
                order='id asc',
                limit=1,
            )
        return config or False