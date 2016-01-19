from google.appengine.api import users
from google.appengine.ext import ndb
from google.appengine.api import app_identity
from google.appengine.api import images
from google.appengine.ext import blobstore
from google.appengine.api import taskqueue
from google.appengine.api import mail
from google.appengine.ext.webapp import mail_handlers

import webapp2
import jinja2
import cloudstorage
import mimetypes

import os
import re

from models import Note, CheckListItem, NoteFile, UserLoader


jinja_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(os.path.dirname(__file__)))


images_formats = {
    '0': 'image/png',
    '1': 'image/jpeg',
    '2': 'image/webp',
    '-1': 'image/bmp',
    '-2': 'image/gif',
    '-3': 'image/ico',
    '-4': 'image/tiff',
}


class MainHandler(webapp2.RequestHandler):
    def _render_template(self, template_name, context=None):
        if context is None:
            context = {}

        user = users.get_current_user()
        ancestor_key = ndb.Key("User", user.nickname())
        qry = Note.owner_query(ancestor_key)
        context['notes'] = qry.fetch()

        template = jinja_env.get_template(template_name)
        return template.render(context)

    def _get_urls_for(self, file_name):
        user = users.get_current_user()
        if user is None:
            return

        bucket_name = app_identity.get_default_gcs_bucket_name()
        path = os.path.join('/', bucket_name, user.user_id(),
                            file_name)
        real_path = '/gs' + path
        key = blobstore.create_gs_key(real_path)
        try:
            url = images.get_serving_url(key, size=0)
            thumbnail_url = images.get_serving_url(key, size=150,
                                                   crop=True)
        except images.TransformationError, images.NotImageError:
            url = "http://storage.googleapis.com{}".format(path)
            thumbnail_url = None

        return url, thumbnail_url

    @ndb.transactional
    def _create_note(self, user, file_name, file_path):
        note = Note(parent=ndb.Key("User", user.nickname()),
                    title=self.request.get('title'),
                    content=self.request.get('content'))
        note.put()

        item_titles = self.request.get('checklist_items').split(',')
        for item_title in item_titles:
            if not item_title:
                continue
            item = CheckListItem(parent=note.key, title=item_title)
            item.put()
            note.checklist_items.append(item.key)

        if file_name and file_path:
            url, thumbnail_url = self._get_urls_for(file_name)

            f = NoteFile(parent=note.key, name=file_name,
                         url=url, thumbnail_url=thumbnail_url,
                         full_path=file_path)
            f.put()
            note.files.append(f.key)

            note.put()

    def get(self):
        user = users.get_current_user()
        if user is not None:
            logout_url = users.create_logout_url(self.request.uri)
            template_context = {
                'user': user.nickname(),
                'logout_url': logout_url,
            }
            self.response.out.write(
                self._render_template('main.html', template_context))
        else:
            login_url = users.create_login_url(self.request.uri)
            self.redirect(login_url)

    def post(self):
        user = users.get_current_user()
        if user is None:
            self.error(401)

        bucket_name = app_identity.get_default_gcs_bucket_name()
        uploaded_file = self.request.POST.get('uploaded_file')
        file_name = getattr(uploaded_file, 'filename', None)
        file_content = getattr(uploaded_file, 'file', None)
        real_path = ''
        if file_name and file_content:
            content_t = mimetypes.guess_type(file_name)[0]
            real_path = os.path.join('/', bucket_name, user.user_id(), file_name)

            with cloudstorage.open(real_path, 'w', content_type=content_t,
                                   options={'x-goog-acl': 'public-read'}) as f:
                f.write(file_content.read())
        self._create_note(user, file_name, real_path)

        logout_url = users.create_logout_url(self.request.uri)
        template_context = {
            'user': user.nickname(),
            'logout_url': logout_url,
        }
        self.response.out.write(
            self._render_template('main.html', template_context))


class MediaHandler(webapp2.RequestHandler):
    def get(self, file_name):
        user = users.get_current_user()
        if user is None:
            login_url = users.create_login_url(self.request.uri)
            return self.redirect(login_url)

        bucket_name = app_identity.get_default_gcs_bucket_name()
        content_t = mimetypes.guess_type(file_name)[0]
        real_path = os.path.join('/', bucket_name, user.user_id(), file_name)

        try:
            with cloudstorage.open(real_path, 'r') as f:
                self.response.headers.add_header('Content-Type', content_t)
                self.response.out.write(f.read())
        except cloudstorage.errors.NotFoundError:
            self.abort(404)


