from flask import Blueprint, render_template, send_file

from vinylpi.paths import BASE_DIR
from vinylpi.version import get_version_info

pages_bp = Blueprint("pages", __name__)


@pages_bp.get("/index.html")
@pages_bp.get("/")
def index():
    return render_template("pages/dashboard.html", active_page="dashboard")


@pages_bp.get("/settings.html")
def settings_page():
    return render_template("pages/settings.html", active_page="settings")


@pages_bp.get("/stats.html")
def stats_page():
    return render_template("pages/stats.html", active_page="stats")


@pages_bp.get("/logs.html")
def logs_page():
    return render_template("pages/logs.html", active_page="logs")


@pages_bp.get("/about.html")
def about_page():
    return render_template("pages/about.html", active_page="about", version_info=get_version_info())


@pages_bp.get("/pixoo.html")
def pixoo_page():
    return render_template("pages/pixoo.html", active_page="pixoo")


@pages_bp.get("/assets/readme/Logo.png")
def readme_logo():
    return send_file(
        BASE_DIR / "assets" / "readme" / "Logo.png",
        mimetype="image/png",
        conditional=True,
        max_age=86400,
    )
