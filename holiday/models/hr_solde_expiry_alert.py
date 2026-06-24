# -*- coding: utf-8 -*-
from dateutil.relativedelta import relativedelta

from odoo import models, fields, api, _
import logging

_logger = logging.getLogger(__name__)


class HrSoldeExpiryAlert(models.Model):
    _name        = 'hr.solde.expiry.alert'
    _description = 'Alerte Expiration Solde Conge'
    _order       = 'days_remaining asc'

    employee_id    = fields.Many2one('hr.employee', string="Employe", required=True, ondelete='cascade')
    solde_conge    = fields.Float(string="Solde Conge (j)", readonly=True)
    hire_date      = fields.Date(string="Date d'embauche", readonly=True)
    expiry_date    = fields.Date(string="Date anniversaire 2 ans", readonly=True)
    days_remaining = fields.Integer(string="Jours avant anniversaire", readonly=True)
    dismissed      = fields.Boolean(string="Vu", default=False)
    mail_sent      = fields.Boolean(string="Email envoyé", default=False, readonly=True)

    def _get_next_alert_cycle(self, hire, today):
        cycle = 2
        while True:
            expiry      = hire + relativedelta(years=cycle)
            alert_start = expiry - relativedelta(days=90)

            if today >= expiry:
                cycle += 2
                continue

            if alert_start <= today < expiry:
                return expiry, (expiry - today).days

            if today < alert_start:
                return None

    @api.model
    def _cron_refresh_alerts(self):
        today = fields.Date.today()
        _logger.info("=== CRON EXPIRY ALERT STARTED — today: %s ===", today)

        # ── Remember which employees already had mail sent ────────────
        # Must be done BEFORE unlink so we don't lose the info
        already_mailed = set(
            self.search([('mail_sent', '=', True)]).mapped('employee_id').ids
        )
        _logger.info("Already mailed employee IDs: %s", already_mailed)

        # Clear old non-dismissed alerts
        self.search([('dismissed', '=', False)]).unlink()

        employees = self.env['hr.employee'].search([
            ('date_embauche', '!=', False),
            ('active',        '=',  True),
        ])
        _logger.info("Checking %s employees", len(employees))

        to_create = []
        for emp in employees:
            result = self._get_next_alert_cycle(emp.date_embauche, today)
            if not result:
                _logger.info("  %s — not in alert window", emp.name)
                continue

            expiry, days_left = result
            _logger.info("  %s — IN WINDOW! expiry=%s days_left=%s solde=%s",
                         emp.name, expiry, days_left, emp.solde_conge)

            if emp.solde_conge > 0:
                to_create.append({
                    'employee_id':    emp.id,
                    'solde_conge':    emp.solde_conge,
                    'hire_date':      emp.date_embauche,
                    'expiry_date':    expiry,
                    'days_remaining': days_left,
                    'mail_sent':      emp.id in already_mailed,
                })
            else:
                _logger.info("  %s — skipped (solde = 0)", emp.name)

        _logger.info("Alerts to create: %s", len(to_create))

        if to_create:
            new_alerts = self.create(to_create)
            to_send = new_alerts.filtered(lambda a: not a.mail_sent)
            _logger.info("Alerts to send email: %s", len(to_send))
            to_send._send_expiry_emails()

        _logger.info("=== CRON EXPIRY ALERT FINISHED ===")

    def _send_expiry_emails(self):
        template = self.env.ref(
            'holiday.mail_template_solde_expiry_alert',
            raise_if_not_found=False
        )
        if not template:
            _logger.error("❌ Template not found")
            return

        hr_users = self.env['res.users'].search([]).filtered(
            lambda u: u.has_group('holiday.group_holiday_hr')
        )
        hr_emails = ','.join(filter(None, hr_users.mapped('email')))
        if not hr_emails:
            _logger.error("❌ No HR emails found")
            return

        for alert in self:
            if alert.mail_sent:
                continue
            try:
                # ── Build subject directly in Python — no template rendering ──
                subject = f"⚠️ Alerte Solde Congé — {alert.employee_id.name}"

                template.send_mail(
                    alert.id,
                    force_send=True,
                    email_values={
                        'email_to':   hr_emails,
                        'email_from': self.env.company.email or hr_emails.split(',')[0],
                        'subject':    subject,
                    },
                )
                alert.write({'mail_sent': True})
                _logger.info("✅ Email sent for %s", alert.employee_id.name)
            except Exception as e:
                _logger.error("❌ Failed for %s: %s", alert.employee_id.name, e)

    @api.model
    def get_pending_alerts(self):
        if not self.env.user.has_group('holiday.group_holiday_hr'):
            return []
        alerts = self.search([('dismissed', '=', False)])
        result = []
        for a in alerts:
            result.append({
                'id':             a.id,
                'employee_name':  a.employee_id.name,
                'solde_conge':    a.solde_conge,
                'expiry_date':    a.expiry_date.strftime('%d/%m/%Y') if a.expiry_date else '',
                'days_remaining': a.days_remaining,
            })
        return result

    @api.model
    def dismiss_all_alerts(self):
        self.search([('dismissed', '=', False)]).write({'dismissed': True})
        return True