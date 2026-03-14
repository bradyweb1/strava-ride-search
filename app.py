from flask import Flask, redirect, request, session, url_for
import requests
import psycopg2
import os
from dotenv import load_dotenv
from urllib.parse import urlencode
from html import escape
import threading

load_dotenv()

app = Flask(__name__)
app.secret_key = os.environ["SECRET_KEY"]

CLIENT_ID = os.environ["CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIENT_SECRET"]
REDIRECT_URI = os.environ["REDIRECT_URI"]

DATABASE_URL = os.environ["DATABASE_URL"]
active_imports = set()


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def save_activities_to_db(user_id, activities):
    conn = get_db_connection()
    cur = conn.cursor()

    for ride in activities:
        cur.execute(
            """
            INSERT INTO activities (
                id, user_id, name, sport_type, start_date, distance, elevation,
                moving_time, kudos, comments, prs, max_speed, avg_speed,
                avg_power, max_power, avg_hr, max_hr
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                name = EXCLUDED.name,
                sport_type = EXCLUDED.sport_type,
                start_date = EXCLUDED.start_date,
                distance = EXCLUDED.distance,
                elevation = EXCLUDED.elevation,
                moving_time = EXCLUDED.moving_time,
                kudos = EXCLUDED.kudos,
                comments = EXCLUDED.comments,
                prs = EXCLUDED.prs,
                max_speed = EXCLUDED.max_speed,
                avg_speed = EXCLUDED.avg_speed,
                avg_power = EXCLUDED.avg_power,
                max_power = EXCLUDED.max_power,
                avg_hr = EXCLUDED.avg_hr,
                max_hr = EXCLUDED.max_hr
            """,
            (
                ride.get("id"),
                user_id,
                ride.get("name"),
                ride.get("sport_type"),
                ride.get("start_date_local"),
                ride.get("distance"),
                ride.get("total_elevation_gain"),
                ride.get("moving_time"),
                ride.get("kudos_count"),
                ride.get("comment_count"),
                ride.get("pr_count"),
                ride.get("max_speed"),
                ride.get("average_speed"),
                ride.get("average_watts"),
                ride.get("max_watts"),
                ride.get("average_heartrate"),
                ride.get("max_heartrate"),
            ),
        )

    conn.commit()
    cur.close()
    conn.close()


