import requests
from bs4 import BeautifulSoup
import argparse
import tempfile
from contextlib import contextmanager
import shutil
import os
import logging
import urlparse
import re
import base64
import subprocess

@contextmanager
def tempdir(delete=True):
    dirname = tempfile.mkdtemp()
    try:
        yield dirname
    finally:
        if delete:
            shutil.rmtree(dirname)

def download_swf(dest):
    VOD_CONFIG_URL = 'http://www.mako.co.il/html/flash_swf/VODConfig.xml'
    BASE_URL = 'http://rcs.mako.co.il'

    logging.info('Downloading VideoPlayer SWF.')
    vod_config_raw = requests.get(VOD_CONFIG_URL).content
    # Fix broken XML
    def fix_href(m):
        before, href, after = m.groups()
        return before + re.sub('&(?!amp;)', '&amp;', href) + after

    vod_config = BeautifulSoup(re.sub(r'(<[^>]+Url>)([^<]+)(</)', fix_href, vod_config_raw), 'xml')
    url = urlparse.urljoin(BASE_URL, vod_config.find('PlayerLocation').text.strip())
    logging.debug('Got player URL: %s', url)
    swf_data = requests.get(url).content

    with open(dest, 'wb') as swf:
        swf.write(swf_data)

def disassemble_swf(swf, assets):
    # abcexport is part of RABCDasm
    subprocess.check_call(['abcexport', swf])
    swf_base, swf_ext = os.path.splitext(swf)
    os.rename('%s-0.abc' % (swf_base, ), '%s.abc' % (swf_base, ))
    subprocess.check_call(['rabcdasm', '%s.abc' % (swf_base, )])

    # replace assets
    for name, data in assets.iteritems():
        code = get_script_resource('asset.asasm').format(name=name,
                                                         data=base64.b64encode(data))
        with open(os.path.join(swf_base, '%s.script.asasm' % (name, )), 'w') as f:
            f.write(code)

    return swf_base

def get_binary_assets(swf):
    # swfbinexport is part of RABCDasm
    subprocess.check_call(['swfbinexport', swf])
    # swfdump belongs to swftools.
    swfinfo = subprocess.check_output(['swfdump', swf])

    def get_asset_data(i):
        swf_base, _ = os.path.splitext(swf)
        with open('%s-%d.bin' % (swf_base, i), 'rb') as f:
            return f.read()

    return dict((y, get_asset_data(int(x)))
                for x, y in re.findall('exports (\d+) as "(_a_.*)"', swfinfo))

def sprite_to_object(swf_base, name):
    # This is an ugly hack. redtamarin does not have a definition 
    # of flash.display.Sprite and its base classes. This function modifies a
    # Sprite subclass to be an Object subclass.
    script = os.path.join(swf_base, '%s.script.asasm' % (name, ))
    class_ = os.path.join(swf_base, '%s.class.asasm' % (name, ))

    with open(class_, 'rb') as f:
        class_data = f.read()

    with open(script, 'wb') as f:
        # object.asasm contains a definition of a subclass of Object.
        f.write(get_script_resource('object.asasm').format(name=name))

    with open(class_, 'wb') as f:
        f.write(class_data.replace('extends QName(PackageNamespace("flash.display"), "Sprite")',
                                   'extends QName(PackageNamespace(""), "Object")', 1))

def compile_and_run(swfdir, main):
    main_path = os.path.join(swfdir, 'my.main.asasm')
    abc_path = os.path.join(swfdir, 'my.main.abc')

    with open(main_path, 'w') as f:
        f.write(get_script_resource(main))
    subprocess.check_call(['rabcasm', main_path])
    return subprocess.check_output(['redshell', abc_path]).strip()

def get_playlist_key(swfdir):
    with open(os.path.join(swfdir, 'asxloader/AsxHandler.class.asasm')) as f:
        asx_handler = f.read()
    m = re.search('trait const .*AESKEY.* Utf8\("(.*)"', asx_handler)
    return m and m.group(1)

def get_script_resource(name):
    script_dir = os.path.dirname(__file__)
    with open(os.path.join(script_dir, name), 'rb') as f:
        return f.read()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-d', '--debug', action='store_true', help="Print debug output and don't delete temp dir.")
    parser.add_argument('--swf', help='VideoPlayer.swf file to extract (default is to download it from the server)')
    args = parser.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.debug else logging.WARN)

    with tempdir(delete=not args.debug) as d:
        logging.debug('tempdir at %s', d)
        swf = os.path.join(d, 'VideoPlayer.swf')
        if args.swf is not None:
            os.symlink(os.path.abspath(args.swf), swf)
        else:
            download_swf(swf)
    
        assets = get_binary_assets(swf)
        disasm_dir = disassemble_swf(swf, assets)
        sprite_to_object(disasm_dir, '_a_-_---')
        playlist_key = get_playlist_key(disasm_dir)
        payment_key = compile_and_run(disasm_dir, 'main.asasm')

    print 'Playlist key:', playlist_key
    print 'PaymentService key:', payment_key
        
if __name__ == '__main__':
    main()
