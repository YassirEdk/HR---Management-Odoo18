from odoo import models, fields, api


class AttendanceDeviceSyncLog(models.Model):
    """Persistent evidence of every device sync.

    ISO 9001 §9.1.1 requires that the results of monitoring be RETAINED as
    documented information — a server log file that rotates is not an evidence
    record. This model turns each sync cycle into a queryable record, and it
    serves three clauses at once:

      §7.1.5.2  the measured clock drift is the periodic verification of the
                clock as a measuring instrument;
      §9.1.1    the read/audit counters are the proof that monitoring ran;
      §10.2     the accumulated history is the raw material for root-cause
                analysis (which device fails, how often, since when).

    Rows are written with sudo() by the sync engine and are read-only for
    everyone but Settings, so the evidence cannot be altered after the fact.
    """
    _name        = 'attendance.device.sync.log'
    _description = 'Journal de synchronisation des pointeuses'
    _order       = 'sync_date desc, id desc'

    device_id = fields.Many2one(
        'attendance.device',
        string='Pointeuse',
        required=True,
        ondelete='cascade',
        index=True,
    )
    sync_date = fields.Datetime(
        string='Date de synchronisation',
        required=True,
        default=fields.Datetime.now,
        index=True,
    )
    state = fields.Selection(
        selection=[
            ('success', 'Succès'),
            ('partial', 'Écarts détectés'),
            ('failed',  'Échec'),
        ],
        string='Résultat',
        required=True,
        index=True,
    )
    trigger = fields.Selection(
        selection=[
            ('cron',   'Automatique'),
            ('manual', 'Manuel'),
        ],
        string='Déclenchement',
        default='cron',
    )
    user_id = fields.Many2one(
        'res.users',
        string='Exécuté par',
        default=lambda self: self.env.user,
    )

    # ── Volumétrie lue ───────────────────────────────────────────────────────
    records_read = fields.Integer(
        string='Pointages lus',
        help='Nombre de pointages bruts retournés par la pointeuse.',
    )
    badges_seen = fields.Integer(
        string='Badges du jour',
        help='Nombre de badges distincts ayant pointé sur la journée de shift.',
    )
    unlinked_badges = fields.Integer(
        string='Badges non rattachés',
        help='Badges ayant pointé sans employé correspondant. Chaque badge non '
             'rattaché est un pointage réel perdu — non-conformité de données '
             'maîtres (ISO 9001 §8.5.2).',
    )
    unlinked_badge_ids = fields.Char(
        string='Détail badges non rattachés',
    )

    # ── Auto-contrôle (§9.1.1) ───────────────────────────────────────────────
    audit_ok = fields.Integer(
        string='Badges conformes',
        help='Badges dont les pointages ont bien été créés et traités.',
    )
    audit_issues = fields.Integer(
        string='Badges en écart',
        help='Badges dont un pointage n’a pas été reflété dans un enregistrement.',
    )
    audit_detail = fields.Text(
        string='Détail des écarts',
    )

    # ── Vérification métrologique (§7.1.5.2) ─────────────────────────────────
    clock_drift_seconds = fields.Float(
        string='Dérive d’horloge (s)',
        digits=(16, 1),
        help='Écart entre l’horloge de la pointeuse et l’heure de référence du '
             'serveur, au moment de la lecture. Positif = la pointeuse avance.',
    )
    clock_status = fields.Selection(
        selection=[
            ('ok',      'Conforme'),
            ('drift',   'Dérive hors tolérance'),
            ('unknown', 'Non vérifiée'),
        ],
        string='État de l’horloge',
        default='unknown',
        index=True,
    )
    device_time = fields.Datetime(
        string='Heure lue sur la pointeuse (UTC)',
    )

    error_message = fields.Text(
        string='Erreur',
    )

    @api.depends('device_id', 'sync_date', 'state')
    def _compute_display_name(self):
        labels = dict(self._fields['state'].selection)
        for log in self:
            date = fields.Datetime.to_string(log.sync_date) or ''
            log.display_name = f"{log.device_id.name or '?'} — {date} — {labels.get(log.state, '')}"
