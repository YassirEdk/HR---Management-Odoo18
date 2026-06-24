# -*- coding: utf-8 -*-
import logging
from datetime import date, timedelta
from odoo import models, fields, api, _
from odoo.exceptions import AccessError, ValidationError

_logger = logging.getLogger(__name__)


class HrEmployee(models.Model):
    _inherit = 'hr.employee'

    badge_id = fields.Char(string="Badge Pointeuse")

    date_embauche = fields.Date(
        string="Date d'embauche",
        default=fields.Date.today,
    )

    solde_conge = fields.Float(
        string="Solde congé",
        help="Nombre de jours de congé restants"
    )

    department_type = fields.Selection(
        selection=lambda self: self.env['hr.department.type'].get_selection(),
        string="Department Type",
        default='siege',
    )

    shift_type = fields.Selection(
        selection=[
            ('day',   'Day Shift'),
            ('night', 'Night Shift'),
        ],
        string="Shift",
    )

    type_employee = fields.Selection(
        selection=[
            ('ouvrier',  'Ouvrier'),
            ('employee', 'Employé'),
            ('cadre',    'Cadre'),
        ],
        string="Type d'employé",
    )

    # ── Badge unique per department type ──────────────────────────────────────
    @api.constrains('badge_id', 'department_type')
    def _check_badge_id_unique_per_department_type(self):
        for rec in self:
            if not rec.badge_id:
                continue
            duplicate = self.with_context(active_test=False).search([
                ('id', '!=', rec.id),
                ('badge_id', '=', rec.badge_id),
                ('department_type', '=', rec.department_type),
            ], limit=1)
            if duplicate:
                dept_label = self.env['hr.department.type'].get_label_map().get(
                    rec.department_type, rec.department_type
                )
                raise ValidationError(_(
                    "Le Badge Pointeuse « %(badge)s » est déjà utilisé par "
                    "« %(emp)s » dans le même type de département (%(dept)s).",
                    badge=rec.badge_id,
                    emp=duplicate.name,
                    dept=dept_label,
                ))

    # ── Not stored — recomputes fresh on every read ───────────────────────────
    periode_essai = fields.Selection(
        selection=[
            ('premiere', '1ère PE'),
            ('deuxieme', '2ème PE'),
            ('termine',  'PE Terminée'),
        ],
        string="Période d'essai",
        compute='_compute_periode_essai',
        store=False,
        readonly=True,
    )

    # ── Sent flags — prevent duplicate emails ─────────────────────────────────
    pe1_warning_sent = fields.Boolean(
        string="Alerte PE1 envoyée",
        default=False,
        copy=False,
    )
    pe2_warning_sent = fields.Boolean(
        string="Alerte PE2 envoyée",
        default=False,
        copy=False,
    )
    pe2_expiry_sent = fields.Boolean(
        string="Alerte expiration PE2 envoyée",
        default=False,
        copy=False,
    )

    # ── Reset flags when hire date or type changes ────────────────────────────
    @api.onchange('date_embauche', 'type_employee')
    def _onchange_reset_pe_flags(self):
        self.pe1_warning_sent = False
        self.pe2_warning_sent = False
        self.pe2_expiry_sent  = False

    # ── PE durations ──────────────────────────────────────────────────────────
    @staticmethod
    def _get_pe_status(hire, type_employee, today):
        durations = {
            'ouvrier':  (timedelta(days=15),  timedelta(days=15)),
            'employee': (timedelta(days=45),  timedelta(days=45)),
            'cadre':    (timedelta(days=90),  timedelta(days=105)),
        }
        dur = durations.get(type_employee)
        if not dur or not hire:
            return False, None, None
        dur1, dur2 = dur
        if hasattr(hire, 'date'):
            hire = hire.date()
        end_pe1 = hire + dur1
        end_pe2 = end_pe1 + dur2
        if today < end_pe1:
            return 'premiere', end_pe1, end_pe2
        elif today < end_pe2:
            return 'deuxieme', end_pe1, end_pe2
        else:
            return 'termine', end_pe1, end_pe2

    # ── Always computed from real today ──────────────────────────────────────
    @api.depends('date_embauche', 'type_employee')
    def _compute_periode_essai(self):
        today = date.today()
        for rec in self:
            status, _, _ = self._get_pe_status(
                rec.date_embauche, rec.type_employee, today
            )
            rec.periode_essai = status or False

    # ── onchange ──────────────────────────────────────────────────────────────
    @api.onchange('department_type', 'shift_type')
    def _onchange_department_type(self):
        if self.department_type != 'warehouse':
            self.shift_type = False

    # ── Solde congé access control ────────────────────────────────────────────
    def _can_edit_solde_conge(self):
        if self.env.context.get('allow_solde_conge_update'):
            return True
        if self.env.su:
            return True
        return self.env.user.has_group('base.group_system')

    def _update_solde_conge(self, new_value):
        self.ensure_one()
        self.sudo().with_context(allow_solde_conge_update=True).write(
            {'solde_conge': new_value}
        )
        return True

    # ── Seniority helpers ─────────────────────────────────────────────────────
    def _get_monthly_rate(self, date_embauche, today):
        from dateutil.relativedelta import relativedelta
        years = relativedelta(today, date_embauche).years
        milestones = years // 5
        return 1.5 + (milestones * 1.5 / 12)

    def _is_anniversary_month(self, date_embauche, today):
        from dateutil.relativedelta import relativedelta
        if today.day != 1:
            return False
        if date_embauche.month != today.month:
            return False
        years_elapsed = relativedelta(today, date_embauche).years
        if years_elapsed < 5:
            return False
        anniversary_this_year = date_embauche.replace(year=today.year)
        if anniversary_this_year.month != today.month:
            return False
        return years_elapsed % 5 == 0

    # ── ORM overrides ─────────────────────────────────────────────────────────
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('date_embauche'):
                vals['date_embauche'] = fields.Date.today()
        return super().create(vals_list)

    def write(self, vals):
        # Reset PE flags if hire date or type changes
        if 'date_embauche' in vals or 'type_employee' in vals:
            vals['pe1_warning_sent'] = False
            vals['pe2_warning_sent'] = False
            vals['pe2_expiry_sent']  = False
        if 'solde_conge' in vals \
                and not self.env.context.get('allow_solde_conge_update') \
                and not self.env.su \
                and not self._can_edit_solde_conge():
            raise AccessError(_("You are not allowed to modify Solde congé."))
        return super().write(vals)

    # ── Cron: monthly solde congé increment ───────────────────────────────────
    @api.model
    def _cron_increment_solde_conge(self):
        from dateutil.relativedelta import relativedelta
        today = fields.Date.today()

        employees = self.env['hr.employee'].search([
            ('date_embauche', '!=', False),
            ('active',        '=',  True),
        ])
        for employee in employees:
            hire_date    = employee.date_embauche
            monthly_rate = employee._get_monthly_rate(hire_date, today)
            new_solde    = employee.solde_conge + monthly_rate
            if employee._is_anniversary_month(hire_date, today):
                new_solde += 1.5
            employee.with_context(
                allow_solde_conge_update=True
            ).write({'solde_conge': new_solde})
        return True

    # ── Helper: send all notifications ───────────────────────────────────────
    def _send_pe_notifications(self, hr_users, hr_emails, msg, subject,
                               pe_label, days_left, end_date, action_text,
                               header_color, banner_bg, banner_border,
                               banner_color, badge_bg, badge_color):
        self.ensure_one()
        emp = self

        type_labels = {
            'ouvrier':  'Ouvrier',
            'employee': 'Employé',
            'cadre':    'Cadre',
        }

        # 1. Chatter note
        emp.message_post(
            body=msg,
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )

        # 2. Activity for each RH/Manager user
        for hr_user in hr_users:
            existing = self.env['mail.activity'].search([
                ('res_model', '=', 'hr.employee'),
                ('res_id', '=', emp.id),
                ('user_id', '=', hr_user.id),
                ('note', 'ilike', "période d'essai"),
            ], limit=1)
            if not existing:
                emp.activity_schedule(
                    'mail.mail_activity_data_todo',
                    date_deadline=fields.Date.today(),
                    summary=f"Période d'essai — {emp.name}",
                    note=msg,
                    user_id=hr_user.id,
                )

        # 3. Real-time popup
        for hr_user in hr_users:
            self.env['bus.bus']._sendone(
                hr_user.partner_id,
                'simple_notification',
                {
                    'title':   "⚠️ Période d'essai",
                    'message': (
                        f"{emp.name} : {pe_label} expire dans "
                        f"{days_left} jour(s)"
                        if days_left > 0
                        else f"{emp.name} : {pe_label} a expiré aujourd'hui !"
                    ),
                    'warning': True,
                }
            )

        # 4. Email
        if not hr_emails:
            return

        emp_name    = emp.name or ''
        emp_type    = type_labels.get(emp.type_employee, 'N/A')
        emp_date    = str(emp.date_embauche) if emp.date_embauche else 'N/A'
        emp_manager = emp.parent_id.name if emp.parent_id else 'N/A'
        emp_dept    = emp.department_id.name if emp.department_id else 'N/A'
        emp_company = emp.company_id.name if emp.company_id else ''
        emp_url     = f"/odoo/employees/{emp.id}"

        jours_label = (
            f"<strong>{days_left} jour(s)</strong>"
            if days_left > 0
            else "<strong style='color:#dc3545'>Expire aujourd'hui !</strong>"
        )

        body_html = f"""
<div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto;
            border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden;">
    <div style="background-color: {header_color}; padding: 24px 32px;">
        <h2 style="color: #ffffff; margin: 0; font-size: 20px;">
            &#9888; Avertissement — Période d'Essai
        </h2>
    </div>
    <div style="padding: 32px; background-color: #ffffff;">
        <p style="font-size: 15px; color: #333;">Bonjour,</p>
        <p style="font-size: 15px; color: #333;">
            Ceci est un rappel automatique concernant la période d'essai
            de l'employé(e) suivant(e) :
        </p>
        <table style="width: 100%; border-collapse: collapse; margin: 20px 0;
                      border: 1px solid #e0e0e0;">
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555; width: 40%;">Employé(e)</td>
                <td style="padding: 10px 16px; color: #333;">{emp_name}</td>
            </tr>
            <tr>
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555; background-color: #fafafa;">
                    Type d'employé</td>
                <td style="padding: 10px 16px; color: #333;">{emp_type}</td>
            </tr>
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555;">Date d'embauche</td>
                <td style="padding: 10px 16px; color: #333;">{emp_date}</td>
            </tr>
            <tr>
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555; background-color: #fafafa;">
                    Période d'essai</td>
                <td style="padding: 10px 16px;">
                    <span style="background-color: {badge_bg};
                                 color: {badge_color};
                                 padding: 4px 10px; border-radius: 12px;
                                 font-size: 13px; font-weight: bold;">
                        {pe_label}
                    </span>
                </td>
            </tr>
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555;">Date d'expiration</td>
                <td style="padding: 10px 16px; color: #333;">{end_date}</td>
            </tr>
            <tr>
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555; background-color: #fafafa;">
                    Responsable</td>
                <td style="padding: 10px 16px; color: #333;">{emp_manager}</td>
            </tr>
            <tr style="background-color: #f5f5f5;">
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555;">Département</td>
                <td style="padding: 10px 16px; color: #333;">{emp_dept}</td>
            </tr>
            <tr>
                <td style="padding: 10px 16px; font-weight: bold;
                            color: #555; background-color: #fafafa;">
                    Jours restants</td>
                <td style="padding: 10px 16px; color: #333;">{jours_label}</td>
            </tr>
        </table>
        <div style="background-color: {banner_bg};
                    border-left: 4px solid {banner_border};
                    padding: 14px 18px; margin: 20px 0;">
            <p style="margin: 0; color: {banner_color}; font-size: 14px;">
                <strong>Action requise :</strong> {action_text}
            </p>
        </div>
        <div style="text-align: center; margin: 28px 0;">
            <a href="{emp_url}"
               style="background-color: {header_color}; color: #ffffff;
                      padding: 12px 28px; border-radius: 6px;
                      text-decoration: none; font-size: 15px;
                      font-weight: bold;">
                Voir la fiche employé
            </a>
        </div>
        <p style="font-size: 13px; color: #999; margin-top: 32px;">
            Ce message a été généré automatiquement par le système RH.
            Merci de ne pas y répondre directement.
        </p>
    </div>
    <div style="background-color: #f0f0f0; padding: 14px 32px;
                text-align: center;">
        <p style="margin: 0; font-size: 12px; color: #888;">
            {emp_company} — Système de Gestion RH
        </p>
    </div>
</div>"""

        mail_values = {
            'subject':     subject,
            'body_html':   body_html,
            'email_to':    hr_emails,
            'email_from':  self.env.company.email or '',
            'author_id':   self.env.user.partner_id.id,
            'auto_delete': True,
        }
        mail = self.env['mail.mail'].sudo().create(mail_values)
        mail.sudo().send()
        _logger.info(
            "PE Warning — email sent for %s to: %s", emp.name, hr_emails,
        )

    # ── Cron: daily période d'essai warnings ──────────────────────────────────
    @api.model
    def _cron_check_periode_essai(self):
        """
        Daily cron — 3 alerts per employee lifecycle:
          1. PE1 warning: within warn1 days of PE1 end (flag: pe1_warning_sent)
          2. PE2 warning: within warn2 days of PE2 end (flag: pe2_warning_sent)
          3. PE2 expiry:  on the day PE2 ends          (flag: pe2_expiry_sent)

        Uses <= range + sent flags → fires exactly once, never missed,
        never duplicated even if cron runs multiple times.
        """
        today = date.today()

        warning_days = {
            'ouvrier':  (3,  7),
            'employee': (7,  7),
            'cadre':    (10, 10),
        }

        all_employees = self.env['hr.employee'].search([
            ('date_embauche', '!=', False),
            ('type_employee', '!=', False),
            ('active', '=', True),
        ])

        # ── Fetch RH + Manager users, exclude Odoo Administrators ────────
        hr_group      = self.env.ref(
            'employee.group_employee_hr',      raise_if_not_found=False
        )
        manager_group = self.env.ref(
            'employee.group_employee_manager', raise_if_not_found=False
        )
        admin_group   = self.env.ref(
            'base.group_system',               raise_if_not_found=False
        )
        admin_user_ids = admin_group.users.ids if admin_group else []

        hr_users = self.env['res.users']
        if hr_group:
            hr_users |= hr_group.users
        if manager_group:
            hr_users |= manager_group.users

        hr_users = hr_users.filtered(
            lambda u: u.active
            and not u.share
            and u.email
            and u.id not in admin_user_ids
        )

        _logger.info(
            "PE Warning cron — users to notify: %s",
            hr_users.mapped('name'),
        )

        if not hr_users:
            _logger.warning(
                "PE Warning cron — No RH/Manager users found, skipping."
            )
            return True

        hr_emails = ','.join(u.email for u in hr_users)

        for emp in all_employees:
            warn1, warn2 = warning_days[emp.type_employee]

            pe_status, end_pe1, end_pe2 = self._get_pe_status(
                emp.date_embauche, emp.type_employee, today
            )

            if not pe_status:
                continue

            days_to_pe1 = (end_pe1 - today).days
            days_to_pe2 = (end_pe2 - today).days

            _logger.info(
                "PE check — %s | status=%s | d_pe1=%d | d_pe2=%d | "
                "flags: pe1=%s pe2=%s exp=%s",
                emp.name, pe_status, days_to_pe1, days_to_pe2,
                emp.pe1_warning_sent, emp.pe2_warning_sent,
                emp.pe2_expiry_sent,
            )

            # ── ALERT 1: PE1 warning ──────────────────────────────────────
            if (pe_status == 'premiere'
                    and 0 < days_to_pe1 <= warn1
                    and not emp.pe1_warning_sent):

                emp._send_pe_notifications(
                    hr_users=hr_users,
                    hr_emails=hr_emails,
                    msg=(
                        f"⚠️ <b>{emp.name}</b> : la "
                        f"<b>1ère période d'essai</b> expire dans "
                        f"<b>{days_to_pe1} jour(s)</b> (le {end_pe1})."
                    ),
                    subject=(
                        f"⚠️ 1ère PE — {emp.name} "
                        f"expire dans {days_to_pe1} jour(s)"
                    ),
                    pe_label="1ère Période d'Essai",
                    days_left=days_to_pe1,
                    end_date=str(end_pe1),
                    action_text=(
                        "La <strong>1ère période d'essai</strong> de "
                        "cet(te) employé(e) arrive à expiration. Veuillez "
                        "prendre les mesures nécessaires (confirmation, "
                        "renouvellement ou fin de contrat)."
                    ),
                    header_color='#875A7B',
                    banner_bg='#fff3cd',
                    banner_border='#ffc107',
                    banner_color='#856404',
                    badge_bg='#d1ecf1',
                    badge_color='#0c5460',
                )
                emp.sudo().write({'pe1_warning_sent': True})
                _logger.info("PE1 warning sent and flagged for %s", emp.name)

            # ── ALERT 2: PE2 warning ──────────────────────────────────────
            elif (pe_status == 'deuxieme'
                    and 0 < days_to_pe2 <= warn2
                    and not emp.pe2_warning_sent):

                emp._send_pe_notifications(
                    hr_users=hr_users,
                    hr_emails=hr_emails,
                    msg=(
                        f"⚠️ <b>{emp.name}</b> : la "
                        f"<b>2ème période d'essai</b> expire dans "
                        f"<b>{days_to_pe2} jour(s)</b> (le {end_pe2})."
                    ),
                    subject=(
                        f"⚠️ 2ème PE — {emp.name} "
                        f"expire dans {days_to_pe2} jour(s)"
                    ),
                    pe_label="2ème Période d'Essai",
                    days_left=days_to_pe2,
                    end_date=str(end_pe2),
                    action_text=(
                        "La <strong>2ème période d'essai</strong> de "
                        "cet(te) employé(e) arrive à expiration. Veuillez "
                        "prendre les mesures nécessaires."
                    ),
                    header_color='#e07b39',
                    banner_bg='#fff3cd',
                    banner_border='#ffc107',
                    banner_color='#856404',
                    badge_bg='#fff3cd',
                    badge_color='#856404',
                )
                emp.sudo().write({'pe2_warning_sent': True})
                _logger.info("PE2 warning sent and flagged for %s", emp.name)

            # ── ALERT 3: PE2 expiry day ───────────────────────────────────
            elif (pe_status == 'termine'
                    and days_to_pe2 == 0
                    and not emp.pe2_expiry_sent):

                emp._send_pe_notifications(
                    hr_users=hr_users,
                    hr_emails=hr_emails,
                    msg=(
                        f"🚨 <b>{emp.name}</b> : la "
                        f"<b>période d'essai complète a expiré "
                        f"aujourd'hui</b> ({end_pe2}). "
                        f"Action requise immédiatement."
                    ),
                    subject=(
                        f"🚨 PE Expirée — {emp.name} : "
                        f"période d'essai terminée aujourd'hui"
                    ),
                    pe_label="PE Expirée",
                    days_left=0,
                    end_date=str(end_pe2),
                    action_text=(
                        "La <strong>période d'essai complète</strong> de "
                        "cet(te) employé(e) a expiré "
                        "<strong>aujourd'hui</strong>. Une décision "
                        "contractuelle est requise immédiatement."
                    ),
                    header_color='#dc3545',
                    banner_bg='#f8d7da',
                    banner_border='#dc3545',
                    banner_color='#721c24',
                    badge_bg='#f8d7da',
                    badge_color='#721c24',
                )
                emp.sudo().write({'pe2_expiry_sent': True})
                _logger.info("PE2 expiry alert sent and flagged for %s", emp.name)

        return True