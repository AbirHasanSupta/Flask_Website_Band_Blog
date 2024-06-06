from datetime import datetime, date
from flask import Flask, abort, render_template, redirect, url_for, flash, request
from flask_bootstrap import Bootstrap5
from flask_ckeditor import CKEditor
from flask_gravatar import Gravatar
from flask_login import UserMixin, login_user, LoginManager, current_user, logout_user
from flask_sqlalchemy import SQLAlchemy
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.orm import relationship
from forms import CreatePostForm, RegistrationForm, LoginForm, CommentForm, UploadForm
import smtplib
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get("FLASK_KEY")
ckeditor = CKEditor(app)
Bootstrap5(app)
login_manager = LoginManager()
login_manager.init_app(app)


SERVICE_ACCOUNT_FILE = 'client_secrets.json'
API_NAME = 'drive'
API_VERSION = 'v3'
FOLDER_ID = os.environ.get("FOLDER_ID")


def get_drive_service():
    credentials = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE, scopes=['https://www.googleapis.com/auth/drive']
    )
    drive_service = build(API_NAME, API_VERSION, credentials=credentials)
    return drive_service


def upload_to_drive(file_path, folder_id):
    drive_service = get_drive_service()

    file_metadata = {
        'name': os.path.basename(file_path),
        'parents': [folder_id],
    }

    media = MediaFileUpload(file_path)

    drive_service.files().create(
        body=file_metadata,
        media_body=media,
        fields='id'
    ).execute()


def list_drive_photos(folder_id):
    drive_service = get_drive_service()
    results = drive_service.files().list(q=f"'{folder_id}' in parents and mimeType='image/jpeg'",
                                         fields="files(id)").execute()
    photo_ids = [file['id'] for file in results.get('files', [])]
    return photo_ids


def generate_drive_links(photo_ids):
    return [f'https://drive.google.com/thumbnail?id={photo_id}&sz=w1000' for photo_id in photo_ids]


@login_manager.user_loader
def load_user(user_id):
    return db.get_or_404(User, user_id)


app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get("DB_URI")
db = SQLAlchemy()
db.init_app(app)


gravatar = Gravatar(app,
                    size=100,
                    rating='g',
                    default='retro',
                    force_default=False,
                    force_lower=False,
                    use_ssl=False,
                    base_url=None)


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(250), unique=True, nullable=False)
    password = db.Column(db.String, nullable=False)
    name = db.Column(db.String(250), nullable=False)
    posts = relationship("BlogPost", back_populates="author")
    comments = relationship("Comment", back_populates="comment_author")


class BlogPost(db.Model):
    __tablename__ = "blog_posts"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(250), unique=True, nullable=False)
    subtitle = db.Column(db.String(250), nullable=False)
    date = db.Column(db.String(250), nullable=False)
    body = db.Column(db.Text, nullable=False)
    img_url = db.Column(db.String(250), nullable=False)
    author = relationship("User", back_populates="posts")
    author_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    comments = relationship("Comment", back_populates="parent_post")


class Comment(db.Model):
    __tablename__ = "comments"
    id = db.Column(db.Integer, primary_key=True)
    text = db.Column(db.String, nullable=False)
    posted_time = db.Column(db.DateTime, nullable=False)
    comment_author = relationship("User", back_populates="comments")
    author_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    parent_post = relationship("BlogPost", back_populates="comments")
    post_id = db.Column(db.Integer, db.ForeignKey("blog_posts.id"))


with app.app_context():
    db.create_all()


def calculate_time(posted_time):
    current_time = datetime.now()
    time_diff = current_time - posted_time
    days = time_diff.days
    hours, remainder = divmod(time_diff.seconds, 3600)
    minutes, _ = divmod(remainder, 60)

    if days > 0:
        return f"{days} days ago"
    elif hours > 0:
        return f"{hours} hours ago"
    elif minutes > 0:
        return f"{minutes} minutes ago"
    else:
        return "Just now"


@app.route('/register', methods=["POST", "GET"])
def register():
    form = RegistrationForm()
    if form.validate_on_submit():
        email = form.email.data
        user = db.session.execute(db.select(User).where(User.email == email)).scalar()
        if user:
            flash("You have already signed up with that email. Log in instead!")
            return redirect(url_for('login'))
        hashed_password = generate_password_hash(form.password.data, method="pbkdf2:sha256", salt_length=8)
        new_user = User(
            email=form.email.data,
            password=hashed_password,
            name=form.name.data
        )
        db.session.add(new_user)
        db.session.commit()
        login_user(new_user)
        return redirect(url_for("get_all_posts"))
    return render_template("register.html", form=form, current_user=current_user)


@app.route('/login', methods=["GET", "POST"])
def login():
    form = LoginForm()
    if form.validate_on_submit():
        email = form.email.data
        password = form.password.data
        user = db.session.execute(db.select(User).where(User.email == email)).scalar()
        if user:
            if check_password_hash(user.password, password):
                login_user(user)
                return redirect(url_for("get_all_posts"))
            else:
                flash("Password Incorrect, Try again!")
                return redirect(url_for('login'))
        else:
            flash("This email does not exist. Try again with proper email.")
            return redirect(url_for('login'))
    return render_template("login.html", form=form, current_user=current_user)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('get_all_posts'))


