from bs4 import BeautifulSoup
import progressbar
import multiprocessing.dummy as multiprocessing
import argparse
import requests
import urlparse
import httplib
import struct
import subprocess
import itertools

# Specs:
#   http://download.macromedia.com/f4v/video_file_format_spec_v10_1.pdf
#   http://sourceforge.net/apps/mediawiki/osmf.adobe/index.php?title=Flash_Media_Manifest_(F4M)_File_Format

def get_xml_document(url, session):
    resp = session.get(url)
    resp.raise_for_status()
    return BeautifulSoup(resp.content, 'xml')

def get_box_data(buf, name):
    box_offset = buf.find(name) - 4
    size, = struct.unpack('>L', buf[box_offset:box_offset+4])
    header_size = 8
    if size == 1:
        # Extended box size
        size, = struct.unpack('>Q', buf[box_offset+8:box_offset+16])
        header_size += 8

    return buf[box_offset + header_size:box_offset + size]

def get_fragment_urls(manifest_url, session):
    manifest = get_xml_document(manifest_url, session)

    def fix_url(url):
        baseurl_tag = manifest.find('baseURL')
        base_url = baseurl_tag.string if baseurl_tag else manifest_url
        return urlparse.urljoin(base_url, url)

    # Get the stream with highest bitrate (likely the highest quality)
    media = max(manifest.find_all('media'), key=lambda tag: int(tag.get('bitrate', 0)))

    if 'href' in media.attrs:
        # Multi-level format (F4M 2.0)
        manifest_url = fix_url(media['href'])
        manifest = get_xml_document(manifest_url, session)
        # Assuming there is a single media element and that the second manifest
        # is not multi-level
        media = manifest.find('media')

    media_url = fix_url(media['url'])

    # get max fragment ID from bootstrap info
    bootstrap_info = manifest.find('bootstrapInfo', id=media['bootstrapInfoId']).string.decode('base64')

    asrt = get_box_data(bootstrap_info, 'asrt')
    _, QualityEntryCount, SegmentRunEntryCount, FirstSegment, FragmentsPerSegment = struct.unpack('>LBLLL', asrt)
    assert QualityEntryCount == 0 and SegmentRunEntryCount == 1 and FirstSegment == 1

    for frag_id in xrange(1, FragmentsPerSegment + 1):
        yield '%sSeg1-Frag%d' % (media_url, frag_id)

def download_fragment(frag_url, session):
    status = None
    attempts = 3
    while status != httplib.OK and attempts > 0:
        resp = session.get(frag_url, stream=True)
        status = resp.status_code
        attempts -= 1

    resp.raise_for_status()

    fragment_data = bytearray()
    content_it = resp.iter_content(4096)
    for chunk in content_it:
        fragment_data.extend(chunk)
        mdat_pos = fragment_data.find('mdat')
        # Make sure the entire mdat header was read
        if 0 <= mdat_pos and mdat_pos + 12 < len(fragment_data):
            break

    box_offset = mdat_pos - 4
    size, = struct.unpack('>L', str(fragment_data[box_offset:box_offset+4]))
    header_size = 8
    if size == 1:
        # Extended box size
        size, = struct.unpack('>Q', str(fragment_data[box_offset+8:box_offset+16]))
        header_size += 8

    payload = fragment_data[box_offset + header_size:]
    size -= len(payload)
    yield payload

    for chunk in content_it:
        if size == 0:
            break
        payload = chunk[:size]
        size -= len(payload)
        yield payload

def download(manifest_url, out_filename, reindex=True, session=None, parallel=20, progress=False):
    FLV_HEADER = '464c5601050000000900000000'.decode('hex')
    USER_AGENT = 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/27.0.1453.93 Safari/537.36'

    if session is None:
        session = requests.Session()
    if session.headers['User-Agent'] == requests.utils.default_user_agent():
        session.headers['User-Agent'] = USER_AGENT

    if progress:
        pbar_widgets = ['Downloading: ', progressbar.Percentage(), ' ', progressbar.Bar(), 
                      ' ', progressbar.ETA(), ]
    else:
        pbar_widgets = []

    stream_urls = list(get_fragment_urls(manifest_url, session))
    pb = progressbar.ProgressBar(widgets=pbar_widgets, maxval=len(stream_urls)).start()

    with open(out_filename, 'w') as outfile:
        pool = multiprocessing.Pool(parallel)

        outfile.write(FLV_HEADER)
        for frag in pool.imap(lambda url: download_fragment(url, session), stream_urls):
            pb.update(pb.currval + 1)
            for chunk in frag:
                outfile.write(chunk)

        pb.finish()

    # The downloaded FLV is playable by itself, but will have extremely
    # slow seeking without reindexing.
    if reindex:
        subprocess.call(['index-flv', '-rU', out_filename])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('manifest_url')
    parser.add_argument('outfile')
    parser.add_argument('-t', '--ticket')
    parser.add_argument('-p', '--parallel', type=int, default=20, help='Number of parallel connections.')

    args = parser.parse_args()

    session = requests.Session()
    session.params = args.ticket

    download(args.manifest_url, args.outfile, session=session, parallel=args.parallel, progress=True)

if __name__ == '__main__':
    main()
