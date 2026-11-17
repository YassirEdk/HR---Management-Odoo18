from odoo import fields, models


class AttendanceDevicePunch(models.Model):
    """Raw punch log, one row per device punch (stored in UTC).

    The sync used to rebuild attendance records straight from the device read,
    picking "new" punches with ``timestamp > last_sync``. A device clock lagging
    by a minute, or a check-in on one device and a checkout on another, was
    enough to lose a punch or flip the IN/OUT parity — the record then stayed
    with a check-in only.

    Punches are now persisted idempotently (UNIQUE device/badge/timestamp, the
    INSERT returns only the rows that are really new), and every attendance day
    is rebuilt from ALL stored punches of the employee, whatever the device.
    """
    _name        = 'attendance.device.punch'
    _description = 'Pointage brut'
    _order       = 'timestamp desc, id desc'
    _rec_name    = 'badge_id'

    device_id   = fields.Many2one('attendance.device', string='Pointeuse',
                                  required=True, ondelete='cascade', index=True)
    badge_id    = fields.Char(string='Badge', required=True, index=True)
    employee_id = fields.Many2one('hr.employee', string='Employé',
                                  ondelete='set null', index=True)
    timestamp   = fields.Datetime(string='Heure du pointage', required=True, index=True)

    _sql_constraints = [
        ('unique_punch', 'UNIQUE(device_id, badge_id, timestamp)',
         'Ce pointage existe déjà.'),
    ]

    def init(self):
        # Rebuilds query punches by employee + time range.
        self.env.cr.execute(
            "CREATE INDEX IF NOT EXISTS attendance_device_punch_emp_ts_idx "
            "ON attendance_device_punch (employee_id, timestamp)"
        )