def load_activities_from_db(user_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            id, name, sport_type, start_date, distance, elevation,
            moving_time, kudos, comments, prs, max_speed, avg_speed,
            avg_power, max_power, avg_hr, max_hr
        FROM activities
        WHERE user_id = %s
        """,
        (user_id,),
    )

    rows = cur.fetchall()
    cur.close()
    conn.close()

    activities = []
    for row in rows:
        activities.append(
            {
                "id": row[0],
                "name": row[1],
                "sport_type": row[2],
                "start_date_local": row[3].isoformat() if row[3] else "",
                "distance": row[4],
                "total_elevation_gain": row[5],
                "moving_time": row[6],
                "kudos_count": row[7],
                "comment_count": row[8],
                "pr_count": row[9],
                "max_speed": row[10],
                "average_speed": row[11],
                "average_watts": row[12],
                "max_watts": row[13],
                "average_heartrate": row[14],
                "max_heartrate": row[15],
            }
        )

    return activities


def meters_to_miles(meters):
    return meters * 0.000621371


def meters_to_feet(meters):
    return meters * 3.28084


def mps_to_mph(mps):
    return mps * 2.23694


def seconds_to_hhmm(seconds):
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}:{minutes:02d}"


def hhmm_to_seconds(value):
    value = value.strip()
    if not value:
        return None
    try:
        parts = value.split(":")
        if len(parts) != 2:
            return None
        hours = int(parts[0])
        minutes = int(parts[1])
        return hours * 3600 + minutes * 60
    except ValueError:
        return None


def safe_float(value):
    value = value.strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None    

def fetch_first_activities(access_token, total_to_fetch=100):
    headers = {"Authorization": f"Bearer {access_token}"}
    activities = []
    page = 1

    while len(activities) < total_to_fetch:
        response = requests.get(
            f"https://www.strava.com/api/v3/athlete/activities?per_page=200&page={page}",
            headers=headers,
        )

        batch = response.json()

        if isinstance(batch, dict) and batch.get("message"):
            return batch

        if not batch:
            break

        activities.extend(batch)
        page += 1

    return activities[:total_to_fetch]

def fetch_all_activities(access_token):
    headers = {"Authorization": f"Bearer {access_token}"}
    all_activities = []
    page = 1

    while True:
        response = requests.get(
            f"https://www.strava.com/api/v3/athlete/activities?per_page=200&page={page}",
            headers=headers,
        )
        batch = response.json()

        if isinstance(batch, dict) and batch.get("message"):
            return batch

        if not batch:
            break

        all_activities.extend(batch)
        page += 1

    return all_activities

def background_import_all_activities(user_id, access_token):
    global active_imports

    if user_id in active_imports:
        return

    active_imports.add(user_id)

    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        page = 1

        while True:
            response = requests.get(
                f"https://www.strava.com/api/v3/athlete/activities?per_page=200&page={page}",
                headers=headers,
            )

            batch = response.json()

            if isinstance(batch, dict) and batch.get("message"):
                print("Background import error:", batch)
                break

            if not batch:
                break

            save_activities_to_db(user_id, batch)
            page += 1

        print(f"Background import finished for user {user_id}")

    except Exception as e:
        print(f"Background import crashed for user {user_id}: {e}")

    finally:
        active_imports.discard(user_id)

@app.route("/import_status")
def import_status():
    user_id = session.get("user_id")

    if not user_id:
        return {"status": "unknown"}

    if user_id in active_imports:
        return {"status": "importing"}
    else:
        return {"status": "complete"}

@app.route("/")
def home():
    return """
    <div style="font-family:Arial, sans-serif; max-width:600px; margin:40px auto; text-align:center;">

    <h2 style="font-size:34px;">RideFind3000</h2>

    <p style="font-size:24px;">
    <a href="/authorize">Connect with Strava</a>
    </p>

    <hr style="margin-top:30px;">

    <p style="font-size:18px; color:#666;">
    Powered by Strava
    </p>

    <p style="font-size:18px; color:#666;">
    RideFind3000 uses the Strava API but is not affiliated, endorsed, or certified by Strava.
    </p>

    <p style="font-size:18px;">
    <a href="/privacy">Privacy Policy & Information</a>
    </p>

    </div>
    """


@app.route("/authorize")
def authorize():
    auth_url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={CLIENT_ID}"
        f"&response_type=code"
        f"&redirect_uri={REDIRECT_URI}"
        f"&approval_prompt=force"
        f"&scope=activity:read_all"
    )
    return redirect(auth_url)


@app.route("/exchange_token")
def exchange_token():
    code = request.args.get("code")

    token_response = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "code": code,
            "grant_type": "authorization_code",
        },
    )

    token_data = token_response.json()

    if "access_token" not in token_data:
        return f"<pre>{escape(str(token_data))}</pre>"

    session["access_token"] = token_data["access_token"]
    session["user_id"] = token_data["athlete"]["id"]

    return redirect(url_for("loading"))

@app.route("/loading")
def loading():
    return """
    <html>
    <head>
        <title>Loading Recent Activities</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                background: #f7f7f7;
                margin: 0;
                padding: 0;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
            }
            .box {
                background: white;
                padding: 35px;
                border-radius: 12px;
                box-shadow: 0 2px 12px rgba(0,0,0,0.12);
                max-width: 500px;
                text-align: center;
            }
            h1 {
                margin-top: 0;
                font-size: 44px;
            }
            p {
                font-size: 26px;
                color: #444;
                line-height: 1.5;
            }
            .small {
                margin-top: 25px;
                font-size: 20px;
                color: #777;
            }
        </style>
    </head>
    <body>
        <div class="box">
            <h1>Loading your recent Strava activities...</h1>
            <p>Please hang tight while we import your most recent activities.</p>
            <p>This usually takes less than a 30 seconds.</p>
            <div class="small" id="status">Starting import...</div>
        </div>

        <script>
            window.onload = async function() {
                try {
                    document.getElementById("status").innerText = "Importing recent activities...";
                    await fetch("/first_import", { credentials: "same-origin" });
                    window.location.href = "/activities?sync_msg=Recent+activities+loaded";
                } catch (error) {
                    document.getElementById("status").innerText = "Something went wrong. Please reload the page.";
                }
            };
        </script>
    </body>
    </html>
    """

@app.route("/first_import")
def first_import():
    access_token = session.get("access_token")
    user_id = session.get("user_id")

    if not access_token or not user_id:
        return redirect(url_for("home"))

    existing_activities = load_activities_from_db(user_id)

    if not existing_activities:
        starter_activities = fetch_first_activities(access_token, 100)

        if isinstance(starter_activities, dict) and starter_activities.get("message"):
            return f"<pre>{escape(str(starter_activities))}</pre>"

        if isinstance(starter_activities, list):
            save_activities_to_db(user_id, starter_activities)

            threading.Thread(
                target=background_import_all_activities,
                args=(user_id, access_token),
                daemon=True
            ).start()

    return "OK"

@app.route("/activities")
def activities():
    access_token = session.get("access_token")
    user_id = session.get("user_id")

    if not access_token or not user_id:
        return redirect(url_for("home"))

    all_activities = load_activities_from_db(user_id)
    
    # Determine the current background import status
    is_importing = user_id in active_imports
    
    # Check if the URL indicates the import just finished and refreshed
    import_just_finished = request.args.get("import_just_finished") == "true"
    
    banner_html = ""

    if is_importing:
        # If an import is actively running, show the "importing" banner
        banner_html = """
        <div id="import-banner" style="
        background:#eaf3ff;
        border:1px solid #bcd4ff;
        padding:12px;
        margin-bottom:15px;
        border-radius:8px;
        font-family:Arial;
        font-size:18px;">
        Importing the rest of your Strava history in the background.<br>Give us up to a few minutes, but we will let you know when it's finished.<br>This is a one-time process.
        </div>
        """
    elif import_just_finished:
        # If the import has finished AND the page reloaded with the flag, show a static complete banner.
        # This banner will not have the auto-refresh instruction.
        banner_html = """
        <div id="import-banner" style="
        background:#e7f7ea;
        border:1px solid #b9e2c0;
        padding:12px;
        margin-bottom:15px;
        border-radius:8px;
        font-family:Arial;
        font-size:18px;">
        Full Strava activity history imported.
        </div>
        """
    
    if user_id not in active_imports and len(all_activities) < 500:
        threading.Thread(
            target=background_import_all_activities,
            args=(user_id, access_token),
            daemon=True
        ).start()


    if not all_activities:
        return redirect(url_for("first_import"))

    keyword = request.args.get("keyword", "").strip().lower()
    year = request.args.get("year", "").strip()
    month = request.args.get("month", "").strip()
    exact_date = request.args.get("exact_date", "").strip()
    start_date = request.args.get("start_date", "").strip()
    end_date = request.args.get("end_date", "").strip()

    min_miles = safe_float(request.args.get("min_miles", ""))
    max_miles = safe_float(request.args.get("max_miles", ""))

    min_elev = safe_float(request.args.get("min_elev", ""))
    max_elev = safe_float(request.args.get("max_elev", ""))

    min_time_seconds = hhmm_to_seconds(request.args.get("min_time", ""))
    max_time_seconds = hhmm_to_seconds(request.args.get("max_time", ""))

    min_avg_power = safe_float(request.args.get("min_avg_power", ""))
    max_avg_power = safe_float(request.args.get("max_avg_power", ""))

    min_max_power = safe_float(request.args.get("min_max_power", ""))
    max_max_power = safe_float(request.args.get("max_max_power", ""))

    min_avg_hr = safe_float(request.args.get("min_avg_hr", ""))
    max_avg_hr = safe_float(request.args.get("max_avg_hr", ""))

    min_max_hr = safe_float(request.args.get("min_max_hr", ""))
    max_max_hr = safe_float(request.args.get("max_max_hr", ""))

    min_max_speed = safe_float(request.args.get("min_max_speed", ""))
    max_max_speed = safe_float(request.args.get("max_max_speed", ""))

    min_avg_speed = safe_float(request.args.get("min_avg_speed", ""))
    max_avg_speed = safe_float(request.args.get("max_avg_speed", ""))

    selected_types = request.args.getlist("sport_type")
    sort_by = request.args.get("sort_by", "date_desc")
    sync_msg = request.args.get("sync_msg", "")

    preferred_order = [
        "Ride",
        "MountainBikeRide",
        "GravelRide",
        "VirtualRide",
        "EBikeRide",
        "EMountainBikeRide",
        "Run",
        "TrailRun",
        "Walk",
        "Hike",
        "Swim",
        "Workout",
        "WeightTraining",
    ]

    found_types = {a.get("sport_type", "") for a in all_activities if a.get("sport_type")}
    sport_types = [s for s in preferred_order if s in found_types]
    sport_types += sorted(found_types - set(preferred_order))

    years = sorted(
        {a.get("start_date_local", "")[:4] for a in all_activities if a.get("start_date_local")},
        reverse=True
    )

    filtered = []

    for ride in all_activities:
        name = ride.get("name", "")
        ride_date = ride.get("start_date_local", "")[:10]
        ride_year = ride_date[:4] if ride_date else ""
        ride_month = ride_date[5:7] if ride_date else ""

        distance_miles = meters_to_miles(ride.get("distance", 0) or 0)
        elev_feet = meters_to_feet(ride.get("total_elevation_gain", 0) or 0)
        moving_time = ride.get("moving_time", 0) or 0

        avg_power = ride.get("average_watts")
        max_power = ride.get("max_watts")
        avg_hr = ride.get("average_heartrate")
        max_hr = ride.get("max_heartrate")
        sport_type = ride.get("sport_type", "")

        max_speed_mph = mps_to_mph(ride.get("max_speed", 0) or 0)
        avg_speed_mph = mps_to_mph(ride.get("average_speed", 0) or 0)

        if keyword and keyword not in name.lower():
            continue

        if year and ride_year != year:
            continue

        if month and ride_month != month.zfill(2):
            continue

        if exact_date and ride_date != exact_date:
            continue

        if start_date and ride_date and ride_date < start_date:
            continue

        if end_date and ride_date and ride_date > end_date:
            continue

        if min_miles is not None and distance_miles < min_miles:
            continue
        if max_miles is not None and distance_miles > max_miles:
            continue

        if min_elev is not None and elev_feet < min_elev:
            continue
        if max_elev is not None and elev_feet > max_elev:
            continue

        if min_time_seconds is not None and moving_time < min_time_seconds:
            continue
        if max_time_seconds is not None and moving_time > max_time_seconds:
            continue

        if selected_types and sport_type not in selected_types:
            continue

        if min_avg_power is not None and (avg_power is None or avg_power < min_avg_power):
            continue
        if max_avg_power is not None and (avg_power is None or avg_power > max_avg_power):
            continue

        if min_max_power is not None and (max_power is None or max_power < min_max_power):
            continue
        if max_max_power is not None and (max_power is None or max_power > max_max_power):
            continue

        if min_avg_hr is not None and (avg_hr is None or avg_hr < min_avg_hr):
            continue
        if max_avg_hr is not None and (avg_hr is None or avg_hr > max_avg_hr):
            continue

        if min_max_hr is not None and (max_hr is None or max_hr < min_max_hr):
            continue
        if max_max_hr is not None and (max_hr is None or max_hr > max_max_hr):
            continue

        if min_max_speed is not None and max_speed_mph < min_max_speed:
            continue
        if max_max_speed is not None and max_speed_mph > max_max_speed:
            continue

        if min_avg_speed is not None and avg_speed_mph < min_avg_speed:
            continue
        if max_avg_speed is not None and avg_speed_mph > max_avg_speed:
            continue

        filtered.append(ride)

    def sort_value(ride, field, default=0):
        value = ride.get(field)
        return default if value is None else value

    if sort_by == "date_asc":
        filtered.sort(key=lambda r: r.get("start_date_local", ""))
    elif sort_by == "date_desc":
        filtered.sort(key=lambda r: r.get("start_date_local", ""), reverse=True)
    elif sort_by == "name_desc":
        filtered.sort(key=lambda r: r.get("name", "").lower(), reverse=True)
    elif sort_by == "name_asc":
        filtered.sort(key=lambda r: r.get("name", "").lower())
    elif sort_by == "type_desc":
        filtered.sort(key=lambda r: r.get("sport_type", ""), reverse=True)
    elif sort_by == "type_asc":
        filtered.sort(key=lambda r: r.get("sport_type", ""))
    elif sort_by == "miles_desc":
        filtered.sort(key=lambda r: r.get("distance", 0) or 0, reverse=True)
    elif sort_by == "miles_asc":
        filtered.sort(key=lambda r: r.get("distance", 0) or 0)
    elif sort_by == "elev_desc":
        filtered.sort(key=lambda r: r.get("total_elevation_gain", 0) or 0, reverse=True)
    elif sort_by == "elev_asc":
        filtered.sort(key=lambda r: r.get("total_elevation_gain", 0) or 0)
    elif sort_by == "time_desc":
        filtered.sort(key=lambda r: r.get("moving_time", 0) or 0, reverse=True)
    elif sort_by == "time_asc":
        filtered.sort(key=lambda r: r.get("moving_time", 0) or 0)
    elif sort_by == "kudos_desc":
        filtered.sort(key=lambda r: r.get("kudos_count", 0) or 0, reverse=True)
    elif sort_by == "kudos_asc":
        filtered.sort(key=lambda r: r.get("kudos_count", 0) or 0)
    elif sort_by == "comments_desc":
        filtered.sort(key=lambda r: r.get("comment_count", 0) or 0, reverse=True)
    elif sort_by == "comments_asc":
        filtered.sort(key=lambda r: r.get("comment_count", 0) or 0)
    elif sort_by == "prs_desc":
        filtered.sort(key=lambda r: r.get("pr_count", 0) or 0, reverse=True)
    elif sort_by == "prs_asc":
        filtered.sort(key=lambda r: r.get("pr_count", 0) or 0)
    elif sort_by == "max_speed_desc":
        filtered.sort(key=lambda r: r.get("max_speed", 0) or 0, reverse=True)
    elif sort_by == "max_speed_asc":
        filtered.sort(key=lambda r: r.get("max_speed", 0) or 0)
    elif sort_by == "avg_speed_desc":
        filtered.sort(key=lambda r: r.get("average_speed", 0) or 0, reverse=True)
    elif sort_by == "avg_speed_asc":
        filtered.sort(key=lambda r: r.get("average_speed", 0) or 0)
    elif sort_by == "avg_power_desc":
        filtered.sort(key=lambda r: sort_value(r, "average_watts"), reverse=True)
    elif sort_by == "avg_power_asc":
        filtered.sort(key=lambda r: sort_value(r, "average_watts"))
    elif sort_by == "max_power_desc":
        filtered.sort(key=lambda r: sort_value(r, "max_watts"), reverse=True)
    elif sort_by == "max_power_asc":
        filtered.sort(key=lambda r: sort_value(r, "max_watts"))
    elif sort_by == "avg_hr_desc":
        filtered.sort(key=lambda r: sort_value(r, "average_heartrate"), reverse=True)
    elif sort_by == "avg_hr_asc":
        filtered.sort(key=lambda r: sort_value(r, "average_heartrate"))
    elif sort_by == "max_hr_desc":
        filtered.sort(key=lambda r: sort_value(r, "max_heartrate"), reverse=True)
    elif sort_by == "max_hr_asc":
        filtered.sort(key=lambda r: sort_value(r, "max_heartrate"))

    def selected_type_html(s_type):
        return "checked" if s_type in selected_types else ""

    def next_sort(current_sort_value, desc_value, asc_value):
        return asc_value if current_sort_value == desc_value else desc_value

    def sort_arrow(current_sort_value, desc_value, asc_value):
        if current_sort_value == desc_value:
            return " ↓"
        if current_sort_value == asc_value:
            return " ↑"
        return ""

    def build_sort_link(label, desc_value, asc_value):
        params = request.args.to_dict(flat=False)
        params["sort_by"] = [next_sort(sort_by, desc_value, asc_value)]
        query_string = urlencode(params, doseq=True)
        return f'<a href="/activities?{query_string}" class="sort-link">{escape(label)}{sort_arrow(sort_by, desc_value, asc_value)}</a>'

    html = f"""
    <html>
    <head>
        <title>RideFind3000</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: Arial, sans-serif;
                margin: 20px;
            }}
            .section {{
                border: 1px solid #ccc;
                padding: 12px;
                margin-bottom: 14px;
                border-radius: 8px;
            }}
            .row {{
                margin-bottom: 10px;
            }}
            label {{
                margin-right: 8px;
                font-weight: bold;
            }}
            input, select {{
                margin-right: 14px;
                padding: 4px;
            }}
            .types-box {{
                max-height: 220px;
                overflow-y: auto;
                border: 1px solid #ddd;
                padding: 10px;
                background: #fafafa;
            }}
            .type-item {{
                display: inline-block;
                width: 220px;
                margin-bottom: 6px;
            }}
            .button-row {{
                margin-top: 10px;
            }}
            table {{
                border-collapse: collapse;
                width: 100%;
            }}
            th, td {{
                border: 1px solid #ccc;
                padding: 8px;
                text-align: left;
            }}
            td.name-col {{
                max-width: 400px;
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }}
            th {{
                background: #f3f3f3;
            }}
            .sort-link {{
                color: inherit;
                text-decoration: none;
                display: block;
            }}
            .sort-link:hover {{
                text-decoration: underline;
            }}
            .sub-option {{
                font-weight: normal;
                margin-left: 12px;
            }}
            .table-container {{
                overflow-x: auto;
            }}
            button {{
                padding: 10px 14px;
                font-size: 16px;
            }}
            .mobile-cards {{
                display: none;
            }}
            .activity-card {{
                border: 1px solid #ccc;
                border-radius: 10px;
                padding: 12px;
                margin-bottom: 12px;
                background: #fff;
            }}
            .activity-card-title {{
                font-size: 18px;
                font-weight: bold;
                margin-bottom: 8px;
            }}
            .activity-card-title a {{
                text-decoration: none;
                color: inherit;
            }}
            .activity-card-meta {{
                color: #555;
                margin-bottom: 8px;
            }}
            .activity-card-primary {{
                display: flex;
                justify-content: space-between;
                gap: 10px;
                margin-bottom: 12px;
                padding: 8px 10px;
                background: #f7f7f7;
                border-radius: 8px;
                font-weight: bold;
                font-size: 18px;
            }}
            .activity-card-grid {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 8px 16px;
                margin-bottom: 12px;
                font-size: 15px;
            }}
            .activity-card-bottom {{
                display: flex;
                gap: 18px;
                font-size: 15px;
                font-weight: bold;
                flex-wrap: wrap;
            }}
            .filter-grid {{
                display: grid;
                grid-template-columns: repeat(4, 1fr);
                gap: 10px 14px;
            }}
            .field-box {{
                display: flex;
                flex-direction: column;
            }}
            .field-box label {{
                margin-bottom: 4px;
                font-weight: bold;
            }}
            .field-box input {{
                width: 100%;
                box-sizing: border-box;
            }}
            .back-to-top {{
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: #ff5a1f;
                color: white;
                border: none;
                border-radius: 50%;
                width: 48px;
                height: 48px;
                font-size: 20px;
                cursor: pointer;
                box-shadow: 0 3px 8px rgba(0,0,0,0.3);
                display: none;
                z-index: 999;
            }}
            .back-to-top:hover {{
                background: #e64a17;
            }}
            
            .sync-button {{
                display: inline-block;
                padding: 10px 16px;
                background-color: #fc4c02;
                color: white;
                text-decoration: none;
                border-radius: 6px;
                font-weight: bold;
                border: 2px solid #d84300;
                margin-bottom: 12px;
            }}

            .sync-button:hover {{
                background-color: #e64300;
            }}

            @media (max-width: 768px) {{
                body {{
                    margin: 10px;
                    font-size: 16px;
                }}
                .section {{
                    padding: 10px;
                }}
                .row {{
                    display: flex;
                    flex-direction: column;
                    gap: 8px;
                }}
                input:not([type="checkbox"]), select {{
                    width: 100%;
                    box-sizing: border-box;
                }}
                .sub-option input[type="checkbox"] {{
                    width: auto;
                    margin-right: 6px;
                }}

                .types-box input[type="checkbox"] {{
                    width: auto;
                }}
                .types-box {{
                    max-height: 260px;
                }}
                .types-box .type-item {{
                    display: flex;
                    align-items: center;
                }}

                .types-box .type-item label {{
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    font-weight: normal;
                }}

                .types-box .type-item input {{
                    margin: 0;
                }}
                .type-item {{
                    display: block;
                    width: 100%;
                }}
                .table-container {{
                    display: none;
                }}
                .mobile-cards {{
                    display: block;
                }}
                .filter-grid {{
                    grid-template-columns: 1fr 1fr;
                    gap: 10px 10px;
                }}
            }}

            @media (max-width: 480px) {{
                .activity-card-primary {{
                    font-size: 16px;
                    flex-wrap: wrap;
                }}
                .activity-card-grid {{
                    gap: 6px 12px;
                    font-size: 14px;
                }}
                .activity-card-bottom {{
                    gap: 14px;
                    font-size: 14px;
                }}
            }}
        </style>

        <script>
            const rideOnlyTypes = ["Ride", "MountainBikeRide", "GravelRide"];

            function toggleAllSportTypes(source) {{
                const checkboxes = document.querySelectorAll('input[name="sport_type"]');
                checkboxes.forEach(cb => cb.checked = source.checked);
                syncRidesOnlyCheckbox();
            }}

            function toggleRidesOnly(source) {{
                rideOnlyTypes.forEach(type => {{
                    const cb = document.querySelector(`input[name="sport_type"][value="${{type}}"]`);
                    if (cb) {{
                        cb.checked = source.checked;
                    }}
                }});
                syncRidesOnlyCheckbox();
            }}

            function syncRidesOnlyCheckbox() {{
                const ridesOnly = document.getElementById("rides_only");
                if (!ridesOnly) return;

                const allChecked = rideOnlyTypes.every(type => {{
                    const cb = document.querySelector(`input[name="sport_type"][value="${{type}}"]`);
                    return cb && cb.checked;
                }});

                ridesOnly.checked = allChecked;
            }}

            function clearFilters() {{
                const form = document.querySelector('form');

                form.querySelectorAll('input[type="text"], input[type="date"]').forEach(input => {{
                    input.value = '';
                }});

                form.querySelectorAll('select').forEach(select => {{
                    if (select.name !== "sort_by") {{
                        select.selectedIndex = 0;
                    }}
                }});

                form.querySelectorAll('input[name="sport_type"]').forEach(cb => {{
                    cb.checked = false;
                }});

                const ridesOnly = document.getElementById("rides_only");
                if (ridesOnly) {{
                    ridesOnly.checked = false;
                }}
            }}

            function scrollToTop() {{
                window.scrollTo({{
                    top: 0,
                    behavior: "smooth"
                }});
            }}

            window.addEventListener("scroll", function() {{
                const btn = document.querySelector(".back-to-top");
                if (!btn) return;

                if (window.scrollY > 400) {{
                    btn.style.display = "block";
                }} else {{
                    btn.style.display = "none";
                }}
            }});

            window.addEventListener("DOMContentLoaded", function() {{
                syncRidesOnlyCheckbox();

                document.querySelectorAll('input[name="sport_type"]').forEach(cb => {{
                    cb.addEventListener("change", syncRidesOnlyCheckbox);
                }});
            }});
        </script>
    </head>
    <body>
    {banner_html}
        
    <h2>RideFind3000</h2>
        
        <p><a href="/sync_recent" class="sync-button">Sync Latest Activities</a></p>
        {'<p style="color: green; font-weight: bold;">' + escape(sync_msg) + '</p>' if sync_msg else ''}

        <form method="get" action="/activities">
            <div class="section">
                <div class="row">
                    <label>Keyword</label>
                    <input type="text" name="keyword" value="{escape(request.args.get('keyword', ''))}">
                </div>

                <div class="row">
                    <label>Year</label>
                    <select name="year">
                        <option value="">All</option>
    """

    for y in years:
        selected = "selected" if y == year else ""
        html += f'<option value="{escape(y)}" {selected}>{escape(y)}</option>'

    html += f"""
                    </select>

                    <label>Month</label>
                    <select name="month">
                        <option value="">All</option>
                        <option value="01" {"selected" if month == "01" else ""}>01</option>
                        <option value="02" {"selected" if month == "02" else ""}>02</option>
                        <option value="03" {"selected" if month == "03" else ""}>03</option>
                        <option value="04" {"selected" if month == "04" else ""}>04</option>
                        <option value="05" {"selected" if month == "05" else ""}>05</option>
                        <option value="06" {"selected" if month == "06" else ""}>06</option>
                        <option value="07" {"selected" if month == "07" else ""}>07</option>
                        <option value="08" {"selected" if month == "08" else ""}>08</option>
                        <option value="09" {"selected" if month == "09" else ""}>09</option>
                        <option value="10" {"selected" if month == "10" else ""}>10</option>
                        <option value="11" {"selected" if month == "11" else ""}>11</option>
                        <option value="12" {"selected" if month == "12" else ""}>12</option>
                    </select>

                    <label>Exact Date</label>
                    <input type="date" name="exact_date" value="{escape(exact_date)}">
                </div>

                <div class="row">
                    <label>Start Date</label>
                    <input type="date" name="start_date" value="{escape(start_date)}">

                    <label>End Date</label>
                    <input type="date" name="end_date" value="{escape(end_date)}">
                </div>
            </div>
            
                <div class="section">
                <div class="row">
                    <label>Sport Type</label>

                    <label class="sub-option">
                        <input type="checkbox" id="rides_only" onclick="toggleRidesOnly(this)">
                        Rides Only
                    </label>

                    <label class="sub-option">
                        <input type="checkbox" onclick="toggleAllSportTypes(this)">
                        Select / Deselect All
                    </label>
                </div>
                
                <div class="types-box">
    """

    for s_type in sport_types:
        html += f"""
            <div class="type-item">
                <label style="font-weight: normal;">
                    <input type="checkbox" name="sport_type" value="{escape(s_type)}" {selected_type_html(s_type)}>
                    {escape(s_type)}
                </label>
            </div>
        """

    html += f"""
                </div>
            </div>                

            <div class="section">
                <div class="filter-grid">
                    <div class="field-box">
                        <label>Min Miles</label>
                        <input type="text" name="min_miles" value="{escape(request.args.get('min_miles', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Miles</label>
                        <input type="text" name="max_miles" value="{escape(request.args.get('max_miles', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Elev (ft)</label>
                        <input type="text" name="min_elev" value="{escape(request.args.get('min_elev', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Elev (ft)</label>
                        <input type="text" name="max_elev" value="{escape(request.args.get('max_elev', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Time (H:MM)</label>
                        <input type="text" name="min_time" value="{escape(request.args.get('min_time', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Time (H:MM)</label>
                        <input type="text" name="max_time" value="{escape(request.args.get('max_time', ''))}" size="6">
                    </div>
                </div>
            </div>

            <div class="section">
                <div class="filter-grid">
                    <div class="field-box">
                        <label>Min Max Speed</label>
                        <input type="text" name="min_max_speed" value="{escape(request.args.get('min_max_speed', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Max Speed</label>
                        <input type="text" name="max_max_speed" value="{escape(request.args.get('max_max_speed', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Avg Speed</label>
                        <input type="text" name="min_avg_speed" value="{escape(request.args.get('min_avg_speed', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Avg Speed</label>
                        <input type="text" name="max_avg_speed" value="{escape(request.args.get('max_avg_speed', ''))}" size="6">
                    </div>
                </div>
            </div>

            <div class="section">
                <div class="filter-grid">
                    <div class="field-box">
                        <label>Min Avg Power</label>
                        <input type="text" name="min_avg_power" value="{escape(request.args.get('min_avg_power', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Avg Power</label>
                        <input type="text" name="max_avg_power" value="{escape(request.args.get('max_avg_power', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Max Power</label>
                        <input type="text" name="min_max_power" value="{escape(request.args.get('min_max_power', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Max Power</label>
                        <input type="text" name="max_max_power" value="{escape(request.args.get('max_max_power', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Avg HR</label>
                        <input type="text" name="min_avg_hr" value="{escape(request.args.get('min_avg_hr', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Avg HR</label>
                        <input type="text" name="max_avg_hr" value="{escape(request.args.get('max_avg_hr', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Min Max HR</label>
                        <input type="text" name="min_max_hr" value="{escape(request.args.get('min_max_hr', ''))}" size="6">
                    </div>
                    <div class="field-box">
                        <label>Max Max HR</label>
                        <input type="text" name="max_max_hr" value="{escape(request.args.get('max_max_hr', ''))}" size="6">
                    </div>
                </div>
            </div>


            <div class="section">
                <div class="row">
                    <label>Sort By</label>
                    <select name="sort_by">
                        <option value="date_desc" {"selected" if sort_by == "date_desc" else ""}>Date ↓</option>
                        <option value="date_asc" {"selected" if sort_by == "date_asc" else ""}>Date ↑</option>
                        <option value="name_desc" {"selected" if sort_by == "name_desc" else ""}>Name Z-A</option>
                        <option value="name_asc" {"selected" if sort_by == "name_asc" else ""}>Name A-Z</option>
                        <option value="type_desc" {"selected" if sort_by == "type_desc" else ""}>Type ↓</option>
                        <option value="type_asc" {"selected" if sort_by == "type_asc" else ""}>Type ↑</option>
                        <option value="miles_desc" {"selected" if sort_by == "miles_desc" else ""}>Miles ↓</option>
                        <option value="miles_asc" {"selected" if sort_by == "miles_asc" else ""}>Miles ↑</option>
                        <option value="elev_desc" {"selected" if sort_by == "elev_desc" else ""}>Elevation ↓</option>
                        <option value="elev_asc" {"selected" if sort_by == "elev_asc" else ""}>Elevation ↑</option>
                        <option value="time_desc" {"selected" if sort_by == "time_desc" else ""}>Time ↓</option>
                        <option value="time_asc" {"selected" if sort_by == "time_asc" else ""}>Time ↑</option>
                        <option value="kudos_desc" {"selected" if sort_by == "kudos_desc" else ""}>Kudos ↓</option>
                        <option value="kudos_asc" {"selected" if sort_by == "kudos_asc" else ""}>Kudos ↑</option>
                        <option value="comments_desc" {"selected" if sort_by == "comments_desc" else ""}>Comments ↓</option>
                        <option value="comments_asc" {"selected" if sort_by == "comments_asc" else ""}>Comments ↑</option>
                        <option value="prs_desc" {"selected" if sort_by == "prs_desc" else ""}>PRs ↓</option>
                        <option value="prs_asc" {"selected" if sort_by == "prs_asc" else ""}>PRs ↑</option>
                        <option value="max_speed_desc" {"selected" if sort_by == "max_speed_desc" else ""}>Max Speed ↓</option>
                        <option value="max_speed_asc" {"selected" if sort_by == "max_speed_asc" else ""}>Max Speed ↑</option>
                        <option value="avg_speed_desc" {"selected" if sort_by == "avg_speed_desc" else ""}>Avg Speed ↓</option>
                        <option value="avg_speed_asc" {"selected" if sort_by == "avg_speed_asc" else ""}>Avg Speed ↑</option>
                        <option value="avg_power_desc" {"selected" if sort_by == "avg_power_desc" else ""}>Avg Power ↓</option>
                        <option value="avg_power_asc" {"selected" if sort_by == "avg_power_asc" else ""}>Avg Power ↑</option>
                        <option value="max_power_desc" {"selected" if sort_by == "max_power_desc" else ""}>Max Power ↓</option>
                        <option value="max_power_asc" {"selected" if sort_by == "max_power_asc" else ""}>Max Power ↑</option>
                        <option value="avg_hr_desc" {"selected" if sort_by == "avg_hr_desc" else ""}>Avg HR ↓</option>
                        <option value="avg_hr_asc" {"selected" if sort_by == "avg_hr_asc" else ""}>Avg HR ↑</option>
                        <option value="max_hr_desc" {"selected" if sort_by == "max_hr_desc" else ""}>Max HR ↓</option>
                        <option value="max_hr_asc" {"selected" if sort_by == "max_hr_asc" else ""}>Max HR ↑</option>
                    </select>
                </div>

                <div class="button-row">
                    <button type="submit">Search</button>
                    <button type="button" onclick="clearFilters()" style="margin-left: 12px;">Clear Filters</button>
                </div>
            </div>
        </form>

        <p><strong>{len(filtered)}</strong> matching activities out of <strong>{len(all_activities)}</strong></p>
        
        <p style="margin-top:10px; font-weight:bold;">
        Select an activity below to view it on Strava.
        </p>

        <div class="table-container">
        <table>
            <tr>
                <th>{build_sort_link("Date", "date_desc", "date_asc")}</th>
                <th>{build_sort_link("Name", "name_desc", "name_asc")}</th>
                <th>{build_sort_link("Type", "type_desc", "type_asc")}</th>
                <th>{build_sort_link("Miles", "miles_desc", "miles_asc")}</th>
                <th>{build_sort_link("Elev (ft)", "elev_desc", "elev_asc")}</th>
                <th>{build_sort_link("Time", "time_desc", "time_asc")}</th>
                <th>{build_sort_link("Kudos", "kudos_desc", "kudos_asc")}</th>
                <th>{build_sort_link("Comments", "comments_desc", "comments_asc")}</th>
                <th>{build_sort_link("PRs", "prs_desc", "prs_asc")}</th>
                <th>{build_sort_link("Max Speed", "max_speed_desc", "max_speed_asc")}</th>
                <th>{build_sort_link("Avg Speed", "avg_speed_desc", "avg_speed_asc")}</th>
                <th>{build_sort_link("Avg Pwr", "avg_power_desc", "avg_power_asc")}</th>
                <th>{build_sort_link("Max Pwr", "max_power_desc", "max_power_asc")}</th>
                <th>{build_sort_link("Avg HR", "avg_hr_desc", "avg_hr_asc")}</th>
                <th>{build_sort_link("Max HR", "max_hr_desc", "max_hr_asc")}</th>
            </tr>
    """

    for ride in filtered:
        activity_id = ride.get("id")
        name = ride.get("name", "")
        sport_type = ride.get("sport_type", "")
        ride_date = ride.get("start_date_local", "")[:10]
        distance_miles = round(meters_to_miles(ride.get("distance", 0) or 0), 1)
        elev_feet = round(meters_to_feet(ride.get("total_elevation_gain", 0) or 0))
        moving_time = seconds_to_hhmm(ride.get("moving_time", 0) or 0)
        kudos = ride.get("kudos_count", 0) or 0
        comments = ride.get("comment_count", 0) or 0
        prs = ride.get("pr_count", 0) or 0
        max_speed = round(mps_to_mph(ride.get("max_speed", 0) or 0), 1)
        avg_speed = round(mps_to_mph(ride.get("average_speed", 0) or 0), 1)
        avg_power = ride.get("average_watts", "")
        max_power = ride.get("max_watts", "")
        avg_hr = ride.get("average_heartrate", "")
        max_hr = ride.get("max_heartrate", "")

        html += f"""
        <tr>
            <td>{escape(ride_date)}</td>
            <td class="name-col"><a href="https://www.strava.com/activities/{activity_id}" target="_blank">{escape(name)}</a></td>
            <td>{escape(sport_type)}</td>
            <td>{distance_miles}</td>
            <td>{elev_feet}</td>
            <td>{moving_time}</td>
            <td>{kudos}</td>
            <td>{comments}</td>
            <td>{prs}</td>
            <td>{max_speed}</td>
            <td>{avg_speed}</td>
            <td>{escape(str(avg_power))}</td>
            <td>{escape(str(max_power))}</td>
            <td>{escape(str(avg_hr))}</td>
            <td>{escape(str(max_hr))}</td>
        </tr>
        """

    html += """
        </table>
        </div>

        <div class="mobile-cards">
    """

    for ride in filtered:
        activity_id = ride.get("id")
        name = ride.get("name", "")
        sport_type = ride.get("sport_type", "")
        ride_date = ride.get("start_date_local", "")[:10]
        distance_miles = round(meters_to_miles(ride.get("distance", 0) or 0), 1)
        elev_feet = round(meters_to_feet(ride.get("total_elevation_gain", 0) or 0))
        moving_time = seconds_to_hhmm(ride.get("moving_time", 0) or 0)
        kudos = ride.get("kudos_count", 0) or 0
        comments = ride.get("comment_count", 0) or 0
        prs = ride.get("pr_count", 0) or 0
        max_speed = round(mps_to_mph(ride.get("max_speed", 0) or 0), 1)
        avg_speed = round(mps_to_mph(ride.get("average_speed", 0) or 0), 1)
        avg_power = ride.get("average_watts", "")
        max_power = ride.get("max_watts", "")
        avg_hr = ride.get("average_heartrate", "")
        max_hr = ride.get("max_heartrate", "")

        html += f"""
        <div class="activity-card">
            <div class="activity-card-title">
                <a href="https://www.strava.com/activities/{activity_id}" target="_blank">{escape(name)}</a>
            </div>

            <div class="activity-card-meta">
                {escape(ride_date)} • {escape(sport_type)}
            </div>

            <div class="activity-card-primary">
                <div>{distance_miles} mi</div>
                <div>{elev_feet} ft</div>
                <div>{moving_time}</div>
            </div>

            <div class="activity-card-grid">
                <div>Avg Speed</div><div>{avg_speed}</div>
                <div>Max Speed</div><div>{max_speed}</div>
                <div>Avg HR</div><div>{escape(str(avg_hr))}</div>
                <div>Max HR</div><div>{escape(str(max_hr))}</div>
                <div>Avg Power</div><div>{escape(str(avg_power))}</div>
                <div>Max Power</div><div>{escape(str(max_power))}</div>
            </div>

            <div class="activity-card-bottom">
                <div>PRs {prs}</div>
                <div>Kudos {kudos}</div>
                <div>Comments {comments}</div>
            </div>
        </div>
        """

    html += """
        </div>

        <button class="back-to-top" onclick="scrollToTop()">↑</button>
        
        <hr style="margin-top:30px;">
        <p style="font-size:12px; color:#666;">
        Powered by Strava
        </p>
        
        <hr style="margin-top:30px;">
        <p style="font-size:12px; color:#666;">
        RideFind3000 uses the Strava API but is not affiliated, endorsed, or certified by Strava.
        </p>
        
        <p style="font-size:12px;">
        <a href="/privacy">Privacy Policy & Information</a>
        </p>

    <script>
    function checkImportStatus() {
        fetch("/import_status")
            .then(response => response.json())
            .then(data => {
                const banner = document.getElementById("import-banner");
                const urlParams = new URLSearchParams(window.location.search);

                if (data.status === "complete") {
                    // Only set the auto-refresh if this is the *first* time we're seeing "complete"
                    // on this page load (i.e., 'import_just_finished' is not yet in the URL)
                    if (banner && !urlParams.get("import_just_finished")) {
                        banner.style.background = "#e7f7ea";
                        banner.style.border = "1px solid #b9e2c0";
                        banner.innerHTML =
                            "Full Strava activity history imported.<br><br>" +
                            "This page will refresh in 10 seconds. " +
                            "<button onclick='location.reload()'>Refresh now</button>";

                        // After 10 seconds, reload the page and add the 'import_just_finished' flag
                        setTimeout(() => {
                            const currentUrl = new URL(window.location.href);
                            currentUrl.searchParams.set("import_just_finished", "true");
                            window.location.href = currentUrl.toString();
                        }, 10000);
                    }
                    // If 'import_just_finished' is already true, or no banner, do nothing.
                    // The server-rendered HTML will handle the final "complete" banner state.
                } else if (data.status === "importing") {
                    // Still importing, so keep polling
                    if (banner) {
                        banner.style.background = "#eaf3ff";
                        banner.style.border = "1px solid #bcd4ff";
                        banner.innerHTML = "Importing the rest of your Strava history in the background.<br>Give us up to a few minutes, but we will let you know when it's finished.<br>This is a one-time process.";
                    }
                    setTimeout(checkImportStatus, 4000);
                }
            })
            .catch(error => {
                console.log("Import status check failed:", error);
                const banner = document.getElementById("import-banner");
                if (banner) {
                    banner.innerHTML = "Error checking import status. Please try refreshing.";
                    banner.style.background = "#ffeaea";
                    banner.style.border = "1px solid #ffbcbc";
                }
            });
    }

    // Only start checking import status if the 'import_just_finished' flag is NOT present in the URL
    // and if there's an 'import-banner' element to potentially update.
    // This prevents the polling loop from starting if the import has already completed and refreshed.
    window.addEventListener("DOMContentLoaded", function() {
        const urlParams = new URLSearchParams(window.location.search);
        if (!urlParams.get("import_just_finished") && document.getElementById("import-banner")) {
            setTimeout(checkImportStatus, 4000);
        }
        
        // Existing event listeners and function calls that are not related to the import status polling
        syncRidesOnlyCheckbox();
        document.querySelectorAll('input[name="sport_type"]').forEach(cb => {
            cb.addEventListener("change", syncRidesOnlyCheckbox);
        });
    });
    </script>

    </body>
    </html>
    """

    return html

def fetch_recent_activities(access_token, after_epoch, per_page=50):
    headers = {"Authorization": f"Bearer {access_token}"}

    response = requests.get(
        f"https://www.strava.com/api/v3/athlete/activities?per_page={per_page}&page=1&after={after_epoch}",
        headers=headers,
    )

    batch = response.json()

    if isinstance(batch, dict) and batch.get("message"):
        return batch

    return batch

def get_latest_activity_epoch(user_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT MAX(start_date)
        FROM activities
        WHERE user_id = %s
    """, (user_id,))

    result = cur.fetchone()[0]

    cur.close()
    conn.close()

    if result is None:
        return 0

    return int(result.timestamp())

