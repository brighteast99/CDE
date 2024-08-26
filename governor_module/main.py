import io
import os
import tarfile
from hashlib import sha512 as hash
from json import loads

from apscheduler.schedulers.background import BackgroundScheduler
from cde_governor.db import Database
from cde_governor.manage import Manager
from flask import Flask, flash, redirect, render_template, request, send_file, session
from logger_initializer import setup_logger
from pymysql import IntegrityError

ANNOUNCEMENT_PATH = os.getenv("ANNOUNCEMENT_PATH")

LOG_PATH = os.getenv("LOG_PATH")
BACKUP_PATH = os.getenv("BACKUP_PATH")
CDE_IMAGE = os.getenv("CDE_IMAGE")
CDE_PORT = os.getenv("CDE_PORT")
SERVER_INFO = loads(os.getenv("SERVER_INFO"))

DB_HOST = os.getenv("MYSQL_HOST")
DB_NAME = os.getenv("MYSQL_DATABASE")
DB_USER = os.getenv("MYSQL_USER")
DB_USER_PW = os.getenv("MYSQL_PASSWORD")

CONTAINER_TYPE_DEV = 0
CONTAIENR_TYPE_VAL = 1


class server_core:
    def __init__(self):
        self.__app = Flask(__name__)
        self.__app.secret_key = hash(os.urandom(32)).hexdigest()
        self.__logger = self.__setup_logger()
        self.__db = self.__setup_db()
        self.__manager = self.__setup_manager()
        self.__scheduler = self.__setup_backup_scheduler()
        self.__handle_routes()
        self.__handle_requests()

    def __setup_logger(self):
        return setup_logger(
            "cde_logger",
            log_dir=LOG_PATH,
            log_format='%(asctime)s %(name)-12s %(levelname)-8s at "%(filename)s", line %(lineno)s, in %(funcName)s: %(message)s',
        )

    def __setup_db(self):
        self.__logger.info("Setting up DB")
        try:
            return Database(
                host=DB_HOST, user=DB_USER, password=DB_USER_PW, database=DB_NAME
            )
        except Exception:
            self.__logger.error("Failed to setup DB", exc_info=True)

    def __setup_manager(self):
        self.__logger.info("Setting up manager")
        return Manager(
            {
                "servers": SERVER_INFO,
                "container_types": [CONTAINER_TYPE_DEV, CONTAIENR_TYPE_VAL],
                "backup_dir": BACKUP_PATH,
                "cde_image": CDE_IMAGE,
                "cde_port": CDE_PORT,
                "db": self.__db,
            }
        )

    def __setup_backup_scheduler(self):
        self.__logger.info("Setting up backup scheduler")
        scheduler = BackgroundScheduler()

        def backup_containers():
            self.__logger.info("Start backing up containers")
            try:
                self.__manager.backup_containers()
            except:
                self.__logger.error("Failed to backup containers", exc_info=True)
                return
            self.__logger.info("Finished backing up containers")

        scheduler.add_job(
            backup_containers, "cron", day_of_week="mon", hour=0, minute=0
        )
        # self.__scheduler.add_job(backup_containers, 'interval', seconds=10)
        scheduler.start()

        return scheduler

    def __is_authenticated(self):
        user = session.get("user", None)
        return user is not None

    def __handle_routes(self):
        @self.__app.route("/", methods=["GET"])
        def index():
            if self.__is_authenticated():
                return redirect("/dashboard")
            return redirect("/login")

        @self.__app.route("/login", methods=["GET"])
        def login_page():
            if self.__is_authenticated():
                return redirect("/dashboard")
            return render_template("login.html")

        @self.__app.route("/signup", methods=["GET"])
        def signup_page():
            return render_template("signup.html")

        @self.__app.route("/dashboard", methods=["GET"])
        def dashboard_page():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            return render_template(
                "dashboard.html", username=session.get("username", "Unknown user")
            )

        @self.__app.route("/check_pw", methods=["GET"])
        def check_pw_page():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            return render_template("check_pw.html", next=request.args.get("next"))

        @self.__app.route("/user_info", methods=["GET"])
        def user_info_page():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            return render_template(
                "user_info.html", username=session.get("username", "Unknown user")
            )

        @self.__app.route("/connect/<container_type>", methods=["GET"])
        def connect_page(container_type):
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            return redirect(
                self.__manager.get_cde_url(session.get("user"), container_type)
            )

    def __handle_requests(self):
        @self.__app.route("/login", methods=["POST"])
        def handle_login_request():
            data = request.form.to_dict()
            username = data.get("username", None)
            password = data.get("password", None)

            self.__logger.debug(f'User "{username}" tries to log in')
            user_id = self.__db.auth(username, password)
            if user_id is None:
                self.__logger.debug(f'User "{username}" failed to log in')
                flash("Incorrect user info")
                return redirect("/login")

            self.__logger.debug(f'User "{username}" logged in')
            session["user"] = user_id
            session["username"] = username

            if not os.path.exists(ANNOUNCEMENT_PATH):
                os.mkdir(ANNOUNCEMENT_PATH)

            for announcement in os.listdir(ANNOUNCEMENT_PATH):
                with open(os.path.join(ANNOUNCEMENT_PATH, announcement), "r") as file:
                    flash(file.read())

            return redirect("/dashboard")

        @self.__app.route("/signup", methods=["POST"])
        def handle_signup_request():
            data = request.form.to_dict()
            username = data.get("username", None)
            password = data.get("password", None)
            password_confirm = data.get("password_confirm", None)

            if password != password_confirm:
                flash("Password and password confirm does not match")
                return redirect("/signup")

            try:
                user_id = self.__db.create_user(username, password)
                self.__logger.debug(f"User {username} created")
                self.__manager.create_cde(user_id, username)
                flash(f"Signup complete")
                return redirect("/")
            except IntegrityError:
                self.__logger.debug("Username already in use", exc_info=True)
                flash("Username already in use")
                return redirect("/signup")
            except Exception:
                self.__logger.error("Failed to create user", exc_info=True)
                flash("Failed to sign up")
                return redirect("/signup")

        @self.__app.route("/upload", methods=["POST"])
        def handle_upload_request():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            data = request.form.to_dict()
            container_type = data.get("type", "dev")
            self.__logger.debug(container_type)
            files = request.files.getlist("files")

            tar_bytes = io.BytesIO()
            with tarfile.open(mode="w:gz", fileobj=tar_bytes) as tar:
                for file in files:
                    tarinfo = tarfile.TarInfo(name=file.filename)
                    file.seek(0, io.SEEK_END)
                    tarinfo.size = file.tell()
                    file.seek(0)
                    tar.addfile(tarinfo, file)
            tar_bytes.seek(0)

            user = session.get("user")

            try:
                self.__manager.upload_file(
                    user_id=user, container_type=container_type, file=tar_bytes
                )
            except:
                self.__logger.error("Failed to upload files", exc_info=True)
                flash("Failed to upload files")
                return redirect("/dashboard")

            flash("Upload completed")
            return redirect("/dashboard")

        @self.__app.route("/check_pw", methods=["POST"])
        def handle_check_pw_request():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            data = request.form.to_dict()
            password = data.get("password")

            username = session.get("username")
            user_id = self.__db.auth(username, password)
            if user_id is None:
                flash("Wrong password")
                return redirect(f"/check_pw?next={request.args.get('next', '')}")

            return redirect(request.args.get("next", "/"))

        @self.__app.route("/update_pw", methods=["POST"])
        def handle_update_pw_request():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            username = session.get("username", None)
            data = request.form.to_dict()
            cur_password = data.get("cur_password", None)
            new_password = data.get("new_password", None)
            new_password_confirm = data.get("new_password_confirm", None)

            if new_password != new_password_confirm:
                flash("Password and password confirm does not match")
                return redirect("/user_info")

            user_id = self.__db.auth(username, cur_password)
            if user_id is None:
                flash("Wrong current password")
                return redirect("/user_info")

            try:
                self.__db.update_pw(username, new_password)
            except:
                self.__logger.error(
                    f"""Failed to update password (user {
                        session.get('username')})""",
                    exc_info=True,
                )
                flash("Failed to update password")
                return redirect("/user_info")

            del session["user"]
            del session["username"]
            flash("Password changed.\nYou need to re-login")
            return redirect("/login")

        @self.__app.route("/backup", methods=["POST"])
        def handle_backup_request():
            if not self.__is_authenticated():
                flash("Login required")
                return redirect("/login")

            user = session.get("user", None)
            data = request.form.to_dict()
            container_type = data.get("type", "dev")
            self.__logger.debug(container_type)

            try:
                result_file_path = self.__manager.backup_container(
                    user_id=user, container_type=container_type
                )
                return send_file(
                    result_file_path, mimetype="application/x-tar", as_attachment=True
                )
            except AssertionError:
                self.__logger.debug(
                    f"User {session.get('username')} tried to backup container while ther is no container for them",
                    exc_info=True,
                )
                flash("No environment to backup")
                return redirect("/dashboard")
            except:
                self.__logger.error(
                    f"Failed to backup container (user {session.get('username')})",
                    exc_info=True,
                )
                flash("Failed to backup container")
                return redirect("/dashboard")

    def runner(self):
        try:
            self.__app.run(host="0.0.0.0", port=443, ssl_context="adhoc", debug=True)
        finally:
            self.__logger.info("Exiting server")
            self.__logger.info("Shutting down backup scheduler")
            self.__scheduler.shutdown()
