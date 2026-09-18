import os
import re
import hashlib
import base64
import logging
from datetime import datetime

from flask import (
    Flask, render_template, request,
    send_from_directory, Response, redirect, abort
)

app = Flask(__name__,
            static_folder='APP/static',
            template_folder='APP/templates')

app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', os.urandom(32))

# Defensive cookie flags — inert today (nothing sets session[...] yet), but
# correct by default the moment anything does.
app.config.update(
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',
    MAX_CONTENT_LENGTH=2 * 1024 * 1024,  # 2MB — no upload endpoint exists; caps stray large requests
)

SITE_URL = 'https://tradescene.org'

NO_REDIRECT_PATHS = {'/robots.txt', '/sitemap.xml', '/favicon.ico'}

logging.basicConfig(level=logging.INFO)
log = logging.getLogger('tradescene')

@app.before_request
def redirect_www_to_non_www():
    if request.path in NO_REDIRECT_PATHS:
        return

    host = request.host.split(':')[0].lower()
    if host == 'www.tradescene.org':
        new_url = request.url.replace(
            '://www.tradescene.org', '://tradescene.org', 1
        )
        return redirect(new_url, code=301)

# Matches inline <script> tags with no src= attribute, so their body can be
# hashed for the CSP below.
_INLINE_SCRIPT_RE = re.compile(
    rb'<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>', re.DOTALL | re.IGNORECASE
)

CSP_STATIC_DIRECTIVES = (
    "default-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https://images.unsplash.com; "
    "media-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'; "
    "upgrade-insecure-requests"
)

@app.after_request
def set_security_headers(response):
    # Hashing each page's actual inline scripts here, instead of hand-listing
    # them, means the CSP can't drift out of sync with the templates.
    script_src = "'self' https://cdnjs.cloudflare.com"
    if response.mimetype == 'text/html':
        body = response.get_data()
        hashes = set()
        for match in _INLINE_SCRIPT_RE.finditer(body):
            digest = hashlib.sha256(match.group(1)).digest()
            hashes.add("'sha256-" + base64.b64encode(digest).decode() + "'")
        if hashes:
            script_src += ' ' + ' '.join(sorted(hashes))

    response.headers['Content-Security-Policy'] = (
        f"script-src {script_src}; " + CSP_STATIC_DIRECTIVES
    )
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = (
        'camera=(), microphone=(), geolocation=(), payment=(), usb=()'
    )
    response.headers['Strict-Transport-Security'] = (
        'max-age=63072000; includeSubDomains; preload'
    )
    response.headers['X-XSS-Protection'] = '0'  # legacy filter — disabled per current OWASP guidance
    return response

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/services/digital-transformation')
def digital_transformation():
    return render_template('services/digital-transformation.html')

@app.route('/services/custom-software')
def custom_software():
    return render_template('services/custom-software.html')

@app.route('/services/product-development')
def product_development():
    return render_template('services/product-development.html')

@app.route('/services/cybersecurity')
def cybersecurity():
    return render_template('services/cybersecurity.html')

@app.route('/contact')
def contact():
    return render_template('contact.html')

@app.route('/privacy')
def privacy():
    return render_template('privacy.html')

@app.route('/terms')
def terms():
    return render_template('terms.html')

@app.route('/cookies')
def cookies():
    return render_template('cookies.html')

# Allow-listed rather than interpolating the URL segment straight into a
# template path, since the old version would try to render any name given.
PACKAGE_TEMPLATES = {'starter', 'business', 'ecommerce-starter', 'ecommerce-pro'}

@app.route('/packages/<package_name>')
def package_detail(package_name):
    if package_name not in PACKAGE_TEMPLATES:
        abort(404)
    return render_template(f'services/{package_name}.html')

@app.route('/favicon.ico')
def favicon():
    resp = send_from_directory(
        os.path.join(app.root_path, 'APP', 'static', 'icons'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )
    resp.headers['Cache-Control'] = 'public, max-age=2592000'  # 30 days
    return resp

@app.route('/favicon.png')
def favicon_png():
    """Explicit PNG favicon route for browsers that prefer it."""
    resp = send_from_directory(
        os.path.join(app.root_path, 'APP', 'static', 'icons'),
        'favicon-96x96.png',
        mimetype='image/png'
    )
    resp.headers['Cache-Control'] = 'public, max-age=2592000'
    return resp

@app.route('/sitemap.xml', methods=['GET'])
def sitemap():
    """Generate sitemap.xml dynamically. Update PAGES when you add routes."""
    PAGES = [
        ('/',                                      'weekly',  '1.0', None),
        ('/about',                                 'monthly', '0.9', None),
        ('/contact',                               'monthly', '0.9', None),
        ('/services/custom-software',              'monthly', '0.9', None),
        ('/services/cybersecurity',                'monthly', '0.9', None),
        ('/services/product-development',          'monthly', '0.9', None),
        ('/services/digital-transformation',       'weekly',  '0.9', None),
        ('/services/digital-transformation/business',           'monthly', '0.7', None),
        ('/services/digital-transformation/ecommerce-starter',  'monthly', '0.7', None),
        ('/services/digital-transformation/ecommerce-pro',      'monthly', '0.7', None),
        ('/privacy',                               'yearly',  '0.3', None),
        ('/terms',                                 'yearly',  '0.3', None),
        ('/cookies',                               'yearly',  '0.3', None),
    ]

    today = datetime.utcnow().strftime('%Y-%m-%d')

    urls = []
    for path, changefreq, priority, lastmod in PAGES:
        urls.append(f"""  <url>
    <loc>{SITE_URL}{path}</loc>
    <lastmod>{lastmod or today}</lastmod>
    <changefreq>{changefreq}</changefreq>
    <priority>{priority}</priority>
  </url>""")

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{chr(10).join(urls)}
</urlset>"""

    resp = Response(xml, mimetype='application/xml; charset=utf-8')
    resp.headers['Content-Type'] = 'application/xml; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    resp.headers['X-Robots-Tag'] = 'noindex'
    resp.headers['Link'] = f'<{SITE_URL}/favicon.ico>; rel="icon"'
    return resp

@app.route('/robots.txt', methods=['GET'])
def robots():
    """Serve robots.txt with a link to the sitemap."""
    content = f"""User-agent: *
Allow: /

# Block internal and irrelevant paths
Disallow: /api/
Disallow: /static/videos/
Disallow: /*?*

Sitemap: {SITE_URL}/sitemap.xml
"""
    resp = Response(content, mimetype='text/plain; charset=utf-8')
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Cache-Control'] = 'public, max-age=3600'
    resp.headers['X-Robots-Tag'] = 'noindex'
    resp.headers['Link'] = f'<{SITE_URL}/favicon.ico>; rel="icon"'
    return resp

if __name__ == '__main__':
    # Was hardcoded debug=True — the Werkzeug interactive debugger it turns
    # on lets anyone who can trigger an unhandled exception run arbitrary
    # Python in the browser. Now opt-in only, and off by default.
    debug_mode = os.environ.get('FLASK_DEBUG', '0').lower() in ('1', 'true', 'yes')
    app.run(debug=debug_mode, host='0.0.0.0', port=int(os.environ.get('PORT', 8000)))