@app.route('/')
def get_all_posts():
    result = db.session.execute(db.select(BlogPost))
    posts = result.scalars().all()
    return render_template("index.html", all_posts=posts, current_user=current_user)


@app.route("/post/<int:post_id>", methods=["GET", "POST"])
def show_post(post_id):
    comment_form = CommentForm()
    requested_post = db.get_or_404(BlogPost, post_id)
    if comment_form.validate_on_submit():
        if not current_user.is_authenticated:
            flash("You need to login or register to comment.")
            return redirect(url_for("login"))
        new_comment = Comment(
            text=comment_form.comment.data,
            comment_author=current_user,
            parent_post=requested_post,
            posted_time=datetime.now()
                              )
        db.session.add(new_comment)
        db.session.commit()
    comments = Comment.query.filter_by(post_id=post_id).all()
    main_time = []
    for comment in comments:
        time = calculate_time(comment.posted_time)
        main_time.append(time)
    return render_template("post.html", post=requested_post, current_user=current_user,
                           form=comment_form, posted_time=main_time)


def admin(func):
    @wraps(func)
    def decorated_func(*args, **kwargs):
        if not current_user.is_authenticated or current_user.id != 1:
            return abort(403)
        return func(*args, **kwargs)
    return decorated_func


@app.route("/new-post", methods=["GET", "POST"])
@admin
def add_new_post():
    form = CreatePostForm()
    if form.validate_on_submit():
        new_post = BlogPost(
            title=form.title.data,
            subtitle=form.subtitle.data,
            body=form.body.data,
            img_url=form.img_url.data,
            author=current_user,
            date=date.today().strftime("%B %d, %Y")
        )
        db.session.add(new_post)
        db.session.commit()
        return redirect(url_for("get_all_posts"))
    return render_template("make-post.html", form=form, current_user=current_user)


@app.route("/edit-post/<int:post_id>", methods=["GET", "POST"])
@admin
def edit_post(post_id):
    post = db.get_or_404(BlogPost, post_id)
    edit_form = CreatePostForm(
        title=post.title,
        subtitle=post.subtitle,
        img_url=post.img_url,
        author=post.author,
        body=post.body
    )
    if edit_form.validate_on_submit():
        post.title = edit_form.title.data
        post.subtitle = edit_form.subtitle.data
        post.img_url = edit_form.img_url.data
        post.author = current_user
        post.body = edit_form.body.data
        db.session.commit()
        return redirect(url_for("show_post", post_id=post.id))
    return render_template("make-post.html", form=edit_form, is_edit=True, current_user=current_user)


@app.route("/delete/<int:post_id>")
@admin
def delete_post(post_id):
    post_to_delete = db.get_or_404(BlogPost, post_id)
    db.session.delete(post_to_delete)
    db.session.commit()
    return redirect(url_for('get_all_posts'))


def comment_delete(func):
    @wraps(func)
    def decorated_func(*args, **kwargs):
        user = db.session.execute(db.select(Comment).where(Comment.author_id == current_user.id)).scalar()
        if not current_user.is_authenticated or current_user.id != user.author_id:
            return abort(403)
        return func(*args, **kwargs)
    return decorated_func


@app.route("/delete/comment/<int:comment_id>/<int:post_id>")
@comment_delete
def delete_comment(post_id, comment_id):
    comment_to_delete = db.get_or_404(Comment, comment_id)
    db.session.delete(comment_to_delete)
    db.session.commit()
    return redirect(url_for('show_post', post_id=post_id))


@app.route("/about")
def about():
    return render_template("about.html", current_user=current_user)


@app.route('/photos', methods=['GET', 'POST'])
def show_photos():
    photo_ids = list_drive_photos(FOLDER_ID)
    drive_links = generate_drive_links(photo_ids)
    form = UploadForm()
    if form.validate_on_submit():
        image = form.image.data
        filename = image.filename
        upload_to_drive(filename, FOLDER_ID)
        return redirect(url_for("show_photos"))

    return render_template('photos.html', drive_links=drive_links, form=form, current_user=current_user)


MAIL_ADDRESS = os.environ.get("EMAIL_KEY")
MAIL_APP_PW = os.environ.get("PASSWORD_KEY")


@app.route("/contact", methods=["GET", "POST"])
def contact():
    if request.method == "POST":
        data = request.form
        send_email(data["name"], data["email"], data["phone"], data["message"])
        return render_template("contact.html", msg_sent=True)
    return render_template("contact.html", msg_sent=False)


def send_email(name, email, phone, message):
    email_message = f"Subject:New Message\n\nName: {name}\nEmail: {email}\nPhone: {phone}\nMessage:{message}"
    with smtplib.SMTP("smtp.gmail.com") as connection:
        connection.starttls()
        connection.login(MAIL_ADDRESS, MAIL_APP_PW)
        connection.sendmail(from_addr=MAIL_ADDRESS, to_addrs=MAIL_ADDRESS, msg=email_message)


if __name__ == "__main__":
    app.run(debug=False)
