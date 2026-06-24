{
    "name": "HR New Time Off - Mapping FR",
    "version": "1.0",
    "depends": ["hr", "hr_holidays"],
    "data": [

        "views/hr_leave_manager_mapping.xml",
        "data/hr_leave_type_mapping_data.xml",
        "data/hr_solde_expiry_cron.xml",
        "data/hr_solde_expiry_mail_template.xml",
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/rules.xml",
        "views/hr_hide_my_allocation.xml",
        "views/hr_leave_kanban.xml",
        "views/status_tree.xml",
        "views/hr_leave_approval.xml",
        "views/false_create.xml",
        "views/total_solde_conge_wizard_views.xml",
        "views/hr_leave_views.xml",
        "views/desactivate_allocation_view.xml",
        "views/awakheir.xml",
        "views/hr_solde_expiry_alert_views.xml",
        "views/hr_leave_holiday_warning_wizard_views.xml",
        "views/hr_public_holiday_views.xml",
        "data/hr_public_holiday_data.xml",


    ],

    "installable": True,
}
