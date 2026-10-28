"""Selected raster inputs stored privately; no arbitrary filesystem URLs or OCR."""
import base64
import hashlib
import os
from pathlib import Path
import struct
import uuid
from .errors import AssistantError


def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24:
        width,height=struct.unpack('>II',data[16:24])
        if not width or not height:raise AssistantError('image_dimensions','The image has no dimensions.')
        return 'image/png','.png'
    if data.startswith(b'\xff\xd8\xff') and data.endswith(b'\xff\xd9'):return 'image/jpeg','.jpg'
    if len(data)>=16 and data[:4]==b'RIFF' and data[8:12]==b'WEBP':return 'image/webp','.webp'
    raise AssistantError('invalid_image','Select a PNG, JPEG or WebP screenshot; executable and vector formats are not accepted.')


def save(app,name,mime,encoded_data,key):
    if not isinstance(name,str) or not 1<=len(name)<=200 or not isinstance(encoded_data,str) or not isinstance(key,str) or not 1<=len(key)<=200:
        raise AssistantError('invalid_attachment','Invalid image name, content or delivery key.')
    try:data=base64.b64decode(encoded_data,validate=True)
    except (ValueError,TypeError) as error:raise AssistantError('invalid_attachment','Invalid encoded image.') from error
    # No product size cap: a provider limit surfaces as that turn's visible error.
    if not data:raise AssistantError('invalid_attachment','The image is empty.')
    actual,suffix=image_type(data)
    if mime!=actual:raise AssistantError('invalid_image','The declared image type does not match its content.')
    digest=hashlib.sha256(data).hexdigest()
    with app.store.lock(),app.store.connect() as con:
        app.store.assert_writable()
        old=con.execute('SELECT * FROM attachment_requests WHERE key=?',(key,)).fetchone()
        if old:
            if old['sha256']!=digest:raise AssistantError('idempotency_conflict','This image delivery key identifies different content.')
            row=con.execute('SELECT id,name,mime,sha256 FROM attachments WHERE id=?',(old['attachment_id'],)).fetchone()
            if not row:raise AssistantError('attachment_not_found','The previous image receipt is unavailable.')
            return dict(row)
        identity=str(uuid.uuid4());folder=app.store.path/'attachments';folder.mkdir(mode=0o700,exist_ok=True)
        path=folder/(identity+suffix)
        try:
            fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
            with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            con.execute('INSERT INTO attachments VALUES(?,?,?,?,?,?)',(identity,Path(name).name,actual,digest,str(path.relative_to(app.store.path)),app.now()))
            con.execute('INSERT INTO attachment_requests VALUES(?,?,?)',(key,digest,identity))
        except BaseException:
            path.unlink(missing_ok=True);raise
        return {'id':identity,'name':Path(name).name,'mime':actual,'sha256':digest}


def load(app,identity):
    with app.store.connect() as con:row=con.execute('SELECT * FROM attachments WHERE id=?',(identity,)).fetchone()
    if not row:raise AssistantError('attachment_not_found','The image does not exist.')
    path=(app.store.path/row['relative_path']).resolve()
    if not path.is_relative_to((app.store.path/'attachments').resolve()) or not path.is_file():raise AssistantError('attachment_corrupt','The image location is invalid.')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=row['sha256']:raise AssistantError('attachment_corrupt','The image failed its integrity check.')
    return data,row['mime']
