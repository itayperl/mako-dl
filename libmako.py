from Crypto.Cipher import AES
from bs4 import BeautifulSoup
import argparse
import sys
import subprocess
import progressbar
import re
import collections
import os
import requests
import json
import uuid
import urllib
import urlparse
import logging

import f4v

BASE_URL = 'http://www.mako.co.il'
USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/27.0.1453.93 Safari/537.36'
PLAYER_CONFIG = {}
VOD_CONFIG = {}

PLAYLIST_KEY = 'LTf7r/zM2VndHwP+4So6bw=='
PAYMENT_SERVICE_KEY = 'Ad4NIXQN4y6HyPp1qoT1H1=='

logger = logging.getLogger('mako')

def fix_asx(asx):
    """Fix unescaped ampersands in //ref/@href"""
    def fix_href(m):
        before, href, after = m.groups()
        return before + re.sub('&(?!amp;)', '&amp;', href) + after

    return re.sub(r'(<ref\s[^>]*href\s*=\s*")([^"]*)(")', fix_href, asx)

def load_config():
    VOD_CONFIG_URL = 'http://www.mako.co.il/html/flash_swf/VODConfig.xml'
    # The player now loads configNew.xml which is identical
    PLAYER_CONFIG_URL = 'http://rcs.mako.co.il/flash_swf/players/makoPlayer/configNew.xml'

    vod_config_raw = requests.get(VOD_CONFIG_URL).content
    # Fix broken XML
    def fix_href(m):
        before, href, after = m.groups()
        return before + re.sub('&(?!amp;)', '&amp;', href) + after

    vod_config = BeautifulSoup(re.sub(r'(<[^>]+Url>)([^<]+)(</)', fix_href, vod_config_raw), 'xml')
    for key in ('PremiumUrl', 'NotPremiumUrl'):
        url = vod_config.find(key).text.strip()
        VOD_CONFIG[key] = urlparse.urljoin(BASE_URL, url)

    player_config = BeautifulSoup(requests.get(PLAYER_CONFIG_URL).content, 'xml')
    for key in ('VODAsxUrl', 'PlaylistUrl', 'PaymentService'):
        url = player_config.find(key).text.strip()
        PLAYER_CONFIG[key] = urlparse.urljoin(BASE_URL, url)

load_config()

def decrypt(encrypted, key):
    aes = AES.new(key.decode('base64'), mode=AES.MODE_ECB)
    decrypted = aes.decrypt(encrypted.decode('base64'))
    # trim padding
    return decrypted[:-ord(decrypted[-1])]

def get_playlist(vcmid, channelId, galleryChId):
    url = PLAYER_CONFIG['PlaylistUrl'].replace('$$vcmid$$', vcmid).replace('$$videoChannelId$$', channelId).replace('$$galleryChannelId$$', galleryChId)
    return requests.get(url).json()

def get_ticket(vcmid, url):
    # Without this the service still returns a token, but it doesn't work.
    headers = { 'Referer': 'http://www.mako.co.il/html/flash_swf/makoTVLoader.swf' }

    resp = requests.post(PLAYER_CONFIG['PaymentService'], headers=headers,
                         data=dict(et='gt',
                                   dv=vcmid,
                                   lp=urlparse.urlparse(url).path,
                                   du=str(uuid.uuid1()),
                                   rv='CASTTIME'))

    payment_info = json.loads(resp.content)
    logger.debug('Payment info: %r', payment_info)
    return urllib.unquote(payment_info['tickets'][0]['ticket'])

def collect_json(url):
    return json.loads(requests.get(url, params={'type':'service'}).content)['root']

def show_programs(programs):
    max_col_width = max(len(p['url']) for p in programs)
    for p in programs:
        print '%s%s' % (p['url'].ljust(max_col_width + 2), p['title'])

def download_hls(session, manifest_url, output):
    def parse_m3u8(text):
        if '#' not in text:
            return text.decode('base64')
        else:
            return text

    m3u8 = parse_m3u8(session.get(manifest_url, params=session.params).content)
    # highest resolution is last
    best_res = urlparse.urljoin(manifest_url, m3u8.splitlines()[-1])

    url = parse_m3u8(session.get(best_res, params=session.params).content)
    chunks = [ urlparse.urljoin(manifest_url, line) for line in url.splitlines() if not line.startswith('#') ]
    pbar_widgets = ['Downloading: ', progressbar.Percentage(), ' ', progressbar.Bar(), 
                  ' ', progressbar.ETA(), ]
    pb = progressbar.ProgressBar(widgets=pbar_widgets, maxval=len(chunks)).start()

    with open(output, 'w') as fp:
        for i, chunk in enumerate(chunks, 1):
            pb.update(i)
            fp.write(session.get(chunk).content)

    pb.finish()

def download_casttime(video_data, output):
    filename = os.path.join(output, '%s.flv' % (video_data['title'], ))
    if os.path.exists(filename):
        return

    playlist = get_playlist(video_data['guid'], video_data['chId'], video_data['galleryChId'])
    manifest_url = [ m for m in playlist['media'] if m['format'] == 'CASTTIME_HLS' ][0]['url']

    ticket = get_ticket(video_data['guid'], manifest_url)

    session = requests.Session()
    session.params = ticket
    session.headers['User-Agent'] = USER_AGENT

    logger.debug('download_hls filename=%s url=%s ticket=%s', filename, manifest_url, ticket)
    download_hls(session, manifest_url, filename)

