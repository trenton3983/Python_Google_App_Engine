import webapp2

from google.appengine.ext import ndb
from google.appengine.api import images

import cloudstorage

from models import Note
from utils import images_formats


class ShrinkCronJob(webapp2.RequestHandler):
    @ndb.tasklet
    def _shrink_note(self, note):
        for file_key in note.files:
            file = yield file_key.get_async()
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

    def get(self):
        if 'X-AppEngine-Cron' not in self.request.headers:
            self.error(403)

        notes = Note.query().fetch()
        for note in notes:
            self._shrink_note(note)


app = webapp2.WSGIApplication([
    (r'/shrink_all', ShrinkCronJob),
], debug=True)