@app.route("/sync_recent")
def sync_recent():
    access_token = session.get("access_token")
    user_id = session.get("user_id")

    if not access_token or not user_id:
        return redirect(url_for("home"))

    latest_epoch = get_latest_activity_epoch(user_id)
    recent_activities = fetch_recent_activities(access_token, latest_epoch)

    if isinstance(recent_activities, dict) and recent_activities.get("message"):
        return f"<pre>{escape(str(recent_activities))}</pre>"

    new_count = 0

    if isinstance(recent_activities, list):
        new_count = len(recent_activities)
        save_activities_to_db(user_id, recent_activities)

    if new_count == 0:
        return redirect(url_for("activities", sync_msg="No New Activities Found"))

    if new_count == 1:
        return redirect(url_for("activities", sync_msg="1 New Activity Loaded"))

    return redirect(url_for("activities", sync_msg=f"{new_count} New Activities Loaded"))

@app.route("/privacy")
def privacy():
    return """
    <h2>Privacy Policy & Information</h2>
    <p>RideFind3000 uses the Strava API to access activity data that you authorize.</p>
    <p>We store activity data in order to provide search and filtering functionality.</p>
    <p>We do not sell or share user data.</p>
    <p>You may revoke access at any time from your Strava account settings.</p>
    <p>To request deletion of stored RideFind3000 data, contact: ridefind3000@mail.com</p>
    """


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
