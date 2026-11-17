from odoo import fields, models
from odoo.addons.base.models.res_partner import _tz_get

from .tz_utils import DEFAULT_TZ, TZ_PARAM


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    attendance_timezone = fields.Selection(
        _tz_get,
        string='Fuseau horaire des présences',
        config_parameter=TZ_PARAM,
        default=DEFAULT_TZ,
        help="Fuseau horaire local de l'entreprise. Tous les horaires de shift, "
             "pauses, absences et statuts sont interprétés dans ce fuseau, et les "
             "pointages sont convertis vers ce fuseau.",
    )