class ShrinkHandler(webapp2.RequestHandler):
    def _shrink_note(self, note):
        for file_key in note.files:
            file = file_key.get()
            try:
                with cloudstorage.open(file.full_path) as f:
                    image = images.Image(f.read())
                    image.resize(640)
                    new_image_data = image.execute_transforms()

                content_t = images_formats.get(str(image.format))
                with cloudstorage.open(file.full_path, 'w',
                                       content_type=content_t) as f:
                    f.write(new_image_data)

            except images.NotImageError:
                pass

    def post(self):
        if not 'X-AppEngine-TaskName' in self.request.headers:
            self.error(403)

        user_email = self.request.get('user_email')
        user = users.User(user_email)

        ancestor_key = ndb.Key("User", user.nickname())
        notes = Note.owner_query(ancestor_key).fetch()

        for note in notes:
            self._shrink_note(note)

        sender_address = "Notes Team <notes@example.com>"
        subject = "Shrink complete!"
        body = "We shrunk all the images attached to your notes!"
        mail.send_mail(sender_address, user_email, subject, body)

    def get(self):
        user = users.get_current_user()
        if user is None:
            login_url = users.create_login_url(self.request.uri)
            return self.redirect(login_url)

        taskqueue.add(url='/shrink',
                      params={'user_email': user.email()})
        self.response.write('Task added to the queue.')


class ShrinkCronJob(ShrinkHandler):
    def post(self):
        self.abort(405, headers=[('Allow', 'GET')])

    def get(self):
        if 'X-AppEngine-Cron' not in self.request.headers:
            self.error(403)

        notes = Note.query().fetch()
        for note in notes:
            self._shrink_note(note)


class CreateNoteHandler(mail_handlers.InboundMailHandler):
    def _reload_user(self, user_instance):
        key = UserLoader(user=user_instance).put()
        key.delete(use_datastore=False)
        u_loader = UserLoader.query(UserLoader.user == user_instance).get()
        return u_loader.user

    def receive(self, mail_message):
        email_pattern = re.compile(r'([\w\-\.]+@(\w[\w\-]+\.)+[\w\-]+)')
        match = email_pattern.findall(mail_message.sender)
        email_addr = match[0][0] if match else ''

        try:
            user = users.User(email_addr)
            user = self._reload_user(user)
        except users.UserNotFoundError:
            return self.error(403)

        title = mail_message.subject
        content = ''
        for content_t, body in mail_message.bodies('text/plain'):
            content += body.decode()

        attachments = getattr(mail_message, 'attachments', None)

        self._create_note(user, title, content, attachments)

    @ndb.transactional
    def _create_note(self, user, title, content, attachments):

        note = Note(parent=ndb.Key("User", user.nickname()),
                    title=title,
                    content=content)
        note.put()

        if attachments:
            bucket_name = app_identity.get_default_gcs_bucket_name()
            for file_name, file_content in attachments:
                content_t = mimetypes.guess_type(file_name)[0]
                real_path = os.path.join('/', bucket_name, user.user_id(), file_name)

                with cloudstorage.open(real_path, 'w', content_type=content_t,
                                       options={'x-goog-acl': 'public-read'}) as f:
                    f.write(file_content.decode())

                key = blobstore.create_gs_key('/gs' + real_path)
                try:
                    url = images.get_serving_url(key, size=0)
                    thumbnail_url = images.get_serving_url(key, size=150, crop=True)
                except images.TransformationError, images.NotImageError:
                    url = "http://storage.googleapis.com{}".format(real_path)
                    thumbnail_url = None

                f = NoteFile(parent=note.key, name=file_name,
                             url=url, thumbnail_url=thumbnail_url,
                             full_path=real_path)
                f.put()
                note.files.append(f.key)

            note.put()

app = webapp2.WSGIApplication([
    (r'/', MainHandler),
    (r'/media/(?P<file_name>[\w.]{0,256})', MediaHandler),
    (r'/shrink', ShrinkHandler),
    (r'/shrink_all', ShrinkCronJob),
    (r'/_ah/mail/create@book-123456\.appspotmail\.com', CreateNoteHandler),
], debug=True)
