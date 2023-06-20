from functools import wraps
from urllib.parse import urlparse
from urllib.request import urlopen
from flask import Blueprint, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.security import safe_join, generate_password_hash
from flask_login import current_user, login_required
from .models import User
from .thumbnails import THUMBNAIL_DIR, gen_thumbnail, get_thumbnail
from .posts import delete_post, get_all_posts, get_post, new_post, Response
from . import media_manager
from .media_manager import Response as MResponse
from . import DATA_DIR, MEDIA_DIR, TEMP_DIR, db
from .settings import get_setting, set_setting
from datetime import datetime as dt
import os
import mimetypes
import shutil

main = Blueprint("main", __name__)

# get random id for uploads
def get_random():
    random = os.urandom(5).hex()
    return random

# function to split array into array of arrays of size n
def split_into(n, arr):
    if len(arr) % 3:
        yield arr[:len(arr) % 3]
    for i in range(len(arr) % 3, len(arr), n):
        yield arr[i:i+n]

# decorator to limit access to admins
def admin_only(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        if current_user.is_authenticated and current_user.admin:
            return func(*args, **kwargs)
        else:
            flash("You have to be an admin to do that!")
            return redirect(url_for("main.index"))
    return wrapper

def strftime(timestamp):
    return dt.fromtimestamp(timestamp).strftime("%d %b %Y - %H:%M:%S")

# main website ui
@main.route("/")
@login_required
def index():
    posts = get_all_posts()
    return render_template("index.html", posts=list(split_into(3, posts)), get_thumbnail=get_thumbnail, strftime=strftime)

# post viewing path
@main.route("/post")
@login_required
def post():
    post_ts = request.args.get("ts")
    if post_ts.isdigit():
        post_ts = int(post_ts)
    post = get_post(post_ts)
    if not post:
        flash("Post not found")
    post_dir = safe_join(MEDIA_DIR, str(post_ts))
    media = []
    if os.path.exists(post_dir):
        for file in os.listdir(post_dir):
            media.append({
                "location": f"/media/{post_ts}/{file}",
                "type": mimetypes.guess_type(file)[0].split("/")[0]
            })
    if not media and post:
        flash("Media not found")
    return render_template("post.html", post=post, media=media, strftime=strftime)

# post adding paths
@main.route("/add")
@login_required
@admin_only
def add():
    return render_template("add.html", session_id=get_random())

@main.route("/add", methods=["POST"])
@login_required
@admin_only
def add_post():
    datetime = request.form.get("datetime")
    source = request.form.get("source") or ""
    tags = request.form.get("tags") or ""
    session_id = request.form.get("session_id") or get_random()

    split_tags = [tag.strip() for tag in tags.split(",")]
    split_tags.sort()
    if len(split_tags) == 1 and split_tags[0] == "":
        split_tags = []

    temp_post_dir = safe_join(TEMP_DIR, session_id)
    temp_post_dir_exists = os.path.exists(temp_post_dir)
    files = []
    if temp_post_dir_exists:
        for file in os.listdir(safe_join(TEMP_DIR, session_id)):
            files.append({
                "location": f"/temp/{session_id}/{file}",
                "type": mimetypes.guess_type(file)[0].split("/")[0],
                "file": file
            })
    files.sort(key=lambda x: int( x["file"].split(".")[0] ))

    if not datetime:
        flash("Date/time can't be empty")
        print(session_id)
        return render_template("add.html", source=source, tags=tags, session_id=session_id, files=files)
    datetime = dt.fromisoformat(datetime)
    datetime = round(dt.timestamp(datetime))

    new_post_dir = safe_join(MEDIA_DIR, str(datetime))
    if files:
        if os.path.exists(new_post_dir):
            shutil.rmtree(new_post_dir, ignore_errors=True)
        os.makedirs(new_post_dir, exist_ok=True)
        for index, file in enumerate(files):
            old_file = file["file"]
            old_file_ext = old_file.split(".")[-1]
            new_file = f"{index}.{old_file_ext}"
            old_file_path = safe_join(temp_post_dir, old_file)
            new_file_path = safe_join(new_post_dir, new_file)
            shutil.move(old_file_path, new_file_path)
    if temp_post_dir_exists:
        os.rmdir(temp_post_dir)
    response = new_post(datetime, source=source, tags=split_tags)
    if response["response"] == Response.FAILED:
        flash(response["message"])
        return render_template("add.html", datetime=datetime, source=source, tags=tags, session_id=session_id, files=files)
    gen_thumbnail(str(datetime))

    return redirect(url_for("main.index"))

# post manipulation paths
@main.route("/delete")
@login_required
@admin_only
def delete():
    post_ts = request.args.get("ts")
    if post_ts.isdigit():
        post_ts = int(post_ts)
    post = get_post(post_ts)
    if os.path.exists(safe_join(MEDIA_DIR, str(post_ts))):
        shutil.rmtree(safe_join(MEDIA_DIR, str(post_ts)))
    if os.path.exists(safe_join(THUMBNAIL_DIR, f"{post_ts}.webp")):
        os.remove(safe_join(THUMBNAIL_DIR, f"{post_ts}.webp"))
    result = delete_post(post_ts)
    if result["response"] == Response.FAILED:
        flash(result["message"])
    return redirect(url_for("main.index"))

# media upload/generation paths
@main.route("/upload", methods=["POST"])
@login_required
@admin_only
def upload():
    session_id = request.args.get("id")
    files = list(request.files.values())
    if not session_id:
        return "No session id provided", 400
    if not files:
        return {"files": []}, 400
    upload_path = safe_join(TEMP_DIR, session_id)
    os.makedirs(upload_path, exist_ok=True)
    start_index = 0
    existing_files = os.listdir(upload_path)
    if existing_files:
        existing_files.sort(key=lambda x: int( x.split(".")[0] ))
        start_index = int( existing_files[-1].split(".")[0] ) + 1
    uploaded_files = []
    for index, file in enumerate(files):
        index += start_index
        name = file.filename or "0.png"
        ext = name.split(".")[-1]
        new_filename = f"{index}.{ext}"
        uploaded_files.append({
            "location": f"/temp/{session_id}/{new_filename}",
            "type": mimetypes.guess_type(new_filename)[0].split("/")[0],
            "file": new_filename
        })
        new_path = safe_join(upload_path, new_filename)
        file.save(new_path)
    uploaded_files.sort(key=lambda x: int( x["file"].split(".")[0] ))
    return {
        "files": uploaded_files
    }

@main.route("/upload_social", methods=["POST"])
@login_required
@admin_only
def upload_social():
    session_id = request.args.get("id")
    media_url = request.get_data().decode("UTF-8")
    if not session_id:
        return "No session id provided", 400
    response = media_manager.get_image_links(media_url)
    if response["response"] != MResponse.SUCCESS:
        return response
    links = response["links"]
    upload_path = safe_join(TEMP_DIR, session_id)
    os.makedirs(upload_path, exist_ok=True)
    start_index = 0
    existing_files = os.listdir(upload_path)
    if existing_files:
        existing_files.sort(key=lambda x: int( x.split(".")[0] ))
        start_index = int( existing_files[-1].split(".")[0] ) + 1
    uploaded_files = []
    for index, link in enumerate(links):
        index += start_index
        parsed_link = urlparse(link)
        name = parsed_link.path
        ext = name.split(".")[-1]
        new_filename = f"{index}.{ext}"
        uploaded_files.append({
            "location": f"/temp/{session_id}/{new_filename}",
            "type": mimetypes.guess_type(new_filename)[0].split("/")[0],
            "file": new_filename
        })
        new_path = safe_join(upload_path, new_filename)
        dl_req = urlopen(link)
        data = dl_req.read()
        with open(new_path, "wb") as output:
            output.write(data)
    uploaded_files.sort(key=lambda x: int( x["file"].split(".")[0] ))
    return {
        "response": "success",
        "files": uploaded_files
    }

# media manipulation methods
@main.route("/media_up", methods=["POST"])
@login_required
@admin_only
def media_up():
    session_id = request.args.get("id")
    if not session_id:
        return "No session id provided", 400
    move_file = request.get_data().decode("UTF-8")
    temp_post_dir = safe_join(TEMP_DIR, session_id)
    files = []
    if os.path.exists(temp_post_dir):
        files = os.listdir(safe_join(TEMP_DIR, session_id))
    files.sort(key=lambda x: int( x.split(".")[0] ))
    if move_file in files:
        new_index = files.index(move_file) - 1
        random = get_random()
        if new_index >= 0:
            # file 1 is the requested file
            # file 2 is the other file already in place
            f1_num = move_file.split(".")[0]
            f2_num = files[new_index].split(".")[0]
            f1_ext = move_file.split(".")[1]
            f2_ext = files[new_index].split(".")[1]

            os.rename(safe_join(temp_post_dir, move_file), safe_join(temp_post_dir, f"{random}.{f1_ext}"))
            os.rename(safe_join(temp_post_dir, files[new_index]), safe_join(temp_post_dir, f"{f1_num}.{f2_ext}"))
            os.rename(safe_join(temp_post_dir, f"{random}.{f1_ext}"), safe_join(temp_post_dir, f"{f2_num}.{f1_ext}"))
    return "OK"

@main.route("/media_down", methods=["POST"])
@login_required
@admin_only
def media_down():
    session_id = request.args.get("id")
    if not session_id:
        return "No session id provided", 400
    move_file = request.get_data().decode("UTF-8")
    temp_post_dir = safe_join(TEMP_DIR, session_id)
    files = []
    if os.path.exists(temp_post_dir):
        files = os.listdir(safe_join(TEMP_DIR, session_id))
    files.sort(key=lambda x: int( x.split(".")[0] ))
    if move_file in files:
        new_index = files.index(move_file) + 1
        random = get_random()
        if new_index < len(files):
            # file 1 is the requested file
            # file 2 is the other file already in place
            f1_num = move_file.split(".")[0]
            f2_num = files[new_index].split(".")[0]
            f1_ext = move_file.split(".")[1]
            f2_ext = files[new_index].split(".")[1]

            os.rename(safe_join(temp_post_dir, move_file), safe_join(temp_post_dir, f"{random}.{f1_ext}"))
            os.rename(safe_join(temp_post_dir, files[new_index]), safe_join(temp_post_dir, f"{f1_num}.{f2_ext}"))
            os.rename(safe_join(temp_post_dir, f"{random}.{f1_ext}"), safe_join(temp_post_dir, f"{f2_num}.{f1_ext}"))
    return "OK"

@main.route("/media_delete", methods=["POST"])
@login_required
@admin_only
def media_delete():
    session_id = request.args.get("id")
    if not session_id:
        return "No session id provided", 400
    delete_file = request.get_data().decode("UTF-8")
    temp_post_dir = safe_join(TEMP_DIR, session_id)
    file_path = safe_join(temp_post_dir, delete_file)

    os.remove(file_path)

    return delete_file

# website setting paths
@main.route("/settings")
@login_required
def settings():
    allow_signups = int(get_setting("allow_signups", "1"))
    return render_template("settings.html", allow_signups=allow_signups)

@main.route("/settings", methods=["POST"])
@login_required
@admin_only
def settings_post():
    allow_signups = request.form.get("allow_signups")
    allow_signups = "1" if allow_signups == "on" else "0"
    app_name = request.form.get("app_name") 
    app_name = app_name or get_setting("app_name", "Art Downloader")

    set_setting("allow_signups", allow_signups)
    set_setting("app_name", app_name)
    return redirect(url_for("main.settings"))

@main.route("/user_settings", methods=["POST"])
@login_required
@admin_only
def user_settings_post():
    username = request.form.get("username")
    password = request.form.get("password")

    old_username = current_user.username
    user = User.query.filter_by(username=old_username).first()

    if username:
        user.username = username

    if password:
        user.password = generate_password_hash(password)

    db.session.commit()
    return redirect(url_for("main.settings"))

# preview paths
@main.route("/media/<path:path>")
@login_required
def serve_media(path):
    return send_from_directory(MEDIA_DIR, path)

@main.route("/thumb/<path:path>")
@login_required
def serve_thumbnail(path):
    thumb_dir = os.path.join(DATA_DIR, "thumbnails")
    return send_from_directory(thumb_dir, path)

@main.route("/temp/<path:path>")
@login_required
@admin_only
def serve_temp(path):
    return send_from_directory(TEMP_DIR, path)