def download_wmv(video_data, output):
    logging.debug('download_wmv')
    filename = os.path.join(output, '%s.wmv' % (video_data['title'], ))
    if os.path.exists(filename):
        return

    session = requests.Session()
    session.headers['User-Agent'] = USER_AGENT
    session.headers['Referer'] = urlparse.urljoin(BASE_URL, video_data['url'])

    if video_data['isPremium'] == 'true':
        iframe_url = (VOD_CONFIG['PremiumUrl'].replace('$$$$$', video_data['guid']) +
                      urllib.quote(video_data['wmvUrl']))
    else:
        iframe_url = VOD_CONFIG['NotPremiumUrl'] + urllib.quote(video_data['wmvUrl'])
    iframe_html = session.get(iframe_url).content
    title = BeautifulSoup(iframe_html).find('title').string.strip()
    if title == 'CastUP WMV Player':
        pl_url = video_data['wmvUrl']
    elif title == 'Silverlight Detection':
        clipurl = urllib.unquote_plus(re.search('var linkSkip = .*clipurl=([^"]+)', iframe_html).group(1))

        # This is cruel! A random-length part from the end of the ticket ID
        # is removed and added at the beginning (before the first '|')
        scheme, netloc, path, query, fragment = urlparse.urlsplit(clipurl)
        pq = dict(urlparse.parse_qsl(query))
        pq['ticket'] = re.sub(r'(.*?)(\|.*)', r'\2\1', pq['ticket'])
        pl_url = urlparse.urlunsplit((scheme, netloc, path, urllib.urlencode(pq), fragment))
    else:
        logging.info('Unknown castup page title %r' % (title, ))
        return

    asx = BeautifulSoup(fix_asx(session.get(pl_url).content.decode('cp1255')), 'xml')
    start_entry = asx.find('PARAM', NAME='BM_START_ENTRY')
    if start_entry:
        main_entry = asx.find('PARAM', NAME='PLAY_LIST_ITEM_ID', VALUE=start_entry['VALUE']).parent
    else:
        main_entry = asx.find('starttime').parent
    stream_url = main_entry.find('ref')['href']
    subprocess.call(['mimms2', stream_url, filename])

def do_video(video_data, download=False, output='.', silent=False):
    if not silent:
        print '%s - %s' % (video_data['title'], video_data['brief'])
    if download:
        if video_data['videoFormat'] == '1':
            download_wmv(video_data, output)
        else:
            download_casttime(video_data, output)

def do_episodes(program_data, selection, download=False, output='.'):
    print program_data['title']
    for sid, season in enumerate(program_data['seasons'][::-1], 1):
        print '\t%d. %s' % (sid, season['name'], )
        for epid, vod in enumerate(season['vods'], 1):
            print '\t\t%d. %s: %s' % (epid, vod['title'], vod['shortSubtitle'])

            if download and (sid, epid) in selection:
                dl_dir = os.path.join(output, season['name'])
                if not os.path.isdir(dl_dir):
                    os.makedirs(dl_dir)

                logger.debug('Loading video URL "%s"', vod['link'])
                vod_json = collect_json(urlparse.urljoin(BASE_URL, vod['link']))
                if vod_json['pageType'] != 'ViewPage':
                    print >>sys.stderr, 'No video variable in episode page. Skipping.'
                    continue
                
                do_video(vod_json['video'], download, dl_dir, silent=True)

def process_url(url, selection, output='.', download=True):
    fragment = urlparse.urlparse(url).fragment
    if fragment.startswith('/'):
        url = urlparse.urljoin(BASE_URL, fragment)
    else:
        url = urlparse.urljoin(BASE_URL, url)

    json_vars = collect_json(url)
    logger.debug('Main URL "%s" has JSON vars: %r', url, json_vars.keys())
    if json_vars['pageType'] == 'Programs':
        show_programs(json_vars['allPrograms'])
    elif json_vars['pageType'] == 'ProgramPage':
        do_episodes(json_vars['programData'], selection, download, output)
    elif json_vars['pageType'] == 'ViewPage':
        do_video(json_vars['video'], download, output)

class Selection(object):
    Entry = collections.namedtuple('Entry', ('seasons', 'episodes'))
    Range = collections.namedtuple('Range', ('start', 'end'))
    INFINITY = float('inf')

    def __init__(self):
        self._entries = set()

    def __contains__(self, item):
        # The default selection accepts all episodes
        if len(self._entries) == 0:
            return True

        season, episode = item
        return any(any(s.start <= season <= s.end for s in entry.seasons) and
                   any(e.start <= episode <= e.end for e in entry.episodes)
                   for entry in self._entries)

    def __repr__(self):
        return repr(self._entries)

    @staticmethod
    def _validate_string(s):
        RANGE_RE = r'\d*(?:-\d*)?'
        SELECTION_RE = r'{0}(?:,{0})*'.format(RANGE_RE)
        FULL_RE = r'^(?:{0}:)?{0}$'.format(SELECTION_RE)
        if s == '' or re.match(FULL_RE, s) is None:
            raise argparse.ArgumentTypeError('Invalid selection string')

    def add_from_string(self, s):
        self._validate_string(s)

        def make_range(range_str):
            if range_str == '':
                range_str = '-'
            if '-' not in range_str:
                return self.Range(start=int(range_str), end=int(range_str))
            start, end = range_str.split('-')
            start = 1 if start == '' else int(start)
            end = self.INFINITY if end == '' else int(end)
            return self.Range(start, end)
            
        if ':' not in s:
            s = '1:%s' % (s, )

        seasons, episodes = [ tuple(map(make_range, x.split(','))) for x in  s.split(':') ]
        self._entries.add(self.Entry(seasons=seasons, episodes=episodes))
        return self

def add_selection_option(parser, *names):
    selection = Selection()
    parser.add_argument(*names, help='Select episodes to download in the form s1-sN:ep1-epN (the option can be repeated, and any number can be omitted, e.g. : means all episodes of all seasons, 1-4,7 means episodes 1,2,3,4,7 of the first season', type=selection.add_from_string)
