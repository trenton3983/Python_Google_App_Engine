from google.appengine.api import memcache
from models import Note


images_formats = {
    '0': 'image/png',
    '1': 'image/jpeg',
    '2': 'image/webp',
    '-1': 'image/bmp',
    '-2': 'image/gif',
    '-3': 'image/ico',
    '-4': 'image/tiff',
}


def get_note_counter():
    data = memcache.get('note_count')
    if data is None:
        data = Note.query().count()
        memcache.set('note_count', data)

    return data


def inc_note_counter():
    client = memcache.Client()
    retry = 0
    while retry < 10:
        data = client.gets('note_count')
        if client.cas('note_count', data+1):
            break
        retry += 1