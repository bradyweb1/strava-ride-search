from flask import Flask, redirect, request, session, url_for, render_template
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
    return render_template("home.html")


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
    return render_template("loading.html")

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
    
    if is_importing:
        banner_status = "importing"
    elif import_just_finished:
        banner_status = "finished"
    else:
        banner_status = ""
    
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

    table_rows = []
    for ride in filtered:
        table_rows.append({
            "id": ride.get("id"),
            "name": ride.get("name", ""),
            "sport_type": ride.get("sport_type", ""),
            "date": ride.get("start_date_local", "")[:10],
            "distance_miles": round(meters_to_miles(ride.get("distance", 0) or 0), 1),
            "elev_feet": round(meters_to_feet(ride.get("total_elevation_gain", 0) or 0)),
            "moving_time": seconds_to_hhmm(ride.get("moving_time", 0) or 0),
            "kudos": ride.get("kudos_count", 0) or 0,
            "comments": ride.get("comment_count", 0) or 0,
            "prs": ride.get("pr_count", 0) or 0,
            "max_speed": round(mps_to_mph(ride.get("max_speed", 0) or 0), 1),
            "avg_speed": round(mps_to_mph(ride.get("average_speed", 0) or 0), 1),
            "avg_power": ride.get("average_watts", "") or "",
            "max_power": ride.get("max_watts", "") or "",
            "avg_hr": ride.get("average_heartrate", "") or "",
            "max_hr": ride.get("max_heartrate", "") or "",
        })

    sort_links = {
        "date": build_sort_link("Date", "date_desc", "date_asc"),
        "name": build_sort_link("Name", "name_desc", "name_asc"),
        "type": build_sort_link("Type", "type_desc", "type_asc"),
        "miles": build_sort_link("Miles", "miles_desc", "miles_asc"),
        "elev": build_sort_link("Elev (ft)", "elev_desc", "elev_asc"),
        "time": build_sort_link("Time", "time_desc", "time_asc"),
        "kudos": build_sort_link("Kudos", "kudos_desc", "kudos_asc"),
        "comments": build_sort_link("Comments", "comments_desc", "comments_asc"),
        "prs": build_sort_link("PRs", "prs_desc", "prs_asc"),
        "max_speed": build_sort_link("Max Speed", "max_speed_desc", "max_speed_asc"),
        "avg_speed": build_sort_link("Avg Speed", "avg_speed_desc", "avg_speed_asc"),
        "avg_power": build_sort_link("Avg Pwr", "avg_power_desc", "avg_power_asc"),
        "max_power": build_sort_link("Max Pwr", "max_power_desc", "max_power_asc"),
        "avg_hr": build_sort_link("Avg HR", "avg_hr_desc", "avg_hr_asc"),
        "max_hr": build_sort_link("Max HR", "max_hr_desc", "max_hr_asc"),
    }

    return render_template(
        "activities.html",
        banner_status=banner_status,
        sync_msg=sync_msg,
        keyword=keyword,
        year=year,
        month=month,
        exact_date=exact_date,
        start_date=start_date,
        end_date=end_date,
        years=years,
        sport_types=sport_types,
        selected_types=selected_types,
        sort_by=sort_by,
        filtered_count=len(filtered),
        total_count=len(all_activities),
        table_rows=table_rows,
        sort_links=sort_links,
        args=request.args,
    )

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
    return render_template("privacy.html")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
