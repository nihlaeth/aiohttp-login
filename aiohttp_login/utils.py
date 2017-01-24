from os.path import join
import string
import random
from logging import getLogger
from datetime import datetime, timedelta
from email.mime.text import MIMEText

from aiohttp.web import HTTPFound
from aiohttp_session import get_session
from aiohttp_jinja2 import render_string
import passlib.hash
import aiosmtplib

from .cfg import cfg


CHARS = string.ascii_uppercase + string.ascii_lowercase + string.digits
log = getLogger(__name__)


def encrypt_password(password):
    return passlib.hash.sha256_crypt.encrypt(password, rounds=1000)


def check_password(password, password_hash):
    return passlib.hash.sha256_crypt.verify(password, password_hash)


def get_random_string(min, max=None):
    max = max or min
    size = random.randint(min, max)
    return ''.join(random.choice(CHARS) for x in range(size))


async def make_confirmation_link(request, confirmation):
    link = url_for('auth_confirmation', code=confirmation['code'])
    return '{}://{}{}'.format(request.scheme, request.host, link)


async def is_confirmation_allowed(user, action):
    db = cfg.STORAGE
    confirmation = await db.get_confirmation({'user': user, 'action': action})
    if not confirmation:
        return True
    if is_confirmation_expired(confirmation):
        await db.delete_confirmation(confirmation)
        return True


def is_confirmation_expired(confirmation):
    age = datetime.utcnow() - confirmation['created_at']
    lifetime_days = cfg['{}_CONFIRMATION_LIFETIME'.format(
        confirmation['action'].upper())]
    lifetime = timedelta(days=lifetime_days)
    return age > lifetime


async def authorize_user(request, user):
    session = await get_session(request)
    session[cfg.SESSION_USER_KEY] = cfg.STORAGE.user_session_id(user)


async def get_cur_user_id(request):
    session = await get_session(request)

    user_id = session.get(cfg.SESSION_USER_KEY)
    while user_id:
        if not isinstance(user_id, str):
            log.error('Wrong type of user_id in session')
            break

        user_id = cfg.STORAGE.user_id_from_string(user_id)
        if not user_id:
            break

        return user_id

    if cfg.SESSION_USER_KEY in session:
        del session['user']


async def get_cur_user(request):
    user_id = await get_cur_user_id(request)
    if user_id:
        user = await cfg.STORAGE.get_user({'id': user_id})
        if not user:
            session = await get_session(request)
            del session['user']
        return user


def url_for(urlname, *args, **kwargs):
    if str(urlname).startswith(('/', 'http://', 'https://')):
        return urlname
    return cfg.APP.router[urlname].url_for(*args, **kwargs)


def redirect(urlname, *args, **kwargs):
    return HTTPFound(url_for(urlname, *args, **kwargs))


def get_client_ip(request):
    try:
        ips = request.headers['X-Forwarded-For']
    except KeyError:
        ips = request.transport.get_extra_info('peername')[0]
    return ips.split(',')[0]


async def send_mail(recipient, subject, body):
    async with aiosmtplib.SMTP(
        loop=cfg.APP.loop,
        hostname=cfg.SMTP_HOST,
        port=cfg.SMTP_PORT,
        use_tls=cfg.SMTP_TLS,
    ) as smtp:
        if cfg.SMTP_USERNAME:
            await smtp.login(cfg.SMTP_USERNAME, cfg.SMTP_PASSWORD)
        msg = MIMEText(body, 'html')
        msg['Subject'] = subject
        msg['From'] = cfg.SMTP_SENDER
        msg['To'] = recipient
        await smtp.send_message(msg)


async def render_and_send_mail(request, to, template, context=None):
    page = render_string(template, request, context)
    subject, body = page.split('\n', 1)
    await send_mail(to, subject.strip(), body)


def themed(template):
    return join('aiohttp_login', cfg.THEME, template)