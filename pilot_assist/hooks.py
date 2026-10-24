app_name = "pilot_assist"
app_title = "PilotAssist"
app_publisher = "MicroNXT"
app_description = "AI chat assistant for ERPNext"
app_email = "helpdesk@micronxt.com"
app_license = "mit"

required_apps = ["erpnext"]

boot_session = "pilot_assist.api.ai_api.boot_session"

app_include_js = [
    "/assets/pilot_assist/js/pilot_assist_widget.js?v=1",
]

after_install = "pilot_assist.install.after_install"
after_migrate = "pilot_assist.install.after_migrate"
