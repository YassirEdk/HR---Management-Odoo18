from odoo import models, fields, api


class AttendanceOverview(models.TransientModel):
    _name        = 'attendance.overview.wizard'
    _description = 'Attendance Overview'

    date = fields.Date(string='Date')

    # ── Global totals ─────────────────────────────────────────
    total_present = fields.Integer(string="A l'heure",  compute='_compute_summary')
    total_late    = fields.Integer(string='En retard',  compute='_compute_summary')
    total_absent  = fields.Integer(string='Absent',     compute='_compute_summary')
    total_autres  = fields.Integer(string='Missing Checkout',     compute='_compute_summary')
    total_timeoff = fields.Integer(string='Time Off',   compute='_compute_summary')

    # ── Siege ─────────────────────────────────────────────────
    siege_present = fields.Integer(string="Siege - A l'heure", compute='_compute_summary')
    siege_late    = fields.Integer(string='Siege - En retard',  compute='_compute_summary')
    siege_absent  = fields.Integer(string='Siege - Absent',     compute='_compute_summary')
    siege_autres  = fields.Integer(string='Siege - Autres',     compute='_compute_summary')

    # ── Agence ────────────────────────────────────────────────
    agence_present = fields.Integer(string="Agence - A l'heure", compute='_compute_summary')
    agence_late    = fields.Integer(string='Agence - En retard',  compute='_compute_summary')
    agence_absent  = fields.Integer(string='Agence - Absent',     compute='_compute_summary')
    agence_autres  = fields.Integer(string='Agence - Autres',     compute='_compute_summary')

    # ── Warehouse ─────────────────────────────────────────────
    warehouse_present = fields.Integer(string="Warehouse - A l'heure", compute='_compute_summary')
    warehouse_late    = fields.Integer(string='Warehouse - En retard',  compute='_compute_summary')
    warehouse_absent  = fields.Integer(string='Warehouse - Absent',     compute='_compute_summary')
    warehouse_autres  = fields.Integer(string='Warehouse - Autres',     compute='_compute_summary')

    # ── Aéroport ──────────────────────────────────────────────
    aeroport_present = fields.Integer(string="Aéroport - A l'heure", compute='_compute_summary')
    aeroport_late    = fields.Integer(string='Aéroport - En retard',  compute='_compute_summary')
    aeroport_absent  = fields.Integer(string='Aéroport - Absent',     compute='_compute_summary')
    aeroport_autres  = fields.Integer(string='Aéroport - Autres',     compute='_compute_summary')

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        if 'date' in fields_list:
            res['date'] = fields.Date.context_today(self)
        return res

    @api.depends('date')
    def _compute_summary(self):
        for rec in self:
            if not rec.date:
                for f in ['total_present','total_late','total_absent','total_autres',
                          'siege_present','siege_late','siege_absent','siege_autres',
                          'agence_present','agence_late','agence_absent','agence_autres',
                          'warehouse_present','warehouse_late','warehouse_absent','warehouse_autres',
                          'aeroport_present','aeroport_late','aeroport_absent','aeroport_autres']:
                    setattr(rec, f, 0)
                continue

            date_str = rec.date.strftime('%Y-%m-%d')
            domain   = [
                ('check_in', '>=', date_str + ' 00:00:00'),
                ('check_in', '<=', date_str + ' 23:59:59'),
            ]
            records = rec.env['hr.attendance'].search(domain)

            def count(recs, dept=None, status=None):
                r = recs
                if dept:
                    r = r.filtered(lambda x: x.department_type == dept)
                if status:
                    r = r.filtered(lambda x: x.checkin_status == status)
                return len(r)

            rec.total_present = count(records, status='on_time')
            rec.total_late    = count(records, status='late')
            rec.total_absent  = count(records, status='absent')
            rec.total_autres  = count(records, status='autres')

            rec.siege_present = count(records, 'siege', 'on_time')
            rec.siege_late    = count(records, 'siege', 'late')
            rec.siege_absent  = count(records, 'siege', 'absent')
            rec.siege_autres  = count(records, 'siege', 'autres')

            rec.agence_present = count(records, 'agence', 'on_time')
            rec.agence_late    = count(records, 'agence', 'late')
            rec.agence_absent  = count(records, 'agence', 'absent')
            rec.agence_autres  = count(records, 'agence', 'autres')

            rec.warehouse_present = count(records, 'warehouse', 'on_time')
            rec.warehouse_late    = count(records, 'warehouse', 'late')
            rec.warehouse_absent  = count(records, 'warehouse', 'absent')
            rec.warehouse_autres  = count(records, 'warehouse', 'autres')

            rec.aeroport_present = count(records, 'aeroport', 'on_time')
            rec.aeroport_late    = count(records, 'aeroport', 'late')
            rec.aeroport_absent  = count(records, 'aeroport', 'absent')
            rec.aeroport_autres  = count(records, 'aeroport', 'autres')

    def _attendance_action(self, date_str, extra_domain=None):
        domain = [
            ('check_in', '>=', date_str + ' 00:00:00'),
            ('check_in', '<=', date_str + ' 23:59:59'),
        ]
        if extra_domain:
            domain += extra_domain
        return {
            'type':      'ir.actions.act_window',
            'name':      f'Attendances — {date_str}',
            'res_model': 'hr.attendance',
            'view_mode': 'list,form',
            'views':     [[False, 'list'], [False, 'form']],
            'domain':    domain,
            'context': {
                'create':                          False,
                'search_default_group_dept_type':  1,
                'search_default_group_status':     2,
            },
            'target': 'current',
        }

    def action_pick_date(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'))

    def action_view_present(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('checkin_status', '=', 'on_time')])

    def action_view_late(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('checkin_status', '=', 'late')])

    def action_view_absent(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('checkin_status', '=', 'absent')])

    def action_view_autres(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('checkin_status', '=', 'autres')])

    # ── Per-dept actions ──────────────────────────────────────
    def _dept_action(self, dept, status=None):
        self.ensure_one()
        if not self.date:
            return
        domain = [('department_type', '=', dept)]
        if status:
            domain += [('checkin_status', '=', status)]
        return self._attendance_action(self.date.strftime('%Y-%m-%d'), domain)

    def action_siege_present(self):   return self._dept_action('siege', 'on_time')
    def action_siege_late(self):      return self._dept_action('siege', 'late')
    def action_siege_absent(self):    return self._dept_action('siege', 'absent')
    def action_siege_autres(self):    return self._dept_action('siege', 'autres')

    def action_agence_present(self):  return self._dept_action('agence', 'on_time')
    def action_agence_late(self):     return self._dept_action('agence', 'late')
    def action_agence_absent(self):   return self._dept_action('agence', 'absent')
    def action_agence_autres(self):   return self._dept_action('agence', 'autres')

    def action_warehouse_present(self): return self._dept_action('warehouse', 'on_time')
    def action_warehouse_late(self):    return self._dept_action('warehouse', 'late')
    def action_warehouse_absent(self):  return self._dept_action('warehouse', 'absent')
    def action_warehouse_autres(self):  return self._dept_action('warehouse', 'autres')

    def action_aeroport_present(self):  return self._dept_action('aeroport', 'on_time')
    def action_aeroport_late(self):     return self._dept_action('aeroport', 'late')
    def action_aeroport_absent(self):   return self._dept_action('aeroport', 'absent')
    def action_aeroport_autres(self):   return self._dept_action('aeroport', 'autres')