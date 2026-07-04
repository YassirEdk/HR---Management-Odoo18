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

            # A record can now carry several buckets at once (e.g. on time in the
            # morning + absent in the afternoon). To keep the four counters
            # mutually exclusive — as the old single-valued checkin_status was —
            # resolve each record to a single "primary" bucket by severity:
            # absence > missing checkout > late > on time (same order the old
            # _compute_checkin_status applied).
            count_priority = ('absence', 'missing_checkout', 'late', 'on_time')

            def primary_bucket(att):
                codes = att.status_bucket_ids.mapped('code')
                return next((c for c in count_priority if c in codes), None)

            def count(recs, dept=None, status=None):
                r = recs
                if dept:
                    r = r.filtered(lambda x: x.department_type == dept)
                if status:
                    r = r.filtered(lambda x: primary_bucket(x) == status)
                return len(r)

            rec.total_present = count(records, status='on_time')
            rec.total_late    = count(records, status='late')
            rec.total_absent  = count(records, status='absence')
            rec.total_autres  = count(records, status='missing_checkout')

            rec.siege_present = count(records, 'siege', 'on_time')
            rec.siege_late    = count(records, 'siege', 'late')
            rec.siege_absent  = count(records, 'siege', 'absence')
            rec.siege_autres  = count(records, 'siege', 'missing_checkout')

            rec.agence_present = count(records, 'agence', 'on_time')
            rec.agence_late    = count(records, 'agence', 'late')
            rec.agence_absent  = count(records, 'agence', 'absence')
            rec.agence_autres  = count(records, 'agence', 'missing_checkout')

            rec.warehouse_present = count(records, 'warehouse', 'on_time')
            rec.warehouse_late    = count(records, 'warehouse', 'late')
            rec.warehouse_absent  = count(records, 'warehouse', 'absence')
            rec.warehouse_autres  = count(records, 'warehouse', 'missing_checkout')

            rec.aeroport_present = count(records, 'aeroport', 'on_time')
            rec.aeroport_late    = count(records, 'aeroport', 'late')
            rec.aeroport_absent  = count(records, 'aeroport', 'absence')
            rec.aeroport_autres  = count(records, 'aeroport', 'missing_checkout')

    def _attendance_action(self, date_str, extra_domain=None, group_by_shift=False):
        domain = [
            ('check_in', '>=', date_str + ' 00:00:00'),
            ('check_in', '<=', date_str + ' 23:59:59'),
        ]
        if extra_domain:
            domain += extra_domain
        context = {
            'create':                       False,
            'search_default_group_status':  2,
        }
        # "Voir toutes les presences" mirrors the Aujourd'hui / date-wizard
        # grouping (Department / Shift); drilldown buttons keep dept type.
        if group_by_shift:
            context['search_default_group_dept_shift'] = 1
        else:
            context['search_default_group_dept_type'] = 1
        return {
            'type':           'ir.actions.act_window',
            'name':           f'Attendances — {date_str}',
            'res_model':      'hr.attendance',
            'view_mode':      'list,form',
            'views':          [[False, 'list'], [False, 'form']],
            'search_view_id': [self.env.ref('hr_attendance.hr_attendance_view_filter').id, 'search'],
            'domain':         domain,
            'context':        context,
            'target':         'current',
        }

    def action_pick_date(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       group_by_shift=True)

    def action_view_present(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('status_bucket_ids.code', '=', 'on_time')])

    def action_view_late(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('status_bucket_ids.code', '=', 'late')])

    def action_view_absent(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('status_bucket_ids.code', '=', 'absence')])

    def action_view_autres(self):
        self.ensure_one()
        if not self.date:
            return
        return self._attendance_action(self.date.strftime('%Y-%m-%d'),
                                       [('status_bucket_ids.code', '=', 'missing_checkout')])

    # ── Per-dept actions ──────────────────────────────────────
    def _dept_action(self, dept, status=None):
        self.ensure_one()
        if not self.date:
            return
        domain = [('department_type', '=', dept)]
        if status:
            domain += [('status_bucket_ids.code', '=', status)]
        return self._attendance_action(self.date.strftime('%Y-%m-%d'), domain)

    def action_siege_present(self):   return self._dept_action('siege', 'on_time')
    def action_siege_late(self):      return self._dept_action('siege', 'late')
    def action_siege_absent(self):    return self._dept_action('siege', 'absence')
    def action_siege_autres(self):    return self._dept_action('siege', 'missing_checkout')

    def action_agence_present(self):  return self._dept_action('agence', 'on_time')
    def action_agence_late(self):     return self._dept_action('agence', 'late')
    def action_agence_absent(self):   return self._dept_action('agence', 'absence')
    def action_agence_autres(self):   return self._dept_action('agence', 'missing_checkout')

    def action_warehouse_present(self): return self._dept_action('warehouse', 'on_time')
    def action_warehouse_late(self):    return self._dept_action('warehouse', 'late')
    def action_warehouse_absent(self):  return self._dept_action('warehouse', 'absence')
    def action_warehouse_autres(self):  return self._dept_action('warehouse', 'missing_checkout')

    def action_aeroport_present(self):  return self._dept_action('aeroport', 'on_time')
    def action_aeroport_late(self):     return self._dept_action('aeroport', 'late')
    def action_aeroport_absent(self):   return self._dept_action('aeroport', 'absence')
    def action_aeroport_autres(self):   return self._dept_action('aeroport', 'missing_checkout')