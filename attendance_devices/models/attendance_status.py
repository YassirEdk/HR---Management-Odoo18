from odoo import models, fields


class AttendanceStatusBucket(models.Model):
    """The 4 simplified statuses shown/filtered/grouped in the list view."""
    _name        = 'attendance.status.bucket'
    _description = 'Attendance Status Bucket (view)'
    _order       = 'sequence, id'

    name     = fields.Char(required=True, translate=True)
    code     = fields.Char(required=True, index=True)
    color    = fields.Integer(default=0)
    sequence = fields.Integer(default=10)

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Bucket code must be unique.'),
    ]


class AttendanceStatus(models.Model):
    """The 7 detailed statuses a single attendance record can carry."""
    _name        = 'attendance.status'
    _description = 'Attendance Status (detailed)'
    _order       = 'sequence, id'

    name      = fields.Char(required=True, translate=True)
    code      = fields.Char(required=True, index=True)
    color     = fields.Integer(default=0)
    sequence  = fields.Integer(default=10)
    bucket_id = fields.Many2one('attendance.status.bucket', string='View bucket', ondelete='set null')

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Status code must be unique.'),
    ]
